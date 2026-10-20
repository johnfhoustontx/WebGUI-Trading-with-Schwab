# Opening Scalp — the 15-minute calls' first verdict (2026-10-20)

What the intraday study found on its first run against prod data, and how to read it.
Design: [2026-10-20-scalp-intraday-calls-design.md](../plans/2026-10-20-scalp-intraday-calls-design.md)
(§4 declares the model; the Brier margin was declared before any real result was read).
The full printed report is reproduced at the end, unedited. The 08:36 call's own
verdict is in [opening-scalp-study.md](opening-scalp-study.md).

## The data

- Backfilled on prod 2026-10-20 after the big-ten bars were added: 1,627,507 bars, 23
  symbols, 1- and 5-minute; each big-ten member about 40,500 five-minute bars.
- Rebuild: 181 trading days (2026-02-02 to 2026-10-20) × 21 slots × 3 symbols = 11,403
  slot calls; 4,104 had no direction (a flat last 15 minutes, or no bars — SPY and QQQ
  history starts 2026-03-30); 116,784 outcome rows (4 roles × 4 volatility multipliers);
  108 cells; 87 s on prod.

## The verdict, in plain words

**There is no usable edge in the 15-minute calls either.** Read three ways:

1. **The last 15 minutes carry no information about the next hour.** In every symbol
   and block, buying with the last 15 minutes and buying against them win almost the
   same share (e.g. $SPX midday 37.3% vs 38.8%, SPY midday 39.7% vs 38.8%, QQQ morning
   50.5% vs 49.8%). The direction is a coin flip.
2. **0 of 108 cells pass the declared rule** (Brier at least 0.005 below the base rate,
   at every multiplier, over at least 20 scored days). The chance estimate is about
   0.1. Four cells show an "EDGE" at one or two multipliers only — three in thin
   "low" buckets (22–32 days, $SPX), one QQQ afternoon "mid" bucket (73 days) at two
   of the four — and none survives the every-multiplier rule. Every intraday call will show its base rates with "no
   measured edge".
3. **The time of day matters more than the direction.** A long option reaching +50%
   before −50% within the hour is best in the morning (44–51%) and worst midday
   (37–40%). Credit spreads are the reverse: about 27–36% in the morning and midday, but
   41–49% in the afternoon, when theta on a 0DTE spread works fastest. These are
   in-sample base rates, not edges — but they are what the page will show, and they
   are worth knowing.

Also worth knowing:
- **The alignment buckets barely separate.** The "low" bucket holds only 3–32 days per
  cell (most slots read mid or high alignment), so the bucket rule had little to work
  with. That is a finding about the inputs, not a reason to retune them after the fact.
- **The confirmation reading is not in any of this.** It is recorded from day one, and
  the declared test of whether it adds anything waits for about 40 recorded sessions
  (mid-December 2026).

## What this does and does not show

- Premiums are MODELLED (Black-Scholes at $VIX / $VXN × the multiplier); the slot
  chain captures (from 2026-10-21) will calibrate that, as the 08:36 ones do.
- One regime, February–October 2026; a day's 21 slots share one tape, so every count
  above is in days.
- The rule was declared before the run. Changing blocks, buckets, dead zones, inputs or
  the margin after reading this would be a new study, declared and run as one.

## Appendix — the report as printed (`tools/scalp_intraday_report.py`, 2026-10-20)

### Intraday scalp study - 2026-10-20

Store: `/home/administrator/dev/services/options_svc/data/scalp.db`. Days with a simulated slot: from 2026-02-02 to 2026-10-20 (181 trading days). Slots 08:51 to 13:51 CT every 15 minutes in blocks morning, midday, afternoon. Alignment cuts [0.4, 0.7]; verdict multipliers x0.8, x1, x1.25, x1.5; current x1; minimum scored DAYS 20; an edge needs a Brier margin 0.005 below the base at every multiplier; target 50% / stop 50% / time stop 60 min.

Win = the target level was reached first; a timeout and a loss both count 0. Every rate is a share of modelled trades (Black-Scholes at the volatility index times the multiplier), not of filled ones. The day is the unit: up to 21 slots a day share one tape, so every n below counts days, and a row is predicted only from earlier days.

#### Summary

**0 of 108 cells pass at every scale** (x0.8, x1, x1.25, x1.5). A cell is one symbol x time block x role x alignment bucket; 108 are defined and 108 have any scored day.

With no edge anywhere, about 0.1 of 108 would pass (at most 1 in any one rotation; 20 of 20 rotations pass at least 0): the same verdicts with each cell's bucket sequence rotated against its outcomes. A rough estimate of chance, not a significance test: the cells are strongly correlated (three symbols on one tape, shared buckets), so the count of passes swings far more than independent cells would.

No cell passes at every scale: every call would show its base rate with "no measured edge".

#### Base rates by block (x1)

Every labelled row of the role in the block: what the page shows when no bucket has an edge. The interval is Wilson with n = days.

