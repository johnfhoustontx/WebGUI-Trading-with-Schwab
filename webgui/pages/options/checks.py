"""The Go / No-Go checklist for one candidate - PURE (no UI framework, no bus).

Design 2026-09-15, Part 2. Each check is ``{"key", "label", "tone", "text"}``
with tone ``pos`` / ``warn`` / ``neg`` / ``muted``. A check that does not apply
is OMITTED (never shown as passing); only ``book`` can be ``neg``, because every
other hard gate already removed its failures upstream.

A check that is grey BECAUSE a live view it reads was not supplied carries
``"missing_view": True``, and the summary then refuses to say "Clear": a
checklist that could not see the board is only partly checked.

Every input may be None or junk. Each check is guarded on its own, so a bad
field costs that one line ("Couldn't check") rather than the whole list. The
guard logs the FIRST failure of each (check, exception type) and counts the
rest silently: the list is rebuilt on every repaint, so a field broken on every
row would otherwise flood the log.
"""
import datetime as _dt
import logging
import math

from shared import book_caps

from ..fmt import num
from . import book_fit, ev

log = logging.getLogger(__name__)

_MAX_REASONS = 3           # cautions named in a tooltip before it trails off
VOL_MARGIN = 10.0          # points above the floor before Vol rank reads green
FRICTION_OK_PCT = 10.0     # round-trip bid-ask as % of credit/debit
FRICTION_WIDE_PCT = 25.0   # above this the words say "very wide" (still amber)
EM_OK = 1.0                # short strike at least this many expected moves away
ZERO_DTE_MAX = 4           # the 0-DTE bucket spans DTE 0..4

TONE_CLASS = {"pos": "text-emerald-400", "warn": "text-amber-400",
              "neg": "text-rose-400", "muted": "text-[#7f8db0]"}

ORDER = ("book", "earnings", "vol", "cost", "em", "wall", "gamma", "direction", "record")

LABELS = {"book": "Paper book", "earnings": "Earnings", "vol": "Vol rank",
          "cost": "Cost to trade", "em": "Expected move", "wall": "Walls",
          "gamma": "Dealer gamma", "direction": "Direction", "record": "Track record"}

# Structures that SELL premium, by name. A mirror of ``shared.structures``
# LEDGER_CREDIT + SHORT_PUT + COVERED_CALL, plus the scanner's SHORT_CALL (no
# ledger name): Tier 1 does not import ``shared.structures``, so
# test_checks.py pins this against that file's source. A name here wins over the
# row's economics - a covered call's net_debit is mostly the share purchase.
CREDIT_TYPES = ("PCS", "CCS", "IC", "IRON_CONDOR", "SHORT_PUT", "NAKED_PUT",
                "COVERED_CALL", "SHORT_CALL")

# Structures that BUY premium, by name - a mirror of ``shared.structures``
# LEDGER_DEBIT, pinned the same way.
DEBIT_TYPES = ("LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT",
               "BUTTERFLY_CALL", "BUTTERFLY_PUT", "CONDOR_CALL", "CONDOR_PUT")

# Fixed English names: strftime's month abbreviation follows the host locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# (check key, exception type) pairs already logged, and the silent repeat count.
_LOGGED = set()
_REPEATS = {}


def _check(key, tone, text, **extra):
    return {"key": key, "label": LABELS[key], "tone": tone, "text": text, **extra}


def _dict(v):
    return v if isinstance(v, dict) else {}


def _strike(v):
    """``100`` for 100.0, ``97.5`` for 97.5 - a strike as a trader writes it."""
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}".rstrip("0").rstrip(".")


def _tenths(v):
    """``v`` truncated to tenths, so a printed figure never rounds across a
    threshold its colour was decided on (0.99 prints 0.9, 10.19 prints 10.1)."""
    return math.floor(v * 10 + 1e-9) / 10


def _pct(v):
    """``8`` for 8.0, ``10.1`` for 10.1 - truncated like every other figure here."""
    t = _tenths(v)
    return f"{t:.0f}" if t == int(t) else f"{t:.1f}"


def _date(v):
    if not isinstance(v, str) or len(v) < 10:
        return None
    try:
        return _dt.date.fromisoformat(v[:10])
    except ValueError:
        return None


def _type(row):
    kind = row.get("type")
    return kind.strip().upper() if isinstance(kind, str) else ""


def _short_legs(row):
    """``[(right, strike)]`` for every short option leg."""
    legs = row.get("legs")
    if legs:
        if not isinstance(legs, (list, tuple)):
            return []
        out = []
        for leg in legs:
            if not isinstance(leg, dict) or leg.get("side") != "short":
                continue
            kind, strike = leg.get("kind"), num(leg.get("strike"))
            if kind in ("put", "call") and strike is not None:
                out.append((kind, strike))
        return out
    kind = _type(row)
    short, call_short = num(row.get("short_strike")), num(row.get("call_short"))
    if kind == "PCS":
        pairs = [("put", short)]
    elif kind == "CCS":
        pairs = [("call", short)]
    elif kind == "IC":
        pairs = [("put", short), ("call", call_short)]
    else:
        pairs = []
    return [(r, s) for r, s in pairs if s is not None]


