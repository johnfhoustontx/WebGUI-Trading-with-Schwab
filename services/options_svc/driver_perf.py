"""PURE performance scorecard over the driver paper account's positions.

No I/O, no engine import — given the positions list (paper_account_db.fetch_all_positions)
and the account snapshot, it computes the 'how good is the autonomous module' metrics
the /driver page renders. Defensive: sparse/None rows are tolerated, never raise."""


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _is_closed(p):
    return (p or {}).get("status") in ("CLOSED", "EXPIRED")


def build_scorecard(positions, snapshot) -> dict:
    positions = [p for p in (positions or []) if isinstance(p, dict)]
    snap = snapshot or {}
    closed = [p for p in positions if _is_closed(p)]
    open_ = [p for p in positions if not _is_closed(p)]
    realized = [r for r in (_num(p.get("realized_pnl")) for p in closed) if r is not None]
    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    sum_w, sum_l = round(sum(wins), 2), round(sum(losses), 2)
    pf = round(sum_w / abs(sum_l), 2) if sum_l else None  # None = no losses yet; 0.0 = only losses
    # best/worst drawn from the SAME None-excluded population as the other metrics
    # (a closed row with no realized_pnl must not be reported as best/worst).
    priced = [p for p in closed if _num(p.get("realized_pnl")) is not None]
    best = max(priced, key=lambda p: p["realized_pnl"], default=None)
    worst = min(priced, key=lambda p: p["realized_pnl"], default=None)
    open_unreal = _num(snap.get("open_unrealized")) or 0.0
    return {
        "total_trades": len(positions),
        "open": len(open_), "closed": len(closed),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 4) if closed else 0.0,
        "realized_pnl": round(sum(realized), 2),
        "open_unrealized": round(open_unreal, 2),
        "total_pnl": round(sum(realized) + open_unreal, 2),
        "session_pnl": _num(snap.get("session_pnl")),
        "avg_win": round(sum_w / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum_l / len(losses), 2) if losses else 0.0,
        "profit_factor": pf,
        "best": best, "worst": worst,
        "by_symbol": _group(closed, "symbol"),
        "by_strategy": _group(closed, "strategy"),
    }


def _group(closed, key):
    buckets = {}
    for p in closed:
        k = p.get(key) or "?"
        b = buckets.setdefault(k, {"trades": 0, "wins": 0, "pnl": 0.0})
        r = _num(p.get("realized_pnl")) or 0.0
        b["trades"] += 1
        b["wins"] += 1 if r > 0 else 0
        b["pnl"] = round(b["pnl"] + r, 2)
    out = [{key: k, "trades": b["trades"], "pnl": b["pnl"],
            "win_rate": round(b["wins"] / b["trades"], 4) if b["trades"] else 0.0}
           for k, b in buckets.items()]
    return sorted(out, key=lambda r: r["pnl"], reverse=True)
