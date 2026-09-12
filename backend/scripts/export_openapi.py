"""Export the FastAPI app's OpenAPI schema to a file.

Short-lived: imports the FastAPI app object and calls app.openapi()
directly - no server process is started, no network port is opened.
Used by the frontend's type-generation and contract-drift-check
scripts so neither needs a live `uvicorn` process running, locally or
in CI. Works without a real .env or Azure credentials: every settings
field they'd fill is optional (see config.py) - this only proves the
endpoint SHAPE, never real values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tenderpilot.main import app

DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "openapi.json"


def main(argv: list[str]) -> None:
    output_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT_PATH
    output_path.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main(sys.argv)