| Symbol | Block | Role | rows | days | win rate | Wilson 95% (days) |
|---|---|---|---|---|---|---|
| $SPX | morning | Long, with the move | 743 | 181 | 44.1% | 37.1% to 51.4% |
| $SPX | morning | Long, against the move | 743 | 181 | 47.0% | 39.8% to 54.2% |
| $SPX | morning | Credit, with the move | 743 | 181 | 27.7% | 21.7% to 34.7% |
| $SPX | morning | Credit, against the move | 743 | 181 | 28.8% | 22.7% to 35.8% |
| $SPX | midday | Long, with the move | 1339 | 181 | 37.3% | 30.6% to 44.5% |
| $SPX | midday | Long, against the move | 1339 | 181 | 38.8% | 32.0% to 46.0% |
| $SPX | midday | Credit, with the move | 1339 | 181 | 26.7% | 20.8% to 33.6% |
| $SPX | midday | Credit, against the move | 1339 | 181 | 27.3% | 21.3% to 34.2% |
| $SPX | afternoon | Long, with the move | 722 | 179 | 38.6% | 31.8% to 45.9% |
| $SPX | afternoon | Long, against the move | 722 | 179 | 39.5% | 32.6% to 46.8% |
| $SPX | afternoon | Credit, with the move | 722 | 179 | 41.0% | 34.1% to 48.3% |
| $SPX | afternoon | Credit, against the move | 722 | 179 | 43.6% | 36.6% to 51.0% |
| SPY | morning | Long, with the move | 559 | 141 | 45.8% | 37.8% to 54.0% |
| SPY | morning | Long, against the move | 559 | 141 | 46.3% | 38.3% to 54.6% |
| SPY | morning | Credit, with the move | 559 | 141 | 30.6% | 23.6% to 38.6% |
| SPY | morning | Credit, against the move | 559 | 141 | 31.7% | 24.6% to 39.7% |
| SPY | midday | Long, with the move | 1001 | 141 | 39.7% | 32.0% to 47.9% |
| SPY | midday | Long, against the move | 1001 | 141 | 38.8% | 31.1% to 47.0% |
| SPY | midday | Credit, with the move | 1001 | 141 | 30.2% | 23.2% to 38.2% |
| SPY | midday | Credit, against the move | 1001 | 141 | 30.5% | 23.5% to 38.5% |
| SPY | afternoon | Long, with the move | 538 | 139 | 39.0% | 31.3% to 47.3% |
| SPY | afternoon | Long, against the move | 538 | 139 | 38.8% | 31.1% to 47.1% |
| SPY | afternoon | Credit, with the move | 538 | 139 | 48.9% | 40.7% to 57.1% |
| SPY | afternoon | Credit, against the move | 538 | 139 | 48.9% | 40.7% to 57.1% |
| QQQ | morning | Long, with the move | 624 | 141 | 50.5% | 42.3% to 58.6% |
| QQQ | morning | Long, against the move | 624 | 141 | 49.8% | 41.7% to 58.0% |
| QQQ | morning | Credit, with the move | 624 | 141 | 34.9% | 27.6% to 43.1% |
| QQQ | morning | Credit, against the move | 624 | 141 | 35.7% | 28.3% to 43.9% |
| QQQ | midday | Long, with the move | 1149 | 141 | 39.8% | 32.1% to 48.0% |
| QQQ | midday | Long, against the move | 1149 | 141 | 39.0% | 31.3% to 47.2% |
| QQQ | midday | Credit, with the move | 1149 | 141 | 29.5% | 22.6% to 37.5% |
| QQQ | midday | Credit, against the move | 1149 | 141 | 29.1% | 22.2% to 37.0% |
| QQQ | afternoon | Long, with the move | 624 | 140 | 38.5% | 30.8% to 46.7% |
| QQQ | afternoon | Long, against the move | 624 | 140 | 38.6% | 31.0% to 46.9% |
| QQQ | afternoon | Credit, with the move | 624 | 140 | 44.9% | 36.9% to 53.1% |
| QQQ | afternoon | Credit, against the move | 624 | 140 | 45.8% | 37.8% to 54.1% |

#### $SPX

##### Walk-forward verdict (5-minute bars, the day as the unit)

Each cell: Brier of the bucket's rate / Brier of the base rate over the same scored rows, (n) the DAYS those rows came from, and `EDGE` when the bucket's is lower by at least the margin with n at least the minimum. Lower Brier is better.

