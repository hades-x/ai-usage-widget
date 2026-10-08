"""Shared quota helpers: window labels, plan labels, window construction."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .timeutil import epoch_to_iso

SESSION_MINUTES = 300
WEEK_MINUTES = 10080
STALE_AFTER_S = 15 * 60


def window_label(window_minutes: Optional[int]) -> str:
    if window_minutes == SESSION_MINUTES:
        return "Session 5 h"
    if window_minutes == WEEK_MINUTES:
        return "Semaine"
    if not window_minutes:
        return "Fenêtre"
    return f"Fenêtre {window_minutes / 60:g} h"


def codex_plan_label(plan_type: Optional[str]) -> Optional[str]:
    if not plan_type:
        return None
    known = {"prolite": "Pro Lite", "plus": "Plus", "pro": "Pro", "team": "Team"}
    return known.get(plan_type.lower(), plan_type.title())


def make_window(
    wid: str,
    label: str,
    used_percent: float,
    window_minutes: Optional[int],
    resets_at: Optional[float],
    now: float,
) -> Dict[str, Any]:
    """Window entry per STATE_SCHEMA.md. A reset already in the past zeroes the usage."""
    reset_since = False
    used = float(used_percent)
    if resets_at is not None and resets_at < now:
        used = 0.0
        reset_since = True
    elapsed: Optional[float] = None
    if resets_at is not None and window_minutes:
        span = window_minutes * 60.0
        elapsed = 100.0 * (1.0 - (resets_at - now) / span)
        elapsed = round(min(100.0, max(0.0, elapsed)), 1)
    return {
        "id": wid,
        "label": label,
        "used_percent": used,
        "window_minutes": window_minutes,
        "resets_at": epoch_to_iso(resets_at),
        "elapsed_percent": elapsed,
        "reset_since_observation": reset_since,
    }


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
