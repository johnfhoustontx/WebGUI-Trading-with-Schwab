# Strategy Finder: why no credit spreads, and the per-trade cap

2026-09-22. The question: why does the Strategy Finder not find put and call credit
spreads?

## What was measured

The Finder's own `screen_spreads` call, run on prod with its reject funnel on (Finder
defaults: short delta 0.10-0.20, 10% minimum credit, DTE 0-60):

| Symbol | Short strikes in band | Built | Rejections |
|---|---|---|---|
| SPY | 209 | 0 | 82 credit floor, 63 edge floor, 49 outside the expected-move window, 15 illiquid |
| NVDA | 36 | 0 | 19 credit floor, 14 edge floor |
| AMD | 144 | 0 | 42 edge floor, 21 credit floor, 43 illiquid, 3 over the $250 cap |

Without the $250 cap AMD built 3 PCS. Widening the band to 0.27 gave SPY one CCS. The
survivors would then have met the SWING IV-rank floor of 30, with SPY at 25.5 and
NVDA at 29.5 that day.

The **edge floor** (`credit/width >= |short delta| + EDGE_MARGIN`, 0.02) is the
structural one: a vertical's credit/width is close to the risk-neutral chance of
finishing past the short strike. A call's delta exceeds that chance, so a CCS almost
never clears it; a put's |delta| sits just below, so a PCS has a sliver of room that the
fill model (40% from the natural side) usually spends. The **$0.25 absolute minimum
credit** rules out narrow widths at these deltas.

## Edge-floor replay (item 2)

Every closed PCS/CCS captured signal on prod (917, 2026-06-14 to 2026-09-22), bucketed
by `entry_credit/width - |entry_short_delta|`, R = realized P&L / max loss:

| Margin | n | Mean R | Win |
|---|---|---|---|
| 0-0.02 | 18 | +0.135 | 72% |
| 0.02-0.05 | 581 | +0.102 | 63% |
| 0.05-0.10 | 193 | +0.247 | 79% |
| >= 0.10 | 125 | +0.674 | 74% |

**What this cannot say:** every row post-dates the 0.02 margin, so nothing below the
floor was ever recorded; what the floor refuses is unmeasured. **What it does say:**
among the trades it admits, the thinnest edge is the weakest, rising steadily with
margin (PCS and SWING alike; CCS is too sparse to read). That argues against loosening
the floor. **Decision: `EDGE_MARGIN` stays 0.02.** Caveat: the captured book's
`MANUAL_CLOSE` rows book the full credit on some trades (see CLAUDE.md), which flatters
every bucket.

## What changed

1. **Per-trade caps to config, both $750** (operator decision). `config/paper.toml`
   `[risk] max_risk_per_trade` (the Account; also sizes the Market Scanner's widths) and
   `ledger_max_risk_per_trade` (the Ledger), read by `shared/paper_limits.py` into
   `config_paper`'s existing constants, catalogued in Settings -> Configuration ->
   Paper books. The Account was $250.
2. **The Finder and Income Window size against the Ledger's cap.** `swing_scan` passes
   `config_paper.LEDGER_MAX_RISK_PER_TRADE` to `screen_spreads`, read at call time:
   their trades book into the Ledger.
3. **The IV-rank floor is unchanged** - it has measured support.
4. **An empty credit list says why.** `swing_scan` collects the funnel and returns
   `compute.credit_spread_summary` (`{strikes, built, reasons}`) as `credit_spreads`;
   the handler publishes it on `cache:options:swing` and the public Finder's payload;
   `finder_view.credit_spread_note` words it under the count line on both Finder pages
   when the list holds no PCS, CCS or IC.
