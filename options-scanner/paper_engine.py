"""
paper_engine.py - Portfolio paper-trading orchestration
Version: 1.0.0
Last Updated: 2026-06-03

Per-cycle entry scan + position management for the Portfolio tab. RTH-gated.
Reuses signal_db (source), paper_broker (fills), paper_account_db (state),
signal_repricer + signal_recommender (exits), paper_trader (settlement).

Version 1.0.0 Changes:
- Initial implementation
"""
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config_paper
import paper_sizing
import paper_account_db
import signal_repricer
import signal_recommender
import scanner_engine
import paper_broker as _default_broker
from commissions import round_trip_commission

log = logging.getLogger("paper_engine")

TZ = ZoneInfo("America/Chicago")
MULTIPLIER = 100

# True regular trading hours for placing/closing paper orders: 08:30-15:00 CT.
# scanner_engine.is_market_hours() DEFAULTS to the scanner's 08:00-15:15
# operating window, which INCLUDES premarket — wrong for order placement, since
# paper fills must only use live RTH quotes (never premarket/stale data).
RTH_START_H, RTH_START_M = 8, 30
RTH_END_H, RTH_END_M = 15, 0


def in_trading_window(now=None):
    """True only during true regular trading hours (08:30-15:00 CT) on a trading
    day. The engine itself is market-hours-agnostic; the UI calls this to gate
    when entry/manage cycles may run, so fills never use premarket/stale quotes.
    Position MANAGEMENT (closing) uses this full window."""
    return scanner_engine.is_market_hours(
        now, start_h=RTH_START_H, start_m=RTH_START_M,
        end_h=RTH_END_H, end_m=RTH_END_M)


def in_entry_window(now=None):
    """Like in_trading_window but also excludes the first OPEN_BUFFER_MIN minutes
    after the open — opening-auction quotes are unreliable and produce garbage
    fills. New ENTRIES use this; management still uses the full RTH window."""
    if not in_trading_window(now):
        return False
    if now is None:
        now = datetime.now(TZ)
    open_dt = now.replace(hour=RTH_START_H, minute=RTH_START_M,
                          second=0, microsecond=0)
    return now >= open_dt + timedelta(minutes=config_paper.OPEN_BUFFER_MIN)


#############################################
# [PAPER] AUDIT LOG
#############################################

