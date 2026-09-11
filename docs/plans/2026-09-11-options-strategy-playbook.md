# Options strategy playbook

**Date:** 2026-09-11 · **Status:** reference · **Companion:** [gap assessment — the app against this playbook](2026-09-11-options-strategy-gap-assessment.md)

This playbook covers the 19 strategies in the brief. For each one it gives how it is built, how it pays, and the rules for entering and leaving it. It also sets out the portfolio rules that apply to every trade.

**How claims are sourced:**
- **Every rule is attributed.** Codes in brackets such as [OA-TSR] resolve to the source list at the end.
- **Disagreements are shown, not averaged.** Where reputable sources disagree with the brief or with each other, both sides are given.
- **tastylive figures are unverified.** Its research pages no longer load (404s and redirects), so those numbers come from search-result snippets and are marked *(snippet)*.
- **Analysis written for this document** is marked *(analysis)*.

**Conventions:**
- **DTE** is calendar days to expiration.
- **Delta** is quoted as an absolute value.
- **Credit, debit and strike widths** are per share, and one contract covers 100 shares.
- **Width** is the distance between a spread's strikes.
- **R** is a trade's maximum loss.

---

## Part 1 — Rules for every trade

The rule IDs are the ones the gap assessment scores the app against.

### 1.1 Risk and sizing

**S1 — Risk 1–2% of equity on any one defined-risk trade.** The brief allows up to 5% for diversified, lower-risk income setups.
- **Option Alpha:** 1–5%, mostly 1–3%, and less is better. Nearer 0.5% for options once an account is larger [OA-TSR, OA-PSB]. Its illustration of why: at a 70% win rate, ten losses in a row is about a 1-in-169,350 event, so small sizing is what makes a streak survivable [OA-TSR].
- **theoptionpremium:** 2–5% per position, depending on account tier [TOP].

**S2 — Keep 30–50% of the account in unallocated cash.**
- **Option Alpha:** never commit more than 50–60%, and keep 40–50% idle, because margin can balloon in a volatility spike [OA-TSR].
- **theoptionpremium:** caps total open risk at 20–25% instead, a tighter measure [TOP].
- **A third-party retest of tastytrade's short-strangle formula** (SPX, 2005–2016) found 15% margin use returned +2% over 11 years. 35% lost 61%, and 70% blew up in 2011 [SJO].

**S3 — Size by maximum loss, not by premium or contract count.** Option Alpha sizes by buying-power effect: $20,000 × 2% = $400, which buys five spreads at $77.50 of risk each [OA-TSR]. See §2.5 for the arithmetic on a $25,000 book.

**S4 — Diversify across sectors; avoid correlated positions.** Mix uncorrelated sectors, industries and directions; "portfolio fit" (beta weighting) is step one of Option Alpha's entry checklist [OA-DIV, OA-7]. No source gives a numeric limit. Several single-name spreads in one sector are one bet, sliced.

### 1.2 Setup and mechanics

**M1 — Trade only liquid underlyings and options.** The brief's "bid-ask under $0.05–0.10" appears in no source found. The sourced tests are volume, open interest and quote quality:
- **Option Alpha:** the underlying trades 500–600k shares a day with deep open interest, and volume plus open interest fixes about 90% of fill problems [OA-LIQ, OA-EXE].
- **OIC:** liquidity is tight quotes plus size — volume and open interest alone are not liquidity [OIC-GI, OIC-BA].
- **tastylive** *(snippet)*: the stock trades over ~1M shares a day, each strike carries a few hundred contracts of open interest, and the option spread is a couple of cents, with caution above $0.20 [LB-LIQ].

**M2 — Sell premium at 30–45 DTE.** The window captures accelerating time decay while leaving time to manage.
- **OIC:** sellers typically target 30–45 days [OIC-APR], and decay starts in earnest about 30 days out [OIC-TH].
- **Option Alpha:** 30–45 days in high volatility and 60–90 in low [OA-7, OA-DUR].
- **OptionsPlay:** 4–6 weeks [OPR].
- **tastylive** *(snippet)*: 45 DTE had the highest average profit and profit per day in its strangle studies, and a much higher win rate than 7 DTE [MM-DTE, LB-45, LB-CAGE].

**M3 — Buy premium with time on your side.** When buying options, go 45–60+ days out at 50–60 delta [OPX], or use LEAPS, since decay concentrates in the final ~90 days [OIC-LEAPS]. tastylive *(snippet)* prefers debit spreads to outright long premium [LB-LPT]. The brief's "60–90+ DTE" is at the long end of this range.

**M4 — Use limit orders only.** Start at the mid, move about $0.05 at a time, wait 30–60 minutes, and don't chase — try again the next day [OA-EXE]. Limits reduce slippage but may not fill [OIC-BA].

### 1.3 Volatility and selection

**V1 — Sell when implied volatility is rich; buy when it is cheap.**
- **The threshold:** sell above the 50th IV percentile and consider buying below it [OA-7]. tastylive *(snippet)* uses the same split [LB-VOL]. The "above 30" variant appears in no primary source. OIC treats IV rank and percentile as context, with no threshold [OIC-CR].
- **When IV is low:** Option Alpha and tastylive lengthen duration rather than switch to buying [OA-DUR, LB-ADJ], and IV can stay low for a long time [OA-IV].

**V2 — Know which Greek is driving the trade.**
- **Short-dated trades are gamma trades.** Price moves change the position fast.
- **Long-dated trades are vega and theta trades.** They are sensitive to volatility shifts and the daily decay.

This is from the brief; see each strategy's Greek profile in Part 3.

**V3 — Don't hold short-term options through an earnings report**, except as a deliberate, small volatility-crush trade.
- **Option Alpha's 10-year study** covered 1,546 reports on 40 tickers. The stock opened inside the expected move 71% of the time and closed inside it 64%. Misses averaged 34–38% *beyond* the expected move. Short strategies won about 57% of the time, but with larger losses, and Option Alpha stopped trading earnings [OA-EARN].
- **OIC's IV-crush example:** a $105 call bought at $2.90 fell to $2.10 even though the stock rose from $100 to $106. Implied volatility fell from 80% to 30% [OIC-CR].
- **Dissent:** tastylive *(snippet)* sells into the crush [LB-EARN].

### 1.4 Management and exits

**X1 — Take profits early.** The brief: 50% of max profit for premium selling (iron condors, strangles), 25–50% for credit verticals.
- **Supporting 50%:** OptionsPlay [OPR], tastytrade's iron-condor guide [TT-IC], and tastylive's vertical-spread page, which uses it for all four vertical types [TL-vert].
- **Going further:** TradingBlock takes about **80%** on both credit and debit verticals [TB].
- **Supporting 25%:** Option Alpha takes 25% on iron butterflies, or exits 5 days before expiry [OA-IB]. tastytrade uses 25–50% on butterflies, citing pin risk [TT-BF], and tastylive *(snippet)* 25% on straddles [LB-GTM].
- **Not supported:** no source uses 25% for credit verticals.
- **Against early exits:**
  - Option Alpha's SPY put-spread backtest found a **75%** target beat 50% (Sharpe 0.83 vs 0.77, profit factor 1.34 vs 1.26) [OA-SPY].
  - A 314-trade strangle study showed managing at 50% won 90% of the time for $7,209 total, while holding won 82% of the time for $12,297 [WXC].
  - The trade-off: early exits buy a higher win rate and lower drawdowns with total return [OA-CC].

