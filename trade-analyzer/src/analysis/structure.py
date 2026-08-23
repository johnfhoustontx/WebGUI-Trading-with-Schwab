"""Which option structure expresses a read — a pure lookup, not a prediction.

Three inputs the app already knows decide it: which SIDE cleared, whether IV is
cheap or rich, and where the dealer walls sit. Nothing here scores or forecasts;
it turns a read the model already made into a way of holding it.

**Tenor.** The model predicts 20 TRADING days, so debits buy 30–45 DTE — the
thesis window sits inside the option's life with theta still shallow — and
credits sell 20–35 DTE, into the decay. Expressing a 20-day read in 0-DTEs or
LEAPS is the horizon mismatch this whole program has been unpicking.

**Clearance outranks IV.** A tape that has not cleared a directional short does
not become permissive because premium happens to be rich. Clearance is a gate;
the IV read is what chooses the instrument once through it.

**Walls set the short strike.** Dealer supply sits at the call wall and dealer
cushion at the put wall, so a short premium strike belongs BEYOND the relevant
one — the same rule ``scanner_engine.passes_wall`` already applies to scanner
signals, reused on a new surface. Walls are withheld off-hours by design, and
when they are the structure survives while only the strike guidance goes quiet.
"""

# IV-rank bands. "unknown" deliberately lands on MID: IV rank builds forward
# from the first run, so absent is common, and reading it as cheap would
# recommend BUYING premium on no information.
_CHEAP_BELOW = 30.0
_RICH_ABOVE = 60.0

_DEBIT_DTE = (30, 45)
_CREDIT_DTE = (20, 35)


def iv_state_from_rank(rank):
    """``"cheap"`` / ``"mid"`` / ``"rich"`` from an IV rank, or ``"mid"``."""
    try:
        r = float(rank)
    except (TypeError, ValueError):
        return "mid"
    if r != r:                      # NaN
        return "mid"
    if r < _CHEAP_BELOW:
        return "cheap"
    if r > _RICH_ABOVE:
        return "rich"
    return "mid"


def _blank(rationale):
    return {"structure": None, "action": "none", "dte_min": None,
            "dte_max": None, "short_strike_guidance": "", "rationale": rationale}


def _guidance(side, call_wall, put_wall, spot):
    """Where a SHORT premium strike belongs, in words, or ``""``."""
    try:
        spot = float(spot)
    except (TypeError, ValueError):
        return ""
    if spot <= 0:
        return ""
    if side == "short" and call_wall is not None:
        return (f"place the short strike above the {call_wall:g} call wall — "
                f"dealer supply sits there")
    if side == "long" and put_wall is not None:
        return (f"place the short strike below the {put_wall:g} put wall — "
                f"dealer hedging cushions a decline there")
    return ""


def choose(side, iv_state, clearance="cleared", call_wall=None, put_wall=None,
           spot=None):
    """The structure for this read.

    ``side``: ``"long"`` / ``"short"``. ``iv_state``: ``"cheap"`` / ``"mid"`` /
    ``"rich"``. ``clearance``: ``"cleared"`` / ``"relative_only"`` /
    ``"blocked"`` (see ``market_filter``). Never raises."""
    side = (side or "").strip().lower()
    iv_state = (iv_state or "mid").strip().lower()
    clearance = (clearance or "cleared").strip().lower()

    if side not in ("long", "short"):
        return _blank("No direction to express.")

    if clearance == "blocked":
        return _blank("The tape blocks this side — nothing to express.")

    if clearance == "relative_only":
        # The model's label IS a relative one: 20-day forward EXCESS return vs
        # SPY. When the tape has not cleared a directional version, the pair is
        # not a fallback — it is the expression the prediction literally makes.
        other = "top-decile" if side == "short" else "bottom-decile"
        return {
            "structure": f"pair vs a {other} name",
            "action": "relative",
            "dte_min": _DEBIT_DTE[0], "dte_max": _DEBIT_DTE[1],
            "short_strike_guidance": "",
            "rationale": ("The model predicts EXCESS return vs SPY, and the "
                          "tape has not cleared a directional version of it — "
                          "so express it relatively, premium-balanced."),
        }

    if iv_state == "rich":
        structure = "call credit spread" if side == "short" else "put credit spread"
        return {
            "structure": structure, "action": "credit",
            "dte_min": _CREDIT_DTE[0], "dte_max": _CREDIT_DTE[1],
            "short_strike_guidance": _guidance(side, call_wall, put_wall, spot),
            "rationale": ("IV is rich against its own history — be the seller, "
                          "and lean the short strike on dealer positioning."),
        }

    structure = "put debit spread" if side == "short" else "call debit spread"
    cheap = iv_state == "cheap"
    return {
        "structure": structure, "action": "debit",
        "dte_min": _DEBIT_DTE[0], "dte_max": _DEBIT_DTE[1],
        "short_strike_guidance": "",
        "rationale": ("IV is cheap — pay for convexity rather than sell it."
                      if cheap else
                      "IV is unremarkable — a defined-risk debit spread inside "
                      "the expected move."),
    }
