# Design — `big_delta` options-flow detector

**Date:** 2026-08-10
**Status:** design settled in shape; **thresholds PROVISIONAL** pending a three-session clean series (2026-08-11 -> 2026-08-13), then **recalibrated every trading day** (§11)
**Related:** `services/options_svc/flow_alerts.py`, `config/flow_alerts.toml`, `webgui/pages/options/flow.py`
**Instrumentation:** `tools/flow_delta_instrumentation.py`, reports under
`options-scanner/data/flow_delta_instrumentation/<date>/`

---

## 1. Why

All three existing flow detectors measure **dollars**:

| detector | measures |
|---|---|
| `crossover` | call vs put **premium** ($) lead flipping sign |
| `uoa` | contract **volume vs open interest**, gated on **premium** ($) |
| `gamma_flip` | spot crossing the dealer gamma flip **level** |

None measures **directional exposure**. A $6M premium print in a 0.05-delta wing and a $6M
print in a 0.90-delta ITM contract are the same alert today, but they move a dealer's book by
utterly different amounts. `big_delta` adds the missing axis:

```
delta_notional = |delta| × volume × 100 × spot
```

— the dollar value of underlying whose directional risk changed hands in that contract.

**The question was never "would it fire".** It was whether it fires so often it drowns the
channel. Two instrumentation runs measured exactly that across the ~92-symbol collected
universe.

## 2. What the measurement established

Full numbers in the two report files. The load-bearing results:

**A relative threshold is the right shape.** Absolute thresholds concentrate in `$SPX` on
both sessions ($100M → `$SPX` 217 of 455 alerts Friday, 228 of 603 Monday). A flat floor
reproduces exactly the bias the flat $5M premium floor already has. A threshold expressed as a
share of *each symbol's own* gross delta notional spreads across the universe — at 20%, the
loudest single symbol had **2** alerts on both days, across 35–45 symbols.

**A relative threshold alone is not viable.** This is the finding that changed the design.
At 20%-of-gross with no floor, the smallest qualifying "alerts" were:

| symbol | delta notional | premium | volume | share of gross |
|---|---:|---:|---:|---:|
| XLB | $0.1M | ~$0 | 63 | 21% |
| XLC | $0.4M | $0.06M | 41 | 24% |
| CMCSA | $0.5M | $0.01M | 270 | 20% |

63 contracts of volume clears 20% of XLB's gross only because XLB barely trades. These are
noise, and a pure ratio rule cannot tell them from signal. **The detector needs a dual gate:
relative share AND an absolute delta-notional floor.**

**0-DTE concentration is real but tolerable.** Monday's largest contracts by delta notional
are all 0-DTE with saturated delta and collapsed premium (SPY 773P: mark $0.21, delta −0.867,
986k volume → $66B). That is pin and hedging churn, not new directional risk. But 0-DTE is
**19%** of 20%-relative alerts versus **41%** of current UOA alerts — the new rule is *less*
0-DTE-skewed than the rule already shipping. No special 0-DTE handling is warranted in v1.

## 3. Two corrections to the earlier findings

Recorded because both were stated confidently and both were wrong.

**The "`$SPX` never alerts" blind spot does not exist.** Friday's run concluded `$SPX`
produced zero UOA alerts because its deep open interest keeps `vol/OI` under 3. That is not
what happened. Schwab returned **`openInterest = 0` for all 1,741 `$SPX` contracts** in that
pull, and both the live detector (`flow_alerts.py:150`) and the instrumentation replica skip
`oi <= 0`. Those contracts were structurally incapable of firing. On Monday's live intraday
pull `$SPX` has real OI (1.19M) and is the **loudest symbol at 26 alerts**. `$NDX`, MSFT and
GOOGL likewise fire. Only IWM remains at zero, legitimately (`vol/OI` 1.52).

**Friday's session is not a valid baseline and must not be averaged with Monday's.** It was
pulled on a Sunday, so:

- every `$SPX`/`$NDX` contract carried `openInterest = 0`
- the dataset contained **zero 0-DTE contracts** — they had expired off the chain
- OI for the remaining names had already settled to include Friday's own trades, deflating
  `vol/OI` throughout

That is why Friday's baseline reads 77 alerts and Monday's reads 119 under an identical rule.
**Monday is session 1 of a clean series.** Friday is retained only as weak corroboration of
the *relative-vs-absolute* shape, which is the one conclusion that does not depend on OI or
0-DTE.

## 4. Thresholds — provisional

