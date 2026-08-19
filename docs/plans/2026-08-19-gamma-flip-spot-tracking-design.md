# The gamma flip tracks spot for index symbols (2026-08-19)

Found by inspection of the new `/desk` Dealer Positioning panel, which shows four
symbols' flips side by side and refreshes every minute. The defect long predates
that page — the single-symbol Gamma page simply never put `$NDX` next to SPY.

**Status: root cause established and measured. Steps 1 and 2 are implemented.
$SPX is fixed; $NDX is halved but not fixed; the ETFs are untouched. See
[the $NDX caveat](#-ndx-remains-partly-affected--known-not-overlooked).**

## The symptom

The gamma flip for `$SPX` and `$NDX` follows spot minute to minute. SPY and QQQ
hold still, which is what a structural level should do.

Measured over the 2026-08-19 session, `view='gex'`, from `gex_history.db`:

| | corr(spot, flip) | spot range | flip range |
|---|---|---|---|
| **$SPX** | **+0.972** | 50.1 | 32.6 |
| **$NDX** | **+0.846** | 324 | **401** |
| QQQ | −0.097 | 8.78 | **0.10** |
| SPY | −0.469 | 3.89 | **0.18** |

QQQ's flip moved **0.10 points** while spot moved 8.78. `$NDX`'s flip moved
*further than spot did*.

## Root cause

Three layers, each measured.

### 1. The producer: "pick the crossing nearest spot"

The stored flip comes from **`GammaEngine.snapshot_summary`** (`gamma_tool.py`),
not from `calc_flip_point`:

```python
# Flip point: linear interpolation where net crosses zero near spot.
# Collect all crossings within ±3% band, then pick nearest to spot.
if v1 * v2 <= 0 and (v2 - v1) != 0:
    if abs(s1 - spot) <= spot * 0.03 or abs(s2 - spot) <= spot * 0.03:
        candidates.append(round(interp, 2))
if candidates:
    flip = min(candidates, key=lambda x: abs(x - spot))
```

**`min(..., key=|x - spot|)` is spot-anchored by construction.** Confirmed by
re-implementing the rule and reproducing the stored value **exactly on all 946
rows** across the four symbols (301/301, 215/215, 215/215, 215/215).

### 2. Why it only bites the indices: candidate count

The rule is only as stable as the set it chooses from.

| | mean candidates offered to the rule |
|---|---|
| SPY | **1.1** |
| QQQ | 2.8 |
| $SPX | 7.0 |
| **$NDX** | **23.6** |

With ~1 candidate the rule is inert — you get the only crossing, and it sits
still. Choosing "nearest to spot" from 24 scattered levels is arithmetically
close to reporting spot itself.

### 3. Why the indices generate so many candidates: dead strikes

| | candidates | **from a zero endpoint** | strict (`< 0`) only | **zero-net strikes in band** |
|---|---|---|---|---|
| $SPX | 7.0 | 2.0 | 5.0 | **45.0** |
| **$NDX** | 23.6 | **8.9** | 14.7 | **134.7** |
| QQQ | 2.8 | 0.0 | 2.8 | **0.0** |
| SPY | 1.1 | 0.0 | 1.1 | **0.0** |

`$SPX` and `$NDX` carry **45–135 strikes with net GEX of exactly zero** inside the
±3% search band. SPY and QQQ have **none** — every 1-wide strike is genuinely
traded. Index chains list far more strikes than trade, so the near-money ladder
is full of dead ones.

And the comparison is **`v1 * v2 <= 0`, non-strict**, so every boundary between a
dead run and live data **manufactures a crossing**. That accounts for **8.9 of
`$NDX`'s 23.6** candidates — pure artifacts of absent data.

## Impact on downstream consumers

Every consumer reduces the flip to one bit — is spot above or below it — so the
blast radius is decided by how each handles that bit. It changes **31%** of
minutes for `$NDX` and **25%** for `$SPX`; **1%** for SPY.

| Consumer | Severity | Why |
|---|---|---|
| Desk dealer-regime chip | **Severe (indices)** | Raw `gex_regime`, no hysteresis. `LONG GAMMA · PINS` and `SHORT GAMMA · RUNS` are opposite postures |
| Opportunity Board `WHY` + regime cell | **Severe (indices)** | Same raw bit |
| `dealer_regime` setup tag | Affected, currently inert | Keys off `gex_regime`, but the IV gates hold it at `neutral`/`na` — it would surface the moment that axis fires |
| Flow `gamma_flip` alert | Degraded, not spamming | 0.15% hysteresis + symbol allowlist; 2 alerts all day, both QQQ |
| Driver `_posture` | Affected, low frequency | Briefing `gamma_flip`, 4×/day; driver is off/shadow |
| Sentiment five-state regime | **Largely insulated** | `_matrix_row` reads **SPY first**, `$SPX` only as fallback |
| Rescue advisories | Context only | Advisory text, not a gate |

**The hysteresis suppression is not free.** `band_pct = 0.0015` (0.15%) versus a
median gap of 0.041% (`$SPX`) / 0.027% (`$NDX`) means the band is rarely cleared:

| | minutes clearing the 0.15% band | in the alert symbol list |
|---|---|---|
| SPY | **87.6%** | yes |
| QQQ | 49.1% | yes |
| $SPX | **38.2%** | yes |
| $NDX | 21.1% | **no** |

So the index gamma-flip alert is **meaningfully less sensitive**, not dead — `$SPX`
still clears the band 38% of the time. (`$NDX`'s absence from the allowlist is a
separate config gap, unrelated to this defect.)

**Net:** nothing downstream *acts* on it automatically today — the driver is off,
the setup tag is inert, alerts are gated, sentiment reads SPY. This is a
**display-integrity problem, not a live trading-risk one** — but it is a display
someone would reasonably trade off.

## The fix, step 1: exclude zero-net strikes (IMPLEMENTED)

Change `v1 * v2 <= 0` to `v1 * v2 < 0` in `snapshot_summary`.

A strike whose net GEX is exactly zero is **not a level** — it is the absence of
data. Interpolating a "crossing" onto the boundary of a dead run invents a
structural feature out of an untraded strike. This is the same principle applied
throughout the Desk work: missing data must degrade to *unknown*, never to a
confident value.

`calc_flip_point` already uses the strict `<`, so this also removes one of the
divergences between the two implementations (below).

### Measured effect — necessary, but NOT sufficient

Re-run over the same session after the change:

| | candidates before → after | corr(spot, flip) | above/below bit-flips |
|---|---|---|---|
| $SPX | 7.0 → **5.0** | +0.972 → **+0.972** | 24% → **24%** |
| $NDX | 23.4 → **14.6** | +0.847 → **+0.843** | 32% → **29%** |
| QQQ | 2.8 → 2.8 | −0.094 → −0.094 | 8% → 8% |
| SPY | 1.1 → 1.1 | −0.440 → −0.440 | 1% → 1% |

**The candidate reduction is real and the ETFs are provably untouched — but the
symptom barely moves.** This corrects an assumption in the analysis above: the
zero-strike artifacts were a genuine defect (the flip could be reported at a
dead-strike boundary, which is an invented level) but a **minor contributor** to
the tracking. The ~14.6 genuine crossings that remain are on their own enough to
make "nearest to spot" degenerate.

So the honest status after step 1 is: **the flip no longer invents levels out of
untraded strikes, and the index flip still tracks spot.** The user-visible defect
is unfixed. The selection rule is the dominant cause and is where step 2 must go.

Shipping step 1 anyway is still right — reporting a structural level at a strike
nobody traded is wrong independently of the tracking, and the cleaned candidate
set is the correct basis on which to evaluate step 2. But it should not be
described as a fix for the symptom.

## The fix, step 2: require the sign to PERSIST (IMPLEMENTED)

A crossing counts only when the sign **holds for 2 live strikes on each side**
(`_FLIP_PERSIST_STRIKES`). A genuine flip separates a sustained positive region
from a sustained negative one; a profile that pops negative for one strike and
returns is a lumpy strike, not a regime boundary.

"Nearest to spot" is **kept** — with oscillation removed it is the right
tie-break, and it is what makes SPY/QQQ correct today.

**Zero-net strikes are SKIPPED when checking the run**, not counted as breaking
it — the same principle as step 1. On an index ladder carrying ~135 dead strikes,
treating a zero as a sign break would reject most genuine flips.

### Why k=2, measured

Every alternative was run over the session: strongest-crossing, cumulative
totals, and re-binning the profile at 0.15 / 0.25 / 0.40% of spot, each crossed
with k = 1 / 2 / 3.

| bucket | k | $SPX range/flip-rate | $NDX | QQQ | SPY |
|---|---|---|---|---|---|
| none | 1 | 32.56 / 25% | 401 / 31% | 0.09 / 8% | 0.17 / 1% |
| **none** | **2** | **1.08 / 1%** | **207 / 17%** | **0.09 / 8%** | **0.17 / 1%** |
| none | 3 | **no flip on 323/324** | 121 / 2% | 0.09 / 8% | 0.17 / 1% |
| 0.15% | 2 | 11.00 / 3% (179 misses) | 152 / 11% | 0.92 / 5% | 1.07 / 1% |
| 0.25% | 2 | 43.68 / 11% (75 misses) | 258 / 8% | 1.47 / 2% | 1.22 / 2% |

**No combination passed a strict acceptance bar.** Bucketing at any width widened
the ETF ranges (SPY 0.17 → 1.07+), and k=3 destroyed `$SPX`. k=2 with no bucketing
is the only rule that improves the indices while leaving the working symbols
untouched.

### Measured effect of the shipped rule

| | corr(spot, flip) | flip range | above/below flips | misses |
|---|---|---|---|---|
| **$SPX** | +0.968 → **−0.374** | 32.56 → **1.08** | 25% → **1%** | 0/327 |
| **$NDX** | +0.843 → +0.752 | 401.41 → **206.68** | 31% → **18%** | 0/241 |
| QQQ | −0.252 → −0.099 | 0.09 → 0.10 | 8% → **7%** | 0/241 |
| SPY | −0.414 → −0.394 | 0.17 → 0.18 | 1% → 1% | 0/241 |

**`$SPX` is fixed. `$NDX` is halved, not fixed. The ETFs are untouched and nothing
lost a reading.**

### ⚠ $NDX remains partly affected — known, not overlooked

Its 25-wide, unevenly-spaced ladder (5-wide near the money among 10-wide, the
same unevenness documented as combing the Gamma heatmap) oscillates even at
2-strike persistence: 18% of minutes still swap the above/below bit. No rule
tested fixed it without damaging the symbols that work.

The untested idea most likely to help is computing the flip on a **uniform strike
ladder** — `gamma.uniform_strike_grid()` already exists for the heatmap and solves
exactly this unevenness. That is a larger change (it moves what the engine
computes on, not just how it selects) and belongs in its own investigation.

Until then: **`$NDX`'s dealer-regime chip is materially more trustworthy than it
was, and still the least trustworthy of the four.**

## What is deliberately NOT changed

**The nearest-to-spot tie-break is kept.** It is the proximate cause of the
tracking only when the candidate set is noisy; with oscillation filtered out it
is the correct rule, and it is why SPY/QQQ read correctly today. The replacements
tested were all worse:

| | current (nearest to spot) | strongest crossing |
|---|---|---|
| $SPX | corr +0.972, range 32.56 | corr −0.786, **range 0.35** |
| $NDX | corr +0.846, range 401.41 | corr +0.670, **range 785** |
| QQQ | corr −0.095, range 0.10 | corr +0.070, **range 8.07** |
| SPY | corr −0.456, range 0.18 | corr −0.157, **range 14.31** |

Selecting the strongest crossing fixes `$SPX` beautifully, leaves `$NDX` worse on
range, and **degrades both ETFs** — which are the working case. A cumulative
(running-total) definition was also tested and did **not** decorrelate the
indices while making SPY/QQQ worse.

So the right order is: **land the zero filter, re-measure, then revisit the
selection rule against the cleaned candidate set.** The picture changes once
~38% of `$NDX`'s candidates disappear, and choosing a rule against the current
noisy set would be fitting to artifacts.

**Explicitly rejected as symptom fixes:** widening the ±3% window, smoothing the
flip across ticks, median-filtering it. Each produces a *stable* number without
establishing it is the *right* one — and a wrong-but-steady flip is worse than a
noisy honest one, because it stops looking suspicious.

## ⚠ Latent hazard: two flip implementations that disagree

`gamma_tool.py` contains **two** flip computations with different semantics:

| | `calc_flip_point` (line ~2851) | `snapshot_summary` (line ~1223) |
|---|---|---|
| comparison | `v1 * v2 < 0` (strict) | `v1 * v2 <= 0` (non-strict) |
| window | filters strikes to ±3%, then scans | scans all, tests either endpoint |
| selection | **first** crossing ascending | **nearest to spot** |

Only `snapshot_summary`'s is used in production. The duplicate cost this
investigation two full rounds: an early single-snapshot spot-check appeared to
match `calc_flip_point` for SPY/QQQ, and that reading was generalised from `n=1`.
Across 214 rows they do not match at all — QQQ's recomputed range is **188×** the
stored one. Consolidating them is worth doing, but as its own change with its own
verification, not folded into a fix.

## How to reproduce these measurements

All numbers here come from read-only queries against
`options-scanner/gex_history.db` on the prod checkout, session 2026-08-19,
`view='gex'`. The three scratch scripts used are not committed; the queries are
simple enough to rebuild from the tables above:

- correlation + range per symbol: `load_date_with_grid`, correlate `spot` vs `flip`
- candidate counts: re-implement `snapshot_summary`'s loop, count per snapshot
- zero-strike counts: count grid entries with `net == 0.0` inside `spot * 0.03`
- bit stability: `gex_regime(spot, flip)` per row, count adjacent changes

The reproduction check that matters most is the exact one: re-implementing
"nearest to spot" must reproduce the stored `flip` column on every row. If a
future change breaks that, the producer has moved.
