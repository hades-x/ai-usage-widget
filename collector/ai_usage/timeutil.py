"""Timestamp helpers. Everything internal is epoch seconds (int or float, UTC)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional


def utc_now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def parse_iso(value: object) -> Optional[float]:
    """Parse an ISO 8601 timestamp (``Z`` or offset). Naive values are taken as UTC."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def epoch_to_iso(epoch: Optional[float]) -> Optional[str]:
    """Epoch seconds -> ``YYYY-MM-DDTHH:MM:SSZ`` (UTC, whole seconds)."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_date(epoch: float) -> date:
    """Local calendar date (machine timezone, honours TZ / time.tzset)."""
    return datetime.fromtimestamp(epoch).date()


def local_day(epoch: float) -> str:
    return local_date(epoch).isoformat()
