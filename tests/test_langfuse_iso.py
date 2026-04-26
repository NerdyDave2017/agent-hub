"""Langfuse metrics helper (no HTTP)."""

from datetime import datetime, timezone

from worker.handlers.langfuse_public_metrics import iso_z


def test_iso_z_uses_z_suffix() -> None:
    dt = datetime(2026, 4, 1, 12, 30, 45, tzinfo=timezone.utc)
    assert iso_z(dt) == "2026-04-01T12:30:45Z"
