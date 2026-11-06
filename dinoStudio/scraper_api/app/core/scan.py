import fnmatch
import os
from pathlib import Path

from .config import ScraperConfig


def should_ignore(path: Path, root: Path, config: ScraperConfig) -> bool:
    """Return whether a file is excluded by the notebook's scan rules."""
    try:
        relative = path.relative_to(root)
        size = path.stat().st_size
    except (OSError, ValueError):
        return True

    if any(part.lower() in config.ignore_dirs for part in relative.parts[:-1]):
        return True
    if path.suffix.lower() not in config.allowed_extensions:
        return True
    if any(fnmatch.fnmatch(path.name.lower(), pattern.lower()) for pattern in config.ignore_file_patterns):
        return True
    return size > config.max_file_bytes


def classify(relative_path: str) -> str:
    """Classify a repository-relative POSIX path using notebook precedence."""
    normalized = relative_path.replace("\\", "/")
    lowered = normalized.lower()
    if lowered.endswith(".spec.ts"):
        return "ts_spec"
    if lowered.endswith(".cs") and "/controllers/" in lowered:
        return "cs_controller"
    if lowered.endswith(".cs"):
        return "cs_other"
    if lowered.endswith(".service.ts"):
        return "ts_service"
    if lowered.endswith(".model.ts"):
        return "ts_model"
    if lowered.endswith(".component.ts"):
        return "ts_component"
    return "other"


def collect_files(root: Path, config: ScraperConfig) -> list[Path]:
    """Return sorted allowed files beneath root, matching the notebook scan."""
    resolved_root = root.resolve()
    ignored_directories = {name.lower() for name in config.ignore_dirs}
    files: list[Path] = []
    for current, directories, filenames in os.walk(resolved_root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name for name in directories
            if name.lower() not in ignored_directories
        )
        for filename in filenames:
            path = current_path / filename
            if not path.is_symlink() and not should_ignore(path, resolved_root, config):
                files.append(path)
    return sorted(files)