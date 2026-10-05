"""Market-dashboard symbol map (pure data — no I/O).

Baked from ``symbol_categories.csv``. Single source of truth for CSV→Schwab
symbol translation, per-symbol risk polarity, and how each tile is sourced
(a plain quote, a computed spread, or an external app value). Everything the
poll + coloring logic needs is a lookup here, so those stay pure + testable.

Each entry:
  csv_symbol   – the symbol as written in the CSV (display fallback)
  display      – tile label
  description  – short muted subtitle
  category     – one of CATEGORY_ORDER
  polarity     – "normal" (value up → risk-on/green) | "inverted" (up → risk-off/red)
  kind         – "quote" (fetch quote_symbol) | "spread" (compute) | "external"
  quote_symbol – Schwab symbol to fetch (kind=="quote"); None otherwise
  value_only   – True for internals Schwab returns with no % change (color by sign)
  spread       – (leg_a, leg_b, mode) for kind=="spread"; mode ∈ {"diff_last","diff_pct"}
  source       – for kind=="external" (e.g. "sentiment_pcr")

The two Equity Index Futures tiles are the one part of this that is NOT fixed
data: a futures contract expires every quarter, so their display, quote symbol
and description are worked out from the date by ``symbol_map()``. Read the map
through that function. There is deliberately no module-level ``SYMBOL_MAP`` -
one resolved at import would keep quoting an expired contract until the service
was next restarted, which is how ``/ES[U26]`` came to be blank in October 2026.
"""

from shared import futures as _futures
# BIG10's membership must match services/options_svc/net_premium's basket
# (a comment used to say so; config/symbols.toml now enforces it).
from shared import symbols as _shared_symbols

# Frame layout order (design §5): macro gauges → tape → rotation.
CATEGORY_ORDER = [
    "Volatility", "Options Sentiment", "Market Internals / Breadth", "Currency",
    "Cash Index", "Equity Index Futures", "Broad-Market ETF", "Top 10",
    "Sector SPDR", "Thematic / Industry ETF",
    "Fixed Income / Credit ETF", "Crypto / Alternatives", "Metals", "Countries",
]
# "Factor / Momentum ETF" (MTUM, SPMO) was dropped 2026-09-27: measured over a
# year of daily bars the two moved together at 0.97, and MTUM tracked SMH (0.93)
# more closely than SPY, so the frame repeated the Thematic semis tiles.

# Frames rendered as a LEADERBOARD: their tiles are emitted ranked by day
# %-change (descending), so the strongest name sits top-left and the rank is
# readable at a glance. These are the peer-comparison frames — one index/sector/
# theme/country against its siblings — where the curated order carries no meaning.
#
# Broad-Market ETF joined the leaderboards on 2026-08-19. Its old curated order
# (SPY/DIA/QQQ/IWM then the equal-weights) was chosen to read as a fixed layout,
# but the six ARE peers of each other — large vs mega vs small cap, cap-weighted
# vs equal-weighted — so which one leads on the day is the single most useful
# thing the frame can say, and a fixed order hides it.
#
# Every OTHER frame keeps its symbol-map order, deliberately: Volatility reads
# VIX then its tenors, Cash Index pairs with Futures. Ranking those would
# destroy a layout that IS the information.
#
# A basket tile (BIG10) is pinned leftmost within its frame — see
# compute.rank_tiles.
SORTED_CATEGORIES = ("Broad-Market ETF", "Top 10", "Sector SPDR",
                     "Thematic / Industry ETF", "Countries")

# The frames an advance/decline count reads: the four EQUITY frames. Counting
# the whole board would let a bid VIX and a rallying Treasury cancel out equity
# selling. The Macro Board's meter keeps its own copy (webgui/pages/market.py,
# Tier 1 cannot import this); shared/tests/test_cross_tier_mirrors.py pins the
# two equal. Read here by market_read.breadth.
BREADTH_CATEGORIES = ("Broad-Market ETF", "Top 10", "Sector SPDR",
                      "Thematic / Industry ETF")


def _q(csv, quote, desc, cat, polarity="normal", value_only=False, prem=False):
    # ``prem=True`` → the tile also shows a per-symbol call/put PREMIUM skew subline
    # (looked up in cache:options:matrix by ``quote``), for the collected-universe
    # names the user wants an options-flow read on.
    return {"csv_symbol": csv, "display": csv, "description": desc, "category": cat,
            "polarity": polarity, "kind": "quote", "quote_symbol": quote,
            "value_only": value_only, "spread": None, "source": None, "prem": prem}