def _is_short_premium(row):
    """Whether the trade SELLS premium, read from what it is and what it costs.

    ⚠ Never from ``net_vega`` first. The scanner's ``net_vega`` is
    ``short.vega - long.vega`` - POSITIVE for a credit spread - and
    ``adapt_credit_spread`` carries it across unchanged, while an adapted iron
    condor's legs carry vega 0. Reading its sign made every Finder credit spread
    look like long premium.

    Order: a known credit or debit structure name; then the economics (a
    positive per-share ``credit`` or ``net_credit`` with no positive
    ``net_debit`` sells, a positive ``net_debit`` buys); then, only when neither
    is available, a negative ``net_vega``. A scanner credit row with no legs and
    nothing else to go on stays short premium - those lists hold nothing else.
    """
    kind = _type(row)
    if kind in CREDIT_TYPES:
        return True
    if kind in DEBIT_TYPES:
        return False
    debit = num(row.get("net_debit"))
    if debit is not None and debit > 0:
        return False
    if (num(row.get("credit")) or 0) > 0 or (num(row.get("net_credit")) or 0) > 0:
        return True
    if not row.get("legs"):
        return True
    vega = num(row.get("net_vega"))
    return vega is not None and vega < 0


def _bias(row):
    bias = row.get("bias")
    if bias is not None:
        return bias
    return {"PCS": "bullish", "CCS": "bearish", "IC": "neutral"}.get(_type(row))


def _is_zero_dte(row):
    dte = num(row.get("dte"))
    return row.get("trade_type") == "0-DTE" or (dte is not None and dte <= ZERO_DTE_MAX)


def _same_strike(shorts):
    """A short put and a short call at ONE strike - a short straddle or an iron
    butterfly, whose strikes say nothing about where the trade starts losing."""
    puts = {s for r, s in shorts if r == "put"}
    return any(r == "call" and s in puts for r, s in shorts)


def _breakevens(row):
    """``(lower, upper)`` from ``breakevens`` or a ``"a / b"`` ``breakeven``, or None."""
    raw = row.get("breakevens")
    if isinstance(raw, (list, tuple)):
        values = [num(v) for v in raw]
    else:
        text = row.get("breakeven")
        values = [num(p) for p in text.split("/")] if isinstance(text, str) else []
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    return min(values), max(values)


# ── the checks ────────────────────────────────────────────────────────────


def _book(row, caps, qty):
    if not row.get("_allow_paper"):
        return None
    fit = book_fit.preview(row, caps, qty)
    if not fit.get("available"):
        return _check("book", "muted",
                      fit.get("unavailable_text") or "The paper book's limits aren't available")
    breach = fit.get("breach")
    if breach:
        return _check("book", "neg", book_caps.describe(breach), breach=breach)
    n = fit.get("max_quantity")
    if isinstance(n, int) and n >= 1:
        word = "contract" if n == 1 else "contracts"
        return _check("book", "pos", f"Fits the paper book — up to {n} {word}")
    return _check("book", "pos", "Fits the paper book")


def _earnings(row):
    status = row.get("earnings_status")
    if status == "none_scheduled":
        return _check("earnings", "pos", "No report scheduled")
    if status != "upcoming":
        return _check("earnings", "muted", "No earnings coverage")
    when = _date(row.get("earnings_date"))
    if when is None:
        return _check("earnings", "muted", "Earnings date unknown")
    day = f"{_MONTHS[when.month - 1]} {when.day}"
    expiry = _date(row.get("expiration"))
    if expiry is None:
        return _check("earnings", "muted", f"Earnings {day}, expiry unknown")
    if when == expiry:
        return _check("earnings", "warn", f"Earnings {day}, on expiry day")
    if when < expiry:
        return _check("earnings", "warn", f"Earnings {day}, before expiry")
    return _check("earnings", "pos", f"Earnings {day}, after expiry")


def _vol(row):
    if not _is_short_premium(row):
        return None
    floor = num(row.get("vol_floor"))
    if floor is None or floor <= 0:
        return None
    rank = num(row.get("iv_rank"))
    if row.get("iv_rank_known") is False or rank is None:
        return _check("vol", "muted", "No IV history")
    # Truncated, not rounded: 39.9 must not print as a passing 40.
    text = f"Vol rank {math.floor(rank):.0f} · floor {floor:.0f}"
    return _check("vol", "pos" if rank >= floor + VOL_MARGIN else "warn", text)


