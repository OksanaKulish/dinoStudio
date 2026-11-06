import argparse
import importlib.metadata
import inspect
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import TrainingConfig
from .dataset import load_dataset_zip
from .preflight import TRAINING_PACKAGES, preflight

RUNS_ROOT = Path(__file__).resolve().parents[3] / "runs"
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def _run_path(run_dir: Path, name: str) -> Path:
    path = run_dir / name
    if path.resolve().parent != run_dir:
        raise ValueError(f"{name} must remain inside the run directory.")
    return path


def _run_dir(path: Path) -> Path:
    if RUNS_ROOT.is_symlink():
        raise ValueError("The runs directory cannot be a symbolic link.")
    directory = path.expanduser().resolve(strict=True)
    if not directory.is_dir() or directory.parent != RUNS_ROOT.resolve():
        raise ValueError("--run-dir must name an existing direct child of runs/.")
    return directory


def _write_metrics(path: Path, pending: Path, metrics: dict[str, Any]) -> None:
    pending.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def configure_run_environment(run_dir: Path) -> None:
    cache_dir = _run_path(run_dir, "cache")
    temp_dir = _run_path(run_dir, "tmp")
    cache_dir.mkdir(exist_ok=True)
    temp_dir.mkdir(exist_ok=True)
    os.environ.update({
        "HF_HOME": str(cache_dir / "huggingface"),
        "HF_HUB_CACHE": str(cache_dir / "huggingface" / "hub"),
        "TORCH_HOME": str(cache_dir / "torch"),
        "TRITON_CACHE_DIR": str(cache_dir / "triton"),
        "XDG_CACHE_HOME": str(cache_dir),
        "TMPDIR": str(temp_dir),
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "HF_HUB_DISABLE_TELEMETRY": "1",
    })


class ProgressWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def emit(self, stage: str, **details: Any) -> None:
        event = {"time": datetime.now(timezone.utc).isoformat(), "stage": stage, **details}
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8") as output:
            output.write(line + "\n")
        print(line, flush=True)


