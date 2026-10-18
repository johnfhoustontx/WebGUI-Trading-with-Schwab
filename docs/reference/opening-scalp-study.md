# Opening Scalp — the study's first verdict (2026-10-18)

What the Opening Scalp study found on its first run against prod data, and how to
read it. Design: [2026-10-18-opening-scalp-design.md](../plans/2026-10-18-opening-scalp-design.md)
(§5 declares the model before any outcome was seen). The full printed report is
reproduced at the end, unedited.

## The data

- Backfilled on prod 2026-10-18: 814,888 bars, 13 symbols, 1- and 5-minute.
  5-minute history reaches 2026-03-26..30 for SPY, QQQ, /ES and /NQ (about 140
  trading mornings) and 2026-02-02 for $SPX and $VIX; 1-minute history starts
  2026-09-01..07.
- Rebuild: 179 trading days × 3 symbols = 537 mornings; 398 simulated
  ($SPX 159, SPY 113, QQQ 126); skipped 80 with no opening bar (mostly SPY and
  QQQ before their history starts) and 59 whose first five minutes moved less
  than the 0.03% dead zone. 23 s on prod.
- ⚠ `tools/scalp_backfill.py` run by hand needs the stack's `.env` in the
  environment (`set -a; . ./.env; set +a`): `/passthrough` checks
  `PROXY_SHARED_SECRET`, and without it every request is a 401. The first run
  failed exactly so — every key reported FAILED, nothing was written. The
  nightly top-up runs inside options_svc, whose unit loads `.env`.

## The verdict, in plain words

**There is no usable edge.** Read three ways:

1. **The opening direction carries almost no information about the next hour.**
   Buying the 0DTE contract *with* the first five minutes wins 46–55% of the
   time; buying it *against* them wins 50–58% (SPY 49.6%). The base rates are a coin flip
   either way, and the "against" side is if anything slightly ahead on every
   symbol.
2. **One cell of 36 passes the declared rule at every volatility multiplier:**
   $SPX, "Long, against the open", low alignment (22 scored mornings; the
   minimum is 20). That is about what chance alone produces across 36 tests —
   and the 36 are not independent (the four roles on one morning move
   together, and so do the four multipliers on one path). Its Brier margin over
   the base rate is 0.03–0.08. It is also a *negative* signal: on mornings
   where the premarket disagrees with the open, fading the open won 5 of 23
   (21.7%) against a 50% base. It says "do not fade those mornings", not "take
   this trade". SPY and QQQ have no cell that passes at every multiplier.
3. **Credit spreads mostly run out of time.** Half the credit rarely decays in
   one hour at the open: credit roles win 28–40% with 16–36% of trades timing
   out. The ±50%-in-an-hour definition suits the long options far better than
   the spreads.

Two notes on how far to trust this:
- **The bias check came back clean.** On the 31 days with 1-minute bars the
  5-minute and 1-minute walks agree on 315 of 316 trades, and across all 1,592
  five-minute trades at ×1 no bar ever reached both levels. The conservative
  "both in one bar = loss" rule costs nothing here.
- **The comparison leans against finding an edge.** A bucket is scored from its
  second morning, so its first predictions are 0 or 1 and carry a large Brier
  penalty, while the base rate pools all three buckets and settles faster. A
  real but small edge could be hidden by that for the first few dozen mornings.
  That is a reason to keep collecting, not a reason to loosen the rule.

## What this does and does not show

- Premiums are MODELLED (Black-Scholes at $VIX / $VXN × the multiplier), not
  observed; the 08:36 chain captures (from 2026-10-19) will calibrate that.
- The sample is one regime, March–October 2026. Nothing here generalises past it.
- The rule was declared before the run. Changing buckets, dead zones, inputs or
  the target after reading this would be a new study, and must be declared and
  run as one.

## Appendix — the report as printed (`tools/scalp_study_report.py`, 2026-10-18)

### Opening Scalp study - 2026-10-18

Store: `/home/administrator/dev/services/options_svc/data/scalp.db`. Mornings from 2026-02-02 to 2026-10-16 (179 trading days). Alignment cuts [0.4, 0.7]; verdict multipliers [0.8, 1.0, 1.25, 1.5]; current x1; minimum scored mornings 20; target 50% / stop 50% / time stop 60 min.

