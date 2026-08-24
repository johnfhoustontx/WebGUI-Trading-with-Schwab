"""Plain-English hover explanations for the Trade Analyzer's tiles.

Every tile on the four Signal Desk screens should be able to answer "what am I
looking at, and what would I do differently if this number changed" without the
reader leaving the page. That is what lives here: prose, keyed by what the tile
actually renders, returned as a string the page hands to ``ui.tooltip``.

**Three rules the tests enforce**, because prose fails silently:

1. **No internal identifier reaches the reader.** ``relative_only`` is a state
   key; the reader sees "Relative only" and an explanation with no underscore
   in it anywhere.
2. **Detail, not a label.** A tooltip that restates the thing it is attached to
   is worse than none — it costs a hover and teaches nothing. Each entry says
   what the number IS, and where it is useful or misleading.
3. **Coverage is pinned.** A new factor, column or plan row without an entry
   fails the suite, because a missing tooltip is invisible on screen.

Lookup is case- and space-insensitive: pages pass whatever they happen to
render (``"Call wall"``, ``"CALL WALL"``, ``"EXP / 20D"``), and an explanation
must not disappear over capitalisation.
"""

# ── the clearance states — the ones that prompted this module ───────────────
# `services/trade_svc/market_filter.py` is the source of the semantics. The
# asymmetry is the whole point and every one of these strings has to carry it:
# the model's labels are 20-day forward EXCESS returns vs SPY, so a bottom-band
# name is predicted to LAG the index, not to fall. A naked short on a perfectly
# correct read still loses money in a rising tape.

_LONG_CLEARED = (
    "The market is not standing in the way of a long here.\n\n"
    "The model ranks this name against the rest of today's list, and the "
    "broad-market check — is the S&P above its 200-day average, and is that "
    "average still rising — came back friendly. So you can take the trade the "
    "obvious way: buy the stock, or buy a call spread.\n\n"
    "This is permission, not a recommendation. It says the wind is not in "
    "your face; it says nothing about whether this particular name is a good "
    "one. That question is the band and the factors."
)

_LONG_RELATIVE = (
    "The model likes this name, but the broad market is shaky enough that "
    "simply owning it is a bet on two things at once — the name AND the "
    "market.\n\n"
    "Express it as a pair instead: long this name against a short in the S&P, "
    "so you are paid for the thing the model actually predicted — that this "
    "name beats the index — and not for the market's direction.\n\n"
    "If you take it outright anyway, size it smaller than a cleared long. You "
    "are adding a market bet the model never made."
)

_LONG_BLOCKED = (
    "You should not normally see this.\n\n"
    "A long is never blocked outright. The reasoning is that a long in a weak "
    "market is a worse trade, not a forbidden one — so the market check "
    "demotes a long to a paired trade rather than refusing it. Shorts are the "
    "side that can be blocked.\n\n"
    "If this is showing, treat it as a fault rather than as advice, and check "
    "the System Status page."
)

_SHORT_CLEARED = (
    "The market is weak enough that a short can be taken the direct way — "
    "short the stock, or buy a put spread.\n\n"
    "This needs more than a low ranking. The S&P has to be below its 200-day "
    "average or that average has to be falling, AND the market regime read "
    "has to be fresh and pointing down. All of it has to line up, because the "
    "model's own claim is weaker than it looks: it predicts this name will do "
    "worse than the index, which in a rising market can still mean the stock "
    "goes up.\n\n"
    "Cleared is the rarest of the three states, and the only one where a "
    "directional short is the intended expression."
)

