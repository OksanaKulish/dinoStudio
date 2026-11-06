import argparse
import json
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Any

from ..core.checks import check_all
from ..core.config import ScraperConfig
from ..core.context import Ctx
from ..core.parse_csharp import parse_cs_file
from ..core.parse_typescript import parse_ts_file
from .config import TrainingConfig
from .dataset import load_dataset_zip
from .preflight import preflight
from .train import ProgressWriter, _run_dir, _run_path, configure_run_environment

FENCE = re.compile(r"```(?P<language>csharp|cs|typescript|ts)\s*\n(?P<code>.*?)```", re.IGNORECASE | re.DOTALL)


@dataclass(slots=True)
class ParsedCode:
    classes: list[Any] = field(default_factory=list)
    endpoints: list[Any] = field(default_factory=list)
    models: list[Any] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)


def _parse_code(text: str, prefix: str) -> ParsedCode:
    parsed = ParsedCode()
    snippets = [(match.group("language").lower(), match.group("code")) for match in FENCE.finditer(text)]
    if not snippets and re.search(r"\b(class|interface|record|struct)\b", text):
        snippets = [("ts" if "export " in text else "cs", text)]
    for index, (language, code) in enumerate(snippets):
        if language in {"cs", "csharp"}:
            classes, endpoints = parse_cs_file(code, f"{prefix}_{index}.cs")
            parsed.classes.extend(classes)
            parsed.endpoints.extend(endpoints)
        else:
            models, _, calls = parse_ts_file(code, f"{prefix}_{index}.ts")
            parsed.models.extend(models)
            parsed.calls.extend(calls)
    return parsed


def _normalize_dto(text: str) -> str:
    code = [match.group("code") for match in FENCE.finditer(text)
            if match.group("language").lower() in {"ts", "typescript"}]
    return re.sub(r"\s+", " ", "\n".join(code) if code else text).strip()


def _score(row: dict[str, Any], output: str, root: Path) -> dict[str, Any]:
    expected_text = row["messages"][2]["content"]
    expected = _parse_code(expected_text, "expected")
    actual = _parse_code(output, "generated")
    expected_kinds = (
        bool(expected.classes), bool(expected.endpoints), bool(expected.models), bool(expected.calls),
    )
    actual_kinds = (bool(actual.classes), bool(actual.endpoints), bool(actual.models), bool(actual.calls))
    parse_success = all(found for required, found in zip(expected_kinds, actual_kinds) if required) if any(expected_kinds) else None

    expected_endpoints = {(endpoint.verb, endpoint.route_norm) for endpoint in expected.endpoints}
    actual_endpoints = {(endpoint.verb, endpoint.route_norm) for endpoint in actual.endpoints}
    endpoint_coverage = (len(expected_endpoints & actual_endpoints) / len(expected_endpoints)) if expected_endpoints else None

    consistency_errors: int | None = None
    prompt = _parse_code(row["messages"][1]["content"], "prompt")
    if (actual.endpoints and actual.calls) or (row["meta"]["task_type"] == "dto_to_ts" and prompt.classes and actual.models):
        context = Ctx(root=root, config=ScraperConfig(use_git=False))
        context.cs_classes = actual.classes + (prompt.classes if row["meta"]["task_type"] == "dto_to_ts" else [])
        context.endpoints = actual.endpoints
        context.calls = actual.calls
        context.ts_models = actual.models
        consistency_errors = sum(issue.severity in {"error", "warning"} for issue in check_all(context))

    dto_exact: bool | None = None
    dto_similarity: float | None = None
    if row["meta"]["task_type"] == "dto_to_ts":
        normalized_expected = _normalize_dto(expected_text)
        normalized_actual = _normalize_dto(output)
        dto_exact = normalized_expected == normalized_actual
        dto_similarity = round(SequenceMatcher(None, normalized_expected, normalized_actual).ratio(), 4)
    return {"parse_success": parse_success, "endpoint_coverage": endpoint_coverage,
            "consistency_errors": consistency_errors, "dto_exact_match": dto_exact, "dto_similarity": dto_similarity}


def _aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    def average(key: str) -> float | None:
        values = [float(score[key]) for score in scores if score[key] is not None]
        return round(mean(values), 4) if values else None

    summary = {"examples": len(scores), "parse_success_rate": average("parse_success"),
               "endpoint_coverage": average("endpoint_coverage"),
               "consistency_errors": average("consistency_errors"),
               "dto_exact_match_rate": average("dto_exact_match"),
               "dto_similarity": average("dto_similarity")}
    components = [summary["parse_success_rate"], summary["endpoint_coverage"], summary["dto_similarity"]]
    if summary["consistency_errors"] is not None:
        components.append(1 / (1 + summary["consistency_errors"]))
    summary["quality_score"] = round(mean(value for value in components if value is not None), 4) if any(
        value is not None for value in components
    ) else None
    return summary