def _spread(csv, leg_a, leg_b, mode, desc, cat, polarity="normal"):
    return {"csv_symbol": csv, "display": csv, "description": desc, "category": cat,
            "polarity": polarity, "kind": "spread", "quote_symbol": None,
            "value_only": False, "spread": (leg_a, leg_b, mode), "source": None}


def _external(csv, source, desc, cat, polarity, display):
    return {"csv_symbol": csv, "display": display, "description": desc, "category": cat,
            "polarity": polarity, "kind": "external", "quote_symbol": None,
            "value_only": False, "spread": None, "source": source}


def _basket(display, members, desc, cat, polarity="normal", prem=False):
    """A composite tile: equal-weighted avg day-move + breadth over ``members``
    (each of which is also fetched as its own quote tile). ``prem=True`` → also
    show a dollar-weighted call/put PREMIUM skew aggregated over the members."""
    return {"csv_symbol": display, "display": display, "description": desc, "category": cat,
            "polarity": polarity, "kind": "basket", "quote_symbol": None,
            "value_only": False, "spread": None, "source": None,
            "basket": tuple(members), "prem": prem}


def _future(root, name, cat, polarity="normal"):
    """A front-month futures tile, e.g. ``_future("/ES", "E-mini S&P 500 future")``.

    Only the ROOT and the contract's name are written here. ``symbol_map()``
    fills ``display`` (``/ES[Z26]``), ``quote_symbol`` (``/ESZ26``) and
    ``description`` ("…, Dec 2026") from the date; until then the entry carries
    the bare root and no quote symbol, so it can never be fetched half-built.
    """
    e = _q(root, None, name, cat, polarity)
    e["future_root"] = root
    return e


_INT = "Market Internals / Breadth"
_SEC = "Sector SPDR"
_THM = "Thematic / Industry ETF"
_BRD = "Broad-Market ETF"
_CTY = "Countries"
_MAG = "Top 10"

