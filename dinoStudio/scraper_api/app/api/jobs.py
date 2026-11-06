import io
import json
import logging
import threading
import time
import uuid
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ..core.config import ScraperConfig
from ..core.context import Ctx
from ..core.pipeline import run_pipeline
from ..core.scan import classify
from .workspace import ProjectWorkspace, WorkspaceStore

logger = logging.getLogger("uvicorn.error")
JobStateName = Literal["queued", "running", "completed", "failed"]
RESULT_SECTIONS = frozenset({"files", "endpoints", "calls", "dto_pairs", "matches", "issues", "examples", "coverage"})


@dataclass(slots=True)
class JobRecord:
	job_id: str
	project_id: str
	status: JobStateName
	stage: str
	percent: int
	created_at: datetime
	updated_at: datetime
	error: str | None = None
	context: Ctx | None = None
	output_dir: Path | None = None
	future: Future[None] | None = None
	events: list[dict[str, Any]] = field(default_factory=list)
	next_event_id: int = 0


def _jsonable(value: Any) -> Any:
	if hasattr(value, "__dataclass_fields__"):
		return _jsonable(asdict(value))
	if isinstance(value, Path):
		return value.as_posix()
	if isinstance(value, datetime):
		return value.isoformat()
	if isinstance(value, dict):
		return {str(key): _jsonable(item) for key, item in value.items()}
	if isinstance(value, (set, frozenset)):
		return sorted(_jsonable(item) for item in value)
	if isinstance(value, (list, tuple)):
		return [_jsonable(item) for item in value]
	return value


