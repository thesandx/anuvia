import pytest

from app.utils.helpers import format_ist


def test_format_ist_converts_utc_to_ist():
    # 12:15 UTC is 17:45 IST (UTC+5:30).
    assert format_ist("2026-07-26T12:15:00Z") == "2026-07-26 17:45:00 IST"


def test_format_ist_handles_offset_form():
    assert format_ist("2026-07-26T12:15:00+00:00") == "2026-07-26 17:45:00 IST"


def test_format_ist_returns_none_for_empty():
    assert format_ist("") is None


def test_format_ist_returns_none_for_garbage():
    assert format_ist("not-a-timestamp") is None


@pytest.mark.asyncio
async def test_health_includes_deployed_at_field(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # DEPLOYED_AT is unset in tests, so the field is present and null.
    assert "deployed_at" in body
    assert body["deployed_at"] is None
