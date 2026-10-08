# Contributor rules (humans and AI agents)

Read first: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/STATE_SCHEMA.md](docs/STATE_SCHEMA.md),
[docs/DESIGN.md](docs/DESIGN.md). Human-oriented setup is in [CONTRIBUTING.md](CONTRIBUTING.md).

`docs/STATE_SCHEMA.md` is the contract between the collector and the extension.
Do not change it silently. If a change is needed, bump `schema_version` and document
it in the same change, or stop and ask the maintainer.

## Workflow
* Work on a branch or a git worktree, never directly on `main`. Do not merge, rewrite
  published history, or push to shared remotes unless the maintainer asks.
* Keep changes small. Use conventional commits: `feat(collector): …`, `fix(extension): …`,
  `test: …`, `docs: …`, `chore: …`.
* Before you report something as working, run the command that proves it and report its
  output. Do not claim a test passes without running it.

## Code rules
* **Collector** (`collector/ai_usage/`): Python 3.11+ standard library only. No
  third-party imports. Type hints on public functions. No global mutable state.
  Tests must be deterministic: inject `now` and paths, never read the real `$HOME`.
* **Extension** (`extension/ai-usage@hades-x.github.io/`): GNOME Shell 43 **legacy** API
  (`imports.gi.St`, `imports.ui.main`, `ExtensionUtils`). No ES modules and no `import`
  statements. Every signal, timeout, actor and monitor created in `enable()` must be
  released in `disable()`. No synchronous I/O inside the shell process; use `Gio` async
  calls. Release Cairo contexts with `$dispose()`.
* **Secrets and content**: never log, print, persist or put in an exception message:
  OAuth tokens, credential file contents, prompt or message text. Parsers read only usage
  counters, ids, model names and timestamps. Test data must be synthetic.
* **Network**: the only outbound endpoint is `api.anthropic.com` (optional, read-only).
  Do not add listeners, telemetry or other endpoints without a documented decision and a
  SECURITY.md update.

## Personal and environment data
* Never commit real usage figures, hostnames, IP addresses, user names, home-directory
  paths, SSH key names, or any credential. Use `~/`, "your desktop user", and synthetic
  values (`docs/examples/state.example.json` is the reference).
* Screenshots must show only synthetic data and a generic wallpaper.

## Headless harness
* `tools/headless/run-headless.sh` installs an extension, changes dconf settings and starts
  a headless `gnome-shell`. Run it only as a dedicated, unprivileged test account, never as
  your desktop user and never in a shared session. Serialise runs if several jobs share
  one account (for example with `flock`).

## Definition of done
* `make test` passes (or the relevant subset is run and its output reported).
* Docs are updated when behaviour, CLI flags, settings or error codes change.
* CHANGELOG.md has an entry under `[Unreleased]`.
