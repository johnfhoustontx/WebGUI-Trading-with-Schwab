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

**Do-not-exercise (DNE) instruction** — Telling your broker *not* to auto-exercise a barely-ITM option, usually because commissions and overnight share risk cost more than the pennies of intrinsic value.

**AM settlement** — The contract settles off the opening prices of the index components on expiration morning. SPX monthly contracts work this way, and the settlement value can differ noticeably from where the index printed at Thursday's close.

**PM settlement** — Settles off the closing price on expiration day. SPXW (weeklies and dailies) and all equity options are PM settled.

**SET** — The specific published settlement value for AM-settled SPX contracts. It's a calculated number, not a price anything actually traded at, which is why you occasionally see it land outside the day's range.

**Adjusted option** — A contract whose terms were changed after a split, merger, spinoff, or special dividend. The deliverable may no longer be 100 clean shares. These trade badly, price strangely, and are usually best avoided.

**Deliverable** — What actually changes hands on exercise. Normally 100 shares, but adjusted options can deliver odd share counts plus cash or shares of a second company.

**Ex-dividend risk** — The reason short ITM calls get assigned early. If the remaining time value of the call is less than the dividend, it's rational for the holder to exercise the day before ex-date to capture it.

**Hard to borrow (HTB)** — When shares are expensive or impossible to short. This distorts option pricing: puts get bid up, calls get cheap, and put-call parity appears to break because the borrow cost is buried in the prices.

**OCC (Options Clearing Corporation)** — The clearinghouse that stands between every option buyer and seller. It guarantees the contracts, handles assignment, and publishes open interest.

**Random allocation** — How assignment actually gets assigned. The OCC picks a clearing firm at random, and the firm picks a customer account by its own method. Being assigned isn't personal and isn't predictable.

**Position limits / exercise limits** — Caps on how many contracts one trader can hold or exercise on the same side of the market in a single name. Rarely relevant at retail size.

**FLEX options** — Customized exchange-traded contracts with negotiated strikes, expirations, and exercise styles. Institutional; almost no secondary liquidity.

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

**Lambda (omega, elasticity)** — The leverage ratio. What percentage the option moves for a 1% move in the underlying. A 0.30 delta call on a $500 stock trading at $4 has roughly 37x leverage. This is the number that explains why small option positions blow up accounts.

**Probability ITM** — The model's actual estimate of finishing in the money. Close to delta but not identical — delta is a hedge ratio that happens to approximate it. For skewed names the two can diverge meaningfully.

**Probability of touch** — The chance price *ever* trades through the strike before expiration, roughly double the probability of finishing ITM. This is the number that matters if you'll be forced to manage or close the position early.

**Gamma scalping** — Holding long gamma (long options) and repeatedly re-hedging delta as price swings: sell into rallies, buy into dips, bank the difference. You're trying to earn more from the scalps than you pay in theta. The whole trade is a bet that realized volatility beats implied.

**Vega notional** — Dollar P&L per 1-point IV move for a whole position or book, rather than per contract.

**Bucketed greeks** — Greeks broken out by expiration or strike range instead of summed into one number. A book can look delta-neutral overall while carrying big offsetting risks in the front week and the back month.

**Greek units caveat** — Vendors disagree on scaling. Gamma may be quoted per share or per contract, theta per day or per year, vega per 1 point or per 1% of IV. Always confirm the convention before wiring an API feed into a risk calculation.

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

**Volatility surface** — Skew and term structure combined: IV plotted across both strike and expiration. The full picture of how the market prices every contract at once.

**ATM volatility** — The IV at the at-the-money strike. The reference point everything else is quoted against.

**25-delta risk reversal** — The IV of the 25-delta call minus the IV of the 25-delta put. The standard single number for how steep the skew is. Deeply negative means downside protection is expensive relative to upside.

**25-delta butterfly** — The average IV of the 25-delta wings minus ATM IV. Measures how much the curve smiles — how much extra the market charges for tails on both sides.

**Sticky strike** — The assumption that each strike's IV stays put as spot moves. Under sticky strike, an option's delta is just its Black-Scholes delta.

**Sticky delta (sticky moneyness)** — The assumption that the whole IV curve slides along with spot, so a 25-delta option keeps the same IV as price moves. Which regime you assume changes your effective delta materially, and equity indexes behave somewhere between the two.

**Forward volatility** — The volatility implied for a future window, backed out from two expirations. Useful for isolating what the market expects around a specific event once you strip out the vol on either side of it.

**Volatility cone** — Realized volatility over various lookback windows plotted against its own historical range. Shows whether current vol is high or low for that particular measurement horizon.

**Realized vol estimators** — Different math for measuring how much something actually moved. Close-to-close is the simple one; Parkinson uses the high-low range, Garman-Klass adds open and close, and Yang-Zhang handles overnight gaps. Range-based estimators are far more efficient on limited data, which matters for intraday work.

**Contango** — Far-dated volatility priced above near-dated. The normal, calm state of the VIX term structure.

**Backwardation** — Near-dated volatility above far-dated. Signals acute stress and often marks the panic phase of a selloff.

