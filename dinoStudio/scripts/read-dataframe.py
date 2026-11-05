import base64
import json
import sys


def json_value(value):
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def main():
    import pandas as pd

    request = json.load(sys.stdin)
    name = request.get("name", "")
    encoded = request.get("data", "")
    if not isinstance(name, str) or not isinstance(encoded, str):
        raise ValueError("Invalid file upload.")
    content = base64.b64decode(encoded, validate=True)
    if not content or len(content) > 5_000_000:
        raise ValueError("Choose a non-empty file under 5 MB.")

    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix == "csv":
        frame = pd.read_csv(__import__("io").BytesIO(content))
    elif suffix == "xlsx":
        frame = pd.read_excel(__import__("io").BytesIO(content), engine="openpyxl")
    elif suffix == "json":
        frame = pd.read_json(__import__("io").BytesIO(content))
    else:
        raise ValueError("Supported formats are CSV, XLSX, and JSON.")

    if len(frame.columns) > 50:
        raise ValueError("Files may contain at most 50 columns.")
    result = {
        "name": name,
        "rows": [[None if pd.isna(value) else json_value(value) for value in row]
             for row in frame.itertuples(index=False, name=None)],
        "columns": [str(column) for column in frame.columns],
        "rowCount": len(frame),
    }
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)