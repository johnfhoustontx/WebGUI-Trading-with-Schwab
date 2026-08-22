"""Dealer positioning and volatility context for one analyzed symbol.

The stack already collects per-strike GEX/charm/DEX/vanna every minute for ~93
symbols and publishes ``call_wall`` / ``put_wall`` / ``flip`` / ``net_gex`` /
``atm_iv`` / ``iv_state`` / ``dealer_regime`` per symbol in
``cache:options:matrix`` — and the Trade Analyzer has never read a byte of it.
This joins one row onto an analysis so the page can say what dealer positioning
implies for the name in front of you.

**It is context, never a scoring input.** The house pattern is that positioning
gates and informs (``scanner_engine.apply_gex_gate``, ``rescue``), and only the
IC-tested harness grants weight. Nothing here touches a verdict.

**Two ways a wall is untrustworthy**, both mirrored from the Desk's own guard
rather than reinvented — see ``webgui/pages/desk._walls_trustworthy``:

* **stale** — the collector has stopped, so the levels describe an earlier tape;
* **net GEX present-but-exactly-zero** — index option open interest reads 0
  after hours, yielding an all-zero grid whose "wall" is the argmax tie-break:
  an arbitrary strike wearing the authority of a level. An *absent* net GEX is
  NOT that signature (the symbol simply doesn't publish the figure), so it keeps
  its walls.

Pure: the caller supplies the row and the payload timestamp.
"""
import datetime as dt

# Beyond this the collector has effectively stopped for our purposes. The
# matrix rides a 1-minute tick, so anything past a few minutes is already a
# different tape; 15 minutes is generous enough to survive a slow cycle.
MAX_AGE_MINUTES = 15.0

_REGIME_WORDS = {"above": "long gamma — dealers damp moves, expect pinning",
                 "below": "short gamma — dealers amplify moves, expect runs"}

_SETUP_WORDS = {
    "gamma_cascade": "Cascade — below the flip with IV spiking",
    "vanna_squeeze": "Vol crush — above the flip with IV collapsing",
    "delta_wall_pin": "Pin — parked on a wall into the close",
    "charm_grind": "Grind — charm drift into the close",
}


def _num(v):
    """A real number, or None. Rejects NaN and bool (``float(True)`` is 1.0)."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _age_minutes(ts, now):
    try:
        t = dt.datetime.fromisoformat(str(ts))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return (now - t).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def _pct_from(spot, level):
    if spot is None or level is None or spot <= 0:
        return None
    return 100.0 * (level - spot) / spot


def build(row, ts=None, now=None):
    """Dealer/IV context for one matrix row (or None if uncollected).

    ``row`` is a ``cache:options:matrix`` row; ``ts`` the payload's timestamp.
    Never raises — every field degrades to None and the summary to a sentence
    saying why."""
    now = now or dt.datetime.now(dt.timezone.utc)

    if row is None:
        return {"collected": False, "stale": False, "walls_trustworthy": False,
                "spot": None, "flip": None, "call_wall": None, "put_wall": None,
                "call_wall_pct": None, "put_wall_pct": None, "net_gex": None,
                "atm_iv": None, "iv_state": None, "gamma_regime": None,
                "regime_words": "", "setup_words": "",
                "summary": "Not collected — this symbol is outside the "
                           "gamma-collection universe."}

    row = row if isinstance(row, dict) else {}
    age = _age_minutes(ts, now)
    stale = age is None or age > MAX_AGE_MINUTES

    spot = _num(row.get("spot"))
    flip = _num(row.get("flip"))
    net_gex = _num(row.get("net_gex"))
    atm_iv = _num(row.get("atm_iv"))

    # Mirrors desk._walls_trustworthy. Absent net GEX is not the zero signature.
    trustworthy = (not stale) and not (net_gex is not None and net_gex == 0.0)

    call_wall = _num(row.get("call_wall")) if trustworthy else None
    put_wall = _num(row.get("put_wall")) if trustworthy else None

    gamma_regime = row.get("gex_regime")
    gamma_regime = gamma_regime if isinstance(gamma_regime, str) else None
    regime_words = _REGIME_WORDS.get(gamma_regime or "", "")

    dealer_regime = row.get("dealer_regime")
    dealer_regime = dealer_regime if isinstance(dealer_regime, str) else None
    setup_words = _SETUP_WORDS.get(dealer_regime or "", "")

    iv_state = row.get("iv_state")
    iv_state = iv_state if isinstance(iv_state, str) else None

    out = {
        "collected": True,
        "stale": stale,
        "age_minutes": age,
        "walls_trustworthy": trustworthy,
        "spot": spot, "flip": flip,
        "call_wall": call_wall, "put_wall": put_wall,
        "call_wall_pct": _pct_from(spot, call_wall),
        "put_wall_pct": _pct_from(spot, put_wall),
        "net_gex": net_gex, "atm_iv": atm_iv, "iv_state": iv_state,
        "gamma_regime": gamma_regime,
        "regime_words": regime_words,
        "setup_words": setup_words,
    }
    out["summary"] = _summary(out)
    return out


def _summary(c):
    """One readable sentence. Says 'stale' rather than quoting levels that
    describe an earlier tape."""
    if c["stale"]:
        return ("Dealer positioning is stale — the collector has not published "
                "recently, so levels are withheld.")
    bits = []
    if c["regime_words"]:
        bits.append(c["regime_words"])
    walls = []
    if c["call_wall"] is not None:
        walls.append("call wall %g%s" % (
            c["call_wall"],
            "" if c["call_wall_pct"] is None else " (%+.1f%%)" % c["call_wall_pct"]))
    if c["put_wall"] is not None:
        walls.append("put wall %g%s" % (
            c["put_wall"],
            "" if c["put_wall_pct"] is None else " (%+.1f%%)" % c["put_wall_pct"]))
    if walls:
        bits.append(" · ".join(walls))
    if c["setup_words"]:
        bits.append(c["setup_words"])
    if c["atm_iv"] is not None:
        iv = "ATM IV %.1f%%" % c["atm_iv"]
        if c["iv_state"] and c["iv_state"] != "na":
            iv += " (%s)" % c["iv_state"]
        bits.append(iv)
    return " · ".join(bits) if bits else "No dealer levels published for this symbol."