Win = the target level was reached first; a timeout and a loss both count 0. Every rate is a share of modelled trades (Black-Scholes at the volatility index times the multiplier), not of filled ones. P&L per trade is not reported: a win or loss row stores the level, not a fill.

#### $SPX

Mornings stored: 179; simulated: 159; skipped: no_direction 20.

##### Walk-forward verdict (5-minute bars)

Each cell: Brier of the bucket's rate / Brier of the base rate, over the same scored mornings (n), and `EDGE` when the bucket's is lower with n at least the minimum. Lower Brier is better.

| Role | Bucket | x0.8 | x1 | x1.25 | x1.5 | Edge at every scale |
|---|---|---|---|---|---|---|
| Long, with the open | low | 0.249 / 0.256 (22) EDGE | 0.296 / 0.258 (22) | 0.259 / 0.219 (22) | 0.213 / 0.170 (22) | no |
| Long, with the open | mid | 0.276 / 0.260 (50) | 0.276 / 0.260 (50) | 0.258 / 0.245 (50) | 0.236 / 0.223 (50) | no |
| Long, with the open | high | 0.270 / 0.263 (84) | 0.270 / 0.262 (84) | 0.257 / 0.246 (84) | 0.233 / 0.224 (84) | no |
| Long, against the open | low | 0.244 / 0.271 (22) EDGE | 0.194 / 0.271 (22) EDGE | 0.163 / 0.242 (22) EDGE | 0.163 / 0.201 (22) EDGE | YES |
| Long, against the open | mid | 0.276 / 0.250 (50) | 0.280 / 0.254 (50) | 0.273 / 0.248 (50) | 0.264 / 0.235 (50) | no |
| Long, against the open | high | 0.268 / 0.265 (84) | 0.266 / 0.263 (84) | 0.269 / 0.269 (84) EDGE | 0.262 / 0.258 (84) | no |
| Credit, with the open | low | 0.259 / 0.214 (22) | 0.238 / 0.180 (22) | 0.213 / 0.141 (22) | 0.148 / 0.074 (22) | no |
| Credit, with the open | mid | 0.264 / 0.243 (50) | 0.217 / 0.206 (50) | 0.155 / 0.148 (50) | 0.125 / 0.108 (50) | no |
| Credit, with the open | high | 0.248 / 0.243 (84) | 0.231 / 0.226 (84) | 0.188 / 0.186 (84) | 0.118 / 0.123 (84) EDGE | no |
| Credit, against the open | low | 0.163 / 0.224 (22) EDGE | 0.163 / 0.179 (22) EDGE | 0.129 / 0.142 (22) EDGE | 0.129 / 0.127 (22) | no |
| Credit, against the open | mid | 0.273 / 0.246 (50) | 0.258 / 0.227 (50) | 0.244 / 0.213 (50) | 0.207 / 0.169 (50) | no |
| Credit, against the open | high | 0.269 / 0.264 (84) | 0.237 / 0.233 (84) | 0.193 / 0.192 (84) | 0.137 / 0.138 (84) EDGE | no |

**$SPX: an edge at every multiplier (x0.8, x1, x1.25, x1.5) for: Long, against the open / low.**

Edges at some multipliers only (an artefact of the volatility assumption, not shown on the page): Long, with the open / low (at x0.8); Long, against the open / high (at x1.25); Credit, with the open / high (at x1.5); Credit, against the open / low (at x0.8, x1, x1.25); Credit, against the open / high (at x1.5).

##### Rates over every stored morning (x1, 5-minute bars)

