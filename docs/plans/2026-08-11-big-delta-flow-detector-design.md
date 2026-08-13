# `big_delta` flow detector — design

**Date:** 2026-08-11
**Status:** approved (brainstorm complete) — plan/impl to follow
**Scope:** a fourth options-flow detector in `services/options_svc`, plus its config and the
instrumentation loop that tunes it. Ships **quiet-live** (Flow screen, no push).

## Problem / motivation

The three live flow detectors all measure **dollars**, not **directional exposure**:
crossover compares call vs put premium, unusual activity (UOA) gates on
`mark × volume × 100 ≥ premium_floor` ($5M), and the gamma flip is a price level. None
measures how much **directional risk** changed hands. A one-off SPY measurement (2026-08-09)
found 42 contracts carrying ≥ $100M of delta-notional that fell *below* the $5M premium floor —
$12.71B of exposure the current detectors cannot see, all cheap OTM contracts traded in size.

`tools/flow_delta_instrumentation.py` (the daily 15:30 CT post-close run) generalized that across
the ~92-symbol universe to choose a threshold from evidence. **Three sessions** (Fri 08-07 via the
08-09 run, Mon 08-10, Tue 08-11) settled the two decisions that matter:

- **A relative threshold beats an absolute one.** An absolute $ floor makes **$SPX the top symbol
  at every level, every session** — it just re-inflicts the mega-cap bias the flat $5M premium
  floor already has. A relative trigger (share of the symbol's *own* gross delta-notional) spreads
  across the universe; the top name is never $SPX.
- **~20% relative is stable:** 48/35, 53/45, 46/39 (alerts / symbols) across the three sessions,
  ~46–47 of them *new* (not already caught by the premium floor). That is the profile wanted: a
  meaningful, well-spread add that won't drown a channel currently carrying ~82–162/day.

Two findings the three-session pass also produced, recorded so they aren't re-litigated:
- The instrumentation model **overcounts real fires ~2.4×** (151 modelled vs 64 actual UOA on
  Tue; closing delta/premium ≠ intraday). Every modelled count is order-of-magnitude only — the
  live detector's actual fire rate is the ground truth, which is *why* it ships live-but-quiet.
- The "**$SPX fires zero UOA alerts**" blind spot was a **Friday-only artifact** (0 → 26 → 37
  across the three sessions). It is not a stable finding; the stable version is the
  absolute-threshold concentration above.

## The detector — `flow_alerts.detect_big_delta(symbol, chain, cfg)`

Pure + defensive (`→ []` on any failure), TDD'd. One walk of the **already-fetched** chain
(reuses the poll's `on_chain` chain — no re-fetch; a *separate* function from `detect_uoa` for
isolation — the extra in-memory loop over one symbol's contracts is negligible). Per contract:

1. Read `delta` (already sitting in the contract dict, unused today), `totalVolume`, and the
   chain's `underlyingPrice` (spot).
2. **Guard `|delta| ≤ delta_max` (1.0)** — Schwab returns `−999.0` as a no-value sentinel that
   manufactures billions in phantom exposure; without this it fires daily on unpriced junk
   (24–83 such contracts every measured session).
3. Keep only the **delta band** `[delta_lo, delta_hi]` (0.05–0.85) — excludes near-zero (noise)
   and deep-ITM (stock-replacement / assignment mechanics, not directional bets).
4. `delta_notional = |delta| × volume × 100 × spot`; **accumulate the symbol's gross**.

After the walk, flag contracts where **`delta_notional ≥ rel_threshold × gross`** AND
**`delta_notional ≥ min_contract_notional`**. The absolute floor replaces a separate symbol-level
`min_gross`: a contract in a $500k-gross name (XLC) can't clear $10M, so tiny names drop out
naturally — one knob, not two. Return `top_n` by delta-notional, each an alert dict:

```
{type:"big_delta", side, symbol, strike, expiry, dte, delta, volume,
 delta_notional, pct_of_gross, cost:mark}
```

Note: for the *relative* test spot cancels within a symbol (`|δ|·vol·spot / Σ|δ|·vol·spot`), so a
missing spot doesn't break the relative decision — spot only scales the displayed `$` and the
absolute floor; degrade gracefully when it's absent.

## Data flow

Detected in `compute.collect_gex_snapshots`'s `on_chain(sym, chain)` callback, beside the existing
`detect_uoa` call, into a new `_BIG_DELTA_STASH` (mirrors `_UOA_STASH` +
`clear/stash/take_big_delta_stash`). `handlers.run_flow_alerts` drains
`compute.take_big_delta_stash()` beside the UOA drain, using the **same** cooldown seen-set
(once-per-contract-per-day; id `= "{sym}|big_delta|{side}|{strike:g}|{expiry}"`), stamps `ts` +
`text`, and folds into `fresh`.

## Quiet-live split (two suppressions, gated on config)

`big_delta` alerts are appended to `cache:options:flow_alerts` like any other (so the Flow screen
shows them), but when **`not cfg["big_delta"]["push"]`**:

1. **`run_flow_alerts`** skips `push_notify.send_flow_alert(a)` for `type=="big_delta"` (no
   phone/Telegram/Discord/SMS).
2. **`webgui/alerts.py`** `new_flow_alerts` excludes `big_delta` ids from the chime/toast trigger
   set (still listed in the screen; just doesn't chime).

Flipping **`push = true`** in the toml removes both suppressions — full-live (screen + chime +
phone) with no code change.

## Config — `config/flow_alerts.toml [big_delta]`

Read by `flow_alerts.load_thresholds()` (defaults added to its literal, same as `[uoa]`), edit +
restart `options_svc` to tune:

```toml
[big_delta]
enabled = true            # run the detector
push    = false           # QUIET-LIVE: false = Flow screen only (no chime/toast/phone);
                          #             true = full-live. The one-line go-live change.
rel_threshold = 0.20      # fire when a contract's delta-notional >= this share of the symbol's OWN gross
min_contract_notional = 10000000   # AND >= this absolute $ ($10M) — a big share of a tiny name isn't real
delta_lo = 0.05           # delta band low  — drop near-zero delta (noise)
delta_hi = 0.85           # delta band high — drop deep-ITM (stock-replacement / assignment)
delta_max = 1.0           # sentinel guard  — drop |delta| > 1 (Schwab's -999 no-value)
top_n = 3                 # most-significant contracts per symbol per tick (by delta-notional)
```

## Flow Alerts screen (`webgui/pages/options/flow.py`)

Add `big_delta` to: the **kind filter** (a fourth toggle), the finite `(type, side)` → Tailwind
class **color map**, and the `alert_detail` **cell** (renders `delta_notional` + `pct_of_gross`,
e.g. "$312M · 24% of gross"). Tier-1 only — reads the same `cache:options:flow_alerts`; no new
key/command. Row-click still hands off to Dealer Positioning.

## Instrumentation — keep the 15:30 loop, wire it to the config

The Windows `FlowDeltaInstrumentation` task **stays** (not cleared) — it is the daily tuning
feedback. `tools/flow_delta_instrumentation.py` gets three changes so the report *is* the tuning
dashboard:

1. **A "live config" line** — reads `[big_delta]` and reports what the detector *as currently
   configured* would fire on today's close (N alerts / M symbols), applying the exact live
   `rel_threshold` + `min_contract_notional` + band. The one number that answers "is my config
   right?".
2. **A `big_delta` reconciliation** — modelled-config vs what the **live detector actually fired**
   today, read from `cache:options:flow_alerts` filtered to `type=="big_delta"` (the script
   already reads that cache for the UOA reconciliation). Closes the ~2.4× model gap for the live
   config specifically: "config modelled X, live fired Y."
3. **The candidate ABS/REL exploration tables stay**, now CLI-overridable (`--rel`, `--abs`), so a
   *different* threshold can be previewed before touching the toml.

The band / `delta_max` in the script also read `[big_delta]` so exploration and the live detector
never drift. **Tuning loop:** read the 15:30 report → compare live-config-modelled vs
actually-fired vs candidates → edit `[big_delta]` → restart `options_svc` → repeat; flip
`push=true` when the real rate is right and well-spread.

## Testing (TDD)

- **`detect_big_delta`** (options_svc): relative threshold fires the top-share contract and not a
  sub-share one; `min_contract_notional` floor drops a big-share-of-a-tiny-name; `|delta|>1`
  sentinel dropped; delta band excludes near-zero + deep-ITM; gross accumulated over the band;
  `top_n` cap; spot-absent degrades (relative still decides); defensive `→ []` on junk.
- **config** (`load_thresholds`): `[big_delta]` defaults present; file overrides; `push`/`enabled`
  read.
- **stash** (compute): `stash/take_big_delta_stash` round-trips + clears; `on_chain` populates it.
- **quiet-live split** (handlers): a `big_delta` alert lands in `cache:options:flow_alerts` but
  `send_flow_alert` is NOT called for it when `push=false`, and IS when `push=true`; UOA push
  unaffected; cooldown dedup shared.
- **chime exclusion** (webgui alerts): `new_flow_alerts` ignores `big_delta` for the chime trigger.
- **screen** (webgui flow): the kind filter/color/detail builders handle `big_delta`; no-inline-style
  guard stays green.
- **instrumentation**: the live-config selector applies `[big_delta]`; the reconciliation reads
  type-filtered live alerts; CLI overrides parse.

## Out of scope / YAGNI

- A separate symbol-level `min_gross` (the contract floor covers it).
- Combining `detect_big_delta` into `detect_uoa`'s walk (isolation chosen over a micro-optimization).
- Signed / directional inference — option volume is unsigned (no tape); this measures exposure
  changing hands, not anyone's direction.
- Auto-tuning the threshold — deliberately a human-in-the-loop config knob.
