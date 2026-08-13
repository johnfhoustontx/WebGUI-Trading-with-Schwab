# NQ Dealer-Positioning HUD — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement
> this plan task-by-task. Read the companion design first:
> [2026-07-29-nq-dealer-positioning-hud-design.md](2026-07-29-nq-dealer-positioning-hud-design.md)

**Starting state:** `tools/nq_hud.py` v1.0.0 exists and is **uncommitted**. It has
**no tests in the repo** and has **never been run against live data**. A code review
(2026-07-29) found two real defects and one fabricated citation; this plan lands the
tool properly and fixes all three.

**Goal:** Pure logic extracted and tested to house standard, the two defects fixed,
`$NDX` collecting, and the whole thing verified against a live session.

**Explicitly NOT in scope:** no order placement; no webgui page; no change to
`options_svc` compute or the Gamma page; no new port or `start_all` slot. The HUD
stays a standalone Tier-1 reader.

**Architecture:** PURE math extracted to `tools/nq_signal.py` (no I/O, no tk,
TDD'd); I/O readers + CustomTkinter shell stay in `tools/nq_hud.py`.

**House rules that bind every task:** TDD (failing test first); ruff clean before
each commit; no hard-coded `D:\` paths or ports — import from `repo_paths`; every
read is defensive (degrade, never raise); comments explain *why*, not what; commit
after each green task.

**Task order matters here.** Pure extraction and its tests come first so the two
behaviour fixes (Tasks 3, 4) are written test-first against a green baseline. The
only task touching a running service ($NDX collection) is deliberately late, after
the tool is tested and correct.

---

## Task 1: Extract pure logic to `tools/nq_signal.py`

**Files:**
- New: `tools/nq_signal.py`
- Edit: `tools/nq_hud.py` (import from the new module; delete the moved copies)

> **Amendment (2026-07-29, decided during execution).** A review before starting
> found `services/options_svc/matrix.py` already provides a tested, CI-covered
> dealer-regime classifier that overlaps this task: `gex_regime`, `iv_regime`,
> `intraday_trend`, `dealer_regime`, `dealer_regime_from_rows`. `test_matrix.py`
> is in the CI matrix, and `tools/dry_run_dealer_regime.py` is a working precedent
> for a `tools/` script importing it. **Verified** `matrix.py` imports nothing but
> `__future__` and pulls in no Redis / NiceGUI / tk / pandas, so it is a safe
> dependency for a HUD that must start without the stack.
>
> So: **do not hand-roll what matrix.py already owns.** `nq_signal.py` keeps only
> what is genuinely NQ-specific. Verdict semantics are unchanged.

Move these **verbatim** — behaviour changes belong in Tasks 3–5, not here:
`ndx_scale`, `to_nq`, `near`, `stop_points`, `build_verdict`, `session_phase`,
`is_trading_day`, `tradeable`, plus the constants they close over
(`FLIP_ZONE_PCT`, `WALL_PROXIMITY_PCT`, `STOP_ATR_MULT`, `MIN_STOP_POINTS`,
`MAX_STOP_POINTS`, the RTH time constants, `HOLIDAYS`, `PHASE_NOTE`).

**`classify_regime` is the one exception — it delegates.** The flip zone stays
local (it is the HUD's main risk control and `matrix.gex_regime` has no such
band — it is a bare `spot >= flip`), but the above/below decision is single-sourced:

```python
from services.options_svc import matrix as mx   # pure, no I/O

def classify_regime(nq_spot, nq_flip):
    if nq_spot is None or nq_flip is None:
        return "unknown", None
    dist = nq_spot - nq_flip
    if abs(dist) <= nq_spot * FLIP_ZONE_PCT:
        return "flip_zone", dist          # local: the no-trade band
    reg = mx.gex_regime(nq_spot, nq_flip)  # single-sourced above/below
    return ("positive" if reg == "above" else "negative"), dist
```

Keep the `positive`/`negative`/`flip_zone`/`unknown` vocabulary at the HUD
boundary — `build_verdict` and the paint map are written against it, and matrix's
`above`/`below` means the same thing under a different name.

One structural change, because it is what makes the module pure: colour constants
stay in `nq_hud.py`, and **`build_verdict` returns a semantic `action` only** —
no `color` key. The HUD maps action→colour at paint time.

`nq_hud.py` keeps: `read_tape`, `read_gamma`, `pick_source_symbol`, `market_now`,
the `NQHud` class, `main`.

**Verify:** `tools.nq_signal` imports with no Redis, no SQLite, and no tkinter
available.

---

## Task 2: Tests for the pure logic

**Files:**
- New: `tools/tests/test_nq_signal.py`
- Edit (decision below): `.github/workflows/*.yml` — add a matrix entry

**Test location — the original choice was wrong, and the replacement needs a call.**

The plan originally put these at `tests/test_nq_signal.py` (repo root). That is the
one place to avoid: **the CI matrix has no entry for the root `tests/` directory**, so
`tests/test_repo_paths_ports.py` already sits there unrun. Don't add a second orphan.

`tools/tests/` is the natural home — but note it is *also* outside CI, and
deliberately so. `tools/tests/test_watchdog.py` says in its own docstring: *"not in
the per-service CI matrix — tools/ is a utilities dir"*, run via
`cd tools && ..\.venv\Scripts\python -m pytest tests`. That is an explicit prior
decision, not an oversight.

So there is a real choice:

* **(a) Follow the existing convention** — land in `tools/tests/`, local-only, no
  workflow edit. Consistent with the three files already there.
* **(b) Promote `tools/tests` into CI** — recommended here. This module carries a
  trading-correctness invariant (never fade a short-gamma tape) plus two defects fixed
  in Tasks 3–4; a silent regression in it costs money, which is not true of
  `test_db_admin.py`. The "utilities dir" rationale doesn't extend to signal logic.

If (b), add:

```yaml
- name: tools
  dir: "."
  cmd: python -m pytest tools/tests -q
```

**Verified this works:** `python -m pytest tools/tests -q` from the repo root passes
(36 tests, 2026-07-29). The existing files use
`sys.path.insert(0, parents[1])` rather than relying on cwd, so they are
invocation-independent and the matrix entry picks them up as a bonus. Match that
`sys.path` idiom in the new file so `import nq_signal` resolves either way.

**Cases.** These pin current behaviour; Tasks 3–5 change it test-first afterwards.

*Conversion* — `$NDX` scale is exactly 1.0; QQQ scale is the live ratio; a QQQ
strike of 560 at NDX 23000 with basis +120 lands at NQ 23120; any `None` input
returns `None` rather than a wrong number.

*Regime* — positive / negative / flip-zone boundaries at exactly ±0.30%;
`None` spot or flip yields `unknown`.

*Session* — 07:00 premarket, 08:35 opening, 09:30 morning, 12:00 pin, 14:10
afternoon, 14:56 flatten, 15:30 closed; Saturday closed; 2026-07-03 (observed
July 4th) closed.

*Stops* — clamp at 15 and 45; a 150-point session range gives 30.

*Verdict* — at the call wall in positive gamma → SHORT with stop above the wall;
at the put wall → LONG with stop below; mid-range → WAIT; negative gamma inside
the walls → WAIT; broken above → LONG with no fixed target; flip zone → STAND
DOWN; opening/flatten/closed → WAIT; missing walls never raise.

*Risk distance bounds* — **new, covers the §6 proximity/stop interaction.** For a
signal triggered anywhere inside the proximity band, assert
`abs(entry - stop)` stays within `[stop_points, WALL_PROXIMITY_PCT*spot + stop_points]`.
Testing `stop_points` alone does not cover this: the entry can sit up to ~35 NQ
points from the wall the stop is measured from.

**The invariant test that matters most:** assert that for **every** spot value
across the wall range, `build_verdict("negative", ...)` never returns a fade —
never SHORT above the call wall, never LONG below the put wall. Parametrise it;
do not spot-check.

---

## Task 3: Fix the unguarded target side

**Files:**
- Edit: `tools/nq_signal.py`
- Test: `tools/tests/test_nq_signal.py`

`build_verdict` sets `target = pin` unconditionally, so a positive-gamma SHORT at
the call wall gets a target *above* its entry whenever the pin is above spot — and
the mirror case for the put-wall LONG. See design §6.

**Failing test first**, both directions:
- positive gamma, at the call wall, `pin > nq_spot` → must not return a SHORT whose
  target is above entry;
- positive gamma, at the put wall, `pin < nq_spot` → mirror.

Then fix. Either resolution is acceptable, but pick one and say why in a comment:
require the pin on the profitable side and fall through to `WAIT`, or clamp the
target to the flip. Prefer `WAIT` — a fade with no valid mean-reversion target is
not a setup, and silently substituting a different level changes what the signal
means.

Keep the negative-gamma branch untouched (it already returns `target=None`), and
re-run the Task 2 invariant test.

---

## Task 4: Stop re-decoding the whole session's grids

**Files:**
- Edit: `tools/nq_hud.py` (`read_gamma`)

`read_gamma` calls `load_date_with_grid(conn, symbol, "gex", today)` and uses only
`rows[-1]`'s grid plus the `spot` column from every other row. `_decode_grid`
zlib-decompresses and JSON-parses **all** ~390 of them — at a 2 s refresh, ~195 grid
decodes/second. The root `CLAUDE.md` (2026-07-18) records this exact pattern being
removed from `gamma_snapshot` once already, at 1-minute cadence. See design §2.

Split the read:
- **spot series** (for `atr_proxy`) — the grid-free query. `load_today` returns
  `(ts, spot, flip, top_pos_strike, top_neg_strike, net_total)` with no `gex_json`.
- **newest grid only** (for walls + pin) — either a single-row grid read, or
  `load_date_with_grid(..., since_ts=<last seen ts>)` memoised per
  `(symbol, view, date)`, which is what `compute.py` does.

Note `latest_spot_flip(conn, symbol, "gex", date)` gives `(ts, spot, flip)` in one
cheap query if that shape fits better.

This is an internal refactor with no output change. **Verify by equivalence:** for a
fixed session, the new path must produce byte-identical `spot`, `flip`, `call_wall`,
`put_wall`, `pin`, `atr_proxy` to the old one. Note the measured before/after decode
count in the commit message.

**Careful:** `_decode_grid` returns **float** strike keys. Any new read path must
preserve that — `get_directional_walls` compares `s > spot` and the pin takes
`max()` over keys, and both silently misbehave on strings.

---

## Task 5: Hygiene — ruff, imports, deps, and the one non-defensive lookup

**Files:**
- Edit: `tools/nq_hud.py`, `requirements.txt`

Close the `_paint` exception noted in design §7: `rmap[st["regime"]]` is a bare
subscript on the UI thread. Make it a `.get(...)` with an unknown-regime default.
Safe today, but it is the only non-defensive lookup in a component whose stated
contract is that there are none.

Run `ruff check tools/nq_hud.py tools/nq_signal.py tools/tests/test_nq_signal.py`.

Confirm the `sys.path` insertion pattern matches `webgui/proxy.py` and
`shared/bus/client.py` (repo root first, then `OPTIONS_SCANNER` from `repo_paths`).
The `gamma_tool` / `gex_history_db` imports are deliberately lazy inside
`read_gamma` so an import failure degrades the gamma panel instead of preventing
the window from opening — **keep them lazy.**

Add `customtkinter` to `requirements.txt` (confirmed absent) and to
`requirements.lock` per the repo's pinning convention.

---

## Task 6: Collect `$NDX`

**Files:**
- Edit: `options-scanner/gex_collector.py`

```python
SYMBOLS = ["$SPX", "$VIX", "SPY", "QQQ", "$NDX"]
```

> **Amendment (2026-07-29, measured during execution). The premise was wrong, but
> the task still stands — for the opposite reason.**
>
> `$NDX` was *already being collected*, and the HUD *already selected it*: 440
> `$NDX` gex rows for 2026-07-29 in the live DB, `read_gamma()` returning
> `symbol='$NDX'`. So the poll-cost question below is moot — `$NDX` was in every
> poll already, and adding it to `SYMBOLS` fetches nothing new. Verified:
> `collection_symbols()` is byte-identical before and after (82 symbols).
>
> It was collected only because it happens to sit in `Top 20.xlsx`, which is
> **gitignored**. Simulate that file's absence and the universe collapses to the
> four base symbols with no `$NDX` — the HUD then silently falls back to the QQQ
> proxy, whose call-overwriting flow can invert the gamma sign. **That silent
> degradation is the reason to make the edit**, not the throughput the plan
> worried about. Zero cost, removes a failure mode, and puts the correct
> underlying for an NQ tool in the guaranteed base where it belongs.
>
> Consequence: **no `options_svc` restart is required for the HUD to see `$NDX`** —
> it already does. Restart only so the base list takes effect for a future run
> without the watchlist.

This is the only task that touches a running service, and it is late on purpose:
by here the HUD is tested and correct, so a source-symbol switch changes one input
rather than compounding with unknown logic.

~~**Measure the poll cost before committing, out of band.**~~ Moot — see the
amendment. Retained for the record: `poll_once` fetches concurrently
(`POLL_FETCH_WORKERS = 6`, confirmed) and the proxy spaces upstream calls ~0.2 s,
and `$NDX` is a wide chain on which Schwab has 502'd before
(`protocol.http.TooBigBody` — see the `TERM_DTE_HORIZON_DAYS` comment in
`gex_collector.py`). None of that bites, because the fetch was already happening.

**Verify:**
1. `collection_symbols()` contains `$NDX` **with the watchlist absent** — the
   actual point of the change.
2. The universe is unchanged with the watchlist present (no duplicate, no new
   fetch).
3. `tests/test_gex_collector.py` shows no regression **against a measured
   baseline** — that file is in the documented timing-dependent set, so compare
   the failing SET, not a remembered count.

---

## Task 7: Live verification

Run with the stack up, during RTH:

```powershell
.\.venv\Scripts\Activate.ps1
python tools\nq_hud.py
```

Check, in order:

1. Header shows the source symbol; amber if QQQ, neutral if `$NDX`.
2. Snapshot age ticks and stays under ~60 s while `options_svc` is running.
3. Basis is plausible — small positive, single-to-low-double-digit points — and
   moves with the tape rather than sitting frozen.
4. Converted levels bracket NQ spot sensibly. Cross-check the flip against the
   `/options/gamma` page with the same symbol selected: they should agree after
   basis adjustment.
5. Kill `options_svc` for three minutes — the health line must turn red and name
   the staleness, and the verdict must not keep asserting a stale regime.
6. **CPU is flat.** After Task 4 the poll should be cheap; watch the process for a
   few minutes and confirm it is not burning a core on grid decodes.

---

## Task 8: Signal logging (required before sizing up)

**Files:**
- New: `tools/nq_signal_log.py` (append-only CSV or SQLite under
  `options-scanner/data/` — follow `repo_paths` for the location)
- Edit: `tools/nq_hud.py` to write one row per verdict *transition* (not per poll)

Per transition record: timestamp, source symbol, regime, action, NQ spot, flip,
both walls, basis, stop, snapshot age — **and both pin candidates**: `max(|net|)`
(what the HUD uses) and the stored `top_pos_strike`.

Logging both is what settles the open question in design §6. `top_pos_strike` is
`max(net)` over positive-net strikes only and is stored per snapshot (free, no grid
decode); `max(|net|)` can land on a strongly negative-net strike, which is an
amplifier rather than an attractor and is arguably the wrong mean-reversion target.
Compare which one price actually gravitates to.

> **Shipped 2026-07-29.** `tools/nq_signal_log.py` — append-only CSV at
> `options-scanner/data/nq_signals.csv` (gitignored), one row per **(regime, action)**
> change rather than per poll, 23 columns including both pin candidates in NQ points.
> CSV over SQLite deliberately: single writer, append-only, never read in a request
> path, and the consumer is an offline pandas pass — no schema, no migration, and the
> simplest possible write path for something that must never break the HUD. 25 tests.
>
> **First live sample already suggests an answer** (design §6): `pin_top_pos_nq`
> came out *equal to* `call_wall_nq`, which looks structural. **First analysis to
> run** on the accumulated log is therefore `pin_top_pos_nq == call_wall_nq` — if it
> holds broadly, the pin question closes in favour of the current `max(|net|)`.

Then log 20–30 sessions before increasing size, and evaluate **regime
classification accuracy first**. If the regime call is under ~60%, the entry rules
are not the problem and tuning them wastes time — fix the source symbol
(`$NDX`, then a mega-cap gamma overlay) instead.

---

## Follow-ups, not in this plan

- **Prior-session ATR seeding** — the range proxy is the session range *so far*, so
  the stop clamps to its 15-point floor early in the day, tightest exactly when the
  tape is most volatile (design §6). Changes sizing, so decide it against Task 8's
  logged data.
- **Confirmation layer** — ES/NQ divergence, mega-cap tape, Nasdaq breadth. Highest
  value next addition: gamma alone is insufficient for NQ given the flow
  fragmentation in design §4.
- **Mega-cap gamma overlay** — index-weighted dollar gamma across the top 8 NDX
  names. This is the piece that would close the largest gap between the HUD's map
  and the real dealer position.
- **`$VXN` tile** in `market_svc.symbols.SYMBOL_MAP`, replacing the `$VIX` proxy.
- **`/nq` NiceGUI page** reading `tools/nq_signal` for parity in the browser.
- **`WALL_PROXIMITY_PCT` calibration** — 0.0015 is ~35 NQ points at 23,000, a wide
  reading of "at the wall". Tighten it against logged fill quality.