| Role | Bucket | n | win | loss | timeout | win rate | Wilson 95% |
|---|---|---|---|---|---|---|---|
| Long, with the open | all (base) | 159 | 75 | 64 | 20 | 47.2% | 39.6% to 54.9% |
| Long, with the open | low | 23 | 13 | 4 | 6 | 56.5% | 36.8% to 74.4% |
| Long, with the open | mid | 51 | 22 | 23 | 6 | 43.1% | 30.5% to 56.7% |
| Long, with the open | high | 85 | 40 | 37 | 8 | 47.1% | 36.8% to 57.6% |
| Long, against the open | all (base) | 159 | 80 | 57 | 22 | 50.3% | 42.6% to 58.0% |
| Long, against the open | low | 23 | 5 | 9 | 9 | 21.7% | 9.7% to 41.9% |
| Long, against the open | mid | 51 | 27 | 18 | 6 | 52.9% | 39.5% to 65.9% |
| Long, against the open | high | 85 | 48 | 30 | 7 | 56.5% | 45.9% to 66.5% |
| Credit, with the open | all (base) | 159 | 45 | 56 | 58 | 28.3% | 21.9% to 35.8% |
| Credit, with the open | low | 23 | 6 | 4 | 13 | 26.1% | 12.5% to 46.5% |
| Credit, with the open | mid | 51 | 13 | 18 | 20 | 25.5% | 15.5% to 38.9% |
| Credit, with the open | high | 85 | 26 | 34 | 25 | 30.6% | 21.8% to 41.0% |
| Credit, against the open | all (base) | 159 | 50 | 51 | 58 | 31.4% | 24.7% to 39.0% |
| Credit, against the open | low | 23 | 4 | 6 | 13 | 17.4% | 7.0% to 37.1% |
| Credit, against the open | mid | 51 | 18 | 15 | 18 | 35.3% | 23.6% to 49.0% |
| Credit, against the open | high | 85 | 28 | 30 | 27 | 32.9% | 23.9% to 43.5% |

##### DIAGNOSTIC - Up-only and Down-only mornings (x1, 5-minute bars)

Never the number shown: the study pools both directions by role.

| Role | Mornings | Structure | n | win | loss | timeout | win rate | Wilson 95% |
|---|---|---|---|---|---|---|---|---|
| Long, with the open | Up-only | long_call | 85 | 38 | 38 | 9 | 44.7% | 34.6% to 55.3% |
| Long, with the open | Down-only | long_put | 74 | 37 | 26 | 11 | 50.0% | 38.9% to 61.1% |
| Long, against the open | Up-only | long_put | 85 | 44 | 30 | 11 | 51.8% | 41.3% to 62.1% |
| Long, against the open | Down-only | long_call | 74 | 36 | 27 | 11 | 48.6% | 37.6% to 59.8% |
| Credit, with the open | Up-only | pcs | 85 | 24 | 31 | 30 | 28.2% | 19.8% to 38.6% |
| Credit, with the open | Down-only | ccs | 74 | 21 | 25 | 28 | 28.4% | 19.4% to 39.5% |
| Credit, against the open | Up-only | ccs | 85 | 28 | 27 | 30 | 32.9% | 23.9% to 43.5% |
| Credit, against the open | Down-only | pcs | 74 | 22 | 24 | 28 | 29.7% | 20.5% to 40.9% |

#### SPY

Mornings stored: 179; simulated: 113; skipped: no_direction 26, no_open_bar 40.

##### Walk-forward verdict (5-minute bars)

Each cell: Brier of the bucket's rate / Brier of the base rate, over the same scored mornings (n), and `EDGE` when the bucket's is lower with n at least the minimum. Lower Brier is better.

