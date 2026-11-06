import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

TRAINING_PACKAGES = (
    "torch", "unsloth", "trl", "datasets", "transformers", "peft", "accelerate", "bitsandbytes", "pydantic",
)


@dataclass(frozen=True, slots=True)
class PreflightReport:
    ready: bool
    base_model: str
    python: str
    cuda: bool
    vram_total_gb: float | None
    vram_free_gb: float | None
    minimum_vram_gb: int
    versions: dict[str, str | None]
    reasons: list[str]


def minimum_vram_gb(base_model: str) -> int:
    name = base_model.lower()
    if "0.5b" in name:
        return 4
    return 8 if "3b" in name else 12


def preflight(base_model: str) -> PreflightReport:
    versions: dict[str, str | None] = {}
    reasons: list[str] = []
    for package in TRAINING_PACKAGES + ("llama-cpp-python",):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
            if package in TRAINING_PACKAGES:
                reasons.append(f"Missing {package} in the training Python environment.")

    cuda = False
    total_gb: float | None = None
    free_gb: float | None = None
    if versions["torch"] is not None:
        try:
            import torch

            cuda = torch.cuda.is_available()
            if cuda:
                free_bytes, total_bytes = torch.cuda.mem_get_info()
                total_gb = round(total_bytes / 1024**3, 2)
                free_gb = round(free_bytes / 1024**3, 2)
        except Exception as error:
            reasons.append(f"CUDA check failed in the training environment: {error}")
    if not cuda:
        reasons.append("CUDA GPU unavailable in the training environment.")
    minimum = minimum_vram_gb(base_model)
    if free_gb is not None and free_gb < minimum:
        reasons.append(f"Insufficient free GPU memory: {free_gb} GB available; {minimum} GB required for {base_model}.")
    return PreflightReport(not reasons, base_model, sys.executable, cuda, total_gb, free_gb, minimum, versions, reasons)


def check_training_python(base_model: str) -> PreflightReport:
    configured_python = os.environ.get("TRAINING_PYTHON", "")

    def blocked(reason: str) -> PreflightReport:
        return PreflightReport(False, base_model, configured_python, False, None, None,
                               minimum_vram_gb(base_model), {}, [reason])

    if not configured_python:
        return blocked("TRAINING_PYTHON is not configured; point it to a separate training environment's Python executable.")
    python_path = Path(configured_python)
    if not python_path.is_absolute() or not python_path.is_file():
        return blocked("TRAINING_PYTHON must be the absolute path to an existing Python executable.")
    if python_path.resolve() == Path(sys.executable).resolve():
        return blocked("TRAINING_PYTHON must use a separate environment from the FastAPI process.")
    try:
        result = subprocess.run(
            [str(python_path), "-m", "scraper_api.app.training.preflight", "--base-model", base_model],
            cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return blocked(f"Training Python preflight could not finish: {error}")
    if result.returncode:
        return blocked(f"Training Python preflight failed: {result.stderr.strip()[-1000:] or 'unknown error'}")
    try:
        return PreflightReport(**json.loads(result.stdout))
    except (TypeError, ValueError):
        return blocked("Training Python returned an invalid preflight response.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the separate training Python and GPU without downloading models.")
    parser.add_argument("--base-model", required=True)
    args = parser.parse_args()
    print(json.dumps(asdict(preflight(args.base_model))))


if __name__ == "__main__":
    main()