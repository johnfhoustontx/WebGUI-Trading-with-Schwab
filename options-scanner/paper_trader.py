"""
paper_trader.py - Paper Trade Management
Version: 1.1.0
Last Updated: 2026-04-19

Manages paper trades. Persistence is now SQLite-backed (trades_db.py) instead
of rewriting paper_trades.json on every mutation; the public API is unchanged
so dashboard.py and server/routes/trades.py keep working.

Version 1.1.0 Changes:
- Migrated persistence from paper_trades.json / trade_log.json to
  data/trades.db (via trades_db.py). Removes per-mutation whole-file rewrite
  and the corruption risk of killing the process mid-write. Legacy JSON files
  are imported on first run and preserved as a backup.

Version 1.0.0 Changes:
- Initial implementation
- JSON-based trade log
- P&L tracking
- Trade lifecycle (open/close/expire)
"""

import uuid
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import trades_db
import trade_tracker_client

TZ = ZoneInfo("America/Chicago")
DATA_DIR = Path(__file__).parent / "data"

DATA_DIR.mkdir(exist_ok=True)

#############################################
# TRADE MODEL
#############################################

# Non-credit DEFINED-RISK structures the swing scanner produces that the ledger can now
# paper-trade: long single options + debit verticals. Their max loss is the debit paid.
# Naked shorts (SHORT_CALL/SHORT_PUT) are deliberately EXCLUDED (undefined risk).
PAPER_DEBIT_TYPES = {"LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT"}

# Shares per option contract. Ledger rows store PER-SHARE prices (entry_credit,
# max_loss_per) and PER-CONTRACT dollars (*_total); this is the factor between them.
_CONTRACT_MULT = 100


def _create_debit_trade(signal, quantity, mode, now):
    """Build a legs-based DEBIT paper trade (long option / debit vertical).

    The swing scanner's ``net_debit`` / ``max_loss`` / ``max_profit`` are PER-CONTRACT
    dollars (already ×100). A DEBIT is stored so the existing Paper Trades columns render:
    ``entry_credit`` is the NEGATIVE per-share debit (reads as "you paid"), ``max_loss`` is
    the debit at risk, and ``legs`` drives repricing + expiration settlement."""
    mult = 100
    legs = [{"kind": l.get("kind"), "side": l.get("side"), "strike": l.get("strike"),
             "expiration": l.get("expiration") or signal.get("expiration"),
             "qty": l.get("qty", 1)}
            for l in (signal.get("legs") or []) if isinstance(l, dict)]
    net_debit = signal.get("net_debit") or 0.0        # per-contract $
    max_loss = signal.get("max_loss") or 0.0          # per-contract $ (incl. commission)
    max_profit = signal.get("max_profit")             # per-contract $ or None (unbounded)
    return {
        "trade_id": str(uuid.uuid4())[:8], "mode": mode, "status": "OPEN",
        "symbol": signal["symbol"], "strategy": signal["type"],
        "trade_type": signal.get("trade_type", "SWING"),
        "expiration": signal["expiration"], "dte_at_entry": signal.get("dte", 0),
        "quantity": quantity,
        "direction": "DEBIT",
        "legs": legs,
        "entry_debit": round(net_debit, 2),                       # per contract
        "entry_debit_total": round(net_debit * quantity, 2),
        # Display-compat: a DEBIT reads as a NEGATIVE credit (premium PAID).
        "entry_credit": round(-net_debit / mult, 4),             # per share
        "entry_credit_total": round(-net_debit * quantity, 2),
        "max_loss_per": round(max_loss / mult, 4),               # per share
        "max_loss_total": round(max_loss * quantity, 2),
        "max_profit_total": (round(max_profit * quantity, 2)
                             if max_profit is not None else None),
        "unbounded": bool(signal.get("unbounded")),
        "breakeven": ", ".join(str(b) for b in (signal.get("breakevens") or [])),
        "short_strike": None, "long_strike": None, "width": None,
        "short_delta": None, "net_theta": signal.get("net_theta", 0),
        "entry_delta": None, "entry_theta": None, "entry_vega": None, "entry_gamma": None,
        "entry_bid": None, "entry_ask": None,
        "underlying_at_entry": signal.get("underlying_price", 0),
        "entry_time": now.isoformat(), "exit_time": None, "exit_debit": None,
        "exit_debit_total": None, "realized_pnl": None, "exit_reason": None, "notes": "",
    }


