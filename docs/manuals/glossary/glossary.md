# Options Trading Glossary

Plain-English definitions of the terms you'll run into trading and analyzing options.

---

## 1. The Basics

**Option** — A contract that gives the buyer the *right* (not the obligation) to buy or sell 100 shares of something at a fixed price before a fixed date. The seller has the *obligation* to deliver if the buyer exercises.

**Call** — The right to *buy* at the strike price. You buy calls when you think the price is going up.

**Put** — The right to *sell* at the strike price. You buy puts when you think the price is going down (or you want insurance on shares you own).

**Strike price** — The fixed price written into the contract. A 500 call lets you buy at $500 no matter what the stock is actually trading at.

**Expiration** — The date the contract dies. After that it's either exercised or worthless.

**Premium** — The price of the option. What the buyer pays and the seller collects.

**Contract multiplier** — Standard equity options control 100 shares. A premium of $2.50 means you pay $250 per contract. Index options like SPX use $100 per point too, but settle in cash.

**Underlying** — Whatever the option is written on: a stock, an ETF, an index, a futures contract.

**Long / Short** — Long means you bought it. Short means you sold it (and owe something). "Long puts" and "short calls" are very different positions even though both are bearish.

**Writing** — Selling an option you don't already own. Same thing as going short the option.

---

## 2. Contract Mechanics

**In the money (ITM)** — The option has real value if exercised right now. A call is ITM when the stock is above the strike; a put is ITM when the stock is below.

**Out of the money (OTM)** — The opposite. Exercising would make no sense. All the premium is bets on future movement.

**At the money (ATM)** — Strike sits right around the current price.

**Intrinsic value** — The part of the premium that's real, exercisable value. Stock at $105, 100 call → $5 of intrinsic value.

**Extrinsic value (time value)** — Everything above intrinsic. This is the part that decays to zero by expiration. OTM options are 100% extrinsic.

**Exercise** — The buyer uses their right and converts the option into shares.

**Assignment** — The flip side. You sold an option and someone exercised against you, so now you must buy or deliver shares.

**Early assignment** — Getting assigned before expiration. Mostly happens on short ITM calls right before an ex-dividend date, or on deep ITM puts.

**American style** — Can be exercised any day up to expiration. Nearly all single-stock options.

**European style** — Can only be exercised at expiration. Most cash-settled index options (SPX, NDX).

**Cash settlement** — No shares change hands. The difference is paid in cash. Index options work this way.

**Physical settlement** — Actual shares get delivered.

**Open interest (OI)** — How many contracts are currently alive at that strike. Builds up over time. Tells you where positions actually exist.

**Volume** — How many contracts traded *today*. Volume is activity; open interest is accumulated positioning.

**Expiration cycle** — Which expirations are listed. Weeklies expire every Friday, monthlies on the third Friday, quarterlies at quarter end. Big names now list Mon/Wed/Fri or daily.

**0DTE** — Zero days to expiration. An option expiring the same day it's traded. Gamma and theta are enormous, and behavior is dominated by dealer hedging.

**DTE** — Days to expiration. Shorthand: "30 DTE" means a month out.

**LEAPS** — Long-dated options, typically a year or more out. Behave more like stock substitutes than bets on short-term movement.

**Option chain** — The full grid of every strike and expiration for an underlying, with bids, asks, volume, OI, and greeks.

**Pin risk** — When the price sits almost exactly on a big strike at expiration and you don't know whether you'll be assigned. Also describes the tendency of price to gravitate toward heavily-traded strikes.

**Exercise by exception (auto-exercise)** — Brokers automatically exercise anything ITM by a penny or more at expiration unless you tell them not to.

---

## 3. The Greeks

The greeks measure how an option's price reacts to different things changing.

**Delta** — How much the option price moves for a $1 move in the underlying. A 0.40 delta call gains about $0.40 per $1 rally. Calls run 0 to +1, puts run 0 to −1. Also a rough shorthand for the probability of finishing ITM.

**Gamma** — How fast delta changes as the underlying moves. High gamma means your delta swings around quickly. Peaks at the money and explodes near expiration.

**Theta** — Time decay. How much value the option bleeds per day, all else equal. Negative for buyers, positive for sellers. Accelerates hard in the final weeks.

**Vega** — Sensitivity to implied volatility. How much the price changes for a 1-point move in IV. Long options are always long vega.

**Rho** — Sensitivity to interest rates. Usually ignorable on short-dated options, matters on LEAPS.

**Charm (delta decay)** — How delta changes as time passes with price standing still. OTM options bleed delta toward zero as expiration approaches; ITM options drift toward ±1. Drives predictable hedging flows, especially into Friday.

