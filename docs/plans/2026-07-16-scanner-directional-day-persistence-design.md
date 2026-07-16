# Options Scanner — directional trades + day-persistent signals

**Date:** 2026-07-16
**Branch:** `Using_Highcharts`
**Status:** design approved, ready to plan

Two independent changes to the Options Scanner (`/`):

1. **Expand the scan to include directional trades** — single-leg `LONG_CALL` /
   `LONG_PUT` / `SHORT_CALL` / `SHORT_PUT`, in their own sub-tab.
2. **Persist the day's signals until end of day** — the table becomes the day's
   accumulated union rather than the last scan's snapshot, with a **New** marker on
   signals you haven't seen yet.

---

## Background — what exists today

`scanner_engine.run_full_scan` (`options-scanner/scanner_engine.py:1143`) builds
**only `PCS` / `CCS` / `IC`** (`_LEGS_BY_TYPE`, line 50). Its scorer
(`options-scanner/scoring.py`) is structurally a **premium-seller's model**:
`norm_theta` rewards *positive* theta, `norm_vega` penalizes long vega, and
`norm_em_buffer(short_strike, ...)` requires a short strike to exist. **It cannot
score a long call.** That is not a bug to fix here — it is why a second scorer
already exists.

`options-scanner/strategy_scanner.py:221` **already builds all four directional
types** (`build_directional`, long legs at 0.55 delta / short legs at 0.28), scored
by `strategy_scoring.score_all` on a Fit+Quality model — but for the **Swing**
page, on a **different cache key** (`cache:options:swing`), in a **different signal
shape**:

| | flat scanner (`screen_spreads`) | `strategy_scanner` (`_assemble`) |
|---|---|---|
| `type` | `PCS`/`CCS`/`IC` | `LONG_CALL`/`SHORT_PUT`/… |
| structure | flat `short_strike`/`long_strike` | **`legs` list** |
| units | **per-share** | **per-contract dollars (×100)** |
| R:R | `rr_pct` (percent) | `rr` (ratio) |
| breakeven | scalar (or `"p/c"` string for IC) | `breakevens` **list** |
| scorer | `scoring.score_all_signals` (0-100 composite) | `strategy_scoring.score_all` (Fit+Quality) |

`handlers.rescan` (`services/options_svc/handlers.py:264`) **fully replaces**
`cache:options:scan` on every scan — a signal that stops qualifying vanishes.

---

## Decisions

| Question | Decision |
|---|---|
| How are directional trades scored/displayed? | **Separate `Directional` sub-tab**, scored by the existing `strategy_scoring` Fit+Quality model. No cross-model score comparison is ever implied. |
| Which structures? | **All four.** Naked shorts flagged undefined-risk, excluded from paper trade + driver (as the Swing page already does). |
| Which DTE window? | **Both** (0-4 and 5-15) — both chains are already fetched. One tab, DTE column disambiguates. |
| What do persisted signals show? | **Live signals show fresh numbers; dropped-out signals freeze at last-seen.** No repricing work. |
| Stale marker? | **Yes** — dimmed + explicit marker. A live signal must never be confused with a dead one. |
| What does "New" mean? | **Unseen since you last viewed the page.** Walk away for 3 hours, nothing is missed. |
| Where does the day list live? | **New Redis key**, service-side, date-scoped envelope. |

### Rejected alternatives

- **Extending `scoring.py` to score directional trades.** Would unify the score but
  requires rewriting a tuned premium model that the autonomous driver ranks its menu
  from. Unacceptable regression risk for a display convenience.
- **Reusing `signals.db` as the day store.** Investigated and rejected — it is the
  backing table for **Captured Signals**, a different concern. It drops everything
  scoring below 58 (`signal_recorder.py:14`), projects away `pop_pct` and the factor
  breakdown, and its `dedup_key` **has no date component** (`signal_recorder.py:18`),
  so a spread first seen Monday never re-records Tuesday. `first_seen_date = today`
  is therefore *not* "signals the scanner surfaced today". Reusing it would mean
  changing dedup semantics and breaking Captured Signals.
- **Accumulating into `cache:options:scan` itself.** Would feed no-longer-qualifying
  signals to the autonomous driver's menu. Rejected on safety.

---

## Part A — Directional scan

### Engine

The directional pass runs **inside `scanner_engine.run_full_scan`**, not
`options_svc/compute.run_scan`. The chains for both windows, per-symbol technicals,
and `iv_data` are all already in scope there; building it anywhere else means
re-fetching chains.

Per symbol, per window (0-4 and 5-15):

```
build_directional(chain, symbol, spot, atm_iv, dte_min, dte_max)   # strategy_scanner
  → strategy_scoring.infer_market_view(technicals, iv_analysis)
  → strategy_scoring.score_all(signals, view)
  → results["signals_directional"]
```

Up to 8 signals/symbol (4 types × 2 expirations), sorted by score descending.