_SHORT_RELATIVE = (
    "The model's read on this name is real, but the market will not let you "
    "express it as a plain short.\n\n"
    "Here is why. The model predicts how a name does COMPARED WITH the S&P "
    "over the next 20 trading days. A bottom-band name is predicted to LAG "
    "the index — not to fall. In a rising market a stock can lag and still go "
    "up, so a plain short on a perfectly correct read loses money. This "
    "desk's own automated trader learned that the expensive way, selling call "
    "spreads into a rally.\n\n"
    "So express it relatively: pair it against a long in the S&P, or sell "
    "premium above a level the stock would have to break through. Either one "
    "pays you for the lag the model predicted rather than for a fall it never "
    "predicted.\n\n"
    "You will also land here when the market read is simply missing or has "
    "gone stale. That is deliberate — reading a four-day-old market call as "
    "permission is exactly how a dead service ends up authorising shorts into "
    "a tape that has since turned back up."
)

_SHORT_BLOCKED = (
    "Do not express this short at all — not directly, and not as a pair.\n\n"
    "This is the strongest refusal the market check makes. Something about "
    "conditions makes any version of the trade a bad idea, and the reasons "
    "are listed underneath this label.\n\n"
    "The model may still be ranking the name near the bottom. That is not a "
    "contradiction: the ranking is about the name, and this is about whether "
    "the market lets you act on it."
)

_UNKNOWN_SIDE = (
    "No market read is available for this side right now, so nothing has been "
    "cleared or refused.\n\n"
    "This usually means the market data behind the check has not arrived yet, "
    "or the market regime service is not running. Treat it as "
    "\"not yet answered\" rather than as permission, and check the System "
    "Status page if it stays this way."
)

_CLEARANCE = {
    ("long", "cleared"): _LONG_CLEARED,
    ("long", "relative_only"): _LONG_RELATIVE,
    ("long", "blocked"): _LONG_BLOCKED,
    ("short", "cleared"): _SHORT_CLEARED,
    ("short", "relative_only"): _SHORT_RELATIVE,
    ("short", "blocked"): _SHORT_BLOCKED,
}


def clearance_help(side, state):
    """Explain one side's clearance chip. Never empty — an unrecognised state
    still says what the reader should conclude, which is "nothing yet"."""
    key = ((side or "").strip().lower(), (state or "").strip().lower())
    return _CLEARANCE.get(key, _UNKNOWN_SIDE)


# ── the model's factors ─────────────────────────────────────────────────────
# Keys mirror `pages.trade._FACTOR_LABELS`; a test fails if that map grows a
# factor without an explanation here.

