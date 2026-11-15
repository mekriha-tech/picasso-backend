import json

from scripts.export_openapi import OUT, build


def test_committed_openapi_matches_app():
    committed = json.loads(OUT.read_text(encoding="utf-8"))
    assert committed == build(), (
        "docs/openapi.json is out of date with the API. "
        "Run `python scripts/export_openapi.py` and commit the result "
        "(see docs/COLLABORATION.md)."
    )