> **Naming trap.** `run_full_scan` already has a "directional pass" that stamps
> `mode="DIRECTIONAL"` on directionally-biased **credit spreads**. That is unrelated
> to this feature (single-leg options). The new list is `signals_directional`; the
> existing `mode` field is untouched.

### Contract + cache

- `shared/contracts/options.py:ScanResult` gains `signals_directional: list = []` —
  **additive with a default**, so pre-existing cached payloads still validate.
- `_SCAN_DEFAULTS` (`handlers.py:250`) gains the same key.

### Blast radius — deliberately zero

- **The driver never sees directional signals.** `driver_svc/compute.build_packet`
  (line 440) merges `signals_0dte + signals_swing` only; a third list is invisible
  to it by construction. The `{"PCS","CCS","IC"}` allowlist (`guardrails.py:38`)
  stays as defense-in-depth. **Both facts get pinned by a test.**

> **The two defenses are REDUNDANT — learned while pinning them (2026-07-16).**
> Mutating `build_packet` to merge `signals_directional` does **not** produce an
> unsafe outcome: the very next line filters through `guardrails.is_allowed`,
> which rejects the single-leg types on the allowlist. So the driver is safer
> than this design claims — belt *and* suspenders both hold independently.
>
> The consequence is about **testability**, not safety: an outcome-asserting test
> built from realistic data **cannot** pin the merge property, because the
> allowlist masks the regression. Any such test passes under mutation and is
> therefore worthless as a guard. Pinning the merge requires a **synthetic probe**
> — an allowlist-*passing* `PCS` parked in `signals_directional`, which only the
> merge property can exclude (`test_build_packet_never_reads_signals_directional_key`).
>
> In practice the allowlist is the defense doing the work. The merge is a genuine
> second layer, but it is only *observably* load-bearing via that probe. If you
> ever delete the probe as "unrealistic", you have silently stopped testing the
> merge.
- Naked shorts carry `unbounded` from `payoff_metrics`, render an explicit
  undefined-risk marker, and are excluded from Send-to-Paper via the Swing page's
  existing `_allow_paper` gate.

### UI

A third **`Directional`** sub-tab beside 0-DTE / Swing, rendering
`signals_directional` through the **existing `pages/options/strategy_table.py`**
builders (`strategy_columns` / `strategy_rows` / `grade_class`) — which already
render this exact shape for the Swing page. Its Fit+Quality score column is never
placed beside a premium composite score; that is the entire reason for the separate
tab.

---

## Part B — Day persistence + New icon

### Two keys, not one

`rescan` publishes both:

| Key | Contents | Read by |
|---|---|---|
| `cache:options:scan` | **live-only**, exactly as today (plus `signals_directional`) | driver, `alerts.py` badge/chime, EOD, Status |
| `cache:options:scan_day` **(new)** | the day union | the Scanner page only |

Splitting them is what makes this safe: `cache:options:scan` keeps its semantics
verbatim, so no stale signal can reach the driver.

### The merge

A **pure, unit-testable** function in `options_svc/compute.py`, keyed off each
signal's `id` (the engine already guarantees uniqueness —
`{symbol}_{side}_{exp}_{short}_{long}`; `_assemble` sets one too).

Envelope: `{date, signals_0dte, signals_swing, signals_directional}`. A **date
mismatch resets it wholesale** — the auto-reset-at-date-roll pattern
`push_notify`'s seen-set already proves (`push_notify.py:342`).

Per signal:
- **Present in the current scan** → take it fresh, `live: True`.
- **Absent from the current scan** → carry the last-seen copy forward frozen,
  `live: False`, stamped with the time it went stale.

Runs in `handlers.rescan`: read prev `cache:options:scan_day` → merge → `cache_set`.