def _is_normalized_signal(signal):
    """True for a signal that has been through ``strategy_scanner`` normalization.

    Keyed on ``legs``: ``_normalize_credit`` always attaches the reconstructed leg
    list (2 for a vertical, 4 for an iron condor), and every natively-built family
    carries one too. No raw ``scanner_engine`` signal has it — neither
    ``screen_spreads`` nor ``build_iron_condors`` builds a ``legs`` key, and neither
    does ``signal_db``/``signal_recorder`` on the way back out. It is also the key
    ``_create_debit_trade`` already reads, so both scale-sensitive paths agree on
    what "normalized" means.
    """
    return bool(signal.get("legs"))


def _credit_max_loss_per_share(signal):
    """Per-SHARE max loss for a credit spread, whichever scanner produced the signal.

    A RAW ``scanner_engine`` signal carries ``max_loss`` in PER-SHARE dollars. A swing
    signal normalized by ``strategy_scanner._normalize_credit`` carries it in
    PER-CONTRACT dollars (x100) with round-trip commission folded IN. That asymmetry
    is deliberate upstream — the Strategy Finder table renders ``max_loss`` as dollars
    while ``credit`` stays per-share — but it MUST be undone here, or a $345 spread is
    booked as $34,500 of risk.

    Commission is stripped rather than kept, so the ledger stays GROSS throughout:
    ``entry_credit`` is the gross per-share credit and ``close_paper_trade`` computes
    realized P&L gross, so a commission-inclusive risk figure would be the only net
    number in the row. Stripping it also makes ``entry_credit + max_loss_per``
    reconcile exactly to ``width``, and makes the stored row identical for identical
    economics regardless of which scanner produced the signal.
    """
    max_loss = signal["max_loss"]
    if not _is_normalized_signal(signal):
        return max_loss                                  # already per-share
    commission = signal.get("commission") or 0.0         # absent -> keep it conservative
    return round((max_loss - commission) / _CONTRACT_MULT, 4)


def create_paper_trade(signal, quantity=1, mode="PAPER"):
    """Create a paper trade from a scanner signal.

    In the future, replace this with schwab order placement:
        client.place_order(account_hash, order)
    """
    now = datetime.now(TZ)
    if signal.get("type") in PAPER_DEBIT_TYPES:
        return _create_debit_trade(signal, quantity, mode, now)
    multiplier = 100
    max_loss_per = _credit_max_loss_per_share(signal)

    trade = {
        "trade_id": str(uuid.uuid4())[:8],
        "mode": mode,  # PAPER or LIVE (future)
        "status": "OPEN",
        "symbol": signal["symbol"],
        "strategy": signal["type"],  # PCS, CCS, IC
        "trade_type": signal.get("trade_type", ""),
        "expiration": signal["expiration"],
        "dte_at_entry": signal.get("dte", 0),
        "short_strike": signal["short_strike"],
        "long_strike": signal["long_strike"],
        "width": signal["width"],
        "quantity": quantity,
        "entry_credit": signal["credit"],
        "entry_credit_total": round(signal["credit"] * quantity * multiplier, 2),
        "max_loss_per": max_loss_per,
        "max_loss_total": round(max_loss_per * quantity * multiplier, 2),
        "breakeven": signal.get("breakeven", ""),
        "short_delta": signal.get("short_delta", 0),
        "net_theta": signal.get("net_theta", 0),
        "entry_delta": None,  # signal lacks long-leg delta; honest unknown
        "entry_theta": (-signal["net_theta"]) if signal.get("net_theta") is not None else None,
        "entry_vega": (-signal["net_vega"]) if signal.get("net_vega") is not None else None,
        "entry_gamma": None,  # scanner does not carry net gamma
        # Persist entry-time spread quotes so liquidity scoring still works
        # for paper trades when the live chain is unavailable (after-hours,
        # chain mismatch, etc.).
        "entry_bid": signal.get("bid"),
        "entry_ask": signal.get("ask"),
        "underlying_at_entry": signal.get("underlying_price", 0),
        "entry_time": now.isoformat(),
        "exit_time": None,
        "exit_debit": None,
        "exit_debit_total": None,
        "realized_pnl": None,
        "exit_reason": None,
        "notes": "",
    }

    # IC has extra fields
    if signal["type"] == "IC":
        trade["call_short"] = signal.get("call_short", 0)
        trade["call_long"] = signal.get("call_long", 0)

    return trade


