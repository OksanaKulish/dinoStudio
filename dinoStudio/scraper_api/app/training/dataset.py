import argparse
import hashlib
import json
import os
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import TrainingConfig

MAX_JSONL_BYTES = 100_000_000
MAX_LINE_BYTES = 4_000_000
MAX_ROWS = 200_000


class DatasetValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    dataset_hash: str
    counts_per_task: dict[str, dict[str, int]]
    train_count: int
    test_count: int
    too_long_rows: list[dict[str, Any]]
    warnings: list[str]
    manifest: dict[str, Any]


@dataclass(slots=True)
class TrainingDataset:
    train: list[dict[str, Any]]
    test: list[dict[str, Any]]
    summary: DatasetSummary


def _read_split(archive: zipfile.ZipFile, name: str, digest: Any) -> list[dict[str, Any]]:
    info = archive.getinfo(name)
    if info.file_size > MAX_JSONL_BYTES:
        raise DatasetValidationError(f"{name} exceeds the JSONL size limit.")
    rows: list[dict[str, Any]] = []
    consumed = 0
    split = name.removesuffix(".jsonl")
    with archive.open(info) as source:
        while line := source.readline(MAX_LINE_BYTES + 1):
            consumed += len(line)
            if len(line) > MAX_LINE_BYTES or consumed > MAX_JSONL_BYTES:
                raise DatasetValidationError(f"{name} contains a row or file that exceeds the size limit.")
            digest.update(line)
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DatasetValidationError(f"{name} line {len(rows) + 1} is not valid UTF-8 JSON.") from error
            if not isinstance(row, dict) or not isinstance(row.get("messages"), list) or not isinstance(row.get("meta"), dict):
                raise DatasetValidationError(f"{name} line {len(rows) + 1} needs messages and meta objects.")
            messages = row["messages"]
            if len(messages) != 3 or any(
                not isinstance(message, dict) or message.get("role") != role
                or not isinstance(message.get("content"), str) or not message["content"].strip()
                for message, role in zip(messages, ("system", "user", "assistant"))
            ):
                raise DatasetValidationError(f"{name} line {len(rows) + 1} needs nonempty system, user, assistant messages in order.")
            meta = row["meta"]
            if any(not isinstance(meta.get(key), str) or not meta[key].strip()
                   for key in ("id", "task_type", "feature", "source")) or meta.get("split") != split:
                raise DatasetValidationError(f"{name} line {len(rows) + 1} has invalid id, task_type, feature, source or split metadata.")
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise DatasetValidationError(f"{name} exceeds the row limit.")
    return rows


def load_dataset_zip(zip_path: str | Path, config: TrainingConfig, tokenizer: Any | None = None) -> TrainingDataset:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            if any(names.count(name) != 1 for name in ("manifest.json", "train.jsonl", "test.jsonl")):
                raise DatasetValidationError("ZIP must contain exactly one manifest.json, train.jsonl and test.jsonl at its root.")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > 1_000_000:
                raise DatasetValidationError("Manifest is too large.")
            manifest = json.loads(archive.read(manifest_info))
            if not isinstance(manifest, dict) or manifest.get("secret_scan_passed") is not True:
                raise DatasetValidationError("Manifest has no confirmed secret-scan pass. Re-run the scraper with the updated exporter.")
            digest = hashlib.sha256()
            digest.update(b"train.jsonl\0")
            train_rows = _read_split(archive, "train.jsonl", digest)
            digest.update(b"test.jsonl\0")
            test_rows = _read_split(archive, "test.jsonl", digest)
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DatasetValidationError("Training input must be a readable ZIP with a valid UTF-8 manifest.") from error

    ids: set[str] = set()
    for row in train_rows + test_rows:
        identifier = row["meta"]["id"]
        if identifier in ids:
            raise DatasetValidationError(f"Duplicate example id: {identifier}.")
        ids.add(identifier)
    overlap = {row["meta"]["feature"] for row in train_rows} & {row["meta"]["feature"] for row in test_rows}
    if overlap:
        raise DatasetValidationError(f"Train/test feature leakage: {', '.join(sorted(overlap))}.")

    if tokenizer is None:
        try:
            from transformers import AutoTokenizer
        except ImportError as error:
            raise DatasetValidationError("The separate training environment needs Transformers for real token counts.") from error
        tokenizer_source = config.base_model
        offline_preview = os.environ.get("DINO_PREVIEW_OFFLINE") == "1"
        if offline_preview:
            from huggingface_hub import try_to_load_from_cache

            cached = try_to_load_from_cache(config.base_model, "tokenizer.json")
            if not isinstance(cached, str):
                raise DatasetValidationError(
                    f"The tokenizer for {config.base_model!r} is not cached in the scraper API environment. "
                    "Select a cached dry-run model or cache this exact model tokenizer before previewing."
                )
            tokenizer_source = str(Path(cached).parent)
        try:
            tokenizer = AutoTokenizer.from_pretrained(tokenizer_source, local_files_only=offline_preview, trust_remote_code=False)
        except Exception as error:
            raise DatasetValidationError(f"Could not load the tokenizer for configured base model {config.base_model!r}.") from error

    kept: dict[str, list[dict[str, Any]]] = {"train": [], "test": []}
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"train": 0, "test": 0})
    too_long: list[dict[str, Any]] = []
    for split, rows in (("train", train_rows), ("test", test_rows)):
        for row in rows:
            meta = row["meta"]
            if config.task_types and meta["task_type"] not in config.task_types:
                continue
            try:
                token_count = len(tokenizer.apply_chat_template(
                    row["messages"], tokenize=True, add_generation_prompt=False,
                ))
            except Exception as error:
                raise DatasetValidationError(f"Tokenization failed for {split} example {meta['id']} with {config.base_model!r}.") from error
            if token_count > config.max_seq_length:
                too_long.append({"id": meta["id"], "split": split, "task_type": meta["task_type"],
                                 "feature": meta["feature"], "tokens": token_count})
                continue
            kept[split].append(row)
            counts[meta["task_type"]][split] += 1

    if not kept["train"] or not kept["test"]:
        raise DatasetValidationError("Both train and held-out test splits need at least one valid example after filtering and token limits.")
    warnings = []
    if len(kept["train"]) < 100:
        warnings.append(f"Only {len(kept['train'])} training examples; fewer than 100 may not fine-tune reliably.")
    if too_long:
        warnings.append(f"Dropped {len(too_long)} rows exceeding {config.max_seq_length} model tokens.")
    summary = DatasetSummary(digest.hexdigest(), dict(sorted(counts.items())), len(kept["train"]),
                             len(kept["test"]), too_long, warnings, manifest)
    return TrainingDataset(kept["train"], kept["test"], summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a scraper ZIP with the configured model tokenizer.")
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--config", type=Path, help="JSON file containing TrainingConfig fields")
    args = parser.parse_args()
    config = TrainingConfig.model_validate_json(args.config.read_text(encoding="utf-8")) if args.config else TrainingConfig()
    print(json.dumps(asdict(load_dataset_zip(args.zip_path, config).summary), indent=2))


if __name__ == "__main__":
    main()