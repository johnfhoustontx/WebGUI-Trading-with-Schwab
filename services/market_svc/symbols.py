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
"""

# Frame layout order (design §5): macro gauges → tape → rotation.
CATEGORY_ORDER = [
    "Volatility", "Options Sentiment", "Market Internals / Breadth", "Currency",
    "Cash Index", "Equity Index Futures", "Broad-Market ETF",
    "Sector SPDR", "Thematic / Industry ETF", "Factor / Momentum ETF",
    "Fixed Income / Credit ETF", "Crypto / Alternatives", "Countries",
]


def _q(csv, quote, desc, cat, polarity="normal", value_only=False):
    return {"csv_symbol": csv, "display": csv, "description": desc, "category": cat,
            "polarity": polarity, "kind": "quote", "quote_symbol": quote,
            "value_only": value_only, "spread": None, "source": None}


def _spread(csv, leg_a, leg_b, mode, desc, cat, polarity="normal"):
    return {"csv_symbol": csv, "display": csv, "description": desc, "category": cat,
            "polarity": polarity, "kind": "spread", "quote_symbol": None,
            "value_only": False, "spread": (leg_a, leg_b, mode), "source": None}


def _external(csv, source, desc, cat, polarity, display):
    return {"csv_symbol": csv, "display": display, "description": desc, "category": cat,
            "polarity": polarity, "kind": "external", "quote_symbol": None,
            "value_only": False, "spread": None, "source": source}


_INT = "Market Internals / Breadth"
_SEC = "Sector SPDR"
_THM = "Thematic / Industry ETF"
_BRD = "Broad-Market ETF"
_CTY = "Countries"

SYMBOL_MAP = [
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
    # Market Internals / Breadth (value-only internals + a computed net spread)
    _q("$ADVN", "$ADVN", "NYSE advancing issues", _INT, "normal", value_only=True),
    _q("$DECN", "$DECN", "NYSE declining issues", _INT, "inverted", value_only=True),
    _spread("$ADVN-$DECN", "$ADVN", "$DECN", "diff_last",
            "Net advancers (breadth spread)", _INT, "normal"),
    _q("$TICK", "$TICK", "NYSE TICK", _INT, "normal", value_only=True),
    # Currency (equivalent: UUP; inverted — dollar strength = risk-off)
    _q("$DXY", "UUP", "US Dollar Index (via UUP proxy)", "Currency", "inverted"),
    # Cash Index
    _q("SPX", "$SPX", "S&P 500 Index", "Cash Index"),
    _q("NDX", "$NDX", "Nasdaq 100 Index", "Cash Index"),
    # Equity Index Futures
    _q("/ES[U26]", "/ESU26", "E-mini S&P 500 future, Sep 2026", "Equity Index Futures"),
    _q("/NQ[U26]", "/NQU26", "E-mini Nasdaq 100 future, Sep 2026", "Equity Index Futures"),
    # Broad-Market ETF
    _q("SPY", "SPY", "SPDR S&P 500 ETF", _BRD),
    _q("DIA", "DIA", "SPDR Dow Jones Industrial Average ETF", _BRD),
    _q("QQQ", "QQQ", "Invesco QQQ (Nasdaq 100) ETF", _BRD),
    _q("IWM", "IWM", "iShares Russell 2000 ETF", _BRD),
    _q("RSP", "RSP", "Invesco S&P 500 Equal Weight ETF", _BRD),
    _q("QQEW", "QQEW", "First Trust Nasdaq-100 Equal Weight ETF", _BRD),
    # Factor / Momentum ETF
    _q("MTUM", "MTUM", "iShares MSCI USA Momentum Factor ETF", "Factor / Momentum ETF"),
    _q("SPMO", "SPMO", "Invesco S&P 500 Momentum ETF", "Factor / Momentum ETF"),
    # Thematic / Industry ETF
    _q("SMH", "SMH", "VanEck Semiconductor ETF", _THM),
    _q("XSD", "XSD", "SPDR S&P Semiconductor ETF", _THM),
    _q("IGV", "IGV", "iShares Expanded Tech-Software Sector ETF", _THM),
    _q("QTUM", "QTUM", "Defiance Quantum ETF", _THM),
    _q("XBI", "XBI", "SPDR S&P Biotech ETF", _THM),
    _q("XRT", "XRT", "SPDR S&P Retail ETF", _THM),
    _q("XME", "XME", "SPDR S&P Metals & Mining ETF", _THM),
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
    # Countries (single-country iShares MSCI ETFs; literal up=green)
    _q("MCHI", "MCHI", "China — iShares MSCI China ETF", _CTY),
    _q("EWJ", "EWJ", "Japan — iShares MSCI Japan ETF", _CTY),
    _q("EWY", "EWY", "South Korea — iShares MSCI South Korea ETF", _CTY),
    _q("INDA", "INDA", "India — iShares MSCI India ETF", _CTY),
    _q("EWT", "EWT", "Taiwan — iShares MSCI Taiwan ETF", _CTY),
    _q("EWZ", "EWZ", "Brazil — iShares MSCI Brazil ETF", _CTY),
    _q("EWA", "EWA", "Australia — iShares MSCI Australia ETF", _CTY),
    _q("EWU", "EWU", "United Kingdom — iShares MSCI United Kingdom ETF", _CTY),
    _q("EWW", "EWW", "Mexico — iShares MSCI Mexico ETF", _CTY),
    _q("EWC", "EWC", "Canada — iShares MSCI Canada ETF", _CTY),
]


def quote_symbols():
    """Deduped list of real Schwab symbols to fetch (kind=='quote' + spread legs)."""
    out = []
    for t in SYMBOL_MAP:
        if t["kind"] == "quote" and t["quote_symbol"]:
            out.append(t["quote_symbol"])
        elif t["kind"] == "spread":
            out.extend([t["spread"][0], t["spread"][1]])
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq
