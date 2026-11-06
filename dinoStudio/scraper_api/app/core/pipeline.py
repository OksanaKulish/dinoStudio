import logging
import tempfile
import time
from pathlib import Path
from typing import Callable

from .checks import check_all
from .config import ScraperConfig
from .context import Ctx
from .export import export_project
from .history import GitHistoryProvider
from .parse_csharp import parse_cs_file
from .parse_typescript import parse_ts_file
from .scan import collect_files

ProgressCallback = Callable[[str, int], None]
logger = logging.getLogger("uvicorn.error")


def _report(progress: ProgressCallback | None, stage: str, percent: int) -> None:
	if progress is not None:
		progress(stage, max(0, min(100, percent)))


def run_pipeline(
	root: Path,
	config: ScraperConfig,
	progress: ProgressCallback | None = None,
	context_ready: Callable[[Ctx], None] | None = None,
) -> Ctx:
	"""Run the complete local repository analysis without executing repository code."""
	resolved_root = root.expanduser().resolve(strict=True)
	if not resolved_root.is_dir():
		raise ValueError("Project root must be a directory.")
	ctx = Ctx(root=resolved_root, config=config)
	_report(progress, "scanning", 2)
	started = time.monotonic()
	logger.info("scan.collect_files: starting for %s", resolved_root)
	ctx.files = collect_files(resolved_root, config)
	logger.info("scan.collect_files: found %d files in %.1fs", len(ctx.files), time.monotonic() - started)
	file_count = max(len(ctx.files), 1)
	started = time.monotonic()
	for index, path in enumerate(ctx.files, start=1):
		relative = path.relative_to(resolved_root).as_posix()
		ctx.texts[relative] = path.read_text(encoding="utf-8-sig", errors="ignore").replace("\r\n", "\n")
		_report(progress, "reading_files", 2 + round(index / file_count * 18))
		if index <= 3 or index % 50 == 0 or index == len(ctx.files):
			logger.info("reading_files: %d/%d (%s)", index, len(ctx.files), relative)
	logger.info("reading_files: completed in %.1fs", time.monotonic() - started)

	if config.use_git:
		_report(progress, "git_history", 22)
		started = time.monotonic()
		logger.info("history.GitHistoryProvider: reading repository history")
		provider = GitHistoryProvider()
		ctx.repo_top = provider.repository_top(resolved_root) or resolved_root
		ctx.history = provider.commit_history(resolved_root)
		ctx.git_sha = provider.head_sha(resolved_root)
		logger.info("history.GitHistoryProvider: completed in %.1fs (%d files with history)", time.monotonic() - started, len(ctx.history))
	else:
		logger.info("history.GitHistoryProvider: skipped (Git history disabled)")
		ctx.repo_top = None
		ctx.history = {}
		ctx.git_sha = None

	cs_classes = []
	endpoints = []
	ts_models = []
	ts_services = []
	calls = []
	source_files = [path for path in ctx.files if path.suffix.lower() in {".cs", ".ts"}]
	source_count = max(len(source_files), 1)
	started = time.monotonic()
	logger.info("parse_csharp.parse_cs_file / parse_typescript.parse_ts_file: processing %d source files", len(source_files))
	for index, path in enumerate(source_files, start=1):
		relative = path.relative_to(resolved_root).as_posix()
		source = ctx.texts[relative]
		if path.suffix.lower() == ".cs":
			parsed_classes, parsed_endpoints = parse_cs_file(source, relative)
			cs_classes.extend(parsed_classes)
			endpoints.extend(parsed_endpoints)
		elif not relative.lower().endswith(".spec.ts"):
			parsed_models, parsed_service, parsed_calls = parse_ts_file(source, relative)
			ts_models.extend(parsed_models)
			if parsed_service is not None:
				ts_services.append(parsed_service)
			calls.extend(parsed_calls)
		_report(progress, "parsing", 22 + round(index / source_count * 43))
		if index <= 3 or index % 50 == 0 or index == len(source_files):
			logger.info("parsing: %d/%d (%s)", index, len(source_files), relative)

	ctx.cs_classes = cs_classes
	ctx.endpoints = endpoints
	ctx.ts_models = ts_models
	ctx.ts_services = ts_services
	ctx.calls = calls
	logger.info("parsing: completed in %.1fs (%d endpoints, %d services, %d calls)", time.monotonic() - started, len(endpoints), len(ts_services), len(calls))
	_report(progress, "matching_and_checks", 68)
	started = time.monotonic()
	logger.info("checks.check_all: starting")
	check_all(ctx)
	logger.info("checks.check_all: completed in %.1fs (%d matches, %d issues)", time.monotonic() - started, len(ctx.matches), len(ctx.issues))
	if context_ready is not None:
		context_ready(ctx)
	output_dir = Path(tempfile.mkdtemp(prefix="scraper-export-"))
	_report(progress, "generating_examples_and_exports", 74)
	started = time.monotonic()
	logger.info("export.export_project: building examples, counting tokens and writing outputs")
	export_project(ctx, output_dir, progress)
	logger.info("export.export_project: completed in %.1fs (%d examples, %d chunks)", time.monotonic() - started, len(ctx.examples), len(ctx.chunks))
	_report(progress, "complete", 100)
	return ctx