def train(run_dir: Path) -> None:
    run_dir = _run_dir(run_dir)
    config_path = _run_path(run_dir, "config.json")
    dataset_path = _run_path(run_dir, "dataset.zip")
    metrics_path = _run_path(run_dir, "metrics.json")
    pending_metrics_path = _run_path(run_dir, "metrics.json.tmp")
    progress = ProgressWriter(_run_path(run_dir, "progress.jsonl"))
    configure_run_environment(run_dir)
    started = time.monotonic()
    metrics: dict[str, Any] = {"status": "running"}

    try:
        config = TrainingConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        config_path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
        model_id = config.dry_run_model if config.dry_run else config.base_model
        metrics.update(config=config.model_dump(mode="json"), effective_base_model=model_id)
        progress.emit("preflight", base_model=model_id)
        report = preflight(model_id)
        if not report.ready:
            raise RuntimeError("Training preflight failed: " + "; ".join(report.reasons))
        if not dataset_path.is_file():
            raise FileNotFoundError("dataset.zip is missing from the run directory.")

        progress.emit("validating_dataset")
        effective_config = config.model_copy(update={"base_model": model_id})
        dataset = load_dataset_zip(dataset_path, effective_config)
        metrics.update(dataset_hash=dataset.summary.dataset_hash, counts_per_task=dataset.summary.counts_per_task,
                       train_examples=dataset.summary.train_count, test_examples=dataset.summary.test_count,
                       excluded_examples=len(dataset.summary.too_long_rows), warnings=dataset.summary.warnings)
        progress.emit("dataset_ready", train=dataset.summary.train_count, test=dataset.summary.test_count,
                      excluded=len(dataset.summary.too_long_rows), counts_per_task=dataset.summary.counts_per_task,
                      warnings=dataset.summary.warnings)

        from datasets import Dataset
        from transformers import TrainerCallback
        from trl import SFTConfig, SFTTrainer
        from unsloth import FastLanguageModel
        from unsloth.chat_templates import train_on_responses_only

        config_parameters = inspect.signature(SFTConfig).parameters
        if "max_seq_length" not in config_parameters:
            raise RuntimeError("The installed TRL SFTConfig does not support the notebook's max_seq_length parameter.")
        trainer_parameters = inspect.signature(SFTTrainer.__init__).parameters
        tokenizer_parameter = "processing_class" if "processing_class" in trainer_parameters else "tokenizer"
        if tokenizer_parameter not in trainer_parameters:
            raise RuntimeError("The installed TRL SFTTrainer has no processing_class or tokenizer parameter.")

        progress.emit("loading_model", base_model=model_id)
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_id, max_seq_length=config.max_seq_length, load_in_4bit=True,
        )
        model = FastLanguageModel.get_peft_model(
            model, r=config.lora_r, lora_alpha=config.lora_alpha, lora_dropout=0,
            target_modules=TARGET_MODULES, use_gradient_checkpointing="unsloth", random_state=config.seed,
        )
        train_rows = Dataset.from_list([
            {"text": tokenizer.apply_chat_template(row["messages"], tokenize=False)} for row in dataset.train
        ])
        test_rows = Dataset.from_list([
            {"text": tokenizer.apply_chat_template(row["messages"], tokenize=False)} for row in dataset.test
        ])
        trainer_args = SFTConfig(
            dataset_text_field="text", max_seq_length=config.max_seq_length,
            per_device_train_batch_size=config.batch_size, gradient_accumulation_steps=config.grad_accum,
            num_train_epochs=config.epochs, max_steps=2 if config.dry_run else -1,
            learning_rate=config.learning_rate, lr_scheduler_type="cosine", warmup_ratio=0.05,
            optim="adamw_8bit", weight_decay=0.01, logging_steps=1,
            eval_strategy="epoch", save_strategy="no", seed=config.seed,
            output_dir=str(_run_path(run_dir, "trainer")), report_to="none",
        )
        trainer = SFTTrainer(
            model=model, train_dataset=train_rows, eval_dataset=test_rows,
            args=trainer_args, **{tokenizer_parameter: tokenizer},
        )
        trainer = train_on_responses_only(
            trainer, instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n",
        )

        class RunProgressCallback(TrainerCallback):
            def on_log(self, args: Any, state: Any, control: Any, logs: dict[str, Any] | None = None, **kwargs: Any) -> None:
                step = int(state.global_step)
                total = int(state.max_steps)
                elapsed = time.monotonic() - started
                eta = round(elapsed / step * (total - step)) if step and total > step else 0
                progress.emit("training", step=step, total_steps=total, loss=(logs or {}).get("loss"),
                              eval_loss=(logs or {}).get("eval_loss"), eta_seconds=eta)

        trainer.add_callback(RunProgressCallback())
        progress.emit("training", step=0, total_steps=2 if config.dry_run else None, dry_run=config.dry_run)
        result = trainer.train()

        progress.emit("saving_adapter")
        adapter_dir = _run_path(run_dir, "adapter")
        model.save_pretrained(str(adapter_dir))
        tokenizer.save_pretrained(str(adapter_dir))
        versions = {}
        for package in TRAINING_PACKAGES:
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        metrics.update(status="completed", final_loss=result.training_loss, train_metrics=result.metrics,
                       library_versions=versions, duration_seconds=round(time.monotonic() - started, 2))
        from .export import export_model

        progress.emit("exporting_gguf" if not config.dry_run else "writing_model_card")
        card = export_model(model, tokenizer, run_dir, config, metrics)
        metrics["gguf"] = card["gguf"]
        _write_metrics(metrics_path, pending_metrics_path, metrics)
        progress.emit("completed", final_loss=result.training_loss, duration_seconds=metrics["duration_seconds"])
    except Exception as error:
        message = str(error)
        if "out of memory" in message.lower():
            message = "CUDA out of memory. Reduce max_seq_length or batch_size, or use a smaller configured model."
        metrics.update(status="failed", error=message, duration_seconds=round(time.monotonic() - started, 2))
        _write_metrics(metrics_path, pending_metrics_path, metrics)
        progress.emit("failed", error=message)
        raise RuntimeError(message) from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a response-only LoRA adapter in a dedicated run directory.")
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    train(args.run_dir)


if __name__ == "__main__":
    main()