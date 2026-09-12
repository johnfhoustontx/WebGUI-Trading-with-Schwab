"""Pure trade-entry rules shared by the Calculator and the Simulator.

Everything the entry panel and the table leg layout DECIDE lives here, so it is
tested without a browser: stepping a strike along the real chain ladder, what a
grid click turns into, when a leg's price re-fills from the chain, and the
recalculation debounce. No nicegui import.
"""
import math

#: A grid click's column → the leg's side. Clicking the Bid sells, the Ask buys
#: (the thinkorswim convention).
_PICK_SIDE = {"bid": "short", "ask": "long"}
_PICK_TYPES = ("call", "put")
#: Changing one of these makes the leg a different CONTRACT, so its price is no
#: longer that contract's price.
_REFILL_FIELDS = ("strike", "expiry", "option_type")


def _ladder(strikes):
    return sorted({float(s) for s in (strikes or [])
                   if isinstance(s, (int, float)) and not isinstance(s, bool)
                   and math.isfinite(s)})


def step_strike(strikes, current, step):
    """The strike ``step`` rungs from ``current`` on the real ladder.

    ``current`` is snapped to the nearest real strike first (step 0 is a plain
    snap), and the result stops at either end rather than wrapping. No ladder →
    None; no current strike → the bottom rung."""
    xs = _ladder(strikes)
    if not xs:
        return None
    if current is None:
        return xs[0]
    snapped = min(xs, key=lambda k: abs(k - float(current)))
    i = xs.index(snapped) + int(step)
    return xs[max(0, min(i, len(xs) - 1))]


def parse_strike_text(text, strikes):
    """Typed strike text → the nearest real strike, or None when it is not a number."""
    try:
        v = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return step_strike(strikes, v, 0)


def leg_from_pick(column, option_type, strike, expiry, price):
    """A chain-grid click → a one-contract leg.

    ⚠ ``price`` is the MARK whichever column was clicked; the column decides only
    long or short. Pricing a sell at the bid would make every trade look worse by
    the full spread and disagree with the IV the page implies from the mark."""
    if column not in _PICK_SIDE:
        raise ValueError(f"not a pick column: {column!r}")
    if option_type not in _PICK_TYPES:
        raise ValueError(f"not an option type: {option_type!r}")
    return {"option_type": option_type, "side": _PICK_SIDE[column],
            "strike": float(strike), "expiry": expiry, "qty": 1, "premium": price}


def should_refill(field, manual):
    """Whether an edit to ``field`` re-fills the leg's price from the chain.

    Only a change of CONTRACT does, and never over a price the user typed — that
    stays until they reset it."""
    return field in _REFILL_FIELDS and not manual


class Debounce:
    """Fire once, ``delay`` seconds after the LAST poke. Time is passed in, so the
    page drives it from a timer and tests drive it with plain numbers."""

    def __init__(self, delay):
        self.delay = float(delay)
        self._due = None

    @property
    def pending(self):
        return self._due is not None

    def poke(self, now):
        self._due = float(now) + self.delay

    def cancel(self):
        self._due = None

    def ready(self, now):
        if self._due is not None and float(now) >= self._due:
            self._due = None
            return True
        return False
