"""Marks, exits and settlement for TRACKED structures.

A tracked structure is a ``signals.db`` row under ``signal_db.TRACKED_TYPES``: a
Market Scanner structure that is not a credit spread, recorded so its outcome
can be measured. It is never a position in a paper book. This module is what
turns one into a mark series and an outcome, from its stored legs alone.

It is separate from ``signal_repricer`` on purpose. That module marks the paper
books' positions through a leg layout keyed on the structure's name, and the
absence of a layout is what keeps a straddle or a strangle from ever being
marked - and so held - there (``tests/test_straddle_analysis_only.py``). Nothing
here adds a layout: a tracked row is marked from its own ``legs_json``, whatever
it is called.

**One convention**, the store's. ``entry_credit`` is SIGNED per share (a credit
positive, a debit negative) and a value is the COST TO CLOSE per share - what
buying the whole position back would cost, so a long position that is worth
money has a NEGATIVE value. Then for every structure::

    pnl per contract = (entry_credit - value) * 100

which is ``signal_db.close_signal_manually``'s own formula.

PURE apart from ``reprice``'s chain fetch (through ``signal_repricer``, so it
shares that module's per-run chain cache and its quote rules).
"""
import datetime
import json
import math
import pathlib as _pathlib
import sys as _sys

import signal_repricer as _sr

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared import trade_mgmt as _trade_mgmt  # noqa: E402

MULTIPLIER = 100.0

#: Recommendation codes that close a tracked row on its mark.
CLOSE_CODES = ("TARGET_HIT", "MONEY_STOP", "FRONT_EXPIRY")

_NO_MARK = {"current_value": None, "unrealized_pnl": None, "pnl_pct_of_credit": None,
            "current_underlying": None, "current_short_delta": None}


def _real(value):
    """A finite number, or None (a bool is not a number)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# ── the legs ────────────────────────────────────────────────────────────────

def legs_of(row):
    """The row's legs as ``[{kind, side, strike, expiration, qty}]``, or None when
    the row carries none that can be used. Never raises: a blob that cannot be
    read is a row that cannot be marked, not a failed cycle."""
    raw = (row or {}).get("legs_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for leg in raw:
        if not isinstance(leg, dict):
            return None
        kind, side = leg.get("kind"), leg.get("side")
        strike, exp = _real(leg.get("strike")), leg.get("expiration")
        qty = _real(leg.get("qty"))
        if (kind not in ("call", "put") or side not in ("long", "short")
                or strike is None or not isinstance(exp, str) or not exp):
            return None
        out.append({"kind": kind, "side": side, "strike": strike,
                    "expiration": exp[:10], "qty": int(qty) if qty and qty >= 1 else 1})
    return out


def expirations(legs):
    """The legs' distinct expirations, earliest first."""
    return sorted({leg["expiration"] for leg in legs or []})


def is_two_expiry(legs):
    """A calendar or a diagonal: its back month outlives its front leg."""
    return len(expirations(legs)) > 1


def _sign(leg):
    """+1 for a short leg, -1 for a long: the cost-to-close convention."""
    return 1.0 if leg["side"] == "short" else -1.0


def cost_to_close(legs, price_of):
    """What buying the position back costs per share, or None when any leg has
    no price. ``price_of(leg)`` is that leg's price per share (never negative)."""
    total = 0.0
    for leg in legs:
        price = _real(price_of(leg))
        if price is None or price < 0:
            return None
        total += _sign(leg) * price * leg["qty"]
    return round(total, 4)


def pnl_of(row, value):
    """Per-contract dollars at ``value`` (a cost to close), or None."""
    entry, value = _real((row or {}).get("entry_credit")), _real(value)
    if entry is None or value is None:
        return None
    return round((entry - value) * MULTIPLIER, 2)


# ── a live mark ─────────────────────────────────────────────────────────────

def _quote(leg_map, strike):
    """The mid of the contract at ``strike``, or None when it has no market.

    The strike is matched as a NUMBER. ``signal_repricer._leg_mid`` formats it
    to one decimal, which cannot find a quarter-point strike (99.75), and a
    butterfly on a fine ladder is made of them."""
    for strikes in (leg_map or {}).values():
        for key, contracts in (strikes or {}).items():
            try:
                hit = abs(float(key) - strike) < 1e-6
            except (TypeError, ValueError):
                continue
            if hit and contracts:
                bid, ask = _sr._market(contracts[0])
                return None if ask is None else (bid + ask) / 2.0
    return None


