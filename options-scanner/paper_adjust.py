"""
paper_adjust.py - Rescue apply primitives for paper positions
Version: 1.0.0
Last Updated: 2026-06-21

Executes a chosen rescue candidate (from the options_svc rescue engine) against
an OPEN paper position in paper_account_db. Each primitive:
  (a) validates the position is OPEN (else a clean no-op refusal),
  (b) mutates the position / opens a linked new one,
  (c) realizes commission + any net cash into the account using the SAME cash
      mechanism the manage-cycle close path uses (release_buying_power +
      realize_pnl), never a parallel one,
  (d) writes a position_adjustments audit row LAST (so a failure before it never
      records a phantom adjustment),
  (e) returns a result dict.

Result dict shape:
  {"ok": bool, "action": str, "position_id": int,
   "new_position_id": int|None, "realized": float|None, "error": str|None}

`position` is the DB row as a dict (caller loads it). `candidate` is the
RescueCandidate dict from the rescue engine (action / gross_cash / commission /
net_cash / est_fill_legs / new_max_loss / new_width / new_expiry ...).

Cash conventions (match rescue.py builders):
  gross_cash : credit (+) / debit (-) on the option legs, before fees
  commission : positive dollars, ALWAYS reduces cash
  net_cash   : gross_cash - commission (the net effect on cash)

Version 1.0.0 Changes:
- Initial implementation (Task 5.2 — rescue tested trades).
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import paper_account_db
import paper_engine

log = logging.getLogger("paper_adjust")

TZ = ZoneInfo("America/Chicago")
MULTIPLIER = 100


#############################################
# RESULT / GUARD HELPERS
#############################################

def _result(ok, action, position_id, new_position_id=None, realized=None, error=None):
    return {"ok": ok, "action": action, "position_id": position_id,
            "new_position_id": new_position_id, "realized": realized, "error": error}


def _is_open(position):
    return (position or {}).get("status", "OPEN") == "OPEN"


def _legs(candidate):
    return candidate.get("est_fill_legs") or []


def _record(db_path, position, candidate, parent_position_id=None):
    """Write the audit row. Called LAST in every primitive."""
    paper_account_db.insert_adjustment(
        db_path,
        position_id=position["position_id"],
        action=candidate.get("action"),
        legs=_legs(candidate),
        gross_cash=candidate.get("gross_cash", 0.0),
        commission=candidate.get("commission", 0.0),
        net_cash=candidate.get("net_cash", 0.0),
        reason="rescue",
        parent_position_id=parent_position_id,
    )


def _apply_net_cash(db_path, candidate):
    """Realize the candidate's net cash (credit/debit, commission already netted)
    into the account using the same realize_pnl mechanism the close path uses."""
    net = round(candidate.get("net_cash", 0.0), 2)
    if net:
        paper_account_db.realize_pnl(db_path, net)


def _reconcile_bp(db_path, old_ml, new_ml):
    """Keep reserved buying power equal to the position's current max_loss_total.

    At entry the engine reserves max_loss_total; at close it releases the same
    amount. Any primitive that REWRITES max_loss_total (old_ml -> new_ml) must
    move reserved BP by the delta so the invariant
    `buying_power_reserved == max_loss_total` still holds (otherwise the wrong
    amount is released at close, stranding/over-releasing BP).

    delta = old_ml - new_ml:
      delta > 0 (max loss shrank)  -> release  (cash += delta, reserved -= delta)
      delta < 0 (max loss grew)    -> reserve  (cash -= -delta, reserved += -delta)
    This is SEPARATE from _apply_net_cash (the actual premium flow); both are
    correct and independent. No-op when max_loss_total is unchanged/unknown.
    """
    if old_ml is None or new_ml is None:
        return
    delta = round(old_ml - new_ml, 2)
    if delta > 0:
        paper_account_db.release_buying_power(db_path, delta)
    elif delta < 0:
        paper_account_db.reserve_buying_power(db_path, -delta)


def _leg_strike(candidate, side, right):
    """First est_fill_leg matching side+right (e.g. the BUY PUT = new long)."""
    for lg in _legs(candidate):
        if (lg.get("side") == side and
                (lg.get("right") or "").upper() == right.upper()):
            return lg.get("strike")
    return None


def _exit_debit(position, candidate):
    """Per-contract debit implied by the close gross_cash. gross_cash is the
    NEGATIVE dollar cost (debit) on qty contracts: gross = -cv*100*qty."""
    qty = position.get("quantity") or 1
    gross = candidate.get("gross_cash", 0.0)
    return round(-gross / (MULTIPLIER * qty), 4) if qty else 0.0


#############################################
# CLOSE / PARTIAL
#############################################

def apply_close(db_path, position, candidate, broker=None):
    """Full close at the candidate's implied debit. Reuses paper_engine._close
    for the BP-release + P&L-realize cash math, then debits commission."""
    if not _is_open(position):
        return _result(False, "close", position.get("position_id"),
                       error="position not open")
    qty = position.get("quantity") or 1
    exit_debit = _exit_debit(position, candidate)
    realized = round((position["entry_credit"] - exit_debit) * MULTIPLIER * qty, 2)
    paper_engine._close(db_path, position, exit_debit=exit_debit, exit_order_id=None,
                        realized_pnl=realized, reason="RESCUE_CLOSE", status="CLOSED")
    # commission is on top of the realized spread P&L
    comm = round(candidate.get("commission", 0.0), 2)
    if comm:
        paper_account_db.realize_pnl(db_path, -comm)
    _record(db_path, position, candidate)
    log.info("RESCUE close pos=%s realized=%.2f comm=%.2f",
             position["position_id"], realized, comm)
    return _result(True, "close", position["position_id"], realized=realized)


def apply_partial_close(db_path, position, candidate, broker=None):
    """Close qty//2 contracts: reduce quantity, realize P&L on the closed slice,
    release that fraction of reserved BP, and reset max_loss_total to the
    candidate's new value (covering the remaining contracts)."""
    if not _is_open(position):
        return _result(False, "partial_close", position.get("position_id"),
                       error="position not open")
    qty = position.get("quantity") or 1
    close_qty = qty // 2
    if close_qty < 1:
        return _result(False, "partial_close", position["position_id"],
                       error="too few contracts to scale down")
    remaining = qty - close_qty
    exit_debit = round(-candidate.get("gross_cash", 0.0) / (MULTIPLIER * close_qty), 4)
    realized = round((position["entry_credit"] - exit_debit) * MULTIPLIER * close_qty, 2)

    old_ml = position.get("max_loss_total") or 0.0
    new_ml = candidate.get("new_max_loss")
    if new_ml is None:
        new_ml = round(old_ml * remaining / qty, 2)
    released_bp = round(old_ml - new_ml, 2)

    # release the closed slice's reserved BP, realize its P&L, debit commission
    if released_bp:
        paper_account_db.release_buying_power(db_path, released_bp)
    paper_account_db.realize_pnl(db_path, realized)
    comm = round(candidate.get("commission", 0.0), 2)
    if comm:
        paper_account_db.realize_pnl(db_path, -comm)

    paper_account_db.update_position_mark(
        db_path, position["position_id"],
        quantity=remaining, max_loss_total=new_ml,
        last_mark_ts=datetime.now(TZ).isoformat())
    _record(db_path, position, candidate)
    log.info("RESCUE partial_close pos=%s %d/%d realized=%.2f",
             position["position_id"], close_qty, qty, realized)
    return _result(True, "partial_close", position["position_id"], realized=realized)