| Block | Role | Bucket | x0.8 | x1 | x1.25 | x1.5 | Edge at every scale |
|---|---|---|---|---|---|---|---|
| morning | Long, with the move | low | 0.276 / 0.246 (22 days) | 0.276 / 0.231 (22 days) | 0.259 / 0.210 (22 days) | 0.154 / 0.168 (22 days) EDGE | no |
| morning | Long, with the move | mid | 0.258 / 0.253 (138 days) | 0.258 / 0.252 (138 days) | 0.242 / 0.236 (138 days) | 0.218 / 0.212 (138 days) | no |
| morning | Long, with the move | high | 0.253 / 0.252 (178 days) | 0.249 / 0.248 (178 days) | 0.234 / 0.233 (178 days) | 0.210 / 0.209 (178 days) | no |
| morning | Long, against the move | low | 0.279 / 0.240 (22 days) | 0.302 / 0.249 (22 days) | 0.297 / 0.249 (22 days) | 0.281 / 0.230 (22 days) | no |
| morning | Long, against the move | mid | 0.257 / 0.256 (138 days) | 0.253 / 0.251 (138 days) | 0.246 / 0.240 (138 days) | 0.212 / 0.210 (138 days) | no |
| morning | Long, against the move | high | 0.252 / 0.250 (178 days) | 0.253 / 0.252 (178 days) | 0.244 / 0.243 (178 days) | 0.217 / 0.216 (178 days) | no |
| morning | Credit, with the move | low | 0.259 / 0.208 (22 days) | 0.154 / 0.163 (22 days) EDGE | 0.119 / 0.119 (22 days) | 0.083 / 0.082 (22 days) | no |
| morning | Credit, with the move | mid | 0.244 / 0.237 (138 days) | 0.213 / 0.207 (138 days) | 0.177 / 0.173 (138 days) | 0.136 / 0.132 (138 days) | no |
| morning | Credit, with the move | high | 0.234 / 0.234 (178 days) | 0.203 / 0.202 (178 days) | 0.158 / 0.158 (178 days) | 0.108 / 0.108 (178 days) | no |
| morning | Credit, against the move | low | 0.298 / 0.241 (22 days) | 0.290 / 0.248 (22 days) | 0.233 / 0.179 (22 days) | 0.145 / 0.084 (22 days) | no |
| morning | Credit, against the move | mid | 0.243 / 0.238 (138 days) | 0.209 / 0.205 (138 days) | 0.169 / 0.164 (138 days) | 0.102 / 0.102 (138 days) | no |
| morning | Credit, against the move | high | 0.243 / 0.242 (178 days) | 0.208 / 0.207 (178 days) | 0.160 / 0.159 (178 days) | 0.122 / 0.121 (178 days) | no |
| midday | Long, with the move | low | 0.281 / 0.249 (32 days) | 0.242 / 0.225 (32 days) | 0.242 / 0.224 (32 days) | 0.205 / 0.203 (32 days) | no |
| midday | Long, with the move | mid | 0.248 / 0.243 (154 days) | 0.240 / 0.235 (154 days) | 0.212 / 0.207 (154 days) | 0.170 / 0.166 (154 days) | no |
| midday | Long, with the move | high | 0.252 / 0.253 (180 days) | 0.239 / 0.239 (180 days) | 0.217 / 0.217 (180 days) | 0.185 / 0.184 (180 days) | no |
| midday | Long, against the move | low | 0.288 / 0.253 (32 days) | 0.273 / 0.236 (32 days) | 0.265 / 0.221 (32 days) | 0.217 / 0.170 (32 days) | no |
| midday | Long, against the move | mid | 0.255 / 0.249 (154 days) | 0.245 / 0.239 (154 days) | 0.208 / 0.204 (154 days) | 0.161 / 0.158 (154 days) | no |
| midday | Long, against the move | high | 0.250 / 0.251 (180 days) | 0.239 / 0.239 (180 days) | 0.210 / 0.208 (180 days) | 0.178 / 0.177 (180 days) | no |
| midday | Credit, with the move | low | 0.242 / 0.220 (32 days) | 0.203 / 0.201 (32 days) | 0.080 / 0.097 (32 days) EDGE | 0.055 / 0.069 (32 days) EDGE | no |
| midday | Credit, with the move | mid | 0.226 / 0.220 (154 days) | 0.198 / 0.194 (154 days) | 0.146 / 0.143 (154 days) | 0.113 / 0.111 (154 days) | no |
| midday | Credit, with the move | high | 0.228 / 0.227 (180 days) | 0.204 / 0.204 (180 days) | 0.152 / 0.151 (180 days) | 0.110 / 0.109 (180 days) | no |
| midday | Credit, against the move | low | 0.265 / 0.224 (32 days) | 0.232 / 0.187 (32 days) | 0.161 / 0.125 (32 days) | 0.118 / 0.079 (32 days) | no |
| midday | Credit, against the move | mid | 0.238 / 0.234 (154 days) | 0.197 / 0.193 (154 days) | 0.141 / 0.142 (154 days) | 0.088 / 0.091 (154 days) | no |
| midday | Credit, against the move | high | 0.233 / 0.234 (180 days) | 0.202 / 0.202 (180 days) | 0.151 / 0.150 (180 days) | 0.104 / 0.103 (180 days) | no |
| afternoon | Long, with the move | low | 0.308 / 0.238 (16 days) | 0.308 / 0.233 (16 days) | 0.251 / 0.179 (16 days) | 0.251 / 0.172 (16 days) | no |
| afternoon | Long, with the move | mid | 0.262 / 0.253 (133 days) | 0.248 / 0.239 (133 days) | 0.224 / 0.214 (133 days) | 0.202 / 0.189 (133 days) | no |
| afternoon | Long, with the move | high | 0.253 / 0.251 (171 days) | 0.243 / 0.241 (171 days) | 0.210 / 0.210 (171 days) | 0.184 / 0.185 (171 days) | no |
| afternoon | Long, against the move | low | 0.314 / 0.249 (16 days) | 0.300 / 0.236 (16 days) | 0.300 / 0.244 (16 days) | 0.265 / 0.233 (16 days) | no |
| afternoon | Long, against the move | mid | 0.254 / 0.251 (133 days) | 0.247 / 0.243 (133 days) | 0.228 / 0.224 (133 days) | 0.191 / 0.188 (133 days) | no |
| afternoon | Long, against the move | high | 0.254 / 0.252 (171 days) | 0.247 / 0.246 (171 days) | 0.224 / 0.224 (171 days) | 0.197 / 0.197 (171 days) | no |
| afternoon | Credit, with the move | low | 0.336 / 0.249 (16 days) | 0.307 / 0.228 (16 days) | 0.307 / 0.227 (16 days) | 0.282 / 0.204 (16 days) | no |
| afternoon | Credit, with the move | mid | 0.263 / 0.254 (133 days) | 0.257 / 0.247 (133 days) | 0.248 / 0.238 (133 days) | 0.237 / 0.221 (133 days) | no |
| afternoon | Credit, with the move | high | 0.251 / 0.250 (171 days) | 0.247 / 0.244 (171 days) | 0.225 / 0.225 (171 days) | 0.209 / 0.209 (171 days) | no |
| afternoon | Credit, against the move | low | 0.326 / 0.256 (16 days) | 0.326 / 0.265 (16 days) | 0.340 / 0.255 (16 days) | 0.337 / 0.243 (16 days) | no |
| afternoon | Credit, against the move | mid | 0.259 / 0.255 (133 days) | 0.258 / 0.254 (133 days) | 0.254 / 0.250 (133 days) | 0.232 / 0.228 (133 days) | no |
| afternoon | Credit, against the move | high | 0.255 / 0.255 (171 days) | 0.250 / 0.250 (171 days) | 0.244 / 0.244 (171 days) | 0.233 / 0.232 (171 days) | no |

