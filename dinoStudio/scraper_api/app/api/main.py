import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import UploadFile as FormUploadFile
from starlette.concurrency import run_in_threadpool

from ..training.config import TrainingConfig
from ..training.dataset import DatasetSummary
from ..training.preflight import PreflightReport, check_training_python
from ..training.registry import ModelRegistry, RegisteredModel
from ..training.runner import MAX_ARCHIVE_BYTES, RunManager, RunRecord
from ..training.serve import GenerationClient
from .jobs import JobManager
from .schemas import (
	DefaultConfigResponse,
	JobCreatedResponse,
	JobStatusResponse,
	LocalProjectRequest,
	ProjectResponse,
	SectionPageResponse,
	StartJobRequest,
	UploadStatusResponse,
)
from .workspace import MAX_UPLOAD_BYTES, WorkspaceStore

logger = logging.getLogger("uvicorn.error")


def _is_progress_log(line: str) -> bool:
	try:
		entry = json.loads(line)
	except json.JSONDecodeError:
		return False
	return isinstance(entry, dict) and "stage" in entry and "time" in entry


app = FastAPI(title="Local repository scraper", version="0.1.0")
app_port = os.environ.get("DINO_APP_PORT", "4300")
configured_origins = os.environ.get("DINO_APP_ORIGINS")
allowed_origins = (
	[origin.strip() for origin in configured_origins.split(",") if origin.strip()]
	if configured_origins
	else [f"http://{host}:{port}" for port in {"4300", "4310", app_port} for host in ("localhost", "127.0.0.1")]
)
app.add_middleware(
	CORSMiddleware,
	allow_origins=allowed_origins,
	allow_methods=["GET", "POST", "DELETE"],
	allow_headers=["Content-Type"],
	allow_credentials=False,
)

workspaces = WorkspaceStore()
jobs = JobManager(workspaces)
training_runs = RunManager()
model_registry = ModelRegistry()
generation = GenerationClient(model_registry)
upload_statuses: dict[str, dict] = {}
upload_lock = threading.RLock()


def update_upload(upload_id: str, stage: str, processed: int = 0, total: int = 0, error: str | None = None) -> None:
	with upload_lock:
		status = upload_statuses.setdefault(upload_id, {
			"upload_id": upload_id, "stage": stage, "processed_files": 0,
			"total_files": 0, "error": None, "events": [],
		})
		status.update(stage=stage, processed_files=processed, total_files=total, error=error)
		message = f"{stage}: {processed}/{total} files" if total else stage
		if error:
			message = f"{stage}: {error}"
		status["events"].append({
			"id": status["events"][-1]["id"] + 1 if status["events"] else 1,
			"time": datetime.now(timezone.utc).isoformat(), "message": message,
		})
		del status["events"][:-80]
		if len(upload_statuses) > 100:
			for oldest_id in list(upload_statuses):
				if upload_statuses[oldest_id]["stage"] in {"Registered project", "Failed"}:
					del upload_statuses[oldest_id]
					break


@app.get("/api/uploads/{upload_id}", response_model=UploadStatusResponse)
def get_upload_status(upload_id: UUID) -> UploadStatusResponse:
	with upload_lock:
		status = upload_statuses.get(str(upload_id))
		if status is None:
			raise HTTPException(status_code=404, detail="Upload has not reached the API yet.")
		return UploadStatusResponse(**status)


@app.get("/api/health")
def health() -> dict[str, str]:
	return {"service": "scraper-api", "version": app.version}


@app.post("/api/projects", response_model=ProjectResponse)
async def upload_project(file: UploadFile = File(...), upload_id: UUID | None = None) -> ProjectResponse:
	def report(stage: str, processed: int = 0, total_files: int = 0, error: str | None = None) -> None:
		if upload_id is not None:
			update_upload(str(upload_id), stage, processed, total_files, error)

	logger.info("Receiving project ZIP: %s", file.filename)
	report("Reading ZIP")
	chunks: list[bytes] = []
	total = 0
	try:
		while chunk := await file.read(1024 * 1024):
			total += len(chunk)
			if total > MAX_UPLOAD_BYTES:
				logger.warning("Project ZIP rejected: compressed upload exceeds %d bytes", MAX_UPLOAD_BYTES)
				report("Failed", error="ZIP upload exceeds the compressed size limit.")
				raise HTTPException(status_code=413, detail="ZIP upload exceeds the compressed size limit.")
			chunks.append(chunk)
	finally:
		await file.close()
	logger.info("Project ZIP received: %d bytes; validating and extracting", total)
	try:
		workspace = await run_in_threadpool(
			workspaces.register_zip, b"".join(chunks), file.filename or "uploaded-project.zip", report,
		)
	except ValueError as error:
		logger.warning("Project ZIP rejected: %s", error)
		report("Failed", error=str(error))
		raise HTTPException(status_code=400, detail=str(error)) from error
	except Exception:
		logger.exception("Project ZIP registration failed")
		report("Failed", error="ZIP processing failed; see the API terminal for details.")
		raise
	logger.info("Project ZIP registered: %s (%s)", workspace.project_id, workspace.name)
	report("Registered project")
	return ProjectResponse(project_id=workspace.project_id, name=workspace.name)


