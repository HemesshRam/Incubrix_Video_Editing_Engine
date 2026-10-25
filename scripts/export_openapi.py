"""Export the exact FastAPI OpenAPI schema to the assessment's OpenAPI file."""

import json
from pathlib import Path

from app.main import app


target = Path(__file__).resolve().parents[1] / "openapi.yaml"
target.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Wrote OpenAPI {app.openapi_version} schema with {len(app.openapi()['paths'])} paths to {target}")