> **The day key is BOUNDED — the original growth estimate was wrong (2026-07-16).**
> This design initially assumed the union was "bounded by watchlist × structures
> (tens–low hundreds)". Measured, that is wrong by 1–2 orders of magnitude:
> `watchlist.get_scan_symbols()` returns **45 symbols**; `autoscan_due` fires
> **30 scans/day** (15-min slots, 08:00–15:15 CT); each symbol yields up to 24
> signals/scan (`pcs[:3]+ccs[:3]+ics(≤2)` × 2 buckets + 8 directional) → a
> **1,080/scan ceiling**. Critically, the `id` encodes **both strikes and the
> expiration** (`scanner_engine.py:862`), so every strike drift as spot moves
> mints a *permanent* new entry — the binding constraint is `scans × per-scan
> cap`, not the id space.
>
> | yield | churn/scan | day-end signals | day-end payload |
> |---|---|---|---|
> | 35% | 10% (calm) | 1,474 | 1.5 MB |
> | 50% | 30% (central) | **5,238** | **5.4 MB** |
> | 100% | 50% (volatile) | 16,740 | **17.4 MB** |
>
> The volatile case meets/exceeds the **16 MB `cache:options:gamma` payload that
> forced the documented P2 crop** — and the Scanner page version-polls and fetches
> the whole payload on every change (the P5/P6 problem gamma already had to fix).
> So the merge **caps each list, evicts oldest-stale-first, and NEVER evicts a
> `live=True` signal** (that would break the feature's promise), logging what it
> drops per the repo's no-silent-caps convention. Dead weight (`gex_walls`/
> `dex_walls` — zero consumers outside options-scanner's own intra-scan scoring)
> is stripped from day entries.

> **Date basis: `_today_ct()` — NOT `active_session_date()` (2026-07-16).**
> The union's date must be **CT-pinned**, matching `push_notify` (`_today_ct()`)
> and the scheduler (`_market_now()`); a naive local date would be the only
> local-time date in an otherwise CT-pinned pipeline.
>
> **`active_session_date()` is the obvious-looking helper and it is WRONG here.**
> It flips to today at `_GEX_START = 08:30` CT, while scans start at
> `_SCAN_START = 08:00`:
>
> ```
> 08:00 CT  scan fires  →  active_session_date = YESTERDAY
> 08:15 CT  scan fires  →  active_session_date = YESTERDAY
> 08:30 CT  scan fires  →  flips to today → date change → WIPES both
> ```
>
> It is tuned to the GEX collector's window for heatmap persistence. Using it
> here would silently discard the first two scans of every day.

> **`date` is load-bearing FOR THE CONSUMER, not just for the reset.**
> `rescan`'s merge/publish is best-effort: on failure it leaves the key
> **untouched**, which is correct (writing an empty envelope would destroy the
> day's data) — but the consequence is **stale, not absent**. If it throws on the
> first scan of a new day, the key still holds *yesterday's* envelope, including
> signals stamped `live=True` from yesterday's 15:15 scan. **The page MUST gate
> its render on `payload["date"] == today_ct`** or it will present day-old
> signals as live.

### The New icon — and a bug this fixes

**Existing bug.** `scanner.py:143` `_sig_key` reads `short_strike`/`long_strike`,
but `_populate` feeds it **display rows** from `signal_rows` (line 123), which merge
both strikes into a single `strikes` field. Every page-side key collapses to
`SPY|PCS|None|None|07/17`, so a genuinely new signal **at different strikes is
silently not marked New** whenever anything on that symbol/type/expiry existed last
scan. It also never catches IC call-wing changes. (The same function is correct in
`alerts.py:44`, which feeds it *raw* signals — one function, two shapes, only the
page-side caller broken. The nav badge and the page's New tags disagree today.)

Keying off `id` fixes it outright.

**Semantics.** On page build, snapshot `unseen = all_ids − seen_ids` **before**
marking them seen, then mark rows in that snapshot. **Ordering is load-bearing** —
acknowledge first and nothing would ever show New. On a scan version bump while the
page is open, recompute for newly-arrived ids and re-mark.

**Accepted caveat.** The seen-set is page-side module state (like today's `_NEW`),
so a **webgui restart re-marks everything New**. The day's *signals* survive a
webgui restart — they are in Redis; only the read-marks do not. Pushing GUI
read-state server-side is scope this does not earn.

### Left alone deliberately

The nav badge and chime keep firing on **credit spreads only**, off the live key.
Directional trades appear in the table but do not chime. Their score is a different
model, so the user's "min score to alert" setting cannot be honestly applied to them
— wiring it in would need its own threshold and would silently reinterpret an
existing setting.

---

## Testing

TDD per layer, following the repo's established practice.

- **`strategy_scanner` / `strategy_scoring`** — already covered; the directional
  builders are reused, not rewritten.
- **Engine** — `run_full_scan` emits `signals_directional`; all four types build;
  both DTE windows contribute; naked shorts carry `unbounded`.
- **Merge (pure)** — fresh-on-live / frozen-on-dropped; date roll resets; a signal
  that drops out then *reappears* goes live again with fresh numbers; empty prev;
  malformed prev degrades rather than raises.
- **Contract** — `ScanResult` validates a payload with no `signals_directional`
  (back-compat).
- **Driver isolation (the load-bearing guard)** — `build_packet` given a scan
  containing directional signals produces a menu with none of them; the guardrail
  allowlist still rejects `LONG_CALL`/`SHORT_PUT`.
- **Page** — `id`-keyed New marking (a regression test for the collapsed-key bug);
  unseen snapshot taken before acknowledge; stale rows render dimmed + marked;
  Send-to-Paper gated off for naked shorts.
- **Live verification** — the repo's Redis-driven end-to-end check (enqueue
  `rescan`, read both keys) rather than the browser, per the documented practice.

## Restart required

**`options_svc`** (engine + handler changes) and **the webgui** (new tab + page
logic).