#############################################
# NARROW (update long strike in place)
#############################################

def apply_narrow(db_path, position, candidate, broker=None):
    """Move the long strike closer (buy a nearer long, sell the old long).
    Recomputes width + max_loss_total from the candidate and applies the net
    debit to cash. Position stays OPEN."""
    if not _is_open(position):
        return _result(False, "narrow", position.get("position_id"),
                       error="position not open")
    new_long = _leg_strike(candidate, "BUY", "PUT")
    if new_long is None:
        new_long = _leg_strike(candidate, "BUY", "CALL")
    new_width = candidate.get("new_width")
    new_ml = candidate.get("new_max_loss")

    _apply_net_cash(db_path, candidate)   # narrow is a net debit
    # Reconcile reserved buying power so it tracks the new max_loss_total.
    # Narrowing shrinks width so new_ml <= old_ml; the delta releases BP
    # (cash += delta, reserved -= delta). Done independently of the net-cash
    # premium flow above. (Same pattern as apply_partial_close.)
    _reconcile_bp(db_path, position.get("max_loss_total"), new_ml)
    fields = {"last_mark_ts": datetime.now(TZ).isoformat()}
    if new_long is not None:
        fields["long_strike"] = new_long
    if new_width is not None:
        fields["width"] = new_width
    if new_ml is not None:
        fields["max_loss_total"] = new_ml
    paper_account_db.update_position_mark(db_path, position["position_id"], **fields)
    _record(db_path, position, candidate)
    log.info("RESCUE narrow pos=%s long=%s width=%s",
             position["position_id"], new_long, new_width)
    return _result(True, "narrow", position["position_id"])


