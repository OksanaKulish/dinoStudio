import json
import sys

try:
    import nbformat
    from nbclient import NotebookClient
except ImportError:
    print(json.dumps({"error": "Install notebook dependencies from requirements-notebook.txt in the configured Python environment."}))
    sys.exit(1)


def main():
    payload = json.load(sys.stdin)
    notebook = nbformat.from_dict(payload)
    nbformat.validate(notebook)
    if len(notebook.cells) > 40:
        raise ValueError("Notebook exceeds 40 cells.")
    client = NotebookClient(notebook, timeout=20, kernel_name="python3", allow_errors=True)
    client.execute()
    results = []
    for cell in notebook.cells:
        if cell.cell_type != "code":
            results.append([])
            continue
        outputs = []
        for output in cell.get("outputs", []):
            if output.output_type == "stream":
                outputs.append({"text": output.text})
            elif output.output_type == "error":
                outputs.append({"text": "\n".join(output.traceback)})
            else:
                data = output.get("data", {})
                outputs.append({
                    "text": data.get("text/plain", ""),
                    "html": data.get("text/html", ""),
                })
        results.append(outputs)
    print(json.dumps({"results": results}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)