"""External client wrapper for PaperAgent's native Gradio event boundary."""

from __future__ import annotations

import contextlib
import json
import sys

from gradio_client import Client


def _serializable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: paperagent_client.py BASE_URL")
    request = json.load(sys.stdin)
    if not isinstance(request, dict):
        raise SystemExit("stdin must contain a JSON object")
    api_name = request.get("api_name")
    arguments = request.get("arguments")
    output_names = request.get("output_names")
    if not isinstance(api_name, str) or not isinstance(arguments, list):
        raise SystemExit("api_name must be a string and arguments must be a list")
    if not isinstance(output_names, list) or not all(isinstance(item, str) for item in output_names):
        raise SystemExit("output_names must be a list of strings")
    with contextlib.redirect_stdout(sys.stderr):
        client = Client(sys.argv[1], verbose=False)
        result = client.predict(*arguments, api_name=api_name)
    values = list(result) if isinstance(result, tuple) else [result]
    payload = {
        name: _serializable(values[index]) if index < len(values) else None
        for index, name in enumerate(output_names)
    }
    print(json.dumps({"outputs": payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