#############################################
# CONVERT TO IC / BUTTERFLY (add opposite-side legs)
#############################################

def _apply_convert(db_path, position, candidate, action):
    if not _is_open(position):
        return _result(False, action, position.get("position_id"),
                       error="position not open")
    strategy = position.get("strategy")
    # The added legs form the opposite side. For a PCS we add a CALL spread; for
    # a CCS we add a PUT spread (mirrors rescue.build_convert_ic/butterfly).
    sell_call = _leg_strike(candidate, "SELL", "CALL")
    buy_call = _leg_strike(candidate, "BUY", "CALL")
    sell_put = _leg_strike(candidate, "SELL", "PUT")
    buy_put = _leg_strike(candidate, "BUY", "PUT")

    fields = {"strategy": "IC", "last_mark_ts": datetime.now(TZ).isoformat()}
    if strategy == "PCS":     # adding a call spread above
        if sell_call is not None:
            fields["call_short"] = sell_call
        if buy_call is not None:
            fields["call_long"] = buy_call
    else:                     # CCS — adding a put spread below
        if sell_put is not None:
            fields["short_strike"] = sell_put
        if buy_put is not None:
            fields["long_strike"] = buy_put
    new_ml = candidate.get("new_max_loss")
    if new_ml is not None:
        fields["max_loss_total"] = new_ml

    _apply_net_cash(db_path, candidate)   # convert collects a net credit
    # Reconcile reserved BP to the new max_loss_total. Converts usually LOWER
    # max loss (credit collected) -> release; an edge-case increase reserves
    # more. Handled both directions by _reconcile_bp. Independent of net_cash.
    _reconcile_bp(db_path, position.get("max_loss_total"), new_ml)
    paper_account_db.update_position_mark(db_path, position["position_id"], **fields)
    _record(db_path, position, candidate)
    log.info("RESCUE %s pos=%s -> IC max_loss=%s",
             action, position["position_id"], new_ml)
    return _result(True, action, position["position_id"])


def apply_convert_ic(db_path, position, candidate, broker=None):
    """Convert PCS->IC (or CCS->IC): add the opposite-side spread legs, recompute
    max_loss_total from the candidate, credit the net cash. Stays OPEN."""
    return _apply_convert(db_path, position, candidate, "convert_ic")


def apply_convert_butterfly(db_path, position, candidate, broker=None):
    """Convert to an iron-fly-like body (added legs from est_fill_legs). The
    schema's strategy values don't include a butterfly label, so we keep "IC"
    (the position is now a four-leg defined-risk structure); max_loss_total is
    reset from the candidate and the net credit reaches cash. Stays OPEN."""
    return _apply_convert(db_path, position, candidate, "convert_butterfly")


#############################################
# ROLL (close current + open linked new)
#############################################