# The tile table as written. NOT the map to read - the futures entries here are
# unresolved roots. Every reader goes through ``symbol_map()``.
_ENTRIES = [
    # Volatility (inverted — fear up = risk-off)
    _q("VIX", "$VIX", "CBOE Volatility Index (30-day)", "Volatility", "inverted"),
    _q("VIX1D", "$VIX1D", "1-day volatility index", "Volatility", "inverted"),
    _q("VIX3M", "$VIX3M", "3-month volatility index", "Volatility", "inverted"),
    _q("SKEW", "$SKEW", "CBOE SKEW / tail-risk index", "Volatility", "inverted"),
    # Options Sentiment (external — app's cap-weighted sector P/C; inverted).
    # Display is short ("Put/Call") so it fits the tile; the "cap-weighted sector"
    # detail lives in the description (hover tooltip).
    _external("Put/Call", "sentiment_pcr", "Cap-weighted sector put/call ratio",
              "Options Sentiment", "inverted", "Put/Call"),
    # Net Prem (external — dollar-weighted call-vs-put PREMIUM skew across the ~45
    # collected symbols, from cache:options:matrix; "normal" polarity: more money
    # through calls = risk-on/green. A money-weighted Put/Call, NOT net buying.
    _external("Net Prem", "options_net_prem",
              "Dollar-weighted call vs put premium across ~45 collected symbols "
              "(money-weighted Put/Call, not net buying)",
              "Options Sentiment", "normal", "Net Prem"),
    # CBOE Magnificent Ten Index (Schwab API symbol $MGTN; the ThinkorSwim symbol
    # is IMGTN:CGI, which the market-data API doesn't accept). An index level
    # colored by day %-move (up = risk-on/green).
    _q("MGTN", "$MGTN", "CBOE Magnificent Ten Index", "Options Sentiment"),
    # Market Internals / Breadth (value-only internals + a computed net spread)
    _q("$ADVN", "$ADVN", "NYSE advancing issues", _INT, "normal", value_only=True),
    _q("$DECN", "$DECN", "NYSE declining issues", _INT, "inverted", value_only=True),
    _spread("$ADVN-$DECN", "$ADVN", "$DECN", "diff_last",
            "Net advancers (breadth spread)", _INT, "normal"),
    _q("$TICK", "$TICK", "NYSE TICK", _INT, "normal", value_only=True),
    # Currency (equivalent: UUP; inverted — dollar strength = risk-off). The yen is
    # inverted too: it is the funding currency of the carry trade, so a yen surge is
    # carry being unwound, which is risk-off. The euro and pound stay literal: they
    # rise when the dollar falls, so they read as the mirror of the inverted UUP.
    _q("$DXY", "UUP", "US Dollar Index (via UUP proxy)", "Currency", "inverted"),
    _q("FXY", "FXY", "Japanese yen — Invesco CurrencyShares Yen Trust",
       "Currency", "inverted"),
    _q("FXE", "FXE", "Euro — Invesco CurrencyShares Euro Trust", "Currency"),
    _q("FXB", "FXB", "British pound — Invesco CurrencyShares British Pound Trust",
       "Currency"),
    # Cash Index (prem: per-symbol call/put premium skew subline)
    _q("SPX", "$SPX", "S&P 500 Index", "Cash Index", prem=True),
    _q("NDX", "$NDX", "Nasdaq 100 Index", "Cash Index", prem=True),
    # Equity Index Futures — the front-month contract, resolved by symbol_map()
    _future("/ES", "E-mini S&P 500 future", "Equity Index Futures"),
    _future("/NQ", "E-mini Nasdaq 100 future", "Equity Index Futures"),
    # Broad-Market ETF (SPY/DIA/QQQ/IWM carry a premium skew subline)
    _q("SPY", "SPY", "SPDR S&P 500 ETF", _BRD, prem=True),
    _q("DIA", "DIA", "SPDR Dow Jones Industrial Average ETF", _BRD, prem=True),
    _q("QQQ", "QQQ", "Invesco QQQ (Nasdaq 100) ETF", _BRD, prem=True),
    _q("IWM", "IWM", "iShares Russell 2000 ETF", _BRD, prem=True),
    _q("RSP", "RSP", "Invesco S&P 500 Equal Weight ETF", _BRD),
    _q("QQEW", "QQEW", "First Trust Nasdaq-100 Equal Weight ETF", _BRD),
    # Top 10 — a leading equal-weighted composite (avg day-move + breadth + the net
    # call/put PREMIUM skew) over the 10 mega-cap names (the Mag-7 + AVGO/PLTR/AMD),
    # then the constituents.
    _basket("BIG10", _shared_symbols.big10(),
            "Equal-weighted avg day move of the 10 mega-caps (+ N/10 advancing) "
            "and the net call/put premium skew of the 10", _MAG, prem=True),
    _q("NVDA", "NVDA", "NVIDIA", _MAG, prem=True),
    _q("MSFT", "MSFT", "Microsoft", _MAG, prem=True),
    _q("GOOGL", "GOOGL", "Alphabet (Google)", _MAG, prem=True),
    _q("AMZN", "AMZN", "Amazon", _MAG, prem=True),
    _q("META", "META", "Meta Platforms", _MAG, prem=True),
    _q("AAPL", "AAPL", "Apple", _MAG, prem=True),
    _q("TSLA", "TSLA", "Tesla", _MAG, prem=True),
    _q("AVGO", "AVGO", "Broadcom", _MAG, prem=True),
    _q("PLTR", "PLTR", "Palantir Technologies", _MAG, prem=True),
    _q("AMD", "AMD", "Advanced Micro Devices", _MAG, prem=True),
    # Thematic / Industry ETF
    _q("SMH", "SMH", "VanEck Semiconductor ETF", _THM),
    _q("XSD", "XSD", "SPDR S&P Semiconductor ETF", _THM),
    _q("IGV", "IGV", "iShares Expanded Tech-Software Sector ETF", _THM),
    _q("QTUM", "QTUM", "Defiance Quantum ETF", _THM),
    _q("XBI", "XBI", "SPDR S&P Biotech ETF", _THM),
    _q("XRT", "XRT", "SPDR S&P Retail ETF", _THM),
    _q("XME", "XME", "SPDR S&P Metals & Mining ETF", _THM),
    _q("USO", "USO", "United States Oil Fund (WTI crude)", _THM),
    _q("IYT", "IYT", "iShares Transportation Average ETF", _THM),
    # Sector SPDR (all literal up=green — defensive sectors NOT inverted, per design)
    _q("XLB", "XLB", "Materials Select Sector SPDR", _SEC),
    _q("XLC", "XLC", "Communication Services Select Sector SPDR", _SEC),
    _q("XLE", "XLE", "Energy Select Sector SPDR", _SEC),
    _q("XLF", "XLF", "Financials Select Sector SPDR", _SEC),
    _q("XLI", "XLI", "Industrials Select Sector SPDR", _SEC),
    _q("XLK", "XLK", "Technology Select Sector SPDR", _SEC),
    _q("XLP", "XLP", "Consumer Staples Select Sector SPDR", _SEC),
    _q("XLRE", "XLRE", "Real Estate Select Sector SPDR", _SEC),
    _q("XLU", "XLU", "Utilities Select Sector SPDR", _SEC),
    _q("XLV", "XLV", "Health Care Select Sector SPDR", _SEC),
    _q("XLY", "XLY", "Consumer Discretionary Select Sector SPDR", _SEC),
    # Fixed Income / Credit ETF (TLT inverted — flight-to-safety)
    _q("TLT", "TLT", "iShares 20+ Year Treasury Bond ETF", "Fixed Income / Credit ETF", "inverted"),
    _q("HYG", "HYG", "iShares iBoxx High Yield Corp Bond ETF", "Fixed Income / Credit ETF"),
    _q("LQD", "LQD", "iShares iBoxx Investment Grade Corp Bond ETF", "Fixed Income / Credit ETF"),
    # Crypto / Alternatives
    _q("GDLC", "GDLC", "Grayscale CoinDesk Crypto 5 ETF", "Crypto / Alternatives"),
    _q("VCX", "VCX", "Fundrise Innovation Fund (private venture)", "Crypto / Alternatives"),
    # Metals (gold, silver, copper miners; literal up=green)
    _q("GLD", "GLD", "SPDR Gold Shares (gold bullion)", "Metals"),
    _q("SLV", "SLV", "iShares Silver Trust (silver bullion)", "Metals"),
    _q("COPX", "COPX", "Global X Copper Miners ETF", "Metals"),
    # Countries (single-country iShares MSCI ETFs; literal up=green)
    _q("MCHI", "MCHI", "China — iShares MSCI China ETF", _CTY),
    _q("EWJ", "EWJ", "Japan — iShares MSCI Japan ETF", _CTY),
    _q("EWY", "EWY", "South Korea — iShares MSCI South Korea ETF", _CTY),
    _q("INDA", "INDA", "India — iShares MSCI India ETF", _CTY),
    _q("EWT", "EWT", "Taiwan — iShares MSCI Taiwan ETF", _CTY),
    _q("EWZ", "EWZ", "Brazil — iShares MSCI Brazil ETF", _CTY),
    _q("EWA", "EWA", "Australia — iShares MSCI Australia ETF", _CTY),
    _q("EWU", "EWU", "United Kingdom — iShares MSCI United Kingdom ETF", _CTY),
    _q("EWG", "EWG", "Germany — iShares MSCI Germany ETF", _CTY),
    _q("EWQ", "EWQ", "France — iShares MSCI France ETF", _CTY),
    _q("EWW", "EWW", "Mexico — iShares MSCI Mexico ETF", _CTY),
    _q("EWC", "EWC", "Canada — iShares MSCI Canada ETF", _CTY),
]


