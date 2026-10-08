"""Claude plan quota: /api/oauth/usage client, cache and backoff.

Credentials are read-only and never refreshed. The access token is never logged, never
stored in the cache and never placed in exception text.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Tuple

from .quota import is_number, make_window
from .state import read_json
from .timeutil import epoch_to_iso, parse_iso

API_URL = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA = "oauth-2025-04-20"
USER_AGENT = "ai-usage-widget/0.2.0"
TIMEOUT_S = 10.0
MIN_INTERVAL_S = 300.0
BACKOFF_S = (300.0, 600.0, 1200.0, 1800.0)
WINDOW_IDS = (
    ("five_hour", "Session 5 h", 300),
    ("seven_day", "Semaine", 10080),
    ("seven_day_opus", "Semaine Opus", 10080),
    ("seven_day_sonnet", "Semaine Sonnet", 10080),
)
CACHE_VERSION = 1


class NetworkError(Exception):
    """Raised by an HttpGet implementation on transport failure (message never logged)."""


class HttpResponse(NamedTuple):
    status: int
    headers: Dict[str, str]
    body: bytes


HttpGet = Callable[[str, Dict[str, str], float], HttpResponse]


@dataclass(frozen=True)
class Credentials:
    token: str
    expires_at_ms: Optional[float]
    subscription_type: Optional[str]
    rate_limit_tier: Optional[str]


def default_http_get(url: str, headers: Dict[str, str], timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # TLS verified
            body = response.read(1 << 20)
            return HttpResponse(response.status, _lower_headers(response.headers), body)
    except urllib.error.HTTPError as exc:  # must precede URLError (subclass)
        try:
            body = exc.read(1 << 16)
        except Exception:  # noqa: BLE001 - body is optional for error codes
            body = b""
        return HttpResponse(exc.code, _lower_headers(exc.headers), body)
    except (urllib.error.URLError, OSError) as exc:
        raise NetworkError("transport") from exc


def _lower_headers(headers: Any) -> Dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def read_credentials(path: Optional[Path]) -> Optional[Credentials]:
    """Read ``claudeAiOauth`` from the credentials file.

    None when the file is absent, unreadable, malformed or has no ``claudeAiOauth`` object.
    A present object with an empty/missing token is returned with ``token == ""`` so that the
    caller can tell "logged out" apart from "no credentials" (see :func:`is_logged_out`).
    """
    if path is None:
        return None
    raw = read_json(path)
    if not isinstance(raw, dict):
        return None
    oauth = raw.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    expires = oauth.get("expiresAt")
    sub = oauth.get("subscriptionType")
    tier = oauth.get("rateLimitTier")
    return Credentials(
        token=token if isinstance(token, str) else "",
        expires_at_ms=float(expires) if is_number(expires) else None,
        subscription_type=sub if isinstance(sub, str) else None,
        rate_limit_tier=tier if isinstance(tier, str) else None,
    )


def is_logged_out(creds: Credentials) -> bool:
    """Claude Code is logged out: empty/missing access token, or ``expiresAt`` == 0."""
    return creds.token == "" or creds.expires_at_ms == 0


def plan_label(subscription: Optional[str], tier: Optional[str]) -> Optional[str]:
    if not subscription:
        return None
    tier_l = (tier or "").lower()
    sub_l = subscription.lower()
    if "max_20x" in tier_l:
        return "Max 20x"
    if "max_5x" in tier_l:
        return "Max 5x"
    if sub_l == "pro":
        return "Pro"
    if sub_l == "max":
        return "Max"
    return subscription.title()


def token_expired(expires_at_ms: Optional[float], now: float) -> bool:
    return expires_at_ms is not None and expires_at_ms / 1000.0 <= now


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def fetch_usage(token: str, http_get: HttpGet) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[float]]:
    """Return (data, error_code, retry_after_s). Exactly one HTTP call."""
    headers = {
        "Authorization": "Bearer " + token,
        "anthropic-beta": ANTHROPIC_BETA,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    try:
        response = http_get(API_URL, headers, TIMEOUT_S)
    except NetworkError:
        return None, "network", None
    status = response.status
    if status == 200:
        try:
            data = json.loads(response.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, "bad_response", None
        if not isinstance(data, dict):
            return None, "bad_response", None
        return data, None, None
    if status == 429:
        return None, "http_429", parse_retry_after(response.headers.get("retry-after"))
    if status == 401:
        return None, "http_401", None
    if 500 <= status <= 599:
        return None, "http_5xx", None
    return None, f"http_{status}", None


def _parse_windows(data: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for wid, _label, _minutes in WINDOW_IDS:
        node = data.get(wid)
        if not isinstance(node, dict) or not is_number(node.get("utilization")):
            continue
        resets = parse_iso(node.get("resets_at"))
        out[wid] = {"used": float(node["utilization"]), "resets_at": resets}
    return out


def _parse_extra(data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    extra = data.get("extra_usage")
    if not isinstance(extra, dict):
        return None
    kept = {k: extra[k] for k in ("is_enabled", "monthly_limit", "used_credits", "utilization") if k in extra}
    return kept or None


def empty_cache() -> Dict[str, Any]:
    return {
        "version": CACHE_VERSION,
        "fetched_at": None,
        "windows": {},
        "extra_usage": None,
        "failures": 0,
        "next_attempt_at": None,
        "last_attempt_at": None,
        "error": None,
        "plan_name": None,
        "plan_label": None,
    }


def load_cache(path: Path) -> Dict[str, Any]:
    raw = read_json(path)
    if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
        return empty_cache()
    base = empty_cache()
    base.update({k: raw[k] for k in base if k in raw})
    if not isinstance(base["windows"], dict):
        base["windows"] = {}
    return base


def update_cache(
    cache: Dict[str, Any],
    creds: Optional[Credentials],
    now: float,
    http_get: HttpGet,
) -> Dict[str, Any]:
    """Return the cache after at most one API call, honouring the min interval and backoff."""
    new = dict(cache)
    if creds is None:
        new["error"] = "no_credentials"
        return new
    new["plan_name"] = creds.subscription_type
    new["plan_label"] = plan_label(creds.subscription_type, creds.rate_limit_tier)
    if is_logged_out(creds):  # checked before expiry: expiresAt == 0 would read as "expired"
        new["error"] = "logged_out"
        return new
    if token_expired(creds.expires_at_ms, now):
        new["error"] = "token_expired"
        return new
    due = cache.get("next_attempt_at")
    if is_number(due) and now < float(due):
        return new  # min interval / backoff not elapsed: keep last answer

    data, error, retry_after = fetch_usage(creds.token, http_get)
    new["last_attempt_at"] = now
    if error is None and data is not None:
        new.update(
            {
                "fetched_at": now,
                "windows": _parse_windows(data),
                "extra_usage": _parse_extra(data),
                "failures": 0,
                "next_attempt_at": now + MIN_INTERVAL_S,
                "error": None,
            }
        )
        return new

    failures = int(cache.get("failures") or 0) + 1
    if error == "http_429" and retry_after is not None:
        wait = retry_after
    else:
        wait = BACKOFF_S[min(failures - 1, len(BACKOFF_S) - 1)]
    new.update(
        {
            "failures": failures,
            "next_attempt_at": now + max(wait, MIN_INTERVAL_S),
            "error": error,
        }
    )
    return new


def plan_and_windows(cache: Mapping[str, Any], now: float) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Render the Claude plan object and window list from the cache (no I/O)."""
    fetched = cache.get("fetched_at")
    raw_windows = cache.get("windows") or {}
    windows: List[Dict[str, Any]] = []
    for wid, label, minutes in WINDOW_IDS:
        node = raw_windows.get(wid)
        if not isinstance(node, dict) or not is_number(node.get("used")):
            continue
        resets = node.get("resets_at")
        windows.append(
            make_window(wid, label, float(node["used"]), minutes, float(resets) if is_number(resets) else None, now)
        )
    error = cache.get("error")
    has_data = bool(windows) or fetched is not None
    stale = bool(has_data and (error is not None or (is_number(fetched) and now - float(fetched) > 900)))
    plan = {
        "name": cache.get("plan_name"),
        "label": cache.get("plan_label"),
        "source": "oauth_api" if has_data or error not in (None, "no_credentials", "logged_out") else "none",
        "observed_at": epoch_to_iso(float(fetched)) if is_number(fetched) and windows else None,
        "stale": stale,
        "error": error,
        "credits": cache.get("extra_usage"),
    }
    return plan, windows


def cache_for_disk(cache: Mapping[str, Any]) -> Dict[str, Any]:
    """Explicit allow-list of persisted fields: no token, no raw response body."""
    keys = (
        "version", "fetched_at", "windows", "extra_usage", "failures", "next_attempt_at",
        "last_attempt_at", "error", "plan_name", "plan_label",
    )
    return {k: cache.get(k) for k in keys}
