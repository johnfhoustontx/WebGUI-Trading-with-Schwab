# Volatility gate — apply the floor wherever premium is sold (B2)

*Design, 2026-09-12. Gap assessment item **B2**.*

## What the assessment asked for

> **B2. Apply the volatility floor wherever premium is sold.** The Strategy
> Finder, the Income Window and the directional tab's naked shorts have none.
> Add an optional *ceiling* for the long-premium profiles: buy when volatility is
> low.

## What measurement found

Two things, and the second is larger than the item as written.

### 1. The gap is real, and the Income Window is the worst of it

`MIN_IV_RANK` is applied in exactly one place — `scanner_engine.run_full_scan`,
over `signals_0dte` and `signals_swing`. Every other premium-selling surface is
ungated:

| surface | path | gated? |
|---|---|---|
| Market Scanner 0-DTE / Swing | `run_full_scan` | **yes** (35 / 30) |
| Market Scanner **Directional** | `run_full_scan` → `signals_directional` | no |
| **Strategy Finder** | `compute.swing_scan` | no |
| **Income Window** | `compute.income_scan` → `swing_scan(trade_type="INCOME")` | no — and `min_iv_rank()` has no `INCOME` key, so even a keyed lookup returns 0 |

Measured on the live board (prod `cache:options:income`, 2026-09-12) — the
window's five published candidates, in the app's own rank order:

| rank | symbol | structure | IV rank | score |
|---|---|---|---|---|
| 1 | IREN | PCS | **0.1** | 57.0 |
| 2 | SPY | CCS | 34.9 | 54.4 |
| 3 | SPY | CCS | 34.9 | 54.0 |
| 4 | XOM | SHORT_PUT | 69.2 | 53.5 |
| 5 | CRWV | PCS | **15.3** | 50.2 |

The board's **top-ranked idea sells premium at an IV rank of 0.1** — the
cheapest possible reading, the exact trade the 0-DTE/SWING floor exists to
refuse. Four of the five sit below the SWING floor of 30. Median 34.9.

The reason it ranks first rather than last: the income board is scored by
`strategy_scoring` (Fit+Quality), where volatility enters only through
`fit_vol`, weighted `FIT_WEIGHT × FIT_VOL_W` = **0.3 × 0.4 = 12%** of the
composite. A short-premium structure in a "low" regime loses about 12 points of
composite — real, and nowhere near a refusal.

### 2. The floors that DO exist are set well below where the edge starts

Measured over the 910 closed captured signals on prod with an outcome
(`signals` joined to `signal_outcomes`, R = realized / `entry_max_loss`):

| entry IV rank | n | mean R | median R | win |
|---|---|---|---|---|
| 0–39 | 21 | **−0.149** | −0.143 | 23.8% |
| 40–44 | 73 | **−0.151** | — | 26.0% |
| 45–49 | 45 | +0.015 | — | 26.7% |
| 50–54 | 26 | +0.484 | — | 50.0% |
| 55–69 | 134 | +0.239 | +0.297 | 69.4% |
| 70–84 | 183 | +0.223 | +0.258 | 71.6% |
| 85–100 | 428 | +0.266 | +0.339 | 81.5% |

`corr(entry_iv_rank, R) = +0.110`. Where a floor would land:

| floor | cut n | cut mean R | cut total R | kept mean R | kept total R |
|---|---|---|---|---|---|
| 35 (today, 0-DTE) | 3 | +0.422 | +1.3 | +0.203 | +184.5 |
| 40 | 21 | −0.149 | −3.1 | +0.212 | +188.9 |
| **45** | 94 | −0.150 | **−14.1** | +0.245 | **+199.9** |
| 50 | 139 | −0.097 | −13.5 | +0.258 | +199.2 |
| 55 | 165 | −0.005 | −0.9 | +0.251 | +186.7 |
| 60 | 205 | +0.106 | +21.8 | +0.233 | +164.0 |

**45 is the optimum on total R** and 55 gives the gain back, so "tighter is
better" stops being true past 50 — worth stating, because this repo's standing
preference is the tight end.

**The effect survives both obvious confounds.** Within each `entry_score`
tercile (cut at 59.7 / 63.4), IV rank below 55 is far worse than at or above it
— score_hi +0.053 vs +0.343, score_mid +0.094 vs +0.277, score_lo −0.176 vs
+0.120 — so it is **not the composite score in disguise**; the composite's own
`iv` factor does not capture it. And within 2026-08, the one month with a large
low-IV sample, the split is the same direction (+0.001 / 33.6% win vs +0.235 /
70.2%), so it is not a single bad regime.