| Role | Bucket | x0.8 | x1 | x1.25 | x1.5 | Edge at every scale |
|---|---|---|---|---|---|---|
| Long, with the open | low | 0.292 / 0.253 (30) | 0.288 / 0.274 (30) | 0.256 / 0.241 (30) | 0.209 / 0.185 (30) | no |
| Long, with the open | mid | 0.277 / 0.264 (36) | 0.275 / 0.265 (36) | 0.257 / 0.249 (36) | 0.231 / 0.236 (36) EDGE | no |
| Long, with the open | high | 0.278 / 0.275 (44) | 0.283 / 0.260 (44) | 0.263 / 0.230 (44) | 0.238 / 0.209 (44) | no |
| Long, against the open | low | 0.286 / 0.263 (30) | 0.276 / 0.261 (30) | 0.201 / 0.204 (30) EDGE | 0.181 / 0.185 (30) EDGE | no |
| Long, against the open | mid | 0.290 / 0.255 (36) | 0.290 / 0.258 (36) | 0.266 / 0.245 (36) | 0.240 / 0.238 (36) | no |
| Long, against the open | high | 0.285 / 0.280 (44) | 0.282 / 0.280 (44) | 0.283 / 0.298 (44) EDGE | 0.257 / 0.249 (44) | no |
| Credit, with the open | low | 0.283 / 0.252 (30) | 0.242 / 0.214 (30) | 0.112 / 0.089 (30) | 0.086 / 0.040 (30) | no |
| Credit, with the open | mid | 0.243 / 0.234 (36) | 0.218 / 0.226 (36) EDGE | 0.168 / 0.175 (36) EDGE | 0.126 / 0.127 (36) EDGE | no |
| Credit, with the open | high | 0.269 / 0.254 (44) | 0.247 / 0.217 (44) | 0.198 / 0.168 (44) | 0.069 / 0.065 (44) | no |
| Credit, against the open | low | 0.201 / 0.201 (30) EDGE | 0.201 / 0.200 (30) | 0.151 / 0.139 (30) | 0.126 / 0.122 (30) | no |
| Credit, against the open | mid | 0.277 / 0.271 (36) | 0.240 / 0.236 (36) | 0.187 / 0.194 (36) EDGE | 0.128 / 0.128 (36) EDGE | no |
| Credit, against the open | high | 0.271 / 0.268 (44) | 0.251 / 0.240 (44) | 0.220 / 0.210 (44) | 0.198 / 0.194 (44) | no |

**SPY: no bucket shows an edge at every volatility multiplier (x0.8, x1, x1.25, x1.5). The page would show the base rate with "no measured edge".**

Edges at some multipliers only (an artefact of the volatility assumption, not shown on the page): Long, with the open / mid (at x1.5); Long, against the open / low (at x1.25, x1.5); Long, against the open / high (at x1.25); Credit, with the open / mid (at x1, x1.25, x1.5); Credit, against the open / low (at x0.8); Credit, against the open / mid (at x1.25, x1.5).

##### Rates over every stored morning (x1, 5-minute bars)

| Role | Bucket | n | win | loss | timeout | win rate | Wilson 95% |
|---|---|---|---|---|---|---|---|
| Long, with the open | all (base) | 113 | 52 | 44 | 17 | 46.0% | 37.1% to 55.2% |
| Long, with the open | low | 31 | 17 | 7 | 7 | 54.8% | 37.8% to 70.8% |
| Long, with the open | mid | 37 | 14 | 16 | 7 | 37.8% | 24.1% to 53.9% |
| Long, with the open | high | 45 | 21 | 21 | 3 | 46.7% | 32.9% to 60.9% |
| Long, against the open | all (base) | 113 | 56 | 41 | 16 | 49.6% | 40.5% to 58.6% |
| Long, against the open | low | 31 | 11 | 12 | 8 | 35.5% | 21.1% to 53.1% |
| Long, against the open | mid | 37 | 20 | 11 | 6 | 54.1% | 38.4% to 69.0% |
| Long, against the open | high | 45 | 25 | 18 | 2 | 55.6% | 41.2% to 69.1% |
| Credit, with the open | all (base) | 113 | 33 | 40 | 40 | 29.2% | 21.6% to 38.2% |
| Credit, with the open | low | 31 | 9 | 6 | 16 | 29.0% | 16.1% to 46.6% |
| Credit, with the open | mid | 37 | 10 | 13 | 14 | 27.0% | 15.4% to 43.0% |
| Credit, with the open | high | 45 | 14 | 21 | 10 | 31.1% | 19.5% to 45.7% |
| Credit, against the open | all (base) | 113 | 36 | 37 | 40 | 31.9% | 24.0% to 40.9% |
| Credit, against the open | low | 31 | 7 | 10 | 14 | 22.6% | 11.4% to 39.8% |
| Credit, against the open | mid | 37 | 12 | 12 | 13 | 32.4% | 19.6% to 48.5% |
| Credit, against the open | high | 45 | 17 | 15 | 13 | 37.8% | 25.1% to 52.4% |

