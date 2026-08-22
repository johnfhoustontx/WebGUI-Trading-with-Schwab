"""Fundamentals wrapper: parses Schwab fundamentals payload into a normalized dataclass.

Missing fields stay None so downstream gate logic can detect "insufficient data".
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


@dataclass
class Fundamentals:
    pe_ratio: Optional[float] = None
    peg_ratio: Optional[float] = None
    rev_growth_ttm: Optional[float] = None
    eps_growth_ttm: Optional[float] = None
    roe: Optional[float] = None
    margin_expanding: Optional[bool] = None
    fcf: Optional[float] = None
    eps_surprises: Optional[List[float]] = None
    last_eps_surprise: Optional[float] = None
    guidance: Optional[str] = None
    days_to_earnings: Optional[int] = None
    # Short interest, in Schwab's own units: PERCENT of float, and days-to-cover.
    # Deliberately NOT normalized to a fraction the way ``roe`` is — squeeze
    # thresholds are naturally written in percent ("over 15% of float"), and the
    # deep-dive engine already consumes these fields as percents. They feed the
    # SHORT-side squeeze gate, never the fundamental read, so they are excluded
    # from ``is_sufficient`` by design.
    short_int_to_float: Optional[float] = None
    short_int_day_to_cover: Optional[float] = None

    def is_sufficient(self) -> bool:
        """True if at least 3 of {pe_ratio, rev_growth_ttm, eps_growth_ttm, roe} are not None."""
        core = [self.pe_ratio, self.rev_growth_ttm, self.eps_growth_ttm, self.roe]
        return sum(1 for v in core if v is not None) >= 3


def _parse_iso_date(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def _pct_to_fraction(v):
    """Schwab change/margin fields are percents (12.76 -> 0.1276)."""
    return v / 100.0 if v is not None else None


def _short_interest_or_none(v):
    """Schwab's short-interest fields, with its 0.0 SENTINEL mapped to None.

    Measured live 2026-08-22: ``shortIntToFloat`` and ``shortIntDayToCover`` are
    present in every ``/instruments`` fundamental payload and populated for NO
    symbol — 0.0 for AAPL, TSLA, GME and CVNA alike, while ``peRatio`` /
    ``returnOnEquity`` / ``marketCapFloat`` in the same response are correct. A
    listed, optionable US equity with literally zero short interest does not
    exist, so 0.0 means "Schwab does not serve this", the same way
    ``volatility = -999`` does on the chain.

    Passing it through as a real reading would silently disable the short-side
    squeeze gate for every symbol forever, with nothing on screen to say why.
    A real source (finviz `Short Float` / `Short Ratio`) has to supply these.
    Negatives are impossible too, so they degrade the same way."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _roe_to_fraction(v):
    """Normalize ROE to a fraction.

    Schwab's ``/instruments`` fundamental returns ROE as a PERCENT (e.g. 141.47
    for AAPL); the legacy speculative payloads passed a fraction (e.g. 0.21).
    Heuristic: a magnitude above 2 (i.e. >200% if it were a fraction) can only
    be a percent, so divide; otherwise treat it as an already-normalized
    fraction. (Trade-off: a genuine 0–2% percent ROE is left as-is — a rare edge
    that does not change its scoring tier.)
    """
    if v is None:
        return None
    return v / 100.0 if abs(v) > 2 else v


