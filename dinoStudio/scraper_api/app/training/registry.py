import json
import threading
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .train import RUNS_ROOT


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    run_id: str
    base_model: str
    quantization: str
    gguf: str
    active: bool


class ModelRegistry:
    def __init__(self) -> None:
        if RUNS_ROOT.is_symlink():
            raise RuntimeError("The runs directory cannot be a symbolic link.")
        RUNS_ROOT.mkdir(exist_ok=True)
        self.root = RUNS_ROOT.resolve()
        self._lock = threading.RLock()
        self._pointer = self.root / "active.json"

    def _folder(self, run_id: str) -> Path:
        name = str(uuid.UUID(run_id))
        folder = self.root / name
        if folder.is_symlink() or folder.resolve().parent != self.root or not folder.is_dir():
            raise ValueError("Training run directory is unavailable or invalid.")
        return folder

    def _card(self, run_id: str) -> tuple[dict[str, Any], Path]:
        folder = self._folder(run_id)
        card_path = folder / "model_card.json"
        if card_path.is_symlink() or not card_path.is_file():
            raise ValueError("A completed model card is required.")
        card = json.loads(card_path.read_text(encoding="utf-8"))
        if not isinstance(card, dict) or card.get("run_id") != run_id or not isinstance(card.get("gguf"), str):
            raise ValueError("This run has no registered GGUF model (dry runs cannot be activated).")
        gguf = (folder / card["gguf"]).resolve()
        if not gguf.is_relative_to(folder) or gguf.suffix.lower() != ".gguf" or not gguf.is_file():
            raise ValueError("Model card points to an invalid GGUF file.")
        return card, gguf

    def active_id(self) -> str | None:
        with self._lock:
            if self._pointer.is_symlink():
                raise ValueError("Active model pointer cannot be a symbolic link.")
            if not self._pointer.is_file():
                return None
            data = json.loads(self._pointer.read_text(encoding="utf-8"))
            identifier = data.get("run_id")
            if not isinstance(identifier, str) or str(uuid.UUID(identifier)) != identifier:
                raise ValueError("Active model pointer is invalid.")
            return identifier

    def get(self, run_id: str) -> RegisteredModel:
        with self._lock:
            card, _ = self._card(run_id)
            return RegisteredModel(run_id, card["base_model"], card["quantization"], card["gguf"],
                                   self.active_id() == run_id)

    def list(self) -> list[RegisteredModel]:
        models: list[RegisteredModel] = []
        for folder in self.root.iterdir():
            try:
                models.append(self.get(folder.name))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return models

    def activate(self, run_id: str) -> RegisteredModel:
        with self._lock:
            self._card(run_id)
            status = json.loads((self._folder(run_id) / "status.json").read_text(encoding="utf-8"))
            if status.get("status") != "completed" or status.get("eval_status") != "completed":
                raise ValueError("Complete training and held-out evaluation before activating this model.")
            pending = self.root / "active.json.tmp"
            if self._pointer.is_symlink() or pending.is_symlink():
                raise ValueError("Active model pointer cannot be a symbolic link.")
            pending.write_text(json.dumps({"run_id": run_id}) + "\n", encoding="utf-8")
            pending.replace(self._pointer)
            return self.get(run_id)

    def active(self) -> tuple[RegisteredModel, Path, str]:
        run_id = self.active_id()
        if run_id is None:
            raise ValueError("No trained model is active. Activate a completed and evaluated run first.")
        card, gguf = self._card(run_id)
        return self.get(run_id), gguf, card["system_message"]

    def clear_if_active(self, run_id: str) -> None:
        with self._lock:
            if self.active_id() == run_id:
                self._pointer.unlink()

    def bundle(self, run_id: str) -> Path:
        with self._lock:
            _, gguf = self._card(run_id)
            folder = self._folder(run_id)
            status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
            if status.get("status") != "completed":
                raise ValueError("Download is available only after GGUF export completes.")
            archive = folder / "bundle.zip"
            pending = folder / "bundle.zip.tmp"
            if archive.is_symlink() or pending.is_symlink():
                raise ValueError("Run download path cannot be a symbolic link.")
            if archive.is_file():
                return archive
            files = [gguf, folder / "model_card.json", folder / "metrics.json"]
            adapter = folder / "adapter"
            if not adapter.is_dir() or adapter.is_symlink():
                raise ValueError("Trained adapter is missing or invalid.")
            files.extend(path for path in adapter.rglob("*") if path.is_file())
            try:
                with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_STORED) as output:
                    for path in files:
                        if not path.resolve().is_relative_to(folder) or not path.is_file():
                            raise ValueError("Model artifact escapes the run directory.")
                        output.write(path, path.relative_to(folder).as_posix())
                pending.replace(archive)
            except Exception:
                pending.unlink(missing_ok=True)
                raise
            return archive