##### DIAGNOSTIC - Up-only and Down-only mornings (x1, 5-minute bars)

Never the number shown: the study pools both directions by role.

| Role | Mornings | Structure | n | win | loss | timeout | win rate | Wilson 95% |
|---|---|---|---|---|---|---|---|---|
| Long, with the open | Up-only | long_call | 57 | 25 | 25 | 7 | 43.9% | 31.8% to 56.7% |
| Long, with the open | Down-only | long_put | 56 | 27 | 19 | 10 | 48.2% | 35.7% to 61.0% |
| Long, against the open | Up-only | long_put | 57 | 31 | 19 | 7 | 54.4% | 41.6% to 66.6% |
| Long, against the open | Down-only | long_call | 56 | 25 | 22 | 9 | 44.6% | 32.4% to 57.6% |
| Credit, with the open | Up-only | pcs | 57 | 16 | 20 | 21 | 28.1% | 18.1% to 40.8% |
| Credit, with the open | Down-only | ccs | 56 | 17 | 20 | 19 | 30.4% | 19.9% to 43.3% |
| Credit, against the open | Up-only | ccs | 57 | 19 | 17 | 21 | 33.3% | 22.5% to 46.3% |
| Credit, against the open | Down-only | pcs | 56 | 17 | 20 | 19 | 30.4% | 19.9% to 43.3% |

#### QQQ

Mornings stored: 179; simulated: 126; skipped: no_direction 13, no_open_bar 40.

##### Walk-forward verdict (5-minute bars)

Each cell: Brier of the bucket's rate / Brier of the base rate, over the same scored mornings (n), and `EDGE` when the bucket's is lower with n at least the minimum. Lower Brier is better.

| Role | Bucket | x0.8 | x1 | x1.25 | x1.5 | Edge at every scale |
|---|---|---|---|---|---|---|
| Long, with the open | low | 0.283 / 0.262 (47) | 0.276 / 0.262 (47) | 0.278 / 0.256 (47) | 0.271 / 0.248 (47) | no |
| Long, with the open | mid | 0.304 / 0.231 (25) | 0.304 / 0.239 (25) | 0.298 / 0.250 (25) | 0.263 / 0.229 (25) | no |
| Long, with the open | high | 0.278 / 0.282 (51) EDGE | 0.280 / 0.274 (51) | 0.281 / 0.274 (51) | 0.281 / 0.278 (51) | no |
| Long, against the open | low | 0.275 / 0.257 (47) | 0.266 / 0.248 (47) | 0.278 / 0.263 (47) | 0.279 / 0.268 (47) | no |
| Long, against the open | mid | 0.296 / 0.236 (25) | 0.297 / 0.239 (25) | 0.302 / 0.244 (25) | 0.297 / 0.244 (25) | no |
| Long, against the open | high | 0.280 / 0.275 (51) | 0.280 / 0.277 (51) | 0.281 / 0.272 (51) | 0.277 / 0.266 (51) | no |
| Credit, with the open | low | 0.271 / 0.251 (47) | 0.253 / 0.237 (47) | 0.232 / 0.207 (47) | 0.216 / 0.188 (47) | no |
| Credit, with the open | mid | 0.297 / 0.244 (25) | 0.263 / 0.218 (25) | 0.244 / 0.203 (25) | 0.145 / 0.133 (25) | no |
| Credit, with the open | high | 0.275 / 0.267 (51) | 0.281 / 0.282 (51) EDGE | 0.239 / 0.228 (51) | 0.194 / 0.185 (51) | no |
| Credit, against the open | low | 0.276 / 0.260 (47) | 0.261 / 0.256 (47) | 0.223 / 0.241 (47) EDGE | 0.184 / 0.194 (47) EDGE | no |
| Credit, against the open | mid | 0.302 / 0.235 (25) | 0.296 / 0.272 (25) | 0.269 / 0.254 (25) | 0.200 / 0.187 (25) | no |
| Credit, against the open | high | 0.282 / 0.279 (51) | 0.259 / 0.241 (51) | 0.217 / 0.186 (51) | 0.188 / 0.166 (51) | no |

**QQQ: no bucket shows an edge at every volatility multiplier (x0.8, x1, x1.25, x1.5). The page would show the base rate with "no measured edge".**

Edges at some multipliers only (an artefact of the volatility assumption, not shown on the page): Long, with the open / high (at x0.8); Credit, with the open / high (at x1); Credit, against the open / low (at x1.25, x1.5).

##### Rates over every stored morning (x1, 5-minute bars)

| Role | Bucket | n | win | loss | timeout | win rate | Wilson 95% |
|---|---|---|---|---|---|---|---|
| Long, with the open | all (base) | 126 | 69 | 55 | 2 | 54.8% | 46.1% to 63.2% |
| Long, with the open | low | 48 | 28 | 20 | 0 | 58.3% | 44.3% to 71.2% |
| Long, with the open | mid | 26 | 13 | 12 | 1 | 50.0% | 32.1% to 67.9% |
| Long, with the open | high | 52 | 28 | 23 | 1 | 53.8% | 40.5% to 66.7% |
| Long, against the open | all (base) | 126 | 73 | 49 | 4 | 57.9% | 49.2% to 66.2% |
| Long, against the open | low | 48 | 30 | 17 | 1 | 62.5% | 48.4% to 74.8% |
| Long, against the open | mid | 26 | 15 | 10 | 1 | 57.7% | 38.9% to 74.5% |
| Long, against the open | high | 52 | 28 | 22 | 2 | 53.8% | 40.5% to 66.7% |
| Credit, with the open | all (base) | 126 | 49 | 57 | 20 | 38.9% | 30.8% to 47.6% |
| Credit, with the open | low | 48 | 16 | 23 | 9 | 33.3% | 21.7% to 47.5% |
| Credit, with the open | mid | 26 | 8 | 13 | 5 | 30.8% | 16.5% to 50.0% |
| Credit, with the open | high | 52 | 25 | 21 | 6 | 48.1% | 35.1% to 61.3% |
| Credit, against the open | all (base) | 126 | 50 | 53 | 23 | 39.7% | 31.6% to 48.4% |
| Credit, against the open | low | 48 | 18 | 20 | 10 | 37.5% | 25.2% to 51.6% |
| Credit, against the open | mid | 26 | 12 | 8 | 6 | 46.2% | 28.8% to 64.5% |
| Credit, against the open | high | 52 | 20 | 25 | 7 | 38.5% | 26.5% to 52.0% |

##### DIAGNOSTIC - Up-only and Down-only mornings (x1, 5-minute bars)

Never the number shown: the study pools both directions by role.

| Role | Mornings | Structure | n | win | loss | timeout | win rate | Wilson 95% |
|---|---|---|---|---|---|---|---|---|
| Long, with the open | Up-only | long_call | 60 | 34 | 25 | 1 | 56.7% | 44.1% to 68.4% |
| Long, with the open | Down-only | long_put | 66 | 35 | 30 | 1 | 53.0% | 41.2% to 64.6% |
| Long, against the open | Up-only | long_put | 60 | 34 | 24 | 2 | 56.7% | 44.1% to 68.4% |
| Long, against the open | Down-only | long_call | 66 | 39 | 25 | 2 | 59.1% | 47.0% to 70.1% |
| Credit, with the open | Up-only | pcs | 60 | 22 | 29 | 9 | 36.7% | 25.6% to 49.3% |
| Credit, with the open | Down-only | ccs | 66 | 27 | 28 | 11 | 40.9% | 29.9% to 53.0% |
| Credit, against the open | Up-only | ccs | 60 | 25 | 24 | 11 | 41.7% | 30.1% to 54.3% |
| Credit, against the open | Down-only | pcs | 66 | 25 | 29 | 12 | 37.9% | 27.1% to 49.9% |

#### DIAGNOSTIC - Pooled symbols (x1, 5-minute bars)

The three symbols trade on one tape, so a pooled n overstates the evidence about threefold. Never the number shown.