def parse_schwab_fundamentals(payload: Optional[dict], as_of: str) -> Fundamentals:
    """Parse a Schwab fundamentals payload into a Fundamentals dataclass.

    payload: full Schwab response dict; relevant data lives at payload["fundamental"].
    as_of: ISO date string ("YYYY-MM-DD") used to compute days_to_earnings.

    Superset parser: the PRIMARY source is the real Schwab
    ``/instruments?projection=fundamental`` shape (``revChangeTTM`` /
    ``epsChangePercentTTM`` in percent, ``returnOnEquity`` in percent,
    ``operatingMarginTTM`` vs ``operatingMarginMRQ``), with the legacy
    speculative field names (``revGrowthTTM`` / ``epsGrowthTTM`` as fractions,
    ``operatingMargin`` / ``operatingMarginYoy``, ``epsSurprises``,
    ``guidanceDirection``, ``nextEarningsDate``) kept as fallbacks. The real
    instruments payload omits earnings date / EPS surprises / guidance / FCF, so
    those degrade to None (the InvestorVerdict tolerates it; the earnings gate
    simply does not fire).

    On None/empty payload, returns an all-None Fundamentals.
    """
    if not payload:
        return Fundamentals()

    fund = payload.get("fundamental")
    if not fund:
        return Fundamentals()

    # Growth: prefer the real Schwab percent fields, fall back to legacy fractions.
    if fund.get("revChangeTTM") is not None:
        rev_growth = _pct_to_fraction(fund.get("revChangeTTM"))
    else:
        rev_growth = fund.get("revGrowthTTM")
    if fund.get("epsChangePercentTTM") is not None:
        eps_growth = _pct_to_fraction(fund.get("epsChangePercentTTM"))
    else:
        eps_growth = fund.get("epsGrowthTTM")

    # Margin expanding: real Schwab exposes TTM vs MRQ (latest quarter); the
    # legacy shape used current vs YoY. Only set when a pair is present.
    op_ttm = fund.get("operatingMarginTTM")
    op_mrq = fund.get("operatingMarginMRQ")
    op_margin = fund.get("operatingMargin")
    op_margin_yoy = fund.get("operatingMarginYoy")
    if op_ttm is not None and op_mrq is not None:
        margin_expanding = op_mrq > op_ttm
    elif op_margin is not None and op_margin_yoy is not None:
        margin_expanding = op_margin > op_margin_yoy
    else:
        margin_expanding = None

    # EPS surprises (not in the instruments payload -> None)
    eps_surprises = fund.get("epsSurprises")
    if eps_surprises:
        last_eps_surprise = eps_surprises[-1]
    else:
        last_eps_surprise = None

    # Days to earnings (not in the instruments payload -> None)
    days_to_earnings: Optional[int] = None
    next_earnings = fund.get("nextEarningsDate")
    if next_earnings:
        try:
            next_dt = _parse_iso_date(next_earnings)
            as_of_dt = _parse_iso_date(as_of)
            days_to_earnings = (next_dt - as_of_dt).days
        except (ValueError, AttributeError):
            days_to_earnings = None

    return Fundamentals(
        pe_ratio=fund.get("peRatio"),
        peg_ratio=fund.get("pegRatio"),
        rev_growth_ttm=rev_growth,
        eps_growth_ttm=eps_growth,
        roe=_roe_to_fraction(fund.get("returnOnEquity")),
        margin_expanding=margin_expanding,
        fcf=fund.get("freeCashFlow"),
        eps_surprises=eps_surprises,
        last_eps_surprise=last_eps_surprise,
        guidance=fund.get("guidanceDirection"),
        days_to_earnings=days_to_earnings,
        short_int_to_float=_short_interest_or_none(fund.get("shortIntToFloat")),
        short_int_day_to_cover=_short_interest_or_none(fund.get("shortIntDayToCover")),
    )


def _finviz_pct_to_fraction(s):
    if not s or s.strip() in ("-", "—"):
        return None
    try:
        return float(s.strip().rstrip("%")) / 100.0
    except ValueError:
        return None


def _finviz_float(s):
    if not s or s.strip() in ("-", "—"):
        return None
    try:
        return float(s.strip().replace(",", ""))
    except ValueError:
        return None


def _parse_finviz_earnings_date(raw, as_of):
    """Parse strings like 'May 28 AMC' / 'May 28/B' into days from as_of."""
    if not raw or raw.strip() in ("-", "—"):
        return None
    cleaned = re.sub(r"\s*(AMC|BMO|/A|/B|/AMC|/BMO)\s*$", "", raw.strip(), flags=re.IGNORECASE)
    today = datetime.strptime(as_of, "%Y-%m-%d").date()
    for fmt in ("%b %d", "%B %d"):
        try:
            d = datetime.strptime(cleaned, fmt).date().replace(year=today.year)
            if d < today:
                d = d.replace(year=today.year + 1)
            return (d - today).days
        except ValueError:
            continue
    return None


def parse_finviz_fundamentals(fund: dict, as_of: str) -> Fundamentals:
    """Adapt a finvizfinance ticker_fundament() dict into Fundamentals."""
    if not fund:
        return Fundamentals()

    rev = _finviz_pct_to_fraction(fund.get("Sales Y/Y TTM"))
    if rev is None:
        # fallback: "Sales past 3/5Y" -> "1.81% 8.71%" - take first number
        sp = fund.get("Sales past 3/5Y")
        if sp:
            rev = _finviz_pct_to_fraction(sp.split()[0])

    eps = _finviz_pct_to_fraction(fund.get("EPS Y/Y TTM")) \
        or _finviz_pct_to_fraction(fund.get("EPS this Y"))

    surprise = _finviz_pct_to_fraction(fund.get("EPS Surprise"))
    surprises = [surprise] if surprise is not None else None

    return Fundamentals(
        pe_ratio=_finviz_float(fund.get("P/E")),
        peg_ratio=_finviz_float(fund.get("PEG")),
        rev_growth_ttm=rev,
        eps_growth_ttm=eps,
        roe=_finviz_pct_to_fraction(fund.get("ROE")),
        margin_expanding=None,    # finvizfinance does not expose YoY op margin
        fcf=None,                 # not in ticker_fundament
        eps_surprises=surprises,
        last_eps_surprise=surprise,
        guidance=None,            # not exposed
        days_to_earnings=_parse_finviz_earnings_date(fund.get("Earnings"), as_of),
    )
