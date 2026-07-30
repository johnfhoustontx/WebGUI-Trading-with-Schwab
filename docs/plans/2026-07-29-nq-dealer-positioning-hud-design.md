# NQ Dealer-Positioning Entry HUD — design

> **Status:** v1.0.0 exists as `tools/nq_hud.py` (2026-07-29, uncommitted) and has
> **not** been run against live data. Two defects found in review are not yet
> fixed — see §6 (pin side) and §2 (read cost).
> Companion plan: [2026-07-29-nq-dealer-positioning-hud-plan.md](2026-07-29-nq-dealer-positioning-hud-plan.md)

## 1. What this is

An always-on-top desktop HUD for **manual, RTH-only NQ futures day trading**. It
classifies the dealer-gamma regime from options data the stack already collects,
converts the key levels into NQ futures points, and renders a
**LONG / SHORT / STAND DOWN** verdict with stop and target levels.

It is a decision aid for a human trading in NinjaTrader — not an automated
strategy, not an order router. It places no orders and has no broker connection.

**Why a desktop window rather than a webgui page:** it sits beside a NinjaTrader
chart during the session. A browser tab is the wrong ergonomics for a HUD that
must stay visible over a full-screen charting app. A `/nq` NiceGUI page remains a
reasonable future addition (see §8) but is not what the user asked for.

## 2. Tier placement — this is a Tier-1 reader

Per the 3-tier architecture, Tier 1 renders and does not compute. The HUD honours
that strictly:

| Datum | Source | Mechanism |
|---|---|---|
| NQ / NDX / VIX spot + day % | `cache:market:dashboard` | `shared.bus.Bus` |
| GEX grid, flip, spot, session range | `options-scanner/gex_history.db` | read-only SQLite |
| Call / put walls | `gamma_tool.get_directional_walls` | pure function, no I/O |

It makes **no Schwab calls**, opens no port, writes nothing anywhere, and edits no
existing file. The history DB connection is opened read-only
(`gex_history_db.connect(read_only=True)`, SQLite URI `mode=ro`) and closed every
poll so the collector's write lock is never contended. It needs no entry in
`config/ports.toml` and no `start_all` slot.

`market_svc` already quotes `/NQU26` and `$NDX` on its ~2 s RTH poll
(`services/market_svc/symbols.py`), so the live basis is free. The dashboard tile
`display` strings the HUD matches on are `/NQ[U26]`, `NDX`, `VIX` — `symbols._q`
sets `display = csv_symbol`, so these are the CSV names, not the quote symbols.

### Read cost — being read-only is not the same as being cheap

Read-only access means the HUD cannot *corrupt* the stack. It says nothing about
CPU, and the naive read is expensive enough to matter.

`load_date_with_grid()` decodes **every** row's grid: `_decode_grid` zlib-decompresses
and JSON-parses each one (~114 strikes per grid, ~390 rows by the close). The HUD
needs the grid of exactly **one** row — the newest — plus the `spot` column from the
rest for the session-range stop. Decoding all of them at a 2 s refresh is ~195 grid
decodes per second sustained.

This is a hotspot the repo has already paid for once: the root `CLAUDE.md`
(2026-07-18 entry) records `gamma_snapshot`'s *"whole-session grid re-decode every
minute"* being replaced with an incremental per-`(symbol, view, date)` memo. The HUD
must not reintroduce it at 30× the frequency.

The primitives to avoid it already exist:

* `load_today()` / the same date range **without** `gex_json` — for the spot series
  that feeds the ATR stop.
* `load_date_with_grid(..., since_ts=…)` — returns only rows strictly newer than
  `since_ts`, the memo pattern `compute.py` uses.

Fixed in plan Task 4.

## 3. Why NOT `cache:options:gamma`

The obvious source is wrong and dangerously so.

`handlers.refresh_gamma(bus, symbol="$SPX")` writes **one symbol at a time** —
whichever symbol the `/options/gamma` page currently has selected
(`handlers._current_gamma_symbol`, defaulting to `$SPX`). A HUD reading that key
would show SPX gamma under an NQ label whenever the user navigated the Gamma page
elsewhere. Silent, plausible, and wrong is the worst failure mode for a trading
signal.

`gex_history.db` is per-symbol by construction: `options_svc` collects 1-minute
GEX/Charm/DEX/Vanna snapshots for the whole collection universe, 08:00–15:20 CT,
with the full per-strike grid. `load_date_with_grid()` returns exactly what the
wall picker needs, and `_decode_grid` hands back **float** strike keys — which the
wall picker's `s > spot` comparison and the pin's `max()` both require.

## 4. Source symbol — and its known weakness

`SOURCE_PREFERENCE = ("$NDX", "QQQ")`. First symbol with a GEX row today wins;
off-hours it walks back up to 5 calendar days to find the last collected session
(enough to clear any weekend-plus-holiday closure).

