# Security policy

## Reporting a vulnerability

Please report security issues privately with GitHub's **private vulnerability reporting**:
open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a
public issue or pull request for an unfixed vulnerability.

Include:

- the affected version (`collector_version` in `state.json`, or the git commit),
- steps to reproduce, and what an attacker could gain,
- whether any credential or usage data was exposed.

We aim to acknowledge reports within 7 days. Fixes are released as a new version and
described in the changelog once users can update.

## Threat model (summary)

ai-usage-widget runs entirely in the user session. It has two components:

- a **collector** (`python3 -m ai_usage collect`, run by a systemd user timer), and
- a **GNOME Shell extension** that only reads the state file the collector writes.

### Assets

- Claude Code OAuth access token in `~/.claude/.credentials.json`.
- Local usage logs (`~/.codex/sessions`, `~/.claude/projects`). They contain prompts and
  responses, so the widget treats them as sensitive even though it only reads counters.
- The derived usage state (`$XDG_RUNTIME_DIR/ai-usage/state.json`, cache under `~/.cache/ai-usage`).

### Controls

- **No network listeners.** Neither component opens a socket or port.
- **One outbound endpoint.** The only network call is an HTTPS `GET` to
  `api.anthropic.com/api/oauth/usage`, with certificate verification (standard library
  default context). It is rate-limited to at most one call per 5 minutes, backs off on
  errors, and can be disabled with `--no-api`.
- **Read-only credentials.** The token is read in memory, never refreshed (refreshing could
  invalidate Claude Code's session), never written to state or cache, and never included in
  logs, exception messages or `doctor` output (`doctor` reports only the credentials state).
- **No content extraction.** Parsers read usage counters, response/message ids, model names
  and timestamps. Prompt and response text is never stored.
- **Least privilege.** The collector and the extension run as the desktop user, never as
  root. The installer refuses to run as root. The extension's only subprocess call is
  `systemctl --user start ai-usage-collector.service`.
- **Restrictive permissions.** The collector installer sets its data directory to `0700`.
- **Logs.** The collector writes one summary line per run to the journal; it never logs
  content, tokens or secrets.

### Out of scope / known limitations

- Anyone who can read your home directory or your `$XDG_RUNTIME_DIR` can already read the
  Claude token and usage logs. The widget does not add protection against local users
  with the same account.
- The Claude usage endpoint is undocumented; its behaviour is outside this project's control.
- The headless test harness changes the settings and extension directory of the account it
  runs under. Run it only in a dedicated test account.
- Supply chain: the project has no runtime dependencies. Review the scripts before running
  `make install`.
