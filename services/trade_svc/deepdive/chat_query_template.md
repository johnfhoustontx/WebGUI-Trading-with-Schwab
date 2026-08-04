<!--
=============================================================================
 EquityDeepDive - Chat Query Template
 Version: 1.0.0   Last Updated: 2026-08-03

 HOW TO USE THIS FILE (this comment block is stripped from generated files)

 Option A - automatic:
     python make_chat_prompt.py reports\OKLO_deepdive_20260803_143000.json
   Writes reports\OKLO_chatquery_<stamp>.md with your data already injected.
   Drag that file into a chat. Done.

 Option B - manual:
   Copy everything below the comment block, replace the
   {{QUANT_DATA}} placeholder with the console output or JSON from
   equity_deep_dive.py, and paste into a chat.

 Edit the prompt freely - it is meant to encode YOUR priors, not mine.
=============================================================================
-->

# Task: Equity Trade Note for {{SYMBOL}}

You are a senior analyst writing a trade note for a quantitative trader who
builds his own execution systems. He wants falsifiable structure, not
encouragement. Today's date is {{TODAY}}.

## Step 1 — Research (use web search)

Search the web for the current state of **{{SYMBOL}}**. Find:

1. **News in the last 90 days**, most recent first — what happened, and when.
2. **Upcoming catalysts with dates** — earnings, regulatory or product
   milestones, lockup expiries, investor days, index events.
3. **Recent analyst actions** — firm, rating, price target, date, and whether
   that's an upgrade, downgrade, or initiation.
4. **Fundamentals a price feed can't show** — cash position, burn rate,
   dilution and ATM activity, share count trend, guidance, backlog or bookings.
5. **Sector context** — what comparable names are doing and why.
6. **The bear case as its actual proponents state it** — not a strawman.

Search rules:
- Attach a date to every fact you report. **Stale news presented as current is
  the single most likely way this analysis goes wrong.**
- Name the source for every claim.
- Never state a number you did not find in a source.
- Flag explicitly anything you tried to verify and couldn't.

## Step 2 — The quantitative data

Everything below was computed from a live broker market-data feed. It is
**authoritative** for all price, volatility, technical and positioning figures.

```
{{QUANT_DATA}}
```

## Step 3 — Rules you must follow

**On numbers:**
- Every price, moving average, IV, IV rank, realized vol, implied move, open
  interest, short interest and technical figure comes from the data block above,
  **verbatim**. Do not invent, re-round, or estimate them.
- Every news item, fundamental, date and analyst target comes from your search,
  **with the source named**.
- If a figure is in neither, you may not state it. Say what's missing instead.
- If the data block and your research **conflict**, say so explicitly and
  explain which you weight higher and why.
- If IV rank is marked NOT YET AVAILABLE in the data, do **not** infer one from
  the absolute IV level. Say it's unavailable and reason from the IV/RV ratio
  and RV rank instead.

**On the recommendation:**
- State the bull case and the bear case in full, whatever your direction.
- Your **vehicle must follow from the volatility regime** in the data. Don't
  recommend buying premium at high IV rank, or selling it at low IV rank,
  unless you explicitly justify why the usual logic doesn't apply here.
- Give a specific, **price-based invalidation level** — the price at which the
  thesis is simply wrong. Not a risk-management stop; the level that falsifies
  your reasoning. State why that level and not another.
- Express **sizing in ATR multiples** and distance to invalidation. Never in
  dollars or share counts.
- **Conviction is 1–5.** Reserve 4–5 for cases where the quantitative and
  qualitative pictures agree. If they conflict, conviction is 2 or lower and
  you say why.
- **"No trade" and "wait for X" are legitimate answers** and are often correct.
  Do not manufacture a trade because one was requested.

Never express certainty you don't have. A trader acting on false confidence
loses money; a trader acting on calibrated uncertainty sizes correctly.

## Step 4 — Output format

Write in markdown, in this order:

### Headline
One sentence capturing the situation.

### Summary
Three to five sentences.

### What the data says
Trend and structure · volatility and what it implies about option pricing ·
positioning from short interest, OI and gamma · **any place the data contradicts
itself**.

### What the research says
Recent developments with dates · a **table of upcoming catalysts**
(date | event | why it matters) · analyst consensus *and its dispersion*, sourced.

### Bull case
Thesis · key evidence · what must actually happen for it to work.

### Bear case
Thesis · key evidence · what must actually happen for it to work.

### Base case
The most probable path, and roughly why.

### Recommendation
A table with: direction · vehicle · why this vehicle given the IV regime
specifically · conviction (n/5) · why that number and not higher · time horizon ·
entry zone or the condition to wait for · **invalidation level and why that
level** · targets with reasoning · sizing in ATR terms · what would change your
mind.

### Risks
Table: risk | severity (high/med/low) | mitigation.

### Unknowns
What you could not determine, and why it matters.

### Sources
Table: claim | source | date.

### Self-audit
Answer each of these plainly. This section exists because it catches the
failure modes this kind of analysis actually has — do not skip it or soften it.

1. Is my recommended vehicle consistent with the IV rank and IV/RV ratio in the
   data? If I'm buying premium at high IV or selling at low IV, have I actually
   justified it, or did I just assert it?
2. Is my invalidation level further from spot than one ATR? (If not, normal
   noise triggers it and it isn't a real level.) Is it within ~50% of spot?
   (If not, it doesn't constrain anything.)
3. Does my conviction match my evidence? Is it above 3 while the quantitative
   and qualitative pictures disagree? Is it 4+ alongside a "wait" or "no trade"?
4. How old is my newest source? Am I presenting anything older than 30 days as
   the current state?
5. Which specific claims in this note rest on a single source?
6. What is the strongest argument against my own recommendation, and why didn't
   it change my mind?

---

*Data: Schwab market data API via EquityDeepDive. This is analysis, not
investment advice.*
