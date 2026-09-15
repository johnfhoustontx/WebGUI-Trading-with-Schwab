"""The Go / No-Go checklist for one candidate - PURE (no UI framework, no bus).

Design 2026-09-15, Part 2. Each check is ``{"key", "label", "tone", "text"}``
with tone ``pos`` / ``warn`` / ``neg`` / ``muted``. A check that does not apply
is OMITTED (never shown as passing); only ``book`` can be ``neg``, because every
other hard gate already removed its failures upstream.

Every input may be None or junk. Each check is guarded on its own, so a bad
field costs that one line ("Couldn't check") rather than the whole list - and
the guard logs, because a line that silently reads grey on every row is a bug
with nowhere else to surface.
"""
import datetime as _dt
import logging
import math

from shared import book_caps

from ..fmt import num
from . import book_fit, ev

log = logging.getLogger(__name__)

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


def _check(key, tone, text, **extra):
    return {"key": key, "label": LABELS[key], "tone": tone, "text": text, **extra}


def _dict(v):
    return v if isinstance(v, dict) else {}


def _strike(v):
    """``100`` for 100.0, ``97.5`` for 97.5 - a strike as a trader writes it."""
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}".rstrip("0").rstrip(".")


def _pct(v):
    """``8`` for 8.0, ``10.1`` for 10.1 - never rounds 10.1 up to a passing 10."""
    return f"{v:.0f}" if v == int(v) else f"{v:.1f}"


def _date(v):
    if not isinstance(v, str) or len(v) < 10:
        return None
    try:
        return _dt.date.fromisoformat(v[:10])
    except ValueError:
        return None


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
    kind = row.get("type")
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
    """The scanner's credit lists carry no ``legs``; a normalized row is short
    premium when its position-signed vega is negative."""
    if not row.get("legs"):
        return True
    vega = num(row.get("net_vega"))
    return vega is not None and vega < 0


def _bias(row):
    bias = row.get("bias")
    if bias is not None:
        return bias
    return {"PCS": "bullish", "CCS": "bearish", "IC": "neutral"}.get(row.get("type"))


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
    day = f"{when:%b} {when.day}"
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


def _em(row, matrix):
    if not _is_short_premium(row):
        return None
    em = num(row.get("em_to_expiry"))
    spot, from_scan = _spot(row, matrix)
    shorts = _short_legs(row)
    if em is None or em <= 0 or spot is None or not shorts:
        return _check("em", "muted", "Expected move not measured")
    # The nearest short decides.
    dist, right, strike = min(
        (((spot - s) if r == "put" else (s - spot)) / em, r, s) for r, s in shorts)
    suffix = " (scan price)" if from_scan else ""
    if dist <= 0:
        return _check("em", "warn", f"Short {_strike(strike)} {right} is at or past the price{suffix}")
    # Tenths truncated so the words and the colour agree: 0.99 reads 0.9, amber.
    tenths = math.floor(dist * 10 + 1e-9)
    text = f"Short {_strike(strike)} {right} is {tenths / 10:.1f} expected moves from price{suffix}"
    return _check("em", "pos" if tenths >= EM_OK * 10 else "warn", text)


def _wall(row, matrix):
    if not _is_short_premium(row):
        return None
    shorts = _short_legs(row)
    if not shorts:
        return _check("wall", "muted", "No short strike to check")
    walls = {"put": num(matrix.get("put_wall")), "call": num(matrix.get("call_wall"))}
    if any(walls[r] is None for r, _ in shorts):
        return _check("wall", "muted", "No wall reading")
    inside, outside = [], []
    for right, strike in shorts:
        wall = walls[right]
        beyond = strike < wall if right == "put" else strike > wall
        side = ("below" if beyond else "above") if right == "put" else \
               ("above" if beyond else "below")
        line = f"Short {_strike(strike)} {right} is {side} the {_strike(wall)} {right} wall"
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
    dte = num(row.get("dte"))
    if row.get("trade_type") == "0-DTE" or (dte is not None and dte <= ZERO_DTE_MAX):
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
    out = []
    for key in ORDER:
        try:
            check = builders[key]()
        except Exception:
            log.warning("checklist line %r failed for %r", key, r.get("id"), exc_info=True)
            check = _check(key, "muted", "Couldn't check")
        if check is not None:
            out.append(check)
    return out


def summary(checks):
    """``{state, text, class}`` for the one-chip verdict."""
    checks = [c for c in (checks or []) if isinstance(c, dict)]
    checked = [c for c in checks if c.get("tone") != "muted"]
    if not checked:
        return {"state": "muted", "text": "unchecked", "class": TONE_CLASS["muted"]}
    blocked = [c for c in checks if c.get("tone") == "neg"]
    if blocked:
        return {"state": "neg",
                "text": "Blocked · " + book_fit.short_reason(blocked[0].get("breach")),
                "class": TONE_CLASS["neg"]}
    cautions = sum(1 for c in checks if c.get("tone") == "warn")
    if cautions:
        word = "caution" if cautions == 1 else "cautions"
        return {"state": "warn", "text": f"{cautions} {word}", "class": TONE_CLASS["warn"]}
    k, n = len(checked), len(checks)
    text = f"Clear · {k} checked" if k == n else f"Clear · {k} of {n} checked"
    return {"state": "pos", "text": text, "class": TONE_CLASS["pos"]}