**X2 — Cut losses by a rule set before entry.** The brief: close when the loss reaches 1–2× the credit collected. This is the most contested rule in the playbook.
- **For a stop:** OptionsPlay stops when the loss equals the maximum gain — buy back at twice the credit [OPR].
- **Against stops on defined risk:** Option Alpha uses no stops on defined-risk spreads, because touching a level is about twice as likely as finishing through it. A trade with a 75% chance of expiring profitable has about a 48% chance of touching its stop. For naked positions it uses hard stops at 3–4× the credit [OA-SL1, OA-SL2].
- **The measured trade-off:**
  - Option Alpha, SPY strangles: annual return was 87% higher without stops, but drawdowns were lower with them — 13.0% versus 5.9% in one variant [OA-SLP].
  - tastylive *(snippet)*: stops cut losers by about 42% but lowered the win rate and the average P&L [LB-SL].
- **Terminology trap:** "stop at 2× the credit" can mean *buy back at twice the credit*, which is a loss equal to the credit. Or it can mean *a loss of twice the credit*. Say which.

**X3 — Manage winners; roll losers.** If a trade works early, take it. If a short strike is tested, consider rolling out in time — to the next monthly cycle — for a **net credit**.
- **The economics of a credit roll:** a $1.00 credit roll adds $100 of max profit and removes $100 of max loss per contract. Debit rolls add risk, and sometimes the right answer is to take the loss [OA-ROLL].
- **When to roll:** only if the original thesis still holds [LB-ROLL] *(snippet)*. tastytrade rolls the threatened side for extra credit [TT-IC].
- **What rolling doesn't do:** it reduces assignment risk but does not remove it [OIC-APR].

**X4 — Close or roll shorts before expiration day ends.** Pin risk and after-hours assignment are the reasons.
- **OIC's timing:** exercise notices are accepted until about 5:30 pm ET, so an after-hours move can get a short that closed out of the money assigned. Options $0.01 in the money are exercised automatically. A position closed during the day cannot be assigned [OIC-EX, OIC-AS, OIC-APR].
- **What happens if one leg is assigned:** a spread is no longer defined-risk [OIC-UA].
- **How often it happens:** Option Alpha saw about 1.2% of its contracts assigned over five years, mostly in expiration week [OA-EXP].
- **The deadline:** sources say close by the expiration-day close, not the day before [TT-SPV, TT-BF].

**X5 — Manage premium-selling trades at 21 DTE.** Not in the brief; well supported.
- **OptionsPlay** closes at 21 DTE because gamma rises [OPR].
- **tastylive** *(snippet)* found managing at 21 days improved all three strategies it tested [MM-ANAT].
- **Other exit points:** 15 DTE [OA-SPY], about 14 DTE [DTE], and 5 days before expiry for iron butterflies [OA-IB].

### 1.5 Process

**P1 — Trade a written plan.** Before execution, write the entry trigger, position size, profit target, and stop or adjustment point. Set objectives first [OIC-GS]; use a fixed entry checklist [OA-7]; build a personal, mechanical plan [TT-HOW, LB-JRN] *(snippet)*.

**P2 — Keep a journal.** Record the entry criteria, IV rank, market conditions, rationale and state of mind, and review the losers [LB-JRN] *(snippet)*.

**P3 — Separate investing from trading.** Don't fund speculative, short-term options from a long-term portfolio. This is from the brief; the sources give advice only, no data.

### 1.6 Where the sources disagree with the brief

| Brief says | Sources say |
|---|---|
| Stop at 1–2× the credit | Option Alpha: no stops on defined-risk spreads. Stops cut the size of losses but lower win rate and total return. The sourced stop (OptionsPlay) is a *loss equal to* the credit. |
| Take 25–50% on credit verticals | No source uses 25% for verticals. One backtest found 75% beat 50%. |
| Sell when IV rank > 50 (or > 30) | 50 is supported; 30 is not found in a primary source. |
| Bid-ask ≤ $0.05–0.10 | No source states it. The tests are volume, open interest and quote quality. |
| Keep 30–50% in cash | Supported (Option Alpha: 40–50%). One source is tighter: cap total open risk at 20–25%. |
| Buy options at 60–90+ DTE | Sources say 45–60+ days, or LEAPS. |
| Avoid earnings | Option Alpha agrees and stopped trading them; tastylive sells into the crush. |

---

## Part 2 — Building a credit spread

### 2.1 The arithmetic

- **Maximum loss** = (width − credit) × 100 × contracts. A $5-wide spread sold for $1.50 risks $350 per contract [TOP, ALP, OA-CVD, OIC-BPS].
- **Buying-power requirement** equals the maximum loss, net of the credit received [TT-SPV, ALP-BPS]. Some brokers margin the full width and ignore the credit [ALP-DOC].
- **Break-even** is the short strike minus the credit for a put spread, and the short strike plus the credit for a call spread.
- **The risk is defined only while both legs exist.** If one leg is assigned, the position is no longer defined-risk [OIC-UA].

### 2.2 How wide

The brief's tiers ($1–2.50 under $25k; $5 as the standard; $10+ over $75k or for $300+ stocks) are only partly supported. The one source with tiers draws the lines differently [TOP]:

| Account | Width | Positions | Risk per position |
|---|---|---|---|
| Under $25k | $2.50 | 1–2 contracts | 2–3% |
| $25k–75k | $5 | 2–4 contracts | 3–5% |
| $75k–200k | $5 and $10, by underlying price | 5–8 positions | 3–5% |
| Over $200k | $10 | 5–10 positions | 2–3% |

By stock price the same source uses $5 under $200 and $10 above [TOP]. No source mentions $1-wide spreads, the $20–50 band, or the $300 threshold.

**Wider-and-fewer versus narrower-and-more** is genuinely contested. Here is the same source's comparison at $1,000 of risk [TOP]:

| Width | Contracts | Credit | Return on capital at risk |
|---|---|---|---|
| $2.50 | 6 | $510 | 51.5% |
| $5 | 3 | $450 | 42.9% |
| $10 | 1 | $220 | 28.2% |

- **For narrower:** higher return on the capital at risk [TOP].
- **For wider:** fewer commissions — at $0.65 per contract, fees take about 3% of max profit on a four-leg $0.85 credit against 1% on $2.20. Management is also simpler, and break-evens are slightly further away [TOP]. Option Alpha prefers widening the strikes to adding contracts [OA-SPR].
- **Unsourced:** the brief's claim that wider spreads "behave like a naked option with a higher probability of profit" appears only in a snippet [LB-SPR].

### 2.3 The one-third rule

The rule: collect a credit of at least ⅓ of the width — $1.67 on a $5 spread. It is stated by OptionsPlay, theoptionpremium and a tastytrade deck [OPR, TOP, TT-BITES]. At ⅓, max loss is twice max gain, and tastylive's rule of thumb *(snippet)* puts the probability of profit near 67% [LB-POP].