| gate | value | basis |
|---|---|---|
| `min_share` | **0.20** of the symbol's gross delta notional | 20% held at 48 / 53 alerts and 35 / 45 symbols across two structurally different sessions — the most stable number measured |
| `min_notional` | **$25M** | the noise floor above; see the instability note below |

Combined, on Monday: **18 alerts across 16 symbols**. Against a current channel of ~119
alerts/day that is a ~15% increase in volume — comfortably additive, not a flood.

**The floor is the unstable parameter and is why a third session was commissioned.** Paired
with the 20% share, the two sessions disagree sharply:

| absolute floor | Friday alerts | Monday alerts |
|---|---:|---:|
| $0 | 48 | 53 |
| $25M | 7 | 18 |
| $50M | 2 | 13 |
| $100M | 0 | 10 |

Friday collapses to nothing by $100M while Monday holds at 10. Given §3, most of that gap is
Friday's missing 0-DTE and index volume rather than genuine session variance — but "most" is
an inference, not a measurement. **Three further like-for-like same-day 15:30 CT pulls are scheduled (2026-08-11 -> 2026-08-13),
matching Monday's methodology; the floor is fixed after reading them.**

Ship `min_share` as designed. Treat `min_notional` as the one number expected to move.

## 5. The unmeasured risk: the intraday ramp

**The instrumentation measured a single end-of-day snapshot. The detector runs every minute
from 08:00 CT.** A ratio rule's denominator is near-zero at the open: at 08:31 a symbol may
have three contracts traded, and each is trivially >20% of the symbol's gross. Nothing in
either report can show this, because both measured a full session's accumulated gross.

Untreated, this fires a burst of garbage alerts in the first minutes of every session.

Two mitigations, both cheap:

1. **The absolute floor already does most of the work** — a contract clearing $25M of delta
   notional at 08:31 is genuinely notable regardless of denominator. This is a second,
   independent reason the floor is not optional.
2. **A warm-up gate:** suppress `big_delta` for a symbol until its gross delta notional clears
   `min_gross` (proposed **$250M**, ~10× the contract floor). Expressed in the same units as
   everything else, no clock dependency, and it self-clears as the session fills in.

This is the design's highest-uncertainty area and the first thing to check against live
behaviour on day one.

## 6. Shape of the change

### 6.1 One chain walk, two independent rules

`detect_uoa` already traverses every contract of every expiry for each symbol, and `delta` is
sitting **unused in the same contract dict**. Adding a second traversal would double the hot
path for nothing.

Restructure `flow_alerts.py` into a shared walk plus two rule functions:

```python
def _walk_contracts(symbol, chain):
    """Yield normalized per-contract dicts + the symbol's gross delta notional.

    Returns (rows, gross). One traversal. Applies the sentinel guard.
    rows: {side, strike, expiry, dte, volume, oi, mark, delta,
           premium, delta_notional}
    """

def detect_uoa(symbol, chain, cfg):        # unchanged signature + behaviour
def detect_big_delta(symbol, chain, cfg):  # new
def detect_contract_alerts(symbol, chain, cfg) -> dict:
    """{'uoa': [...], 'big_delta': [...]} — walks the chain ONCE."""
```

`detect_uoa` keeps its exact public contract (it is directly unit-tested and called from the
`on_chain` hook); it simply becomes a filter over `_walk_contracts`. The poll's `on_chain`
callback in `compute.py` switches to `detect_contract_alerts` so the walk is shared, and
`stash_uoa` / `take_uoa_stash` generalize to carry both lists.

**`big_delta` must not inherit UOA's `oi > 0` gate.** Delta notional is well defined with zero
open interest — that gate exists only because UOA's ratio is undefined there. Keeping the
rules' gates independent is the whole point of the split.

### 6.2 The sentinel guard is mandatory

```python
if not isinstance(delta, (int, float)) or abs(delta) > 1.0:
    continue
```

Schwab returns **`-999.0`** as a no-value sentinel for delta on contracts it cannot price.
The instrumentation dropped **46** such contracts on Friday and **83** on Monday. Left in, they
manufacture billions in phantom exposure — Friday had AT&T showing **$18.25B** — and would
fire on every absolute threshold, every day. This is the single most important line in the
detector.

### 6.3 Dedup semantics differ from UOA — deliberately

UOA reuses the day-scoped cooldown map as a **seen-set**, justified by `vol/OI` being
monotonic: a contract crosses `k` once and stays, so alerting once is correct.