| Role | Bucket | n | win | loss | timeout | win rate | Wilson 95% |
|---|---|---|---|---|---|---|---|
| Long, with the open | all (base) | 398 | 196 | 163 | 39 | 49.2% | 44.4% to 54.1% |
| Long, with the open | low | 102 | 58 | 31 | 13 | 56.9% | 47.2% to 66.1% |
| Long, with the open | mid | 114 | 49 | 51 | 14 | 43.0% | 34.3% to 52.2% |
| Long, with the open | high | 182 | 89 | 81 | 12 | 48.9% | 41.7% to 56.1% |
| Long, against the open | all (base) | 398 | 209 | 147 | 42 | 52.5% | 47.6% to 57.4% |
| Long, against the open | low | 102 | 46 | 38 | 18 | 45.1% | 35.8% to 54.8% |
| Long, against the open | mid | 114 | 62 | 39 | 13 | 54.4% | 45.2% to 63.2% |
| Long, against the open | high | 182 | 101 | 70 | 11 | 55.5% | 48.2% to 62.5% |
| Credit, with the open | all (base) | 398 | 127 | 153 | 118 | 31.9% | 27.5% to 36.6% |
| Credit, with the open | low | 102 | 31 | 33 | 38 | 30.4% | 22.3% to 39.9% |
| Credit, with the open | mid | 114 | 31 | 44 | 39 | 27.2% | 19.9% to 36.0% |
| Credit, with the open | high | 182 | 65 | 76 | 41 | 35.7% | 29.1% to 42.9% |
| Credit, against the open | all (base) | 398 | 136 | 141 | 121 | 34.2% | 29.7% to 39.0% |
| Credit, against the open | low | 102 | 29 | 36 | 37 | 28.4% | 20.6% to 37.8% |
| Credit, against the open | mid | 114 | 42 | 35 | 37 | 36.8% | 28.6% to 46.0% |
| Credit, against the open | high | 182 | 65 | 70 | 47 | 35.7% | 29.1% to 42.9% |

#### Bias check - 5-minute vs 1-minute bars

The same trades (same morning, strikes, entry premium and bucket) walked on both bar sizes, on the days that have complete 1-minute bars, at x1. On 5-minute bars a bar that reaches both levels in one bar is counted a loss; the 1-minute walk shows how often that was wrong.

Overlapping trades: 316 over 31 day(s) (2026-09-02 to 2026-10-16).

| Symbol | Role | n | 5-minute win rate | 1-minute win rate | both levels in one bar (5-minute) |
|---|---|---|---|---|---|
| $SPX | Long, with the open | 30 | 50.0% | 50.0% | 0 (0.0%) |
| $SPX | Long, against the open | 30 | 40.0% | 40.0% | 0 (0.0%) |
| $SPX | Credit, with the open | 30 | 30.0% | 30.0% | 0 (0.0%) |
| $SPX | Credit, against the open | 30 | 13.3% | 13.3% | 0 (0.0%) |
| QQQ | Long, with the open | 24 | 75.0% | 75.0% | 0 (0.0%) |
| QQQ | Long, against the open | 24 | 41.7% | 41.7% | 0 (0.0%) |
| QQQ | Credit, with the open | 24 | 54.2% | 54.2% | 0 (0.0%) |
| QQQ | Credit, against the open | 24 | 25.0% | 25.0% | 0 (0.0%) |
| SPY | Long, with the open | 25 | 44.0% | 44.0% | 0 (0.0%) |
| SPY | Long, against the open | 25 | 48.0% | 48.0% | 0 (0.0%) |
| SPY | Credit, with the open | 25 | 28.0% | 28.0% | 0 (0.0%) |
| SPY | Credit, against the open | 25 | 24.0% | 24.0% | 0 (0.0%) |

All overlapping trades, 5-minute result (rows) by 1-minute result (columns):

| 5-minute \ 1-minute | win | loss | timeout |
|---|---|---|---|
| win | 123 | 0 | 0 |
| loss | 0 | 110 | 1 |
| timeout | 0 | 0 | 82 |

Across every 5-minute trade at x1: 0 of 1592 (0.0%) had both levels in one bar and were counted losses.

