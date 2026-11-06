import atexit
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .registry import ModelRegistry
from .runner import RunManager
from .train import _run_dir, _run_path


def serve(run_dir: Path) -> None:
    run_dir = _run_dir(run_dir)
    card = json.loads(_run_path(run_dir, "model_card.json").read_text(encoding="utf-8"))
    gguf = (run_dir / card["gguf"]).resolve()
    if not gguf.is_relative_to(run_dir) or gguf.suffix != ".gguf" or not gguf.is_file():
        raise ValueError("The active model card does not point to a run-local GGUF file.")
    model: Any = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            prompt = request["prompt"]
            max_tokens = request["max_tokens"]
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
                raise ValueError("Prompt must contain 1 to 100000 characters.")
            if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= 4096:
                raise ValueError("max_tokens must be between 1 and 4096.")
            if model is None:
                from llama_cpp import Llama

                model = Llama(model_path=str(gguf), n_ctx=8192, n_gpu_layers=-1, verbose=False)
            result = model.create_chat_completion(
                messages=[{"role": "system", "content": card["system_message"]},
                          {"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=max_tokens,
            )
            response = {"run_id": card["run_id"], "content": result["choices"][0]["message"]["content"],
                        "usage": result.get("usage")}
        except Exception as error:
            response = {"error": str(error)}
        print(json.dumps(response, ensure_ascii=False), flush=True)


class GenerationClient:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] | None = None
        self._run_id: str | None = None
        atexit.register(self.reset)

    def reset(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._responses = None
            self._run_id = None
            if process is not None:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                RunManager._terminate_tree(process)
                if process.stdout is not None:
                    process.stdout.close()

    @staticmethod
    def _read_responses(process: subprocess.Popen[str], responses: queue.Queue[str | None]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                responses.put(line)
        finally:
            responses.put(None)

    def generate(self, prompt: str, max_tokens: int) -> dict[str, Any]:
        with self._lock:
            registered, gguf, _ = self.registry.active()
            python = os.environ.get("TRAINING_PYTHON", "")
            if not python or not Path(python).is_absolute() or not Path(python).is_file():
                raise RuntimeError("TRAINING_PYTHON must point to a separate environment with llama-cpp-python.")
            if Path(python).resolve() == Path(sys.executable).resolve():
                raise RuntimeError("Model generation must use a separate Python environment from FastAPI.")
            if self._run_id != registered.run_id or self._process is None or self._process.poll() is not None:
                self.reset()
                run_dir = gguf.relative_to(self.registry.root).parts[0]
                folder = self.registry.root / run_dir
                responses: queue.Queue[str | None] = queue.Queue()
                with (folder / "serve.log").open("a", encoding="utf-8") as errors:
                    process = subprocess.Popen(
                        [python, "-m", "scraper_api.app.training.serve", "--run-dir", str(folder)],
                        cwd=self.registry.root.parent, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"},
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
                        start_new_session=os.name != "nt",
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                    )
                threading.Thread(target=self._read_responses, args=(process, responses), daemon=True).start()
                self._process, self._responses, self._run_id = process, responses, registered.run_id
            assert self._process.stdin is not None and self._responses is not None
            try:
                self._process.stdin.write(json.dumps({"prompt": prompt, "max_tokens": max_tokens}) + "\n")
                self._process.stdin.flush()
                response = self._responses.get(timeout=max(5, int(os.environ.get("GENERATION_TIMEOUT_SECONDS", "180"))))
                if response is None:
                    raise RuntimeError("Generation worker exited; check serve.log in the active run.")
                payload = json.loads(response)
                if "error" in payload:
                    raise RuntimeError(f"Generation failed: {payload['error']}")
                return payload
            except (OSError, queue.Empty, ValueError, RuntimeError) as error:
                self.reset()
                if isinstance(error, queue.Empty):
                    raise RuntimeError("Generation timed out; the worker was stopped.") from error
                raise


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Serve an active GGUF through a local JSON-line worker.")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    serve(args.run_dir)


if __name__ == "__main__":
    main()