**`big_delta`'s share is not monotonic.** Volume only grows, but the denominator grows too, so
a contract can hold 25% of gross at 09:00 and 8% by the close. First crossing wins; the same
`{symbol}|big_delta|{side}|{strike}|{expiry}` key in the same day-scoped map gives that for
free. Record the reasoning — the identical mechanism is load-bearing for a *different* reason
here, and a future reader will otherwise assume the monotonicity argument carries over.

### 6.4 Alert payload

Matches the existing per-contract shape so the webgui needs no structural change:

```python
{"type": "big_delta", "side": "call" | "put", "symbol": …, "strike": …,
 "expiry": …, "dte": …, "volume": …, "delta": …, "spot": …,
 "delta_notional": …, "share": …, "gross": …}
```

`id` / `ts` / `text` are stamped by the handler drain loop exactly as UOA's are — including
`ts`, which UOA lacked until it was fixed on 2026-08-09. Do not repeat that omission.

`side` is the **contract side, not a direction claim.** Option volume is unsigned — Schwab
provides no tape — so we know exposure changed hands, not who initiated. `alert_text` follows
the module's existing "no buy/sell claim" convention:

```
NVDA 08/15 180C — BIG DELTA: $312M of delta notional (24% of today's flow)
                   · 41,200 vol · Δ0.62 · spot 182.40
```

### 6.5 Config

New block in `config/flow_alerts.toml`, mirrored in `_DEFAULTS`:

```toml
[big_delta]
enabled = true
min_share = 0.20        # >= this share of the symbol's own gross delta notional
min_notional = 25000000 # AND >= this many $ of delta notional — kills the ratio-on-a-thin-symbol artifact
min_gross = 250000000   # warm-up: symbol's gross must clear this before the rule arms
top_n = 3               # most-significant contracts per symbol per tick, by delta notional
```

Per-detector `enabled` lets it ship dark and be switched on without a deploy — worth having
given §4 and §5 are the acknowledged soft spots.

### 6.6 Webgui surface

`webgui/pages/options/flow.py` is a pure reader of `cache:options:flow_alerts` and already
renders per-type rows. Additions are mechanical:

- `big_delta` entry in the `(type, side)` → Tailwind class map
- an `alert_detail` branch rendering share + delta notional
- `big_delta` in the kind filter

No new cache key, no new command, no new service. The alert rides the existing
`cache:options:flow_alerts` list, the existing push channels, and the existing
`_FLOW_ALERTS_MAX` = 300 cap. Universe exclusions (notably `$VIX`) are already applied by the
handler's `allowed` set.

## 7. Failure modes

| risk | mitigation |
|---|---|
| `-999.0` sentinel → phantom billions | `abs(delta) > 1.0` guard (§6.2), unit-tested with a real sentinel row |
| open-of-session ratio burst | absolute floor + `min_gross` warm-up (§5) |
| 0-DTE pin churn dominating | measured at 19% of alerts vs 41% for current UOA — accepted for v1 |
| floor is wrong (sessions disagree) | `min_notional` is config, not code; session 3 fixes it |
| a detect failure breaks GEX collection | the `on_chain` hook is already `try/except`-wrapped best-effort; keep it that way |
| doubled chain traversal cost | shared single walk (§6.1) |

## 8. Testing

Pure module, so TDD is straightforward and covers the real traps:

- sentinel `-999.0` contract is dropped, and does not contribute to `gross`
- a contract below `min_notional` but above `min_share` does **not** fire (the XLB case)
- a contract above `min_notional` but below `min_share` does **not** fire
- warm-up: symbol under `min_gross` yields nothing regardless of contract size
- `oi == 0` contract can still fire `big_delta` but never `uoa`
- `detect_uoa` output is byte-identical before and after the walk restructure — a
  characterization test over a captured real chain fixture
- `detect_contract_alerts` traverses the chain exactly once (assert via a counting proxy)
- malformed chain / missing `underlyingPrice` / non-numeric delta → `[]`, never raises

## 9. Out of scope (YAGNI)

- **Signed / directional delta notional.** `signed_delta_notional` is in the instrumentation
  CSV, but with unsigned volume the sign reflects only put-vs-call composition, not
  positioning. It would read as a direction claim we cannot support.
- **Per-symbol threshold tuning.** The relative gate exists precisely to avoid a per-symbol
  table.
- **Aggregate (symbol-level) delta-notional alerting.** Per-contract first; the aggregate view
  is what the Opportunity Board already provides.
- **Backfill.** Forward-only, like every other flow detector.

## 10. Open question