**VIX futures (VX)** — Exchange-traded futures on the VIX. What VIX ETPs actually hold. They converge to spot VIX at expiration, and the roll between contracts is why long-vol products bleed in calm markets.

**VVIX** — The implied volatility of VIX options. Volatility of volatility. Elevated VVIX with a calm VIX often precedes a shock.

**SKEW index** — CBOE's measure of how much the market is paying for far OTM SPX puts relative to a normal distribution. High readings mean tail hedges are in demand.

**Dispersion** — The relationship between index volatility and the volatility of its components. Index vol is suppressed by diversification, so selling index vol against buying single-name vol is a common institutional trade. When correlation spikes, dispersion trades hurt.

**Correlation (implied)** — Backed out from index vol versus component vols. Rises toward 1 in selloffs, which is exactly when diversification stops helping.

**Volatility arbitrage** — Trading the gap between implied and expected realized volatility while hedging away direction. The purest expression is a delta-hedged straddle.

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

**Butterfly spread** — Buy one strike, sell two of a middle strike, buy one higher strike, all same type and expiration. Cheap, defined risk, and pays off big only if price lands right on the middle strike. A precision bet on where something finishes.

**Condor** — Same idea as a butterfly but with two different middle strikes, giving you a wider profit zone and a smaller maximum payoff.

**Iron condor** — Sell a call spread and a put spread. Profits if price stays in the middle range. Defined risk on both sides.

**Iron butterfly** — Like a condor but the short strikes are the same. Tighter range, bigger credit.

**Ratio spread** — Buy one, sell two (or more) further out. Collects premium but leaves naked exposure past the short strikes.

**Backspread** — The reverse: sell one near, buy two further out. Cheap or free exposure to a large move.

**Broken wing butterfly** — A butterfly with unequal wings, structured so one side has no risk (or a credit) at the cost of more risk on the other.

**Synthetic long / short** — Buy a call and sell a put at the same strike (synthetic long stock), or the reverse. Replicates share exposure using options.

**Box spread** — A bull call spread plus a bear put spread at the same strikes. Locks in a fixed payoff; used as a financing trade, essentially a loan.

**Jelly roll** — The combined value of a calendar spread on both calls and puts. Prices the cost of carry between two expirations.

**Risk reversal** — Sell a put and buy a call (or the reverse), both OTM. Bullish exposure financed by selling downside. The building block behind collars and the standard way skew gets traded.

**Poor man's covered call (PMCC)** — A long-dated deep ITM call standing in for shares, with a short-dated OTM call sold against it. A diagonal that mimics a covered call for a fraction of the capital, at the cost of assignment and vol risk.

**Buy-write** — Buying shares and selling the call in a single order. An entry method for a covered call.

**Overwriting** — Systematically selling calls against a long portfolio to generate income. Works until it doesn't; the cost is your best-performing positions get called away.

**Ladder** — Multiple strikes of the same type spread out at different levels, either bought or sold, to stagger entries or scale exposure.

**Strip / strap** — An unbalanced straddle. A strip is one call and two puts (bearish lean); a strap is two calls and one put (bullish lean). Both bet on a move with a directional tilt.

**Guts** — A straddle built with ITM strikes instead of OTM. Mostly a curiosity, occasionally useful for capturing wide bid-ask inefficiencies.

**Stock repair** — Adding a ratio call spread to an underwater long position so it breaks even on a smaller bounce, in exchange for capping upside.

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

**Gamma squeeze** — Heavy call buying forces dealers to buy shares to hedge, which pushes price up, which increases the deltas they must hedge, which forces more buying. A self-reinforcing loop that runs until the buying stops or the calls go so deep ITM that gamma collapses.

**VEX (vega exposure)** — Aggregate dealer vega. Indicates how the book reacts to IV changes and how much vol-driven hedging is latent in the system.

**Volatility trigger** — A commonly-used name for the spot level where dealer gamma flips sign. Above it the market tends to be stable and mean-reverting; below it, trending and volatile.

**Sign convention (the big caveat)** — Every GEX/DEX number depends on an assumption about who bought and who sold, since exchanges don't publish that. The standard simplification is that customers buy calls and sell puts, making dealers short calls and long puts. This assumption is wrong often enough to matter, and it is the single largest source of error in dealer-positioning models.

**Trade classification** — Inferring whether a trade was buyer- or seller-initiated by comparing the print to the prevailing bid and ask. The foundation of any attempt to build positioning data from raw tape rather than assumptions.

**Gamma units** — GEX can be expressed as shares per 1% move, dollars per 1% move, or dollars per 1-point move. Comparing numbers across sources without checking units produces nonsense.

**Open interest change** — Day-over-day OI change tells you whether positions opened or closed. Volume alone can't distinguish a new position from a closed one, and OI publishes with a delay.

**Hedging window** — The recurring times when mechanical hedging concentrates: the open, the 3:30–4:00 close, Thursday/Friday charm decay, and monthly OPEX.

---

## 7. Orders, Execution, and Risk

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

