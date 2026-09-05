import base64
import json
import uuid
from datetime import datetime
from typing import Any


def encode_cursor(sort_value: Any, row_id: uuid.UUID) -> str:
    """Encodes an opaque pagination cursor from a (sort column value, row id) pair.

    The caller passes whatever value the active sort column holds for the last row of the
    current page; on the next request, decode_cursor() gives that value back (as a string) so
    the caller can build a keyset WHERE clause instead of an OFFSET.
    """
    v = sort_value.isoformat() if isinstance(sort_value, datetime) else str(sort_value)
    payload = json.dumps({"v": v, "id": str(row_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[str, uuid.UUID] | None:
    """Returns (sort_value_as_string, row_id), or None if the cursor is malformed in any way."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if not isinstance(payload["v"], str):
            return None
        return payload["v"], uuid.UUID(payload["id"])
    except Exception:
        return None