def symbol_map(today=None):
    """The tile map for ``today`` (default: today, Central time).

    Every entry is the fixed one from the table above except the futures tiles,
    which are rebuilt on each call for the front-month contract on that date.
    The roll offset is ``[futures] roll_days_before_expiry`` in
    ``config/symbols.toml``. The fixed entries are shared objects, so treat the
    result as read-only.
    """
    out = []
    for e in _ENTRIES:
        root = e.get("future_root")
        if root:
            c = _futures.front_month(root, today)
            e = {**e, "csv_symbol": c.display, "display": c.display,
                 "quote_symbol": c.quote_symbol,
                 "description": f"{e['description']}, {c.label}"}
        out.append(e)
    return out


def quote_symbols(entries=None):
    """Deduped list of real Schwab symbols to fetch (kind=='quote' + spread legs
    + basket members). ``entries`` is a ``symbol_map()`` result; pass the one the
    dashboard is then built from, so both name the same futures contract."""
    out = []
    for t in (symbol_map() if entries is None else entries):
        if t["kind"] == "quote" and t["quote_symbol"]:
            out.append(t["quote_symbol"])
        elif t["kind"] == "spread":
            out.extend([t["spread"][0], t["spread"][1]])
        elif t["kind"] == "basket":
            out.extend(t["basket"])
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq
