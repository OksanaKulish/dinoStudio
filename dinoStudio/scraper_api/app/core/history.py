import subprocess
from pathlib import Path
from typing import Protocol

from .models import GitCommit


class HistoryProvider(Protocol):
    def repository_top(self, root: Path) -> Path | None:
        """Return the repository root, or None when the path is not a Git repo."""

    def commit_history(self, root: Path) -> dict[str, list[GitCommit]]:
        """Return newest-first commits keyed by repository-relative POSIX path."""

    def head_sha(self, root: Path) -> str | None:
        """Return the current commit SHA, or None when unavailable."""


def parse_git_log(raw: str) -> dict[str, list[GitCommit]]:
    """Parse `git log --name-only` output with unit-separated commit fields."""
    history: dict[str, list[GitCommit]] = {}
    current: GitCommit | None = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            fields = line[2:].split("\x1f", 3)
            if len(fields) != 4:
                current = None
                continue
            commit_hash, author, iso_date, subject = fields
            current = GitCommit(commit_hash[:7], author, iso_date, subject)
        elif line.strip() and current is not None:
            history.setdefault(line.strip().replace("\\", "/"), []).append(current)
    return history


class GitHistoryProvider:
    def repository_top(self, root: Path) -> Path | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return Path(result.stdout.strip()).resolve()

    def commit_history(self, root: Path) -> dict[str, list[GitCommit]]:
        repo_top = self.repository_top(root)
        if repo_top is None:
            return {}
        try:
            result = subprocess.run(
                [
                    "git", "-C", str(repo_top), "-c", "core.quotepath=off", "log", "--name-only",
                    "--pretty=format:@@%H%x1f%an%x1f%aI%x1f%s",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        return parse_git_log(result.stdout)

    def head_sha(self, root: Path) -> str | None:
        repo_top = self.repository_top(root)
        if repo_top is None:
            return None
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_top), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None