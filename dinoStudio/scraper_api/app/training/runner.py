import atexit
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import TrainingConfig
from .preflight import PreflightReport, check_training_python

RUNS_ROOT = Path(__file__).resolve().parents[3] / "runs"
MAX_ARCHIVE_BYTES = 50_000_000
ACTIVE_STATUSES = {"starting", "running", "cancelling"}
ACTIVE_EVALUATIONS = {"starting", "running", "cancelling"}


@dataclass(slots=True)
class RunRecord:
    run_id: str
    job_id: str | None
    status: str
    stage: str
    base_model: str
    created_at: str
    updated_at: str
    step: int = 0
    total_steps: int | None = None
    loss: float | None = None
    eta_seconds: int | None = None
    error: str | None = None
    train_examples: int | None = None
    test_examples: int | None = None
    final_loss: float | None = None
    gguf: str | None = None
    eval_score: float | None = None
    eval_status: str | None = None
    eval_stage: str | None = None
    eval_step: int = 0
    eval_total_steps: int | None = None
    eval_error: str | None = None


class RunManager:
    def __init__(self) -> None:
        if RUNS_ROOT.is_symlink():
            raise RuntimeError("The runs directory cannot be a symbolic link.")
        RUNS_ROOT.mkdir(exist_ok=True)
        self.root = RUNS_ROOT.resolve()
        self.timeout_seconds = max(60, int(os.environ.get("TRAINING_TIMEOUT_SECONDS", "86400")))
        self._lock = threading.RLock()
        self._starting = False
        self._runs: dict[str, RunRecord] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._eval_processes: dict[str, subprocess.Popen[bytes]] = {}
        self._load_existing()
        atexit.register(self.shutdown)

    def _folder(self, run_id: str) -> Path:
        canonical = str(uuid.UUID(run_id))
        folder = self.root / canonical
        if folder.is_symlink() or folder.resolve().parent != self.root:
            raise ValueError("Invalid run directory.")
        return folder

    def _store(self, record: RunRecord) -> None:
        record.updated_at = datetime.now(timezone.utc).isoformat()
        folder = self._folder(record.run_id)
        pending = folder / "status.json.tmp"
        if pending.is_symlink() or (folder / "status.json").is_symlink():
            raise ValueError("Run status path cannot be a symbolic link.")
        pending.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
        pending.replace(folder / "status.json")

    def _load_existing(self) -> None:
        for folder in self.root.iterdir():
            try:
                if not folder.is_dir() or folder.is_symlink() or self._folder(folder.name) != folder:
                    continue
                record = RunRecord(**json.loads((folder / "status.json").read_text(encoding="utf-8")))
                if record.run_id != folder.name:
                    continue
                interrupted = False
                if record.status in ACTIVE_STATUSES:
                    record.status = "failed"
                    record.stage = "interrupted"
                    record.error = "API restarted while the worker was active; check worker.log before retrying."
                    interrupted = True
                if record.eval_status in ACTIVE_EVALUATIONS:
                    record.eval_status = "failed"
                    record.eval_stage = "interrupted"
                    record.eval_error = "API restarted during evaluation; check eval.log before retrying."
                    interrupted = True
                if interrupted:
                    self._store(record)
                self._runs[record.run_id] = record
            except (OSError, ValueError, TypeError, KeyError):
                continue

    @staticmethod
    def _check_archive(archive: bytes) -> None:
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ValueError("Training ZIP exceeds the 50 MB compressed size limit.")
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as source:
                manifest = source.getinfo("manifest.json")
                if manifest.file_size > 1_000_000:
                    raise ValueError("Training ZIP manifest is too large.")
                payload = json.loads(source.read(manifest))
                if not isinstance(payload, dict):
                    raise ValueError("Training ZIP manifest.json must contain a JSON object.")
                if payload.get("secret_scan_passed") is False:
                    raise ValueError("The scraper reported a failed secret scan. Resolve the findings before training.")
                if payload.get("secret_scan_passed") is not True:
                    raise ValueError("This ZIP has no confirmed secret scan. In /scraper, run analysis to completion and use Download results; an older results ZIP or source-code ZIP cannot be used.")
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as error:
            raise ValueError("Training needs the Download results ZIP from /scraper, containing manifest.json, train.jsonl and test.jsonl, not the source-code ZIP.") from error

    def preflight(self, base_model: str) -> PreflightReport:
        return check_training_python(base_model)

    def preview(self, archive: bytes, config: TrainingConfig) -> dict[str, Any]:
        self._check_archive(archive)
        configured_python = os.environ.get("TRAINING_PYTHON", "")
        configured_path = Path(configured_python)
        use_api_python = not configured_path.is_absolute() or not configured_path.is_file()
        python = sys.executable if use_api_python else str(configured_path)
        folder = self._folder(str(uuid.uuid4()))
        folder.mkdir()
        try:
            (folder / "dataset.zip").write_bytes(archive)
            effective_model = config.dry_run_model if config.dry_run else config.base_model
            preview_config = config.model_copy(update={"base_model": effective_model})
            (folder / "config.json").write_text(preview_config.model_dump_json(indent=2), encoding="utf-8")
            cache = folder / "cache"
            cache.mkdir()
            environment = os.environ.copy()
            environment.update(PYTHONDONTWRITEBYTECODE="1", HF_HUB_DISABLE_TELEMETRY="1")
            if use_api_python:
                environment.update(DINO_PREVIEW_OFFLINE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            else:
                environment.update(HF_HOME=str(cache / "huggingface"), HF_HUB_CACHE=str(cache / "huggingface" / "hub"))
            result = subprocess.run(
                [python, "-m", "scraper_api.app.training.dataset", str(folder / "dataset.zip"),
                 "--config", str(folder / "config.json")],
                cwd=self.root.parent, env=environment, capture_output=True, text=True, timeout=180, check=False,
            )
            if result.returncode:
                detail = result.stderr.strip().splitlines()
                raise ValueError(f"Dataset preview failed: {detail[-1] if detail else 'unknown error'}")
            try:
                summary = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise RuntimeError("Training Python returned an invalid dataset summary.") from error
            if use_api_python:
                summary["warnings"].insert(0, "Preview used the scraper API Python; configure a separate TRAINING_PYTHON before starting training.")
            return summary
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Dataset preview timed out while loading the configured model tokenizer.") from error
        finally:
            shutil.rmtree(folder)

    def start(self, archive: bytes, config: TrainingConfig, job_id: str | None = None) -> RunRecord:
        with self._lock:
            if self._starting or any(
                record.status in ACTIVE_STATUSES or record.eval_status in ACTIVE_EVALUATIONS
                for record in self._runs.values()
            ):
                raise RuntimeError("Training or evaluation is active. Cancel or wait before starting another run.")
            self._starting = True

        try:
            self._check_archive(archive)
            model_id = config.dry_run_model if config.dry_run else config.base_model
            report = self.preflight(model_id)
            if not report.ready:
                raise RuntimeError("Training preflight blocked the run: " + "; ".join(report.reasons))

            run_id = str(uuid.uuid4())
            folder = self._folder(run_id)
            folder.mkdir()
            process: subprocess.Popen[bytes] | None = None
            try:
                (folder / "dataset.zip").write_bytes(archive)
                (folder / "config.json").write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
                now = datetime.now(timezone.utc).isoformat()
                record = RunRecord(run_id, job_id, "starting", "starting", model_id, now, now)
                with self._lock:
                    self._runs[run_id] = record
                    self._store(record)

                python = os.environ["TRAINING_PYTHON"]
                environment = os.environ.copy()
                environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", HF_HUB_DISABLE_TELEMETRY="1")
                with (folder / "worker.log").open("w", encoding="utf-8") as output:
                    process = subprocess.Popen(
                        [python, "-m", "scraper_api.app.training.train", "--run-dir", str(folder)],
                        cwd=self.root.parent, env=environment, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=os.name != "nt",
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                    )
                with self._lock:
                    self._processes[run_id] = process
                    record.status = "running"
                    self._store(record)
                threading.Thread(target=self._monitor, args=(run_id, process), daemon=True).start()
                return record
            except Exception:
                if process is not None:
                    self._terminate_tree(process)
                with self._lock:
                    self._runs.pop(run_id, None)
                    self._processes.pop(run_id, None)
                shutil.rmtree(folder)
                raise
        finally:
            with self._lock:
                self._starting = False

    def _refresh(self, record: RunRecord) -> None:
        folder = self._folder(record.run_id)
        progress_file = folder / "progress.jsonl"
        if progress_file.is_file():
            with progress_file.open("rb") as source:
                source.seek(max(0, progress_file.stat().st_size - 65536))
                lines = source.read().splitlines()
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if "stage" in event:
                    if record.status not in {"cancelling", "cancelled", "failed"}:
                        record.stage = event["stage"]
                    record.step = event.get("step", record.step)
                    record.total_steps = event.get("total_steps", record.total_steps)
                    record.loss = event.get("loss") if event.get("loss") is not None else record.loss
                    record.eta_seconds = event.get("eta_seconds", record.eta_seconds)
                    break
        metrics_file = folder / "metrics.json"
        if metrics_file.is_file():
            try:
                metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
                record.train_examples = metrics.get("train_examples")
                record.test_examples = metrics.get("test_examples")
                record.final_loss = metrics.get("final_loss")
                record.gguf = metrics.get("gguf")
                if metrics.get("error") and record.status == "failed":
                    record.error = metrics["error"]
            except (OSError, json.JSONDecodeError):
                pass
        evaluation_progress = folder / "eval_progress.jsonl"
        if evaluation_progress.is_file():
            with evaluation_progress.open("rb") as source:
                source.seek(max(0, evaluation_progress.stat().st_size - 65536))
                lines = source.read().splitlines()
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if "stage" in event:
                    if record.eval_status in ACTIVE_EVALUATIONS and record.eval_status != "cancelling":
                        record.eval_stage = event["stage"]
                    record.eval_step = event.get("step", record.eval_step)
                    record.eval_total_steps = event.get("total_steps", record.eval_total_steps)
                    if record.eval_status == "failed" and event.get("error"):
                        record.eval_error = event["error"]
                    break

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"Training worker PID {process.pid} could not be stopped; terminate it manually.") from error

    def _monitor(self, run_id: str, process: subprocess.Popen[bytes]) -> None:
        timed_out = False
        try:
            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                self._terminate_tree(process)
            except RuntimeError as error:
                with self._lock:
                    record = self._runs[run_id]
                    record.status, record.stage, record.error = "cancelling", "termination_failed", str(error)
                    self._store(record)
                return
        with self._lock:
            record = self._runs[run_id]
            if record.status == "cancelling":
                record.status, record.stage = "cancelled", "cancelled"
            elif timed_out:
                record.status, record.stage = "failed", "timeout"
                record.error = f"Training exceeded the {self.timeout_seconds}-second limit and was stopped."
            elif process.returncode == 0:
                record.status, record.stage = "completed", "completed"
            else:
                record.status, record.stage = "failed", "failed"
                record.error = f"Training worker exited with code {process.returncode}; see worker.log."
            self._refresh(record)
            self._store(record)
            self._processes.pop(run_id, None)

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                self._refresh(record)
                return RunRecord(**asdict(record))
            return None

    def list(self) -> list[RunRecord]:
        with self._lock:
            records = [record for run_id in self._runs if (record := self.get(run_id)) is not None]
            return sorted(records, key=lambda record: record.created_at, reverse=True)

    def log_paths(self, run_id: str) -> tuple[Path, Path, Path, Path]:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError("Training run was not found.")
            folder = self._folder(run_id)
            return folder / "progress.jsonl", folder / "worker.log", folder / "eval_progress.jsonl", folder / "eval.log"

    def evaluate(self, run_id: str) -> RunRecord:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError("Training run was not found.")
            if record.status != "completed":
                raise RuntimeError("Evaluation requires a completed training run.")
            if self._starting or any(
                item.status in ACTIVE_STATUSES or item.eval_status in ACTIVE_EVALUATIONS
                for item in self._runs.values()
            ):
                raise RuntimeError("Training or evaluation is already active.")
            folder = self._folder(run_id)
            if not (folder / "adapter").is_dir() or (folder / "eval_report.json").exists():
                raise RuntimeError("Evaluation requires an adapter and cannot overwrite an existing report.")
            self._starting = True

        process: subprocess.Popen[bytes] | None = None
        try:
            report = self.preflight(record.base_model)
            if not report.ready:
                raise RuntimeError("Evaluation preflight blocked the run: " + "; ".join(report.reasons))
            environment = os.environ.copy()
            environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", HF_HUB_DISABLE_TELEMETRY="1")
            with (folder / "eval.log").open("w", encoding="utf-8") as output:
                process = subprocess.Popen(
                    [os.environ["TRAINING_PYTHON"], "-m", "scraper_api.app.training.evaluate", "--run-dir", str(folder)],
                    cwd=self.root.parent, env=environment, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
            with self._lock:
                self._eval_processes[run_id] = process
                record.eval_status, record.eval_stage, record.eval_error = "running", "starting", None
                self._store(record)
            threading.Thread(target=self._monitor_evaluation, args=(run_id, process), daemon=True).start()
            return RunRecord(**asdict(record))
        except Exception as error:
            if process is not None:
                self._terminate_tree(process)
            with self._lock:
                self._eval_processes.pop(run_id, None)
                if record.eval_status == "running":
                    record.eval_status, record.eval_stage, record.eval_error = "failed", "failed", str(error)
                    self._store(record)
            raise
        finally:
            with self._lock:
                self._starting = False

    def _monitor_evaluation(self, run_id: str, process: subprocess.Popen[bytes]) -> None:
        timed_out = False
        try:
            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                self._terminate_tree(process)
            except RuntimeError as error:
                with self._lock:
                    record = self._runs[run_id]
                    record.eval_status, record.eval_stage, record.eval_error = "cancelling", "termination_failed", str(error)
                    self._store(record)
                return
        with self._lock:
            record = self._runs[run_id]
            if record.eval_status == "cancelling":
                record.eval_status, record.eval_stage = "cancelled", "cancelled"
            elif timed_out:
                record.eval_status, record.eval_stage = "failed", "timeout"
                record.eval_error = f"Evaluation exceeded the {self.timeout_seconds}-second limit and was stopped."
            elif process.returncode == 0:
                report_path = self._folder(run_id) / "eval_report.json"
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    record.eval_score = report["overall"]["fine_tuned"]["quality_score"]
                    record.eval_status, record.eval_stage = "completed", "completed"
                except (OSError, ValueError, KeyError, TypeError) as error:
                    record.eval_status, record.eval_stage = "failed", "failed"
                    record.eval_error = f"Evaluation report is invalid: {error}"
            else:
                record.eval_status, record.eval_stage = "failed", "failed"
                record.eval_error = f"Evaluation worker exited with code {process.returncode}; see eval.log."
            self._refresh(record)
            self._store(record)
            self._eval_processes.pop(run_id, None)

    def evaluation_report(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError("Training run was not found.")
            if record.eval_status != "completed":
                raise RuntimeError("Evaluation report is available after evaluation completes.")
            path = self._folder(run_id) / "eval_report.json"
            if path.is_symlink() or path.resolve().parent != self._folder(run_id):
                raise RuntimeError("Invalid evaluation report path.")
        return json.loads(path.read_text(encoding="utf-8"))

    def cancel(self, run_id: str) -> RunRecord:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError("Training run was not found.")
            evaluation = record.eval_status in ACTIVE_EVALUATIONS
            if record.status not in ACTIVE_STATUSES and not evaluation:
                raise RuntimeError("Only an active training or evaluation run can be cancelled.")
            process = (self._eval_processes if evaluation else self._processes).get(run_id)
            if process is None:
                raise RuntimeError("The training process is not available for cancellation.")
            if evaluation:
                record.eval_status, record.eval_stage = "cancelling", "cancelling"
            else:
                record.status, record.stage = "cancelling", "cancelling"
            self._store(record)
        try:
            self._terminate_tree(process)
        except RuntimeError as error:
            with self._lock:
                if evaluation:
                    record.eval_error, record.eval_stage = str(error), "termination_failed"
                else:
                    record.error, record.stage = str(error), "termination_failed"
                self._store(record)
            raise
        process.wait()
        with self._lock:
            if evaluation and record.eval_status == "cancelling":
                record.eval_status, record.eval_stage = "cancelled", "cancelled"
                self._store(record)
            elif record.status == "cancelling":
                record.status, record.stage = "cancelled", "cancelled"
                self._store(record)
            return RunRecord(**asdict(record))

    def delete(self, run_id: str) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError("Training run was not found.")
            if record.status in ACTIVE_STATUSES or record.eval_status in ACTIVE_EVALUATIONS:
                raise RuntimeError("Cancel the active training run before deleting it.")
            shutil.rmtree(self._folder(run_id))
            del self._runs[run_id]

    def shutdown(self) -> None:
        with self._lock:
            active = list(self._processes.values()) + list(self._eval_processes.values())
        for process in active:
            self._terminate_tree(process)