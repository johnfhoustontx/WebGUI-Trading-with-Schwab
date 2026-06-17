# Scanner audio alerts + Settings page + nav badges + modernized drawer

**Date:** 2026-06-17
**Status:** Approved — ready for plan
**Area:** `webgui/` (Tier-1 GUI only; no engine/service changes)

## Goal

Four related GUI features:

1. **Audio alert on new scanner signals**, app-wide (plays on any page), with an
   on/off toggle plus a browser/desktop notification.
2. **A Settings page** (extensible — first batch of options) reachable from the nav.
3. **Notification badges** on nav items that have new/unread items
   (Scanner, Captured Signals, Driver).
4. **A modernized nav drawer** (cosmetic restyle).

All work lives in the GUI tier. The webgui still imports only `nicegui`,
`shared.bus` (via `bus_client`), and `shared.contracts` — **no engine imports**.

## Decisions (from brainstorming)

- **Alert scope:** app-wide — a global watcher runs in the shared `_layout` so the
  chime fires regardless of which page is active.
- **Sound:** bundled chime with a sound picker + volume, **plus** an optional
  OS/browser desktop notification (one-time permission grant).
- **First Settings batch:** audio on/off + sound + volume; only-alert-during-market-hours;
  minimum-score-to-alert. (Default-landing-page / Gamma-symbol prefs were dropped — YAGNI.)
- **Badge targets:** Scanner (count of new signals), Captured Signals (changed
  recommendations), Driver (pending approval).

## Components

### 1. Settings store — `webgui/app_settings.py` (new)

JSON-backed, single-user, pure and unit-testable.

- `DEFAULTS = {alert_enabled: True, alert_sound: "chime", alert_volume: 0.6,
  alert_market_hours_only: True, alert_min_score: 0, desktop_notifications: False}`
- `load()` → dict merged over defaults (missing/garbage file → defaults).
- `get(key)`, `set(key, value)` (writes through to `webgui/data/settings.json`),
  `all()`.
- `data/` is gitignored and regenerates with defaults on a fresh clone.
- No engine imports — pure stdlib (`json`, `pathlib`).

### 2. Audio + notification assets

- `webgui/static/sounds/{chime,bell,ping}.wav` — three short WAVs generated with
  the stdlib `wave` module (small, committed). A generator helper documents how
  they were produced so they can be regenerated.
- Served via `app.add_static_files("/static", webgui/static)` in `main.py`.
- A hidden `<audio id="alert-audio">` is created once in `_layout`; `play_alert(sound,
  volume)` sets `src`/`volume` and calls `.play()` via `ui.run_javascript`.
- **Autoplay caveat:** browsers block audio until the first user gesture; clicking
  any nav link unlocks it. The Settings page notes this and the **Test sound**
  button doubles as the unlock gesture.
- **Desktop notification:** after a one-time `Notification.requestPermission()`
  (triggered by an "Enable desktop notifications" button in Settings), a
  `new Notification(title, {body})` fires alongside the chime when
  `desktop_notifications` is on.

### 3. Global alert + badge watcher — `webgui/alerts.py` (new)

Pure helpers (unit-tested), plus thin wiring used by `_layout`.

Pure functions:
- `scanner_keys(scan)` → set of signal keys (reuses `scanner._sig_key`).
- `scanner_scores(scan)` → {key: composite_score}.
- `unread_count(current_keys, acked_keys)` → len(current − acked).
- `qualifying_new(scan, alerted_keys, min_score)` → new keys whose score ≥ min_score.
- `in_market_hours(now)` → bool (reuses scanner's CT trading-day/hours predicates).
- `should_alert(settings, qualifying, now)` → bool (enabled ∧ qualifying ∧
  (¬market_hours_only ∨ in_market_hours)).

Module-level single-user state (mirrors `_NAV_OPEN`/`_CACHE`):
- `_NAV_BADGES: dict[str, int]` — route → unread count.
- `_ACKED: dict[str, set|version]` — last-acknowledged keys/versions per view.
- `_ALERTED: set` — signal keys already chimed (prevents re-alerting).
- `_LAST_VERSION: dict[str, Any]` — last bus version seen per view.

Wiring: a `ui.timer(2.0, tick)` is added inside `_layout` so it runs on every
page (app-wide). Each tick:
1. Reads bus versions for `options:scan`, `options:captured`, `driver:approvals`.
2. Recomputes badge counts:
   - Scanner: `unread_count(current_keys, acked_scanner_keys)`.
   - Captured: 1 when the captured version changed since last view, else 0.
   - Driver: 1 when `driver:approvals` status is `pending` and unviewed, else 0.
3. Audio: `qualifying_new` minus `_ALERTED`; if non-empty and `should_alert`,
   `play_alert(...)` (+ notification), then add those keys to `_ALERTED`.
4. Updates the badge label elements (per-client refs stored at build time) so the
   counts update live without navigating.

Acknowledgement: when `_layout` builds a page, it clears that route's badge
(sets `_ACKED` for the view to the current keys/version, badge → 0).

### 4. Settings page — `webgui/pages/settings.py` (new)

`render()` builds: an Alerts card (enable switch, sound `ui.select`, volume
`ui.slider`, market-hours switch, min-score `ui.number`, **Test sound** button),
and a Notifications card (**Enable desktop notifications** button + status). Each
control writes through to `app_settings.set(...)`. Route `/settings` + a nav item
(gear icon) registered in `main.py`; added to `test_shell.py`'s expected routes.

### 5. Modernized drawer — `main.py` + scoped `ui.add_css`

A scoped `.nav-drawer` CSS block: rounded active "pill", hover background,
consistent icon sizing, right-aligned `q-badge` counts, tighter group spacing, a
small app-title block at the top. `_nav_link` renders a trailing badge when
`_NAV_BADGES.get(route)`. Cosmetic only — no behavior change beyond badges.

## Data flow

```
options_svc / driver_svc ──> Redis bus ──> bus_client.read/read_version
                                                  │
                          _layout ui.timer(2s) ──> alerts.tick()
                                                  ├─ badge counts → _NAV_BADGES → badge UI
                                                  └─ qualifying_new → play_alert() + Notification
app_settings.json  ──> app_settings.load() ──> gates (enabled/market-hours/min-score)
```

## Testing

- `webgui/tests/test_app_settings.py` — defaults, merge, get/set round-trip,
  garbage-file fallback (uses a tmp path).
- `webgui/tests/test_alerts.py` — `scanner_keys`, `unread_count`, `qualifying_new`
  (min-score filter), `in_market_hours`, `should_alert` truth table.
- `webgui/tests/test_shell.py` — add `/settings` to the expected route set.
- `render()`/`_layout` wiring stays thin; smoke-verify in the browser preview.

## Out of scope / notes

- Default-landing-page and default-Gamma-symbol prefs (not selected).
- Multi-tab: with two tabs open the chime may play in each (acceptable
  single-user). Badge state is recomputed each tick (idempotent), so it never
  double-counts.
- No Tier-2/Tier-3 changes; services and contracts are untouched.
