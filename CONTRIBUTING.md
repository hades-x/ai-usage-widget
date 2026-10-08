# Contributing

Thanks for helping. Please read [AGENTS.md](AGENTS.md) for the code rules that apply to
humans and AI agents alike.

## Development setup

You need:

- Python **3.11** or newer (the tests run on the standard library only).
- Node.js **18** or newer (for the extension's pure-JS tests).
- `glib-compile-schemas` (`libglib2.0-bin` on Debian) if you install the extension.
- For the headless shell test only: GNOME Shell 43, `gjs`, `dbus-run-session`, and a
  dedicated unprivileged test account (see below).

```bash
git clone https://github.com/hades-x/ai-usage-widget
cd ai-usage-widget
make test
```

## Tests

| Command | What it runs |
|---|---|
| `make test-collector` | `python3 -m unittest discover -s collector/tests -v` |
| `make test-extension` | `node extension/tests/run.js` (pure JS: formatting, notices, notification keys) |
| `make test` | both |

Continuous integration runs the Python unit tests on Python 3.11 and 3.13, the node
tests, and syntax and schema checks (JSON, GSettings XML). Run `make test` locally
before opening a PR.

### Headless GNOME Shell smoke test (optional)

```bash
tools/headless/run-headless.sh . docs/examples/state.example.json /tmp/aiu-out
```

- Run it **only** as a dedicated, unprivileged test account, never as your desktop user.
- The script writes `state.json` into that account's `$XDG_RUNTIME_DIR` and changes its
  dconf settings. Keep that account separate from anything you use.
- It produces `probes.txt`, `shell.log`, `prefs.txt` and screenshots in the output directory.

## Commit style

Conventional commits, one logical change per commit, imperative subject under ~72 characters:

```
feat(collector): report logged-out Claude Code as logged_out
fix(extension): release the Cairo context in every repaint
docs: describe the --no-api drop-in
test: cover the token-expired path
```

Scopes in use: `collector`, `extension`, `docs`, `test`, `ci`, `chore`.

## Pull request checklist

- [ ] `make test` passes locally (Python 3.11+, Node 18+).
- [ ] New behaviour has a test; bug fixes have a regression test.
- [ ] Extension code uses the GNOME 43 legacy API (no `import`, no ES modules) and
      releases everything it creates in `disable()`.
- [ ] No secrets, tokens, message content, real usage numbers, hostnames, IP addresses,
      user names or home-directory paths in code, tests, fixtures, docs or screenshots.
- [ ] Docs updated if CLI flags, settings (gsettings keys), error codes or the state
      schema changed. `docs/STATE_SCHEMA.md` changes need a `schema_version` decision.
- [ ] `CHANGELOG.md` entry under `[Unreleased]`.
- [ ] Screenshots, if any, use the synthetic example state.

## Reporting bugs and security issues

- Bugs and feature requests: open a GitHub issue. Include the GNOME Shell version,
  the output of `python3 -m ai_usage doctor` (it prints no secrets), and the relevant
  `journalctl` lines.
- Security issues: do **not** open a public issue. Follow [SECURITY.md](SECURITY.md).