def _cost(row):
    friction = num(row.get("friction_pct"))
    if friction is None:
        return _check("cost", "muted", "Bid-ask not measured")
    if (num(row.get("credit")) or 0) > 0 or (num(row.get("net_credit")) or 0) > 0:
        what = "the credit"
    elif (num(row.get("net_debit")) or 0) > 0:
        what = "the debit"
    else:
        what = "the price"
    text = f"Bid-ask round trip {_pct(friction)}% of {what}"
    if friction <= FRICTION_OK_PCT:
        return _check("cost", "pos", text)
    if friction > FRICTION_WIDE_PCT:
        text += " — very wide"
    return _check("cost", "warn", text)


def _spot(row, matrix):
    """``(price, from_scan)`` - the live board price first, the scan's second."""
    live = num(matrix.get("spot"))
    if live is not None:
        return live, False
    return num(row.get("underlying_price")), True


def _em_points(row, shorts):
    """``[(name, price, side)]`` the expected-move and wall checks measure:
    the short strikes, or the breakevens for a same-strike structure. ``None``
    when that structure's breakevens are missing."""
    if _same_strike(shorts):
        bands = _breakevens(row)
        if bands is None:
            return None
        return [(f"Breakeven {_strike(bands[0])}", bands[0], "put"),
                (f"Breakeven {_strike(bands[1])}", bands[1], "call")]
    return [(f"Short {_strike(s)} {r}", s, r) for r, s in shorts]


def _em(row, matrix):
    if not _is_short_premium(row):
        return None
    em = num(row.get("em_to_expiry"))
    spot, from_scan = _spot(row, matrix)
    shorts = _short_legs(row)
    if not shorts:
        return _check("em", "muted", "Expected move not measured")
    points = _em_points(row, shorts)
    if points is None:
        return _check("em", "muted", "Breakevens unknown")
    if em is None or em <= 0 or spot is None:
        return _check("em", "muted", "Expected move not measured")
    # The nearest point decides.
    dist, name = min((((spot - p) if side == "put" else (p - spot)) / em, name)
                     for name, p, side in points)
    suffix = " (scan price)" if from_scan else ""
    if dist <= 0:
        return _check("em", "warn", f"{name} is at or past the price{suffix}")
    tenths = _tenths(dist)
    text = f"{name} is {tenths:.1f} expected moves from the price{suffix}"
    return _check("em", "pos" if tenths >= EM_OK else "warn", text)


def _wall(row, matrix):
    if not _is_short_premium(row):
        return None
    shorts = _short_legs(row)
    if not shorts:
        return _check("wall", "muted", "No short strike to check")
    points = _em_points(row, shorts)
    if points is None:
        return _check("wall", "muted", "Breakevens unknown")
    walls = {"put": num(matrix.get("put_wall")), "call": num(matrix.get("call_wall"))}
    if any(walls[side] is None for _, _, side in points):
        return _check("wall", "muted", "No wall reading")
    inside, outside = [], []
    for name, price, side in points:
        wall = walls[side]
        beyond = price < wall if side == "put" else price > wall
        if price == wall:
            where = "at"
        elif side == "put":
            where = "below" if beyond else "above"
        else:
            where = "above" if beyond else "below"
        line = f"{name} is {where} the {_strike(wall)} {side} wall"
        (outside if beyond else inside).append(line)
    if inside:
        return _check("wall", "warn", " · ".join(inside))
    return _check("wall", "pos", " · ".join(outside))


def _gamma(row, matrix):
    if not _is_short_premium(row):
        return None
    regime = matrix.get("gex_regime")
    if regime == "above":
        return _check("gamma", "pos", "Price above the gamma flip")
    if regime == "below":
        return _check("gamma", "warn", "Price below the gamma flip — moves can accelerate")
    return _check("gamma", "muted", "No gamma flip reading")


def _sign(v):
    """1 / -1 / 0, or None when there is no reading at all."""
    v = num(v)
    if v is None:
        return None
    return 0 if v == 0 else (1 if v > 0 else -1)


def _direction(row, matrix, regime):
    bias = _bias(row)
    want = {"bullish": 1, "bearish": -1}.get(bias) if isinstance(bias, str) else None
    if want is None:
        return None
    if _is_zero_dte(row):
        name = "Today's move"
        sign = 0 if matrix.get("trend_state") == "flat" else _sign(matrix.get("trend_dir"))
    else:
        name = "Market direction"
        sign = _sign(_dict(regime).get("direction"))
    if sign is None:
        return _check("direction", "muted", f"No reading of {name.lower()}")
    if sign == 0:
        return _check("direction", "muted", f"{name} is flat")
    word = "up" if sign > 0 else "down"
    if sign == want:
        return _check("direction", "pos", f"{name} is {word}, with this {bias} trade")
    return _check("direction", "warn", f"{name} is {word}, against this {bias} trade")