_FACTORS = {
    # Validated swing-model factors
    "mom_12_1": (
        "How the stock did over the last year, DELIBERATELY ignoring the most "
        "recent month.\n\n"
        "The skipped month is the point. Over a year, winners have tended to "
        "keep winning; over a single month, they have tended to snap back. "
        "Measuring both together cancels them out, so this drops the last "
        "month and keeps the year.\n\n"
        "High means a strong year behind it. This is the most-studied effect "
        "in the factor literature, and one of the heavier weights here."),
    "mom_6_1": (
        "The same idea as the twelve-month version, measured over six months "
        "and again skipping the most recent month.\n\n"
        "It reacts sooner. When the two disagree, this name's trend changed "
        "somewhere in the last half year."),
    "pth": (
        "How close the stock is to its own highest price of the past year.\n\n"
        "Near the top means it is at or near a 52-week high. That sounds like "
        "a reason to wait for a pullback, and historically it has been the "
        "opposite: stocks near their highs have tended to keep going. Low "
        "values mean the stock is well off its high, with overhead supply to "
        "chew through."),
    "str_5d": (
        "The last week's move, with the sign flipped.\n\n"
        "A stock that dropped hard over five days scores HIGH here, because "
        "very short-term moves have tended to partly reverse. It is the "
        "deliberate counterweight to the momentum factors above, which is why "
        "a name can score well on both — a strong year plus a bad week."),
    "vol_adj_mom": (
        "Momentum divided by how jumpy the stock has been.\n\n"
        "It asks whether a gain was earned steadily or in a couple of violent "
        "jumps. Two stocks up the same amount score differently: the calmer "
        "one rates higher, because a steady climb has tended to persist where "
        "a spiky one has not."),
    "trend_quality": (
        "How straight the stock's climb has been, rather than how big.\n\n"
        "A tidy, persistent advance scores high; the same total move "
        "delivered in lurches scores low. Read it beside the momentum "
        "factors: they say how far, this says how convincingly."),
    "low_vol": (
        "How calm the stock has been, as a factor in its own right.\n\n"
        "Calm stocks have historically delivered better risk-adjusted returns "
        "than wild ones. Be aware that this factor is a large part of why "
        "this model's ranking behaves like a bet on the market — the "
        "calm-versus-wild axis is exactly what moves when the whole market "
        "moves."),
    "rs_spy": (
        "How this stock has done compared with the S&P 500.\n\n"
        "Above the middle means it beat the index over the window. This "
        "matters more than it looks, because everything the model predicts is "
        "stated relative to the index — so this factor is measured in the "
        "same units as the answer."),
    "rs_sector": (
        "How this stock has done compared with its own sector, rather than "
        "the whole market.\n\n"
        "It separates the stock from its neighbourhood. A chip stock up 20% "
        "while every chip stock is up 25% is lagging, and only this factor "
        "sees that."),
    "turnover": (
        "How much of the company's stock changes hands on a typical day, "
        "relative to its size.\n\n"
        "Partly a liquidity check — thin names are harder to get in and out "
        "of — and partly a crowding signal, since unusually heavy turnover "
        "tends to mark names that attention has already found."),
    "max_effect": (
        "How extreme the stock's single best day was recently.\n\n"
        "Names with a huge one-day jackpot in the recent past have tended to "
        "do WORSE afterwards — the lottery-ticket effect. A big number here "
        "is a caution, not a compliment."),
    "semivol": (
        "How jumpy the stock has been counting only its DOWN days.\n\n"
        "Ordinary volatility treats a sharp rise and a sharp fall as the same "
        "thing. This measures only the falls, which is closer to what "
        "actually hurts when you are long."),
    "downside_beta": (
        "How hard this stock falls specifically on the days the market "
        "falls.\n\n"
        "A stock can look calm on average and still be the one that drops "
        "hardest in a sell-off. This separates those two."),
    "below_200ema": (
        "Whether the stock is trading below its long-run average price.\n\n"
        "A blunt line in the sand that many trend followers watch. Below it, "
        "the medium-term picture is broken regardless of what the "
        "shorter-term factors say."),
    # Legacy technical factors
    "rsi": (
        "A 0-100 gauge of how one-sided recent trading has been.\n\n"
        "Traditionally above 70 is called overbought and below 30 oversold, "
        "but in a genuine trend a stock can sit above 70 for weeks. Read it "
        "as \"how stretched\", never as a signal on its own."),
    "macd": (
        "The gap between a fast and a slow average of the price — a way of "
        "asking whether the trend is picking up speed or losing it.\n\n"
        "Above zero and widening means the advance is accelerating; narrowing "
        "means it is tiring, even while price still rises."),
    "adx": (
        "How strong the trend is, WITHOUT saying which way it points.\n\n"
        "High means the market is trending decisively; low means it is "
        "chopping sideways. Below about 15, trend-following signals of every "
        "kind get unreliable, which is why this is used as a gate rather than "
        "as a score."),
    "ema_alignment": (
        "Whether the short, medium and long price averages are lined up in "
        "order.\n\n"
        "Stacked in the bullish order is the textbook picture of a healthy "
        "uptrend; tangled together means the timeframes disagree and the "
        "trend has no clear owner."),
    "rel_volume": (
        "How today's trading volume compares with this stock's own normal "
        "day.\n\n"
        "Well above normal means something happened — news, an earnings "
        "reaction, or a large buyer working an order. A move on ordinary "
        "volume is much easier to fade than the same move on triple volume, "
        "which is why this qualifies the other signals rather than standing "
        "on its own."),
    "vwap": (
        "Where price sits against the day's volume-weighted average — "
        "roughly, the average price everyone who traded today actually "
        "paid.\n\n"
        "Above it, buyers are in control of the session; below it, sellers "
        "are. It is a day-trading reference and says little about weeks."),
    "volume_profile": (
        "Which price levels the most shares have actually changed hands "
        "at.\n\n"
        "Heavily-traded prices act like magnets and like barriers: plenty of "
        "people own stock there and have opinions about it. Thin areas are "
        "where price tends to move fast, because nothing is in the way."),
    "rs_3m": (
        "How the stock has done against the market over the past three "
        "months.\n\n"
        "Short enough to catch a recent change in leadership, long enough not "
        "to be noise."),
    "rs_6m": (
        "How the stock has done against the market over the past six "
        "months.\n\n"
        "Read it beside the three-month version: if the shorter one is "
        "stronger, this name's leadership is recent."),
    "dist_52wk": (
        "How far below its highest price of the past year the stock is "
        "trading.\n\n"
        "A large distance means a lot of people bought higher and are waiting "
        "to get out even, which tends to cap rallies."),
    "sector": (
        "How the stock's whole sector is doing, independent of the stock.\n\n"
        "Sector moves explain a large share of any single stock's move, so a "
        "great name in a sinking sector is swimming against a current. A "
        "sector in a confirmed downtrend caps the Long Term verdict at Hold "
        "outright."),
    # Investor (months+) factors
    "valuation": (
        "Whether the stock looks cheap or expensive, from its "
        "price-to-earnings ratio against its own sector's typical level, and "
        "its price-to-earnings-to-growth ratio.\n\n"
        "Only the pieces that actually arrived are averaged. A missing sector "
        "comparison does not quietly drag the score toward zero — it is left "
        "out, which is the fix for a bug that used to halve this number."),
    "growth_quality": (
        "Whether the business is growing, and whether that growth is any "
        "good.\n\n"
        "Four things averaged: revenue growth, earnings growth, return on "
        "equity, and whether profit margins are widening or narrowing. Growth "
        "bought at the cost of collapsing margins scores worse than the "
        "headline growth number alone would suggest."),
    "earnings_traj": (
        "Whether the company has been beating expectations and guiding "
        "higher.\n\n"
        "This one can never score, for any stock. Schwab's data feed does not "
        "publish earnings surprises or company guidance, so both of its "
        "inputs are permanently missing and it contributes exactly zero — "
        "which means 15 of the Long Term score's 100 points are unreachable "
        "before any company is examined. That is why a decent business can "
        "show a middling score here."),
    "rs_vs_spy": (
        "How the stock has done against the S&P 500 over three, six and "
        "twelve months, averaged.\n\n"
        "Using three windows stops one good quarter from carrying the whole "
        "reading."),
    "rs_vs_sector": (
        "How the stock has done against its own sector over three, six and "
        "twelve months, averaged.\n\n"
        "Beating the market because your whole sector is hot is a different "
        "fact from beating your sector, and this is the one that isolates the "
        "company."),
}


