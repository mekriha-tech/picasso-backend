import uuid
from datetime import datetime, timezone

from app.services.pagination import encode_cursor, decode_cursor


def test_round_trip_datetime_value():
    row_id = uuid.uuid4()
    dt = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
    cursor = encode_cursor(dt, row_id)
    decoded_value, decoded_id = decode_cursor(cursor)
    assert decoded_value == dt.isoformat()
    assert decoded_id == row_id


def test_round_trip_numeric_value():
    row_id = uuid.uuid4()
    cursor = encode_cursor("199.50", row_id)
    decoded_value, decoded_id = decode_cursor(cursor)
    assert decoded_value == "199.50"
    assert decoded_id == row_id


def test_decode_rejects_garbage_base64():
    assert decode_cursor("not-valid-base64!!!") is None


def test_decode_rejects_valid_base64_bad_json():
    import base64
    garbage = base64.urlsafe_b64encode(b"not json").decode()
    assert decode_cursor(garbage) is None


def test_decode_rejects_missing_id_field():
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({"v": "x"}).encode()).decode()
    assert decode_cursor(payload) is None


def test_decode_rejects_invalid_uuid():
    import base64
    import json
    payload = base64.urlsafe_b64encode(
        json.dumps({"v": "x", "id": "not-a-uuid"}).encode()
    ).decode()
    assert decode_cursor(payload) is None


def test_decode_rejects_non_string_value():
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({"v": None, "id": str(uuid.uuid4())}).encode()).decode()
    assert decode_cursor(payload) is None
