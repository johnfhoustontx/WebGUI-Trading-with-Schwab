# Options Extended Trading Hours — Design

**Date:** 2026-08-02
**Status:** Approved for planning
**Activation:** 2026-08-17 (all behavior below is inert before this date)

## 1. What is changing in the market

Cboe C1 is offering **extended trading hours on select multi-listed single-stock
options**:

| Session | ET | CT | Notes |
|---|---|---|---|
| Pre-market ETH | 07:30–09:25 | 06:30–08:25 | New — outside our current windows |
| Regular | 09:30–16:00 | 08:30–15:00 | Unchanged |
| Curb ETH | 16:00–16:15 | 15:00–15:15 | Already inside our 15:20 CT collection stop |

Eligibility is ADV ≥ 150k contracts, underlying market cap ≥ $50B, and underlying
ADV ≥ 10M shares, measured over the trailing six months. **The eligible list is
refreshed semi-annually**, which is the single most important constraint on the
design: any hardcoded symbol list is guaranteed to go stale twice a year.

Cboe's published launch date is 2026-07-13, "subject to SEC approval of a related
rule filing." We are building to a **2026-08-17** activation per direction from the
project owner. The date is configuration, not a constant, so a further slip is a
one-line edit.

Separately, `$SPX`, `$VIX`, `$XSP` and `$RUT` already trade Global Trading Hours
(20:15–09:25 ET) plus a curb session (16:15–17:00 ET). **This design deliberately
does not cover overnight GTH** — see §3.

## 2. Key discovery: Schwab already answers the eligibility question

Probing the live proxy (2026-08-02) shows the option chain response carries a
root-level **`ethOptionEligible`** boolean, and it discriminates correctly:

```
NVDA True   TSLA True   AAPL True   PLTR True   AMD True   AVGO True   MU True
SPY  False  QQQ  False  IWM  False  KO   False  XOM  False
$SPX True   $VIX True
```

Three consequences:

1. **We never maintain a symbol list.** Eligibility is read from a chain response
   we already fetch every minute for every collected symbol. Semi-annual list
   refreshes are absorbed automatically, at zero additional API cost.
2. **SPY, QQQ and IWM are not eligible.** The program is single-stock; the ETF
   options are excluded. Since the scanner and driver trade predominantly
   SPY/QQQ/`$SPX` credit spreads, only `$SPX` of the usual instrument set is
   ETH-eligible. Extended hours will surface single-name activity we currently
   have no view of.
3. **The universe is mixed.** Roughly 7 of our ~45 collected symbols are eligible,
   so at 07:00 CT most of the board is legitimately frozen. That must render as
   "not eligible," never as "stale."

Two further runtime signals from the same probe:

| Signal | Location | Meaning |
|---|---|---|
| `securityStatus` | equity quote | `"Normal"` / `"Closed"` — live session state |
| `quoteTimeInLong` | option contract | Mark freshness; distinguishes a live ETH quote from Friday's close |

## 3. Scope decisions

Three decisions were taken during design review.

**Session coverage — cash-adjacent only.** Cover the pre-market and curb sessions
attached to the cash day. Do **not** poll overnight GTH for `$SPX`/`$VIX`. GEX
collection is already the dominant consumer of our Schwab API budget (~36k of ~70k
calls/day); overnight polling would be a structural cost increase for a session we
have no execution posture in.

**Execution posture — observe only in extended hours.** The app gains full ETH
awareness: collection, gamma, flow, matrix, display. The **execution layer stays
inert** outside regular hours — no driver entries, no paper auto-manage, no
auto-close. Manual action from the UI remains available.

This posture is already the status quo and requires no change to the execution
path. `paper_engine.in_trading_window` is 08:30–15:00 CT with an existing comment
stating fills "must only use live RTH quotes (never premarket/stale data)"
([paper_engine.py:33-48](../../options-scanner/paper_engine.py)), and the driver's
entry window is 09:45–15:30 ET
([scheduler.py:67-68](../../services/driver_svc/scheduler.py)). Both are correct
as written and are explicitly **out of scope for modification**.