**Fix `min_notional` after the clean series completes (2026-08-13).** With Monday plus
Tue/Wed/Thu there are four comparable sessions. If they cluster near Monday's shape, $25M
stands. If the median session is materially quieter, the floor drops toward $10M so the
detector is not silent on ordinary days — a detector that only fires on the busiest sessions is worse than
none, because its silence reads as "nothing happening".

Set the floor from the MEDIAN session in the series, not the mean — an alert channel is
tuned by how it behaves on a typical day, and the median is robust to a single outlier
session in either direction. Recalibration is then continuous, not one-off: see §11.

## 11. Ongoing daily calibration

The threshold is recalibrated **every trading day**, not fixed once at the end of the initial
series. Two ways to deliver that.

**Path A — manual.** Keep the 15:30 CT instrumentation running on weekdays, read the report,
edit `min_notional` in the TOML when it drifts. Zero new code. Costs a daily human read, which
is exactly the kind of chore that lapses after a fortnight — and a lapsed calibration is worse
than a fixed one, because the config then claims a freshness it does not have.

**Path B — automated (recommended, phase 2).** Invert what config holds: a **target alert
rate**, not a dollar floor. A nightly job recomputes the floor that yields that rate, publishes
it, and the detector reads it.

```toml
[big_delta]
target_alerts_per_day = 15   # what the channel should carry; the floor is DERIVED
floor_min = 5000000          # clamps so a pathological window cannot drive the floor
floor_max = 250000000        #   to zero (noise flood) or to infinity (silence)
```

Recommended because it matches what this codebase already does — a nightly recompute
(`sentiment_svc` runs the momentum cascade at 16:20 CT for the same reason: daily bars change
once a day), derived values published to a cache key, and a per-minute hot path that only
reads a number. And because **dollar levels drift with regime** while an alert rate does not:
spot levels rise, volumes shift, and a floor that was right in August is arbitrary by
December. "The channel stays useful" is a statement about alert rate, so calibrate the thing
you actually care about.

Sketch:

- **window:** trailing 20 sessions, **median** — robust to a single outlier in either direction
- **floor:** the delta-notional level at which the 20%-share rule yields
  `target_alerts_per_day` on the median session
- **cadence:** recompute nightly after the close, **never intraday** — an adaptive floor that
  moves mid-session would change the rule underneath the detector while it runs
- **publish:** `cache:options:big_delta_floor`; the detector falls back to the TOML
  `min_notional` when the key is missing or stale, so a failed calibration degrades to the
  last known-good static rule rather than to no rule

**The risk to watch: regime muting.** A sustained volatility event gradually raises the floor
and quiets alerts precisely when flow is most worth seeing. A 20-session window dilutes a
week-long event, `floor_max` caps the damage, and the job should log the floor whenever it
moves so drift is visible rather than silent. This is the failure mode to check first once it
is live.

### 11.1 Trading-day gate — DONE (2026-08-10)

`tools/flow_delta_instrumentation.py` originally had no trading-day gate: `main()` stamped
`datetime.now(CT).date()` and fetched unconditionally, so a holiday run wrote a
legitimate-looking session directory holding a near-empty distribution — which an automated
calibration window would absorb as a genuine quiet session and be dragged down by.

Fixed. `is_trading_day(d)` gates on weekday **and** the NYSE full-closure set. A non-trading
day prints why, **writes nothing**, and exits **0** — a correct skip is not a failure, and
returning non-zero would make every holiday look like a broken run in the wrapper's
`exit=` log. `--force` bypasses it for ad-hoc exploration and must not be used for a report
the calibration consumes.

The gate covers **weekends as well as holidays**, because the weekend case is the one that
already caused damage: the invalid baseline in §3 came from a Sunday pull, where open interest
had settled to include Friday's own trades and every `$SPX` contract came back with
`openInterest = 0`.

`_HOLIDAYS` is **copied** from `services/options_svc/scheduler.py`, not imported — importing
that module pulls `compute` and `handlers`, i.e. the engine chain and the Redis bus, into a
tool that touches neither. Verified equal to the service's set (20 dates, 2026–2027).
**Update both yearly**, alongside `webgui/alerts.py`.

**Related hazard, NOT fixed: there is no TIME gate.** A run before the close writes a
partial-session distribution over that date's directory, overwriting a good report with a
worse one. Harmless while the only scheduled invocation is 15:30 CT, but worth a guard before
anything else calls this script.

### 11.2 Retire the standalone fetch once the detector ships

The instrumentation fetches ~91 option chains at 15:30 to build its distribution. Once
`big_delta` is live the service already walks every chain every minute, so the same
distribution can be captured from the running detector for free. Prefer that; the standalone
script is the bootstrap, not the steady state.
