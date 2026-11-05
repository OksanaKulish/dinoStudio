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
