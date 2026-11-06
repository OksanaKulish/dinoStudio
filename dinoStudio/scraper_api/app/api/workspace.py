import io
import logging
import os
import shutil
import stat
import tempfile
import threading
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable


MAX_UPLOAD_BYTES = 50_000_000
MAX_FILES = 10_000
MAX_UNCOMPRESSED_BYTES = 500_000_000
MAX_FILE_BYTES = 200_000_000
MAX_COMPRESSION_RATIO = 1_000
logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True, slots=True)
class ProjectWorkspace:
	project_id: str
	root: Path
	name: str
	storage_dir: Path | None = None


class WorkspaceStore:
	def __init__(self, storage_dir: Path | None = None) -> None:
		configured_storage = os.environ.get("SCRAPER_STORAGE_DIR")
		if storage_dir is not None:
			self.storage_dir = storage_dir
		elif configured_storage:
			self.storage_dir = Path(configured_storage)
		else:
			self.storage_dir = Path(tempfile.gettempdir()) / "scraper-api"
		self.storage_dir = self.storage_dir.expanduser().resolve()
		self.storage_dir.mkdir(parents=True, exist_ok=True)
		self._projects: dict[str, ProjectWorkspace] = {}
		self._lock = threading.RLock()

	def _allowed_roots(self) -> tuple[Path, ...]:
		configured = os.environ.get("SCRAPER_ALLOWED_ROOTS")
		roots = [Path(item).expanduser().resolve() for item in configured.split(os.pathsep) if item.strip()] if configured else [Path.cwd().resolve()]
		return tuple(roots)

	def register_local_path(self, path: str) -> ProjectWorkspace:
		candidate = Path(path).expanduser().resolve(strict=True)
		if not candidate.is_dir():
			raise ValueError("The local project path must be a directory.")
		if not any(candidate == root or root in candidate.parents for root in self._allowed_roots()):
			raise ValueError("The local project path is outside SCRAPER_ALLOWED_ROOTS.")
		project_id = uuid.uuid4().hex
		workspace = ProjectWorkspace(project_id=project_id, root=candidate, name=candidate.name or str(candidate))
		with self._lock:
			self._projects[project_id] = workspace
		return workspace

	def register_zip(
		self, archive: bytes, filename: str,
		progress: Callable[[str, int, int], None] | None = None,
	) -> ProjectWorkspace:
		if len(archive) > MAX_UPLOAD_BYTES:
			raise ValueError(f"ZIP upload exceeds the {MAX_UPLOAD_BYTES // 1_000_000} MB limit.")
		project_id = uuid.uuid4().hex
		destination = (self.storage_dir / project_id).resolve()
		destination.mkdir(parents=True, exist_ok=False)
		try:
			self._extract_zip(archive, destination, progress)
			if progress is not None:
				progress("Registering project", 0, 0)
			children = list(destination.iterdir())
			project_root = children[0] if len(children) == 1 and children[0].is_dir() else destination
			workspace = ProjectWorkspace(
				project_id=project_id,
				root=project_root,
				name=Path(filename).stem or "uploaded-project",
				storage_dir=destination,
			)
			with self._lock:
				self._projects[project_id] = workspace
			return workspace
		except Exception:
			shutil.rmtree(destination, ignore_errors=True)
			raise

	def _extract_zip(
		self, archive: bytes, destination: Path,
		progress: Callable[[str, int, int], None] | None = None,
	) -> None:
		if progress is not None:
			progress("Validating ZIP", 0, 0)
		try:
			zip_file = zipfile.ZipFile(io.BytesIO(archive))
		except zipfile.BadZipFile as error:
			raise ValueError("Upload must be a valid ZIP archive.") from error
		with zip_file:
			entries = zip_file.infolist()
			file_entries = [entry for entry in entries if not entry.is_dir()]
			if len(file_entries) > MAX_FILES:
				raise ValueError(f"ZIP contains more than {MAX_FILES} files.")
			declared_total = sum(entry.file_size for entry in file_entries)
			if declared_total > MAX_UNCOMPRESSED_BYTES:
				raise ValueError("ZIP uncompressed size exceeds the configured limit.")
			logger.info("Extracting ZIP: %d files, %d uncompressed bytes", len(file_entries), declared_total)
			if progress is not None:
				progress("Extracting files", 0, len(file_entries))
			root = destination.resolve()
			actual_total = 0
			files_extracted = 0
			for entry in entries:
				name = entry.filename.replace("\\", "/")
				if not name or "\x00" in name:
					raise ValueError("ZIP contains an invalid entry path.")
				windows_path = PureWindowsPath(name)
				relative_path = PurePosixPath(name)
				if windows_path.drive or relative_path.is_absolute() or any(part == ".." for part in name.split("/")):
					raise ValueError("ZIP contains a path traversal entry.")
				if any(":" in part for part in relative_path.parts):
					raise ValueError("ZIP contains a Windows alternate-stream path.")
				mode = entry.external_attr >> 16
				if stat.S_ISLNK(mode):
					raise ValueError("ZIP symlinks are not allowed.")
				if entry.is_dir():
					continue
				if entry.file_size > MAX_FILE_BYTES:
					raise ValueError("ZIP contains an oversized file.")
				if entry.file_size / max(entry.compress_size, 1) > MAX_COMPRESSION_RATIO:
					raise ValueError("ZIP contains a suspiciously compressed file.")

				target = (root / Path(*relative_path.parts)).resolve()
				if target != root and root not in target.parents:
					raise ValueError("ZIP entry escapes its extraction directory.")
				target.parent.mkdir(parents=True, exist_ok=True)
				copied = 0
				with zip_file.open(entry, "r") as source, target.open("xb") as output:
					while chunk := source.read(1024 * 1024):
						copied += len(chunk)
						actual_total += len(chunk)
						if copied > MAX_FILE_BYTES or actual_total > MAX_UNCOMPRESSED_BYTES:
							raise ValueError("ZIP expanded beyond the configured size limit.")
						output.write(chunk)
				if copied != entry.file_size:
					raise ValueError("ZIP entry size did not match its directory metadata.")
				files_extracted += 1
				if progress is not None and (files_extracted % 25 == 0 or files_extracted == len(file_entries)):
					progress("Extracting files", files_extracted, len(file_entries))
				if files_extracted % 100 == 0 or files_extracted == len(file_entries):
					logger.info("Extracted %d/%d files (%d bytes)", files_extracted, len(file_entries), actual_total)

	def get(self, project_id: str) -> ProjectWorkspace | None:
		with self._lock:
			return self._projects.get(project_id)