**What it demands** *(analysis)*: for a vertical spread, credit ÷ width is close to the market's probability of the spread finishing in the money, averaged across its strikes. For a narrow spread, that is close to the short strike's delta. A flat-volatility Black-Scholes pass (no skew; r = 4.5%) gives:

| Spread width as % of the stock price | Short-strike delta needed to collect ⅓ (7–45 DTE, IV 18–60%) |
|---|---|
| About 1% (for example $5 on a $650 ETF) | 0.27–0.40 |
| 2.5% | 0.29–0.52 |
| 5% | 0.31–0.51, and unreachable at 7 DTE in low volatility |

At 16–20 delta short strikes and 30–45 DTE, a fairly priced spread collects only about **8–25%** of its width.

**So the ⅓ rule and the popular 16–30 delta convention can't both hold unless implied volatility is high.** That is why OptionsPlay reaches ⅓ by selling the 50-delta and buying the 25-delta, why high-probability spreads collect 9–15% [DTE], and why theoptionpremium's own $10-wide example collects 22% [TOP]. Treat ⅓ as a statement of how much risk you accept per dollar collected, not as a free rule of thumb.

### 2.4 Width and time to expiration

Longer-dated spreads can be wider, because more premium and more time are available. Shorter-dated spreads should be narrower, because gamma makes a gap through the strikes more likely to do full damage. One source offers a table [DTE]:

| DTE | Width |
|---|---|
| 45+ | $3.50–4.50 |
| 30–44 | $2.50–3.50 |
| 21–29 | $2–3 |
| 14–20 | $1.50–2.50 |
| 7–13 | $1–2 |
| 3–6 | $0.50–1.50, and don't open new trades |

It cites no backtest. Other sources don't tie width to DTE, though OptionsPlay's 21-DTE exit rests on the same gamma argument [OPR].

### 2.5 Worked example — a $25,000 book

This combines S1 and §2.1:

| Spread | Credit | Max loss per contract | Contracts at 1% ($250) | Contracts at 2% ($500) |
|---|---|---|---|---|
| $2.50 wide | $0.83 (⅓) | $167 | 1 | 2 |
| $5 wide | $1.65 (⅓) | $335 | **0 — doesn't fit** | 1 |
| $10 wide | $3.30 (⅓) | $670 | 0 | 0 |

At 1–2% risk, a $25,000 book tops out at $5-wide spreads — which is where theoptionpremium's tiers put it. A sizing rule that picks the width first and the account second will pick trades the account cannot open.

---

## Part 3 — The strategies

Each card has the same seven parts.

| Part | What it gives | Source |
|---|---|---|
| **Build** | The legs of the trade | — |
| **Payoff** | Maximum profit, maximum loss and break-even | OIC unless marked |
| **Greeks** | The signs of delta, gamma, theta and vega. Vega and theta signs are sourced; delta and gamma signs are standard arithmetic | Vega, theta: sourced |
| **Enter** | When to open it | Practitioner sources |
| **Exit** | When and how to close it | Practitioner sources |
| **Watch for** | Assignment and expiration hazards | OIC |
| **In the app** | Where the app stands | The [gap assessment](2026-09-11-options-strategy-gap-assessment.md) §1 |

**What the practitioner sources don't give:**
- **Numeric rules from the exchange sources.** OIC and ASX give no numeric rules; the numbers below come from the practitioner sites.
- **Much from Option Alpha and tastylive.** Option Alpha's strategy pages give almost no numbers. tastylive's fetched pages state fewer numbers than its well-known house rules — the "21 DTE" and "IV rank 50" figures come from research pages that no longer load.

**Source codes.** Strategy-page codes: [OIC] Options Industry Council · [ASX] ASX booklet · [OA] Option Alpha · [TL] tastylive · [TB] TradingBlock · [OS] OptionSamurai · [MO] Macroption. The per-strategy pages are listed under Sources.

**Strike notation** in the Build lines: +1 C 100 means buy one 100-strike call; −2 C 100 means sell two.

### 3.1 Single-leg strategies

#### Long call
*Bullish · debit · defined risk*
- **Build:** +1 C (strike). Out-of-the-money is cheaper and less likely to pay [OA][TL][TB].
- **Payoff:** max profit unlimited · max loss the premium · break-even strike + premium [OIC][ASX].
- **Greeks:** Δ + · Γ + · Θ − · vega +.
- **Enter:**
  - When implied volatility is low, since you are buying premium [OS][TL].
  - Buy time: longer expirations cut the decay drag [OS]. One source puts it at 45–60+ days and 50–60 delta [OPX].
  - Use LEAPS for long horizons [OIC-LEAPS].
- **Exit:**
  - No source sets a profit target or a stop for a single long option.
  - Close before the final weeks, while time value remains, because decay accelerates [TB][OS][OIC]. Use price targets or review dates [OIC].
  - To de-risk, sell a higher call to make a bull call spread, which caps the profit and cuts the max loss [OA][OS][TB].
  - Or roll out, which adds cost [OA].
- **Watch for:** in-the-money calls are exercised automatically at expiry. Exercise early only to collect a dividend [OIC].
- **In the app:**
  - Priced in the Calculator.
  - Generated by the Market Scanner's directional tab and the Strategy Finder.
  - Paper-traded in the ledger, with no automated exits.

#### Long put
*Bearish, or a hedge · debit · defined risk*
- **Build:** +1 P (strike).
- **Payoff:** max profit strike − premium (the stock at zero) · max loss the premium · break-even strike − premium [OIC][ASX].
- **Greeks:** Δ − · Γ + · Θ − · vega +.
- **Enter:**
  - On a sharp expected fall, or to hedge shares you own [OIC][ASX].
  - When implied volatility is low [TL]. TradingBlock's example runs about three months [TB].
- **Exit:**
  - No sourced target or stop. Close before expiry [TB].
  - Sell a lower put to make a bear put spread [OA].
  - Exercise only when in the money [TB][OA].
- **Watch for:** automatic exercise can leave you short stock you didn't want [OIC].
- **In the app:** as the long call.

#### Short (naked) call
*Bearish to neutral · credit · **undefined risk***
- **Build:** −1 C (strike), with no shares.
- **Payoff:** max profit the premium · max loss unlimited · break-even strike + premium [OIC][ASX].
- **Greeks:** Δ − · Γ − · Θ + · vega −.
- **Enter:**
  - High implied volatility, never cheap premium. TradingBlock warns against selling when IV is too low; OptionSamurai's example has IV rank above 80 [TB][OS].
  - About 45 DTE [TB].
  - About 5% out of the money [TB], or a break-even two to three average true ranges away [OS].
  - Expect substantial margin [TL].
- **Exit:**
  - TradingBlock closes once about 90% of the premium has decayed [TB].
  - Set alerts, for example on a 5% one-day rise [OS].
  - Roll up and out for a credit [TB][OA]. To cap the risk, buy a higher call, which makes it a bear call spread [OA].
- **Watch for:** OIC calls this the riskiest position there is.
  - Assignment is likely, especially around ex-dividend dates.
  - Notice arrives the following Monday, so you carry weekend risk [OIC].