**Activation — 2026-08-17, not earlier.** Every ETH branch is gated on the
activation date. Before it, `session_at()` reports `CLOSED` for the ETH windows and
every consumer behaves byte-identically to today.

## 4. The problem this exposes

There is no single definition of "market hours" in this codebase. Session windows
are hardcoded in **at least 14 places** across five services, the webgui and the
engines, and the holiday calendar is duplicated in **nine**:

**Window constants**

| File | Constant | Value (CT) |
|---|---|---|
| `services/options_svc/scheduler.py:27` | `_SCAN_START/_SCAN_END` | 08:00–15:15 |
| `services/options_svc/scheduler.py:78` | `_GEX_START/_GEX_STOP` | 08:00–15:20 |
| `services/options_svc/scheduler.py:168` | `_PAPER_HOURS` | 09–14 hourly |
| `services/options_svc/scheduler.py:315` | `_MKT_SNAP_START/_END` | 08:30–15:00 |
| `services/sentiment_svc/scheduler.py:35` | `_RTH_START/_RTH_END` | 08:30–15:00 |
| `services/market_svc/scheduler.py:25` | `_RTH_START/_RTH_END` | 08:30–15:00 |
| `services/portfolio_svc/scheduler.py:43` | `_RTH_START/_RTH_END` | 08:30–15:00 |
| `services/driver_svc/scheduler.py:67` | `RTH_START/RTH_END` | 09:45–15:30 ET |
| `options-scanner/gex_collector.py:30` | `START_HOUR/STOP_HOUR` | 08:00–15:20 |
| `options-scanner/gex_status.py:8` | `MARKET_OPEN/MARKET_CLOSE` | 08:30–15:20 |
| `options-scanner/paper_engine.py:37` | `RTH_START_H/RTH_END_H` | 08:30–15:00 |
| `webgui/alerts.py:12` | `_OPEN/_CLOSE` | 08:00–15:00 |
| `tools/check_env.py:38` | `MARKET_OPEN_HOUR/CLOSE` | 08–16 |
| `claude-driver/config.py:169` | `MARKET_OPEN_HOUR/CLOSE` | 09:30–16:00 ET |

**Holiday calendar copies:** `services/{options,sentiment,portfolio,market,driver}_svc/scheduler.py`,
`shared/notify/channels.py:364`, `webgui/alerts.py:20`, `options-scanner/scanner.py:111`,
`claude-driver/config.py:118`.

Note that `gex_status.MARKET_OPEN = 08:30` is **already wrong** — collection has
started at 08:00 since the 2026-07-11 cadence change, and the UI string still reads
"Collector: starts 8:30". This is the drift the duplication produces, and it is why
we are fixing the seam rather than adding a fifteenth window.

## 5. Architecture

Two new artifacts, following existing house conventions.

### 5.1 `config/sessions.toml`

Session windows and the activation date become data, alongside the existing
`commissions.toml` / `flow_alerts.toml` / `theme.toml`.

```toml
# All times are America/Chicago. ET and CT shift together for DST, so the
# CT values are stable year-round.

[activation]
# Extended-hours behavior is INERT before this date. Everything degrades to
# the pre-ETH behavior, byte-identical to today.
extended_hours_from = "2026-08-17"

[sessions.premarket_eth]
start = "06:30"   # 07:30 ET
end   = "08:25"   # 09:25 ET

[sessions.regular]
start = "08:30"   # 09:30 ET
end   = "15:00"   # 16:00 ET

[sessions.curb_eth]
start = "15:00"   # 16:00 ET
end   = "15:15"   # 16:15 ET

[collection]
# Widened only for ETH-eligible symbols; see §5.3.
eth_start = "06:30"
stop      = "15:20"

[alerts]
# Phone pushes and in-app toasts during ETH. Off by default — a 06:30 CT
# push for a thin premarket print is noise, and the posture is observe-only.
fire_in_extended_hours = false
```

### 5.2 `shared/market_calendar.py`

The single logic seam. This is the module the
[2026-07-03 config-consolidation plan](2026-07-03-config-consolidation.md)
specified but never shipped, extended from holidays-only to sessions.

