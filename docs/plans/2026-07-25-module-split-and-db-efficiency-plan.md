# Module splitting + database efficiency — findings & remediation plan

**Date:** 2026-07-25
**Branch:** `Using_Highcharts`
**Status:** PLAN ONLY — no code changes made. Every number below is measured, not estimated.

---

## 0. Blocker — read first

A concurrent session has **uncommitted WIP on exactly the files this plan touches**:

```
 M options-scanner/gex_collector.py        <- DB write path
 M options-scanner/gex_history_db.py       <- DB layer (Part 2 targets this)
 M services/options_svc/compute.py         <- 6,243-line split target (Part 1)
 M services/options_svc/matrix.py
 M services/market_svc/{compute,symbols}.py
```

plus a second worktree at `.worktrees/config-consolidation`.

This exact overlap has silently broken `cache:options:matrix` before (see
`concurrent-session-staging-hazard` memory). **Nothing in Part 1 §B or Part 2 should start
until that WIP is landed or parked.** Part 1 §A (`gamma_tool.py`) is clear of it and can
proceed independently.

---

## 1. Consumer verification (requested before the storage decision)

### Who reads the `gex_json` grid blobs?

| Consumer | Location | Status |
|---|---|---|
| `_history_rows_incremental` → `gamma_snapshot` | `services/options_svc/compute.py:1619` | **The only live consumer** |
| `GammaWindow._render_heat` | `options-scanner/gamma_tool.py:5777` | Dead — inside the Tk class (line > 4068) |
| `GammaWindow._build_analysis_data` | `options-scanner/gamma_tool.py:6796` | Dead — inside the Tk class |
| `fix_gex_history_scale.py` | `options-scanner/scripts/` | One-off historical migration |

**Conclusion: one live reader, and it feeds the Gamma heatmap only.**

### What that reader needs

`load_date_with_grid` returns `(ts, spot, flip, top_pos_strike, top_neg_strike, net_total, grid)`.
Critically, **`flip` and the wall strikes are stored COLUMNS**, computed at collection time from
the full chain. They are independent of the grid blob — so grid compaction cannot affect
flip/wall correctness. Only the heatmap image depends on the blob.

### Why an insert-time crop is unsafe

`_crop_gamma_views` (compute.py:1469) already crops for the cache, but it crops **at read time**
around the *current* spot **widened to span the intraday spot path** (`min→max` of history-row
spots). That widening exists specifically because a fixed band around the final spot clips
strikes that mattered earlier in the session.

An insert-time crop cannot reproduce this: **at write time you do not know the session's eventual
spot range.** Measured over all 239 symbol-sessions in the retained data:

| Crop window | Payload | Sessions with heatmap holes |
|---|---|---|
| ±20 strikes | 245 MB (75% saving) | 7 of 239 — including **all 5 $NDX sessions** |
| ±40 strikes | 339 MB (65% saving) | still holes $NDX (42.4, 58.6, 71.8) |
| ±60 strikes | 385 MB (60% saving) | still holes $NDX (71.8) |
| **round only, no crop** | **459 MB (52% saving)** | **none — structurally impossible** |

`$NDX` at ~28,000 with 10-wide strikes turns a 2% day into a 56-strike drift. No fixed
strike-count window is safe for it. Worse, the holes land on the **highest-movement sessions** —
exactly the ones worth reviewing.

> **Correction:** an earlier 6-symbol sample reported "max drift 24 strikes, ±40 safe". That
> sample excluded `$NDX` and was wrong. The 239-session figure above supersedes it.

### Recommendation

**Take rounding (52%). Do not crop at insert.** Rounding is a serialization-level change with no
windowing semantics to get wrong, no schema migration, and no dependence on future volatility.
If more is wanted later, a **percent-of-spot** window (e.g. ±3%) adapts per instrument where a
strike-count window cannot — but it is a second, separately-verified step, not part of this one.

---

## 2. Part 1 — Module splitting

**Framing:** splitting a file does not by itself make anything faster. File length is a
maintainability metric; the *import graph* is the speed metric. §A is worth doing because it is
both. §B is maintainability only — it should not be sold as a performance win.

### §A — `options-scanner/gamma_tool.py` (7,203 lines) — the high-value one

```
lines 20-45    import tkinter, ttk, colorchooser, matplotlib,
               FigureCanvasTkAgg, PIL              <- MODULE LEVEL
line  4068     class GammaWindow(tk.Toplevel):     <- runs to EOF, ~3,135 lines (43%)
```