- **In the app:**
  - Priced and generated.
  - Refused for paper trading and for the driver, as undefined risk.

#### Short put / cash-secured put
*Bullish to neutral · credit · max loss strike − premium; defined when cash-secured*
- **Build:** −1 P (strike). The cash-secured version holds cash equal to strike × 100 [OIC][OA].
- **Payoff:** max profit the premium · max loss strike − premium · break-even strike − premium [OIC][ASX].
- **Greeks:** Δ + · Γ − · Θ + · vega − (neutral to slightly negative for the cash-secured version [OIC]).
- **Enter:**
  - Elevated implied volatility [TB][TL].
  - 30–45 DTE [TB].
  - About 5% out of the money [TB], or a break-even about 1.7 average true ranges away [OS]. Choose the strike cautiously, not to maximise premium [OIC].
  - Only on a stock you would be glad to own [OS].
- **Exit:**
  - Close once about 90% of the premium has decayed [TB].
  - When tested, roll down, out, or down-and-out for a credit [TB][OA][TL].
  - Or buy a lower put to make a bull put spread [OA].
  - If assigned and the thesis still holds, the shares start the wheel [OS].
- **Watch for:** early assignment when deep in the money, and weekend risk because notice arrives Monday [OIC].
- **In the app:**
  - Generated by three scanners, including the Income Window at 30–45 DTE.
  - Paper-traded in the account. Assignment works.
  - **Nothing manages it before expiry** (assessment §3.1).

### 3.2 Income and protection (stock plus options)

#### Covered call
*Neutral to mildly bullish · credit against shares*
- **Build:** +100 shares, −1 C (out-of-the-money strike).
- **Payoff:** max profit strike − share cost + premium · max loss share cost − premium (the stock at zero) · break-even share cost − premium [OIC][ASX].
- **Greeks:** Δ + (less than the stock alone) · Γ − · Θ + · vega −.
- **Enter:**
  - High or elevated implied volatility [TL][TB].
  - About 45 DTE [TL], or 30–45 [TB].
  - Sell the most liquid call near 30 delta [TL], or 0.30–0.50 delta [TB]. Pick a strike you would be happy to sell at [OIC].
- **Exit:**
  - When the call is worth pennies — about 95% captured — close it and roll into the next ~45-DTE cycle [TB]. Roll when little extrinsic value is left [TL].
  - If the stock is flat, roll the same strike out. If it falls, roll down for a credit, but never below your break-even [TL].
  - If it rallies, roll up and out to keep the shares, or accept assignment if selling was the plan [OA][TB].
- **Watch for:** early assignment usually comes only the day before an ex-dividend date, and costs the writer the dividend [OIC][OA].
- **In the app:**
  - The Income board generates it only against shares the paper account already holds, struck at or above their cost.
  - It is paper-traded; nothing manages it before expiry.

#### Protective (married) put
*Bullish but worried about a drop · a debit, bought as insurance*
- **Build:** +100 shares, +1 P (strike below the price). A married put is bought together with the shares.
- **Payoff:** max profit unlimited, less the put's cost · max loss share cost − strike + premium · break-even share cost + premium [OIC]. ASX words the maximum loss differently; OIC's form is the standard arithmetic.
- **Greeks:** Δ + (less than the stock alone) · Γ + · Θ − · vega +.
- **Enter:**
  - When implied volatility is low; buying in high IV overpays [TB].
  - About 45 DTE within a 30–90 range [TB], or longer [TL][MO].
  - 3–5% out of the money, about 30 delta [TB].
  - Ahead of known events [TB]. You can hedge part of a position [TL].
- **Exit:**
  - It is insurance, so no target or stop.
  - Roll at about 30 DTE back out toward 90 days. After a rally, roll up to lock in gains [TB][OA].
  - Below the strike, you have three choices: exercise, sell the put, or hold [OIC].
- **Watch for:** it can trigger a constructive sale for tax purposes [OS].
- **In the app:** not supported anywhere. The leg model has no stock leg.

#### Collar
*Mildly bullish, protecting a gain · zero cost, or a small debit or credit*
- **Build:** +100 shares, +1 P (below the price), −1 C (above), same expiration.
- **Payoff:**
  - Max profit: call strike − share cost − debit (or + credit).
  - Max loss: share cost − put strike + debit (or − credit).
  - Break-even: share cost + debit (or − credit).
  - ⚠ OIC's printed max-loss line has the signs reversed; the formula above is the corrected arithmetic, and ASX's wording agrees with it.
- **Greeks:** small, because the legs largely offset [OIC][ASX].
- **Enter:**
  - Implied volatility elevated but not extreme, so a dear put is paid for by a dear call [TB].
  - 30–60 DTE [TB].
  - Both about 5% out of the money for zero cost, or a tighter 3% put and 5% call [TB].
  - It suits LEAPS [OIC]. Avoid thinly traded names [TB].
- **Exit:** manage it before expiry. Roll the call up or out, or roll the put, to keep the shares [TB][OA].
- **Watch for:** the short call can be assigned early near an ex-dividend date [OIC].
- **In the app:** not supported.

### 3.3 Vertical spreads

#### Bull call spread (debit)
*Moderately bullish · debit · defined risk*
- **Build:** +1 C K1, −1 C K2 (K2 > K1), same expiration.
- **Payoff:** max profit K2 − K1 − debit · max loss the debit · break-even K1 + debit [OIC][ASX].
- **Greeks:** Δ + · Θ − (less than a long call) · vega slight [OIC].
- **Enter:**
  - Implied volatility low and expected to rise [TB][TL].
  - One to two months out; avoid entries around 4 DTE [TB].
  - Long 0.50–0.60 delta, short 0.20–0.35 [TB]. Or tastylive's version: buy in the money and sell at or out of the money, so the short leg carries more time value and the probability of profit exceeds 50% [TL].
  - Wider costs more and pays more [OA].
- **Exit:**
  - Take 50% of max profit [TL]; TradingBlock takes about 80% with about two weeks left [TB].
  - No stop: losing debit spreads aren't defended, only closed before expiry [TL].
  - Roll the short leg up or out [TB][OA].
- **Watch for:** early assignment of the short call around ex-dividend dates, and pin risk near K2 at expiry [OIC].
- **In the app:**
  - Generated only by the Strategy Finder (buy 0.60 delta, sell 0.30).
  - Paper-traded in the ledger, with no automated exits.

#### Bear call spread (credit)
*Bearish to neutral · credit · defined risk*
- **Build:** −1 C K1, +1 C K2 (K2 > K1).
- **Payoff:** max profit the credit · max loss (K2 − K1) − credit · break-even K1 + credit [OIC].
- **Greeks:** Δ − · Γ − · Θ + · vega −.
- **Enter:**
  - IV rank at or above 30, higher preferred [TB][TL].
  - 35–45 DTE [TB].
  - Short 0.30–0.40 delta, long 0.10–0.20 [TB].
  - Aim for about ⅓ of the width in credit [OPR][TOP]. §2.3 explains what that implies for the strikes.
