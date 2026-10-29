"""The daily 0-DTE iron condor, booked into the Paper Ledger (trades.db) each morning.

At ``[slots.daily_condor]`` (08:45 CT) one chain of ``[daily_condor] symbol``'s
same-day expiry is fetched NOW (``max_age=0``, the rule for a fill), and ``pick``
builds the condor: on each side the short is the out-of-the-money strike whose
delta is nearest ``short_delta`` (a tie takes the strike farther from the money),
and the long wing is the strike exactly ``width`` beyond it. It is priced at the
realistic fill (``fill_model.realistic_vertical_fill``, each vertical worked into
its own net market) and booked as an ordinary ``IC`` Ledger row with ``source =
"daily_condor"`` (design: docs/plans/2026-10-29-daily-spy-condor-design.md).

Like a posted idea it bypasses EVERY cap - it never goes through
``compute.create_paper_trade`` - and from booking on it belongs to
``idea_ledger.mark_cycle``: marked every 5 minutes, closed at its stop at THAT
mark's value, otherwise settled at the close. It has no target.

The stop is a PRICE: a loss of ``stop_mult`` x the credit, so with $1.00 taken in
and ``stop_mult = 2`` it closes when buying it back costs $3.00. ``entry_credit``
carries the round-trip commission (as an idea's entry cash does), so the stop line
adds it back: ``stop_pnl = (stop_mult x credit + k) x 100``, ``k`` the commission a
share, which puts the trigger exactly on that price.

Imports nothing from ``compute`` (the file has a line-count ceiling).
"""
import logging
import sys
import uuid

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import fill_model  # noqa: E402
import paper_lock as _paper_lock  # noqa: E402  (the one lock for both paper books)
import paper_trader  # noqa: E402
import signal_repricer as _sr  # noqa: E402

from services.options_svc import commission, idea_ledger  # noqa: E402
from shared import greeks as _greeks  # noqa: E402
from shared import market_calendar as _mc  # noqa: E402
from shared import paper_limits  # noqa: E402

log = logging.getLogger(__name__)

SOURCE = idea_ledger.CONDOR_SOURCE
_MULT = 100.0                  # one contract = 100 shares; entry_credit is per share
_MAPS = {"put": "putExpDateMap", "call": "callExpDateMap"}


def _skip(reason) -> dict:
    return {"skip": reason}


def _contracts(chain, kind) -> dict:
    """``{strike: contract}`` for one side of a one-expiry chain."""
    out = {}
    for by_strike in ((chain or {}).get(_MAPS[kind]) or {}).values():
        for key, contracts in (by_strike or {}).items():
            try:
                strike = float(key)
            except (TypeError, ValueError):
                continue
            if contracts and isinstance(contracts[0], dict):
                out[strike] = contracts[0]
    return out


def _short(side, spot, target):
    """``(strike, delta)`` of the out-of-the-money contract whose delta is nearest
    ``target``, or None. A tie takes the strike farther from ``spot``."""
    found = []
    for strike, ctr in side.items():
        d = _greeks.delta(ctr.get("delta"))
        if d is None or strike == spot:
            continue
        found.append((round(abs(abs(d) - target), 6), -abs(strike - spot), strike, d))
    return min(found)[2:] if found else None


def pick(chain, expiration, cfg) -> dict:
    """The condor ``chain`` gives for ``cfg``, or ``{"skip": reason}``. PURE.

    ``{"legs", "put_short", "put_long", "call_short", "call_long", "put_delta",
    "call_delta", "credit", "spot"}``; ``credit`` is per share, gross of commission,
    the two verticals' realistic fills summed. A short whose delta misses
    ``short_delta`` by more than ``max_delta_miss`` is a skip (Schwab's ``-999`` on
    the near strikes would otherwise pass a far strike off as the target), every leg
    must have a market (``signal_repricer._market``), and a condor that takes in
    nothing is a skip."""
    puts, calls = _contracts(chain, "put"), _contracts(chain, "call")
    spot = _sr._chain_spot(chain) if isinstance(chain, dict) else None
    if not puts or not calls or not spot:
        return _skip("no chain for today")
    width, target = float(cfg["width"]), float(cfg["short_delta"])
    out, credit, legs = {"spot": spot}, 0.0, []
    for kind, side, sign in (("put", {k: c for k, c in puts.items() if k < spot}, -1),
                             ("call", {k: c for k, c in calls.items() if k > spot}, 1)):
        found = _short(side, spot, target)
        if found is None:
            return _skip(f"no usable {kind} delta")
        short_k, delta = found
        miss = float(cfg["max_delta_miss"])
        if round(abs(abs(delta) - target), 6) > miss:
            return _skip(f"no {kind} delta within {miss:g} of {target:g}")
        long_k = round(short_k + sign * width, 2)
        whole = puts if kind == "put" else calls
        if long_k not in whole:
            return _skip(f"no {kind} strike at {long_k}")
        quotes = []
        for k in (short_k, long_k):
            bid, ask = _sr._market(whole[k])
            if ask is None:
                return _skip(f"no market on the {k} {kind}")
            quotes.append((bid, ask))
        (sb, sa), (lb, la) = quotes
        credit += fill_model.realistic_vertical_fill(sb, sa, lb, la, "SELL_TO_OPEN")
        out.update({f"{kind}_short": short_k, f"{kind}_long": long_k,
                    f"{kind}_delta": delta})
        legs += [{"kind": kind, "side": "short", "strike": short_k,
                  "expiration": expiration, "qty": 1},
                 {"kind": kind, "side": "long", "strike": long_k,
                  "expiration": expiration, "qty": 1}]
    credit = round(credit, 2)
    if credit <= 0:
        return _skip("no credit")
    out.update({"legs": legs, "credit": credit})
    return out


