# Options strategy playbook vs the app — gap assessment

**Date:** 2026-09-11 · **Code compared:** `main` at `c152b00`, identical to `origin/main` (what prod runs) · **Status:** analysis, since acted on — **A1–A6, B1, B3, B6 and C1 have shipped on `claude/options-strategies-gaps-8636bf`**, and the rows they changed say so inline. Everything unmarked still describes `c152b00`.
**Companion:** [Options strategy playbook](2026-09-11-options-strategy-playbook.md). Rule IDs such as **S1**, **M2** or **X4** refer to its Part 1.

## Verdict

The app is a deep, well-instrumented **credit-spread machine**. For its three core structures — the bull put spread (PCS), the bear call spread (CCS) and the iron condor (IC) — it meets or exceeds nearly every playbook rule. It has commission-aware economics, conservative limit-order fills, a delta-aware credit floor, a 50% profit target, and stops on loss, delta and time. It also has a ten-action repair board and a nightly calibration that measures whether any of it works. Its own data says the exits are the edge.

Outside those three structures, coverage thins quickly:

| Of the playbook's 19 strategies, the app can… | Count |
|---|---|
| **analyze** in the Calculator or Simulator | 16 |
| **generate** from a scanner | 10 |
| **paper-trade** | 9 |
| **manage with automated exit rules** | **5** since B1 — PCS, CCS, IC on the full rule set; the cash-secured put and covered call on a target plus a 21-DTE manage, with no loss-side stop by design (was **3**) |

Three findings matter more than any missing strategy:

1. **Positions the app already opens were left unmanaged.** Cash-secured puts and covered calls opened from the Income Window got no profit target, no stop, and no close action: the repricer could not price a single leg, so every exit rule was skipped and they rode to expiry (§3.1, checked by hand). **Fixed 2026-09-11** by A2, A3 and B1 — they are marked, they take the profit target and a 21-DTE management rule, they carry no loss-side stop by a sourced decision now written as config, and the Rescue board offers the rolls and the wheel instead of one "Close now" row.
2. **Entry rules are applied on some scan surfaces and not others.** The implied-volatility floor binds one of four surfaces, and the earnings gate is live on one of three paths. The Strategy Finder and Income Window can sell premium at any volatility, into regimes the Market Scanner would refuse (§2.3).
3. **Two of the largest risk gaps already had a tested fix that hadn't shipped.** Commit `dde98b8` on `claude/orcl-concentration-risk-9ad685` (2026-09-09) adds per-symbol and per-expiry concentration caps and makes the earnings gate fire on the live scan. It was not in `main` at `c152b00`. **It has since been merged on this branch as `05ed539`** (§7, A1), and it reaches prod once `main` is fast-forwarded and promoted.

**Most gaps can be closed, and cheaply.** §7 makes 25 recommendations, and 11 of them are a day of work or less. The order that pays:

- **First, the correctness fixes (A1–A7).** They make existing features do what they already claim.
- **Next, the capital-protection rules (B1–B8).**
- **Then measurement (C1–C5).** This comes before any change to the app's short-dated core.
- **Strategies last (D1–D5), and only where they reuse existing machinery.** The iron butterfly is the standout. It is an iron condor whose two short strikes coincide, so the whole iron-condor pipeline can carry it once a scanner emits it.

## How this was done

Seven research passes ran in parallel. Three read the web sources behind the playbook; four read the code, covering the strategy catalog, entry rules, exit rules, and risk and process controls. Every claim about the app cites `file:line` on `main`. Claims that were surprising, or where two passes disagreed, were re-read by hand and are marked **(checked)**. One disagreement was settled that way: one pass said Income Window positions are auto-managed, and the code shows they are not.

§6.2 rests on a separate Black-Scholes calculation of credit ÷ width by short-strike delta. It assumes flat volatility with no skew, r = 0.045.

Two of the brief's sources could not be read. Investopedia blocks the crawler, and the NCFE PDF's server sends an incomplete certificate chain. tastylive's research pages return 404, so the playbook marks their figures as snippet-only. Nothing here rests on the five YouTube videos, which yielded only titles.

---

## 1. Strategy coverage

**Columns.** *Analyze* = the Calculator/Simulator can price it. *Generate* = a scanner emits it as a candidate. *Paper* = it can be opened in a paper book: the **ledger** (`trades.db`, hand-managed) or the **account** (`paper_account.db`, auto-managed). *Exits* = automated profit, stop or time rules act on it. *Rescue* = the repair board works on a held position. *Driver* = the autonomous book may trade it.

| Strategy | Analyze | Generate | Paper | Exits | Rescue | Driver |
|---|---|---|---|---|---|---|
| Long call | Yes | Market Scanner directional tab (0–15 DTE); Strategy Finder | Ledger | No | Ad-hoc form only | No |
| Long put | Yes | as long call | Ledger | No | Ad-hoc only | No |
| Short (naked) call | Yes | Directional tab; Strategy Finder | No — undefined risk refused | No | Ad-hoc only | No, by policy |
| Short put / cash-secured put | Yes | Directional tab; Strategy Finder; Income Window (30–45 DTE) | Account, from the Income Window only | **Yes** since B1 — target, 21-DTE manage, then assignment | **Yes** since B1 — advisory (§3.1) | No |
| Covered call | No — no stock leg | Income Window, against held share lots | Account | **Yes** since B1 — target, 21-DTE manage, then call-away | **Yes** since B1 — advisory | No |
| Protective (married) put | No | No | No | No | No | No |
| Collar | No | No | No | No | No | No |
| Bull call spread (debit) | Yes | Strategy Finder only | Ledger | No | Ad-hoc only | No |
| Bear call spread (credit, CCS) | Yes | All three scanners | Ledger + account + driver | **Yes** | **Yes** | **Yes** |
| Bull put spread (credit, PCS) | Yes | All three scanners | Ledger + account + driver | **Yes** | **Yes** | **Yes** |
| Bear put spread (debit) | Yes | Strategy Finder only | Ledger | No | Ad-hoc only | No |
| Long straddle | Custom legs — no template | No | No | No | No | No |
| Short straddle | Custom legs | No | No | No | No | No |
| Long strangle | Custom legs | No | No | No | No | No |
| Short strangle | Custom legs | No | No | No | No | No |
| Iron condor | Yes | Market Scanner; Strategy Finder | Ledger + account + driver | **Yes** | **Yes** | **Yes** |
| Iron butterfly | Yes | No | No | No | Ad-hoc, relabeled as IC | No |
| Butterfly (long 1-2-1) | Yes | No | No | No | Ad-hoc, long only | No |
| Calendar | Yes — per-leg expiry | No | No | No | Refused (multi-expiry) | No |
| *The wheel (workflow)* | No | Both halves on the Income board | Yes — full loop | No | Broken | No |

**Reading notes**

- **Where the evidence lives.** Templates are in `webgui/pages/options/strategies.py:19-49`. Scanner builders are `strategy_scanner.py:183-260` and `scanner_engine.py:845-1096`. The paper allow-list is `strategy_table.py:24-25`, which excludes naked shorts as undefined risk. The driver allow-list is `shared/driver_policy.py:37`, which admits PCS, CCS and IC only.
- **Analysis never becomes a trade.** Nothing built by hand in the Calculator or Simulator can be paper-traded: neither page has an action that opens a position. Paper trades start only from a scanned row.
- **Straddles and strangles have no template.** They can be priced leg by leg through the generic numeric path, but the glossary defines them and the picker doesn't offer them.
- **An iron butterfly is already an iron condor.** Its put and call shorts share a strike. A paper position stores `short_strike` / `long_strike` / `call_short` / `call_long`, and every downstream piece is keyed on `IC`: repricer, rules, Rescue and driver. So the structure is representable end to end today; only a producer is missing.
- **The wheel turns in paper, unmanaged.** Put assignment creates a share lot, a covered call is written against it, and the lot is called away and can repeat (`paper_engine.py:340-517`). No exit rule touches either half.
- **Beyond the 19,** the Calculator also offers all-call/all-put condors and call/put diagonals (analysis only). Ratio spreads can be built with unequal quantities. A broken-wing butterfly exists only as a Rescue suggestion, never as an openable trade. The jade lizard, backspreads, PMCC and LEAPS are absent; LEAPS were deliberately deferred on 2026-09-05.

