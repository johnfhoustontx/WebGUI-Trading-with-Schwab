"""The front-month equity-index futures contract, worked out from the date.

``services/market_svc/symbols.py`` named ``/ESU26`` and ``/NQU26`` as literals
and ``tools/nq_instruments.py`` copied both, with a test to keep the copies
equal. Equal is not current: the September 2026 contract expired on 2026-09-18,
a quote for an expired contract returns nothing at all, and the Macro Board's
two Equity Index Futures tiles sat blank on the public page with every test
green. This module is what both of them read instead.

**The cycle.** E-mini index futures list quarterly: March (H), June (M),
September (U), December (Z). A contract's final settlement is the third Friday
of its month, or the session before when that Friday is a full closure (June
2026: the 19th is Juneteenth, so it settled Thursday the 18th). The Friday is
calendar arithmetic and the closure is the NYSE calendar's; both come from
``shared.market_calendar``, so there is no date list here to maintain.

**The roll.** Volume moves to the next contract before the old one expires.
The convention is the Thursday of the week before the third Friday, eight days
ahead. That offset is the operator's choice, not a fact about the contract, so
it is ``[futures] roll_days_before_expiry`` in ``config/symbols.toml`` (see
``shared.symbols.futures_roll_days``). On the roll date itself the front month
is already the NEXT contract.

Pure: dates in, a ``Contract`` out. No network and no Schwab call. Schwab's
streamer does report ``FUTURE_ACTIVE_SYMBOL`` for a continuous ``/ES``, but the
market service REST-polls ``/quotes``, where the tile has to know its contract
before it asks.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from shared import market_calendar as _mc
from shared import symbols as _symbols

# Contract month -> its CME month code and the label the tile description shows.
# The exchange's own vocabulary, not a tunable: an index future has no other
# listing months.
QUARTER_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}
_MONTH_LABELS = {3: "Mar", 6: "Jun", 9: "Sep", 12: "Dec"}


@dataclass(frozen=True)
class Contract:
    """One futures contract. Every spelling of it comes off this one object, so
    a tile's label, the symbol it quotes and its description cannot name
    different contracts."""

    root: str          # "/ES"
    year: int
    month: int         # 3, 6, 9 or 12
    expiry: date       # final settlement

    @property
    def code(self) -> str:
        """``Z26`` - month code + two-digit year."""
        return f"{QUARTER_CODES[self.month]}{self.year % 100:02d}"

    @property
    def quote_symbol(self) -> str:
        """``/ESZ26`` - what Schwab's ``/quotes`` takes and keys its answer by."""
        return f"{self.root}{self.code}"

    @property
    def display(self) -> str:
        """``/ES[Z26]`` - the tile label."""
        return f"{self.root}[{self.code}]"

    @property
    def label(self) -> str:
        """``Dec 2026`` - for the tile description."""
        return f"{_MONTH_LABELS[self.month]} {self.year}"


def today_ct() -> date:
    """Today's date in Central time, the zone the rest of the calendar uses."""
    return datetime.now(_mc.CT).date()


def expiry_date(year: int, month: int) -> date:
    """Final settlement of the (year, month) quarterly contract."""
    if month not in QUARTER_CODES:
        raise ValueError(f"month {month} is not a quarterly contract month")
    d = _mc.third_friday(year, month)
    return d if _mc.is_trading_day(d) else _mc.prev_trading_day(d)


def front_month(root: str, today=None, roll_days=None) -> Contract:
    """The contract the ``root`` tile should quote on ``today``.

    ``today`` defaults to today's Central date and ``roll_days`` to the config
    value. The front month is the first quarterly contract whose roll date
    (expiry less ``roll_days``) is still ahead, so on the roll date the answer
    has already moved on, and with ``roll_days=0`` it moves on expiry day.
    """
    if today is None:
        today = today_ct()
    if roll_days is None:
        roll_days = _symbols.futures_roll_days()
    # The offset is capped far below one quarter, so the answer is always one
    # of the next five listings: this year's four or next year's March.
    for year in (today.year, today.year + 1):
        for month in QUARTER_CODES:
            expiry = expiry_date(year, month)
            if today < expiry - timedelta(days=roll_days):
                return Contract(root, year, month, expiry)
    raise ValueError(f"no front month for {today} at roll_days={roll_days}")
