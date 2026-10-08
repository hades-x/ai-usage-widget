"""Credentials states: no_credentials vs logged_out vs token_expired, and doctor output (synthetic data)."""

import _pathfix  # noqa: F401 - must precede ai_usage imports

import json
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

from ai_usage import claude_api
from ai_usage.cli import doctor
from ai_usage.collector import collect

from helpers import ParisTZ, TempPaths

NOW = 1791460800.0  # 2026-10-08T12:00:00Z (checked: datetime(2026,10,8,12,tzinfo=utc).timestamp())
SECRET = "FAKE-ACCESS-TOKEN-FOR-TESTS-0002"
FUTURE_MS = 4102444800000  # 2100-01-01


class FakeHttp:
    """Records every call; any call is a test failure for logged-out states."""

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, url: str, headers: Dict[str, str], timeout: float) -> claude_api.HttpResponse:
        self.calls.append(url)
        return claude_api.HttpResponse(200, {}, b"{}")


def write_creds(root: Path, oauth: Optional[Dict[str, Any]], raw: Optional[str] = None) -> Path:
    path = root / "claude" / ".credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    else:
        body: Dict[str, Any] = {} if oauth is None else {"claudeAiOauth": oauth}
        path.write_text(json.dumps(body), encoding="utf-8")
    return path


def logged_out_oauth(**overrides: Any) -> Dict[str, Any]:
    """Production structure: empty token, expiresAt 0, plan kept."""
    oauth: Dict[str, Any] = {
        "accessToken": "",
        "refreshToken": "",
        "expiresAt": 0,
        "scopes": ["user:inference"],
        "subscriptionType": "pro",
        "rateLimitTier": "default_claude_ai",
    }
    oauth.update(overrides)
    return oauth


class LoggedOutStateTests(ParisTZ):
    def cache_for(self, path: Optional[Path], http: FakeHttp) -> Dict[str, Any]:
        creds = claude_api.read_credentials(path)
        return claude_api.update_cache(claude_api.empty_cache(), creds, NOW, http)

    def test_empty_token_is_logged_out_without_http_call(self) -> None:
        tmp = TempPaths(self)
        path = write_creds(tmp.root, logged_out_oauth())
        http = FakeHttp()
        cache = self.cache_for(path, http)
        self.assertEqual(cache["error"], "logged_out")
        self.assertEqual(http.calls, [])

    def test_missing_token_key_is_logged_out(self) -> None:
        tmp = TempPaths(self)
        oauth = logged_out_oauth()
        del oauth["accessToken"]
        path = write_creds(tmp.root, oauth)
        http = FakeHttp()
        self.assertEqual(self.cache_for(path, http)["error"], "logged_out")
        self.assertEqual(http.calls, [])

    def test_non_string_token_is_logged_out(self) -> None:
        tmp = TempPaths(self)
        path = write_creds(tmp.root, logged_out_oauth(accessToken=12345))
        http = FakeHttp()
        self.assertEqual(self.cache_for(path, http)["error"], "logged_out")
        self.assertEqual(http.calls, [])

    def test_expires_at_zero_with_token_is_logged_out_not_expired(self) -> None:
        tmp = TempPaths(self)
        path = write_creds(tmp.root, logged_out_oauth(accessToken=SECRET))
        http = FakeHttp()
        cache = self.cache_for(path, http)
        self.assertEqual(cache["error"], "logged_out")
        self.assertEqual(http.calls, [])

    def test_token_expired_unchanged_and_no_call(self) -> None:
        tmp = TempPaths(self)
        path = write_creds(tmp.root, {"accessToken": SECRET, "expiresAt": int(NOW * 1000) - 1000})
        http = FakeHttp()
        self.assertEqual(self.cache_for(path, http)["error"], "token_expired")
        self.assertEqual(http.calls, [])

    def test_missing_file_and_no_oauth_object_stay_no_credentials(self) -> None:
        tmp = TempPaths(self)
        http = FakeHttp()
        self.assertEqual(self.cache_for(None, http)["error"], "no_credentials")
        path = write_creds(tmp.root, None)  # file with no claudeAiOauth object
        self.assertEqual(self.cache_for(path, http)["error"], "no_credentials")
        path = write_creds(tmp.root, None, raw="{not json")
        self.assertEqual(self.cache_for(path, http)["error"], "no_credentials")
        self.assertEqual(http.calls, [])

    def test_plan_label_kept_when_logged_out(self) -> None:
        tmp = TempPaths(self)
        path = write_creds(tmp.root, logged_out_oauth())
        cache = self.cache_for(path, FakeHttp())
        plan, windows = claude_api.plan_and_windows(cache, NOW)
        self.assertEqual(plan["label"], "Pro")
        self.assertEqual(plan["name"], "pro")
        self.assertEqual(plan["error"], "logged_out")
        self.assertEqual(plan["source"], "none")
        self.assertFalse(plan["stale"])
        self.assertEqual(windows, [])

    def test_rate_limit_tier_default_claude_ai_with_pro_is_label_pro(self) -> None:
        self.assertEqual(claude_api.plan_label("pro", "default_claude_ai"), "Pro")

    def test_logged_out_keeps_last_good_windows_stale(self) -> None:
        tmp = TempPaths(self)
        good_path = write_creds(tmp.root, {"accessToken": SECRET, "expiresAt": FUTURE_MS,
                                           "subscriptionType": "max", "rateLimitTier": "default_claude_max_5x"})
        body = (Path(__file__).resolve().parent / "fixtures" / "api_usage_200.json").read_bytes()

        class OkHttp(FakeHttp):
            def __call__(self, url: str, headers: Dict[str, str], timeout: float) -> claude_api.HttpResponse:
                self.calls.append(url)
                return claude_api.HttpResponse(200, {}, body)

        good = self.cache_for(good_path, OkHttp())
        out_path = write_creds(tmp.root, logged_out_oauth())
        later = NOW + 3600
        creds = claude_api.read_credentials(out_path)
        bad = claude_api.update_cache(good, creds, later, FakeHttp())
        plan, windows = claude_api.plan_and_windows(bad, later)
        self.assertEqual(plan["error"], "logged_out")
        self.assertTrue(plan["stale"])
        self.assertEqual(plan["source"], "oauth_api")
        self.assertTrue(len(windows) > 0)

    def test_collect_logged_out_writes_state_without_http(self) -> None:
        tmp = TempPaths(self)
        write_creds(tmp.root, logged_out_oauth())
        http = FakeHttp()
        state = collect(tmp.paths(), now=NOW, use_api=True, http_get=http)
        plan = state["providers"]["claude"]["plan"]
        self.assertEqual(plan["error"], "logged_out")
        self.assertEqual(plan["label"], "Pro")
        self.assertEqual(http.calls, [])
        self.assertNotIn(SECRET, tmp.paths().state.read_text(encoding="utf-8"))


