"""Token counters and window aggregation.

A *day table* is ``{"YYYY-MM-DD": {model: counters}}`` where counters hold the raw
(already normalised, see ARCHITECTURE.md §3) integers plus ``requests``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

COUNTER_KEYS = ("input", "cached_input", "cache_write", "output", "reasoning", "requests")
DayTable = Dict[str, Dict[str, Dict[str, int]]]


def empty_counters() -> Dict[str, int]:
    return {key: 0 for key in COUNTER_KEYS}


def nonneg_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def add_into(dst: Dict[str, int], src: Mapping[str, Any]) -> None:
    for key in COUNTER_KEYS:
        dst[key] = dst.get(key, 0) + nonneg_int(src.get(key, 0))


def total_of(counters: Mapping[str, Any]) -> int:
    """total = input + cached_input + cache_write + output (reasoning is a subset of output)."""
    return (
        nonneg_int(counters.get("input"))
        + nonneg_int(counters.get("cached_input"))
        + nonneg_int(counters.get("cache_write"))
        + nonneg_int(counters.get("output"))
    )


def add_day(table: DayTable, day: str, model: str, counters: Mapping[str, Any]) -> None:
    models = table.setdefault(day, {})
    add_into(models.setdefault(model, empty_counters()), counters)


def merge_table(dst: DayTable, src: DayTable) -> None:
    for day, models in src.items():
        for model, counters in models.items():
            add_day(dst, day, model, counters)


def prune_table(table: DayTable, oldest_kept: str) -> None:
    """Drop days (ISO date strings) strictly older than ``oldest_kept``."""
    for day in [d for d in table if d < oldest_kept]:
        del table[day]


def _public_counters(raw: Mapping[str, int]) -> Dict[str, int]:
    return {
        "input": raw["input"],
        "cached_input": raw["cached_input"],
        "cache_write": raw["cache_write"],
        "output": raw["output"],
        "reasoning": raw["reasoning"],
        "total": total_of(raw),
        "requests": raw["requests"],
    }


def build_tokens(table: DayTable, today: date) -> Dict[str, Any]:
    """Windows ``today`` / ``last_7d`` (today + 6 previous) / ``last_30d`` / by_model_7d / daily_14d."""

    def in_range(day: str, first: date, last: date) -> bool:
        return first.isoformat() <= day <= last.isoformat()

    def window(first: date, last: date) -> Dict[str, int]:
        acc = empty_counters()
        for day, models in table.items():
            if in_range(day, first, last):
                for counters in models.values():
                    add_into(acc, counters)
        return acc

    day7 = today - timedelta(days=6)
    day30 = today - timedelta(days=29)

    model_totals: Dict[str, int] = {}
    for day, models in table.items():
        if in_range(day, day7, today):
            for model, counters in models.items():
                model_totals[model] = model_totals.get(model, 0) + total_of(counters)
    grand = sum(model_totals.values())
    by_model = [
        {"model": model, "total": total, "share": (total / grand) if grand else 0.0}
        for model, total in model_totals.items()
        if total > 0
    ]
    by_model.sort(key=lambda item: (-item["total"], item["model"]))

    daily: List[Dict[str, Any]] = []
    for offset in range(13, -1, -1):
        current = today - timedelta(days=offset)
        day_total = 0
        for counters in table.get(current.isoformat(), {}).values():
            day_total += total_of(counters)
        daily.append({"date": current.isoformat(), "total": day_total})

    return {
        "today": _public_counters(window(today, today)),
        "last_7d": _public_counters(window(day7, today)),
        "last_30d": _public_counters(window(day30, today)),
        "by_model_7d": by_model,
        "daily_14d": daily,
    }


def table_total_for_day(table: DayTable, day: str) -> Optional[int]:
    models = table.get(day)
    if models is None:
        return None
    return sum(total_of(c) for c in models.values())


def iter_days(table: DayTable) -> Iterable[str]:
    return iter(sorted(table))