- **Exit:**
  - Take 50% [TL][OPR]; TradingBlock takes about 80% [TB].
  - Stop when the loss equals the credit, which means buying back at twice the credit [OPR].
  - Manage at 21 DTE [OPR].
  - Roll out for a credit, and never let it go fully in the money [TL][OA]. To defend, add a bull put spread to make an iron condor [OA].
- **Watch for:**
  - Early assignment near ex-dividend dates, with notice arriving Monday.
  - If the price sits between the strikes at expiry, buy the short back [OIC][OA][TB].
- **In the app:** fully covered — generated, paper-traded, managed, repairable, and driver-eligible.

#### Bull put spread (credit)
*Bullish to neutral · credit · defined risk*
- **Build:** −1 P K2, +1 P K1 (K1 < K2).
- **Payoff:** max profit the credit · max loss (K2 − K1) − credit · break-even K2 − credit [OIC].
- **Greeks:** Δ + · Γ − · Θ + · vega −.
- **Enter:**
  - IV rank above 30, or IV of 30% or more, ideally about to fall [TB].
  - 35–45 DTE [TB].
  - Short 0.25–0.45 delta (about 4–5% out of the money), long 0.10–0.15 [TB].
  - Aim for about ⅓ of the width in credit [OPR][TOP].
- **Exit:**
  - Take 50% [TL][OPR]; TradingBlock takes about 80% [TB].
  - The stop is contested:
    - OptionsPlay stops when the loss equals the credit [OPR].
    - TradingBlock gives no stop [TB].
    - Option Alpha argues against stops on defined-risk spreads [OA-SL1].
  - Manage at 21 DTE [OPR].
  - Roll out for a credit, or convert to an iron condor or iron butterfly when challenged [OA][TL].
- **Watch for:**
  - Early assignment when deep in the money, and pin risk near K2 [OIC].
  - Buy back an in-the-money short put near expiry [TB].
- **In the app:** fully covered.

#### Bear put spread (debit)
*Moderately bearish · debit · defined risk*
- **Build:** +1 P K2, −1 P K1 (K1 < K2).
- **Payoff:** max profit (K2 − K1) − debit · max loss the debit · break-even K2 − debit [OIC][ASX].
- **Greeks:** Δ − · Θ − (less than a long put) · vega slight.
- **Enter:**
  - Implied volatility low to moderate and expected to rise [TB].
  - About six weeks out [TB].
  - Long 0.35–0.60 delta, short 0.15–0.35 [TB]. Or buy in the money and sell at or out of the money [TL].
  - The probability of profit falls as the spread widens: about 40% at 3 points for a $1 debit, about 15% at 10 points [TB].
- **Exit:**
  - Take 50% [TL]; TradingBlock takes about 80% [TB].
  - Losers aren't defended [TL]; close before expiry [TB].
- **Watch for:** early assignment of the short put when deep in the money [OIC].
- **In the app:**
  - Generated only by the Strategy Finder.
  - Paper-traded in the ledger, with no automated exits.

### 3.4 Volatility and neutral strategies

#### Long straddle
*A big move either way · debit*
- **Build:** +1 C K, +1 P K, both at the money, same expiration.
- **Payoff:** max profit unlimited upward and large downward · max loss both premiums, with the price at the strike · break-evens strike ± total premium [OIC][ASX].
- **Greeks:** Δ ≈ 0 · Γ + · Θ −, accelerating · vega +. OIC calls theta and vega both "extremely important".
- **Enter:**
  - Implied volatility low and expected to rise [TB][TL].
  - 30–45 DTE, never same-day [TB].
  - Each leg 0.45–0.55 delta [TB].
  - Ahead of a catalyst [OA][TB].
  - Keep combined theta under 0.20, or no more than 2% of the premium [TB].
- **Exit:**
  - Take a 25–50% gain on the premium [TB].
  - Exit on an IV jump even without a price move [TB].
  - Cut it if the stock stalls and IV falls [TB].
  - Never hold into expiration day [TB].
- **Watch for:** no assignment risk, but one leg may be exercised automatically at expiry [OIC].
- **In the app:** priced leg by leg in the Calculator only. There is no template for it.

#### Short straddle
*Neutral, expecting volatility to fall · credit · **undefined risk***
- **Build:** −1 C K, −1 P K, at the money.
- **Payoff:** max profit both premiums, with the price at the strike · max loss unlimited upward, and strike − premiums downward · break-evens strike ± total premium [OIC][ASX].
- **Greeks:** Δ ≈ 0 · Γ − · Θ + · vega −.
- **Enter:**
  - IV rank above 50; avoid below 25 [TB].
  - About 45 DTE [TL], or 30–45 [TB].
  - Avoid weeks with a Fed meeting or a major macro release [OS].
- **Exit:**
  - Take 25–50% of max profit [TB]. tastylive takes 25% on straddles *(snippet)* [LB-GTM].
  - Stop when the loss nears twice the credit [TB] — the only numeric stop in the practitioner sources.
  - Close or roll at 21–25 DTE [TB].
  - Roll the tested side, or buy one wing to make a broken-wing butterfly [OA][TB].
- **Watch for:**
  - Either leg can be assigned at any time, and both can be near the strike at expiry.
  - Notice arrives Monday, so you carry weekend risk [OIC].
- **In the app:** priced leg by leg only.

#### Long strangle
*A big move either way · debit; cheaper than a straddle, but needs a bigger move*
- **Build:** +1 C (above the price), +1 P (below).
- **Payoff:** max loss both premiums, with the price between the strikes · break-evens call strike + premium and put strike − premium [OIC].
- **Greeks:** Δ ≈ 0 · Γ + · Θ − · vega +.
- **Enter:**
  - Implied volatility low — TradingBlock says only when it's extremely low — and expected to rise [TB][TL][OS].
  - 30–45 DTE [TB].
  - 0.20–0.30 delta on each side [TB].
  - Ahead of a catalyst [TB][OS].
- **Exit:**
  - Take about a 50% gain on the premium [TB].
  - Exit at least a week before expiry [TB].
  - Take off one side if you expect a reversal [TB].
- **In the app:** priced leg by leg only.

#### Short strangle
*Neutral · credit · **undefined risk***
- **Build:** −1 C (above the price), −1 P (below), roughly equidistant from it.
- **Payoff:** max profit both premiums, with the price between the strikes · max loss unlimited upward, and substantial downward · break-evens call strike + premium and put strike − premium [OIC][ASX].
- **Greeks:** Δ ≈ 0 · Γ − · Θ + · vega −. A volatility spike can raise the margin required [OIC].
- **Enter:**
  - High implied volatility, expected to fall [TL][TB][OS].
  - About 45 DTE [TL][TB].
  - About 16 delta on each side, roughly a 68% probability of profit [TB].
  - Avoid earnings [OS]. European-style index options remove early-assignment risk [TB].
- **Exit:**
  - Take 50% of max profit [TL][TB].
  - Close at least 7 days before expiry, and watch it closely inside 14 DTE [TB].
  - When one side is breached, roll the untested side closer [TL]. Buy back the safe leg once it trades at $0.10 or less [TB].
  - Or add wings to make an iron condor [OS].
  - On stops, Option Alpha's SPY study found annual returns 87% higher without them, but drawdowns lower with them [OA-SLP].
