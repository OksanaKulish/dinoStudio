# DinoStudio

This project was generated using [Angular CLI](https://github.com/angular/angular-cli) version 22.1.6.

## Development server

Start the Angular app and local API together:

```bash
npm start
```

Open the address printed by `npm start` (usually `http://localhost:4300/`). If a port is busy, the app and API use the next available ports. The API listens only on `127.0.0.1` and is also needed by the notebook node. WSL is required only for the terminal node: on Windows, install WSL and a GNU/Linux distribution first (`wsl --install`). In the terminal node, enter the Windows path to the folder you want to use (for example `C:\Projects\my-folder`). A browser folder picker cannot supply this path. Commands run in your default WSL distribution with your Linux user permissions; use the terminal only with folders and commands you trust. Commands are non-interactive, with a 30-second limit and bounded output.

To run the terminal service separately, use `npm run terminal:server`.

## Notebooks

Install Python 3 and the notebook dependencies in a local virtual environment:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements-notebook.txt
```

Restart the terminal service after installing dependencies. The notebook node runs its built-in pandas example or a selected `.ipynb` using the local Python kernel. It executes arbitrary Python code with your user permissions: only open and run notebooks you trust. Outputs include text and pandas tables; the runner limits notebook size, execution time, and response size.

## Ollama

Install and start [Ollama](https://ollama.com/), then download a model (for example `ollama pull llama3.2`). Use **Add Ollama** in the component palette to choose an installed model and exchange prompts and responses. The local API forwards requests only to `127.0.0.1:11434`; Ollama does not need to be running for other components to work.

Use **Add code generator** to enter a Hugging Face model ID, select a language, and request code. The Transformers model downloads to the local Hugging Face cache on first use and runs on CUDA when available or CPU otherwise. The default is `Qwen/Qwen2.5-Coder-0.5B-Instruct`. Install dependencies with `.venv\Scripts\python -m pip install -r requirements-code.txt`. Custom model code is not trusted or executed; use public Transformers-compatible causal language models.

Use **Add data viewer** to upload `.csv`, `.xlsx`, or `.json` files and inspect all rows as a table parsed locally with pandas. Uploads are limited to 5 MB and 50 columns. Install `openpyxl` in the notebook virtual environment for XLSX support using `.venv\Scripts\python -m pip install -r requirements-notebook.txt`.

## Repository scraper

Install the scraper API dependencies with Python 3.11 or newer:

```powershell
py -m pip install -r requirements-scraper.txt
```

Start `npm start` and note the Angular port it prints. In a second terminal, set that exact port and the roots allowed for local-folder registration, then run the scraper launcher. It starts at port 8000 and automatically picks the next free port if needed:

```powershell
$env:DINO_APP_PORT = "4300"
$env:SCRAPER_ALLOWED_ROOTS = "C:\Projects;D:\Work"
.\.venv\Scripts\python.exe -m scraper_api.run
```

Replace `DINO_APP_PORT` with the port printed by `npm start`, and set `SCRAPER_ALLOWED_ROOTS` to the folders you intend to analyze. The launcher prints the API URL; Angular discovers it automatically. The API binds to loopback only. Alternatively, upload a ZIP; ZIP extraction rejects traversal paths, symlinks, oversized files, and archives over configured limits. The scraper never executes repository code. Tokenizer loading is local-cache-only; cache the configured tokenizer before running a job.

## Training preparation (Stage 1)

Training uses a separate Python environment. Set `TRAINING_PYTHON` to the absolute path of that environment's interpreter before starting the scraper API; do not point it at the scraper's `.venv`. Preflight does not download a model or start training:

```powershell
$env:TRAINING_PYTHON = "C:\venvs\dino-training\Scripts\python.exe"
Invoke-RestMethod http://127.0.0.1:8001/api/training/preflight
```

Use the API port printed by the launcher in place of `8001`. The response lists missing packages, CUDA availability, free VRAM and reasons when training is blocked. To validate an exported scraper ZIP from the separate environment, run this from the project root:

```powershell
& $env:TRAINING_PYTHON -m scraper_api.app.training.dataset "C:\path\to\scraper-job.zip"
```

This reads the ZIP without extracting it, uses the configured model's real tokenizer (which may download only that model's tokenizer), rejects missing or failed secret-scan markers and train/test feature overlap, and prints retained counts and too-long rows. Successful new scraper exports include `secret_scan_passed: true`; older ZIPs must be regenerated.

## Training CLI (Stage 2)

The training CLI is separate from FastAPI. It requires a CUDA-enabled Python environment with Unsloth, TRL, Transformers, Datasets and the other packages reported by preflight. There is no verified, pinned `requirements-training.txt` yet: the notebook does not record package versions, and this machine has no configured training environment with Unsloth or TRL. Do not start a production run until their signatures and compatible pins are checked on the target GPU environment.

For a manual two-step dry run, place a newly exported scraper ZIP and a config file in a direct child of `runs/`:

```powershell
$run = "runs\manual-dry-run"
New-Item -ItemType Directory -Force $run
Copy-Item "C:\path\to\scraper-job.zip" "$run\dataset.zip"
'{"dry_run":true}' | Set-Content "$run\config.json"
& $env:TRAINING_PYTHON -m scraper_api.app.training.train --run-dir $run
```

The default 7B training model and the explicitly configured 0.5B dry-run model are the only permitted download sources. The CLI writes the resolved `config.json`, `progress.jsonl`, `metrics.json`, and the LoRA `adapter/` under the run directory. Real runs also export GGUF and a model card; dry runs write a model card but skip GGUF conversion.

## Training runs (Stage 3)

Restart the scraper API after setting `TRAINING_PYTHON` to an absolute executable in a separate CUDA training environment. `POST /api/training/runs` accepts JSON such as `{"job_id":"<completed scraper job>","config":{"dry_run":true}}`, or multipart form data with a `file` ZIP and a `config` JSON-string field. Both require a passing secret-scan manifest; the worker validates the complete dataset before loading a model. Preflight rejects missing packages, CUDA or VRAM before a worker starts. Only one run may be active; further requests receive HTTP 409. `TRAINING_TIMEOUT_SECONDS` sets the worker limit (default 86400 seconds).

`GET /api/training/runs` lists runs, `GET /api/training/runs/{id}` reports stage, step, loss and ETA, and `GET /api/training/runs/{id}/logs` streams `progress` and `log` SSE events. `POST /api/training/runs/{id}/cancel` terminates the worker process tree. `DELETE /api/training/runs/{id}` removes an inactive run folder. Progress, config, metrics, adapter and captured `worker.log` remain under `runs/<id>/`. For manual verification, start with `GET /api/training/preflight`, then create a run from a newly completed scraper job and inspect its detail and logs endpoints; cancellation and deletion are available after a run exists.

## Held-out evaluation (Stage 4)

For a completed run with an adapter, `POST /api/training/runs/{id}/evaluate` starts a separate evaluation worker; `GET /api/training/runs/{id}` shows its separate `eval_status`, `eval_stage`, step count and score. The existing `/logs` SSE endpoint also streams `evaluation` and `eval_log` events. After completion, `GET /api/training/runs/{id}/eval` returns `runs/<id>/eval_report.json` with per-task and per-example prompt, expected answer, base output and fine-tuned output. The same cancel endpoint can stop an active evaluation without removing the trained adapter. Run creation is blocked while evaluation uses the GPU.

Evaluation uses deterministic generation on the held-out test split, limited to 512 new tokens per example. Parse success means the scraper parser recognizes the expected kind of code, not that it compiles. Endpoint coverage compares recognized HTTP verb/route pairs; consistency counts errors and warnings from the existing checker only when the necessary paired code is present. `dto_to_ts` also reports whitespace-normalized exact match and text similarity. Inapplicable metrics are `null`; the overall quality score averages only applicable parse success, endpoint coverage, DTO similarity and `1 / (1 + average consistency errors)`. For manual verification, start evaluation on a completed run, follow its SSE stream, then compare `overall` and `by_task_type` in `/eval`. The Unsloth adapter and Transformers/PEFT inference combination remains unverified on this machine because no compatible training environment is configured.

## Model export and generation (Stage 5)

Non-dry training runs invoke the notebook's `save_pretrained_gguf` method in the training subprocess using the configured quantization. The run writes `gguf/` and `model_card.json` with the effective base model, exact config, dataset and manifest hashes, scraper job ID, Git SHA, system message, metrics, and installed library versions. `GET /api/training/runs/{id}/download` streams a ZIP containing the GGUF, adapter, model card, and metrics without holding the GGUF in API memory. Dry runs skip GGUF and cannot be activated.

`GET /api/training/models` lists exported models; `GET /api/training/models/active` returns the active one. After evaluation completes, `POST /api/training/runs/{id}/activate` switches the active model. `POST /api/generate` accepts `{"prompt":"...","max_tokens":1500}` and returns a chat response from a lazily loaded GGUF worker using the training environment's `llama-cpp-python` and the model card's system message. The worker stays loaded between requests and is stopped on model switch, timeout, or run deletion; `GENERATION_TIMEOUT_SECONDS` defaults to 180 seconds. Only the API calls the local worker; it does not import llama-cpp or Unsloth. For manual verification, use a real completed and evaluated run to download, activate, then call `/api/generate`; inspect its `runs/<id>/serve.log` if generation fails. GGUF conversion and its local llama.cpp tooling are not verified here. Unsloth may attempt to install or fetch conversion tools; confirm offline behavior and compatible pinned dependencies on the target GPU machine before running a real export under a restricted-network policy.

## Training UI (Stages 6 and 7)

Restart the scraper API after updating it, then open `/training` from the app navigation. Setup offers a completed scraper job or a ZIP upload, model and LoRA settings, a separate-environment preflight, and a tokenizer-backed dataset preview showing train/test and excluded counts per task. A passing preview and preflight are required before Start is enabled. Runs lists jobs and actions; Run detail polls progress and shows a loss chart and SSE worker log. Evaluation compares held-out base and fine-tuned scores by task and lets you inspect individual prompts and outputs. Try it sends prompts to the active evaluated GGUF through the local API. The page makes no third-party requests; training may download only the configured base model and requires locally available GGUF conversion tooling. Until a compatible `TRAINING_PYTHON` environment is configured, preflight blocks starting a run with specific reasons.

## Text to image

Install the local Python image dependencies into `.venv`:

```powershell
.venv\Scripts\python -m pip install -r requirements-image.txt
```

Add **Text to image** from the palette, enter a prompt, and select **Generate**. The first generation downloads the `segmind/tiny-sd` model from Hugging Face into the local cache; later runs use that cache. The runner automatically uses a CUDA GPU when available or CPU otherwise (256 x 256 output, with fewer inference steps on CPU). CPU generation may take several minutes. The model runs locally after download; no prompt is sent to a hosted image-generation API. If PyTorch is unavailable for your Python version, use a supported Python installation for the image environment and set `NOTEBOOK_PYTHON` before starting the local API.

## Code scaffolding

Angular CLI includes powerful code scaffolding tools. To generate a new component, run:

```bash
ng generate component component-name
```

For a complete list of available schematics (such as `components`, `directives`, or `pipes`), run:

```bash
ng generate --help
```

## Building

To build the project run:

```bash
ng build
```

This will compile your project and store the build artifacts in the `dist/` directory. By default, the production build optimizes your application for performance and speed.

## Running unit tests

To execute unit tests with the [Vitest](https://vitest.dev/) test runner, use the following command:

```bash
ng test
```

## Running end-to-end tests

For end-to-end (e2e) testing, run:

```bash
ng e2e
```

Angular CLI does not come with an end-to-end testing framework by default. You can choose one that suits your needs.

## Additional Resources

For more information on using the Angular CLI, including detailed command references, visit the [Angular CLI Overview and Command Reference](https://angular.dev/tools/cli) page.

<!-- $env:DINO_APP_PORT = '4310'  # use the port printed above
$env:SCRAPER_ALLOWED_ROOTS = 'C:\Projects'  # only needed for local-path registration
$env:TRAINING_PYTHON = 'C:\path\to\separate-training-env\Scripts\python.exe'
.\.venv\Scripts\python.exe -m scraper_api.run -->