def _failed(error):
    return {**_NO_MARK, "error": error}


def reprice(row, client, today=None):
    """Mark a tracked row at its legs' current mids. The same keys
    ``signal_repricer.reprice_swing`` returns, so one mark writer serves both.

    Each leg is priced on ITS OWN expiration's chain - a calendar needs two -
    and a leg with no usable quote means no mark at all, never a partial one
    and never a zero. ``error`` says why. Never raises.
    """
    legs = legs_of(row)
    if legs is None:
        return _failed("no legs")
    if _sr._is_expired(expirations(legs)[0], today):
        return _failed("expired")
    try:
        chains = {}
        for exp in expirations(legs):
            chain = _sr._fetch_chain(client, row["symbol"], exp,
                                     max_age=_sr._paper_limits.mark_chain_max_age_sec())
            if chain is None:
                return _failed("no chain")
            chains[exp] = chain

        def price_of(leg):
            chain = chains[leg["expiration"]]
            key = "callExpDateMap" if leg["kind"] == "call" else "putExpDateMap"
            return _quote(chain.get(key), leg["strike"])

        value = cost_to_close(legs, price_of)
        if value is None:
            return _failed("missing leg quote")
        pnl = pnl_of(row, value)
        entry = _real(row.get("entry_credit"))
        pct = (round(pnl / (abs(entry) * MULTIPLIER) * 100.0, 2)
               if pnl is not None and entry else None)
        return {"current_value": value, "unrealized_pnl": pnl,
                "pnl_pct_of_credit": pct,
                "current_underlying": _sr._chain_spot(chains[expirations(legs)[0]]),
                "current_short_delta": None, "error": None}
    except Exception:  # noqa: BLE001 - a mark that raises is a mark that failed.
        _sr.log.exception("structure_marks.reprice failed for %s", row.get("symbol"))
        return _failed("repricing failed")


# ── settlement ──────────────────────────────────────────────────────────────

def _intrinsic(leg, spot):
    return max(spot - leg["strike"], 0.0) if leg["kind"] == "call" \
        else max(leg["strike"] - spot, 0.0)


def expiry_value(row, spot):
    """The cost to close at expiry with the underlying at ``spot``, or None.

    Intrinsic value, for a structure whose legs all expire together. ⚠ None for
    a calendar or a diagonal: its back month still has time value on the front
    leg's expiry, so its intrinsic is not what it is worth. Those are closed on
    their mark on the front expiry day instead (``front_expiry_due``).
    """
    legs, spot = legs_of(row), _real(spot)
    if legs is None or spot is None or spot <= 0 or is_two_expiry(legs):
        return None
    return cost_to_close(legs, lambda leg: _intrinsic(leg, spot))


def front_expiry_due(row, now_ct, close_time):
    """True on a two-expiry row's FRONT expiry day at or after ``close_time`` (a
    ``datetime.time``, Central). The row is then closed on its mark: after the
    settlement hour the front leg no longer quotes."""
    legs = legs_of(row)
    if legs is None or not is_two_expiry(legs):
        return False
    try:
        front = datetime.date.fromisoformat(expirations(legs)[0])
    except ValueError:
        return False
    return now_ct.date() == front and now_ct.time() >= close_time


# ── the exit rules ──────────────────────────────────────────────────────────

def _hold(reason):
    return {"action": "HOLD", "reason": reason, "code": "HOLD"}


def _net_long(legs, kind):
    return sum(leg["qty"] if leg["side"] == "long" else -leg["qty"]
               for leg in legs if leg["kind"] == kind)


def open_ended(legs):
    """Does the profit keep growing for as long as the stock keeps moving one
    way? True when the position is net LONG calls or net long puts.

    Read from the legs, not from ``entry_max_profit``: the engine gives the put
    side a finite "max profit" - the stock at zero - and flags only the call
    side as unbounded. On a $500 stock that figure is about $49,000 for a long
    put, and half of it is a target no mark will ever reach."""
    return _net_long(legs or [], "call") > 0 or _net_long(legs or [], "put") > 0