---

## 2. Entry rules

| ID | Playbook rule | What the app does | Verdict |
|---|---|---|---|
| **S1** | Risk 1–2% of equity per defined-risk trade | Manual book: $250 fixed, which is 1.0% of $25,000 (`config_paper.py:25`). Driver: $3,000, or 12% (`config/driver.toml:37`, the "Very Aggressive" profile chosen 2026-07-02). Both are fixed dollars; neither shrinks as equity falls. | Met (manual) · Not met (driver, by choice) |
| **S3** | Size by max loss, not premium or contracts | Both books size by `floor(cap ÷ max loss per contract)` (`paper_sizing.py:29-41`); the driver re-derives risk from its open positions (`services/options_svc/compute.py:1495-1516`). But the scanner picks width for a different account (§2.2). | Met, with a sizing mismatch |
| **M1** | Liquid underlyings; bid-ask ≤ $0.05–0.10 | Relative, not absolute: short leg ≤ 15% / 25% / 20% of its mark (0-DTE / swing / income) or ≤ $0.02; open-interest and volume floors; the whole spread's market must be ≤ 30% of its width (`scanner_engine.py:448-518`, `fill_model.py:82-92`). No source found states the $0.05–0.10 figure. | Met, in a different form |
| **M2** | Sell premium at 30–45 DTE | Income Window screens 30–45 DTE once a day (`compute.py:456-457`). The Market Scanner scans 0–4 and 5–15 DTE (`scanner_engine.py:1393-1400`), and it feeds captured signals, auto-entry and the driver. | Deliberate divergence (§6.1) |
| **M3** | Buy options at 60–90+ DTE | Long options and debit spreads are built inside whatever window is scanned: 0–15 DTE on the Market Scanner, user-set on the Strategy Finder (default 0–120). Nothing steers premium buying further out. | Gap |
| **M4** | Limit orders only | Paper fills model a limit worked 40% into the market from the worse side, not the mid (`fill_model.py:58-73`). Also a $0.10 minimum credit and no entries in the first 5 minutes (`config_paper.py:45-46`). | Met |
| **V1** | Sell when IV rank > 50; buy when IV is low | The "IV Rank" is current ATM IV placed in the 52-week *historical*-volatility range (`iv_analysis.py:9-18`, **checked**). A floor of 35 (0-DTE) / 30 (swing) applies to Market Scanner credit spreads only (`scanner_engine.py:1743-1754`, **checked**). Volatility otherwise only nudges a score, by at most 12%. | Partial |
| **V2** | Know which Greek drives the trade | Per-position delta, theta, vega and IV in the detail panel; no gamma; no book-level totals anywhere. | Partial |
| **V3** | Don't hold short-term options through earnings | The gate is live only in the Income Window, and only once an Alpha Vantage key has filled the calendar; otherwise it reads "Not checked" and filters nothing. It is dead on the Market Scanner and Strategy Finder, where no caller passes a date (**checked**). Open positions are never checked. | Gap — partly fixed on `dde98b8` |
| **W1** | Width by account size and stock price | One width per candidate, chosen by the highest expected dollar P&L across 1–100 × the strike increment, capped at $200 (`scanner_engine.py:1233-1314`). No tiering by account or price. | Different approach |
| **W2** | Collect ≥ ⅓ of the width | Nothing reaches ⅓. Floors are 8–20% (0-DTE, by VIX regime), 12% (swing) and 20% (directional), plus `credit ÷ width ≥ \|short delta\| + 0.02`. Premium-mode shorts are capped at 0.27 delta (`scanner_engine.py:305, 964-965, 1291`). | Deliberate divergence (§6.2) |
| **W3** | Narrower spreads under 14 DTE | The width search ignores days to expiration. | Gap (minor) |
| **W4** | Wider spreads, fewer contracts | Contract counts are sized to reach a $1,000 profit, so a narrower spread trades more contracts. Commissions enter the Strategy Finder's ranking, not the Market Scanner's. | Partial |

### 2.1 The "IV Rank" is not an IV rank

`iv_analysis.py` says so itself. The field ranks today's implied volatility against a year of *realized* volatility, because the project never stored a daily IV series. It is a sound volatility-richness gauge, but it is not the IV rank that the playbook's "sell above 50" refers to. So the app's floors of 35/30 and the playbook's 50 are on different scales, and must not be compared or copied across. A true IV rank needs one stored ATM IV per symbol per day (C3).

### 2.2 The width is chosen for a different account than the one that trades it (checked)

`select_best_width` sizes contracts against `account_size = 100000` at `max_risk_pct = 0.05`, a $5,000 risk budget (`scanner_engine.py:1233-1297`). Nothing overrides the default in `run_full_scan` (`:1317`). The manual book then divides its $250 cap by one contract's max loss. At zero contracts it records `RISK_TOO_HIGH` and skips the trade (`paper_sizing.py:29-41`, `paper_engine.py:173, 207`).

Any candidate whose single contract risks more than $250 can therefore top the scan and never open. How often that happens was not measured here; the manual book's rejected orders will say. The fix is A6.

### 2.3 Four scan surfaces, three rule sets

| Rule | Market Scanner credit spreads | Market Scanner directional tab | Strategy Finder | Income Window |
|---|---|---|---|---|
| Volatility floor (35 / 30) | Yes | No | No | No |
| Earnings gate | Dead (no date passed) | Dead | Dead | **Live** |
| Sentiment regime filter | Yes | — | No | No |
| Dealer-gamma gate (index names) | Yes | No | No | No |
| Commissions in the ranking | No (gross) | Yes | Yes | Yes |
| Scoring engine | 9-factor, no cut | Fit + Quality, hard gates | Fit + Quality | Fit + Quality |

The Market Scanner carries the market-context gates but ranks gross of commissions. The other three rank net of commissions but carry almost none of the gates. The income cash-secured put also ignores its window's documented 0.15–0.25 delta band. It is built by the single-leg builder at a fixed 0.28 delta (`strategy_scanner.py:183`, `compute.py:379`, **checked**).

---

## 3. Exit and management rules

| ID | Playbook rule | What the app does | Verdict |
|---|---|---|---|
| **X1** | Take profits early — 50% for premium selling, 25–50% for credit verticals | PCS/CCS/IC at 50% of the credit. The manual book (by default) and the driver close. Captured signals instead arm a break-even stop and let the trade run (`signal_recommender.py:213-220`; the manual toggle defaults off, `webgui/app_settings.py:25`). A profit-lock ladder is built and tested but never called. Nothing else has a target. | Met for 3 structures |
| **X2** | Stop when the loss reaches 1–2× the credit | Money stop at a loss of 2× the credit (`trade_mgmt.toml:25`). Also a delta stop (entry +0.12, hard 0.45, fallback 0.35), a time stop (≤ 2 DTE and losing) and a break-even stop. The paper books never store the entry delta, so they always use the 0.35 fallback. | Met, at the loose end, for 3 structures |
| **X3** | Manage winners; roll losers out in time for a credit | The Rescue board offers ten actions, including roll out (+30 days), roll down, roll down-and-out, narrow, and convert to iron condor or iron butterfly. It is human-applied, manual book only (never the driver's), and PCS/CCS/IC only. Rolls are ranked with a debit penalty rather than required to be credits. | Partial |
| **X4** | Close or roll before expiration day (pin and after-hours assignment) | No such rule. Profitable positions ride into settlement. The paper books settle at intrinsic value at 15:00 CT, which cannot show pin or after-hours assignment on physically-settled names. | Gap |
| **X5** | Manage at 21 DTE | 21 DTE only raises the Rescue heat of a position that is already tested (`trade_mgmt.toml:61`). The one window that trades that far out (income) is unmanaged. | Gap |
| **X6** | Exits for long options and debit spreads | None. They live in the ledger, which only settles at expiry or closes by hand. | Gap |
| **X7** | Handle assignment deliberately (the wheel) | Put assignment creates a share lot at the strike, a covered call can be written against it, and the lot is called away above the strike. The loop can repeat. There is no cash-settled-index branch and no ex-dividend check. | Met (paper) |

### 3.1 Income Window positions were unmanaged (checked)

**Fixed 2026-09-11 by A2, A3 and B1.** The chain below is what was wrong, kept
because it explains why the fix has three halves — a mark (A2), a side test that
knows these structures (A3), and a rule set that is per structure rather than
global (B1). B1 also closed the last consequence below: the Rescue board now
routes single-leg positions to the single-option builders, so the rolls and the
wheel are on the menu instead of "Close now" alone.

Here is the whole chain, each link read by hand:

1. `paper_engine.run_manage_cycle` hands every open position to `signal_repricer.reprice_swing` (`paper_engine.py:610-614`).
2. `_trade_view` passes the strategy through unchanged (`:239-247`).
3. The repricer knows only `PCS`, `CCS` and `IC`, and raises on anything else (`signal_repricer.py:208-238`). Its exception branch returns a mark with no P&L (`:263-269`).
4. The cycle then skips the position: `if per_contract is None: continue` (`paper_engine.py:665-666`). `recommend()` never runs.

**Consequences:**
- A cash-secured put or covered call has no mark, no profit target, no stop and no time rule. Expiry settlement still works, including assignment and call-away.
- The error is logged for every such position on every manage cycle.
- The Rescue board cannot help either. Its "Close now" needs the missing mark (`rescue.py:300-302`), and its risk test treats a `SHORT_PUT` as call-side (`rescue.py:53`), so proximity to the strike reads backwards.
- No other command closes an account position. `paper_close` closes a *ledger* trade (`compute.py:1844-1854`).

### 3.2 One profit target, two behaviours

`trade_mgmt.toml` says reaching `tp_frac` "ARMS a break-even stop — it is not an immediate close". That is true only for captured signals. The manual paper book (with its lifecycle toggle at the default, off) and the driver close outright at 50%. Published research is split on which is better:

- An Option Alpha SPY put-spread backtest found a 75% target beat 50%.
- A tastytrade strangle study showed holding made more total dollars at a lower win rate.
- Among practitioners, TradingBlock takes about 80% on verticals where tastylive takes 50%.

The ratchet ladder already in the config (lock +25% at a 65% peak, +50% at 80%) is a middle path. It has no caller (C2).

### 3.3 The stop — and a terminology trap

"Stop at 2× the credit" is used two ways in the sources:

- **Buy back at twice the credit.** This is a loss equal to the credit, and it is what OptionsPlay uses.
- **A loss of twice the credit.** This is the app's rule: `pnl ≤ −2 × credit`.

The playbook's range is a *loss* of 1–2×, so the app sits at its loose end.

### 3.4 Management cadence

| Book | Cadence |
|---|---|
| Driver | Every minute |
| Captured signals | Every 5 minutes |
| Manual paper account | Hourly, 09:00–14:00 CT (`services/options_svc/scheduler.py:185, 209, 237-259`) |

The hourly cadence is slower than two sources in the repo say, both stale (§8).

---

## 4. Risk, sizing and process

| ID | Playbook rule | What the app does | Verdict |
|---|---|---|---|
| **S2** | Keep 30–50% of the account in cash | Manual book: no cap; it can commit nearly all its cash. Driver: open max loss ≤ $12,000, 48% of the book (`driver.toml:38`), so at least ~52% stays idle. | Gap (manual) · Met (driver) |
| **S4** | Diversify across sectors; avoid correlated positions | Driver: one position per symbol, at most 10 open. Manual book: nothing on `main` at `c152b00`. Per-symbol and per-expiry caps arrive with `dde98b8`, merged on this branch as `05ed539`. No sector or correlation check anywhere. The scan universe leans heavily on semiconductors. | Gap |
| — | Drawdown limits (not in the playbook) | $2,500 session drawdown halt on both books (`config_paper.py:26`). Driver: $1,500 daily loss halt, VIX 35 ceiling, bank-the-day target. | Beyond the playbook |
| **P1** | A written plan for every trade | No plan is stored per trade. Managed positions carry their plan as rules. The driver's model writes a rationale into a 50-entry rolling log only. | Partial |
| **P2** | Keep a trading journal | Captured signals, the EOD report, equity curve and MAE/MFE for both books, a driver scorecard, and a nightly calibration of entry score against realized R. Missing: a scorecard for the manual book, notes, and one commission convention (§8). | Beyond the playbook, with gaps |
| **P3** | Separate investing from trading | Structural: every trade is paper, and the real Schwab portfolio is read-only. | Met |

---

## 5. Where the app already goes beyond the playbook

- **Commission-aware economics.** Schwab's $0.65 per contract per leg is in realized P&L on both paper books, and in the Strategy Finder's ranking. Rolls are ranked net of fees.
- **Realistic fills.** A limit worked 40% into the market, a tradeability test on the spread's total market width, and a stale-price guard (15% drift) on every repair.
- **Smarter strike selection than a delta rule.** Short strikes come from an expected-move window, with delta as a rail, and credit must beat a delta-aware floor.
- **Market-context gates.** A sentiment regime filter, a dealer-gamma regime gate on index names, and the driver's VIX ceiling.
- **A repair menu far beyond "roll the loser".** Ten candidate actions with full economics: narrow, convert to iron condor or butterfly, roll down / out / down-and-out, partial close, and an advisory broken-wing and futures hedge.
- **Measurement.** The calibration joins entry score to realized R with day-clustered t-statistics. Its 2026-08-25 baseline (793 closed signals over 49 days) is why §6.4 can argue from this app's own data.

---

## 6. Deliberate divergences worth keeping

### 6.1 The short-dated core (M2)

The app's premium selling is built around 0–15 DTE. Its calibration found positive expected R in every 0-DTE score bucket, and showed the edge comes from exits rather than selection.

The playbook's 30–45 DTE rule is well supported too: by OIC, by Option Alpha, by OptionsPlay's 4–6 weeks, and by tastylive's 45-DTE studies, though those are only known from search snippets because the pages are gone. Both can be true, each in its own horizon.

The app has a 30–45 DTE screen but records none of its outcomes. So it cannot yet test the playbook's claim on its own trades. **Don't move the core until C1 has produced that evidence.**

### 6.2 The ⅓ rule (W2)

For a vertical spread, credit ÷ width is close to the risk-neutral probability of the spread finishing in the money, averaged across its strikes. For a narrow spread, that is near the short strike's delta. A flat-volatility Black-Scholes pass shows what "collect ⅓" demands:

| Width as % of spot | Short delta needed for ⅓ (7–45 DTE, IV 18–60%) |
|---|---|
| ~1% (e.g. $5 on SPY) | 0.27–0.40 |
| 2.5% | 0.29–0.52 |
| 5% | 0.31–0.51 (unreachable at 7 DTE, low IV) |

At the 0.16–0.20 delta strikes the income convention uses, fair credit at 30–45 DTE is **8–25% of width**. OptionsPlay reaches ⅓ by selling the 50-delta. High-probability sources collect 9–15%. The ⅓ rule and the 16-delta rule therefore cannot both hold unless volatility is high. Put another way, ⅓ caps the probability of profit near 67%.

The app's `credit ÷ width ≥ |delta| + 0.02` expresses the same "be paid enough for the risk" idea in a form that scales with strike distance. In the one case computed (a spread 2.5% of spot wide, 30 DTE, IV 35%), it sat about 3 points of width above flat-volatility fair value.

More usefully, it behaves as a **volatility filter**. The gap between a spread's fair credit ratio and its short delta widens with volatility × √time. For a spread 1% of spot wide at 30 DTE, fair value clears `|delta| + 0.02` at IV 60% and falls short at IV 18% and 35%. That is consistent with the documented absence of index signals in calm markets, where rich single names clear the floor and cheap index premium does not. These are flat-volatility figures; real chains carry skew and fill below the mid, so treat them as indicative. **Keep the floor; don't add ⅓ as a hard gate.** Where ⅓ fits naturally is the app's directional credit spreads (shorts at 0.30–0.55 delta), which already demand 20%.

### 6.3 Undefined risk (short call, short straddle, short strangle)

These are excluded from paper and the driver by policy. That matches the standing preference for tight risk limits, and the defined-risk versions (iron condor, iron butterfly) express the same views.

### 6.4 Stops and targets: the literature is split, this app's data isn't

- **Option Alpha's research argues against stops on defined-risk spreads.** A 75%-probability trade has about a 48% chance of touching its stop. In an SPY strangle study, annual return was 87% higher without stops, although drawdowns were lower with them.
- **tastylive's work (snippets only) finds a trade-off.** Stops cut losers by about 42% but lower both the win rate and the average P&L.
- **This app's calibration found the stop ladder is what produces the money.** Realized win/loss size of 1.9–2.6 against a hold-to-expiry ratio near 0.2, with the time stop exiting at −0.06R where the money stop exits at −0.95R.

Keep the stops, and keep measuring. The calibration re-run due 2026-09-23 is the next checkpoint.

### 6.5 The driver's sizing is a decision to revisit, not a defect

12% per trade and ~48% total at risk were an explicit choice on 2026-07-02. That is 6–12× the playbook's per-trade risk, and more than sources recommend at any account size: Option Alpha says 1–5%, and theoptionpremium 2–5%. It is listed under decisions (§9), not as a fix.

---

## 7. Can the gaps be closed? Recommendations

**Effort:** S = a day or less including tests · M = 2–5 days · L = 1–3 weeks. **Value** is to the paper books' risk-adjusted results and to the app's trustworthiness.

| # | Recommendation | Closes | Effort | Value |
|---|---|---|---|---|
| **A1** | Merge and promote `dde98b8` (concentration caps; live earnings gate) — **merged as `05ed539`; promote pending** | S4, V3 | S | High |
| **A2** | Teach the repricer single legs (`SHORT_PUT`, `COVERED_CALL`) — **shipped 2026-09-11** | X1–X3 for income | S–M | High |
| **A3** | Fix Rescue's put-side test for short puts — **shipped 2026-09-11** | §3.1 | S | Medium |
| **A4** | Give the income cash-secured put its own delta band — **shipped 2026-09-11** | §2.3 | S | Medium |
| **A5** | Pass an earnings date from the Strategy Finder — **shipped 2026-09-11** | V3 | S | Medium |
| **A6** | Size the width search against the real book — **shipped 2026-09-11** | S3, W1, W4 | S–M | Medium–high |
| **A7** | One commission convention; rank on net — **measured 2026-09-11: the analytics half is worth −0.019R and reorders nothing; see below** | P2, W4 | S–M | ~~Medium~~ **Low** |
| **B1** | Exit rules for cash-secured puts and covered calls — **shipped 2026-09-11** | X1, X3, X5 | M | High |
| **B2** | Apply the volatility floor wherever premium is sold — **shipped 2026-09-12; and the floor's LEVEL measured too low, see below** | V1 | S | **High** |
| **B3** | Deployment cap for the manual book — **shipped 2026-09-11** | S2 | S | Medium–high |
| **B4** | Sector cap — **shipped 2026-09-12, with the map it needed** | S4 | M | Medium |
| **B5** | Expiry-day rule for physically-settled names — **the FLAG shipped 2026-09-12; the close deliberately did not, see below** | X4 | M | Medium |
| **B6** | Store the entry short delta — **shipped 2026-09-11** | X2 | S | Medium |
| **B7** | Earnings awareness on open positions — **shipped 2026-09-12 as a heat modifier; ⚠ its rationale did NOT reproduce on this book's data** | V3 | S–M | ~~Medium~~ **Low** |
| **B8** | Size as a percent of current equity — **shipped 2026-09-12 for the DRIVER, where its two caps had drifted to 22.5% and 89.9% of the book** | S1 | S | **Medium–high** |
| **C1** | Record Income Window candidates for calibration — **shipped 2026-09-11** | M2 (evidence) | S–M | High |
| **C2** | Wire the profit-lock ladder; trial the lifecycle on the manual book — **ladder shipped 2026-09-12; the lifecycle trial is decision 8** | X1 | S | Medium |
| **C3** | Store a daily ATM IV to build a true IV rank — **shipped 2026-09-12; the store, writer AND reader already existed with 7 rows** | V1 | S | Medium |
| **C4** | Book-level Greeks | V2 | M | Medium |
| **C5** | Trade plan snapshot and a manual-book scorecard — **scorecard shipped 2026-09-12; the trade-plan snapshot is bigger than written and stays open** | P1, P2 | M | Medium |
| **D1** | Straddle and strangle templates | coverage | S | Low–medium |
| **D2** | Iron butterfly scanner on the IC pipeline | coverage | M | Medium |
| **D3** | Exits for long options and debit spreads | X6 | M | Medium |
| **D4** | Stock legs → covered call, protective put, collar analysis | coverage | M–L | Medium |
| **D5** | Calendars and diagonals end to end | coverage | L | Low–medium |

### Tier A — ship the fix that exists; fix what is broken

**A1. Merge and promote `dde98b8`.**
- **What it adds:** `paper_concentration.py`, which allows 3 positions and $750 of max loss per symbol, and 5 positions per expiry across names. `scan_earnings_dates`, which makes the live scan read `EARNINGS_CALENDAR_DB`. And a narrower earnings exemption: DTE 0 only, instead of the whole 0–4 DTE "0-DTE" bucket.
- **Evidence it works:** 61 new tests, and a replay against the prod ORCL book, which it cuts from 14 positions to 3.
- **Cost:** `main` has moved 64 commits since; a trial merge conflicts only in `docs/CHANGELOG.md`.
- **When:** promoting stops the whole target, so run it after the close (15:25–16:15 CT).
- **Status (2026-09-11):** merged on this branch as `05ed539`. The one conflict was the CHANGELOG. The failing test set was empty before and after; options-scanner gained 48 tests and options_svc 13. Fast-forwarding `main` and promoting remain.

**A2. Teach the repricer single legs.** **Shipped 2026-09-11** with a
`fill_model.realistic_single_fill` helper, and with `strategy` moved into the
manage cycle's base context so the rule engine can tell these structures apart.
The interim exit policy is the profit target only — `PROFIT_TARGET_ONLY_STRATEGIES`
skips the money, time and delta stops until B1 decides. 21 new tests. Add `SHORT_PUT`/`NAKED_PUT` (put map, short strike) and `COVERED_CALL` (call map) to `signal_repricer.reprice_swing`, with the same worked-limit buy-to-close on one leg. That alone restores marks, P&L, `recommend()` and the Rescue board's "Close now" for every Income-board position, and ends an error per position per cycle. Do it with A3, then B1.

**A3. Fix Rescue's put-side test.** **Shipped 2026-09-11** as one predicate,
`rescue.is_put_side`, replacing three copies of the membership test. `is_put_side` (`rescue.py:53`) must include `SHORT_PUT` and `NAKED_PUT`. The structural repairs (roll down-and-out for a credit) are the natural cash-secured-put repair and belong in B1.

**A4. Give the income cash-secured put its own delta band.** Pass `INCOME_PUT_DELTA` to the single-leg builder. Today it ignores the band and sells 0.28 delta, which is richer premium and more assignment than the window documents.

**Shipped 2026-09-11, and the measurement made it a bigger defect than this
entry says.** `swing_scan` already took the band and handed it to `screen_spreads`
alone, so inside ONE call the band governed the window's credit spreads while its
cash-secured put ignored it. ⚠ And 0.28 was only the *target* — `nearest_by_delta`
returns the closest strike on a coarse ladder however far out it lands, so the
**realised** delta was worse on every symbol measured: XOM **0.328**, SPY 0.281,
IREN 0.274, CRWV 0.274 — all at or above the 0.25 ceiling, not just XOM.

The fix hands the band to both builders and gives `build_directional` three rules:
the target is the band's **midpoint** (on the real XOM $5 ladder — |delta| 0.131 /
0.218 / 0.328 — that alone moves the pick from 0.328 to 0.218); only the
**ceiling** is enforced, because escaping the band downward is a thin credit the
edge and credit floors already refuse; and a band **never touches the long legs**,
where a far-OTM strike would be a lottery ticket rather than the bet. Verified
against four live chains: every short moved into the band, every long unchanged.