**Vanna** — How delta changes when implied volatility changes. When IV drops, OTM put deltas shrink, and dealers who hedged those puts have to buy back stock. This is why vol crush and grind-up rallies go together.

**Vomma (volga)** — How vega changes when volatility changes. Matters for volatility trades and far OTM wings.

**Speed, Zomma, Color** — Third-order greeks: how gamma changes with price (speed), with volatility (zomma), and with time (color). Mostly relevant to market makers and modeling work.

**Net delta / dollar delta** — Total directional exposure of a position or book, expressed in share-equivalents or dollars. A book with +5,000 net delta behaves like 5,000 shares of stock.

---

## 4. Volatility

**Historical volatility (HV) / realized volatility (RV)** — How much the underlying *actually* moved, measured from past prices.

**Implied volatility (IV)** — The volatility the market is currently pricing into options. Back-solved from the option's price. It's an expectation, not a measurement.

**IV rank** — Where today's IV sits between its 52-week low and high, on a 0–100 scale. IV rank of 80 means IV is near the top of its yearly range.

**IV percentile** — What percentage of days over the past year had *lower* IV than today. Different from IV rank: rank uses only the two extremes, percentile uses the whole distribution. A single spike can distort rank but barely moves percentile.

**Volatility skew** — The fact that different strikes carry different IVs. In equities, downside puts almost always price at higher IV than upside calls, because crash protection is in demand.

**Volatility smile** — The U-shape you get when you plot IV against strike. Common in FX and commodities; equities usually show a lopsided smirk instead.

**Term structure** — IV plotted across expirations. Normally upward sloping (contango). Inverted term structure — near-dated IV above far-dated — signals stress or a known event.

**Vol crush** — The sharp collapse in IV right after a scheduled event like earnings. The uncertainty resolves, the premium evaporates, and long option holders can lose money even when they got the direction right.

**VIX** — The 30-day implied volatility of SPX options, expressed annualized. The market's fear gauge.

**Variance risk premium** — The persistent tendency of implied volatility to price above what actually gets realized. The structural reason option selling has an edge over long periods.

**Expected move** — How far the market is pricing the underlying to travel by a given expiration. Roughly the ATM straddle price, or spot × IV × √(days/365).

---

## 5. Strategies

**Covered call** — Own 100 shares, sell a call against them. Collects premium, caps your upside.

**Cash-secured put** — Sell a put with cash set aside to buy the shares if assigned. A way to get paid while waiting for a lower entry.

**Protective put** — Own shares, buy a put as insurance.

**Collar** — Own shares, buy a put, sell a call to pay for it. Boxes in your range.

**Vertical spread** — Buy one strike, sell another in the same expiration. Caps both risk and reward. A *debit spread* costs money and needs movement; a *credit spread* collects money and profits from time or non-movement.

**Bull call spread / bear put spread** — Debit verticals betting up or down.

**Bull put spread / bear call spread** — Credit verticals betting the price stays above or below a level.

**Calendar spread (time spread)** — Sell a near expiration, buy a further one at the same strike. Profits from the near contract decaying faster.

**Diagonal spread** — A calendar with different strikes too.

**Straddle** — Buy (or sell) a call and put at the same strike. Long straddle bets on a big move either way; short straddle bets on stillness.

**Strangle** — Same idea, but OTM strikes on both sides. Cheaper than a straddle, needs a bigger move.

**Iron condor** — Sell a call spread and a put spread. Profits if price stays in the middle range. Defined risk on both sides.

**Iron butterfly** — Like a condor but the short strikes are the same. Tighter range, bigger credit.

**Ratio spread** — Buy one, sell two (or more) further out. Collects premium but leaves naked exposure past the short strikes.

**Backspread** — The reverse: sell one near, buy two further out. Cheap or free exposure to a large move.

**Broken wing butterfly** — A butterfly with unequal wings, structured so one side has no risk (or a credit) at the cost of more risk on the other.

**Synthetic long / short** — Buy a call and sell a put at the same strike (synthetic long stock), or the reverse. Replicates share exposure using options.

**Box spread** — A bull call spread plus a bear put spread at the same strikes. Locks in a fixed payoff; used as a financing trade, essentially a loan.

**Jelly roll** — The combined value of a calendar spread on both calls and puts. Prices the cost of carry between two expirations.

**Wheel** — A repeating loop: sell cash-secured puts until assigned, then sell covered calls until called away, repeat.

**Roll** — Close an existing position and open a similar one further out in time or at a different strike. "Rolling up," "rolling out," "rolling for a credit."

---

## 6. Dealer Positioning and Flow

These describe what market makers are forced to do to stay hedged, and why that moves price.

**Market maker / dealer** — The firm on the other side of your trade. They don't want directional risk, so they hedge in the underlying. Their hedging is mechanical and therefore predictable.

