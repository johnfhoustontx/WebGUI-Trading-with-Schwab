"""Pure normalization + coloring for the market dashboard (no I/O)."""

_FLAT_PCT = 0.1     # |%change| below this → flat/grey
_STRONG_PCT = 1.0   # |%change| at/above this → strong intensity


def _num(v):
    try:
        f = float(v)
        return f if f == f else 0.0  # NaN → 0
    except (TypeError, ValueError):
        return 0.0


def normalize_quote(raw):
    """(last, change, change_pct) from one raw Schwab per-symbol dict.

    Picks the % field by asset type: FUTURE → futurePercentChange, else
    netPercentChange (falling back to close-derived). Internals come back with
    close/change 0 → change_pct 0 (value-only; colored by sign upstream).
    """
    q = raw.get("quote", raw) if isinstance(raw, dict) else {}
    last = _num(q.get("lastPrice"))
    change = _num(q.get("netChange"))
    asset = (raw.get("assetMainType") or "").upper()
    if asset == "FUTURE":
        pct = _num(q.get("futurePercentChange"))
    else:
        pct = _num(q.get("netPercentChange")) or _num(q.get("netPercentChangeInDouble"))
    if pct == 0.0:
        close = _num(q.get("closePrice"))
        if close:
            pct = (last - close) / close * 100.0
    return last, change, pct


def spread_value(mode, leg_a, leg_b):
    """Compute a spread tile's (last, change, change_pct) from two legs.

    Each leg is a (last, change, pct) tuple. ``diff_last`` = a.last − b.last
    (used as both value and the color-driving 'change'); ``diff_pct`` =
    a.pct − b.pct (relative day performance).
    """
    al, _ac, ap = leg_a
    bl, _bc, bp = leg_b
    if mode == "diff_pct":
        d = ap - bp
        return d, d, d
    d = al - bl
    return d, d, 0.0


def color_state(effective_change, *, polarity="normal", value_only=False):
    """Map a signed move to a color bucket, applying polarity.

    ``effective_change`` = the % change (or value sign for value_only). Returns
    one of: risk_on_strong / risk_on_mild / flat / risk_off_mild /
    risk_off_strong / no_data. ``polarity=="inverted"`` flips green↔red (VIX up =
    risk-off). ``value_only`` collapses to a single (mild) intensity by sign.
    """
    if effective_change is None:
        return "no_data"
    signed = effective_change * (-1.0 if polarity == "inverted" else 1.0)
    if value_only:
        if signed > 0:
            return "risk_on_mild"
        if signed < 0:
            return "risk_off_mild"
        return "flat"
    mag = abs(signed)
    if mag < _FLAT_PCT:
        return "flat"
    intensity = "strong" if mag >= _STRONG_PCT else "mild"
    side = "risk_on" if signed > 0 else "risk_off"
    return f"{side}_{intensity}"
