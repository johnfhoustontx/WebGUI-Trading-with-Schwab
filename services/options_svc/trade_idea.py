"""The hourly trade idea: pick ONE graded trade off the live Market Scanner and
turn it into the plain shape the social card draws.

Design: docs/plans/2026-09-17-hourly-trade-idea-post-design.md.

Nothing here scores a trade. The scanner already graded every row it publishes to
``cache:options:scan``; this module only chooses among them and normalises the
two row shapes the scan carries into one:

* **credit rows** (``signals_0dte`` / ``signals_swing``: PCS, CCS, IC) keep their
  strikes in flat fields -- ``short_strike``/``long_strike``, plus
  ``call_short``/``call_long`` on an IC only -- and their money PER SHARE
  (``net_credit`` 0.26 means $26 a contract).
* **directional rows** (``signals_directional``) carry a ``legs`` list and their
  money PER CONTRACT (``net_debit`` 2515.0 means $2,515).

⚠ Risk, profit and breakevens are all read off ONE payoff function built from the
legs and the net entry cash, never from the rows' own ``max_loss`` fields. The
two shapes use different units for those fields, and the card draws the payoff
curve beside the numbers: if the numbers came from one place and the curve from
another, a units slip would put a $26 profit beside a curve peaking at $2,600.

PURE: no bus, no Schwab, no clock of its own. Every function takes what it reads.
"""
import datetime as _dt
import math
import pathlib

MULT = 100

#: Grades eligible to post, best first. "Marginal" and "Weak" never post: a public
#: feed of trades the app itself calls marginal is worse than a skipped hour.
DEFAULT_GRADES = ("Strong", "Good")

#: A scan older than this is not posted. The autoscan runs every 15 minutes, so
#: 45 covers one missed run; anything older is a stalled service, and a post
#: would price a trade off a market that has moved.
DEFAULT_MAX_AGE_MIN = 45

#: A post must have at least this many calendar days to expiration. Measured on
#: prod's 15:00 CT scan of 2026-09-16: without it, six of the seven picks were
#: SAME-DAY long options costing $15-$33 -- priced correctly for the minute they
#: were scanned, and gone before a reader of the post could act on them.
DEFAULT_MIN_DTE = 1

_SCAN_LISTS = ("signals_0dte", "signals_swing", "signals_directional")

_CREDIT_TYPES = ("PCS", "CCS", "IC")

STRATEGY_LABELS = {
    "PCS": "Put Credit Spread",
    "CCS": "Call Credit Spread",
    "IC": "Iron Condor",
    "LONG_CALL": "Long Call",
    "LONG_PUT": "Long Put",
    "SHORT_CALL": "Short Call",
    "SHORT_PUT": "Short Put",
    "BULL_CALL": "Bull Call Spread",
    "BEAR_PUT": "Bear Put Spread",
}

_CREDIT_BIAS = {"PCS": "bullish", "CCS": "bearish", "IC": "neutral"}


