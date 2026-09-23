"""PURE performance analytics over a paper book's positions (no I/O, no engine import).

Two views, each a pure transform of the positions list
(``paper_account_db.fetch_all_positions``) + light meta:

* ``equity_curve`` — daily realized P&L + cumulative realized equity, bucketed by the
  closed positions' exit date.
* ``excursion_stats`` — MAE/MFE aggregates (avg worst drawdown, avg peak profit, and
  MFE-capture = how much of the peak favorable move was realized), the evidence for
  tuning profit targets and stops from the book's own history.

Everything is defensive: sparse/None rows and a missing ``mae``/``mfe`` are tolerated;
never raises.
"""


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _is_closed(p):
    return (p or {}).get("status") in ("CLOSED", "EXPIRED")


def _exit_date(p):
    return str((p or {}).get("exit_ts") or "")[:10] or None


def equity_curve(positions, starting_balance=25000.0):
    """Daily realized P&L + cumulative realized equity from CLOSED positions.

    Returns a chronological list of ``{date, realized, cum_realized, equity, trades}`` —
    one point per distinct exit date. ``equity`` = ``starting_balance + cum_realized`` (a
    realized-equity curve; open unrealized is deliberately excluded so the line is the
    banked result). Empty list when nothing has closed.
    """
    buckets = {}
    for p in positions or []:
        if not isinstance(p, dict) or not _is_closed(p):
            continue
        d = _exit_date(p)
        r = _num(p.get("realized_pnl"))
        if d is None or r is None:
            continue
        b = buckets.setdefault(d, {"realized": 0.0, "trades": 0})
        b["realized"] += r
        b["trades"] += 1
    out, cum = [], 0.0
    for d in sorted(buckets):
        cum += buckets[d]["realized"]
        out.append({
            "date": d,
            "realized": round(buckets[d]["realized"], 2),
            "cum_realized": round(cum, 2),
            "equity": round(starting_balance + cum, 2),
            "trades": buckets[d]["trades"],
        })
    return out


def excursion_stats(positions):
    """MAE/MFE aggregates over CLOSED positions that recorded excursions.

    Returns ``{n, avg_mae, avg_mfe, avg_realized, mfe_capture, avg_mae_on_winners}``:
    * ``avg_mae`` — average worst drawdown ($, negative) reached before close,
    * ``avg_mfe`` — average peak paper profit ($) reached,
    * ``mfe_capture`` — mean ``realized / mfe`` over trades whose peak was profitable
      (``mfe > 0``): near 1.0 = targets capture the move, low = leaving profit on the
      table,
    * ``avg_mae_on_winners`` — how deep winners dipped first (stop-placement signal).
    ``n`` is the count of contributing (mae/mfe present) closed positions; zeros when none.
    """
    closed = [p for p in (positions or [])
              if isinstance(p, dict) and _is_closed(p)
              and _num(p.get("mae")) is not None and _num(p.get("mfe")) is not None]
    if not closed:
        return {"n": 0, "avg_mae": 0.0, "avg_mfe": 0.0, "avg_realized": 0.0,
                "mfe_capture": None, "avg_mae_on_winners": None}
    maes = [p["mae"] for p in closed]
    mfes = [p["mfe"] for p in closed]
    reals = [_num(p.get("realized_pnl")) or 0.0 for p in closed]
    captures = [(_num(p.get("realized_pnl")) or 0.0) / p["mfe"]
                for p in closed if p["mfe"] > 0]
    winners_mae = [p["mae"] for p in closed if (_num(p.get("realized_pnl")) or 0.0) > 0]
    return {
        "n": len(closed),
        "avg_mae": round(sum(maes) / len(maes), 2),
        "avg_mfe": round(sum(mfes) / len(mfes), 2),
        "avg_realized": round(sum(reals) / len(reals), 2),
        "mfe_capture": round(sum(captures) / len(captures), 3) if captures else None,
        "avg_mae_on_winners": round(sum(winners_mae) / len(winners_mae), 2) if winners_mae else None,
    }


def build_analytics(positions, *, starting_balance=25000.0):
    """Bundle the two views into one payload for the Paper Account's analytics."""
    return {
        "equity_curve": equity_curve(positions, starting_balance),
        "excursions": excursion_stats(positions),
    }