**Correction (2026-07-29, measured during execution).** An earlier revision of this
section claimed `$NDX` "is not currently collected" and that "today the HUD runs on
QQQ". Both were wrong. `$NDX` *was* being collected and the HUD *does* select it —
verified against the live 1.33 GB history DB: 440 `$NDX` gex rows for 2026-07-29,
`read_gamma()` returning `symbol='$NDX'` with spot 27192.31 / flip 27190.0 / walls
27175–27510.

But it was being collected for a **fragile reason**: not from
`gex_collector.SYMBOLS` (the guaranteed index base, which was
`["$SPX", "$VIX", "SPY", "QQQ"]`) but incidentally, because `$NDX` happens to sit in
`options-scanner/data/Top 20.xlsx` — a **gitignored** file. Measured: with the
watchlist present the universe is 82 symbols including `$NDX`; simulate its absence
(a fresh clone, or simply deleting that row) and the universe collapses to the four
base symbols with **no `$NDX`**, at which point the HUD silently falls back to QQQ.

That silent-degradation path is the real problem, so `$NDX` moved into the base:

```python
SYMBOLS = ["$SPX", "$VIX", "SPY", "QQQ", "$NDX"]
```

**This costs nothing.** `collection_symbols()` dedupes, so on a machine with the
watchlist the universe is byte-identical before and after (82 symbols, verified) and
no additional chain is fetched — the poll-budget concern the plan raised does not
apply, because `$NDX` was already in every poll.

QQQ remains the fallback and the amber header warning stays, because the fallback is
now genuinely reachable only when something is wrong. It matters when it fires: QQQ
carries heavy structural call-overwriting flow (buy-write funds, collar programs),
which can **invert the apparent gamma sign** relative to the real NDX dealer
position.

Fragmentation is a residual limitation regardless: NDX-referencing flow spreads
across NDX, QQQ, XND and NQ futures options, and mega-cap single-name gamma
(~half the index) never appears in any index chain. Expect a materially lower hit
rate than the SPX 0DTE work. This is a real property of the instrument, not a bug.

## 5. Level conversion — measured, never assumed

```
source strike  --×scale-->  NDX-equivalent  --+basis-->  NQ points
```

* `scale` = `1.0` for `$NDX`; the live `NDX / QQQ` ratio (~41) otherwise,
  **recomputed every poll** because it drifts.
* `basis` = live `NQ − NDX`, i.e. carry minus dividend yield. Recomputed every
  poll, so the quarterly roll discontinuity handles itself. Nothing is hard-coded.

Any missing input returns `None` and the row paints `—` rather than a wrong number.

The session-range ATR proxy is scaled but **not** basis-adjusted — it is a
difference, not a level, and basis cancels.

## 6. Regime and verdict rules

Regime, from NQ spot vs the NQ-converted flip:

| Regime | Condition | Dealer behaviour | Play |
|---|---|---|---|
| **Positive gamma** | spot > flip + 0.30% | long gamma; sell rallies, buy dips | mean-revert: fade walls toward the pin |
| **Negative gamma** | spot < flip − 0.30% | short gamma; hedge with the move | continuation on a wall break only |
| **Flip zone** | within ±0.30% of flip | whipsaw | **STAND DOWN** |

The flip zone is a first-class state, not a gap between the other two. Most losses
come from trading it, so refusing is an explicit decision the HUD makes visible.

**Invariant: the HUD never emits a fade in a negative-gamma regime.** Guarded by a
parametrised test, not a spot check.

Levels drawn: gamma flip, call wall (largest call GEX above spot), put wall
(most-negative put GEX below spot), and the pin.

### The pin — definition, and an open question

The pin is currently the largest **absolute net-gamma** strike, recomputed from each
snapshot's grid rather than read from the stored `top_pos_strike` column.

The two are genuinely different metrics, which is why they cannot be mixed:

* `top_pos_strike` (`GammaEngine.snapshot_summary`) is `max(net)` restricted to
  strikes where `net > 0` — it can **only** ever be a positive-net-gamma strike.
* `max(|net|)` can land on a strongly **negative**-net strike, which on a put-heavy
  session is common.

**Open question — and it favours the stored column.** Pinning is caused by *positive*
dealer gamma: dealers long gamma sell rallies and buy dips around that strike. A
strike with large *negative* net gamma is the opposite — an amplifier, not an
attractor. So as a mean-reversion target in a positive-gamma regime, `max(net)` over
positive strikes is the more theoretically defensible choice, and that is exactly
`top_pos_strike` — which is stored per snapshot and therefore **free**, needing no
grid decode at all.

Resolve this with logged data (plan Task 8), not argument: record both candidates per
verdict and compare which the tape actually gravitates to. Until then the
implementation keeps `max(|net|)` and this question stays open in writing rather than
being settled silently. Note that the perf fix in §2 leaves one grid decode in place
regardless (the walls need it), so keeping `max(|net|)` costs nothing extra today.

**Early evidence (n=1, 2026-07-29) — it leans toward the CURRENT choice.** The first
live sample after wiring the log showed the two candidates 310 points apart, and
`top_pos_strike` landing *exactly on the call wall*:

```
pin  max(|net|)     = 27200.0        call_wall_nq   = 27629.0
pin  top_pos_strike = 27510.0  ->NQ  27629.0        <- the same level
```

That looks structural rather than coincidental: `top_pos_strike` is the largest
*positive*-net strike, and above spot calls dominate so net is positive there — the
largest such strike will usually be the call wall itself. If that holds, using it as
the fade target is **degenerate for the call-wall setup**: the trade would target the
very level it is shorting from, i.e. zero expected move, and the Task 3 target-side
guard would send it to WAIT every time.

So the theoretical argument above (pinning comes from positive gamma) is real but
appears to be outweighed by a mechanical one. **This is one snapshot — do not act on
it.** Check whether `pin_top_pos_nq == call_wall_nq` across the logged sessions; if
it holds broadly, the question is settled in favour of `max(|net|)` and this section
can be closed.

> An earlier revision of this document justified rejecting `top_pos_strike` by citing
> a disagreement measured on "383 of 383 rows" of a live session, attributed to a
> comment in `services/options_svc/compute.py`. **No such measurement or comment
> exists.** The claim was fabricated and has been removed. The wrapping idiom
> `get_directional_walls({"gex": grid}, spot)` *is* real — see `compute.py:1427`,
> which does exactly that for the DEX view — but the function once cited as
> `compute.py:_level_track` does not exist either.

### The target must be on the profitable side — currently unguarded

`build_verdict` sets `target = pin` unconditionally. Nothing constrains the pin to
lie between the entry and the direction of the trade, so:

* positive gamma at the **call wall** → `SHORT`, and if the pin is *above* spot the
  short's target sits above its entry;
* positive gamma at the **put wall** → `LONG`, mirror-inverted.

Both are nonsense a trader would have to catch by eye. The fix is to require the pin
on the profitable side and otherwise degrade — either to `WAIT` or to a target
clamped at the flip. Fixed test-first in plan Task 3.

### Proximity band and stop distance interact

`WALL_PROXIMITY_PCT = 0.0015` is **~35 NQ points at 23,000** — a wide reading of "at
the wall". Since `stop = wall ± stop_points`, the realised entry→stop distance varies
from roughly 0 to `35 + stop_points` depending on where inside the band the signal
triggers. Testing `stop_points` in isolation does not cover this; the plan asserts
bounds on the resulting risk distance instead.

### Stops

Stops are **ATR-scaled**, derived from the session's realized spot range, clamped to
15–45 NQ points. NQ needs more room than ES and a fixed tick stop is wrong. Risk
shown in points and dollars at both 1 NQ ($20/pt) and 1 MNQ ($2/pt).

**Known asymmetry:** the ATR proxy is the session range *so far*, so at 08:45 it is
near zero and the stop clamps to the 15-point floor — tightest exactly when the tape
is most volatile, inverting the intent of scaling it. Seeding from the prior session's
range is the fix; deferred as a follow-up because it changes sizing behaviour and
should be decided against logged data.

### Session gating

CT: nothing before **08:45** (opening rotation, overnight hedges unwinding), nothing
after **14:55** (day-trade only). `11:30–14:00` is flagged as the strongest pinning
window. `HOLIDAYS` is a verbatim copy of `services/options_svc/scheduler._HOLIDAYS`
and must be updated in lockstep with it each year.

## 7. Failure behaviour

Every read degrades rather than raises. Missing tape, missing gamma, engine import
failure, and a stalled collector each paint a red health line naming the specific
problem; the verdict falls to `STAND DOWN` / `WAIT` with the reason stated.
Snapshot age is always on screen, with `STALE_AFTER_SEC = 150` (the collector is
1-minute, so >150 s means it has stalled).

**One exception, to be closed:** `_paint` does a bare `rmap[st["regime"]]` subscript.
`classify_regime` only emits the four keys that map contains, so it is safe today —
but it is the single non-defensive lookup in a component whose contract is that there
are none, and it would `KeyError` on the UI thread if a fifth regime were ever added.
Fixed in plan Task 5.

## 8. Explicitly out of scope for v1

- Order placement or any broker interaction.
- Changes to any existing file (the `$NDX` collector line is a separate,
  independently promotable change).
- The confirmation layer from the strategy — ES/NQ divergence, mega-cap tape
  (NVDA/AAPL/MSFT), Nasdaq breadth. These are the highest-value next addition,
  since gamma alone is insufficient for NQ given §4.
- VXN. `$VXN` is not in `market_svc.symbols.SYMBOL_MAP`; the HUD shows `$VIX` as a
  proxy, which is correlated but not the Nasdaq vol measure.
- Signal logging for validation. Needed before sizing up, and needed to settle the
  pin question in §6 (see the plan, Task 8).
- A `/nq` NiceGUI page reading the same logic.
- Prior-session seeding of the ATR proxy (§6).
