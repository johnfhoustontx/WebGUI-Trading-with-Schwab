"""OFFLINE backtest for the directional wrong-side gate — NEVER a request path.

Replays the driver's REAL closed trades (``paper_account_driver.db``) against the
proposed gate: for each trade, reconstruct the broad-index ($SPX) spot-trend at entry
from ``gex_history.db``, derive an ``up``/``down``/``neutral`` posture, and tally the $ of
the CCS loss bucket the gate would have blocked (saved) vs winners forgone. The gate is
enabled live (``settings.DIRECTIONAL_GATE_ENABLED``) only if this shows a net-positive,
loss-bucket-blocking result.

Run:  python services/driver_svc/validate_directional_gate.py
"""
import sys
import sqlite3
import datetime as dt
import pathlib

# Resolve the repo root from THIS file, never a literal: a hard-coded D:\ path
# makes an offline script run against whichever checkout it names rather than
# the one it lives in - so run from prod or a worktree it would silently read
# the dev checkout's repo_paths (and therefore the dev DBs and ports).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from repo_paths import DRIVER_PAPER_DB, OPTIONS_SCANNER  # noqa: E402
from services.driver_svc.guardrails import _side_blocked  # reuse the LIVE block rule  # noqa: E402

_GEX_DB = str(OPTIONS_SCANNER / "gex_history.db")


# ── pure pieces (unit-tested) ────────────────────────────────────────────────
def trend_to_posture(trend_pct, threshold) -> str:
    """A broad-index trend % → posture. up/down when |trend| ≥ threshold, else neutral."""
    if trend_pct is None:
        return "neutral"
    if trend_pct >= threshold:
        return "up"
    if trend_pct <= -threshold:
        return "down"
    return "neutral"


def tally(rows) -> dict:
    """Aggregate the gate's impact. ``rows`` = [{strategy, pnl, posture, blocked}].

    Returns blocked/kept counts, $ saved (blocked losers), $ forgone (blocked winners),
    the net $ impact (saved − forgone), and the surviving (kept) book's realized P&L +
    win rate.
    """
    saved = forgone = 0.0
    blocked = kept = 0
    kept_pnls = []
    for r in rows:
        if r["blocked"]:
            blocked += 1
            if r["pnl"] < 0:
                saved += -r["pnl"]          # a blocked loser = money saved
            else:
                forgone += r["pnl"]          # a blocked winner = money forgone
        else:
            kept += 1
            kept_pnls.append(r["pnl"])
    kept_wins = sum(1 for p in kept_pnls if p > 0)
    return {
        "blocked": blocked, "kept": kept,
        "saved": saved, "forgone": forgone, "net_impact": saved - forgone,
        "kept_realized": sum(kept_pnls),
        "kept_win_rate": (kept_wins / len(kept_pnls)) if kept_pnls else 0.0,
    }


# ── I/O: reconstruct SPX spot-trend at a moment from gex_history ─────────────
def _spx_spot_at_or_before(con, posix_ts):
    row = con.execute(
        "SELECT spot FROM snapshots WHERE symbol='$SPX' AND view='gex' AND spot IS NOT NULL "
        "AND ts <= ? ORDER BY ts DESC LIMIT 1", (posix_ts,)).fetchone()
    return row[0] if row else None


def _entry_trend_pct(con, entry_iso, lookback_hours):
    """SPX % change from ``lookback_hours`` before entry to entry (None if uncovered)."""
    try:
        ent = dt.datetime.fromisoformat(str(entry_iso))
    except (TypeError, ValueError):
        return None
    ent_ts = ent.timestamp()
    spot_now = _spx_spot_at_or_before(con, ent_ts)
    spot_prev = _spx_spot_at_or_before(con, ent_ts - lookback_hours * 3600)
    if not spot_now or not spot_prev or spot_prev == 0:
        return None
    return (spot_now - spot_prev) / spot_prev * 100.0


def _load_closed():
    con = sqlite3.connect(str(DRIVER_PAPER_DB))
    con.row_factory = sqlite3.Row
    q = con.execute("SELECT symbol, strategy, entry_ts, exit_reason, realized_pnl "
                    "FROM paper_positions WHERE status!='OPEN'")
    rows = [dict(r) for r in q.fetchall()]
    con.close()
    return rows


def run(lookback_hours=30.0, threshold=0.3):
    closed = _load_closed()
    gex = sqlite3.connect(_GEX_DB)
    rows, uncovered = [], 0
    for t in closed:
        trend = _entry_trend_pct(gex, t.get("entry_ts"), lookback_hours)
        if trend is None:
            uncovered += 1
        posture = trend_to_posture(trend, threshold)
        blocked = _side_blocked({"type": t.get("strategy")}, posture)
        rows.append({"strategy": t.get("strategy"),
                     "pnl": float(t.get("realized_pnl") or 0.0),
                     "posture": posture, "blocked": blocked, "trend": trend,
                     "symbol": t.get("symbol"), "reason": t.get("exit_reason")})
    gex.close()
    agg = tally(rows)

    total_pnl = sum(r["pnl"] for r in rows)
    ccs_loss = -sum(r["pnl"] for r in rows if r["strategy"] == "CCS" and r["pnl"] < 0)
    print("=" * 70)
    print(f"DIRECTIONAL-GATE BACKTEST  (lookback={lookback_hours}h, threshold={threshold}%)")
    print("=" * 70)
    print(f"  closed trades       : {len(rows)}   (uncovered by gex history: {uncovered})")
    print(f"  actual realized P&L : ${total_pnl:,.2f}   (CCS loss bucket: -${ccs_loss:,.2f})")
    print(f"  gate blocked        : {agg['blocked']}   kept: {agg['kept']}")
    print(f"  $ SAVED (blocked losers)   : ${agg['saved']:,.2f}")
    print(f"  $ forgone (blocked winners): ${agg['forgone']:,.2f}")
    print(f"  NET IMPACT (saved-forgone) : ${agg['net_impact']:+,.2f}")
    print(f"  surviving book realized    : ${agg['kept_realized']:,.2f}  "
          f"win rate {agg['kept_win_rate']*100:.0f}%  (was {total_pnl:,.0f} / see forensics)")
    ccs_blocked_loss = sum(-r["pnl"] for r in rows
                           if r["strategy"] == "CCS" and r["pnl"] < 0 and r["blocked"])
    print(f"  CCS loss bucket BLOCKED    : ${ccs_blocked_loss:,.2f} of ${ccs_loss:,.2f} "
          f"({(ccs_blocked_loss/ccs_loss*100 if ccs_loss else 0):.0f}%)")
    print("\n  per-trade:")
    for r in sorted(rows, key=lambda x: (x["strategy"] or "", x["pnl"])):
        mark = "BLOCK" if r["blocked"] else "keep "
        tr = f"{r['trend']:+.2f}%" if r["trend"] is not None else "  n/a "
        print(f"    [{mark}] {str(r['symbol']):6} {str(r['strategy']):4} "
              f"trend={tr} posture={r['posture']:7} pnl=${r['pnl']:+.0f}  {r['reason']}")
    return agg


if __name__ == "__main__":
    for lb in (24.0, 30.0, 48.0):
        run(lookback_hours=lb, threshold=0.3)
        print()