**NBBO** — The best bid and offer across all options exchanges combined. Your quoted spread, but it can be one contract deep and vanish the moment you send size.

**Complex order book (COB)** — The separate book where multi-leg spreads trade as single instruments. Spread orders often fill better than the sum of the individual legs' quotes suggest, which is why you should send combos as combos.

**Price improvement / auction** — Many retail option orders get routed into exchange auctions where firms compete to fill them inside the NBBO. It's why your limit sometimes fills better than expected.

**Payment for order flow (PFOF)** — Brokers being paid to route your orders to specific market makers. Very common in options and part of why option commissions are low.

**Reg T margin** — The standard, rules-based margin system. Fixed formulas per strategy, no credit for how positions offset each other.

**Portfolio margin** — Risk-based margin that stress-tests the whole account across a range of price moves. Dramatically lower requirements for hedged books, but it requires a larger account and it can tighten fast in a crisis.

**SPAN margin** — The risk-based system used for futures and futures options. Scenario-driven, and generally the most capital-efficient of the three.

**Early exercise premium** — The extra value an American option carries over an otherwise identical European one, because of the right to exercise early. Meaningful for deep ITM puts and for calls facing dividends.

**Section 1256 contracts** — Broad-based index options like SPX and futures options. They're marked to market annually and taxed 60% long-term / 40% short-term regardless of holding period. Materially better tax treatment than SPY options, which are taxed as ordinary short-term gains. Worth understanding before choosing which product to trade. Not tax advice — confirm with a professional for your situation.

**Wash sale** — A loss disallowed because you re-entered a substantially identical position within 30 days. Options complicate this considerably. Section 1256 contracts are exempt.

**Liquidation risk** — Brokers can close positions without warning when margin is breached, usually at the worst possible price. Short options in a fast market are the classic case.

---

## 8. Pricing Models and Math

Relevant if you're calculating any of this yourself rather than reading it off a screen.

**Black-Scholes-Merton** — The standard closed-form model for European options. Fast, analytic greeks, and the common language of the market. Its assumptions (constant volatility, lognormal returns, no jumps) are all wrong, which is precisely why the volatility surface exists — traders bend IV per strike to force the model to match reality.

**Implied volatility solve** — IV isn't calculated directly; you invert the pricing model numerically until the theoretical price matches the market price. Bisection is robust, Newton-Raphson is fast, and hybrid approaches handle the deep wings where vega goes to nearly zero and the solve becomes unstable.

**Binomial model (Cox-Ross-Rubinstein)** — A price tree stepped backward from expiration. Slower than closed-form but handles American early exercise and discrete dividends naturally.

**Bjerksund-Stensland / Barone-Adesi-Whaley** — Closed-form approximations for American options. The practical choice when you need American pricing across thousands of contracts and can't afford a tree per contract.

**Monte Carlo** — Simulating many random price paths and averaging the payoffs. Overkill for vanillas, necessary for path-dependent structures.

**Put-call parity** — The fixed arithmetic relationship between a call, a put, the underlying, and a bond at the same strike and expiry: call − put = spot − discounted strike. It's an arbitrage identity, not a theory. When it appears violated, the cause is almost always dividends, borrow cost, or stale quotes rather than free money.

**Conversion / reversal** — The trades that enforce put-call parity. Long stock plus long put plus short call (conversion), or the reverse. Market makers use them to extract small financing edges.

**Forward price** — Where the market prices the underlying at expiration once carry, dividends, and financing are accounted for. Options are properly priced off the forward, not off spot. Ignoring this is a common source of IV calculation errors in dividend-paying names.

**Moneyness** — Strike relative to spot or forward. Often expressed as log-moneyness, ln(K/F), which makes skew comparable across underlyings and price levels.

**Risk-neutral probability** — The probability distribution implied by option prices. Not a forecast of what will happen — it's real-world probability tilted by what people will pay to hedge. This is why option-implied "probabilities" persistently overstate crash odds.

**Local volatility (Dupire)** — A model where volatility is a deterministic function of spot and time, fitted to reproduce the entire observed surface exactly.

**Stochastic volatility (Heston, SABR)** — Models where volatility has its own random process. Produce more realistic skew dynamics and are the standard for anything vol-sensitive.

**Jump diffusion** — Adding discrete jumps to the price process. Explains why short-dated wings trade far above what a pure diffusion model says they're worth.

**Arbitrage-free surface** — A fitted volatility surface with no butterfly arbitrage (negative implied densities across strikes) and no calendar arbitrage (vol decreasing in total variance across time). Raw interpolation of market IVs frequently violates both, which produces garbage greeks downstream.

**Finite-difference greeks** — Computing greeks by re-pricing with a small bump to an input instead of using an analytic formula. Slower, but the only option for models without closed-form derivatives, and a useful sanity check against analytic values.

**Interest rate and dividend inputs** — The quiet source of most pricing discrepancies. Wrong rate, wrong dividend assumption, or wrong day-count convention will shift your IVs and greeks enough to invalidate comparisons against vendor data.

---

## 9. Quick Reference

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