def _num(v):
    """A finite float, or None. bool is rejected: ``float(True)`` is 1.0."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _leg(side, kind, strike, expiration, qty=1):
    return {"side": side, "kind": kind, "strike": strike,
            "expiration": expiration, "qty": qty}


def _credit_legs(row):
    """Legs for a PCS / CCS / IC scanner row, or None when a strike is missing."""
    t = row.get("type")
    exp = row.get("expiration")
    short_k, long_k = _num(row.get("short_strike")), _num(row.get("long_strike"))
    if short_k is None or long_k is None:
        return None
    if t == "PCS":
        return [_leg("short", "put", short_k, exp), _leg("long", "put", long_k, exp)]
    if t == "CCS":
        return [_leg("short", "call", short_k, exp), _leg("long", "call", long_k, exp)]
    # IC: the put side lives in short/long_strike, the call side in call_*.
    call_s, call_l = _num(row.get("call_short")), _num(row.get("call_long"))
    if call_s is None or call_l is None:
        return None
    return [_leg("long", "put", long_k, exp), _leg("short", "put", short_k, exp),
            _leg("short", "call", call_s, exp), _leg("long", "call", call_l, exp)]


def _credit_entry_cash(row):
    """Net credit per contract after commission, from a PER-SHARE credit row."""
    net = _num(row.get("net_credit"))
    if net is not None:
        return net * MULT
    credit = _num(row.get("credit"))
    if credit is None:
        return None
    return credit * MULT - (_num(row.get("commission")) or 0.0)


def _directional_legs(row):
    out = []
    for lg in row.get("legs") or []:
        if not isinstance(lg, dict):
            return None
        kind, side = lg.get("kind"), lg.get("side")
        strike = _num(lg.get("strike"))
        qty = _num(lg.get("qty")) or 1
        if kind not in ("call", "put") or side not in ("long", "short") or strike is None:
            return None     # a share leg or a malformed one: not drawable here
        out.append(_leg(side, kind, strike, lg.get("expiration"), int(qty)))
    return out or None


def _directional_entry_cash(row):
    """Net cash per contract (PER-CONTRACT row), commission included."""
    debit, credit = _num(row.get("net_debit")), _num(row.get("net_credit"))
    if debit is None and credit is None:
        return None
    return (credit or 0.0) - (debit or 0.0) - (_num(row.get("commission")) or 0.0)


def payoff(legs, entry_cash, price):
    """P&L per contract at expiration if the underlying settles at ``price``."""
    total = entry_cash
    for lg in legs:
        k = lg["strike"]
        intrinsic = max(0.0, price - k) if lg["kind"] == "call" else max(0.0, k - price)
        sign = 1.0 if lg["side"] == "long" else -1.0
        total += sign * intrinsic * MULT * lg["qty"]
    return total


def _call_slope(legs):
    """d(payoff)/d(price) above every strike: + means profit grows without bound."""
    return sum((1 if lg["side"] == "long" else -1) * lg["qty"] * MULT
               for lg in legs if lg["kind"] == "call")


def economics(legs, entry_cash):
    """``(max_profit, max_loss, breakevens)`` per contract, exact.

    The payoff is piecewise linear with kinks at the strikes, so its extremes sit
    at a strike, at a price of zero, or at infinity (the call slope). ``None``
    means unbounded. ``max_loss`` is a positive number of dollars."""
    strikes = sorted({lg["strike"] for lg in legs})
    points = [0.0] + strikes
    values = [payoff(legs, entry_cash, p) for p in points]
    slope = _call_slope(legs)
    hi, lo = max(values), min(values)
    max_profit = None if slope > 0 else hi
    max_loss = None if slope < 0 else max(0.0, -lo)

    breakevens = []
    for (p0, v0), (p1, v1) in zip(zip(points, values), zip(points[1:], values[1:])):
        if v0 == 0 and p0 > 0:
            breakevens.append(p0)
        elif v0 * v1 < 0:
            breakevens.append(p0 + (p1 - p0) * (-v0) / (v1 - v0))
    last_p, last_v = points[-1], values[-1]
    if last_v == 0 and last_p > 0 and last_p not in breakevens:
        breakevens.append(last_p)
    elif slope and last_v * slope < 0:
        breakevens.append(last_p - last_v / slope)
    return max_profit, max_loss, [round(b, 2) for b in breakevens]


def normalize(row):
    """A scanner row as a drawable idea dict, or None when it cannot be drawn.

    None covers: an unknown type, a missing strike, a share leg, legs on more than
    one expiration (a calendar has no single expiry payoff), and no entry cash."""
    if not isinstance(row, dict):
        return None
    t = row.get("type")
    if t in _CREDIT_TYPES and not row.get("legs"):
        legs, cash = _credit_legs(row), _credit_entry_cash(row)
        bias = _CREDIT_BIAS[t]
    else:
        legs, cash = _directional_legs(row), _directional_entry_cash(row)
        bias = row.get("bias") or "neutral"
    if not legs or cash is None:
        return None
    expirations = {lg["expiration"] for lg in legs}
    if len(expirations) != 1 or None in expirations:
        return None
    max_profit, max_loss, breakevens = economics(legs, cash)
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol"),
        "type": t,
        "label": row.get("strategy_label") or STRATEGY_LABELS.get(t, str(t)),
        "bias": bias,
        "legs": legs,
        "expiration": next(iter(expirations)),
        "dte": row.get("dte"),
        "spot": _num(row.get("underlying_price")),
        "grade": row.get("grade"),
        "score": _num(row.get("composite_score")),
        "pop_pct": _num(row.get("pop_pct")),
        "entry_cash": round(cash, 2),
        "max_profit": None if max_profit is None else round(max_profit, 2),
        "max_loss": None if max_loss is None else round(max_loss, 2),
        "breakevens": breakevens,
        "em": _num(row.get("em_to_expiry")),
        "trade_type": row.get("trade_type"),
    }


def _spans_earnings(row):
    """True when a known report date falls on or before the expiration."""
    e, exp = row.get("earnings_date"), row.get("expiration")
    return bool(row.get("spans_earnings")) or bool(e and exp and str(e) <= str(exp))


def scan_age_min(scan, now):
    """Minutes since the scan was published, or None when it carries no stamp."""
    ts = (scan or {}).get("timestamp") if isinstance(scan, dict) else None
    try:
        stamp = _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None or now.tzinfo is None:
        stamp, now = stamp.replace(tzinfo=None), now.replace(tzinfo=None)
    return (now - stamp).total_seconds() / 60.0


def days_to_expiry(expiration, today):
    """Calendar days from ``today`` to ``expiration``, or None when unreadable.

    Counted here rather than read off the row's ``dte``, which is stamped when the
    scan ran -- a scan from yesterday afternoon still says 1."""
    try:
        return (_dt.date.fromisoformat(str(expiration)[:10]) - today).days
    except ValueError:
        return None


def candidates(scan, *, grades=DEFAULT_GRADES, min_score=0.0, today=None,
               min_dte=DEFAULT_MIN_DTE):
    """Every postable idea in the scan, unordered.

    Postable = an eligible grade, at or above ``min_score``, drawable, not open
    through an earnings report, at least ``min_dte`` days to expiration (skipped
    when ``today`` is None), a known probability of profit, and a BOUNDED loss.
    A naked short's unlimited risk is a real trade but not one to publish as an
    idea with a dollar figure beside it."""
    out = []
    for name in _SCAN_LISTS:
        rows = (scan or {}).get(name) if isinstance(scan, dict) else None
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get("grade") not in grades:
                continue
            if (_num(row.get("composite_score")) or 0.0) < (min_score or 0.0):
                continue
            if _spans_earnings(row):
                continue
            idea = normalize(row)
            if idea is None or idea["max_loss"] is None or idea["pop_pct"] is None:
                continue
            if today is not None:
                dte = days_to_expiry(idea["expiration"], today)
                if dte is None or dte < (min_dte or 0):
                    continue
                idea["dte"] = dte
            out.append(idea)
    return out


def pick(ideas, posted=None, *, grades=DEFAULT_GRADES):
    """The one idea to post, or None.

    ``posted`` is today's state (``{"ids": [...], "symbols": [...], "last_type"}``).
    A trade already posted today is never posted again. Among the rest, in order:
    a symbol not yet posted today, then the better grade, then a structure
    different from the last post, then the higher score. Grade outranks variety on
    purpose -- a feed that alternates into weaker trades to look varied is the
    wrong trade."""
    posted = posted or {}
    ids, symbols = set(posted.get("ids") or ()), set(posted.get("symbols") or ())
    last_type = posted.get("last_type")
    rank = {g: i for i, g in enumerate(grades)}
    pool = [i for i in ideas if i.get("id") not in ids]
    if not pool:
        return None
    return min(pool, key=lambda i: (
        i["symbol"] in symbols,
        rank.get(i["grade"], len(rank)),
        i["type"] == last_type,
        -(i["score"] or 0.0),
        str(i["id"]),
    ))


def next_posted(posted, idea, today):
    """Today's posted state after ``idea`` goes out; a new day starts empty."""
    base = posted if isinstance(posted, dict) and posted.get("date") == today else {}
    return {
        "date": today,
        "ids": list(base.get("ids") or []) + [idea["id"]],
        "symbols": sorted(set(base.get("symbols") or []) | {idea["symbol"]}),
        "last_type": idea["type"],
    }


