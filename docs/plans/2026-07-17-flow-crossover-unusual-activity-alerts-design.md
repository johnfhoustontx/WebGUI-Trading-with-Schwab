# Options-flow alerts: put/call premium crossover + unusual activity — design (2026-07-17)

## Problem

The app collects intraday call/put **premium ($)** and **volume (contracts)** per
symbol every minute (the Flow chart data), but nothing watches it. The user wants to
be pinged — in-app popup **and** Discord/Telegram — on two events:

1. **Crossover** — when cumulative call **premium ($)** overtakes put premium (or vice
   versa) for a symbol: a money-weighted sentiment flip.
2. **Unusual activity** — when there's a sudden burst of call or put **trades** in a
   symbol relative to its own recent pace.

## Data reality (shapes the design)

Per symbol, the 1-min GEX poll computes and stores in `gex_history_db`
(`call_prem`/`put_prem`/`call_vol`/`put_vol`), read via
`gex_history_db.load_flow_series(conn, symbol, date)`:
- **Daily-cumulative** call/put **premium ($)** = `Σ mark × totalVolume × 100`, and
  **volume (contracts)** = `Σ totalVolume`.
- **Unsigned** — Schwab has no time-&-sales tape, so there is NO buy/sell split. This
  is *total traded*, not *net buying*. Alerts say "unusual call activity", not
  "call buying".

## Decisions (locked in brainstorming)

1. **Crossover keys on PREMIUM ($)** — money-weighted, matches the Flow chart's top
   line; less noisy than raw volume (which cheap far-OTM contracts dominate).
2. **Whole collected universe (~24)** — index base `$SPX`/`SPY`/`QQQ` ∪ `Top 20.xlsx`
   — to catch unusual single-name flow, leaning on solid cooldowns for noise control.
3. **Unusual = per-minute volume increment spike vs the symbol's OWN rolling baseline**
   (≥ K× trailing average, with an absolute floor + warm-up) — auto-scales per symbol,
   catches a real-time influx.
4. **In-app popup = toast + chime** (reuse the existing scanner-alert infra), not a
   modal — non-blocking across ~24 symbols × 2 alert types.

## Architecture

Server-side detection in `options_svc`, riding the existing 1-min GEX poll; the browser
is only a reader (mirrors the existing signal/action/EOD push architecture).

### 1. Detection placement
A new handler `handlers.run_flow_alerts(bus)` runs each 1-min slot **after**
`collect_gex_snapshots`. For each collected symbol it reads today's flow series from
`load_flow_series` (stateless / restart-safe — no in-memory rolling state), runs the
pure detectors, and for each new alert pushes (Discord/Telegram) + appends to a rolling
cache view. Naturally gated to the **08:00–15:20 CT trading-day** window (it only runs
inside the poll; flow is daily-cumulative and meaningless off-hours). The engine
(`options-scanner`) stays push-free — the pure logic lives in
`services/options_svc/flow_alerts.py`.

### 2. Detection logic (pure, in `flow_alerts.py`)
Operate on the day's flow series + a small Redis **cooldown map** (last-fired time per
`symbol|type|side`) that is the single source of truth for "already fired".

- **Crossover (premium):** `net = call_prem − put_prem`. Fire when `sign(net)` flips vs
  the prior snapshot AND `|net| ≥ CROSS_BAND` (a % of the larger side — a neck-and-neck
  graze near zero doesn't chatter). Direction: net −→+ = "Calls overtook puts" (bullish
  flip); +→− = "Puts overtook calls" (bearish flip). Per-symbol cooldown suppresses
  re-crossings.
- **Unusual (volume spike):** compute per-minute increments of `call_vol`/`put_vol`
  (this snapshot − last). `baseline = trailing average of the last WINDOW increments`
  (excluding the latest). Fire the call side when `latest_call_inc ≥ K × baseline_call`
  AND `≥ FLOOR`; put side independently. Needs `≥ MIN_POINTS` increments (warm-up).
  Per-`symbol|side` cooldown so a sustained surge pings once.

Defaults (config, tunable): `K=4`, `WINDOW=20`, `FLOOR` per-liquidity, `CROSS_BAND=0.02`
(2%), crossover cooldown 30 min, spike cooldown 20 min, `MIN_POINTS=5`.

### 3. Delivery
- **Discord + Telegram** (`push_notify.send_flow_alert(alert, config)`): a Telegram HTML
  line + a Discord embed, color-coded (green = calls-overtook / call surge; red =
  puts-overtook / put surge), reusing the existing channel senders + config-presence
  gating (missing creds → silent no-op). Content: symbol · type · numbers · CT time.
- **In-app popup:** `run_flow_alerts` appends each alert to a rolling
  `cache:options:flow_alerts` (`{date, alerts:[{id, symbol, type, side, text, ts}], …}`,
  capped ~50, date-scoped). The webgui's existing 2-s watcher (`main.py` `_tick`) gets a
  branch: diff new alert `id`s vs an acked set (same pattern as the scanner badge), and
  for each new one fire `play_alert(sound, volume)` + a colored `ui.notify` toast (+
  optional desktop notification), reusing the current alert-sound/volume/desktop
  settings.
- **Dedup across both:** the Redis cooldown map means one detection → one push + one
  popup, then quiet for the cooldown.

### 4. Config, toggle, gating
- Thresholds in a new **`config/flow_alerts.toml`** (loaded by `flow_alerts.py`, in-code
  defaults if missing) — matches `commissions.toml`/`theme.toml`.
- `flow_alerts.enabled` flag (default on) = whole-feature kill-switch; the push side also
  self-gates on `shared/notifications.json` creds.
- A **Settings → "Flow alerts"** on/off checkbox silences the browser popups
  independently of the phone pushes; reuses the existing alert-sound/volume/desktop
  controls.
- Gating is automatic (runs only in the 08:00–15:20 CT poll window); `FLOOR` + warm-up
  cover the thin 08:00–08:30 pre-open window.

### 5. Testing
- Pure detectors: crossover flip + direction, band rejection, cooldown; spike vs
  baseline, `K`/`FLOOR`/warm-up boundaries, call/put independence.
- Push: `send_flow_alert` Telegram text + Discord embed shape, color-by-type, config
  absent → no-op.
- Handler: `run_flow_alerts` end-to-end (fakeredis + seeded series) → cache view +
  cooldown update + push calls (mocked); a second identical tick is silent (cooldown).
- Webgui: the watcher branch fires toast/chime on a new alert id, silent on an acked one.
- TDD per layer.

## Non-goals
- No buy/sell direction (impossible without a tape).
- No premium-based spike (spike keys on volume = "amount of trades"); premium is the
  crossover metric only.
- No historical per-symbol daily baseline (the spike uses today's rolling window).
- No modal dialog (toast + chime only).