The Tk desktop UI was dropped from this project, but its window class still lives in the engine
module with its toolkit imports at module scope.

**Measured cost:** `import gamma_tool` = **0.69 s**, pulling `tkinter`, `matplotlib`, `PIL`
into every headless service that touches it — `options_svc/compute.py` (~10 lazy import sites),
`gex_collector.py`, `scanner_engine.py`, `tools/gex_term_one_shot.py`. This is also the likely
source of the "intermittent tkinter dashboard crash" that blocks the full options-scanner suite.

**Entanglement — must be resolved first.** Two pure functions call back into the GUI class:

- `compute_projected_flip` (line 2840) → `GammaWindow._calc_flip_point(gex, spot)`
- `build_analysis_dict` (lines 6868, 6874) → `GammaWindow._fetch_symbol_analysis_impl(...)`

**Steps:**

1. Lift `_calc_flip_point` and `_fetch_symbol_analysis_impl` out of `GammaWindow` into
   module-level pure functions. Update the 3 call sites. **Test gate:** full options-scanner
   suite unchanged (modulo the 2 documented date-relative baseline fails).
2. Move `class GammaWindow` to `options-scanner/legacy/gamma_window.py` (or delete — it has no
   live entry point; `start_all` launches nothing that constructs it).
3. Remove the now-unused module-level `tkinter` / `matplotlib` / `PIL` imports. Keep the
   function-local matplotlib imports used by the surviving chart helpers
   (`draw_term_heatmap`, `build_chart_style_vars`) — or move those to the legacy module too if
   nothing live calls them (verify first).
4. **Verify:** re-time `import gamma_tool`; assert `tkinter not in sys.modules`. Add a
   regression test pinning that, mirroring the existing
   `test_no_module_shadows_stdlib` discipline.

**Expected result:** ~7,200 → ~4,050 lines, 0.69 s → well under 0.2 s, no GUI toolkit in any
service process, and the options-scanner suite plausibly unblocked.

### §B — `services/options_svc/compute.py` (6,243 lines, 166 top-level defs)

Maintainability only. The file already carries its own seam markers as comment banners, so this
is a mechanical move, not a redesign:

| Proposed module | Approx. source span |
|---|---|
| `compute/scan.py` (scan + swing) | 210– |
| `compute/paper.py` | 342– |
| `compute/trade.py` | 799– |
| `compute/captured.py` | 1080– |
| `compute/gamma.py` | 1337– |
| `compute/matrix.py` | 2053– |
| `compute/briefings.py` (Claude/EOD/news) | 2741– |
| `compute/calculator.py` | 4328– |
| `compute/simulator.py` | 4526– |
| `compute/rescue_eod.py` | 5103– |

**Constraints:**
- Keep `compute.py` as a package `__init__` re-exporting the existing public names, so
  `handlers.py` and the ~40 test modules need **zero** changes. This makes the split reviewable
  as a pure move.
- **Preserve every lazy-import comment and its placement.** Those banners
  (`# LAZY IMPORTS (IMPORTANT)`) are load-bearing — they document the cross-app module-collision
  discipline (`scoring`, `notifier`, `config`, `src`). Hoisting any of them to module scope
  re-introduces a documented class of bug.
- One module per commit, suite green between each.

### §C — Runners-up (defer)

`handlers.py` (1,527), `sentiment_svc/compute.py` (1,483), `webgui/main.py` (1,298),
`rescue.py` (1,215). All are coherent single-domain modules at a defensible size. Splitting them
is churn with no import-graph payoff. Revisit only if one crosses ~2,000 lines.

---

## 3. Part 2 — Database efficiency

`options-scanner/gex_history.db` = **2.21 GB holding 5 sessions**. Retention *works*
(`purge_keep_sessions(keep=5)`); the storage is simply inefficient.

Current state: `page_size=4096`, `page_count=539,058`, `freelist_count=194,623`,
`auto_vacuum=2 (INCREMENTAL)`, `journal_mode=wal`.
`snapshots` = 413,164 rows; `gex_term_snapshots` = 555,299 rows.

### D1 — Reclaim free pages *(no code change, biggest immediate win)*