**$SPX: no edge. No block, role and bucket beats the base rate at every volatility multiplier; its calls would show the base rate with "no measured edge".**

##### DIAGNOSTIC - Up-only and Down-only slots (x1)

Never the number shown: the study pools both directions by role.

| Block | Role | Slots | Structure | rows | days | win rate | Wilson 95% (days) |
|---|---|---|---|---|---|---|---|
| morning | Long, with the move | Up-only | long_call | 387 | 168 | 44.7% | 37.4% to 52.3% |
| morning | Long, with the move | Down-only | long_put | 356 | 168 | 43.5% | 36.3% to 51.1% |
| morning | Long, against the move | Up-only | long_put | 387 | 168 | 44.2% | 36.9% to 51.7% |
| morning | Long, against the move | Down-only | long_call | 356 | 168 | 50.0% | 42.5% to 57.5% |
| morning | Credit, with the move | Up-only | pcs | 387 | 168 | 28.2% | 21.9% to 35.4% |
| morning | Credit, with the move | Down-only | ccs | 356 | 168 | 27.2% | 21.1% to 34.4% |
| morning | Credit, against the move | Up-only | ccs | 387 | 168 | 26.9% | 20.7% to 34.0% |
| morning | Credit, against the move | Down-only | pcs | 356 | 168 | 30.9% | 24.4% to 38.2% |
| midday | Long, with the move | Up-only | long_call | 680 | 179 | 34.4% | 27.8% to 41.6% |
| midday | Long, with the move | Down-only | long_put | 659 | 177 | 40.2% | 33.3% to 47.6% |
| midday | Long, against the move | Up-only | long_put | 680 | 179 | 38.4% | 31.6% to 45.7% |
| midday | Long, against the move | Down-only | long_call | 659 | 177 | 39.2% | 32.3% to 46.5% |
| midday | Credit, with the move | Up-only | pcs | 680 | 179 | 23.5% | 17.9% to 30.3% |
| midday | Credit, with the move | Down-only | ccs | 659 | 177 | 30.0% | 23.8% to 37.2% |
| midday | Credit, against the move | Up-only | ccs | 680 | 179 | 26.3% | 20.4% to 33.2% |
| midday | Credit, against the move | Down-only | pcs | 659 | 177 | 28.2% | 22.1% to 35.3% |
| afternoon | Long, with the move | Up-only | long_call | 362 | 169 | 34.5% | 27.8% to 42.0% |
| afternoon | Long, with the move | Down-only | long_put | 360 | 160 | 42.8% | 35.4% to 50.5% |
| afternoon | Long, against the move | Up-only | long_put | 362 | 169 | 40.1% | 33.0% to 47.6% |
| afternoon | Long, against the move | Down-only | long_call | 360 | 160 | 38.9% | 31.7% to 46.6% |
| afternoon | Credit, with the move | Up-only | pcs | 362 | 169 | 38.7% | 31.7% to 46.2% |
| afternoon | Credit, with the move | Down-only | ccs | 360 | 160 | 43.3% | 35.9% to 51.1% |
| afternoon | Credit, against the move | Up-only | ccs | 362 | 169 | 45.6% | 38.3% to 53.1% |
| afternoon | Credit, against the move | Down-only | pcs | 360 | 160 | 41.7% | 34.3% to 49.4% |

#### SPY

##### Walk-forward verdict (5-minute bars, the day as the unit)

Each cell: Brier of the bucket's rate / Brier of the base rate over the same scored rows, (n) the DAYS those rows came from, and `EDGE` when the bucket's is lower by at least the margin with n at least the minimum. Lower Brier is better.