- **In the app:** priced leg by leg only.

#### Iron condor
*Neutral, expecting a range · credit · defined risk*
- **Build:** +1 P K1, −1 P K2, −1 C K3, +1 C K4, with equal widths. It is a bull put spread plus a bear call spread.
- **Payoff:** max profit the credit · max loss width − credit · break-evens K3 + credit and K2 − credit [OIC].
- **Greeks:** Δ ≈ 0 · Γ − · Θ + · vega −.
- **Enter:**
  - High implied volatility [TL][TB][OS].
  - 30–45 DTE [TB].
  - Shorts at 16–25 delta, longs at 5–10 [TB].
  - Collect about ⅓ of the wing width, roughly a 67% probability of profit [TL].
  - OptionSamurai's screens look for a weak trend (low ADX) and a Bollinger squeeze [OS].
- **Exit:**
  - Take 50% of max profit [TL][TB][TT-IC].
  - Roll the untested side closer for a credit — as far as the tested short strike, which turns it into an iron butterfly [TL][OA]. Or roll the whole position out [OA].
  - Buy back if the price is near a short strike on expiry day [TB].
- **Naming:** OIC files these legs as "Short Condor (Iron Condor)"; ASX files the same legs under "Long Condor". Identify the trade by its legs.
- **In the app:** fully covered.

#### Iron butterfly
*Neutral, expecting a pin · credit · defined risk*
- **Build:** −1 C K and −1 P K at the money (the body), +1 C and +1 P at equal distances outside (the wings).
- **Payoff:** max profit the credit, with the price at the body · max loss wing width − credit · break-evens body ± credit [OIC].
- **Greeks:** Δ ≈ 0 · Γ −, strongest at the body · Θ + · vega −.
- **Enter:**
  - Elevated implied volatility [TB][OA][TL].
  - 30–45 DTE [TB]. Option Alpha's platform users mostly run it same-day.
  - Shorts at about 0.50 delta, wings at 0.20–0.30 [TB].
- **Exit:**
  - Take **25%** of max profit, or exit 5 days before expiry [OA-IB]. TradingBlock takes 50% [TB].
  - Don't hold through expiry, because of gamma [TB].
  - Roll the challenged side toward the price for a credit [OA].
- **Naming:** OIC's "short iron butterfly" has the same legs as ASX's "long butterfly" construction.
- **In the app:**
  - Priced in the Calculator. Not generated, not paper-traded.
  - Its structure is an iron condor with coincident short strikes (assessment §1).

#### Butterfly (long, 1-2-1)
*A pin at a target price · debit · defined risk*
- **Build:** +1 K1, −2 K2, +1 K3, all calls or all puts, equally spaced, same expiration.
- **Payoff:** max profit (K3 − K2) − debit, with the price at the body · max loss the debit · break-evens K1 + debit and K3 − debit [OIC][ASX].
- **Greeks:** Δ ≈ 0 · Θ + when the body is at the money, − when it is away from it [OIC] · vega slightly −.
- **Enter:**
  - IV rank above 30–40, because the spread is cheaper then; below 20 is riskier [TB][TL].
  - About 30 DTE [TB].
  - Put the body at your expected price [OA][TB].
- **Exit:**
  - Take 25–50% of max profit [TL][TT-BF].
  - Losses aren't managed [TL].
  - Close before expiry: OIC rates the expiration risk at the body "extremely high", because you can't know which short legs will be assigned [OIC][OA].
- **In the app:**
  - Priced in the Calculator.
  - The Rescue ad-hoc form accepts the long version.
  - Nothing else.

#### Calendar (time) spread
*Neutral near-term · debit · the risk is roughly the debit*
- **Build:** −1 near-term option, +1 longer-term option, same strike. Different strikes make it a diagonal.
- **Payoff:** max loss the debit, when the legs converge in value.
  - Max profit comes at the near expiration with the price at the strike. Its size depends on what the far leg is still worth, so it can't be fixed in advance.
  - There is no simple break-even [OIC].
- **Greeks:** Δ ≈ 0 · Θ + while both legs are open · vega + (strongly) [OIC] · Γ −.
- **Enter:**
  - The lowest implied-volatility environments. It profits if IV rises while the price stays near the strike [TL][TB].
  - Strike at or near the current price [TL][TB].
  - Expiration pairs vary by source: tastylive about 45/90 DTE, TradingBlock 7–9/30 [TL][TB].
  - Prefer European-style SPX or NDX options to avoid early assignment [TB].
- **Exit:**
  - Take 10–25% of the debit [TL].
  - Close 5–10 days before the short expiration, or if IV drops sharply [TB].
  - It usually isn't managed [TL]. If the stock moves away, buy back the short and sell a new one nearer the price [OA].
  - OIC describes rolling it: keep selling a new near-term option each cycle [OIC].
- **In the app:** priced in the Calculator and Simulator, with each leg at its own expiration. Nothing else.

---

## Part 4 — Beyond the list

The linked catalogs list about 50 more structures (OIC 48 in total, Option Alpha 36, Macroption about 70). These are the ones with rules worth carrying.

| Strategy | Build | Use | Rules the sources give | In the app |
|---|---|---|---|---|
| **Diagonal (debit)** | Long later-dated at- or in-the-money option; short nearer, out-of-the-money option at another strike | Directional plus time decay | Debit no more than 75% of the width; take 25–50%; roll the short out or closer [TL]. Long 0.55–0.65 delta, short 0.20–0.35, short leg inside 45 DTE, take 50% [TB]. | Calculator template; analysis only |
| **Poor man's covered call** | Long deep in-the-money LEAPS call plus a short out-of-the-money call | A covered call on less capital | Debit no more than 75% of the width; the short's extrinsic value at least the long's [TL]. Long above 0.75 delta (avoid above 0.90), short 0.15–0.30, take 50–75% [TB]. | Deliberately not built |
| **LEAPS** | Long option more than 12 months out | Long-horizon directional | Buy in low IV and avoid IV rank above 50; 0.30–0.60 delta; take about 50%; exit before the last 45 days [TB] | Deliberately not built |
| **Front ratio spread** | Long 1, short 2 further out | Directional to a target | High IV; enter for a credit; short strike at the target; take 25–50% [TL] | Buildable as custom legs |
| **Backspread** | Short 1, long 2 further out | A big move; long volatility | Usually for a credit; the worst case is at the long strike [OA] | Not supported |
| **Broken-wing butterfly** | Narrow debit spread plus wider credit spread sharing a short strike | Neutral to directional; high IV | Always for a credit; take 50% (25% as an alternative); defend by closing the long spread and rolling the short spread out for a credit [TL] | Rescue suggestion only, never an opening trade |
| **Jade lizard** | Short out-of-the-money put plus short out-of-the-money call spread | Neutral to bullish; high IV | A total credit larger than the call spread's width leaves no upside risk; roll out when tested [TL] | Not supported |
| **Reverse iron condor / iron butterfly** | Debit spreads outside the range | Breakout; long volatility | Low IV, 30–45 DTE, longs at 0.20–0.30 delta, take about 50% [TB]; exit before expiry [OA] | Not supported |
| **Strap / strip** | 2 calls + 1 put / 2 puts + 1 call | Volatility with a directional tilt | Usually at the money; exit before expiry [OA] | Buildable as custom legs |
| **Call / put condor** | All-call or all-put condor | Range | Overview only [MO] | Calculator templates |
| **Covered put** | Short 100 shares plus a short out-of-the-money put | Moderately bearish | Put at 0.15–0.25 delta, IV rank above 50, 30–60 DTE [TB] | Not supported |
| **Synthetic stock / risk reversal** | Long call plus short put at one strike, or the reverse | Stock-like exposure | Payoff only [MO]. Option Alpha's "Reversal" is a stock hedge, not the market-standard risk reversal | Buildable as custom legs |
| **Box spread** | Bull call spread plus bear put spread at the same strikes | Financing | Commissions, carry and early assignment can erase the edge [MO] | Not supported |