⚠ **It also makes the Strategy Finder's own Δ inputs bind on its single-leg
shorts**, which they never did — the page's help already claimed they did, so the
help became true rather than changing. Disclosed in `page_help.py`.

**A5. Pass an earnings date from the Strategy Finder.** Its handler supplies none (`handlers.py:470-480`). Do it after A1, so every scanner reads the same store.

**Shipped 2026-09-11.** `compute.scan_earnings` is the one lookup — a thin wrapper
over the income window's, not an alias (an alias binds the function object at
import, so the ~15 tests monkeypatching `_income_earnings` by name would miss the
new path). Every row is also **stamped** with the coverage it got, because a row
that skipped the check must not look like one that passed it, and `not_listed`
deliberately does not block: with no vendor key it is every symbol, so failing
closed would empty the page.

**A6. Size the width search against the real book.**
- **The change:** feed the book's equity and per-trade cap into `select_best_width` instead of $100,000 at 5%, and cap width so that one contract fits the cap.
- **Measure first:** count today's `RISK_TOO_HIGH` rejections to size the problem.

**Shipped 2026-09-11, and the measurement is the headline.** **169 of 773 paper
orders — 21.9% — were rejected `RISK_TOO_HIGH`**, across 35 trading dates. The
mechanism, per symbol: the chosen width cost **$425** a contract on MU (75 of the
169; $894 underlying, $5 strikes), $645 on MSFT (7.5-wide), **$2,044** on ALAB
(25-wide), against a $250 cap. `scanner_engine.DEFAULT_MAX_RISK_DOLLARS` is now
`config_paper.MAX_RISK_PER_TRADE`, and the existing `contracts <= 0` guard drops a
width nothing can size — so ~92% of those rejections are no longer emitted at all.

