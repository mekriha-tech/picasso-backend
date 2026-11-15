"""Writes docs/openapi.json from the FastAPI app.

Run after any endpoint or schema change and commit the result:

    python scripts/export_openapi.py

tests/test_openapi_contract.py fails if the committed file is out of date.
"""
import json
import os
import sys
from pathlib import Path

# The app reads settings at import time; building the spec never touches a database,
# so placeholders are enough. PROJECT_NAME and API_V1_PREFIX are forced so a developer's
# local .env can't change the output and make the drift test flaky.
os.environ.setdefault("SECRET_KEY", "openapi-export")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ["PROJECT_NAME"] = "Picasso API"
os.environ["API_V1_PREFIX"] = "/api/v1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

OUT = ROOT / "docs" / "openapi.json"


def build() -> dict:
    return json.loads(json.dumps(app.openapi()))


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