@app.post("/api/projects/local", response_model=ProjectResponse)
def register_local_project(request: LocalProjectRequest) -> ProjectResponse:
	try:
		workspace = workspaces.register_local_path(request.path)
	except FileNotFoundError as error:
		raise HTTPException(status_code=404, detail="The local project path does not exist.") from error
	except ValueError as error:
		raise HTTPException(status_code=400, detail=str(error)) from error
	return ProjectResponse(project_id=workspace.project_id, name=workspace.name)


@app.post("/api/jobs", response_model=JobCreatedResponse, status_code=202)
def start_job(request: StartJobRequest) -> JobCreatedResponse:
	try:
		job = jobs.start(request.project_id, request.config.to_core())
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=429, detail=str(error)) from error
	return JobCreatedResponse(job_id=job.job_id)


@app.get("/api/jobs", response_model=list[JobStatusResponse])
def list_jobs() -> list[JobStatusResponse]:
	return [JobStatusResponse(**status) for status in jobs.list_statuses()]


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
	status = jobs.status(job_id)
	if status is None:
		raise HTTPException(status_code=404, detail="Job was not found.")
	return JobStatusResponse(**status)


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str) -> StreamingResponse:
	try:
		archive = jobs.create_download(job_id)
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	return StreamingResponse(
		archive,
		media_type="application/zip",
		headers={"Content-Disposition": f'attachment; filename="scraper-job-{job_id}.zip"'},
	)


@app.get("/api/jobs/{job_id}/{section}", response_model=SectionPageResponse)
def get_job_section(
	job_id: str,
	section: str,
	offset: int = Query(default=0, ge=0),
	limit: int = Query(default=100, ge=1, le=500),
	search: str = Query(default="", max_length=200),
	sort_by: str = Query(default="", max_length=100),
	descending: bool = False,
) -> SectionPageResponse:
	try:
		page = jobs.section(job_id, section, offset, limit, search, sort_by, descending)
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except ValueError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	return SectionPageResponse(**page)


@app.get("/api/config/default", response_model=DefaultConfigResponse)
def get_default_config() -> DefaultConfigResponse:
	return DefaultConfigResponse()


@app.get("/api/training/preflight", response_model=PreflightReport)
def get_training_preflight(
	base_model: str = Query(default=TrainingConfig().base_model, min_length=1, max_length=200),
) -> PreflightReport:
	return check_training_python(base_model)


@app.get("/api/training/config/default", response_model=TrainingConfig)
def default_training_config() -> TrainingConfig:
	return TrainingConfig()


async def training_input(request: Request) -> tuple[bytes, TrainingConfig, str | None]:
	content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
	job_id: str | None = None
	try:
		if content_type == "multipart/form-data":
			async with request.form() as form:
				upload = form.get("file")
				config_data = form.get("config", "{}")
				if not isinstance(upload, FormUploadFile) or not isinstance(config_data, str):
					raise HTTPException(status_code=422, detail="Provide a ZIP file and a JSON config form field.")
				config = TrainingConfig.model_validate_json(config_data)
				chunks: list[bytes] = []
				total = 0
				while chunk := await upload.read(1024 * 1024):
					total += len(chunk)
					if total > MAX_ARCHIVE_BYTES:
						raise HTTPException(status_code=413, detail="Training ZIP exceeds the 50 MB limit.")
					chunks.append(chunk)
				archive = b"".join(chunks)
		elif content_type == "application/json":
			payload = await request.json()
			if not isinstance(payload, dict) or not isinstance(payload.get("job_id"), str):
				raise HTTPException(status_code=422, detail="Provide a scraper job_id and config object.")
			job_id = payload["job_id"]
			config = TrainingConfig.model_validate(payload.get("config", {}))
			archive = await run_in_threadpool(jobs.create_download, job_id)
			archive = archive.getvalue()
		else:
			raise HTTPException(status_code=415, detail="Use JSON with job_id or multipart form data with a ZIP file.")
	except ValidationError as error:
		raise HTTPException(status_code=422, detail=error.errors(include_context=False)) from error
	except (ValueError, json.JSONDecodeError) as error:
		raise HTTPException(status_code=422, detail=f"Invalid training request: {error}") from error
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	return archive, config, job_id