⚠ **The honest consequence: a $250 cap excludes high-priced underlyings with wide
strike increments entirely.** MU's narrowest available width is $5 = $425, so the
scan now emits nothing for it — the truth made visible instead of a rejection
buried in the fills log. Raising `MAX_RISK_PER_TRADE` is the operator's call. The
driver's cap is 12× larger, so a width chosen for $250 stays openable there.

⚠ **Two existing tests needed the phantom budget stated explicitly** (their chains
are 5-wide on a $530 underlying, which a $250 book cannot open at all). Their
assertions are untouched; the production default is covered by a new test class.
- **A useful extra:** a days-to-expiration width preference (W3). One published table puts 7–13 DTE at $1–2 wide and 30–44 DTE at $2.50–3.50, though it cites no backtest.

**A7. One commission convention.**
- **Today:** realized P&L is net of commissions in the account and driver books (`paper_engine.py:278-295`), but gross in captured signals (`signal_db.py:340`) and the ledger. The Market Scanner also ranks on gross `rr_pct` (`scanner_engine.py:1765-1766`).
- **Why it matters:** the calibration's R-multiples inherit the gross figure, and commissions fall hardest on the lowest-scoring bucket.
- **The change:** net everywhere, and rank on net.

**⚠ MEASURED 2026-09-11, and the rationale above is wrong. Re-runnable:
`python tools/measure_commission_convention.py --calibration --ranking`.**

*The calibration half is worth nothing.* Over prod's **910** closed captured
signals, netting the round-trip commission ($2.60–$5.20 a contract) moves mean R
by a near-uniform **−0.019**, per bucket −0.017 to −0.031, and **reorders no
bucket** (the tool asserts it). No conclusion changes: 0DTE 55–60 stays positive
at +0.180 net, SWING 55–60 stays at nothing (+0.031 → **+0.012**, which only
sharpens the existing "do not read the pooled table" finding).

And commissions do **not** fall hardest on the lowest-scoring bucket. The
denominator is `entry_max_loss`, so the drop tracks **spread width**, not score —
the largest drop landed on the *highest* bucket (SWING 70–75, −0.031).

*The ranking half is real but is a policy change, not a fix.* On today's scan the
top-10 membership is unchanged but the **order** moves, and the bias is
systematic: a $1-wide spread loses up to **3.82** points of `rr_pct` against 0.70
for a wider one, because the fee is a fixed dollar amount against a smaller
credit. So ranking net pushes selection toward **wider** spreads — more dollar
risk per contract under the same $250 cap. Whether that is an improvement is
unknown, and it belongs with **A6** (width sizing) as a deliberate decision.
⚠ That sample is 10 signals on a Friday; re-run it on a fuller scan before
leaning on the ordering claim.

**Revised recommendation: do not "net everywhere" for the analytics.** What is
left worth doing is the *consistency* of what the operator READS — the Captured
Signals P&L being gross while the Paper Account's is net is a real
apples-to-oranges on adjacent screens — and that is a display decision with no
migration, not a change to the stored column. Netting the stored
`signal_outcomes.realized_pnl` going forward would also leave the calibration's
history spanning two conventions, which is the same class of defect as the units
trap C1 found, for a measured −0.019R of benefit.

### Tier B — close the rule gaps that protect capital

**B1. Exit rules for cash-secured puts and covered calls.** **Shipped
2026-09-11** — [design](2026-09-11-income-exit-rules-design.md). `[structures.*]`
in `config/trade_mgmt.toml`, read per position through
`shared.trade_mgmt.structure_rules`; `loss_rules = false` makes A2's interim
policy permanent and config-driven, and `manage_dte = 21` is the one new
behaviour (a **profitable** position closes, an underwater one is held — closing
it would be the time stop this structure deliberately lacks). The profit target
stayed 0.50: TradingBlock's 0.90/0.95 pairs with an automatic roll this app
cannot do for a single leg. Rolling (X3) landed as Rescue routing single-leg
positions to `single_candidates`, which gained `COVERED_CALL` and a wheel row —
**advisory**, because `apply_roll` books a spread reopen. Also folded the seven
copies of the structure sets into `shared/structures.py`, which surfaced a wrong
one: `paper_adjust.apply_roll` resolved a short put to the CALL side. 73 new
tests. Needed a per-structure rule table in `trade_mgmt.toml` rather than one
global set. The sourced rules:

| Structure | Rules |
|---|---|
| Both | Close or roll once most of the premium has decayed. TradingBlock uses about 90% for a short put and about 95% for a covered call, then rolls into the next ~45-DTE cycle; the playbook's generic 50% (X1) is the more active end. Manage at 21 DTE. |
| Cash-secured put | When tested, roll down, out, or down-and-out for a credit — or accept assignment and continue the wheel (§9, decision 2). |
| Covered call | Roll once little extrinsic value is left. Roll up-and-out to keep the shares, or let them be called away if selling was the plan. No money stop: the shares hedge the call, and an Option Alpha covered-call study found stops only produced more losing trades. |

**B2. Apply the volatility floor wherever premium is sold.** The Strategy Finder, the Income Window and the directional tab's naked shorts have none. Add an optional *ceiling* for the long-premium profiles: buy when volatility is low.

**Shipped 2026-09-12** — [design](2026-09-12-volatility-gate-design.md).
`shared/vol_gate.blocks` keyed on the candidate's own **vega sign**, not its
structure name: every list that needed the gate is mixed, and cheap volatility is
exactly when its long-premium half is the right trade. ⚠ **The Income Window was
much worse than this line implies** — measured on the live board, its
**top-ranked** candidate was an IREN put credit spread at an IV rank of **0.1**,
and four of five rows sat below the SWING floor of 30; volatility reaches that
board only through `fit_vol`, 12% of the composite. `INCOME = 30` shipped, the
ceiling shipped **off** (no long-premium outcome data exists in this app), and
the drop is reported as its own `vol_filtered` count rather than borrowed from
`filtered_out`.

⚠ **Measuring it turned up something bigger than the item: the floors that DO
exist are set well below where the edge starts.** Over 910 closed captured signals
on prod, mean R by entry IV rank runs 0–39 **−0.149** · 40–44 **−0.151** · 45–49
+0.015 · 55–69 +0.239 · 85–100 +0.266, win rate 24% → 81%. A floor at **45** is
the optimum on total R (cuts 94 trades worth −14.1R, keeps +199.9R) and 55 gives
the gain back. It survives controls for `entry_score` and for month, so it is not
the composite in disguise. **Raising `"0-DTE"` / `SWING` / `INCOME` to 45 is
decision 6 below**, not shipped: it is a ~10% cut to every signal the app emits.
Two weaknesses to weigh first — ORCL and UAL are 75 of the 139 trades below 50
(the rest of that band is +0.042, not negative), and the 5-point grid is not
monotone.

**B3. A deployment cap for the manual book.** Total open max loss should stay at or below a set fraction of equity. Option Alpha keeps 40–50% in cash; theoptionpremium caps open risk at 20–25%. Start at 50%, as config, so tightening is one edit.

**Shipped 2026-09-11 at 0.20, the TIGHT end rather than the suggested 50%** —
`config_paper.MAX_DEPLOYED_RISK_PCT`, enforced as a fifth rung in
`paper_concentration.concentration_reject`. Measured first: the live book had
$1,933 committed against $24,184 equity (**8.0%**), so 20% is a real ceiling with
~11 more $250 spreads of headroom rather than something that bites on day one.

The denominator is `session_start_equity` — **this is that field's first reader**;
it was written correctly and consumed by nothing. Fixed for the session on purpose:
a live-equity denominator would loosen the cap as unrealized P&L ticks up and
tighten it on a blip, so how much the book may commit would depend on the minute
you asked. ⚠ No equity SKIPS the cap rather than treating it as zero, and a
`limits` dict without the key keeps the old behaviour. ⚠ **A hand-opened
cash-secured put consumes it fast** — that structure's `max_loss_total` is the whole
strike notional, so one $15k CSP is 62% of this book and auto-entry then stops,
correctly but invisibly (a concentration breach leaves no UI trace by design).

**B4. A sector cap.** Limit both the count and the max loss per sector, with the sector map taken from `config/symbols.toml` or the sentiment service's sector reference. The scan universe's tilt to semiconductors is exactly the correlated book the playbook warns about.

**Shipped 2026-09-12** — [design](2026-09-12-sector-cap-design.md).
`MAX_POSITIONS_PER_SECTOR = 5` / `MAX_RISK_PER_SECTOR = $1,500` as a sixth rung in
`paper_concentration.concentration_reject`.

⚠ **Neither data source named above works.** `config/symbols.toml`'s `sectors` is
the SPDR ETF collection list, not a map; and the sentiment reference covered **48
of 80 watchlist symbols**, missing MU, AMAT, MRVL, INTC, TXN, ALAB, SMCI, DELL and
SPCX — *the semiconductors this item's rationale names* — with **181 of 273
historical paper positions (66%) on symbols it had never heard of**. So the map
was the deliverable: `config/sectors.toml`, 343 symbols, 80/80 watchlist coverage.

**The premise was tested and HELD**, unlike A7's: mean pairwise correlation
**0.250 within** a sector vs **0.017 across**, and 14 of the 15 most-correlated
pairs in the universe share one. Weakest where the book concentrates — IT is 30 of
74 names at only 0.240 internal correlation (IBM and TXN beside IONQ/RGTI at
0.939). Indices are a bucket rather than an exemption, measured at 0.799.

Measured impact: the driver's book once held **$21,531 across 15 IT positions**,
86% of its account in one sector. ⚠ **The driver is NOT covered** — it opens
through `open_driver_position`, not `concentration_reject` — which is defect 16
below.

**B5. An expiry-day rule for physically-settled names.** On expiration day, close (or at least flag) any short within about 1% of its strike by a set CT time; cash-settled index options are exempt. OIC notes that exercise notices are accepted until about 5:30 pm ET, so an after-hours move can assign a short that closed out of the money. Option Alpha saw about 1.2% of its contracts assigned over five years, mostly in expiration week.

**The FLAG shipped 2026-09-12; the CLOSE deliberately did not** —
[design](2026-09-12-position-awareness-design.md). ⚠ `run_manage_cycle` settles an
expiring position at intrinsic against the **15:00 CT** close and models no
after-hours leg, so the 17:30 ET exercise notice this rule defends against
**cannot occur in this simulation** — closing positions to avoid it would protect
against something the paper book cannot express. Nor is it measurable:
`signal_outcomes.settlement_underlying` is NULL in all 910 rows and both books
hold **zero `equity_lots`**, so no assignment has ever occurred here.

What shipped instead is defect 13 finally meaning something: `assignment_risk` was
**unconditionally `True` for every equity short** while the futures branch beside
it gated on moneyness all along. It is now ITM or nothing (no spot → keep the
flag: unknown moneyness must not CLEAR a risk), plus a `pinned_at_expiry` flag +
heat modifier for a physically-settled short on its strike on expiration day, with
index names exempt. The note also **stopped claiming an ex-dividend check** —
there is no ex-dividend *date* in this repo, only `dividendYield`.

**B6. Store the entry short delta.** **Shipped 2026-09-11.** An additive
nullable `paper_positions.entry_short_delta`, written by all three producers
(the captured entry cycle, `open_driver_position` off the raw scan row's
`short_delta`, and `apply_roll` off the candidate's `new_short_delta`), and moved
into the manage cycle's **base** ctx — it had been threaded into the lifecycle
branch alone, which neither book that trades ever takes. `None` keeps the old
fallback, so nothing already in either book changes. The income structures are
untouched: `loss_rules = false` leaves no delta stop to sharpen.

⚠ **Writing these tests surfaced a live defect outside the audit:**
`compute._driver_open_positions` called `paper_account_db.list_open_positions`,
which does not exist, and the `except -> []` made the driver's open-path
`max_concurrent` and `daily_risk_budget` gates measure an always-empty book. 50
degrades in 30 days on prod; nothing breached, because the decision-side
guardrails held. Fixed with an AST guard over every lazily-imported engine
attribute in `compute.py`.

**B7. Earnings awareness on open positions.** Raise Rescue heat and send a push when a position's expiration spans a report. Nothing in `rescue.py`, `signal_recommender.py` or `services/driver_svc` reads the calendar today. Option Alpha's 10-year study of 1,546 reports found misses averaged 34–38% beyond the expected move.

**Shipped 2026-09-12 as a heat MODIFIER — and ⚠ this is the fourth audit rationale
that did not survive measurement.** Over all 910 closed captured signals the split
looked decisive: *no report* +0.254 mean R at **75.6%** win against *spanned a
report* −0.032 at **16.2%**. But the earnings calendar's rows only begin
**2026-08-24**, so every earlier position was filed as "no report" and the
comparison was really June–July against August–September. Restricted to the 132
signals whose whole life sits inside coverage it **inverts**: spanned **−0.059 at
14.9%** against **−0.241 at 12.3%**.

So: a +6 modifier (the same weight as the regime tilt) that can reorder a ranked
list and **never** escalate `state`; **no push**, since a phone alert is a
standing stream with a certain cost and no measured case; and no rule change,
because the *entry* half is already covered — all 67 spanning positions would be
refused today by A1/A5's gate, and **0** such positions are open in either book.
Option Alpha's figure is about the *size* of earnings moves, which is real, so
"not reproduced here" is a reason to keep this advisory rather than to ignore it.

**B8. Size as a percent of current equity,** so the cap shrinks in a drawdown instead of staying $250 / $3,000.

**Shipped 2026-09-12 for the DRIVER, which is the whole of it.** Measured live: the
driver is down 46.6% to **$13,347**, so `per_trade_max_risk` $3,000 (its own
comment: *"~12% of the book"*) is **22.5%** and `daily_risk_budget` $12,000
(*"~half the book"*) is **89.9%**. Nothing was mis-set — the numbers stopped
meaning what they were chosen to mean, which is exactly this item.
`shared.driver_limits.scale_to_equity` resolves both as **`min(dollars, pct ×
equity)`**, so a drawn-down book tightens and a grown one can **never** loosen; the
shipped 0.12 / 0.48 are the comments' own intent and reproduce the dollar figures
at $25,000. Effective caps today: **$1,602** and **$6,406**. Scaled on BOTH driver
paths, because a decision path offering what the open path refuses is the
documented "Executed but nothing opened" failure.

⚠ **The manual book deliberately did not change**: $250 is 1.03% of $24,184 and
$2,500 is 10.3%, B3 already made its book ceiling a percentage, and floating
`MAX_RISK_PER_TRADE` would desync `scanner_engine.DEFAULT_MAX_RISK_DOLLARS` and
re-open A6's `RISK_TOO_HIGH` problem from the other end. And this does **not**
answer decision 3 — the driver's sizing appetite is still open; it only makes the
config honest about whatever percentage is chosen.

### Tier C — measure before changing the core

**C1. Record Income Window candidates** as captured signals with `scanner_type = "INCOME"`. The nightly calibration then tests the playbook's 30–45 DTE claim against the app's short-dated core on its own data. `shared.calibration.family_key` already buckets an unrecognised family on its own.

**Shipped 2026-09-11 — and the one-line version of this recipe would have been a
silent no-op.** Four things the recommendation above did not anticipate, each
found by measuring the live board rather than reading the code:

1. **The capture floor.** `capture_min` is 58 and the whole board scores
   **50.2–57.0** (measured 2026-09-11; every candidate graded *Marginal*), so
   `record_signals` would have kept **nothing, every day**. Hence
   `capture_min_income = 0` — "whatever the board offered", since `income_scan`
   already cuts below `swing_min` upstream — resolved per type by
   `signal_recorder.capture_floor`, with a new type opting in by name.
2. **Units.** `signals.entry_credit`/`entry_max_loss` are **per share** (verified:
   `entry_max_loss == width − entry_credit` exactly on prod rows) while the board
   is per-**contract** dollars, and a single-leg row has no `credit` at all.
   `compute.income_capture_row` converts; getting it wrong would be a 100× error
   in the only dataset this feature produces, and invisible, because an
   R-multiple is unitless.
3. **Shape.** A `SHORT_PUT` row carries only the normalized `legs` — no
   `short_strike`, which `_dedup_key` indexes directly, so the naive call raised.
4. **⚠ It would have made the board an auto-traded feed.** `run_entry_cycle`
   reads every open captured signal with no type filter, and most income
   candidates are ordinary spreads it would size happily. `_NO_AUTO_ENTRY_TYPES`
   refuses the type outright — decision 5 below is still open, and the page
   promises the current answer.

Also: `MANAGE_DTE` joined `_CAPTURED_CLOSE_CODES`, or a tracked income signal's
only exit would be expiry while B1's policy closes it at 21 DTE in profit — the
calibration would have measured a hold-to-expiry policy the manage cycle never
executes. ⚠ **No evidence exists yet**: the first capture is the next scheduled
income slot, and at ~5 candidates a day a useful sample is weeks away.

**C2. Wire the profit-lock ladder and trial the lifecycle on the manual book.** `[trail].ratchet_ladder` is built and tested with no caller, and `manual_paper_lifecycle_enabled` exists and defaults off. Running one book each way gives a direct comparison of "close at 50%" with "arm break-even and ratchet".

**The LADDER shipped 2026-09-12; the lifecycle trial did not** —
[design](2026-09-12-profit-lock-ladder-design.md). ⚠ The mechanism was inert
whatever the TOML said: `_locked_profit_level` has taken `trail_ladder` and
`peak_pnl_frac` since it was written and nothing ever passed them.

**No book each way was needed** — every closed captured signal carries a mark
series, so both policies were replayed against the **same real price path**. With
a 10%-of-credit slippage haircut, over 281 signals: close at +50% **+$4,788**,
break-even stop **+$8,173**, **ratchet +$8,556**. The ratchet is ahead in every
month and both scanner types, and structurally cannot increase loss exposure
(rule 3 takes `max(be_level, locked)`), so it ships on.

⚠ **The lifecycle toggle is decision 8, not a ship.** Its gap is far larger
(+$3,768) but does not survive slicing: September reversed it and 0-DTE gains
nothing from it.

**C3. Store one ATM IV per symbol per day.** The collector already fetches the chains, so this is small. A true IV rank then exists in a year; until then, label the field "Vol rank".

**Shipped 2026-09-12** — [design](2026-09-12-iv-history-capture-design.md). ⚠ **It
was not "write the thing", it was "run the thing that exists".**
`deepdive/iv_history.py` is a complete module — `record_snapshot`,
`constant_maturity_iv`, `iv_rank`, `rv_rank`, a 20-sample floor — and held **7
rows, all dated 2026-08-04**, because it is reached only from
`engine.analyze_symbol`: it filled only when somebody opened a Deep Dive report.

Moved to `shared/iv_history.py` (the `shared/earnings.py` precedent), and
`run_full_scan` now records one `cm30_iv` per symbol per scan at **zero Schwab
cost** — off the **+20..+45 DTE** chain it already fetches, which measured across
ten live symbols brackets 30 DTE every time. ⚠ The collector's chain stops at
**+7 days**, so the recommendation's "the collector already fetches the chains"
would have **clamped every reading** and stored a 7-day IV as a 30-day one.

⚠ Two more dead paths found in passing: `backfill_rv` accepted only a DataFrame
while the scanner holds the raw Schwab payload (so it wrote nothing, silently),
and `DEFAULT_DB_PATH` was a **relative** `./iv_history.db`. Both fixed.

And the relabel was five months overdue: `iv_analysis` has documented since
2026-04-19 that the field is a **variance risk premium** (current ATM IV inside
the 52-week *realized*-vol distribution), while three screens plus two manuals
still said "IV Rank". ⚠ Which means the strong B2 finding is about the **VRP
proxy** — nothing here claims a true IV rank beats it, and the snapshot
deliberately does **not** feed selection.

**C4. Book-level Greeks:** net delta, gamma, theta and vega per book, optionally beta-weighted to SPY.

**C5. Trade plan and scorecard.** Snapshot the rules in force at entry and a free-text thesis on each position. Give the manual book the win-rate and profit-factor scorecard the driver has (`driver_perf.py`).

**The SCORECARD shipped 2026-09-12** —
[design](2026-09-12-manual-scorecard-design.md). `build_scorecard` was already
pure over `(positions, snapshot)`, so `compute.manual_account_perf()` is an
accessor; the pure render builders moved to `webgui/pages/scorecard.py` now that
two pages draw them. ⚠ And it gained a new axis, `by_exit_reason`, because
replaying the ladder showed `MANUAL_CLOSE` accounts for **+$50,102** of the
captured book's reported P&L against +$11,664 for everything else, with 130 of
388 of its rows booking exactly the full credit — invisible when split by symbol
and strategy.

⚠ **The trade-plan snapshot did NOT ship, and is bigger than written.** "The
rules in force at entry" now means `[stops]` overlaid by `[structures.*]`, the
active `[trail]` ladder, `tp_frac_for`, `cut_dte`, the delta triad and the
lifecycle flag — six of which moved on 2026-09-12 — so it needs a granularity
decision of its own. And a free-text thesis implies a workflow change on a book
whose positions open automatically: there is no moment at which a human is
present to type one.

### Tier D — add strategies, cheapest first

**D1. Straddle and strangle templates:** long and short, straddle and strangle, analysis only.

**D2. An iron butterfly scanner.** Emit IC candidates with coincident shorts; the rest of the IC pipeline already carries them. It needs a per-structure profit target: Option Alpha takes 25%, or exits 5 days before expiry; TradingBlock takes 50%.

**D3. Exits for long options and debit spreads.** Move them into the account, or give the ledger a manage cycle. Give them a profit target — tastylive uses 50% on debit spreads, TradingBlock about 80% — and a time exit before the final weeks. The practitioner sources don't stop out losing debit spreads (tastylive closes them before expiry instead), so a percent-of-debit stop is an option, not a sourced rule.

**D4. Stock legs in the leg model.** This unlocks covered call, protective put and collar analysis. It could then extend to a "protect a holding" view on the real Portfolio page, as analysis only.

**D5. Calendars and diagonals end to end:** scanner, two-expiry repricing, and settlement at the front expiry. Revisit after C1.

### Don't

- **Don't adopt the ⅓ rule as a hard gate** (§6.2).
- **Don't open undefined-risk structures in paper or the driver.** That means naked calls, short straddles and short strangles (§6.3).
- **Don't build LEAPS or the PMCC outside their own design.** Both were deliberately deferred on 2026-09-05.

---

## 8. Defects and drift found along the way

Not playbook gaps, but surfaced by the audit. Items marked **(checked)** were re-read by hand.

1. Income Window positions got no mark and no exit rules, and logged an error on every manage cycle **(checked)** — fixed 2026-09-11 (A2).
2. Rescue treated a cash-secured put as call-side, and offered it no candidates, not even "Close now" **(checked)** — fixed 2026-09-11 (A3); the missing roll and wheel candidates followed in B1.
2a. **`paper_adjust.apply_roll` resolved a short put to the CALL side** — a fourth hand-written copy of the same membership test, so a single-leg roll would have been partitioned and priced on the call chain. Unreachable only because no roll candidate existed to apply, exactly like the `_close_legs` commission defect A3 found. Fixed 2026-09-11 (B1), with the seven copies of the structure sets folded into `shared/structures.py` and a test that fails on the next one.
3. ~~The income cash-secured put sells 0.28 delta against a documented 0.15–0.25 band~~ **(checked)** — **fixed 2026-09-11 (A4)**, and the measurement was worse than this line: the *realised* delta ran 0.274–0.328 across every live symbol checked, at or above the ceiling, because 0.28 was only a target on a coarse ladder.
4. The earnings gate never fires on the Market Scanner or the Strategy Finder **(checked)**. `dde98b8`, merged as `05ed539`, fixes the Market Scanner; the Strategy Finder still passes no date (A5).
5. The volatility floor binds one scan surface of four **(checked)**.
6. The width search is sized for a phantom $100,000 account **(checked)**.
7. Three commission conventions across the paper books (§A7) — ⚠ now **four**, since C1 records the income board on the board's own commission-inclusive `max_loss`. **Measured 2026-09-11:** unifying them is worth −0.019R to the calibration and reorders no bucket, so the remaining case is the operator reading a gross P&L on one screen and a net one on the next. See §A7.
8. **Built, tested, never called:**
   - the ratchet ladder;
   - `fill_model.vertical_fill`;
   - five `config_paper` constants: `STARTING_BALANCE`, `SLIPPAGE_TICKS`, `OPTION_TICK`, `ENTRY_CYCLE_MIN`, `MANAGE_CYCLE_MIN`.
   - ⚠ **and `compute.manual_analytics()`** (the manual book's equity curve +
     MAE/MFE analytics), found 2026-09-12 while shipping C5's scorecard. A
     SEPARATE unused surface from the scorecard, recorded so it is not
     rediscovered as new. That makes four instances found by this audit alone —
     the ratchet ladder (C2), the IV-history writer (C3),
     `signal_outcomes.settlement_underlying` (15a) and this.
9. **Orphaned journals:** the settle-and-render half of `daily_trade_log.py`, and the `trade_performance_db` fill journal.
10. **Stale prose:**
    - The driver scheduler's comment says its exits run on a 5-minute cycle; it is 1 minute.
    - `expire_ledger_trades` says it runs on a 5-minute tick; it is hourly plus on demand.
    - `trade_mgmt.toml` says the 50% target arms a break-even stop, which is true only for captured signals.
10a. **The driver's open-path capacity gates could never refuse anything** — `compute._driver_open_positions` called a function that does not exist (`list_open_positions` for `fetch_open_positions`) and its `except -> []` swallowed the `AttributeError`, so `max_concurrent` and `daily_risk_budget` both read an empty book. 50 degrades in 30 days on prod, nothing breached. Fixed 2026-09-11 (found while writing B6's tests), with an AST guard over every lazily-imported engine attribute in `compute.py`.
11. The driver has two halts at different thresholds, a $1,500 decision halt and a $2,500 session drawdown, with nothing tying them together.
12. The Rescue ad-hoc form lists the iron butterfly, but it is relabeled as an iron condor before it leaves the browser.
13. Rescue flags assignment risk on every equity short, regardless of moneyness or dividends.
14. Captured signals settle a 0-DTE trade the moment its DTE reaches 0, at a live mark, while both paper books hold to the 15:00 CT close.
15. Assignment has no cash-settled branch. An index cash-secured put would book shares, which is unreachable today only because the collateral check refuses full index notional in a $25,000 book.
15a. **`signal_outcomes.settlement_underlying` is written by nothing** — NULL in
    all 910 rows — so the app has no record of where any expiring position
    actually settled. Found while measuring B5, which is unmeasurable because of
    it. Its sibling `signal_marks.current_underlying` was worse (a literal `0.0`
    in all 58,895 rows) and is fixed; this one is still open.
