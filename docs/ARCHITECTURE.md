# ai-usage-widget — Architecture

Show Claude Code and Codex CLI token consumption and subscription-plan quotas
in the GNOME Shell top bar and date menu. Target: GNOME Shell 43 (legacy
extension API), Python 3.11+, systemd user session.

## 1. Overview

```
desktop user session
│
├─ systemd --user  ai-usage-collector.timer  (every 60 s, + on demand)
│     └─ ai-usage-collector.service (Type=oneshot, python3 -m ai_usage collect)
│          Sources (read-only):
│            Codex : ~/.codex/sessions/**/rollout-*.jsonl, ~/.codex/archived_sessions/**
│                    (token_usage_record, event_msg/token_count.rate_limits, turn_context.model)
│            Claude: ~/.claude/projects/**/*.jsonl (assistant message.usage)
│                    + GET https://api.anthropic.com/api/oauth/usage   (plan %, ≤ 1 call / 300 s)
│          Cache  : ~/.cache/ai-usage/index.json        (incremental offsets + daily aggregates)
│                   ~/.cache/ai-usage/claude_usage.json (last good API answer + backoff state, no token)
│          Output : $XDG_RUNTIME_DIR/ai-usage/state.json (atomic tmp+rename, schema v1)
│          Logs   : journald, ONE summary line per run, never content/tokens/secrets
│
└─ gnome-shell extension  ai-usage@hades-x.github.io  (GNOME 43, legacy `imports.*` API, NO ESM)
      ├─ Gio.FileMonitor on $XDG_RUNTIME_DIR/ai-usage/ → async read state.json → render
      ├─ Panel indicator (PanelMenu.Button) + popup with full detail
      ├─ Card in the date menu (clock / notifications / media panel), right column
      ├─ GNOME notifications at warn/crit thresholds (default 80 % / 95 %)
      └─ "Refresh" → `systemctl --user start ai-usage-collector.service` (Gio.Subprocess, async)
```

Before the public release the extension UUID was `ai-usage@hades.local`. The
install script migrates from that name; see [CHANGELOG.md](../CHANGELOG.md).

## 2. Key decisions (and why)

| Decision | Why | Rejected alternative |
|---|---|---|
| Separate Python collector, thin extension | gnome-shell is the compositor: parsing hundreds of MB of JSONL or doing HTTP inside it can freeze the whole desktop. A crash in the collector never takes the shell down. | Parse in GJS inside the extension |
| systemd user **timer + oneshot** (not a daemon) | No long-lived process, no leak, restart semantics for free, `journalctl` observability, trivial on-demand refresh | Long-running daemon / D-Bus service (v2 if push updates are ever needed) |
| Incremental parsing (per-file inode/size/offset) | Codex logs can reach hundreds of MB; full reparse every minute wastes CPU | Full reparse |
| state.json in `$XDG_RUNTIME_DIR` (tmpfs) | No disk writes every minute; disappears on logout, which is correct | ~/.cache |
| Python **stdlib only** | No venv/pip to maintain on the desktop; `urllib` is enough | requests, pydantic |
| Codex quota from local logs only | `token_count.rate_limits` already contains `used_percent`, `window_minutes`, `resets_at`, `plan_type`, credits → zero network, zero credentials | chatgpt.com backend endpoint |
| Claude quota from `/api/oauth/usage` | Only source of the real plan % (same as Claude Code `/usage`). Read the access token **read-only**, never refresh it (refresh-token rotation would log Claude Code out), ≥ 300 s between calls, exponential backoff + `Retry-After` on 429, keep last good value flagged stale. Can be disabled with `--no-api`. | No plan % for Claude |
| GNOME 43 legacy API | Shell 43 does not support ESM extensions (ESM starts at 45) | ESM |
| No cost estimation in v1 | Model prices change; a wrong $ figure is worse than none | pricing table (backlog) |

## 3. Data semantics (normalised token counters)

Every token aggregate uses the same 5 counters, all non-negative integers:

| counter | Claude (`message.usage`) | Codex (`usage` of token_usage_record) |
|---|---|---|
| `input` (fresh, uncached) | `input_tokens` | `input_tokens - cached_input_tokens` |
| `cached_input` (cache reads) | `cache_read_input_tokens` | `cached_input_tokens` |
| `cache_write` | `cache_creation_input_tokens` | `cache_write_input_tokens` (0 if absent) |
| `output` | `output_tokens` | `output_tokens` |
| `reasoning` (**subset** of output, informational) | 0 | `reasoning_output_tokens` |

`total = input + cached_input + cache_write + output` (reasoning is NOT added).
Plus `requests` = number of deduplicated API responses.

Day boundaries use the **local timezone of the machine**.
Windows: `today`, `last_7d` (today + 6 previous days), `last_30d`.

### Codex parsing rules
* Files: `~/.codex/sessions/**/*.jsonl` and `~/.codex/archived_sessions/**/*.jsonl` (if present). Honour `$CODEX_HOME`.
* Usage events: lines with `type == "token_usage_record"`; dedupe key = `payload.response_id` (fallback: `payload.thread_id + ordinal`). Use `payload.usage` (per-response), **never** `turn_token_usage`/`thread_token_usage` (cumulative).
* Legacy fallback (older CLI, no token_usage_record in the file): `event_msg` / `payload.type == "token_count"` → `payload.info.last_token_usage`. Do not use both sources for the same file.
* Model: last seen `turn_context.payload.model` in the same file before the event (persist "current model" per file in the index for incremental parsing). Unknown → `"unknown"`.
* Quota: the **most recent** (by `timestamp`) `token_count` event whose `payload.rate_limits` is non-null, across all files. `primary` / `secondary` → windows. `resets_at` is epoch seconds. If `resets_at` < now, the window has reset since observation → `used_percent = 0`, `reset_since_observation = true`.
* `plan_type` (e.g. `prolite`, `plus`, `pro`) → plan label; `credits` copied as-is.

### Claude parsing rules
* Files: `~/.claude/projects/**/*.jsonl` (+ `~/.config/claude/projects/**` if present; honour `$CLAUDE_CONFIG_DIR`, comma-separated allowed).
* Usage events: `type == "assistant"` and `message.usage` present. Skip `message.model == "<synthetic>"`.
* Dedupe key = `message.id + ":" + requestId` (streaming writes the same message several times; sidechain/sub-agent files can repeat it). Keep the seen-key set only for events < 48 h old in the index to bound its size.
* Timestamp: top-level `timestamp` (ISO 8601, UTC).

### Claude plan API
```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken from ~/.claude/.credentials.json>
anthropic-beta: oauth-2025-04-20
User-Agent: ai-usage-widget/<version>
```
Response (fields may be null/absent — be tolerant, ignore unknown keys):
`five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet` → `{utilization: float 0-100+, resets_at: ISO}`; `extra_usage` → `{is_enabled, monthly_limit, used_credits, utilization}`.

Error codes written to `plan.error` (the extension text for each is in [DESIGN.md](DESIGN.md) §2):

| code | cause | API call made? |
|---|---|---|
| `no_credentials` | no credentials file, or no `claudeAiOauth` object | no |
| `logged_out` | `accessToken` empty, or `expiresAt` == 0 (Claude Code logged out) | no |
| `token_expired` | `expiresAt` (ms) is in the past | no |
| `http_401` | token rejected | yes |
| `http_429` | rate limited (`Retry-After` honoured) | yes |
| `http_5xx` | server error | yes |
| `network` | transport failure (message never logged) | yes |
| `bad_response` | body is not a JSON object | yes |
| `http_<n>` | any other HTTP status | yes |

Once an error happens, the collector keeps the last good windows (`stale = true`)
and backs off: 300 s, 600 s, 1200 s, then 1800 s, or the `Retry-After` value on 429.
A logged-out or expired token does not reset the plan name/label.

Plan label from `claudeAiOauth.subscriptionType` / `rateLimitTier` (e.g. `max` + `default_claude_max_5x` → "Max 5x").

## 4. Security

* Collector and extension run as the desktop user; nothing listens on the network.
* Only outbound call: `api.anthropic.com` over HTTPS with certificate verification (stdlib default context). It is optional (`--no-api`).
* Secrets never logged, never written to state.json/cache, never in exceptions (sanitised).
* No prompt / message content is ever extracted: parsers only read usage counters, ids, model, timestamps.
* The extension executes only `systemctl --user start ai-usage-collector.service`.

See [SECURITY.md](../SECURITY.md) for the threat model and how to report issues.

## 5. Observability

* `journalctl --user -u ai-usage-collector -n 20`: one line per run (duration, files scanned/changed, events added, per-provider status).
* Extension errors: `journalctl _COMM=gnome-shell -b | grep -i ai-usage`.
* `python3 -m ai_usage doctor` (with `PYTHONPATH=~/.local/share/ai-usage/collector`) prints sources found, file counts, last event dates, credentials state (never the token) and API cache status.

## 6. Repository layout

```
collector/ai_usage/                 Python package (stdlib only)
collector/tests/                    unittest + synthetic fixtures
systemd/                            user units (.service, .timer)
scripts/install-collector.sh        installs the collector + timer (make install-collector)
scripts/install-extension.sh        installs the extension (make install-extension)
extension/ai-usage@hades-x.github.io/   GNOME Shell extension (GNOME 43, legacy API)
extension/tests/                    node-runnable tests for pure JS
tools/headless/                     headless gnome-shell 43 smoke harness (dedicated test user only)
docs/                               contract, architecture, design, examples
Makefile                            install / uninstall / test targets
```

## 7. Installation

`make install` runs the collector and extension installers as your desktop user
(root is refused). See [README.md](../README.md#install) for the full steps.
