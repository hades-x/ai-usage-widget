# UI design — GNOME Shell 43 (Adwaita dark), French UI strings

## 1. Panel indicator (top bar)

```
 ✳ 62%  ▸_ 10%
 └┬┘     └┬┘
 Claude   Codex       ← one "chip" per enabled provider, 6 px gap between chips
```
* Chip = provider icon (16 px, symbolic SVG shipped in `icons/`) + mini bar + percent label.
  * `claude-symbolic.svg`: 8-ray asterisk/spark. `codex-symbolic.svg`: `>_` prompt in a rounded square.
    Draw your own simple shapes — **no trademark logos**.
  * Mini bar: `St.DrawingArea` 4 × 14 px **vertical** gauge (fills bottom→top) next to the icon,
    track `rgba(255,255,255,0.18)`, radius 2 px.
  * Label: the **binding** quota = max `used_percent` over the provider's windows, rounded, `NN%`.
    No window available → tokens today, compact (`3.2M`, `850k`).
* Colours (fill + label when ≥ warn):
  | state | colour |
  |---|---|
  | normal Claude | `#D97757` |
  | normal Codex | `#10A37F` |
  | ≥ warn (default 80) | `#F6D32D` |
  | ≥ crit (default 95) | `#ED333B`, label bold |
* Stale (`now − generated_at > 3 × refresh_interval_s`, or plan.stale) → chip opacity 50 %.
  Missing state file → single grey icon + `—`.
* Settings: `panel-position` left / center / right (default right, index 0 = before system menu);
  `panel-mode` = `percent` | `tokens` | `both` (`62% · 3.2M`).

## 2. Popup menu (click on indicator) — width 380 px

```
┌──────────────────────────────────────────────────────┐
│ ✳ Claude Code                         Max 5x         │  header: icon, label, plan badge (pill)
│ Session 5 h    ██████████▌░░░░░│░░  62 %  ⟲ 16:00 · 1 h 30 │
│ Semaine        ████░░░░░░░░│░░░░░░  27 %  ⟲ dim. 01:00     │
│ Semaine Sonnet ▌░░░░░░░░░░░│░░░░░   4 %  ⟲ dim. 01:00     │
│ Aujourd'hui 3,2 M · 7 j 41,3 M · 30 j 163 M          │  dim text
│ ▁▃▁▅█▃█▄▁█▆▅▆▃  (14 j)    opus-5-5 72 % · haiku 28 % │  sparkline (DrawingArea 14 bars) + top models
├──────────────────────────────────────────────────────┤
│ ▸_ Codex                             Pro Lite        │
│ Semaine        █▌░░│░░░░░░░░░░░░░░  10 %  ⟲ mer. 18:58     │
│ Aujourd'hui 25,4 M · 7 j 170 M · 30 j 423 M          │
│ ▁▁▁▁▁▄█▁▂█▃▂▂▄  (14 j)    gpt-6.1-sol 100 %         │
├──────────────────────────────────────────────────────┤
│ Mis à jour il y a 40 s          ⟳ Actualiser   ⚙     │
└──────────────────────────────────────────────────────┘
```
* Quota bar: `St.DrawingArea` 140 × 8 px, radius 4, track `rgba(255,255,255,0.12)`, fill colour per §1.
  **Pace tick**: 1 px vertical line `rgba(255,255,255,0.7)` at `elapsed_percent`. If fill > tick, the
  user is consuming faster than linear pace (tooltip: "Au-dessus du rythme").
* Reset text: `⟲ HH:MM · <countdown>` if < 24 h, else `⟲ <jour abrégé> HH:MM` (local time, `fr_FR`).
  Countdowns recomputed every 30 s locally (no need for a new state file).
* `reset_since_observation` → bar empty + text `réinitialisé`.
* Plan errors → one dim line under the header: `token_expired` → "Token expiré — lance `claude` pour le rafraîchir";
  `http_429` → "Limite d'appels API — nouvel essai à HH:MM"; `no_credentials` → "Claude Code non configuré sur ce poste"; `logged_out` → "Claude Code déconnecté — lance `claude` puis /login";
  others → "Quota indisponible (<code>)". When `stale` show "(valeurs du HH:MM)".
* Number format: French — `3,2 M`, `850 k`, `1,2 Md` (milliards), thin space before `%` optional.
* Footer: relative age of `generated_at`, `⟳ Actualiser` (runs the collector), `⚙` opens prefs.
* Hidden provider (setting) → section omitted. Provider `available == false` → section omitted.

## 3. Date-menu card (clock / notifications / media panel)

Inserted in the **right column** of the date menu (below calendar/events/world clocks/weather),
same visual language as the native "Weather"/"World Clocks" buttons (`St.Button`, style class
`weather-button`-like rounded card, `datemenu-*` paddings).

```
┌ Consommation IA ───────────────────┐
│ ✳ Session 5 h  ███████░░░  62 %    │
│ ✳ Semaine      ███░░░░░░░  27 %    │
│ ▸_ Semaine     █░░░░░░░░░  10 %    │
│ 3,2 M + 25,4 M tokens aujourd'hui  │
└────────────────────────────────────┘
```
* Clicking the card closes the date menu and opens the indicator popup (or prefs if panel hidden).
* Shows at most the 2 most relevant windows per provider (5 h + week).
* Setting `show-in-datemenu` (default true). Must be removed cleanly on disable.

## 4. Notifications

* Source title "Consommation IA", icon = provider icon.
* Fire once per `(provider, window.id, resets_at, level)` when `used_percent` crosses warn then crit.
  Body: "Claude Code — Session 5 h à 82 % (réinit. 16:00)". Crit uses `Urgency.HIGH`.
* No notification on first state load if already above threshold? → **do notify once** (user wants to know).
* Settings: `notify-enabled` (true), `warn-threshold` (80), `crit-threshold` (95).

## 5. Preferences (prefs.js, GTK4 + libadwaita 1.2 — `fillPreferencesWindow`)

Page "Général": position (combo), mode (combo), afficher dans le menu horloge (switch),
fournisseurs Claude / Codex (switches). Page "Alertes": notifications (switch), seuils (spin 1–100).
Page "Avancé": chemin du state.json (entry, empty = default), bouton "Ouvrir les logs" optional.
