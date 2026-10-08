# state.json — contract v1

Producer: collector. Consumer: GNOME extension. Path:
`$XDG_RUNTIME_DIR/ai-usage/state.json` (fallback `/run/user/<uid>/ai-usage/state.json`).
Written atomically (write `state.json.tmp` in the same dir, `fsync`, `os.replace`).
Encoding UTF-8, timestamps ISO 8601 **UTC with `Z`**, percentages are floats 0–100
(may exceed 100 if the provider reports it), token counts are integers.

**Compatibility rule:** the consumer MUST ignore unknown keys and tolerate any
optional key being `null`/absent. Breaking changes bump `schema_version`.

```jsonc
{
  "schema_version": 1,
  "collector_version": "0.1.0",
  "generated_at": "2026-10-08T14:30:00Z",
  "refresh_interval_s": 60,            // consumer marks data stale if now - generated_at > 3 × this
  "providers": {
    "claude": { /* Provider */ },
    "codex":  { /* Provider */ }
  }
}
```

## Provider

| key | type | notes |
|---|---|---|
| `id` | `"claude"` \| `"codex"` | |
| `label` | string | `"Claude Code"`, `"Codex"` |
| `available` | bool | false if no source directory exists at all |
| `plan` | Plan | always present |
| `windows` | Window[] | quota windows, may be empty; order = display order |
| `tokens` | Tokens | always present (zeros if nothing) |
| `last_activity_at` | ISO \| null | timestamp of most recent usage event |
| `errors` | string[] | short machine codes, e.g. `"parse_errors:3"`, `"source_missing"` |

## Plan

| key | type | notes |
|---|---|---|
| `name` | string \| null | raw: `"max"`, `"pro"`, `"prolite"`… |
| `label` | string \| null | human: `"Max 5x"`, `"Pro Lite"` |
| `source` | `"oauth_api"` \| `"local_logs"` \| `"none"` | |
| `observed_at` | ISO \| null | when the quota values were measured (API fetch time / log event time) |
| `stale` | bool | true if values come from cache after an error, or are older than 15 min for the API |
| `error` | string \| null | see ARCHITECTURE.md §3 |
| `credits` | object \| null | provider-specific passthrough (Codex `credits`, Claude `extra_usage`) |

## Window

| key | type | notes |
|---|---|---|
| `id` | string | Claude: `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`. Codex: `primary`, `secondary` |
| `label` | string | French UI label: `"Session 5 h"`, `"Semaine"`, `"Semaine Opus"`, `"Semaine Sonnet"`. Codex from `window_minutes`: 300 → `"Session 5 h"`, 10080 → `"Semaine"`, else `"Fenêtre <n> h"` |
| `used_percent` | float | |
| `window_minutes` | int \| null | Claude: 300 / 10080 |
| `resets_at` | ISO \| null | |
| `elapsed_percent` | float \| null | % of the window elapsed at `generated_at` = 100 × (1 − (resets_at − now)/window). Used for the pace tick. Clamp 0–100 |
| `reset_since_observation` | bool | true when resets_at < now (used_percent then forced to 0) |

## Tokens

```jsonc
{
  "today":    Counters,
  "last_7d":  Counters,
  "last_30d": Counters,
  "by_model_7d": [ { "model": "gpt-6.1-sol", "total": 123, "share": 0.72 } ],  // sorted desc, share 0–1
  "daily_14d":   [ { "date": "2026-09-25", "total": 0 }, … ]  // exactly 14 entries, oldest first, local dates
}
```
`Counters` = `{ "input", "cached_input", "cache_write", "output", "reasoning", "total", "requests" }` (ints).

See `docs/examples/state.example.json` for a full sample.
