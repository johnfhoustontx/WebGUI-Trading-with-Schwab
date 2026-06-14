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

    def is_sufficient(self) -> bool:
        """True if at least 3 of {pe_ratio, rev_growth_ttm, eps_growth_ttm, roe} are not None."""
        core = [self.pe_ratio, self.rev_growth_ttm, self.eps_growth_ttm, self.roe]
        return sum(1 for v in core if v is not None) >= 3


def _parse_iso_date(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def parse_schwab_fundamentals(payload: Optional[dict], as_of: str) -> Fundamentals:
    """Parse a Schwab fundamentals payload into a Fundamentals dataclass.

    payload: full Schwab response dict; relevant data lives at payload["fundamental"].
    as_of: ISO date string ("YYYY-MM-DD") used to compute days_to_earnings.

    On None/empty payload, returns an all-None Fundamentals.
    """
    if not payload:
        return Fundamentals()

    fund = payload.get("fundamental")
    if not fund:
        return Fundamentals()

    # Margin expanding: only set when both present
    op_margin = fund.get("operatingMargin")
    op_margin_yoy = fund.get("operatingMarginYoy")
    if op_margin is not None and op_margin_yoy is not None:
        margin_expanding = op_margin > op_margin_yoy
    else:
        margin_expanding = None

    # EPS surprises
    eps_surprises = fund.get("epsSurprises")
    if eps_surprises:
        last_eps_surprise = eps_surprises[-1]
    else:
        last_eps_surprise = None

    # Days to earnings
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
        rev_growth_ttm=fund.get("revGrowthTTM"),
        eps_growth_ttm=fund.get("epsGrowthTTM"),
        roe=fund.get("returnOnEquity"),
        margin_expanding=margin_expanding,
        fcf=fund.get("freeCashFlow"),
        eps_surprises=eps_surprises,
        last_eps_surprise=last_eps_surprise,
        guidance=fund.get("guidanceDirection"),
        days_to_earnings=days_to_earnings,
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