| Block | Role | Bucket | x0.8 | x1 | x1.25 | x1.5 | Edge at every scale |
|---|---|---|---|---|---|---|---|
| morning | Long, with the move | low | 0.295 / 0.253 (15 days) | 0.281 / 0.239 (15 days) | 0.227 / 0.211 (15 days) | 0.185 / 0.170 (15 days) | no |
| morning | Long, with the move | mid | 0.270 / 0.254 (99 days) | 0.271 / 0.251 (99 days) | 0.252 / 0.234 (99 days) | 0.228 / 0.207 (99 days) | no |
| morning | Long, with the move | high | 0.255 / 0.253 (140 days) | 0.256 / 0.254 (140 days) | 0.246 / 0.244 (140 days) | 0.225 / 0.224 (140 days) | no |
| morning | Long, against the move | low | 0.316 / 0.255 (15 days) | 0.319 / 0.252 (15 days) | 0.316 / 0.241 (15 days) | 0.292 / 0.204 (15 days) | no |
| morning | Long, against the move | mid | 0.271 / 0.254 (99 days) | 0.267 / 0.248 (99 days) | 0.258 / 0.241 (99 days) | 0.219 / 0.201 (99 days) | no |
| morning | Long, against the move | high | 0.258 / 0.255 (140 days) | 0.257 / 0.255 (140 days) | 0.244 / 0.241 (140 days) | 0.209 / 0.209 (140 days) | no |
| morning | Credit, with the move | low | 0.227 / 0.212 (15 days) | 0.185 / 0.181 (15 days) | 0.125 / 0.118 (15 days) | 0.125 / 0.115 (15 days) | no |
| morning | Credit, with the move | mid | 0.263 / 0.245 (99 days) | 0.228 / 0.209 (99 days) | 0.194 / 0.174 (99 days) | 0.133 / 0.131 (99 days) | no |
| morning | Credit, with the move | high | 0.244 / 0.242 (140 days) | 0.222 / 0.219 (140 days) | 0.182 / 0.180 (140 days) | 0.124 / 0.123 (140 days) | no |
| morning | Credit, against the move | low | 0.303 / 0.224 (15 days) | 0.314 / 0.226 (15 days) | 0.226 / 0.209 (15 days) | 0.131 / 0.114 (15 days) | no |
| morning | Credit, against the move | mid | 0.264 / 0.246 (99 days) | 0.232 / 0.225 (99 days) | 0.167 / 0.165 (99 days) | 0.132 / 0.129 (99 days) | no |
| morning | Credit, against the move | high | 0.250 / 0.247 (140 days) | 0.217 / 0.216 (140 days) | 0.164 / 0.162 (140 days) | 0.129 / 0.128 (140 days) | no |
| midday | Long, with the move | low | 0.234 / 0.237 (17 days) | 0.282 / 0.236 (17 days) | 0.266 / 0.221 (17 days) | 0.247 / 0.211 (17 days) | no |
| midday | Long, with the move | mid | 0.254 / 0.250 (121 days) | 0.247 / 0.239 (121 days) | 0.212 / 0.205 (121 days) | 0.169 / 0.165 (121 days) | no |
| midday | Long, with the move | high | 0.251 / 0.251 (140 days) | 0.242 / 0.242 (140 days) | 0.219 / 0.218 (140 days) | 0.186 / 0.184 (140 days) | no |
| midday | Long, against the move | low | 0.301 / 0.248 (17 days) | 0.302 / 0.240 (17 days) | 0.272 / 0.207 (17 days) | 0.205 / 0.133 (17 days) | no |
| midday | Long, against the move | mid | 0.255 / 0.249 (121 days) | 0.249 / 0.238 (121 days) | 0.214 / 0.212 (121 days) | 0.172 / 0.170 (121 days) | no |
| midday | Long, against the move | high | 0.249 / 0.248 (140 days) | 0.238 / 0.238 (140 days) | 0.209 / 0.209 (140 days) | 0.177 / 0.177 (140 days) | no |
| midday | Credit, with the move | low | 0.266 / 0.228 (17 days) | 0.282 / 0.246 (17 days) | 0.232 / 0.213 (17 days) | 0.174 / 0.160 (17 days) | no |
| midday | Credit, with the move | mid | 0.241 / 0.235 (121 days) | 0.215 / 0.208 (121 days) | 0.166 / 0.161 (121 days) | 0.127 / 0.124 (121 days) | no |
| midday | Credit, with the move | high | 0.242 / 0.241 (140 days) | 0.214 / 0.213 (140 days) | 0.173 / 0.172 (140 days) | 0.127 / 0.127 (140 days) | no |
| midday | Credit, against the move | low | 0.287 / 0.226 (17 days) | 0.252 / 0.184 (17 days) | 0.166 / 0.109 (17 days) | 0.166 / 0.092 (17 days) | no |
| midday | Credit, against the move | mid | 0.249 / 0.239 (121 days) | 0.220 / 0.213 (121 days) | 0.177 / 0.175 (121 days) | 0.119 / 0.117 (121 days) | no |
| midday | Credit, against the move | high | 0.235 / 0.235 (140 days) | 0.213 / 0.214 (140 days) | 0.171 / 0.171 (140 days) | 0.128 / 0.128 (140 days) | no |
| afternoon | Long, with the move | low | 0.280 / 0.240 (9 days) | 0.280 / 0.243 (9 days) | 0.112 / 0.157 (9 days) | 0.112 / 0.133 (9 days) | no |
| afternoon | Long, with the move | mid | 0.258 / 0.254 (103 days) | 0.248 / 0.241 (103 days) | 0.224 / 0.215 (103 days) | 0.198 / 0.193 (103 days) | no |
| afternoon | Long, with the move | high | 0.250 / 0.250 (133 days) | 0.245 / 0.243 (133 days) | 0.220 / 0.215 (133 days) | 0.192 / 0.191 (133 days) | no |
| afternoon | Long, against the move | low | 0.322 / 0.248 (9 days) | 0.354 / 0.240 (9 days) | 0.342 / 0.216 (9 days) | 0.342 / 0.220 (9 days) | no |
| afternoon | Long, against the move | mid | 0.241 / 0.243 (103 days) | 0.242 / 0.237 (103 days) | 0.199 / 0.199 (103 days) | 0.172 / 0.171 (103 days) | no |
| afternoon | Long, against the move | high | 0.250 / 0.249 (133 days) | 0.244 / 0.244 (133 days) | 0.228 / 0.226 (133 days) | 0.195 / 0.193 (133 days) | no |
| afternoon | Credit, with the move | low | 0.322 / 0.253 (9 days) | 0.337 / 0.256 (9 days) | 0.337 / 0.258 (9 days) | 0.321 / 0.249 (9 days) | no |
| afternoon | Credit, with the move | mid | 0.255 / 0.254 (103 days) | 0.265 / 0.252 (103 days) | 0.262 / 0.250 (103 days) | 0.245 / 0.234 (103 days) | no |
| afternoon | Credit, with the move | high | 0.253 / 0.252 (133 days) | 0.255 / 0.252 (133 days) | 0.254 / 0.249 (133 days) | 0.247 / 0.242 (133 days) | no |
| afternoon | Credit, against the move | low | 0.322 / 0.251 (9 days) | 0.322 / 0.264 (9 days) | 0.322 / 0.266 (9 days) | 0.322 / 0.266 (9 days) | no |
| afternoon | Credit, against the move | mid | 0.264 / 0.252 (103 days) | 0.263 / 0.250 (103 days) | 0.261 / 0.250 (103 days) | 0.251 / 0.245 (103 days) | no |
| afternoon | Credit, against the move | high | 0.256 / 0.253 (133 days) | 0.256 / 0.254 (133 days) | 0.254 / 0.250 (133 days) | 0.247 / 0.244 (133 days) | no |