16. **The driver's book has no concentration caps of any kind** (found while
    measuring B4). `compute.open_driver_position` re-checks structure, defined
    risk, `max_concurrent` and `daily_risk_budget`, but never
    `paper_concentration.concentration_reject` — so none of the six rungs the
    manual book now has applies to it. Measured: the driver's book has held
    **$21,531 across 15 Information Technology positions, 86% of a $25,000 account
    in one sector**, and **$15,018 across nine INDEX positions**. Its envelope is
    `config/driver.toml`, in a service that cannot import that module, so closing
    it is its own change.

---

## 9. Decisions for you

1. **Promote `dde98b8`?** It is merged on this branch as `05ed539`, with the failing test set unchanged. What remains is fast-forwarding `main` and promoting after the close.
2. ~~**Cash-secured puts and covered calls: stops, or the wheel?**~~ **Settled 2026-09-11 by B1: the wheel.** `loss_rules = false` for both structures — no money, time or delta stop — because each of those rules is inverted here rather than merely unproven, and for a covered call the money stop would be reading a P&L that covers only the option leg. What replaces the loss side is `manage_dte = 21`, which acts only on a position in profit. It is config (`[structures.*]`), so reversing it is one edit, but reversing it re-breaks the wheel.
3. **Driver sizing.** Keep the "Very Aggressive" 12%-per-trade profile, or move toward the playbook's 1–2% and the sources' 1–5%? The driver's own realized record is the evidence to weigh.
4. **How tight a deployment cap for the manual book?** 50% of equity at risk, or theoptionpremium's 20–25%?
5. **Income Window: screen or feed?** Should it stay a human-picked screen, or feed auto-entry once C1 has produced outcome data?
8. **Turn on `manual_paper_lifecycle_enabled`?** Measured 2026-09-12 (C2
   above): holding-and-ratcheting beat closing at +50% by **$3,768** over 281
   replayed trades, because 118 of the 205 that reached +50% ran on to ≥95% of
   credit. ⚠ But **September reversed it** and **0-DTE gains nothing**, and the
   sample is three months in which this book was profitable — "hold longer"
   flatters exactly that. It changes how every position in the manual book exits.
6. **Raise the IV-Rank floors to 45?** Measured 2026-09-12 (B2 above): everything
   below 45 returned **mean R −0.150 at a 25.5% win rate** over 910 closed
   captured signals, and 45 is the optimum on total R. It would cut ~10% of every
   signal the app emits — including on symbols whose premium is merely average
   rather than cheap — so it is one config edit (`config/scanner.toml`
   `[iv_rank]`) with a real cost. ⚠ 55 is measurably WORSE than 50, so this is
   not a case where the tight end wins.
