# Flow alerts: contract-level unusual options activity (strike/cost/expiry/premium) — design (2026-07-18)

## Problem

The shipped flow alerts (2026-07-17) carry no strike / expiry / cost, because the
"unusual activity" alert keys on **aggregate** call/put volume summed across the whole
chain (`flow_skew.index_call_put_volume` sums `totalVolume` over every strike + every
expiration). The user wants each unusual-activity alert to name the **specific** hot
contract and include **Strike, Cost, Expiry, Premium amount**. Those fields are
per-contract, so the aggregate signal can't provide them.

Separately: the in-app Windows desktop notification wiring is correct (the flow branch
calls the same `notify_desktop` as the working scanner alerts); it no-ops unless
`desktop_notifications` is on, browser permission is granted, Windows allows browser
notifications, AND an alert actually fires (08:00–15:20 CT only). Not a code change —
diagnosis only.

## Decisions (locked in brainstorming)

1. **"Unusual" = contract-level volume/open-interest** (classic UOA). Computable from a
   single live chain snapshot (no new storage) and directly yields strike/expiry/cost
   (the mark)/premium.
2. **Replace** the aggregate rolling-baseline volume-spike with the contract-level UOA
   (one clear, information-rich alert per contract; avoids two overlapping signals). The
   **crossover** (aggregate premium flip) stays and gains the premium amounts in its text.
3. **Gate hard + cap** to control noise (esp. 0-DTE, whose vol/OI is structurally huge):
   `vol/oi ≥ K` AND `vol ≥ vol_floor` AND `premium ≥ premium_floor`, skip `oi ≤ 0`, and
   alert only the **top-N by premium per symbol per tick**. 0-DTE stays in — the premium
   floor + top-N tame it.

## Architecture

### 1. Detector (pure, `services/options_svc/flow_alerts.py`)
`detect_uoa(chain, cfg)`: walk `callExpDateMap` + `putExpDateMap` once. Per contract read
`totalVolume`, `openInterest`, mark (mid = cost — reuse `flow_skew._contract_mark` logic),
strike (map key), expiry + dte (map key `"YYYY-MM-DD:dte"`). Qualify when `oi > 0` and
`vol/oi ≥ K` and `vol ≥ vol_floor` and `premium = mark·vol·100 ≥ premium_floor`. Sort
qualifiers by premium desc, take top-N. Return dicts: `{symbol, type:"uoa", side:"call"|
"put", strike, expiry, dte, cost, volume, oi, vol_oi, premium}`. Pure + defensive.

### 2. Where the chain comes from (no re-fetch)
The 1-min GEX poll (`compute.collect_gex_snapshots`) already fetches every symbol's chain
and exposes each via `gex_collector.poll_once(on_chain=…)`. Run `detect_uoa` inside that
hook (guarded, thresholds from `load_thresholds()`), stashing the small per-symbol result
list in a **consume-once module dict** (`_uoa_stash`). No extra `get_option_chain` calls.

### 3. Handler
`handlers.run_flow_alerts` gains the UOA path: take the `_uoa_stash`, and for each
qualifying contract build an alert (id `{symbol}|uoa|{side}|{strike}|{expiry}` — stable
across ticks so the same hot contract pings once via the cooldown map, cooldown default
30 min). Crossover path unchanged (reads the aggregate flow series from the DB). Both push
via `send_flow_alert` + append to `cache:options:flow_alerts` exactly as today.

### 4. Alert content (the requested fields)
`alert_text` (UOA): `SPY 07/18 450C — UNUSUAL: 8,200 vol vs 1,300 OI (6.3×) · $1.85 ·
$1.52M premium` — Strike (`450` + C/P), Expiry (`07/18`, `0DTE` tag when dte=0), Cost (mark),
Premium ($, humanized $Xk/$X.XM), plus volume/OI/ratio context. Discord embed = same line
(title = the contract). Green (call) / red (put).
Crossover text gains the premiums: `$SPX — call premium overtook puts: $2.10M calls vs
$1.95M puts (bullish flip)`.

### 5. Config (`config/flow_alerts.toml`)
The `[spike]` section is **replaced** by `[uoa]`:
```toml
[uoa]
k = 3.0                 # flag when volume >= k x open interest
vol_floor = 500         # min contract volume
premium_floor = 250000  # min $ premium (mark x vol x 100) — real money only
top_n = 3               # most-significant contracts per symbol per tick (by premium)
cooldown_min = 30       # per-contract re-fire cooldown
```
`[crossover]` (`band`/`min_premium`/`cooldown_min`) unchanged. `_DEFAULTS` in
`flow_alerts.py` updated to match (drop spike defaults, add uoa).

### 6. Delivery (unchanged)
Discord/Telegram push + the webgui toast+chime popup are unchanged — the richer `text`
flows through both. The three gates (server `enabled` / notifications `enabled` / webgui
`flow_alerts_enabled`) are unchanged.

## Testing
- `detect_uoa`: vol/OI qualification, floors, skip oi≤0, top-N cap + premium sort, field
  extraction (strike/expiry/dte/cost/premium), 0DTE tag.
- `alert_text` UOA (all fields) + enriched crossover text.
- Handler: on_chain stash → `run_flow_alerts` emits UOA alerts, cooldown-deduped; the
  published view + push carry the new fields.
- Existing crossover / push / webgui tests stay green (only the spike detector is replaced;
  crossover text change updates its assertions).

## Non-goals
- No per-contract volume-increment history (vol/OI needs none).
- No strike/expiry on the crossover (it's inherently whole-chain).
- No buy/sell direction (Schwab has no tape).
- No desktop-notification code change (diagnosis only).