def close_paper_trade(trade, exit_debit, reason="MANUAL"):
    """Close a paper trade. In the future, this places a closing order."""
    now = datetime.now(TZ)
    multiplier = 100
    qty = trade["quantity"]

    trade["status"] = "CLOSED"
    trade["exit_time"] = now.isoformat()
    trade["exit_debit"] = round(exit_debit, 2)
    trade["exit_debit_total"] = round(exit_debit * qty * multiplier, 2)
    trade["realized_pnl"] = round((trade["entry_credit"] - exit_debit) * qty * multiplier, 2)
    trade["exit_reason"] = reason
    return trade


def _expire_debit_trade(trade, sp):
    """Settle a DEBIT/legs trade at intrinsic: worth its net intrinsic; P&L = value − debit."""
    import signal_repricer
    qty = trade["quantity"]
    net_per_share, pnl_per_contract = signal_repricer.legs_intrinsic_value(trade, sp)
    trade["status"] = "EXPIRED"
    trade["exit_time"] = datetime.now(TZ).isoformat()
    trade["exit_debit"] = round(net_per_share, 2)                    # exit VALUE (sell to close)
    trade["exit_debit_total"] = round(net_per_share * qty * 100, 2)
    trade["realized_pnl"] = round(pnl_per_contract * qty, 2)
    trade["exit_reason"] = f"EXPIRED @ {sp}"
    return trade


def expire_paper_trade(trade, settlement_price):
    """Mark a paper trade as expired. Calculate P&L at expiration."""
    if trade.get("direction") == "DEBIT":
        return _expire_debit_trade(trade, settlement_price)
    multiplier = 100
    qty = trade["quantity"]
    short_k = trade["short_strike"]
    long_k = trade["long_strike"]
    credit = trade["entry_credit"]
    sp = settlement_price

    if trade["strategy"] == "PCS":
        short_val = max(short_k - sp, 0)
        long_val = max(long_k - sp, 0)
        net_val = short_val - long_val
    elif trade["strategy"] == "CCS":
        short_val = max(sp - short_k, 0)
        long_val = max(sp - trade["long_strike"], 0)
        net_val = short_val - long_val
    elif trade["strategy"] == "IC":
        put_val = max(short_k - sp, 0) - max(long_k - sp, 0)
        call_short = trade.get("call_short", 0)
        call_long = trade.get("call_long", 0)
        call_val = max(sp - call_short, 0) - max(sp - call_long, 0)
        net_val = put_val + call_val
    else:
        net_val = 0

    pnl = (credit - net_val) * qty * multiplier

    trade["status"] = "EXPIRED"
    trade["exit_time"] = datetime.now(TZ).isoformat()
    trade["exit_debit"] = round(net_val, 2)
    trade["exit_debit_total"] = round(net_val * qty * multiplier, 2)
    trade["realized_pnl"] = round(pnl, 2)
    trade["exit_reason"] = f"EXPIRED @ {sp}"
    return trade

#############################################
# PERSISTENCE (SQLite via trades_db)
#############################################

def load_trades():
    """Returns list of trade dicts, ordered by entry_time ascending."""
    conn = trades_db.connect()
    try:
        return trades_db.fetch_all(conn)
    finally:
        conn.close()