def factor_help(key):
    """Explain one model or Long Term factor by its engine key."""
    return _FACTORS.get((key or "").strip().lower(), "")


# ── column headers, and the labelled rows on the plan and dealer panels ─────

_COLUMNS = {
    # Rank board
    "symbol": (
        "The ticker. Your own symbol is highlighted when it appears in the "
        "list, so you can see where the name you are analysing sits among the "
        "candidates."),
    "band": (
        "Which of five bands this stock's score falls into, shown as an "
        "ordinal: 90th is the top band, 10th the bottom.\n\n"
        "The bands are fixed thresholds cut from five years of this model's "
        "own output, so this is NOT a ranking of today's names. On any given "
        "day the bands fill unevenly — a defensive market can leave the top "
        "band nearly empty. For today's ranking, read the Decile column."),
    "score": (
        "The model's raw output for this stock: every factor, measured "
        "against the rest of today's list, multiplied by its weight and added "
        "up.\n\n"
        "Positive means the model expects this name to beat the S&P over the "
        "next 20 trading days; negative means lag. The band beside it is just "
        "this number sorted into a bucket."),
    "exp / 20d": (
        "What names in this band have historically returned COMPARED WITH the "
        "S&P over the following 20 trading days, on average.\n\n"
        "It is a historical average over thousands of examples, not a "
        "forecast for this stock. Individual outcomes are spread far wider "
        "than this number suggests."),
    "hit": (
        "How often names in this band went on to beat the S&P over the next "
        "20 trading days.\n\n"
        "Read this next to the expected return. Even the best band is close "
        "to a coin flip — the edge shows up over many trades, not in any one "
        "of them."),
    "decile": (
        "Where this name ranks among the roughly 78 stocks scored today, from "
        "1 (worst) to 10 (best).\n\n"
        "This is the genuine ranking of today's list, and it is a different "
        "question from the Band column: the band asks how the score compares "
        "with five years of history, this asks how it compares with the other "
        "names right now. A list where everything is mid-band still has a "
        "best and a worst."),
    "dtc": (
        "Days to cover — how many days of normal trading volume it would take "
        "for everyone currently short the stock to buy it back.\n\n"
        "A high number is squeeze risk: if the stock starts rising, those "
        "buyers cannot all get out quickly, and their buying pushes it higher "
        "still. Shown on the short side because that is where it can hurt "
        "you."),
    "dealer": (
        "Where the stock sits relative to the levels where options dealers "
        "have to hedge.\n\n"
        "It reads \"not collected\" for names outside the tracked list, or "
        "when the data is stale. That is not the same as saying the stock is "
        "at a neutral level — it means nothing is known."),
    "iv": (
        "The implied volatility of options struck near the current price — "
        "how much movement the options market is pricing in.\n\n"
        "High means options are expensive, which favours strategies that sell "
        "premium. Low favours buying it."),
    "gates": (
        "Safety checks that would stop this trade, listed by name, or "
        "\"clear\" if none fired.\n\n"
        "The line under the table names exactly which checks were run. A "
        "clear row means it passed those, not that nothing whatsoever is "
        "wrong."),
    # Evidence
    "factor": (
        "One measurable property of the stock that the model uses. Hover the "
        "name itself for what that particular one measures."),
    "z": (
        "How unusual this stock's value is, measured against every other "
        "stock scored today.\n\n"
        "Zero is exactly average for today's list. Plus one is one standard "
        "step above average, minus one a step below. Comparing against "
        "today's list rather than a fixed scale is what lets the model still "
        "work in a market where everything has moved together."),
    "weight": (
        "How much this factor counts, and in which direction.\n\n"
        "Set by how well the factor predicted returns historically, so one "
        "that worked gets more say. A negative weight is not a bug: it means "
        "high values of that factor predicted WORSE returns, so the model "
        "deliberately leans the other way."),
    "contribution": (
        "This factor's actual push on the final score for this stock: its "
        "unusualness multiplied by its weight.\n\n"
        "The bar grows right from the centre for a positive push and left for "
        "a negative one, so you can see at a glance which handful of factors "
        "drove the verdict. These are what add up to the weighted composite "
        "below."),
    "ic": (
        "How well this factor predicted returns during testing, on a scale "
        "where zero is useless.\n\n"
        "Values in finance are small by the standards of most fields: 0.03 is "
        "a real effect and 0.10 is strong. It is the historical track record "
        "of the factor, not a claim about this stock."),
    # Model paper book
    "side": (
        "Whether the model opened this as a long or a short. Shorts are "
        "recorded even when the market check would only permit them as a "
        "paired trade — the book tracks the model's calls, not a real "
        "account."),
    "as": (
        "How the position was expressed: outright, or paired against the "
        "index. It follows whichever market check applied on the day it "
        "opened."),
    "opened": (
        "The date the model recorded this call. Positions are held for the "
        "model's own 20-trading-day horizon and closed on schedule, not on "
        "judgement."),
    "p&l": (
        "How this position has done since it opened, in percent.\n\n"
        "This is a paper record kept to score the model honestly over time. "
        "No order was ever sent, and there are no commissions or slippage in "
        "these numbers."),
    "status": (
        "Whether the position is still open, or why it was closed — its time "
        "ran out, or a stop level was reached."),
}