def evaluate(run_dir: Path) -> None:
    run_dir = _run_dir(run_dir)
    configure_run_environment(run_dir)
    progress = ProgressWriter(_run_path(run_dir, "eval_progress.jsonl"))
    report_path = _run_path(run_dir, "eval_report.json")
    pending_path = _run_path(run_dir, "eval_report.json.tmp")
    adapter_dir = _run_path(run_dir, "adapter")
    if not adapter_dir.is_dir() or report_path.exists():
        raise ValueError("Evaluation needs a saved adapter and must not overwrite an existing report.")
    config = TrainingConfig.model_validate_json(_run_path(run_dir, "config.json").read_text(encoding="utf-8"))
    metrics = json.loads(_run_path(run_dir, "metrics.json").read_text(encoding="utf-8"))
    if metrics.get("status") != "completed":
        raise ValueError("A completed training run is required before evaluation.")
    model_id = metrics["effective_base_model"]
    if model_id != (config.dry_run_model if config.dry_run else config.base_model):
        raise ValueError("The trained model does not match the saved run config.")
    preflight_report = preflight(model_id)
    if not preflight_report.ready:
        raise RuntimeError("Evaluation preflight failed: " + "; ".join(preflight_report.reasons))

    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    import torch

    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
    dataset = load_dataset_zip(_run_path(run_dir, "dataset.zip"),
                               config.model_copy(update={"base_model": model_id}), tokenizer)
    if dataset.summary.dataset_hash != metrics.get("dataset_hash"):
        raise ValueError("The held-out dataset does not match the training dataset hash.")
    rows = dataset.test
    progress.emit("evaluating_base", step=0, total_steps=len(rows))
    model_options: dict[str, Any] = {"device_map": "auto", "trust_remote_code": False}
    if "bnb-4bit" not in model_id.lower():
        model_options["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True)
    base_model = AutoModelForCausalLM.from_pretrained(model_id, **model_options)
    base_model.eval()

    def generate(model: Any, row: dict[str, Any]) -> str:
        messages = row["messages"][:2]
        tokens = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_tensors="pt")
        input_ids = tokens.to(model.get_input_embeddings().weight.device)
        available = config.max_seq_length - input_ids.shape[-1]
        if available < 1:
            raise ValueError(f"Prompt for example {row['meta']['id']} exceeds the model context budget.")
        with torch.no_grad():
            generated = model.generate(input_ids, max_new_tokens=min(512, available), do_sample=False,
                                       pad_token_id=tokenizer.eos_token_id)
        return tokenizer.decode(generated[0][input_ids.shape[-1]:], skip_special_tokens=True).strip()

    started = time.monotonic()
    base_outputs: list[str] = []
    for index, row in enumerate(rows, start=1):
        base_outputs.append(generate(base_model, row))
        progress.emit("evaluating_base", step=index, total_steps=len(rows),
                      eta_seconds=round((time.monotonic() - started) / index * (len(rows) - index)))

    progress.emit("loading_adapter")
    tuned_model = PeftModel.from_pretrained(base_model, str(adapter_dir), is_trainable=False)
    tuned_model.eval()
    progress.emit("evaluating_fine_tuned", step=0, total_steps=len(rows))
    started = time.monotonic()
    fine_outputs: list[str] = []
    for index, row in enumerate(rows, start=1):
        fine_outputs.append(generate(tuned_model, row))
        progress.emit("evaluating_fine_tuned", step=index, total_steps=len(rows),
                      eta_seconds=round((time.monotonic() - started) / index * (len(rows) - index)))

    examples = []
    for row, base_output, fine_output in zip(rows, base_outputs, fine_outputs):
        examples.append({"id": row["meta"]["id"], "task_type": row["meta"]["task_type"],
                         "feature": row["meta"]["feature"], "prompt": row["messages"][1]["content"],
                         "expected": row["messages"][2]["content"], "base_output": base_output,
                         "fine_tuned_output": fine_output,
                         "base_scores": _score(row, base_output, run_dir),
                         "fine_tuned_scores": _score(row, fine_output, run_dir)})
    by_task = {}
    for task in sorted({example["task_type"] for example in examples}):
        task_rows = [example for example in examples if example["task_type"] == task]
        by_task[task] = {"base": _aggregate([example["base_scores"] for example in task_rows]),
                         "fine_tuned": _aggregate([example["fine_tuned_scores"] for example in task_rows])}
    report = {"base_model": model_id, "dataset_hash": dataset.summary.dataset_hash,
              "generation": {"do_sample": False, "max_new_tokens_cap": 512},
              "scoring": "Parser-recognized code (not compilation); applicable verb/route coverage; existing checker errors and warnings; normalized DTO similarity.",
              "overall": {"base": _aggregate([item["base_scores"] for item in examples]),
                          "fine_tuned": _aggregate([item["fine_tuned_scores"] for item in examples])},
              "by_task_type": by_task, "examples": examples}
    pending_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending_path.replace(report_path)
    progress.emit("evaluation_completed", examples=len(examples),
                  base_score=report["overall"]["base"]["quality_score"],
                  fine_tuned_score=report["overall"]["fine_tuned"]["quality_score"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare base and LoRA model outputs on held-out scraper examples.")
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        evaluate(args.run_dir)
    except Exception as error:
        try:
            run_dir = _run_dir(args.run_dir)
            ProgressWriter(_run_path(run_dir, "eval_progress.jsonl")).emit("evaluation_failed", error=str(error))
        except (OSError, ValueError):
            pass
        raise


if __name__ == "__main__":
    main()