def _target_base(row):
    """``(dollars, words)`` the profit target is a fraction of, or ``(None,
    None)`` when the row has no target.

    * A BOUNDED structure (a vertical, a butterfly, a condor, a calendar, a
      short straddle) targets a fraction of its MAX PROFIT.
    * An OPEN-ENDED structure that only BUYS options (a long call or put, a long
      straddle or strangle) has no max profit worth the name, so the only
      denominator is what was paid - ``signal_recommender._debit_target_base``'s
      rule, and the same for a put as for a call.
    * ⚠ An open-ended structure that also SELLS one (a backspread) gets NO
      target and is held to expiry. What it was entered for - a small credit or
      a small debit - is not what the trade is for: half of a $3 debit is $1.50,
      and closing there would record the opposite of what it was opened to
      measure.
    """
    legs = legs_of(row) or []
    if open_ended(legs):
        if any(leg["side"] == "short" for leg in legs):
            return None, None
        paid = _real(row.get("entry_credit"))
        if paid is not None and paid < 0:
            return -paid * MULTIPLIER, "the debit paid"
        return None, None
    max_profit = _real(row.get("entry_max_profit"))
    if max_profit is not None and max_profit > 0:
        return max_profit * MULTIPLIER, "max profit"
    entry = _real(row.get("entry_credit"))
    if entry is not None and entry < 0:
        return -entry * MULTIPLIER, "the debit paid"
    return None, None


def recommend(row, pnl, *, front_expiry_day=False):
    """``{"action", "reason", "code"}`` for a tracked row at ``pnl`` (per-contract
    dollars; None = not marked). First match wins:

    1. Not marked: HOLD. A missing mark is never read as a zero.
    2. ``FRONT_EXPIRY``: a calendar or diagonal on its front leg's expiry day
       (the caller decides the hour) is closed on its mark, win or lose.
    3. ``MONEY_STOP``: a row entered for a CREDIT whose structure keeps the
       loss-side rules, at a loss of ``stop_mult`` times the credit. This is the
       app's standing rule for anything that sells premium, and it is the only
       brake on a short strangle, whose loss has no other bound.
       ``loss_rules = false`` in ``[structures.*]`` turns it off, as shipped for
       the two backspreads: their loss is small, defined, and where they sit
       until the large move they are for.
    4. ``TARGET_HIT``: ``tp_frac`` of the target base (``_target_base``).
    5. HOLD. Everything left settles at expiry.

    The thresholds come from ``shared.trade_mgmt.structure_rules`` and nothing
    here is fitted: there is no outcome data for these structures yet, which is
    what recording them is for. Every mark is stored, so a different exit can be
    replayed over the same rows later.
    """
    pnl = _real(pnl)
    if pnl is None:
        return _hold("no mark yet")
    if front_expiry_day:
        return {"action": "TAKE_PROFIT" if pnl > 0 else "CUT",
                "reason": "front leg expires today", "code": "FRONT_EXPIRY"}
    rules = _trade_mgmt.structure_rules(row.get("strategy"))
    entry = _real(row.get("entry_credit"))
    stop_mult = _real(rules.get("stop_mult"))
    if (entry is not None and entry > 0 and rules.get("loss_rules")
            and stop_mult is not None
            and pnl <= -stop_mult * entry * MULTIPLIER):
        return {"action": "CUT", "reason": f"{stop_mult:g}x credit stop",
                "code": "MONEY_STOP"}
    base, words = _target_base(row)
    tp_frac = _real(rules.get("tp_frac"))
    if base is not None and tp_frac is not None and tp_frac > 0 and pnl >= tp_frac * base:
        return {"action": "TAKE_PROFIT",
                "reason": f">={int(round(tp_frac * 100))}% of {words}",
                "code": "TARGET_HIT"}
    return _hold("holding")


def build_mark(row, rep, now, *, front_expiry_day=False):
    """A ``signal_marks`` row for a tracked structure, or None when the reprice
    failed. The same keys ``signal_recommender.build_mark`` writes; the score
    columns are None (a tracked row is not re-scored)."""
    if rep is None or rep.get("error"):
        return None
    rec = recommend(row, rep.get("unrealized_pnl"), front_expiry_day=front_expiry_day)
    return {
        "signal_id": row["signal_id"],
        "mark_ts": now.isoformat(),
        "mark_date": now.date().isoformat(),
        "current_value": rep.get("current_value"),
        "unrealized_pnl": rep.get("unrealized_pnl"),
        "pnl_pct_of_credit": rep.get("pnl_pct_of_credit"),
        "current_underlying": rep.get("current_underlying"),
        "current_short_delta": None,
        "current_score": None,
        "score_drift": None,
        "recommendation": rec["action"],
        "recommendation_reason": rec["reason"],
        "recommendation_code": rec["code"],
    }