**SPY: no edge. No block, role and bucket beats the base rate at every volatility multiplier; its calls would show the base rate with "no measured edge".**

##### DIAGNOSTIC - Up-only and Down-only slots (x1)

Never the number shown: the study pools both directions by role.

| Block | Role | Slots | Structure | rows | days | win rate | Wilson 95% (days) |
|---|---|---|---|---|---|---|---|
| morning | Long, with the move | Up-only | long_call | 298 | 131 | 47.3% | 39.0% to 55.8% |
| morning | Long, with the move | Down-only | long_put | 261 | 127 | 44.1% | 35.7% to 52.7% |
| morning | Long, against the move | Up-only | long_put | 298 | 131 | 41.6% | 33.5% to 50.2% |
| morning | Long, against the move | Down-only | long_call | 261 | 127 | 51.7% | 43.1% to 60.2% |
| morning | Credit, with the move | Up-only | pcs | 298 | 131 | 31.5% | 24.2% to 39.9% |
| morning | Credit, with the move | Down-only | ccs | 261 | 127 | 29.5% | 22.3% to 37.9% |
| morning | Credit, against the move | Up-only | ccs | 298 | 131 | 29.5% | 22.4% to 37.8% |
| morning | Credit, against the move | Down-only | pcs | 261 | 127 | 34.1% | 26.4% to 42.7% |
| midday | Long, with the move | Up-only | long_call | 524 | 140 | 38.0% | 30.4% to 46.2% |
| midday | Long, with the move | Down-only | long_put | 477 | 136 | 41.5% | 33.6% to 49.9% |
| midday | Long, against the move | Up-only | long_put | 524 | 140 | 36.3% | 28.8% to 44.5% |
| midday | Long, against the move | Down-only | long_call | 477 | 136 | 41.5% | 33.6% to 49.9% |
| midday | Credit, with the move | Up-only | pcs | 524 | 140 | 28.8% | 22.0% to 36.8% |
| midday | Credit, with the move | Down-only | ccs | 477 | 136 | 31.7% | 24.4% to 39.9% |
| midday | Credit, against the move | Up-only | ccs | 524 | 140 | 28.4% | 21.6% to 36.4% |
| midday | Credit, against the move | Down-only | pcs | 477 | 136 | 32.7% | 25.4% to 41.0% |
| afternoon | Long, with the move | Up-only | long_call | 268 | 131 | 36.2% | 28.5% to 44.7% |
| afternoon | Long, with the move | Down-only | long_put | 270 | 125 | 41.9% | 33.6% to 50.6% |
| afternoon | Long, against the move | Up-only | long_put | 268 | 131 | 37.7% | 29.9% to 46.2% |
| afternoon | Long, against the move | Down-only | long_call | 270 | 125 | 40.0% | 31.8% to 48.8% |
| afternoon | Credit, with the move | Up-only | pcs | 268 | 131 | 48.1% | 39.8% to 56.6% |
| afternoon | Credit, with the move | Down-only | ccs | 270 | 125 | 49.6% | 41.0% to 58.3% |
| afternoon | Credit, against the move | Up-only | ccs | 268 | 131 | 50.7% | 42.3% to 59.2% |
| afternoon | Credit, against the move | Down-only | pcs | 270 | 125 | 47.0% | 38.5% to 55.7% |

#### QQQ

##### Walk-forward verdict (5-minute bars, the day as the unit)

Each cell: Brier of the bucket's rate / Brier of the base rate over the same scored rows, (n) the DAYS those rows came from, and `EDGE` when the bucket's is lower by at least the margin with n at least the minimum. Lower Brier is better.