def apply_roll(db_path, position, candidate, broker=None):
    """Handles roll_down / roll_out / roll_down_out: CLOSE the current position
    (realize its P&L via the existing close path), then OPEN a new linked
    position with the candidate's new strikes/expiry/width/credit, setting
    parent_position_id = the old position id. Returns new_position_id.

    The roll's gross_cash is the NET of (close debit + reopen credit) over qty
    contracts. We close the current spread at its current value (the BUY legs of
    the closing pair) so the old position's realized P&L matches the manage cycle,
    and apply the remaining roll cash (reopen credit minus close debit minus
    commission) onto the account."""
    action = candidate.get("action", "roll")
    if not _is_open(position):
        return _result(False, action, position.get("position_id"),
                       error="position not open")
    qty = position.get("quantity") or 1
    is_put = position.get("strategy") in ("PCS", "IC")
    right = "PUT" if is_put else "CALL"

    # Partition est_fill_legs into the closing pair (buy back short / sell long of
    # the OLD spread) and the reopening pair (sell new short / buy new long).
    close_legs, reopen_legs = _partition_roll_legs(candidate, right)

    # --- close the current spread (realize P&L like the manage cycle) ---
    # Closing a credit spread BUYS back the short and SELLS the long.
    exit_debit = _pair_debit(close_legs)   # short_buy_px - long_sell_px
    if exit_debit is None:
        exit_debit = position.get("entry_credit", 0.0)   # fallback: scratch
    realized = round((position["entry_credit"] - exit_debit) * MULTIPLIER * qty, 2)
    paper_engine._close(db_path, position, exit_debit=exit_debit, exit_order_id=None,
                        realized_pnl=realized, reason="RESCUE_ROLL", status="CLOSED")

    # --- account cash for the roll ---
    # The close above already realized (entry_credit - exit_debit) into cash and
    # released the old BP. The reopen credit is captured as the new position's
    # entry_credit and its BP is reserved below (so it is NOT separately credited
    # here — it offsets the newly reserved BP, exactly like a fresh entry). The
    # only roll cash not yet booked is the commission, which we debit here.
    comm = round(candidate.get("commission", 0.0), 2)
    if comm:
        paper_account_db.realize_pnl(db_path, -comm)

    # --- open the new linked spread ---
    new_expiry = candidate.get("new_expiry") or position["expiration"]
    sell_leg = next((lg for lg in reopen_legs if lg.get("side") == "SELL"), None)
    buy_leg = next((lg for lg in reopen_legs if lg.get("side") == "BUY"), None)
    new_short = sell_leg.get("strike") if sell_leg else position.get("short_strike")
    new_long = buy_leg.get("strike") if buy_leg else position.get("long_strike")
    new_width = candidate.get("new_width") or position.get("width")
    new_ml = candidate.get("new_max_loss")
    # reopen credit per contract (the SELL/BUY pair at the NEW strikes/expiry)
    reopen_credit = _pair_credit(reopen_legs)
    if reopen_credit is None:
        reopen_credit = position.get("entry_credit", 0.0)
    max_loss_per = round(new_ml / qty, 2) if (new_ml and qty) else position.get("max_loss_per")

    new_id = paper_account_db.insert_position(db_path, {
        "signal_id": position.get("signal_id"), "symbol": position["symbol"],
        "strategy": position["strategy"],
        "short_strike": new_short, "long_strike": new_long,
        "call_short": position.get("call_short"), "call_long": position.get("call_long"),
        "width": new_width, "expiration": new_expiry,
        "dte_at_entry": position.get("dte_at_entry"), "quantity": qty,
        "entry_credit": reopen_credit, "entry_order_id": None,
        "max_loss_per": max_loss_per, "max_loss_total": new_ml,
        "entry_ts": datetime.now(TZ).isoformat(),
    })
    # link + reserve BP for the reopened position
    paper_account_db.update_position_mark(db_path, new_id, parent_position_id=position["position_id"])
    if new_ml:
        paper_account_db.reserve_buying_power(db_path, round(new_ml, 2))
    _record(db_path, position, candidate, parent_position_id=position["position_id"])
    log.info("RESCUE %s pos=%s -> new=%s %s/%s exp=%s realized=%.2f",
             action, position["position_id"], new_id, new_short, new_long,
             new_expiry, realized)
    return _result(True, action, position["position_id"],
                   new_position_id=new_id, realized=realized)


def _partition_roll_legs(candidate, right):
    """Split est_fill_legs into (close_legs, reopen_legs).

    Roll candidates from the engine come in two shapes:
      * roll_down / roll_down_out (4 legs): [BUY old_short, SELL old_long,
        SELL new_short, BUY new_long] — first SELL/BUY-back pair closes the OLD
        spread, the trailing SELL/BUY pair reopens the new one.
      * roll_out (2 legs): [SELL new_short, BUY new_long] — reopen only. LEGACY:
        boards built before 2026-08-20 emitted this shape (the old spread closed
        at the entry-credit scratch fallback); every builder now emits the 4-leg
        shape, and this branch survives only so a cached pre-fix board still
        applies rather than crashing.
    """
    legs = [lg for lg in _legs(candidate)
            if (lg.get("right") or "").upper() == right.upper()]
    # The engine emits the close-pair (BUY-back short, SELL long) FIRST, then the
    # reopen-pair (SELL new short, BUY new long). Partition positionally — strikes
    # can collide (a roll_down's new short == the old long), so order, not strike,
    # is the reliable discriminator. A 2-leg roll_out carries the reopen pair only.
    if len(legs) >= 4:
        return legs[:2], legs[2:4]
    return [], legs


def _pair_debit(legs):
    """Per-contract debit to close: short buy-back price - long sell price."""
    buy = next((lg.get("price") for lg in legs if lg.get("side") == "BUY"), None)
    sell = next((lg.get("price") for lg in legs if lg.get("side") == "SELL"), None)
    if buy is None and sell is None:
        return None
    return round((buy or 0.0) - (sell or 0.0), 4)