⚠ **Two honest weaknesses.** The low band is concentrated: of the 139 trades
below 50, **ORCL is 56 and UAL 19** (mean R −0.085 and −0.599), and excluding
those two the rest of the band is **+0.042**, not negative, across 20 distinct
symbols. And the 5-point grid is not monotone — 50–54 measures +0.484 on n=26.
So the defensible claim is *"below 45 is negative"* (n=94, mean −0.150, 25.5%
win, every 5-point cell at or below zero), not a precise optimum.

## What ships, and what does not

**Ships: the gate, on every surface.** The level for the surfaces that already
have one is **unchanged**. Raising `"0-DTE"` / `SWING` from 35 / 30 to 45 would
cut ~10% of all signals — a material change to the core with the operator's name
on it, in the same class as `MAX_RISK_PER_TRADE`, so it is a config decision put
to the user with the numbers above, not a drive-by.

**Ships: `INCOME = 30`**, the floor the swing window already applies. This is
deliberately the minimal-policy choice — the same rule the rest of the app
already lives under, which is what B2 literally asks for — and not the measured
45, for the same reason: 45 is a level decision, and it belongs in one edit
alongside the other two rather than being smuggled in on the one surface that had
no floor to change. It cuts IREN (0.1) and CRWV (15.3) from the live board and
keeps the two SPY rows at 34.9.

**Does not ship: the long-premium ceiling, enabled.** The mechanism ships and the
config knob is one edit; the default is **off**. There is **no long-premium
outcome data in this app at all** — `signals.db` holds only PCS, CCS and IC — so
switching on a gate that refuses trades would be exactly the unmeasured change
this audit keeps catching. The natural setting is documented in the TOML: **65**,
which is not a new number but `infer_market_view`'s own "high" boundary, the
mirror of the floor's 35 being its "low" one.

## The shape

**`shared/vol_gate.py` — one pure predicate, and it keys on the candidate's own
vega sign, not on its structure name.** Every surface that needs this emits a
MIXED list: the Market Scanner's Directional tab carries naked shorts beside long
calls and puts, and the Strategy Finder carries debit verticals beside credit
spreads. A blanket floor over such a list would refuse the long-premium
candidates too — and cheap volatility is precisely when those are the right
trade, so it would cut hardest exactly where it should not.

```python
blocks(iv_rank, net_vega, floor=None, ceiling=None) -> str | None
```

returning `"IV_TOO_LOW"`, `"IV_TOO_HIGH"` or `None`. Three rules, each a decision:

- **An unreadable input SKIPS the gate.** No `iv_rank` (the symbol has too little
  HV history — `calc_iv_rank_percentile` returns `None` by design) and no
  `net_vega` both mean "cannot classify", and a fraction of an unknown cannot be
  enforced. This is the `max_deployed_risk_pct` rule and the reason it is stated
  again here: the alternative is the documented NaN trap, where a missing reading
  pins a bound and a data outage reads as a refusal.
- **A vega of exactly zero is not "long premium".** `premium_side` returns `None`
  there, so a vega-neutral structure passes both ways rather than being handed
  the ceiling by the sign of a rounding error.
- **`0` means OFF, for both bounds.** Not "floor at zero" — an IV rank of 0.0 is
  a real reading (IREN, above), so a floor of 0 must not be the thing that
  refuses it. Absent and 0 are the same answer, which is what keeps
  `[iv_rank_ceiling]`'s shipped default of 0 from being a live gate.

**Config: `config/scanner.toml`.** `[iv_rank]` gains `INCOME`; a new sibling
`[iv_rank_ceiling]` carries the buy-side bounds in the same per-trade-type shape,
all 0. ⚠ `scanner_config.min_iv_rank()` is closed over `DEFAULTS["iv_rank"]` —
`{k: sec.get(k, ...) for k in DEFAULTS[...]}` — so a key added to the **TOML
alone is silently dropped**. Both halves, always; a test pins it.

**Call sites, three:**

1. `compute.swing_scan` — covers the Strategy Finder **and** the Income Window in
   one place, since `income_scan` is a thin wrapper over it. Applied **after
   scoring** but reported separately.
2. `scanner_engine.run_full_scan` — the existing `MIN_IV_RANK` loop learns the
   Directional list, per signal rather than per list, because that list is mixed.
3. Nothing else: the 0-DTE and Swing lists are uniformly short-premium credit
   spreads, so their existing whole-list filter already expresses the same rule
   and is left alone.

**The dropped count is its own field, `vol_filtered`, not folded into
`filtered_out`.** The Strategy Finder renders that count as *"N below the quality
bar"*, and a volatility drop is not a quality judgement about the candidate — it
is a statement about the environment. Folding them would print a sentence that is
not true, which the 2026-09-04 copy pass exists to prevent. The page gets its own
line: *"N where premium is too cheap to sell."*