_ROWS = {
    # Trade plan
    "structure": (
        "The shape of the trade the plan suggests, and roughly how long until "
        "expiry.\n\n"
        "It names a structure and a tenor, deliberately NOT strikes. Choosing "
        "strikes needs the live option chain, which is what the Find strikes "
        "button opens."),
    "short strike": (
        "For a trade that sells options, roughly where the sold strike should "
        "sit — usually beyond a level the stock would have to break through "
        "for the trade to be in trouble."),
    "entry zone": (
        "Where to try to get in, rather than chasing the current price.\n\n"
        "Usually phrased against a support level or a wall, because entering "
        "right into the level where dealers are defending is how a good idea "
        "becomes a bad fill."),
    "stop": (
        "The level that says this idea was wrong, chosen before you are in "
        "the trade and emotionally invested in it.\n\n"
        "It is a level, not an order — nothing on this page places "
        "anything."),
    "target": (
        "What the model's own history says this band has been worth, stated "
        "against the S&P over 20 trading days.\n\n"
        "Note the comparison: the model predicts beating the index, so in a "
        "falling market this target can be met by losing less than the index "
        "did."),
    "time stop": (
        "The date the model's prediction runs out.\n\n"
        "This is the row most people ignore, and it is the one the model is "
        "most sure about: everything was measured over a fixed window of 20 "
        "trading days, so past this date the reading is not weak — it is "
        "simply not something the model ever tested. Hold longer if you like, "
        "but you are then on your own judgement, not on this."),
    "events": (
        "Scheduled things that could overwhelm the trade — chiefly an "
        "earnings report inside the holding period.\n\n"
        "An earnings date turns a 20-day statistical read into a coin flip on "
        "one announcement, which is a different trade from the one the model "
        "ranked."),
    # Dealer positioning
    "gamma regime": (
        "Whether options dealers' hedging is currently damping the stock's "
        "moves or amplifying them.\n\n"
        "Positioned one way, their hedging sells rallies and buys dips, which "
        "pins the stock. Positioned the other way, the same mechanical "
        "hedging chases the move and makes it bigger. It changes how far you "
        "should expect price to travel in a day."),
    "setup": (
        "A short description of the structure around the current price — "
        "which levels are near, and what they imply for the next move."),
    "flip": (
        "The price where dealer hedging switches from damping moves to "
        "amplifying them.\n\n"
        "Above it, expect the stock to be stickier than usual; below it, "
        "expect bigger swings. It is the most useful level on this panel, "
        "because crossing it changes the character of the trading, not just "
        "the price."),
    "call wall": (
        "The price with the heaviest call-option interest above the current "
        "price.\n\n"
        "Dealer hedging tends to act as a brake here, so rallies often stall "
        "into it. Useful for choosing where to take profit, and a poor place "
        "to buy."),
    "put wall": (
        "The price with the heaviest put-option interest below the current "
        "price.\n\n"
        "It tends to act as a floor for the same mechanical reason the call "
        "wall acts as a ceiling. A break below one is more meaningful than an "
        "ordinary down day, because the support was structural."),
    "atm iv": (
        "How much movement the options market is pricing into this stock, "
        "read from options struck near the current price.\n\n"
        "High means options are expensive and selling premium is favoured; "
        "low means buying it is. Compare it with how much the stock has "
        "actually been moving — the gap between those two is the edge in most "
        "options trades."),
}