def _pair_credit(legs):
    """Per-contract credit to open: short sell price - long buy price."""
    sell = next((lg.get("price") for lg in legs if lg.get("side") == "SELL"), None)
    buy = next((lg.get("price") for lg in legs if lg.get("side") == "BUY"), None)
    if sell is None and buy is None:
        return None
    return round(max(0.0, (sell or 0.0) - (buy or 0.0)), 4)


#############################################
# INVERTED — advisory-only guard
#############################################

def apply_inverted(db_path, position, candidate, broker=None):
    """Inverted is advisory-only in the rescue engine — never auto-applied. This
    guard lets the dispatcher refuse it cleanly without mutating anything."""
    return _result(False, "inverted", position.get("position_id"),
                   error="inverted is advisory-only — place manually")


#############################################
# DISPATCHER — re-price (stale guard) then route to the matching primitive
#############################################

# action -> primitive
_APPLY = {
    "close": apply_close,
    "partial_close": apply_partial_close,
    "narrow": apply_narrow,
    "convert_ic": apply_convert_ic,
    "convert_butterfly": apply_convert_butterfly,
    "roll_down": apply_roll,
    "roll_out": apply_roll,
    "roll_down_out": apply_roll,
    "inverted": apply_inverted,   # returns ok=False (advisory)
}

# Actions the engine emits as advice only — never auto-applied. `inverted` has a
# dedicated primitive (clean refusal), so it is dispatched normally; the rest are
# refused up front by the dispatcher.
_ADVISORY_ACTIONS = {"inverted", "broken_wing", "futures_hedge"}


def _reprice_candidate_net(candidate, price_leg):
    """Re-price the candidate's est_fill_legs via price_leg and return the fresh
    net_cash, or None if any leg is now unpriceable. A leg's cash sign follows
    its side: SELL = +credit, BUY = -debit. Commission is taken from the
    candidate unchanged (rates are fixed)."""
    legs = candidate.get("est_fill_legs") or []
    if not legs:
        # no tradeable legs to reprice (e.g. close uses current_value, advisory) ->
        # signal "cannot reprice from legs"; caller treats as no-drift.
        return None
    gross = 0.0
    for lg in legs:
        px = price_leg(lg.get("symbol") or candidate.get("symbol"),
                       lg.get("expiry"), lg.get("right"), lg.get("strike"))
        if px is None:
            return None
        qty = lg.get("qty") or 1
        sign = 1.0 if (lg.get("side") == "SELL") else -1.0
        gross += sign * px * MULTIPLIER * qty
    return round(gross - abs(candidate.get("commission", 0.0)), 2)


def apply_adjustment(db_path, position, candidate, price_leg=None, broker=None,
                     tolerance=0.15):
    """Re-price (stale guard) then dispatch to the matching apply primitive.

    Returns the primitive's result dict, or a stale/error result without mutating.

    `tolerance` is a FRACTION (0.15 = 15 %): if the live-repriced net_cash drifts
    more than this from the candidate's net_cash, the adjustment is ABORTED as
    stale (re-review) and nothing is mutated. The drift denominator is floored at
    1.0 so a near-zero candidate net_cash can't blow up the comparison.
    """
    action = candidate.get("action")
    if action in _ADVISORY_ACTIONS and action != "inverted":
        return {"ok": False, "action": action,
                "error": f"{action} is advisory-only — place manually",
                "position_id": position.get("position_id")}
    if not _is_open(position):
        return {"ok": False, "action": action, "stale": True,
                "error": "position no longer open",
                "position_id": position.get("position_id")}
    fn = _APPLY.get(action)
    if fn is None:
        return {"ok": False, "action": action, "error": "unknown action",
                "position_id": position.get("position_id")}

    # Stale-price re-check (only when we can reprice from legs)
    if price_leg is not None:
        fresh_net = _reprice_candidate_net(candidate, price_leg)
        if fresh_net is not None:
            old_net = candidate.get("net_cash", 0.0)
            denom = max(1.0, abs(old_net))
            if abs(fresh_net - old_net) / denom > tolerance:
                return {"ok": False, "action": action, "stale": True,
                        "error": f"prices moved (was {old_net}, now {fresh_net}) — re-review",
                        "position_id": position.get("position_id"),
                        "fresh_net_cash": fresh_net}
            # within tolerance: refresh the candidate economics to the live numbers
            candidate = {**candidate, "net_cash": fresh_net}
    return fn(db_path, position, candidate, broker=broker)