@app.post("/api/training/preview", response_model=DatasetSummary)
async def preview_training_dataset(request: Request) -> DatasetSummary:
	archive, config, _ = await training_input(request)
	try:
		return DatasetSummary(**await run_in_threadpool(training_runs.preview, archive, config))
	except ValueError as error:
		raise HTTPException(status_code=422, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/training/runs", response_model=RunRecord, status_code=202)
async def start_training_run(request: Request) -> RunRecord:
	archive, config, job_id = await training_input(request)
	try:
		return await run_in_threadpool(training_runs.start, archive, config, job_id)
	except ValueError as error:
		raise HTTPException(status_code=422, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/training/runs", response_model=list[RunRecord])
def list_training_runs() -> list[RunRecord]:
	return training_runs.list()


@app.get("/api/training/runs/{run_id}", response_model=RunRecord)
def get_training_run(run_id: UUID) -> RunRecord:
	record = training_runs.get(str(run_id))
	if record is None:
		raise HTTPException(status_code=404, detail="Training run was not found.")
	return record


@app.get("/api/training/runs/{run_id}/logs")
async def stream_training_logs(run_id: UUID, request: Request) -> StreamingResponse:
	try:
		progress_path, worker_path, eval_progress_path, eval_log_path = training_runs.log_paths(str(run_id))
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error

	async def events():
		paths = {"progress": progress_path, "log": worker_path,
		         "evaluation": eval_progress_path, "eval_log": eval_log_path}
		offsets = {name: 0 for name in paths}
		remainders = {name: "" for name in paths}
		idle_ticks = 0
		while not await request.is_disconnected():
			emitted = False
			read_data = False
			for source_name, path in paths.items():
				if not path.is_file():
					continue
				with path.open("r", encoding="utf-8", errors="replace") as source:
					source.seek(offsets[source_name])
					text = source.read(65536)
					offsets[source_name] = source.tell()
				if not text:
					continue
				read_data = True
				lines = (remainders[source_name] + text).split("\n")
				remainders[source_name] = lines.pop()
				for line in lines:
					if not line.strip():
						continue
					if source_name in {"progress", "evaluation"}:
						try:
							entry = json.loads(line)
						except json.JSONDecodeError:
							continue
					else:
						if _is_progress_log(line):
							continue
						entry = {"message": line.rstrip("\r")}
					yield f"event: {source_name}\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n"
					emitted = True
			status = training_runs.get(str(run_id))
			if not read_data and (status is None or (status.status in {"completed", "failed", "cancelled"}
			                      and status.eval_status not in {"starting", "running", "cancelling"})):
				for source_name, line in remainders.items():
					if line.strip():
						yield f"event: {source_name}\ndata: {json.dumps({'message': line}, ensure_ascii=False)}\n\n"
				yield "event: end\ndata: {}\n\n"
				break
			idle_ticks = 0 if emitted else idle_ticks + 1
			if idle_ticks >= 15:
				yield ": keepalive\n\n"
				idle_ticks = 0
			await asyncio.sleep(1)

	return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/training/runs/{run_id}/evaluate", response_model=RunRecord, status_code=202)
async def evaluate_training_run(run_id: UUID) -> RunRecord:
	try:
		return await run_in_threadpool(training_runs.evaluate, str(run_id))
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/training/runs/{run_id}/eval")
async def get_training_evaluation(run_id: UUID) -> dict:
	try:
		return await run_in_threadpool(training_runs.evaluation_report, str(run_id))
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/training/runs/{run_id}/cancel", response_model=RunRecord)
def cancel_training_run(run_id: UUID) -> RunRecord:
	try:
		return training_runs.cancel(str(run_id))
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except RuntimeError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error


@app.delete("/api/training/runs/{run_id}", status_code=204)
def delete_training_run(run_id: UUID) -> Response:
	try:
		if model_registry.active_id() == str(run_id):
			generation.reset()
		training_runs.delete(str(run_id))
		model_registry.clear_if_active(str(run_id))
	except KeyError as error:
		raise HTTPException(status_code=404, detail=str(error)) from error
	except (RuntimeError, ValueError) as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	return Response(status_code=204)


@app.get("/api/training/models", response_model=list[RegisteredModel])
def list_registered_models() -> list[RegisteredModel]:
	return model_registry.list()


@app.get("/api/training/models/active", response_model=RegisteredModel)
def get_active_model() -> RegisteredModel:
	try:
		return model_registry.active()[0]
	except (ValueError, KeyError) as error:
		raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/training/runs/{run_id}/download")
async def download_trained_model(run_id: UUID) -> FileResponse:
	try:
		archive = await run_in_threadpool(model_registry.bundle, str(run_id))
	except (ValueError, KeyError) as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	return FileResponse(archive, media_type="application/zip", filename=f"trained-model-{run_id}.zip")


@app.post("/api/training/runs/{run_id}/activate", response_model=RegisteredModel)
def activate_trained_model(run_id: UUID) -> RegisteredModel:
	try:
		model = model_registry.activate(str(run_id))
		generation.reset()
		return model
	except (ValueError, KeyError) as error:
		raise HTTPException(status_code=409, detail=str(error)) from error


class GenerateRequest(BaseModel):
	prompt: str = Field(min_length=1, max_length=100_000)
	max_tokens: int = Field(default=1500, ge=1, le=4096)


class GenerateResponse(BaseModel):
	run_id: str
	content: str
	usage: dict | None = None


@app.post("/api/generate", response_model=GenerateResponse)
async def generate_from_active_model(request: GenerateRequest) -> GenerateResponse:
	try:
		result = await run_in_threadpool(generation.generate, request.prompt, request.max_tokens)
		return GenerateResponse(**result)
	except ValueError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	except RuntimeError as error:
		status = 504 if "timed out" in str(error).lower() else 503
		raise HTTPException(status_code=status, detail=str(error)) from error