def save_trades(trades):
    """Replace the trades table with the given list. Retained for back-compat
    with callers that used to overwrite paper_trades.json wholesale."""
    conn = trades_db.connect()
    try:
        existing = {t["trade_id"] for t in trades_db.fetch_all(conn)}
        incoming = {t["trade_id"] for t in trades}
        for tid in existing - incoming:
            trades_db.delete_trade(conn, tid)
        for t in trades:
            trades_db.insert_trade(conn, t)
        conn.commit()
    finally:
        conn.close()


def add_trade(trade):
    conn = trades_db.connect()
    try:
        trades_db.insert_trade(conn, trade)
        _log_event(conn, trade, "OPENED")
        conn.commit()
    finally:
        conn.close()
    trade_tracker_client.track(trade)
    return load_trades()


def update_trade(trade_id, updated_trade):
    conn = trades_db.connect()
    try:
        trades_db.update_trade(conn, trade_id, updated_trade)
        _log_event(conn, updated_trade, updated_trade["status"])
        conn.commit()
    finally:
        conn.close()
    if updated_trade["status"] in ("CLOSED", "EXPIRED"):
        trade_tracker_client.untrack(trade_id)
    return load_trades()


def delete_trade(trade_id):
    """Delete a trade by ID. Returns updated trades list."""
    conn = trades_db.connect()
    try:
        trade = trades_db.fetch_one(conn, trade_id)
        trades_db.delete_trade(conn, trade_id)
        if trade:
            _log_event(conn, trade, "DELETED")
        conn.commit()
    finally:
        conn.close()
    if trade:
        trade_tracker_client.untrack(trade_id)
    return load_trades()


def delete_closed_trades():
    """Delete all closed / expired trades. Returns count deleted."""
    conn = trades_db.connect()
    try:
        closed = [
            t for t in trades_db.fetch_all(conn) if t["status"] != "OPEN"
        ]
        n = trades_db.delete_trades_by_status(conn, ("CLOSED", "EXPIRED"))
        for t in closed:
            _log_event(conn, t, "DELETED")
        conn.commit()
    finally:
        conn.close()
    return n


def get_open_trades():
    conn = trades_db.connect()
    try:
        return trades_db.fetch_by_status(conn, "OPEN")
    finally:
        conn.close()


def get_all_trades():
    return load_trades()

#############################################
# TRADE EVENT LOG
#############################################

def _log_event(conn, trade, event):
    """Append trade event row on an already-open connection."""
    entry = {
        "timestamp": datetime.now(TZ).isoformat(),
        "event": event,
        "trade_id": trade["trade_id"],
        "symbol": trade["symbol"],
        "strategy": trade["strategy"],
        "short_strike": trade["short_strike"],
        "credit": trade["entry_credit"],
        "pnl": trade.get("realized_pnl"),
    }
    trades_db.insert_event(conn, entry)


#############################################
# SUMMARY STATS
#############################################

def get_trade_summary():
    trades = load_trades()
    open_trades = [t for t in trades if t["status"] == "OPEN"]
    closed = [t for t in trades if t["status"] in ("CLOSED", "EXPIRED")]
    winners = [t for t in closed if (t.get("realized_pnl") or 0) > 0]
    losers = [t for t in closed if (t.get("realized_pnl") or 0) < 0]
    total_pnl = sum(t.get("realized_pnl") or 0 for t in closed)
    open_risk = sum(t.get("max_loss_total") or 0 for t in open_trades)
    open_credit = sum(t.get("entry_credit_total") or 0 for t in open_trades)

    return {
        "total_trades": len(trades),
        "open_count": len(open_trades),
        "closed_count": len(closed),
        "win_count": len(winners),
        "loss_count": len(losers),
        "win_rate": round(len(winners) / len(closed) * 100, 1) if closed else 0,
        "total_pnl": round(total_pnl, 2),
        "open_risk": round(open_risk, 2),
        "open_credit": round(open_credit, 2),
    }