**Delta hedging** — Buying or selling the underlying to offset the delta of an options book.

**GEX (gamma exposure)** — Estimated total gamma dealers are holding, usually expressed as dollars of stock they must trade per 1% move. The single most watched dealer-positioning metric.

**Long gamma (positive GEX)** — Dealers hold net long gamma, so they sell into rallies and buy into dips to stay hedged. This dampens volatility. Price tends to grind and mean-revert.

**Short gamma (negative GEX)** — Dealers are net short gamma, so they must buy as price rises and sell as it falls. This amplifies moves. Trend days, air pockets, and violent selloffs live here.

**Gamma flip level (zero gamma)** — The price where aggregate dealer gamma crosses from positive to negative. Behavior of the tape often changes character at this level.

**Call wall** — The strike above spot with the largest concentration of call gamma or open interest. Often acts as resistance while dealers are long gamma.

**Put wall** — The equivalent below spot. Often acts as support, and a break below it can accelerate.

**DEX (delta exposure)** — Aggregate dealer delta. Tells you the direction of the standing hedge, and how much stock must be bought or sold if positions unwind.

**Vanna exposure** — Hedging flow driven by IV changes rather than price changes. Falling IV forces dealers to buy stock; this is the mechanism behind slow, low-volume grind-up rallies.

**Charm flow** — Hedging driven purely by time passing. Concentrated into Thursday and Friday of expiration week as OTM options decay toward zero delta.

**OPEX** — Options expiration, especially monthly third-Friday expiration when large amounts of open interest roll off and positioning resets.

**Quad witching** — The quarterly expiration (March, June, September, December) when stock options, index options, stock futures, and index futures all expire together. Very high volume.

**Max pain** — The strike where the largest dollar amount of options would expire worthless. Some traders treat it as a magnet into expiration; the evidence is mixed.

**Put/call ratio** — Put volume or open interest divided by call. A crude sentiment gauge; high readings suggest fear.

**Sweep** — An aggressive order split across multiple exchanges to fill immediately. Often read as urgent, informed buying.

**Block trade** — A single large negotiated trade, usually institutional.

**Unusual options activity** — Volume far above the strike's normal levels, particularly volume exceeding open interest, which means new positions are opening.

---

## 7. Orders, Pricing, and Risk

**Bid / Ask** — What buyers will pay and what sellers will take. Options spreads are usually much wider than stock spreads.

**Mid price** — Halfway between bid and ask. The realistic starting point for a limit order.

**Slippage** — The gap between the price you expected and the price you got. Kills multi-leg strategies traded in illiquid chains.

**Liquidity** — How easily you can get in and out. Judge it by spread width, open interest, and daily volume — not by whether a strike is listed.

**Limit order** — Fill at your price or better. The default for options; market orders in wide chains are how you donate money.

**Multi-leg / combo order** — Sending a spread as a single order so all legs fill together at a net price.

**Legging in** — Entering the legs of a spread separately, hoping for a better combined price. Risks being stuck half-filled.

**Margin** — Collateral required to hold short options. Defined-risk spreads require much less than naked positions.

**Naked / uncovered** — A short option with no offsetting position. Undefined risk, high margin.

**Buying power reduction (BPR)** — How much of your account a position ties up.

**Assignment risk** — The chance you get assigned before you wanted to be, forcing an unplanned share position.

**Break-even** — The underlying price where the trade makes nothing. For a long call it's strike + premium; for a long put it's strike − premium.

**Max profit / max loss** — The best and worst outcomes at expiration. Every defined-risk structure has both; naked shorts do not.

**P&L attribution** — Breaking down a trade's gain or loss into how much came from direction (delta), from movement (gamma), from time (theta), and from vol (vega). Tells you whether you were right for the reason you thought.

**Payoff diagram** — The chart of profit against underlying price at expiration. Worth sketching before every unfamiliar structure.

---

## 8. Quick Reference

| If you think... | Consider |
|---|---|
| Price goes up | Long call, bull call spread, short put spread |
| Price goes down | Long put, bear put spread, short call spread |
| Price goes nowhere | Iron condor, short strangle, calendar |
| Big move, direction unknown | Long straddle, long strangle, backspread |
| IV is too high | Sell premium: credit spreads, condors |
| IV is too low | Buy premium: debit spreads, calendars, straddles |

| Greek | Answers the question |
|---|---|
| Delta | How much do I make per $1 move? |
| Gamma | How fast does that change? |
| Theta | What does waiting cost me per day? |
| Vega | What happens if fear rises or falls? |
| Charm | What happens as Friday approaches? |
| Vanna | What happens to my delta when IV moves? |
