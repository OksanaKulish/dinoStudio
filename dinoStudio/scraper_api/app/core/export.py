import hashlib
import json
import re
import textwrap
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Pattern

from .config import ScraperConfig
from .context import Ctx
from .models import Chunk, Example
from .scan import classify
from .tasks import build_all
from .tasks.common import feature_of_ts

TOOL_VERSION = "0.1.0"
_ISSUE_COLUMNS = ("severity", "rule", "detail", "controller", "action", "service", "method")
_SECRET_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    (
        "connection_string_password",
        re.compile(r"(?i)\b(?:server|data source)\s*=\s*[^;\r\n]+(?:;[^;\r\n]*)*;\s*(?:password|pwd)\s*=\s*[^;\r\n]+"),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?P<key>api[_-]?key|client[_-]?secret|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|pwd)"
            r"\b\s*[:=]\s*(?P<quote>[\"']?)(?P<value>[A-Za-z0-9_./+=:-]{8,})"
        ),
    ),
)
_PLACEHOLDER_PREFIXES = (
    "your_", "your-", "example", "placeholder", "changeme", "change_me", "redacted", "<", "${", "xxx",
)


@dataclass(frozen=True, slots=True)
class SecretHit:
    example_id: str
    task_type: str
    field: str
    rule: str
    excerpt: str


class SecretScanError(ValueError):
    def __init__(self, hits: list[SecretHit]) -> None:
        self.hits = hits
        summary = "\n".join(
            f"- {hit.example_id} [{hit.task_type}] {hit.field}: {hit.rule} ({hit.excerpt})"
            for hit in hits
        )
        super().__init__(f"Secret scan failed with {len(hits)} hit(s): {summary}")


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or any(normalized.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


def scan_examples(examples: list[Example]) -> list[SecretHit]:
    hits: list[SecretHit] = []
    for example in examples:
        example_id = example.id or hashlib.sha1((example.task_type + example.prompt).encode("utf-8")).hexdigest()[:10]
        for field_name, content in (("prompt", example.prompt), ("response", example.response)):
            for rule, pattern in _SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    value = match.groupdict().get("value", match.group(0))
                    if rule == "credential_assignment":
                        if _is_placeholder(value):
                            continue
                        key = match.group("key").replace("_", "").replace("-", "").lower()
                        if not match.group("quote") and key == value.replace("_", "").replace("-", "").lower():
                            continue
                    hits.append(SecretHit(
                        example_id=example_id,
                        task_type=example.task_type,
                        field=field_name,
                        rule=rule,
                        excerpt=f"{content[max(0, match.start() - 24):match.start()]}[REDACTED]",
                    ))
    return hits


def _count_tokens(
    examples: list[Example],
    config: ScraperConfig,
    progress: Callable[[str, int], None] | None = None,
) -> None:
    try:
        from huggingface_hub import try_to_load_from_cache
        from transformers import AutoTokenizer
    except ImportError as error:
        raise RuntimeError("Token counting requires Transformers in the local Python environment.") from error

    tokenizer_file = try_to_load_from_cache(config.tokenizer_model, "tokenizer.json")
    if not isinstance(tokenizer_file, str):
        raise RuntimeError(
            f"Tokenizer {config.tokenizer_model!r} is not cached locally. Cache it before running export; "
            "the pipeline does not access the network."
        )
    tokenizer_directory = Path(tokenizer_file).parent
    if progress is not None:
        progress("loading_tokenizer", 75)
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            str(tokenizer_directory),
            local_files_only=True,
            trust_remote_code=False,
        )
    except Exception as error:
        raise RuntimeError(
            f"Could not load cached tokenizer {config.tokenizer_model!r} from {tokenizer_directory}."
        ) from error

    total = max(len(examples), 1)
    for index, example in enumerate(examples, start=1):
        example.id = hashlib.sha1((example.task_type + example.prompt).encode("utf-8")).hexdigest()[:10]
        token_text = config.system_msg + example.prompt + example.response
        example.n_tokens = len(tokenizer.encode(token_text, add_special_tokens=False))
        example.too_long = example.n_tokens > config.max_tokens
        example.split = "test" if example.feature in config.test_features else "train"
        if progress is not None:
            progress("counting_tokens", 76 + round(index / total * 8))


