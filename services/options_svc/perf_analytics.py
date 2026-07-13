"""PURE performance analytics over a paper book's positions (no I/O, no engine import).

Three views, each a pure transform of the positions list
(``paper_account_db.fetch_all_positions``) + light meta:

* ``equity_curve`` — daily realized P&L + cumulative realized equity, bucketed by the
  closed positions' exit date. The "know thyself" time series the forensic driver review
  had to reconstruct by hand.
* ``posture_postmortem`` — group closed DRIVER positions by whether they were opened WITH
  vs AGAINST the directional posture recorded in ``entry_context`` (a CCS in an up tape is
  "against"), answering *does the decider win more when it trades with the tape?*
* ``excursion_stats`` — MAE/MFE aggregates (avg worst drawdown, avg peak profit, and
  MFE-capture = how much of the peak favorable move was realized), the evidence for
  tuning profit targets and stops from the book's own history.

Everything is defensive: sparse/None rows, a missing/invalid ``entry_context``, and a
missing ``mae``/``mfe`` are tolerated; never raises.
"""
import json


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _is_closed(p):
    return (p or {}).get("status") in ("CLOSED", "EXPIRED")


def _parse_context(p):
    """The position's entry_context as a dict (parses a JSON string), else {}."""
    ctx = (p or {}).get("entry_context")
    if isinstance(ctx, dict):
        return ctx
    if isinstance(ctx, str) and ctx:
        try:
            out = json.loads(ctx)
            return out if isinstance(out, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


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


def posture_stance(strategy, posture):
    """Whether a credit spread's SIDE agrees with the directional posture.

    ``with`` = the spread profits when the tape continues (PCS in an up tape / CCS in a
    down tape); ``against`` = the wrong side (CCS in up / PCS in down — the loss pattern
    the directional gate targets); ``neutral`` = an IC, a missing/neutral posture, or an
    unrecognized structure. Pure; mirrors ``driver_svc.guardrails._side_blocked``.
    """
    s = str(strategy or "").strip().upper()
    p = str(posture or "").strip().lower()
    if p not in ("up", "down") or s not in ("CCS", "PCS"):
        return "neutral"
    if (s == "CCS" and p == "up") or (s == "PCS" and p == "down"):
        return "against"
    return "with"


def _stance_bucket():
    return {"trades": 0, "wins": 0, "realized": 0.0}


def posture_postmortem(positions):
    """Group CLOSED positions by WITH/AGAINST/neutral posture stance → outcome stats.

    Returns ``{by_stance: {stance: {trades, wins, win_rate, realized, avg}}, edge: {...}}``
    where ``edge`` compares WITH vs AGAINST (avg P&L + win-rate delta) — the headline
    answer to whether trading with the tape pays. Only positions carrying a recorded
    ``entry_context.posture`` (i.e. driver opens) contribute; the rest are ignored.
    """
    buckets = {"with": _stance_bucket(), "against": _stance_bucket(), "neutral": _stance_bucket()}
    for p in positions or []:
        if not isinstance(p, dict) or not _is_closed(p):
            continue
        ctx = _parse_context(p)
        posture = ctx.get("posture")
        if not posture:
            continue   # no recorded entry regime → can't attribute a stance
        r = _num(p.get("realized_pnl"))
        if r is None:
            continue
        stance = posture_stance(p.get("strategy"), posture)
        b = buckets[stance]
        b["trades"] += 1
        b["wins"] += 1 if r > 0 else 0
        b["realized"] = round(b["realized"] + r, 2)

    by_stance = {}
    for stance, b in buckets.items():
        n = b["trades"]
        by_stance[stance] = {
            "trades": n, "wins": b["wins"],
            "win_rate": round(b["wins"] / n, 4) if n else 0.0,
            "realized": round(b["realized"], 2),
            "avg": round(b["realized"] / n, 2) if n else 0.0,
        }
    w, a = by_stance["with"], by_stance["against"]
    edge = {
        "with_avg": w["avg"], "against_avg": a["avg"],
        "avg_delta": round(w["avg"] - a["avg"], 2),
        "with_win_rate": w["win_rate"], "against_win_rate": a["win_rate"],
        "n_with": w["trades"], "n_against": a["trades"],
    }
    return {"by_stance": by_stance, "edge": edge}


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
    """Bundle the three views into one payload for the /driver analytics section."""
    return {
        "equity_curve": equity_curve(positions, starting_balance),
        "postmortem": posture_postmortem(positions),
        "excursions": excursion_stats(positions),
    }