**36% of the file — 194,623 pages = 797 MB — is already free space.** `auto_vacuum=INCREMENTAL`
is enabled on the live DB, but `PRAGMA incremental_vacuum` is **never called anywhere in the
repo**. The only reclaim path is `tools/vacuum_gex.py`, which runs a full `VACUUM` and
(correctly) refuses to run during market hours.

- Add a bounded `PRAGMA incremental_vacuum(N)` step to the existing daily purge, capped to a few
  thousand pages per call so it never holds a long lock.
- **Run off-hours.** Verify `freelist_count` before/after.
- Reclaims ~797 MB with no schema change and no data loss.

### D2 — Delete the stale backup *(trivial)*

`options-scanner/gex_history.db.bak` = **3.04 GB**, dated Jul 1. Confirm it is not referenced,
then remove. Largest single disk win in the repo.

### D3 — Round grid floats at serialization *(the recommended storage change)*

Grid payload is 966 MB across 413k rows. The dominant cost is **float entropy, not strike count** —
all four views average ~114 strikes, but per-snapshot bytes are gex 1,110 / dex 1,426 /
charm 1,820 / vanna 2,172. Vanna stores long values like `1.2345678901234e-05`.

Rounding to 6 significant figures before `zlib` in `_encode_grid`:

| view | now | rounded |
|---|---|---|
| gex | 1,110 B | 802 B |
| charm | 1,820 B | 770 B |
| dex | 1,426 B | 1,092 B |
| vanna | 2,172 B | **439 B** |

**966 MB → 459 MB (52%).** Six significant figures is far more precision than dollar-denominated
GEX display needs.

- Change is confined to `_encode_grid` in `gex_history_db.py`. `_decode_grid` needs no change.
- **Forward-only** — existing rows still decode; no migration, no lock, fully reversible.
- **Test gate:** assert a round-tripped grid matches the original to 6 s.f., and that
  `gamma_snapshot`'s flip/walls are byte-identical (they read stored columns, not the grid).

### D4 — `gex_term_snapshots` (555k rows) *(investigate before acting)*

`PRIMARY KEY (timestamp_ct, symbol, expiration_date, strike)` on a **TEXT** timestamp, plus an
expression index `idx_term_date ON substr(timestamp_ct, 1, 10)`. The wide TEXT PK is duplicated
into the autoindex, and purge does a non-sargable `substr()` scan. Options: store the timestamp
as INTEGER epoch (matching `snapshots.ts`) and drop the expression index in favour of a range
scan. **Measure the actual index/table split first** — `dbstat` is not compiled into this SQLite
build, so size it by row arithmetic or an offline copy before committing to a migration.

### D5 — Connection churn *(low priority, real)*

`options-scanner/paper_trader.py` opens a fresh connection per operation at 8+ sites
(lines 221, 231, 245, 257, 271, 287, 302, 334); `sentiment_svc/compute.py:1450` likewise. The
services already have the right pattern elsewhere (`handlers.py` module-level lazily-created
handles). Apply that pattern here. Small per-call cost, but it is on the paper-manage path.

---

## 4. Sequencing

| # | Step | Risk | Payoff | Blocked by other session? |
|---|---|---|---|---|
| 1 | D2 — delete 3 GB `.bak` | none | 3.04 GB | no |
| 2 | §A — extract 2 helpers, drop `GammaWindow` + toolkit imports | low | 0.69 s + no Tk in services; −3,135 lines | no |
| 3 | D1 — incremental vacuum in the daily purge | low (off-hours) | 797 MB | **yes** |
| 4 | D3 — round floats in `_encode_grid` | low, forward-only | 507 MB over 5 sessions | **yes** |
| 5 | D5 — connection reuse | low | minor | partly |
| 6 | §B — split `compute.py` into a package | medium (large diff) | maintainability only | **yes** |
| 7 | D4 — term-table schema | medium (migration) | TBD — measure first | **yes** |

**Net storage:** 2.21 GB + 3.04 GB `.bak` → roughly **0.9 GB**, without losing a single session
of history or narrowing what is queryable.

## 5. Explicitly not recommended

- **Insert-time strike cropping** at any fixed window — §1 shows it holes the heatmap on the
  highest-movement sessions, and `$NDX` breaks even ±60.
- **Splitting `handlers.py` / `sentiment_svc/compute.py` / `webgui/main.py`** — coherent modules
  at a defensible size; churn without payoff.
- **Hoisting any lazy import to module scope** while splitting — re-introduces the documented
  cross-app module-name collisions.