```python
class Session(Enum):
    CLOSED        = "closed"
    PREMARKET_ETH = "premarket_eth"
    REGULAR       = "regular"
    CURB_ETH      = "curb_eth"

HOLIDAYS: frozenset[date]

def is_holiday(d) -> bool
def is_trading_day(d) -> bool
def next_trading_day(d) -> date
def prev_trading_day(d) -> date

def extended_hours_active(d) -> bool      # d >= activation date
def session_at(now_ct) -> Session         # ETH windows -> CLOSED before activation
def is_regular_hours(now_ct) -> bool      # exact current RTH semantics
def in_collection_window(now_ct, *, eth_eligible=False) -> bool
```

Pure functions over an mtime-cached TOML read, matching the `theme.load_theme` and
`flow_alerts.load_thresholds` pattern. `shared/` is a namespace package with no
`__init__.py`, so `from shared.market_calendar import ...` resolves for all five
services, the webgui and the engines once the repo root is on `sys.path`. The one
consumer needing a bootstrap is `options-scanner/scanner.py`, modeled on
`services/options_svc/commission.py:9-12`.

**Migration is a behavior-preserving refactor first.** Each of the fourteen window
constants and nine holiday sets is replaced by a calendar call whose output is
asserted identical across a swept range of timestamps before any ETH behavior is
added. The windows that legitimately differ (the driver's 09:45–15:30 ET entry
window, the scanner's 08:00–15:15 operating window) stay distinct — they become
*named* windows in the calendar, not collapsed into one.

### 5.3 Per-symbol eligibility

Harvested from the chain the 1-minute GEX poll already fetches. `poll_once` has an
existing `on_chain` hook (used today for the UOA stash and the tick-chain stash);
eligibility rides the same hook.

Published to **`cache:options:eth_eligible`** as `{date, symbols: {SYM: bool}}`,
refreshed once per session day. Consumers read the cache, never the chain.

**Zero additional API calls.** This is the design's central economy.

### 5.4 Collection window widening

The pre-market window polls **only ETH-eligible symbols**:

```
06:30–08:00 CT  ~7 eligible symbols   ≈   630 calls/day
08:00–15:20 CT  ~45 symbols           (unchanged)
```

Polling the full universe pre-market would cost ~4,050 calls/day for ~38 symbols
that are not quoting. Restricting to the eligible subset makes the increase
**≈ +2% of the daily Schwab budget**, not +20%.

On a cold start before eligibility is known, the pre-market poll uses the previous
session's cached eligibility, falling back to no pre-market polling. It never
guesses.

## 6. Hazards and how each is handled

**H1 — Flow-alert firing on thin pre-market prints.** UOA (`vol/OI ≥ K`) and the
premium crossover are cumulative-level tests, so pre-market volume accruing into
the day's totals is arithmetically correct and desirable. The risk is *firing* on a
handful of early prints. Handled by gating alert emission in
`handlers.run_flow_alerts` on `session_at(now) == REGULAR`, controlled by
`[alerts].fire_in_extended_hours` (default `false`). Data still accrues; only the
push is suppressed. The cooldown map is already date-scoped, so a signal that
crosses pre-market fires once at the open rather than being lost.

**H2 — `active_session_date` pivots on the collection start.** It currently flips
the gamma display from yesterday to today at `_GEX_START` (08:00 CT)
([scheduler.py:116](../../services/options_svc/scheduler.py)). Moving collection to
06:30 would silently move that flip. Handled by introducing an explicit
`session_flip` time that preserves today's 08:00 behavior, **decoupled from the
collection start**. The flip becomes a stated decision rather than a side effect.

**H3 — Expected-move time math assumes a 15:00 CT close.** `gamma_tool` computes
`hours_left` to 15:00 CT and clamps to 0.1h past it. In pre-market at 06:30 this
yields 8.5 hours to the regular close, which is correct. In the curb session it
clamps — but that is pre-existing post-close behavior, and
`compute._session_expected_move` already overrides the displayed EM with a stable
1-day calculation for exactly this reason. **No change required**; documented so it
is not "fixed" into a regression.

**H4 — Expiration-day settlement.** `paper_engine.SETTLE_HOUR_CT = 15` and
`options_calculator.EXPIRY_CLOSE_HOUR_ET = 16`. Whether eligible names trade the
16:00–16:15 ET curb *on their expiration day* determines whether these remain
correct. This could not be confirmed from Cboe's published material. **Both values
stay unchanged** pending verification — see §9. Conservative: an option settled at
the 16:00 ET close is right under today's rules and wrong by at most 15 minutes of
time value under the other reading.

**H5 — Mixed-universe rendering.** The Opportunity Board and gamma page must
distinguish "not ETH-eligible" from "stale." `eth_eligible` is added to matrix rows
and surfaced as a small badge; the gamma and collector status strips report the
current session by name (`Pre-market` / `Regular` / `Curb` / `Closed`) rather than
a bare "Collector: starts 8:30". `gex_status.MARKET_OPEN` is corrected to the real
08:00 collection start as part of the migration.

## 7. What deliberately does not change

Recording these prevents a future session from "fixing" them:

- **`paper_engine` 08:30–15:00 window** — already the observe-only posture, with a
  comment explaining why. Untouched.
- **Driver entry window 09:45–15:30 ET** — already inert in ETH. Untouched.
- **`guardrails.py`** — no new gate. Untouched.
- **Overnight GTH for `$SPX`/`$VIX`** — out of scope by decision, not oversight.
- **Settlement and expiry-close constants** — pending §9 verification.
- **The scanner's premium/liquidity scoring** — ETH marks never reach it, because
  scanning stays within the existing 08:00–15:15 operating window.

## 8. Testing

- **Pure-function coverage** on `session_at`, `extended_hours_active`,
  `in_collection_window`, and the trading-day helpers, including boundary cases at
  06:30 / 08:25 / 08:30 / 15:00 / 15:15 / 15:20.
- **Activation-gate tests**: 2026-08-14 (Fri, inert), 2026-08-16 (Sun),
  2026-08-17 (Mon, active) — asserting ETH windows report `CLOSED` before the date
  and their true session on and after it.
- **Equivalence tests for the refactor**: for each migrated consumer, assert the
  calendar-backed predicate matches the original constant-based predicate across a
  swept minute-by-minute range over a trading day, a weekend and a holiday. This is
  the acceptance bar for the refactor phase — identical output.
- **Eligibility harvest**: a fake chain carrying `ethOptionEligible` populates the
  cache; a chain missing the field degrades to `False` rather than raising.
- Service suites run **per folder** from the repo root, never `pytest services`
  across all of them (module-name collisions).

## 9. Open questions requiring verification

1. **Does the curb session run on expiration day?** Determines H4. Cboe's notice
   and FAQ pages are JS-rendered and returned no content to automated fetch;
   resolve by reading the C1 notice directly in a browser or contacting Cboe.
2. **Will Schwab accept option orders in ETH, and does the API serve option quotes
   from 07:30 ET?** Not answerable until a live session on or after activation.
   Immaterial to this design — the posture is observe-only and the app is
   paper-only — but it determines whether a future "trade ETH" phase is possible at
   all. **Verification: probe `securityStatus` and `quoteTimeInLong` on an eligible
   name at ~07:00 CT on the first activated trading day.**
3. **Confirm the 2026-08-17 date** against Cboe's final notice once published.

## 10. Rollout

1. Phase A — `shared/market_calendar.py` + `config/sessions.toml`, holidays only,
   all nine copies migrated. Behavior-preserving, shippable alone.
2. Phase B — sessions added to the calendar; all fourteen window constants
   migrated. Behavior-preserving, shippable alone.
3. Phase C — eligibility harvest + `cache:options:eth_eligible`. Additive, inert.
4. Phase D — widened pre-market collection for eligible symbols, gated on
   activation. The first phase with a live behavior change, and it is invisible
   before 2026-08-17.
5. Phase E — display and alert-gating (H1, H5).

Phases A and B pay down debt that predates this change and are worth shipping
regardless of the Cboe timeline.
