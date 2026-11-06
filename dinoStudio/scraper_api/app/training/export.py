import hashlib
import importlib.metadata
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import TrainingConfig
from .train import _run_dir, _run_path


def _dataset_provenance(run_dir: Path) -> tuple[str, str | None, str]:
    with zipfile.ZipFile(_run_path(run_dir, "dataset.zip")) as archive:
        raw_manifest = archive.read("manifest.json")
        manifest = json.loads(raw_manifest)
        if manifest.get("secret_scan_passed") is not True:
            raise ValueError("Training data has no confirmed secret-scan pass.")
        system_messages: set[str] = set()
        for split in ("train.jsonl", "test.jsonl"):
            with archive.open(split) as source:
                for line in source:
                    system_messages.add(json.loads(line)["messages"][0]["content"])
                    if len(system_messages) > 1:
                        raise ValueError("Training and test examples must share the same system message.")
    if not system_messages:
        raise ValueError("Training ZIP has no chat system message.")
    return hashlib.sha256(raw_manifest).hexdigest(), manifest.get("git_sha"), system_messages.pop()


def export_model(
    model: Any, tokenizer: Any, run_dir: Path, config: TrainingConfig, metrics: dict[str, Any],
) -> dict[str, Any]:
    run_dir = _run_dir(run_dir)
    manifest_hash, git_sha, system_message = _dataset_provenance(run_dir)
    status = json.loads(_run_path(run_dir, "status.json").read_text(encoding="utf-8"))
    gguf_path: str | None = None
    if not config.dry_run:
        if not callable(getattr(model, "save_pretrained_gguf", None)):
            raise RuntimeError("The installed Unsloth model cannot export GGUF with the notebook's API.")
        gguf_dir = _run_path(run_dir, "gguf")
        gguf_dir.mkdir(exist_ok=True)
        model.save_pretrained_gguf(str(gguf_dir), tokenizer, quantization_method=config.quantization)
        outputs = [path for path in gguf_dir.rglob("*.gguf") if path.is_file() and path.resolve().is_relative_to(run_dir)]
        if len(outputs) != 1:
            raise RuntimeError("GGUF export must produce exactly one model file inside the run directory.")
        gguf_path = outputs[0].relative_to(run_dir).as_posix()

    versions = dict(metrics["library_versions"])
    try:
        versions["llama-cpp-python"] = importlib.metadata.version("llama-cpp-python")
    except importlib.metadata.PackageNotFoundError:
        versions["llama-cpp-python"] = None
    card = {
        "run_id": status["run_id"],
        "scraper_job_id": status.get("job_id"),
        "base_model": metrics["effective_base_model"],
        "config": config.model_dump(mode="json"),
        "dataset_hash": metrics["dataset_hash"],
        "dataset_manifest_sha256": manifest_hash,
        "git_sha": git_sha,
        "system_message": system_message,
        "library_versions": versions,
        "metrics": {"train_examples": metrics["train_examples"], "test_examples": metrics["test_examples"],
                    "final_loss": metrics["final_loss"], "train_metrics": metrics["train_metrics"]},
        "gguf": gguf_path,
        "quantization": config.quantization if gguf_path else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    card_path = _run_path(run_dir, "model_card.json")
    pending_path = _run_path(run_dir, "model_card.json.tmp")
    pending_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending_path.replace(card_path)
    return card