| Block | Role | Bucket | x0.8 | x1 | x1.25 | x1.5 | Edge at every scale |
|---|---|---|---|---|---|---|---|
| morning | Long, with the move | low | 0.481 / 0.266 (3 days) | 0.481 / 0.260 (3 days) | 0.481 / 0.251 (3 days) | 0.481 / 0.236 (3 days) | no |
| morning | Long, with the move | mid | 0.257 / 0.246 (88 days) | 0.259 / 0.248 (88 days) | 0.261 / 0.248 (88 days) | 0.244 / 0.231 (88 days) | no |
| morning | Long, with the move | high | 0.254 / 0.252 (140 days) | 0.255 / 0.253 (140 days) | 0.251 / 0.249 (140 days) | 0.233 / 0.231 (140 days) | no |
| morning | Long, against the move | low | 0.481 / 0.236 (3 days) | 0.481 / 0.236 (3 days) | 0.481 / 0.265 (3 days) | 0.481 / 0.318 (3 days) | no |
| morning | Long, against the move | mid | 0.262 / 0.256 (88 days) | 0.259 / 0.251 (88 days) | 0.246 / 0.241 (88 days) | 0.225 / 0.215 (88 days) | no |
| morning | Long, against the move | high | 0.252 / 0.251 (140 days) | 0.258 / 0.255 (140 days) | 0.250 / 0.248 (140 days) | 0.236 / 0.235 (140 days) | no |
| morning | Credit, with the move | low | 0.481 / 0.235 (3 days) | 0.481 / 0.229 (3 days) | 0.481 / 0.232 (3 days) | 0.481 / 0.249 (3 days) | no |
| morning | Credit, with the move | mid | 0.260 / 0.247 (88 days) | 0.253 / 0.239 (88 days) | 0.202 / 0.189 (88 days) | 0.162 / 0.151 (88 days) | no |
| morning | Credit, with the move | high | 0.250 / 0.248 (140 days) | 0.231 / 0.230 (140 days) | 0.183 / 0.183 (140 days) | 0.134 / 0.134 (140 days) | no |
| morning | Credit, against the move | low | 0.481 / 0.286 (3 days) | 0.481 / 0.313 (3 days) | 0.481 / 0.386 (3 days) | 0.370 / 0.253 (3 days) | no |
| morning | Credit, against the move | mid | 0.246 / 0.237 (88 days) | 0.225 / 0.215 (88 days) | 0.172 / 0.161 (88 days) | 0.132 / 0.123 (88 days) | no |
| morning | Credit, against the move | high | 0.246 / 0.243 (140 days) | 0.236 / 0.235 (140 days) | 0.193 / 0.193 (140 days) | 0.146 / 0.145 (140 days) | no |
| midday | Long, with the move | low | 0.289 / 0.239 (8 days) | 0.255 / 0.219 (8 days) | 0.255 / 0.206 (8 days) | 0.255 / 0.209 (8 days) | no |
| midday | Long, with the move | mid | 0.257 / 0.250 (111 days) | 0.245 / 0.237 (111 days) | 0.216 / 0.209 (111 days) | 0.172 / 0.168 (111 days) | no |
| midday | Long, with the move | high | 0.251 / 0.251 (140 days) | 0.242 / 0.242 (140 days) | 0.217 / 0.216 (140 days) | 0.188 / 0.188 (140 days) | no |
| midday | Long, against the move | low | 0.309 / 0.246 (8 days) | 0.335 / 0.258 (8 days) | 0.331 / 0.252 (8 days) | 0.256 / 0.165 (8 days) | no |
| midday | Long, against the move | mid | 0.259 / 0.251 (111 days) | 0.235 / 0.234 (111 days) | 0.191 / 0.191 (111 days) | 0.147 / 0.149 (111 days) | no |
| midday | Long, against the move | high | 0.249 / 0.249 (140 days) | 0.241 / 0.240 (140 days) | 0.213 / 0.213 (140 days) | 0.182 / 0.181 (140 days) | no |
| midday | Credit, with the move | low | 0.255 / 0.216 (8 days) | 0.255 / 0.207 (8 days) | 0.190 / 0.162 (8 days) | 0.000 / 0.021 (8 days) | no |
| midday | Credit, with the move | mid | 0.238 / 0.231 (111 days) | 0.204 / 0.197 (111 days) | 0.172 / 0.166 (111 days) | 0.110 / 0.109 (111 days) | no |
| midday | Credit, with the move | high | 0.239 / 0.238 (140 days) | 0.213 / 0.212 (140 days) | 0.168 / 0.168 (140 days) | 0.119 / 0.119 (140 days) | no |
| midday | Credit, against the move | low | 0.331 / 0.240 (8 days) | 0.331 / 0.247 (8 days) | 0.256 / 0.162 (8 days) | 0.000 / 0.019 (8 days) | no |
| midday | Credit, against the move | mid | 0.226 / 0.225 (111 days) | 0.193 / 0.194 (111 days) | 0.137 / 0.138 (111 days) | 0.083 / 0.085 (111 days) | no |
| midday | Credit, against the move | high | 0.237 / 0.236 (140 days) | 0.211 / 0.210 (140 days) | 0.169 / 0.168 (140 days) | 0.117 / 0.116 (140 days) | no |
| afternoon | Long, with the move | low | 0.329 / 0.229 (8 days) | 0.330 / 0.208 (8 days) | 0.336 / 0.196 (8 days) | 0.336 / 0.205 (8 days) | no |
| afternoon | Long, with the move | mid | 0.249 / 0.249 (73 days) | 0.215 / 0.222 (73 days) EDGE | 0.178 / 0.190 (73 days) EDGE | 0.160 / 0.160 (73 days) | no |
| afternoon | Long, with the move | high | 0.253 / 0.251 (139 days) | 0.247 / 0.245 (139 days) | 0.228 / 0.226 (139 days) | 0.193 / 0.192 (139 days) | no |
| afternoon | Long, against the move | low | 0.131 / 0.217 (8 days) | 0.131 / 0.171 (8 days) | 0.131 / 0.133 (8 days) | 0.000 / 0.063 (8 days) | no |
| afternoon | Long, against the move | mid | 0.252 / 0.246 (73 days) | 0.243 / 0.238 (73 days) | 0.198 / 0.191 (73 days) | 0.158 / 0.149 (73 days) | no |
| afternoon | Long, against the move | high | 0.252 / 0.250 (139 days) | 0.244 / 0.241 (139 days) | 0.221 / 0.219 (139 days) | 0.199 / 0.198 (139 days) | no |
| afternoon | Credit, with the move | low | 0.287 / 0.241 (8 days) | 0.287 / 0.241 (8 days) | 0.303 / 0.235 (8 days) | 0.370 / 0.275 (8 days) | no |
| afternoon | Credit, with the move | mid | 0.265 / 0.257 (73 days) | 0.266 / 0.256 (73 days) | 0.257 / 0.242 (73 days) | 0.220 / 0.212 (73 days) | no |
| afternoon | Credit, with the move | high | 0.251 / 0.250 (139 days) | 0.251 / 0.250 (139 days) | 0.239 / 0.239 (139 days) | 0.224 / 0.223 (139 days) | no |
| afternoon | Credit, against the move | low | 0.285 / 0.224 (8 days) | 0.117 / 0.197 (8 days) | 0.000 / 0.166 (8 days) | 0.000 / 0.142 (8 days) | no |
| afternoon | Credit, against the move | mid | 0.265 / 0.259 (73 days) | 0.264 / 0.251 (73 days) | 0.258 / 0.243 (73 days) | 0.253 / 0.238 (73 days) | no |
| afternoon | Credit, against the move | high | 0.254 / 0.251 (139 days) | 0.254 / 0.252 (139 days) | 0.245 / 0.244 (139 days) | 0.239 / 0.238 (139 days) | no |

