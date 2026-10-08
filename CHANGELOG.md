# Changelog

## [Unreleased]

## [0.2.0] - 2026-10-08
First public release.
- chore(release): public release preparation
- refactor(extension): UUID renamed to `ai-usage@hades-x.github.io`; the install script migrates from `ai-usage@hades.local`
- chore: MIT license
- ci: GitHub Actions running the Python 3.11/3.13 unit tests, node tests, and schema/syntax checks
- docs: public README, CONTRIBUTING, SECURITY, cleaned architecture and design docs, synthetic example state and screenshots

## [0.1.1] - 2026-10-08
- fix(collector): a Claude Code logged out (empty `accessToken` or `expiresAt` 0) is reported as `plan.error = "logged_out"`, distinct from `no_credentials`; no plan API call is made; plan name/label are kept; last good windows stay as stale
- fix(extension): `no_credentials` → « Claude Code non configuré sur ce poste »; `logged_out` → « Claude Code déconnecté — lance « claude » puis /login » (replaces « Non connecté »)
- feat(collector): `doctor` prints the credentials state (missing / malformed / logged out / present, expires … / expired since …) without any token material
- test: logged-out states, plan label kept, no HTTP call, doctor secrecy; node cases for the new notices

## [0.1.0] - 2026-10-08
- feat(collector): incremental Codex/Claude log parsing, Claude plan API (cache, backoff), state.json v1, systemd user timer
- feat(extension): top-bar chips + popup, date-menu card, threshold notifications, preferences (GNOME 43)
- test: 66 Python tests, 39 node tests, headless gnome-shell 43.9 smoke harness
- docs: architecture, state contract v1, UI design
- fix(extension): popup builds its content eagerly so it opens (GNOME 43 refuses to open an empty menu); the 30 s timer re-renders it only while open
- fix(extension): Cairo context from St.DrawingArea is released with `$dispose()` in every repaint
- fix(extension): threshold notifications are deduped through the persisted `notified-keys` setting, so lock/unlock (disable/enable) no longer re-notifies
- fix(extension): notification dedupe key uses `resets_at` rounded to the nearest 15 min, so API jitter does not re-notify
- fix(extension): removed the unused `ProviderIndicator.refresh()`
- test(headless): card-click, notification-count and prefs probes; prefs.js is built under GJS/GTK4/Adw on the headless display