def _norm(key):
    return " ".join((key or "").split()).strip().lower()


def column_help(key):
    """Explain a table column by the header text the page renders."""
    return _COLUMNS.get(_norm(key), "")


def row_help(key):
    """Explain a labelled row on the trade plan or the dealer panel."""
    return _ROWS.get(_norm(key), "")


# ── everything else: panel titles, headline numbers, controls ───────────────

_GENERAL = {
    "market_state": (
        "What the broad market is doing right now, and what that permits.\n\n"
        "Two things are checked: whether the S&P is above its 200-day average "
        "and whether that average is still rising, plus the app's own market "
        "regime read. The result is the pair of chips beside this line — one "
        "for the long side, one for the short side. Hover either for what it "
        "means."),
    "recommendation": (
        "What to do about this stock over the next few weeks, and how much "
        "weight to put on it.\n\n"
        "The action combines two separate things: the model's ranking of this "
        "name, and whether the broad market permits acting on it directly. "
        "That is why a name the model ranks at the very bottom can still "
        "read 'Pair short' rather than 'Sell short' — the model "
        "predicts the stock will LAG the index, which in a rising market "
        "can still mean it goes up.\n\n"
        "The confidence chip beside it is not decoration. Read the two "
        "together: an action with Very low confidence is a tilt worth a "
        "small position, not a conviction trade."),
    "confidence": (
        "How well this band has actually worked, from its own history.\n\n"
        "It is the distance from a coin flip, not the raw percentage — "
        "so a band that beat the index only 44% of the time is a STRONG "
        "reading on the short side, not a weak one. Moderate means the "
        "band was at least 5 percentage points away from 50/50; Low "
        "means at least 2; Very low means the historical outcome is "
        "within noise of a coin flip.\n\n"
        "Even Moderate is a small edge by everyday standards. It shows "
        "up across many trades, not in any single one, which is why "
        "sizing matters more here than picking."),
    "position_panel": (
        "A ranking for a holding period of roughly one to eight weeks, from a "
        "model fitted and tested on five years of history.\n\n"
        "It predicts how this stock will do COMPARED WITH the S&P 500 over "
        "the next 20 trading days. It does not predict whether the stock goes "
        "up. In a falling market its best names can lose money and still have "
        "been right.\n\n"
        "Treat it as a tilt within a list, not as a trade call on one name."),
    "investor_panel": (
        "A verdict for a holding period of months or longer, built from the "
        "company's fundamentals and how the stock has performed against the "
        "market and its sector.\n\n"
        "Unlike the Short Term card beside it, this is not a backtested "
        "model — "
        "it is a weighted scorecard. Read it as a structured summary of the "
        "business, not as a tested prediction."),
    "investor_verdict": (
        "Buy, Hold or Sell for a months-plus horizon, from the total score "
        "beside it.\n\n"
        "Two checks can override the score down to Hold no matter how good it "
        "looks: a sector in a confirmed downtrend, and negative cash flow "
        "paired with a missed quarter. The second cannot currently fire, "
        "because the cash-flow figure is not published in this data feed."),
    "band_rail": (
        "Where this stock's score falls among the model's five bands, drawn "
        "left to right from weakest to strongest.\n\n"
        "The marker's position is the band, not a percentage of anything on "
        "screen today. The bands are fixed thresholds cut from years of the "
        "model's own output."),
    "band_stats": (
        "What this band has historically been worth: the average return "
        "compared with the S&P over 20 trading days, and how often names in "
        "this band actually beat it.\n\n"
        "Both come from thousands of past examples. Note how close the hit "
        "rate is to a coin flip even at the top — that is what a real but "
        "small edge looks like, and it is why sizing matters more than any "
        "single call."),
    "dealer_panel": (
        "The levels created by options dealers needing to hedge their books, "
        "drawn in price order with the current price marked.\n\n"
        "These are not opinions about value; they are places where somebody "
        "is mechanically obliged to buy or sell, which is why price so often "
        "reacts at them. The whole panel is withheld when the data is missing "
        "or stale, because a stale wall is worse than no wall."),
    "peers_panel": (
        "The same model's score for other stocks in this one's sector, so you "
        "can see whether the reading is about the company or about its "
        "neighbourhood.\n\n"
        "A name scoring in the top band while every peer scores near the "
        "bottom is a genuinely different signal from one where the whole "
        "sector is being lifted together."),
    "mtf_bias": (
        "The direction agreed by several timeframes at once, from the "
        "shortest to the longest.\n\n"
        "Bullish here means the shorter and longer views point the same way. "
        "When they disagree the bias reads neutral, which is honest rather "
        "than unhelpful — it is telling you the timeframes are in conflict."),
    # Keyed by the COMMAND name the shell dispatches ("deepdive"), not by a
    # slug chosen here — the two diverged, and a tooltip keyed on a name
    # nothing passes attaches silently. A test now reads the keys off
    # `trade_shell._REPORTS`, so the two cannot drift again.
    "deepdive": (
        "Builds a long-form written report on this symbol and opens it in a "
        "new browser tab.\n\n"
        "It takes a little while, because it gathers far more than this "
        "screen shows. If nothing appears, check that your browser did not "
        "block the new tab."),
    "deepdive_query": (
        "Sends this symbol's data to Claude for a written second opinion, and "
        "opens the answer in a new browser tab.\n\n"
        "This is the one control here that costs money per click, and its "
        "answer is generated text — useful for framing, never a source of new "
        "facts. If nothing appears, check for a blocked pop-up."),
    "model_stamp": (
        "Which fitted model produced these numbers, shown as the date it was "
        "built.\n\n"
        "The model is fitted offline and refreshed by hand, so an old date "
        "means the weights predate recent market conditions. \"No artifact "
        "loaded\" means no fitted model was found at all, and the page has "
        "fallen back to an older, simpler scorecard."),
    "composite": (
        "Every factor's push added together — the single number the band is "
        "cut from.\n\n"
        "Worth reading beside the bars above it. A composite near zero built "
        "from several large opposing pushes is a genuinely conflicted read; "
        "one near zero because nothing moved is simply a dull stock."),
    "track_record": (
        "How the model itself has been doing: the version in use, how it "
        "scored on data it was never fitted on, and how it is doing on live "
        "readings since it was deployed.\n\n"
        "This card is about the MODEL. The card below it is about this "
        "SYMBOL. Do not read them as one thing."),
    "symbol_history": (
        "The last few times this particular name was analysed, and what "
        "happened afterwards.\n\n"
        "Shown as rows rather than as a statistic on purpose: a handful of "
        "readings of one stock cannot support an average, and printing one "
        "would invite you to trust it."),
    "exposure_note": (
        "How much of the ranking is really a bet on the market rather than a "
        "judgement between stocks.\n\n"
        "Testing found this model's edge comes largely from the "
        "calm-versus-wild axis, which means the top of the list tends to be "
        "the high-volatility names. In a rising market that looks like skill; "
        "in a falling one the same list underperforms. Size accordingly."),
    "hide_gated": (
        "Hides any candidate a safety check flagged, leaving only rows that "
        "passed every check the board runs.\n\n"
        "Flagged rows are shown by default rather than dropped, because the "
        "reason a name was excluded is often more interesting than the names "
        "that were not."),
    "rebuild": (
        "Re-scores the whole list from fresh price data, and rebuilds the "
        "paper book alongside it.\n\n"
        "It takes a minute or two, because it fetches history for every name "
        "in the list. The board is built once a day on its own, so this is "
        "for when you want it sooner."),
    "paper_book": (
        "Positions the model opened for itself, tracked so its calls can be "
        "scored honestly over time.\n\n"
        "Nothing here is a real order and nothing was ever sent to a broker. "
        "It exists so the model's record cannot be quietly rewritten after "
        "the fact."),
    "no_trade": (
        "What would have to change for this to become a trade, and how to get "
        "the exposure anyway if you want it.\n\n"
        "A blocked or middling read is shown rather than hidden. An empty "
        "screen would suggest the model had nothing to say, which is a "
        "different claim from the model saying \"not this one\"."),
}


def help_for(key):
    """Explain a panel, headline or control by its stable slug."""
    return _GENERAL.get(_norm(key).replace(" ", "_"), "")


ALL_TEXTS = (tuple(_FACTORS.values()) + tuple(_COLUMNS.values())
             + tuple(_ROWS.values()) + tuple(_GENERAL.values()))