def todays_posted(posted, today):
    """The posted state if it belongs to ``today``, else an empty one."""
    return posted if isinstance(posted, dict) and posted.get("date") == today else {}


# ── words for the card and the caption ──────────────────────────────────────
def money(v):
    """'$2,516' / 'Unlimited'. Whole dollars: cents on a per-contract figure are
    noise at a glance."""
    if v is None:
        return "Unlimited"
    return f"${v:,.0f}"


def strike_text(k):
    return f"{k:,.0f}" if abs(k - round(k)) < 1e-9 else f"{k:,.2f}".rstrip("0")


def expiry_text(expiration):
    """'Sep 21' -- the year is noise on a trade that expires this month."""
    try:
        d = _dt.date.fromisoformat(str(expiration)[:10])
    except ValueError:
        return str(expiration or "")
    return f"{d:%b} {d.day}"


def caption(idea):
    """One plain-text line for Telegram and Discord beside the image."""
    legs = " / ".join(
        f"{'+' if lg['side'] == 'long' else '-'}{strike_text(lg['strike'])}{lg['kind'][0].upper()}"
        for lg in idea["legs"])
    bits = [f"{idea['symbol']} {idea['label']}", f"{expiry_text(idea['expiration'])} {legs}",
            f"Grade {idea['grade']}"]
    bits.append(f"Risk {money(idea['max_loss'])}")
    bits.append(f"Profit {money(idea['max_profit'])}")
    bits.append(f"POP {idea['pop_pct']:.0f}%")
    return "Trade idea: " + " · ".join(bits)


def filename(idea, now):
    safe = "".join(c if c.isalnum() else "-" for c in str(idea.get("symbol") or "trade"))
    return f"trade-idea-{now:%Y-%m-%d-%H%M}-{safe}.png"


def archive(root, idea, png, caption_text, now):
    """Write the card and its caption under ``root/<day>/``; return the PNG path.

    Never raises: the archive is a copy for posting by hand, and a full disk must
    not turn into a missed Discord post. Returns None when nothing was written."""
    try:
        day = pathlib.Path(root) / f"{now:%Y-%m-%d}"
        day.mkdir(parents=True, exist_ok=True)
        path = day / filename(idea, now)
        path.write_bytes(png)
        path.with_suffix(".txt").write_text(caption_text + "\n", encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("trade idea archive failed", exc_info=True)
        return None