def _deduplicate_examples(examples: list[Example]) -> list[Example]:
    seen: set[str] = set()
    unique: list[Example] = []
    for example in examples:
        if example.id in seen:
            continue
        seen.add(example.id)
        unique.append(example)
    return unique


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_to_jsonable(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _config_hash(config: ScraperConfig) -> str:
    serialized = json.dumps(_to_jsonable(asdict(config)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_to_jsonable(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, examples: list[Example], system_msg: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for example in examples:
            row = {
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": example.prompt},
                    {"role": "assistant", "content": example.response},
                ],
                "meta": {
                    "id": example.id,
                    "task_type": example.task_type,
                    "feature": example.feature,
                    "source": example.source,
                    "split": example.split,
                },
            }
            output.write(json.dumps(row, ensure_ascii=False) + "\n")


def _project_model(ctx: Ctx) -> dict[str, Any]:
    files = []
    root = ctx.root.resolve()
    for path in ctx.files:
        try:
            relative = path.resolve().relative_to(root).as_posix()
            size = path.stat().st_size
        except (OSError, ValueError):
            continue
        files.append({
            "file_path": relative,
            "extension": path.suffix.lower(),
            "kind": classify(relative),
            "size_bytes": size,
        })
    return {
        "files": files,
        "csharp_classes": ctx.cs_classes,
        "endpoints": ctx.endpoints,
        "calls": ctx.calls,
        "dtos": ctx.cs_dtos,
        "ts_models": ctx.ts_models,
        "dto_pairs": ctx.dto_pairs,
        "matches": ctx.matches,
        "issues": ctx.issues,
        "coverage": ctx.coverage,
    }


def _build_chunks(ctx: Ctx) -> list[Chunk]:
    chunks: list[Chunk] = []

    def add_chunk(chunk_id: str, kind: str, feature: str, path: str, summary: str, body: str, language: str) -> None:
        document = f"{summary}\nFile: {path}\n\n{body}"
        chunks.append(Chunk(
            id=chunk_id,
            text=document,
            kind=kind,
            feature=feature,
            path=path,
            lang=language,
            hash=hashlib.sha1(document.encode("utf-8")).hexdigest()[:16],
        ))

    for endpoint in ctx.endpoints:
        add_chunk(
            f"ep::{endpoint.id}",
            "endpoint",
            endpoint.feature,
            endpoint.controller_path,
            f"ASP.NET endpoint {endpoint.verb} {endpoint.route} ({endpoint.controller}.{endpoint.action}), "
            f"auth: {endpoint.auth}, body: {endpoint.body_type}, query: {endpoint.query_params}",
            textwrap.dedent(endpoint.code),
            "csharp",
        )

    for call in ctx.calls:
        add_chunk(
            f"call::{call.service}.{call.method}",
            "service_method",
            call.feature,
            call.service_path,
            f"Angular method {call.service}.{call.method} calls {call.verb} {call.route}",
            call.code,
            "typescript",
        )

    for match in ctx.matches:
        endpoint = ctx.endpoints[match.ep_i]
        call = ctx.calls[match.call_i]
        add_chunk(
            f"pair::{endpoint.id}",
            "api_pair",
            endpoint.feature,
            endpoint.controller_path,
            f"{endpoint.verb} {endpoint.route} is implemented by {endpoint.controller}.{endpoint.action} "
            f"and called by {call.service}.{call.method}",
            "// BACKEND\n" + textwrap.dedent(endpoint.code) + "\n\n// FRONTEND\n" + call.code,
            "mixed",
        )

    for dto in ctx.cs_dtos:
        add_chunk(f"cs::{dto.name}", "csharp_dto", "shared", dto.path, f"C# class {dto.name}", textwrap.dedent(dto.code), "csharp")
    for model in ctx.ts_models:
        add_chunk(
            f"ts::{model.name}", "ts_model", feature_of_ts(model.path), model.path,
            f"TypeScript model {model.name}", model.code, "typescript",
        )
    for finding in ctx.issues:
        if finding.severity != "info":
            add_chunk(
                f"issue::{finding.rule}::{finding.action or finding.method}",
                "finding", "shared", "consistency_report", f"Known issue ({finding.rule})", finding.detail, "text",
            )
    ctx.chunks = chunks
    return chunks


def _build_coverage(ctx: Ctx) -> dict[str, list[str]]:
    csharp_produced = {item.path for item in ctx.cs_classes} | {item.controller_path for item in ctx.endpoints}
    typescript_produced = (
        {item.path for item in ctx.ts_models}
        | {item.path for item in ctx.ts_services}
        | {item.service_path for item in ctx.calls}
    )
    csharp_zero: list[str] = []
    typescript_zero: list[str] = []
    root = ctx.root.resolve()
    for path in ctx.files:
        suffix = path.suffix.lower()
        if suffix not in {".cs", ".ts"}:
            continue
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if suffix == ".cs" and relative not in csharp_produced:
            csharp_zero.append(relative)
        elif suffix == ".ts" and relative not in typescript_produced:
            typescript_zero.append(relative)
    ctx.coverage = {
        "cs_zero_items": sorted(csharp_zero),
        "ts_zero_items": sorted(typescript_zero),
    }
    return ctx.coverage


def export_project(
    ctx: Ctx,
    output_dir: Path,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Path]:
    """Build tasks, scan secrets, count tokens locally, then write notebook artifacts."""
    output_dir = output_dir.resolve()
    ctx.output_dir = output_dir
    if progress is not None:
        progress("building_examples", 75)
    examples = build_all(ctx)
    _build_chunks(ctx)
    _build_coverage(ctx)
    _count_tokens(examples, ctx.config, progress)
    examples = _deduplicate_examples(examples)
    ctx.examples = examples

    if progress is not None:
        progress("secret_scan", 85)
    secret_hits = scan_examples(examples)
    if secret_hits:
        raise SecretScanError(secret_hits)

    output_dir.mkdir(parents=True, exist_ok=True)
    if progress is not None:
        progress("writing_exports", 88)
    outputs: dict[str, Path] = {}
    all_path = output_dir / "pairs_all.jsonl"
    _write_jsonl(all_path, examples, ctx.config.system_msg)
    outputs[all_path.name] = all_path
    export_count = max(len(examples), 1)
    export_index = 0

    for split_name in ("train", "test"):
        split_examples = [example for example in examples if example.split == split_name and not example.too_long]
        split_path = output_dir / f"{split_name}.jsonl"
        _write_jsonl(split_path, split_examples, ctx.config.system_msg)
        outputs[split_path.name] = split_path
        export_index += 1
        if progress is not None:
            progress("writing_exports", 88 + round(export_index / (export_count + 10) * 9))

    for task_type in sorted({example.task_type for example in examples}):
        task_examples = [example for example in examples if example.task_type == task_type]
        task_path = output_dir / f"pairs_{task_type}.jsonl"
        _write_jsonl(task_path, task_examples, ctx.config.system_msg)
        outputs[task_path.name] = task_path
        export_index += 1
        if progress is not None:
            progress("writing_exports", 88 + round(export_index / (export_count + 10) * 9))

    chunks_path = output_dir / "chunks.jsonl"
    with chunks_path.open("w", encoding="utf-8", newline="\n") as output:
        for chunk in ctx.chunks:
            output.write(json.dumps(_to_jsonable(chunk), ensure_ascii=False) + "\n")
    outputs[chunks_path.name] = chunks_path

    project_model_path = output_dir / "project_model.json"
    _write_json(project_model_path, _project_model(ctx))
    outputs[project_model_path.name] = project_model_path

    import pandas as pd

    issue_rows = [{column: getattr(item, column) for column in _ISSUE_COLUMNS} for item in ctx.issues]
    report_path = output_dir / "consistency_report.csv"
    pd.DataFrame.from_records(issue_rows, columns=_ISSUE_COLUMNS).to_csv(report_path, index=False)
    outputs[report_path.name] = report_path

    manifest = {
        "git_sha": ctx.git_sha,
        "config_hash": _config_hash(ctx.config),
        "secret_scan_passed": True,
        "counts_per_task": dict(sorted(Counter(example.task_type for example in examples).items())),
        "tool_version": TOOL_VERSION,
        "timestamp": datetime.now(timezone.utc),
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    outputs[manifest_path.name] = manifest_path
    if progress is not None:
        progress("writing_exports", 99)
    return outputs