**QQQ: no edge. No block, role and bucket beats the base rate at every volatility multiplier; its calls would show the base rate with "no measured edge".**

##### DIAGNOSTIC - Up-only and Down-only slots (x1)

Never the number shown: the study pools both directions by role.

| Block | Role | Slots | Structure | rows | days | win rate | Wilson 95% (days) |
|---|---|---|---|---|---|---|---|
| morning | Long, with the move | Up-only | long_call | 336 | 136 | 49.4% | 41.1% to 57.7% |
| morning | Long, with the move | Down-only | long_put | 288 | 128 | 51.7% | 43.2% to 60.2% |
| morning | Long, against the move | Up-only | long_put | 336 | 136 | 47.0% | 38.8% to 55.4% |
| morning | Long, against the move | Down-only | long_call | 288 | 128 | 53.1% | 44.5% to 61.6% |
| morning | Credit, with the move | Up-only | pcs | 336 | 136 | 32.1% | 24.9% to 40.4% |
| morning | Credit, with the move | Down-only | ccs | 288 | 128 | 38.2% | 30.2% to 46.8% |
| morning | Credit, against the move | Up-only | ccs | 336 | 136 | 33.9% | 26.5% to 42.2% |
| morning | Credit, against the move | Down-only | pcs | 288 | 128 | 37.8% | 29.9% to 46.5% |
| midday | Long, with the move | Up-only | long_call | 611 | 139 | 37.2% | 29.6% to 45.4% |
| midday | Long, with the move | Down-only | long_put | 538 | 140 | 42.8% | 34.9% to 51.0% |
| midday | Long, against the move | Up-only | long_put | 611 | 139 | 35.8% | 28.4% to 44.1% |
| midday | Long, against the move | Down-only | long_call | 538 | 140 | 42.6% | 34.7% to 50.8% |
| midday | Credit, with the move | Up-only | pcs | 611 | 139 | 26.8% | 20.2% to 34.8% |
| midday | Credit, with the move | Down-only | ccs | 538 | 140 | 32.5% | 25.3% to 40.7% |
| midday | Credit, against the move | Up-only | ccs | 611 | 139 | 26.5% | 19.9% to 34.4% |
| midday | Credit, against the move | Down-only | pcs | 538 | 140 | 32.0% | 24.8% to 40.1% |
| afternoon | Long, with the move | Up-only | long_call | 304 | 136 | 34.9% | 27.4% to 43.2% |
| afternoon | Long, with the move | Down-only | long_put | 320 | 132 | 41.9% | 33.8% to 50.4% |
| afternoon | Long, against the move | Up-only | long_put | 304 | 136 | 40.8% | 32.9% to 49.2% |
| afternoon | Long, against the move | Down-only | long_call | 320 | 132 | 36.6% | 28.8% to 45.1% |
| afternoon | Credit, with the move | Up-only | pcs | 304 | 136 | 41.4% | 33.5% to 49.9% |
| afternoon | Credit, with the move | Down-only | ccs | 320 | 132 | 48.1% | 39.8% to 56.6% |
| afternoon | Credit, against the move | Up-only | ccs | 304 | 136 | 48.0% | 39.8% to 56.4% |
| afternoon | Credit, against the move | Down-only | pcs | 320 | 132 | 43.8% | 35.6% to 52.3% |