def _ensure_file_logging():
    """Attach a file handler to the shared 'paper_engine' logger once so every
    [PAPER] order/fill/reject line is persisted to logs/paper_engine.log. The
    broker logs to the same logger name, so its lines land here too. Idempotent."""
    if any(isinstance(h, logging.FileHandler) for h in log.handlers):
        return
    try:
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        fh = logging.FileHandler(log_dir / "paper_engine.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
        if log.level == logging.NOTSET:
            log.setLevel(logging.INFO)
    except Exception:
        log.exception("could not attach paper_engine file handler")


_ensure_file_logging()

# Recommendations that DON'T block a new entry. None/"" = no mark yet (new signal).
ELIGIBLE_RECS = (None, "", "HOLD")


#############################################
# ENTRY ELIGIBILITY (pure)
#############################################

def is_eligible(signal, min_score=None):
    """A captured signal is eligible to auto-open when its entry_score clears
    the bar AND its latest recommendation is not CUT / not already at target."""
    if min_score is None:
        min_score = config_paper.MIN_ENTRY_SCORE
    if (signal.get("entry_score") or 0) < min_score:
        return False
    return signal.get("recommendation") in ELIGIBLE_RECS


#############################################
# ORDER RECORDING HELPERS
#############################################

def _order_row(order, resp, status, reject_reason=None):
    """Build a paper_orders row from an order dict + optional broker response."""
    r = resp or {}
    filled = status == "FILLED"
    return {
        "signal_id": order.get("signal_id"),
        "ts": r.get("enteredTime") or datetime.now(TZ).isoformat(),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "strategy": order.get("strategy"),
        "legs": json.dumps(order.get("legs", [])),
        "quantity": order.get("quantity"),
        "order_type": r.get("orderType"),
        "limit_price": order.get("limit_price"),
        "status": status,
        "fill_price": r.get("price") if filled else None,
        "fill_ts": r.get("closeTime") if filled else None,
        "reject_reason": reject_reason,
        "response_json": json.dumps(resp) if resp else "{}",
    }


def _record_order(db_path, order, resp):
    """Insert an order row mirroring the broker response. Returns order_id."""
    status = resp["status"]
    reason = resp.get("statusDescription") if status != "FILLED" else None
    return paper_account_db.insert_order(db_path, _order_row(order, resp, status, reason))


def _record_reject(db_path, sig, side, reason):
    """Record a rejection that never reached the broker (e.g. RISK_TOO_HIGH,
    INSUFFICIENT_BUYING_POWER)."""
    order = {"signal_id": sig["signal_id"], "symbol": sig["symbol"], "side": side,
             "strategy": sig["strategy"], "quantity": 0,
             "limit_price": sig.get("entry_credit"), "legs": []}
    return paper_account_db.insert_order(db_path, _order_row(order, None, "REJECTED", reason))


#############################################
# ENTRY CYCLE
#############################################

def run_entry_cycle(client, now_date, signals, broker=None, db_path=None):
    """Open new paper positions from eligible captured signals. RTH gating is the
    caller's responsibility; this opens nothing when the account is halted."""
    broker = broker or _default_broker
    # Fresh quotes: drop cached chains so fills reflect the current market.
    signal_repricer.clear_chain_cache()
    paper_account_db.roll_session_if_needed(db_path, now_date)
    if paper_account_db.get_account(db_path)["halted"]:
        return
    for sig in signals:
        if not is_eligible(sig):
            continue
        sid = sig["signal_id"]
        # Evaluate each signal at most once: skip if it already has ANY recorded
        # order (filled OR rejected). Prevents both re-opening a closed signal
        # and re-rejecting an un-sizeable one every cycle (the reject-churn flood).
        if paper_account_db.has_order_for_signal(db_path, sid):
            continue
        try:
            qty, max_loss_per = paper_sizing.size_contracts(sig["entry_credit"], sig["width"])
            if qty < 1:
                _record_reject(db_path, sig, "SELL_TO_OPEN", "RISK_TOO_HIGH")
                log.info("%s REJECTED %s RISK_TOO_HIGH (1 contract > risk cap)",
                         _default_broker.PREFIX, sig["symbol"])
                continue
            order = {"signal_id": sid, "symbol": sig["symbol"], "side": "SELL_TO_OPEN",
                     "strategy": sig["strategy"], "short_strike": sig["short_strike"],
                     "long_strike": sig["long_strike"], "call_short": sig.get("call_short"),
                     "call_long": sig.get("call_long"), "expiration": sig["expiration"],
                     "quantity": qty, "limit_price": sig["entry_credit"], "legs": []}
            resp = broker.submit_order(order, client)
            if resp["status"] != "FILLED":
                _record_order(db_path, order, resp)
                continue
            fill = resp["price"]
            # Reject garbage opening-auction fills: a real credit spread never
            # fills at a near-zero / negative net credit.
            if fill < config_paper.MIN_FILL_CREDIT:
                _record_reject(db_path, sig, "SELL_TO_OPEN", "LOW_CREDIT")
                log.info("%s REJECTED %s LOW_CREDIT (fill %.2f < %.2f)",
                         _default_broker.PREFIX, sig["symbol"], fill,
                         config_paper.MIN_FILL_CREDIT)
                continue
            # Re-size on the ACTUAL fill credit. The pre-fill size above uses the
            # scanner's optimistic mid and only screens out hopeless signals; the
            # real fill (often much worse, e.g. an IC filled at bid/ask vs mid)
            # determines true per-contract risk. Sizing off the fill is what keeps
            # realized risk within MAX_RISK_PER_TRADE.
            qty, max_loss_per = paper_sizing.size_contracts(fill, sig["width"])
            if max_loss_per <= 0:
                _record_reject(db_path, sig, "SELL_TO_OPEN", "DEGENERATE_FILL")
                log.warning("%s REJECTED %s DEGENERATE_FILL (fill %.2f >= width %.2f)",
                            _default_broker.PREFIX, sig["symbol"], fill, sig["width"])
                continue
            if qty < 1:
                _record_reject(db_path, sig, "SELL_TO_OPEN", "RISK_TOO_HIGH")
                log.info("%s REJECTED %s RISK_TOO_HIGH (fill %.2f, 1 contract > risk cap)",
                         _default_broker.PREFIX, sig["symbol"], fill)
                continue
            max_loss_total = round(max_loss_per * qty, 2)
            if max_loss_total > paper_account_db.get_account(db_path)["cash"]:
                _record_reject(db_path, sig, "SELL_TO_OPEN", "INSUFFICIENT_BUYING_POWER")
                log.info("%s REJECTED %s INSUFFICIENT_BUYING_POWER (need %.2f)",
                         _default_broker.PREFIX, sig["symbol"], max_loss_total)
                continue
            order["quantity"] = qty   # persist the re-sized qty actually opened
            oid = _record_order(db_path, order, resp)
            paper_account_db.reserve_buying_power(db_path, max_loss_total)
            paper_account_db.insert_position(db_path, {
                "signal_id": sid, "symbol": sig["symbol"], "strategy": sig["strategy"],
                "short_strike": sig["short_strike"], "long_strike": sig["long_strike"],
                "call_short": sig.get("call_short"), "call_long": sig.get("call_long"),
                "width": sig["width"], "expiration": sig["expiration"],
                "dte_at_entry": sig.get("dte_at_entry", 0), "quantity": qty,
                "entry_credit": fill, "entry_order_id": oid, "max_loss_per": max_loss_per,
                "max_loss_total": max_loss_total, "entry_ts": resp["enteredTime"]})
            log.info("%s OPENED %s %s x%s credit %.2f BP %.2f", _default_broker.PREFIX,
                     sig["symbol"], sig["strategy"], qty, fill, max_loss_total)
        except Exception:
            log.exception("%s entry failed for %s", _default_broker.PREFIX, sig.get("symbol"))
            continue


#############################################
# MANAGE CYCLE
#############################################

def _trade_view(pos):
    """A position row reshaped to the dict signal_repricer / signal_recommender
    expect (entry_credit + legs)."""
    return {
        "symbol": pos["symbol"], "strategy": pos["strategy"],
        "expiration": pos["expiration"], "short_strike": pos["short_strike"],
        "long_strike": pos["long_strike"], "call_short": pos.get("call_short"),
        "call_long": pos.get("call_long"), "entry_credit": pos["entry_credit"],
    }


def _dte_remaining(expiration, now_date):
    try:
        return (date.fromisoformat(expiration) - date.fromisoformat(now_date)).days
    except (TypeError, ValueError):
        log.warning("could not parse expiration %r; treating as far-dated", expiration)
        return 99


def _position_legs(pos):
    """Leg count for commission: 4 for an iron condor (call side present), else 2."""
    return 4 if pos.get("call_short") is not None else 2


def net_realized_pnl(gross, pos, qty, *, expired):
    """Realized P&L (dollars) net of the position's lifecycle option commission.

    A managed close (BUY_TO_CLOSE) pays commission on BOTH the open and the close
    (round-trip = per-leg rate x legs x qty x 2); an OTM expiration pays only the
    opening commission (a spread that expires is not actively closed), i.e. half
    the round-trip. Folding the whole lifecycle fee into the realized figure at
    close makes both the stored position row AND account cash net-of-fees (``_close``
    writes both from this one value), so the driver scorecard reflects true P&L.
    Defensive: any failure returns ``gross`` unchanged — commission math must never
    break a close.
    """
    try:
        rt = round_trip_commission(_position_legs(pos), pos.get("symbol"), qty)
        commission = round(rt / 2.0, 2) if expired else round(rt, 2)
        return round(gross - commission, 2)
    except Exception:  # pragma: no cover - defensive
        return gross


def _close(db_path, pos, exit_debit, exit_order_id, realized_pnl, reason, status):
    """Close a position, release its reserved buying power, and realize P&L.

    ``realized_pnl`` is expected NET of commission (see ``net_realized_pnl``); it is
    written to both the position row and account cash, keeping equity consistent.
    """
    paper_account_db.close_position(
        db_path, pos["position_id"], exit_debit=exit_debit,
        exit_order_id=exit_order_id, realized_pnl=realized_pnl, exit_reason=reason,
        exit_ts=datetime.now(TZ).isoformat(), status=status)
    paper_account_db.release_buying_power(db_path, pos["max_loss_total"])
    paper_account_db.realize_pnl(db_path, realized_pnl)


# Options settle on the expiration date at the 4pm ET close = 15:00 CT. Only
# auto-settle a position at/after that time on its expiry day (never intraday at
# the open — a 0-DTE credit spread must be held to the close), or on any later
# day (recovery for a position whose settlement window was missed).
SETTLE_HOUR_CT = 15


def should_settle(expiration, today, now_ct):
    """True when an option position should be expiration-settled.

    ``expiration``/``today`` are ISO date strings; ``now_ct`` is a tz-aware CT
    datetime (for the intraday 15:00 gate). Settles when the expiration is in the
    past (``exp < today`` — recovery for a missed window) OR it is the expiry day
    and the CT clock is at/after 15:00. A malformed/missing date never settles."""
    try:
        exp = date.fromisoformat(str(expiration)[:10])
        tod = date.fromisoformat(str(today)[:10])
    except (ValueError, TypeError):
        return False
    if exp < tod:
        return True
    if exp == tod:
        return now_ct.hour >= SETTLE_HOUR_CT
    return False


def underlying_last(client, symbol):
    """Best-effort last underlying price for settlement, or None.

    Used when the repricer can't supply ``current_underlying`` (it returns None
    for a past expiration, since it skips the doomed chain fetch). Maps SPX→$SPX
    and reads lastPrice/mark/closePrice from a direct quote. Never raises."""
    if client is None or not symbol:
        return None
    api = "$SPX" if symbol == "SPX" else symbol
    try:
        r = client.get_quotes([api])
        data = r.json() if hasattr(r, "json") else (r or {})
        info = data.get(api) or data.get(symbol) or {}
        q = info.get("quote", info.get("reference", info)) if isinstance(info, dict) else {}
        px = q.get("lastPrice") or q.get("mark") or q.get("closePrice")
        return float(px) if px else None
    except Exception:
        return None


def run_manage_cycle(client, now_date, broker=None, db_path=None, now_ct=None):
    """Re-price open positions, apply exit rules (target / CUT / expiration), and
    trip the session drawdown halt. RTH gating is the caller's responsibility.

    Expiration settlement fires only at/after 15:00 CT on the expiry day (or any
    later day) via ``should_settle`` — so a 0-DTE spread is held to the close, not
    force-settled at the open — and falls back to a direct underlying quote when
    the repricer can't supply one (a past expiration). ``now_ct`` defaults to the
    live CT clock; inject it for deterministic tests."""
    broker = broker or _default_broker
    now_ct = now_ct or datetime.now(TZ)
    signal_repricer.clear_chain_cache()        # fresh quotes for every fill
    paper_account_db.roll_session_if_needed(db_path, now_date)

    for pos in paper_account_db.fetch_open_positions(db_path):
        trade = _trade_view(pos)
        qty = pos["quantity"]
        dte = _dte_remaining(pos["expiration"], now_date)
        mark = signal_repricer.reprice_swing(trade, client)
        per_contract = mark.get("unrealized_pnl")     # ONE contract

        if per_contract is not None:
            paper_account_db.update_position_mark(
                db_path, pos["position_id"],
                current_value=mark.get("current_value"),
                unrealized_pnl=round(per_contract * qty, 2),   # position-level
                current_short_delta=mark.get("current_short_delta"),
                last_mark_ts=datetime.now(TZ).isoformat())

        # Expiration settlement at intrinsic value vs the underlying — only at/
        # after the 15:00 CT close on the expiry day (or later). The underlying
        # comes from the repricer when available, else a direct quote (the
        # repricer returns None for a past expiration).
        if should_settle(pos["expiration"], now_date, now_ct):
            settlement = mark.get("current_underlying") or underlying_last(client, pos["symbol"])
            if not settlement:
                log.warning("%s EXPIRY DEFERRED %s: no underlying quote, retry next cycle",
                            _default_broker.PREFIX, pos["symbol"])
                continue
            net, pnl_per = signal_repricer.intrinsic_value(trade, settlement)
            realized = net_realized_pnl(round(pnl_per * qty, 2), pos, qty, expired=True)
            _close(db_path, pos, exit_debit=round(net, 2), exit_order_id=None,
                   realized_pnl=realized, reason="EXPIRED", status="EXPIRED")
            log.info("%s EXPIRED %s %s x%s pnl %.2f (net of fees)", _default_broker.PREFIX,
                     pos["symbol"], pos["strategy"], qty, realized)
            continue

        if per_contract is None:
            continue   # unpriceable this cycle; leave the position open

        ctx = {"entry_credit": pos["entry_credit"], "unrealized_pnl": per_contract,
               "current_short_delta": mark.get("current_short_delta"),
               "dte_remaining": dte}
        rec = signal_recommender.recommend(ctx)
        if rec["action"] in ("TAKE_PROFIT", "CUT"):
            order = {"signal_id": pos["signal_id"], "symbol": pos["symbol"],
                     "side": "BUY_TO_CLOSE", "strategy": pos["strategy"],
                     "short_strike": pos["short_strike"], "long_strike": pos["long_strike"],
                     "call_short": pos.get("call_short"), "call_long": pos.get("call_long"),
                     "expiration": pos["expiration"], "quantity": qty,
                     "limit_price": mark.get("current_value"), "legs": []}
            resp = broker.submit_order(order, client)
            oid = _record_order(db_path, order, resp)
            if resp["status"] != "FILLED":
                log.warning("%s CLOSE UNFILLED %s %s status=%s (retry next cycle)",
                            _default_broker.PREFIX, pos["symbol"], pos["strategy"], resp["status"])
                continue
            fill = resp["price"]
            gross = round((pos["entry_credit"] - fill) * MULTIPLIER * qty, 2)
            realized = net_realized_pnl(gross, pos, qty, expired=False)
            _close(db_path, pos, exit_debit=fill, exit_order_id=oid,
                   realized_pnl=realized, reason=rec["code"], status="CLOSED")
            log.info("%s CLOSED %s %s x%s @ %.2f pnl %.2f net (%s)", _default_broker.PREFIX,
                     pos["symbol"], pos["strategy"], qty, fill, realized, rec["code"])

    # Session drawdown guard — never auto-unhalts mid-session.
    if paper_account_db.should_halt(db_path, paper_account_db.open_unrealized(db_path),
                                    config_paper.MAX_SESSION_DRAWDOWN):
        paper_account_db.set_halted(db_path, True)
        log.warning("%s session drawdown breached -> HALTED", _default_broker.PREFIX)


#############################################
# UI SNAPSHOT
#############################################

def account_snapshot(db_path=None):
    """UI-facing account summary for the Portfolio cards."""
    acct = paper_account_db.get_account(db_path)
    open_positions = paper_account_db.fetch_open_positions(db_path)
    open_unreal = paper_account_db.open_unrealized(db_path)
    return {
        "equity": round(acct["cash"] + acct["buying_power_reserved"] + open_unreal, 2),
        "cash": acct["cash"],
        "buying_power_reserved": acct["buying_power_reserved"],
        "open_unrealized": round(open_unreal, 2),
        "session_pnl": round(acct["session_realized_pnl"] + open_unreal, 2),
        "realized_pnl": acct["realized_pnl"],
        "open_count": len(open_positions),
        "halted": bool(acct["halted"]),
    }