def _record(row, calibration):
    if "fit_score" in row:
        return None
    facts = ev.calibrated_facts(row, calibration)
    if not facts:
        return _check("record", "muted", "Not enough history for this score")
    return _check("record", "pos" if facts["ev_r"] > 0 else "warn", facts["text"])


# ── public surface ────────────────────────────────────────────────────────


def _log_once(key, exc):
    seen = (key, type(exc))
    if seen in _LOGGED:
        _REPEATS[seen] = _REPEATS.get(seen, 0) + 1
        return
    _LOGGED.add(seen)
    log.warning("checklist line %r failed (%s); later failures of this kind are "
                "counted, not logged", key, type(exc).__name__, exc_info=exc)


def _missing_views(row, matrix_row, regime, calibration, caps):
    """Which checks, if grey, are grey because a view they read was not given."""
    no_matrix = not isinstance(matrix_row, dict)
    return {"book": not isinstance(caps, dict),
            # The scan's own price stands in for the board's, so only a row with
            # neither is short of a view.
            "em": no_matrix and num(row.get("underlying_price")) is None,
            "wall": no_matrix,
            "gamma": no_matrix,
            "direction": no_matrix if _is_zero_dte(row) else not isinstance(regime, dict),
            "record": not isinstance(calibration, dict)}


def build_checks(row, matrix_row, regime, calibration, caps, qty=1):
    """The checks that apply to ``row``, in :data:`ORDER`. Never raises."""
    r, m = _dict(row), _dict(matrix_row)
    builders = {"book": lambda: _book(r, caps, qty),
                "earnings": lambda: _earnings(r),
                "vol": lambda: _vol(r),
                "cost": lambda: _cost(r),
                "em": lambda: _em(r, m),
                "wall": lambda: _wall(r, m),
                "gamma": lambda: _gamma(r, m),
                "direction": lambda: _direction(r, m, regime),
                "record": lambda: _record(r, calibration)}
    try:
        missing = _missing_views(r, matrix_row, regime, calibration, caps)
    except Exception as exc:
        _log_once("missing_view", exc)
        missing = {}
    out = []
    for key in ORDER:
        try:
            check = builders[key]()
        except Exception as exc:
            _log_once(key, exc)
            check = _check(key, "muted", "Couldn't check")
        else:
            if check is not None and check["tone"] == "muted" and missing.get(key):
                check["missing_view"] = True
        if check is not None:
            out.append(check)
    return out


def verdict(checks):
    """``{state, text, class, short}`` - :func:`summary` plus ``short``, the few
    words a table cell has room for (the full ``text`` goes in its tooltip).

    A CAUTION verdict carries one more key, ``reasons``: the cautions' own words,
    for a hover that would otherwise repeat the cell. ``summary`` keeps it (only
    ``short`` is dropped); the detail panel's headline reads ``text`` and ignores
    it."""
    checks = [c for c in (checks or []) if isinstance(c, dict)]
    checked = [c for c in checks if c.get("tone") != "muted"]
    if not checked:
        return {"state": "muted", "text": "unchecked", "class": TONE_CLASS["muted"],
                "short": "unchecked"}
    blocked = [c for c in checks if c.get("tone") == "neg"]
    if blocked:
        return {"state": "neg",
                "text": "Blocked · " + book_fit.short_reason(blocked[0].get("breach")),
                "class": TONE_CLASS["neg"], "short": "Blocked"}
    warned = [c for c in checks if c.get("tone") == "warn"]
    if warned:
        word = "caution" if len(warned) == 1 else "cautions"
        text = f"{len(warned)} {word}"
        # ``reasons`` is what the TABLE hovers: the chip's own words repeat the
        # cell ("3 cautions"), where the blocked chip names its breach. Capped,
        # because a tooltip is read at a glance.
        reasons = [str(c.get("text") or "") for c in warned[:_MAX_REASONS]]
        if len(warned) > _MAX_REASONS:
            reasons.append("…")
        return {"state": "warn", "text": text, "class": TONE_CLASS["warn"],
                "short": text, "reasons": " · ".join(reasons)}
    k, n = len(checked), len(checks)
    if any(c.get("missing_view") for c in checks):
        return {"state": "muted", "text": f"Partly checked · {k} of {n} checked",
                "class": TONE_CLASS["muted"], "short": "Partly checked"}
    text = f"Clear · {k} checked" if k == n else f"Clear · {k} of {n} checked"
    return {"state": "pos", "text": text, "class": TONE_CLASS["pos"],
            "short": f"Clear · {k} of {n}"}


def summary(checks):
    """``{state, text, class}`` for the one-chip verdict (:func:`verdict` without
    its ``short`` key; a caution verdict's ``reasons`` rides along)."""
    out = verdict(checks)
    del out["short"]
    return out