class DoctorCredentialsTests(ParisTZ):
    def test_status_lines(self) -> None:
        cases = [
            (None, "credentials: missing"),
            ("{not json", "credentials: malformed"),
            (logged_out_oauth(), "credentials: logged out (empty token)"),
            (logged_out_oauth(accessToken=SECRET), "credentials: logged out (expiresAt 0)"),
            ({"accessToken": SECRET, "expiresAt": int(NOW * 1000) - 60_000}, "credentials: expired since "),
            ({"accessToken": SECRET, "expiresAt": FUTURE_MS}, "credentials: present, expires "),
        ]
        for oauth, expected in cases:
            with self.subTest(expected=expected):
                tmp = TempPaths(self)
                if isinstance(oauth, str):
                    write_creds(tmp.root, None, raw=oauth)
                elif oauth is not None:
                    write_creds(tmp.root, oauth)
                text = doctor(tmp.paths(), NOW)
                self.assertIn(expected, text)
                self.assertNotIn(SECRET, text)

    def test_expired_and_present_show_local_datetime(self) -> None:
        tmp = TempPaths(self)
        write_creds(tmp.root, {"accessToken": SECRET, "expiresAt": int(NOW * 1000) - 60_000})
        self.assertIn("expired since 2026-10-08 13:59 CEST", doctor(tmp.paths(), NOW))
        tmp2 = TempPaths(self)
        write_creds(tmp2.root, {"accessToken": SECRET, "expiresAt": int(NOW * 1000) + 3_600_000})
        self.assertIn("present, expires 2026-10-08 15:00 CEST", doctor(tmp2.paths(), NOW))

    def test_doctor_never_prints_token_material(self) -> None:
        tmp = TempPaths(self)
        write_creds(tmp.root, {"accessToken": SECRET, "refreshToken": "FAKE-REFRESH-TOKEN-FOR-TESTS-0002",
                               "expiresAt": FUTURE_MS, "subscriptionType": "pro"})
        text = doctor(tmp.paths(), NOW)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("REFRESH-9913", text)
        self.assertNotIn("access token", text)

    def test_doctor_logged_out_shows_plan_label(self) -> None:
        tmp = TempPaths(self)
        write_creds(tmp.root, logged_out_oauth())
        text = doctor(tmp.paths(), NOW)
        self.assertIn("credentials: logged out (empty token)", text)
        self.assertIn("subscription: Pro", text)


if __name__ == "__main__":
    unittest.main()