class JobManager:
	def __init__(self, workspaces: WorkspaceStore, max_workers: int = 2, max_jobs: int = 100) -> None:
		self.workspaces = workspaces
		self.max_jobs = max_jobs
		self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scraper-job")
		self._jobs: dict[str, JobRecord] = {}
		self._lock = threading.RLock()

	def start(self, project_id: str, config: ScraperConfig) -> JobRecord:
		workspace = self.workspaces.get(project_id)
		if workspace is None:
			raise KeyError("Project was not found.")
		with self._lock:
			if len(self._jobs) >= self.max_jobs:
				raise RuntimeError("The in-memory job limit has been reached.")
			job_id = uuid.uuid4().hex
			now = datetime.now(timezone.utc)
			job = JobRecord(
				job_id=job_id,
				project_id=project_id,
				status="queued",
				stage="queued",
				percent=0,
				created_at=now,
				updated_at=now,
			)
			self._jobs[job_id] = job
			self._event(job, "Queued for analysis")
			job.future = self._executor.submit(self._run, job, workspace, config)
			return job

	def _update(self, job: JobRecord, **changes: Any) -> None:
		with self._lock:
			for key, value in changes.items():
				setattr(job, key, value)
			job.updated_at = datetime.now(timezone.utc)

	def _event(self, job: JobRecord, message: str) -> None:
		with self._lock:
			job.next_event_id += 1
			job.events.append({"id": job.next_event_id, "time": datetime.now(timezone.utc).isoformat(), "message": message})
			del job.events[:-80]

	def _run(self, job: JobRecord, workspace: ProjectWorkspace, config: ScraperConfig) -> None:
		started_at = time.monotonic()
		logger.info("Scraper job %s started for project %s", job.job_id, job.project_id)
		self._update(job, status="running", stage="starting", percent=1)
		self._event(job, f"Analyzing {workspace.name}")
		last_logged_stage = "starting"
		last_logged_percent = 1

		def progress(stage: str, percent: int) -> None:
			nonlocal last_logged_stage, last_logged_percent
			self._update(job, stage=stage, percent=max(0, min(99, percent)))
			if stage != last_logged_stage or percent - last_logged_percent >= 10:
				logger.info("Scraper job %s: %s (%d%%)", job.job_id, stage, percent)
				self._event(job, f"{stage.replace('_', ' ')} ({percent}%)")
				last_logged_stage = stage
				last_logged_percent = percent

		def context_ready(context: Ctx) -> None:
			self._update(job, context=context)
			logger.info(
				"Scraper job %s: parsed %d files, %d endpoints, %d calls, %d issues; results available",
				job.job_id, len(context.files), len(context.endpoints), len(context.calls), len(context.issues),
			)
			self._event(job, f"Results ready: {len(context.files)} files, {len(context.endpoints)} endpoints, {len(context.calls)} calls, {len(context.issues)} issues")

		try:
			context = run_pipeline(workspace.root, config, progress, context_ready)
			self._update(
				job,
				context=context,
				output_dir=context.output_dir,
				status="completed",
				stage="completed",
				percent=100,
			)
			logger.info("Scraper job %s completed in %.1fs with %d examples", job.job_id, time.monotonic() - started_at, len(context.examples))
			self._event(job, f"Completed in {time.monotonic() - started_at:.1f}s with {len(context.examples)} examples")
		except Exception as error:
			logger.exception("Scraper job %s failed during %s after %.1fs", job.job_id, job.stage, time.monotonic() - started_at)
			self._event(job, f"Failed during {job.stage.replace('_', ' ')}: {error}")
			self._update(job, status="failed", stage="failed", error=str(error))

	def get(self, job_id: str) -> JobRecord | None:
		with self._lock:
			return self._jobs.get(job_id)

	def list_statuses(self) -> list[dict[str, Any]]:
		with self._lock:
			return [status for job_id in self._jobs if (status := self.status(job_id)) is not None]

	def status(self, job_id: str) -> dict[str, Any] | None:
		job = self.get(job_id)
		if job is None:
			return None
		return {
			"job_id": job.job_id,
			"project_id": job.project_id,
			"project_name": (workspace.name if (workspace := self.workspaces.get(job.project_id)) else None),
			"status": job.status,
			"stage": job.stage,
			"percent": job.percent,
			"results_ready": job.context is not None,
			"events": list(job.events),
			"error": job.error,
			"created_at": job.created_at.isoformat(),
			"updated_at": job.updated_at.isoformat(),
		}

	def section(
		self, job_id: str, section: str, offset: int, limit: int,
		search: str = "", sort_by: str = "", descending: bool = False,
	) -> dict[str, Any]:
		job = self.get(job_id)
		if job is None:
			raise KeyError("Job was not found.")
		if job.context is None:
			raise RuntimeError("Job results are not ready yet.")
		if section not in RESULT_SECTIONS:
			raise ValueError(f"Unknown result section: {section}.")

		items = self._section_items(job.context, section)
		if search.strip():
			query = search.strip().casefold()
			items = [item for item in items if query in json.dumps(list(item.values()), ensure_ascii=False, default=str).casefold()]
		if sort_by:
			if items and sort_by not in items[0]:
				raise ValueError(f"Unknown sort column: {sort_by}.")
			def sort_key(item: dict[str, Any]) -> tuple[int, float | str]:
				value = item.get(sort_by)
				if isinstance(value, (int, float)):
					return 0, float(value)
				return 1, str(value or "").casefold()
			items.sort(key=sort_key, reverse=descending)
		return {
			"section": section,
			"items": items[offset:offset + limit],
			"total": len(items),
			"offset": offset,
			"limit": limit,
		}

	def _section_items(self, context: Ctx, section: str) -> list[dict[str, Any]]:
		if section == "files":
			return self._file_items(context)
		if section == "coverage":
			return [
				{"extension": extension, "file_path": path, "produced_items": False}
				for extension, key in ((".cs", "cs_zero_items"), (".ts", "ts_zero_items"))
				for path in context.coverage.get(key, [])
			]
			
		values: dict[str, list[Any]] = {
			"endpoints": context.endpoints,
			"calls": context.calls,
			"dto_pairs": context.dto_pairs,
			"matches": context.matches,
			"issues": context.issues,
			"examples": context.examples,
		}
		return [_jsonable(value) for value in values[section]]

	def _file_items(self, context: Ctx) -> list[dict[str, Any]]:
		produced_cs = {item.path for item in context.cs_classes} | {item.controller_path for item in context.endpoints}
		produced_ts = (
			{item.path for item in context.ts_models}
			| {item.path for item in context.ts_services}
			| {item.service_path for item in context.calls}
		)
		items: list[dict[str, Any]] = []
		for path in context.files:
			relative = path.resolve().relative_to(context.root.resolve()).as_posix()
			try:
				size = path.stat().st_size
			except OSError:
				size = 0
			commits = context.history.get(relative, [])
			authors = {commit.author for commit in commits}
			if context.repo_top is not None:
				try:
					repo_relative = path.resolve().relative_to(context.repo_top).as_posix()
				except ValueError:
					repo_relative = relative
				commits = context.history.get(repo_relative, [])
				authors = {commit.author for commit in commits}
			extension = path.suffix.lower()
			produced = relative in (produced_cs if extension == ".cs" else produced_ts)
			items.append({
				"file_path": relative,
				"extension": extension,
				"kind": classify(relative),
				"size_bytes": size,
				"n_commits": len(commits),
				"n_authors": len(authors),
				"last_commit_date": commits[0].iso_date if commits else None,
				"last_commit_msg": commits[0].subject if commits else None,
				"produced_items": produced,
			})
		return items

	def create_download(self, job_id: str) -> io.BytesIO:
		job = self.get(job_id)
		if job is None:
			raise KeyError("Job was not found.")
		if job.status != "completed" or job.output_dir is None:
			raise RuntimeError("Job download is available after the job completes.")
		buffer = io.BytesIO()
		with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
			for path in sorted(job.output_dir.rglob("*")):
				if path.is_file():
					archive.write(path, path.relative_to(job.output_dir).as_posix())
		buffer.seek(0)
		return buffer