OIC's other hub entries include index calls and puts, which are cash-settled with a $100 multiplier. The rest are the cash-backed call, covered ratio spread, covered strangle, stock repair, double bull and double bear spreads, and the synthetic long put.

---

## Sources

### The brief's links — what could be read

| Link | Result |
|---|---|
| [OIC — all strategies](https://www.optionseducation.org/strategies/all-strategies-en) | Read; all 48 strategy pages |
| [Macroption — all option strategies](https://www.macroption.com/all-option-strategies/) | Read; core pages in full, many advanced pages overview-only or empty |
| [Option Alpha — options strategies](https://optionalpha.com/options-strategies) | Read; 34 strategy pages, whose only numbers are platform usage statistics |
| [Investopedia — options strategies](https://www.investopedia.com/trading/options-strategies/) | **Not read** — the site blocks Anthropic's crawler |
| [OptionSamurai](https://optionsamurai.com/blog/option-strategies/) | Read |
| [Yahoo Finance](https://finance.yahoo.com/personal-finance/investing/article/options-trading-strategies-163751717.html) | Read (Anzél Killian, 12 Aug 2026; nine strategies, no numeric rules) |
| [tastylive — 10 strategies](https://www.tastylive.com/concepts-strategies/10-options-strategies-every-trader-should-know) | Read, with 23 linked concept pages. Its research pages return 404, so their figures are snippets |
| [NCFE — Option strategies (PDF)](https://ncfe.org.in/wp-content/uploads/2023/12/Option-strategies.pdf) | **Not read** — the server's TLS certificate chain is incomplete. A desktop browser will likely open it |
| [TradingBlock](https://www.tradingblock.com/option-strategies) | Read; the most numeric source |
| [ASX — Understanding options strategies (PDF)](https://www.asx.com.au/content/dam/asx/investors/investment-options/options/understanding-options-strategies.pdf) | Read (2011 edition; 26 strategies, no calendar) |
| [theoptionpremium — credit spread width](https://www.theoptionpremium.com/p/credit-spread-width-2-5-10-wide) | Read |
| [OptionsPlay — credit spread performance report](https://www.optionsplay.com/blogs/optionsplay-credit-spread-performance-report) | Read. It describes a method and publishes **no performance data** |
| [daystoexpiry — width by DTE](https://www.daystoexpiry.com/blog/credit-spread-width-dte) | Read |
| [Option Alpha — credit vs debit spreads](https://optionalpha.com/learn/credit-spreads-vs-debit-spreads) | Read |
| [Alpaca — credit spreads](https://alpaca.markets/learn/credit-spreads) | Read |
| YouTube: [fD0rtqO58Dc](https://www.youtube.com/watch?v=fD0rtqO58Dc) (Options Trading IQ), [cWxHG5843oc](https://www.youtube.com/watch?v=cWxHG5843oc) (Barchart), [GQ-bT5xoULs](https://www.youtube.com/watch?v=GQ-bT5xoULs) (dtoptions), [5BMMrfBtA_c](https://www.youtube.com/watch?v=5BMMrfBtA_c) (The Cashflow Academy), [-x0dovGDviw](https://www.youtube.com/watch?v=-x0dovGDviw) (tastylive) | Title and channel only. With no transcript, their content is **unverified** and nothing here rests on them |

### Research pages cited by code

- **TOP** — [theoptionpremium, credit spread width](https://www.theoptionpremium.com/p/credit-spread-width-2-5-10-wide)
- **OPR** — [OptionsPlay report](https://www.optionsplay.com/blogs/optionsplay-credit-spread-performance-report)
- **OPX** — [OptionsPlay, expirations and strikes](https://www.optionsplay.com/blogs/optimal-expiration-dates-and-strike-prices)
- **DTE** — [daystoexpiry](https://www.daystoexpiry.com/blog/credit-spread-width-dte)
- **ALP / ALP-BPS / ALP-DOC** — [Alpaca credit spreads](https://alpaca.markets/learn/credit-spreads) · [bull put spread](https://alpaca.markets/learn/bull-put-spread) · [level-3 margin docs](https://docs.alpaca.markets/docs/options-level-3-trading)
- **Option Alpha:**
  - **OA-TSR** [trade size and capital reserves](https://optionalpha.com/lessons/trade-size-capital-reserves)
  - **OA-PSB** [position sizing](https://optionalpha.com/blog/position-sizing-what-everybody-ought-to-know)
  - **OA-7** [7-step entry checklist](https://optionalpha.com/members/video-tutorials/entries-exits/7-step-entry-checklist)
  - **OA-DUR** [trading timeline](https://optionalpha.com/lessons/trading-timeline-duration)
  - **OA-SPY** [SPY put-spread backtest](https://optionalpha.com/blog/spy-put-credit-spread-backtest)
  - **OA-SLP** [stop-loss strategies](https://optionalpha.com/podcast/stop-loss-strategies-for-options)
  - **OA-SL1** [using stop losses](https://optionalpha.com/lessons/using-stop-losses)
  - **OA-SL2** [smarter stop-loss orders](https://optionalpha.com/lessons/applying-smarter-stop-loss-orders)
  - **OA-CC** [covered calls](https://optionalpha.com/podcast/covered-calls)
  - **OA-EARN** [earnings study](https://optionalpha.com/podcast/we-stopped-trading-earnings-after-we-saw-new-research)
  - **OA-ROLL** [rolling](https://optionalpha.com/learn/rolling-options)
  - **OA-DIV** [uncorrelated sectors](https://optionalpha.com/lessons/uncorrelated-industriessectors)
  - **OA-IB** [iron butterfly](https://optionalpha.com/lessons/iron-butterfly)
  - **OA-SPR** [option spreads](https://optionalpha.com/podcast/option-spreads)
  - **OA-EXE** [execution](https://optionalpha.com/podcast/how-to-execute-and-fill-options-trades)
  - **OA-LIQ** [liquidity](https://optionalpha.com/lessons/strong-liquidity-examples)
  - **OA-EXP** [expiration and assignment](https://optionalpha.com/lessons/options-expiration-assignment)
  - **OA-IV** [implied volatility](https://optionalpha.com/learn/implied-volatility)
  - **OA-CVD** [credit vs debit spreads](https://optionalpha.com/learn/credit-spreads-vs-debit-spreads)
- **OIC:**
  - **OIC-APR** [April office hours](https://www.optionseducation.org/news/april-office-hours-faqs-options-strategy-time-decay-and-market-mechanics)
  - **OIC-EX** [exercise FAQ](https://www.optionseducation.org/referencelibrary/faq/options-exercise)
  - **OIC-AS** [assignment FAQ](https://www.optionseducation.org/referencelibrary/faq/options-assignment)
  - **OIC-BPS** [bull put spread](https://www.optionseducation.org/strategies/all-strategies/bull-put-spread-credit-put-spread)
  - **OIC-UA** [understanding assignment](https://www.optionseducation.org/news/trading-options-understanding-assignment)
  - **OIC-CR** [the crush is real](https://www.optionseducation.org/news/the-crush-is-real)
  - **OIC-LEAPS** [LEAPS time erosion](https://www.optionseducation.org/optionsoverview/leaps-time-erosion-versus-delta-effect)
  - **OIC-TH** [theta](https://www.optionseducation.org/advancedconcepts/theta)
  - **OIC-GI** [general information FAQ](https://www.optionseducation.org/referencelibrary/faq/general-information)
  - **OIC-BA** [bid and ask](https://www.optionseducation.org/news/understanding-the-bid-and-ask-prices-for-options)
  - **OIC-GS** [getting started](https://www.optionseducation.org/optionsoverview/getting-started-with-options)
- **tastytrade:**
  - **TT-IC** [iron condor](https://tastytrade.com/learn/trading-products/options/iron-condor/)
  - **TT-BF** [butterfly](https://tastytrade.com/learn/trading-products/options/butterfly-spread/)
  - **TT-SPV** [short put vertical](https://tastytrade.com/learn/options/short-put-vertical-spread/)
  - **TT-HOW** [how to trade options](https://tastytrade.com/learn/trading-products/options/how-to-trade-options/)
  - **TT-BITES** [vertical spreads deck](https://www.slideshare.net/slideshow/tastybites-verticalspreads/65335149)
  - **TL-vert** [tastylive vertical spread](https://www.tastylive.com/concepts-strategies/vertical-spread)
- ***(snippet)* — tastylive research, known only from search results.** The pages return 404 or redirect:
  - **MM-DTE** [days-to-expiration time frames](https://www.tastytrade.com/tt/shows/market-measures/episodes/days-to-expiration-time-frames-05-01-2015)
  - **MM-ANAT** [anatomy of a strangle](https://www.tastytrade.com/shows/market-measures/episodes/the-anatomy-of-a-strangle-put-and-call-03-28-2019)
  - Luckbox articles, all at luckboxmagazine.com/techniques/ or /trades/:
    - **LB-45** the-magic-of-45-optimal-short-options-trade-duration
    - **LB-CAGE** cage-match-weekly-vs-monthly-options
    - **LB-SL** stop-losses
    - **LB-GTM** good-trade-management-pays-for-itself
    - **LB-LIQ** how-much-stock-liquidity-is-enough-your-handy-reference-guide
    - **LB-POP** probability-of-profit
    - **LB-ROLL** rolling-options
    - **LB-EARN** the-earnings-trade-optimal-timing-for-options-sales
    - **LB-LPT** avoiding-the-long-premium-trap
    - **LB-SPR** spreading-for-risk-reduction
    - **LB-VOL** trading-options-using-volatility-metrics
    - **LB-ADJ** adjusting-duration-in-high-volatility-markets
    - **LB-JRN** trading-around-uncertainty
- **Third party:** **SJO** [sjoptions — does tastytrade work](https://www.sjoptions.com/does-tastytrade-work/) · **WXC** a blog summary of a 314-trade tastytrade strangle study.

### Per-strategy pages

**Base URLs:**

| Code | Base URL |
|---|---|
| OIC | `https://www.optionseducation.org/strategies/all-strategies/<slug>` |
| OA | `https://optionalpha.com/strategies/<slug>` |
| TL | `https://www.tastylive.com/concepts-strategies/<slug>` |
| TB | `https://www.tradingblock.com/strategies/<slug>` |
| OS | `https://optionsamurai.com/blog/<slug>/` |
| MO | `https://www.macroption.com/<slug>/` |

The ASX column gives the strategy number in the 2011 booklet.

| Strategy | OIC | OA | TL | TB | OS | MO | ASX |
|---|---|---|---|---|---|---|---|
| Long call | long-call | long-call | hub | long-call-options-strategy | long-call-options-strategy | long-call | #1 |
| Long put | long-put | long-put | hub | long-put-option | put-options-strategy | long-put | #7 |
| Short call | naked-call-uncovered-call-short-call | short-call | short-call | short-call | naked-call-options | short-call | #8 |
| Short put / cash-secured put | naked-put-uncovered-put-short-put · cash-secured-put | short-put | cash-secured-put | short-put | naked-put-options | short-put | #2 |
| Covered call | covered-call-buy-write | covered-call | covered-call | covered-call-options | covered-call-strategy | covered-call | #23 |
| Protective put | protective-put-married-put | protective-put | protective-put | protective-put | protective-put-strategy | protective-put | #24 |
| Collar | collar-protective-collar | collar-strategy | hub | collar | — | collar | #25 |
| Bull call spread | bull-call-spread-debit-call-spread | bull-call-debit-spread | bull-call-spread | bull-call-spread | bull-call-spread-strategy | bull-call-spread | #5 |
| Bear call spread | bear-call-spread-credit-call-spread | bear-call-credit-spread | bear-call-spread | bear-call-spread | — | bear-call-spread | #11 |
| Bull put spread | bull-put-spread-credit-put-spread | bull-put-credit-spread | bull-put-spread | bull-put-spread | — | bull-put-spread | #5 |
| Bear put spread | bear-put-spread | bear-put-debit-spread | bear-put-spread | bear-put-spread | bear-put-spread-strategy | bear-put-spread | #11 |
| Long straddle | long-straddle | long-straddle | straddle | long-straddle | long-straddle-strategy | long-straddle | #19 |
| Short straddle | short-straddle | short-straddle | straddle | short-straddle | short-straddle-strategy | short-straddle | #13 |
| Long strangle | long-strangle-long-combination | long-strangle | strangle | long-strangle | long-strangle | long-strangle | #20 |
| Short strangle | short-strangle | short-strangle | strangle | short-strangle | short-strangle | short-strangle | #14 |
| Iron condor | short-condor | iron-condor | iron-condor | short-iron-condor | iron-condor-strategy | iron-condor | #16 |
| Iron butterfly | short-iron-butterfly | iron-butterfly | iron-butterfly | iron-butterfly | — | iron-butterfly | #15 |
| Butterfly | long-call-butterfly · long-put-butterfly | call-butterfly · put-butterfly | long-butterfly-spread | butterfly | — | long-call-butterfly · long-put-butterfly | #15 |
| Calendar | long-call-calendar-spread-call-horizontal · long-put-calendar-spread-put-horizontal | call-calendar-spread · put-calendar-spread | calendar-spread | calendar-spread | — | calendar-spreads | — |
