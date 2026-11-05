import json
import sys

import nbformat
from nbclient import NotebookClient


def serialize(output):
    if output.output_type == "stream":
        return {"text": output.text}
    if output.output_type == "error":
        return {"text": "\n".join(output.traceback)}
    data = output.get("data", {})
    return {"text": data.get("text/plain", ""), "html": data.get("text/html", "")}


def respond(value):
    print(json.dumps(value), flush=True)


def main():
    notebook = nbformat.v4.new_notebook()
    client = NotebookClient(notebook, timeout=20, kernel_name="python3", allow_errors=True)
    with client.setup_kernel():
        respond({"ready": True})
        for line in sys.stdin:
            try:
                message = json.loads(line)
                source = message.get("source")
                if not isinstance(source, str) or len(source) > 100_000:
                    raise ValueError("Invalid cell source.")
                cell = nbformat.v4.new_code_cell(source)
                notebook.cells.append(cell)
                client.execute_cell(cell, len(notebook.cells) - 1)
                respond({"outputs": [serialize(output) for output in cell.outputs],
                         "executionCount": cell.execution_count})
            except Exception as error:
                respond({"error": str(error)})


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        respond({"error": str(error)})
        sys.exit(1)