def build_row(p, now, cfg, commission_total) -> dict:
    """The Ledger row for the condor ``p`` (from ``pick``) booked at ``now``. PURE.

    ``commission_total`` is the round trip for ALL ``cfg["quantity"]`` condors, in
    dollars. Per-share and per-contract figures are for one condor; ``*_total``
    scale by quantity."""
    qty = int(cfg["quantity"])
    k = commission_total / qty / _MULT                     # a share, one condor
    credit, width = float(p["credit"]), float(cfg["width"])
    net = round(credit - k, 4)
    max_loss = round(width - net, 4)
    return {
        "trade_id": str(uuid.uuid4())[:8], "mode": "PAPER", "status": "OPEN",
        "symbol": cfg["symbol"], "strategy": "IC", "trade_type": "0-DTE",
        "expiration": p["legs"][0]["expiration"], "dte_at_entry": 0,
        "quantity": qty, "legs": p["legs"],
        "short_strike": p["put_short"], "long_strike": p["put_long"],
        "call_short": p["call_short"], "call_long": p["call_long"], "width": width,
        "entry_credit": net, "entry_credit_total": round(net * _MULT * qty, 2),
        "direction": None, "entry_debit": None, "entry_debit_total": None,
        "max_loss_per": max_loss, "max_loss_total": round(max_loss * _MULT * qty, 2),
        "max_profit_total": round(net * _MULT * qty, 2), "unbounded": False,
        "breakeven": f"{p['put_short'] - net:.2f} / {p['call_short'] + net:.2f}",
        "underlying_at_entry": p["spot"],
        "entry_time": now.isoformat(timespec="seconds"),
        # The deltas the shorts were picked at, so a day picked off a thin chain can
        # be found later (``short_delta`` holds one leg; an IC has two).
        "notes": (f"Daily {cfg['symbol']} condor (put delta {p['put_delta']:.2f}, "
                  f"call delta {p['call_delta']:.2f})"),
        "source": SOURCE, "entry_source": SOURCE,
        "target_pnl": None,
        "stop_pnl": round((float(cfg["stop_mult"]) * credit + k) * _MULT, 2),
        "mae_pnl": 0.0,
    }


def _booked_today(symbol, day) -> bool:
    return any(t.get("source") == SOURCE and t.get("symbol") == symbol
               and str(t.get("entry_time") or "")[:10] == day
               for t in paper_trader.get_all_trades())


def _done(status, trade_id=None, reason=None) -> dict:
    return {"status": status, "trade_id": trade_id, "reason": reason}


@_paper_lock.serialized
def book(now, client=None) -> dict:
    """Book today's condor: ``{"status", "trade_id", "reason"}``, the status one of
    ``booked`` / ``duplicate`` / ``disabled`` / ``skipped``.

    At most one a day per symbol, across every row open or closed, so a restart
    inside the slot's grace books nothing twice. A day that cannot be priced is a
    skip with its reason logged; nothing retries it later."""
    cfg = paper_limits.daily_condor()
    if not cfg["enabled"]:
        return _done("disabled")
    day = now.date().isoformat()
    if not _mc.is_trading_day(now.date()):
        return _done("skipped", reason="not a trading day")
    if _booked_today(cfg["symbol"], day):
        return _done("duplicate", reason="already booked today")
    if client is None:
        from services import _proxy               # lazy: it builds a client at import
        client = _proxy.schwab_py_client
    _sr.clear_chain_cache()
    p = pick(_sr._fetch_chain(client, cfg["symbol"], day, max_age=0), day, cfg)
    if "skip" in p:
        log.warning("daily condor SKIPPED %s %s: %s", cfg["symbol"], day, p["skip"])
        return _done("skipped", reason=p["skip"])
    rt = commission.round_trip_commission("IC", cfg["symbol"], int(cfg["quantity"]))
    row = build_row(p, now, cfg, rt)
    paper_trader.add_trade(row)
    log.info("daily condor booked %s %s: %s/%s puts, %s/%s calls, credit %.2f",
             row["trade_id"], day, p["put_long"], p["put_short"], p["call_short"],
             p["call_long"], p["credit"])
    return _done("booked", trade_id=row["trade_id"])
