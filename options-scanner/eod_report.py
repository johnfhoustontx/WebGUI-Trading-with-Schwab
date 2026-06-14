"""Generate EOD signal report. CLI: python eod_report.py [--date YYYY-MM-DD]"""
import argparse
import json
import logging
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import signal_db
import signal_repricer
import signal_recommender
import trade_performance_db
import daily_trade_log

log = logging.getLogger("eod_report")
TZ = ZoneInfo("America/Chicago")
PROJECT_ROOT = Path(__file__).parent
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "data" / "reports"

TOKEN_FILE = Path.home() / ".schwab-mcp" / "token.json"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import SCHWAB_PROXY, APPSETTINGS

if str(SCHWAB_PROXY) not in sys.path:
    sys.path.insert(0, str(SCHWAB_PROXY))


def _create_schwab_client():
    """Initialize Schwab client — proxy if available, else schwab-py direct."""
    try:
        from proxy_client import SchwabPyProxyClient, proxy_available
        if proxy_available():
            return SchwabPyProxyClient()
    except ImportError:
        pass

    from schwab.auth import client_from_token_file
    with open(APPSETTINGS) as f:
        cfg = json.load(f)["Schwab"]
    return client_from_token_file(str(TOKEN_FILE), cfg["AppKey"], cfg["AppSecret"])


def _get_underlying_close(symbol, client):
    """Fetch current underlying price. Tests monkeypatch this."""
    if client is None:
        return None
    try:
        r = client.get_quote(symbol)
        q = r.json().get(symbol, {}).get("quote", {})
        return q.get("lastPrice") or q.get("closePrice")
    except Exception:
        return None


def _close_0dte(signal, client, now, db_path):
    settle = _get_underlying_close(signal["symbol"], client)
    if settle is None:
        return None  # can't close yet
    # signal_repricer.intrinsic_value expects 'entry_credit' which is already the key in the DB row.
    value, pnl = signal_repricer.intrinsic_value(signal, settle)
    outcome = {
        "signal_id": signal["signal_id"],
        "close_ts": now.isoformat(),
        "close_date": now.date().isoformat(),
        "exit_value": value,
        "realized_pnl": pnl,
        "exit_reason": "EXPIRED",
        "settlement_underlying": settle,
    }
    signal_db.insert_outcome(outcome, new_status="EXPIRED", db_path=db_path)
    return outcome


def _close_0dte_cached(signal, settle, now, db_path):
    """Close a 0-DTE signal using a pre-fetched settlement price."""
    if settle is None:
        return None
    value, pnl = signal_repricer.intrinsic_value(signal, settle)
    outcome = {
        "signal_id": signal["signal_id"],
        "close_ts": now.isoformat(),
        "close_date": now.date().isoformat(),
        "exit_value": value,
        "realized_pnl": pnl,
        "exit_reason": "EXPIRED",
        "settlement_underlying": settle,
    }
    signal_db.insert_outcome(outcome, new_status="EXPIRED", db_path=db_path)
    return outcome


def _mark_and_recommend_swing(signal, client, now, db_path):
    r = signal_repricer.reprice_swing(signal, client)

    # Success path: delegate to the shared mark builder so EOD writes the
    # same structure (with current_score, drift, recommendation) that the
    # dashboard's "Refresh marks now" does. iv_data/technicals are not
    # readily available here — passing None degrades the IV-Rank factor
    # gracefully (calc_composite_score handles it).
    if r["error"] is None:
        mark = signal_recommender.build_mark(signal, r, now)
        if mark is None:
            # Repricer said OK but builder rejected (defensive). Fall
            # through to the legacy fail-path so we still log a mark row.
            r["error"] = "build_mark returned None"

    if r["error"] is not None:
        rec = {"action": "HOLD", "reason": "repricing failed"}
        mark = {
            "signal_id": signal["signal_id"],
            "mark_ts": now.isoformat(),
            "mark_date": now.date().isoformat(),
            "current_value": r["current_value"],
            "unrealized_pnl": r["unrealized_pnl"],
            "pnl_pct_of_credit": r["pnl_pct_of_credit"],
            "current_underlying": r["current_underlying"],
            "current_short_delta": r["current_short_delta"],
            "current_score": None,
            "score_drift": None,
            "recommendation": rec["action"],
            "recommendation_reason": rec["reason"],
        }

    signal_db.insert_mark(mark, db_path=db_path)

    rec_action = mark["recommendation"]
    if rec_action in ("TAKE_PROFIT", "CUT"):
        outcome = {
            "signal_id": signal["signal_id"],
            "close_ts": now.isoformat(),
            "close_date": now.date().isoformat(),
            "exit_value": r["current_value"],
            "realized_pnl": r["unrealized_pnl"],
            "exit_reason": mark.get("recommendation_code") or rec_action,
            "settlement_underlying": None,
        }
        signal_db.insert_outcome(outcome, new_status="CLOSED", db_path=db_path)
    return mark, {"action": rec_action,
                  "reason": mark["recommendation_reason"]}


def generate(date_str=None, db_path=signal_db.DEFAULT_DB_PATH,
             reports_dir=DEFAULT_REPORTS_DIR, client=None,
             perf_db_path=trade_performance_db.DEFAULT_DB_PATH):
    now = datetime.now(TZ)
    if date_str is None:
        date_str = now.date().isoformat()

    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    signal_repricer.clear_chain_cache()

    # 1. Close today's 0-DTE signals
    open_0dte = signal_db.get_open_signals(scanner_type="0DTE", db_path=db_path)
    todays_0dte = [s for s in open_0dte if s["expiration"] == date_str]
    log.info(f"0-DTE to close: {len(todays_0dte)}")
    # Cache underlying prices to avoid redundant quote calls. The settlement
    # close writes a signal_outcomes row; the report's "Signals Closed Today"
    # section reads those outcomes back, alongside the day's intraday closes.
    underlying_cache = {}
    for sig in todays_0dte:
        sym = sig["symbol"]
        if sym not in underlying_cache:
            underlying_cache[sym] = _get_underlying_close(sym, client)
            log.info(f"  Fetched {sym} close: {underlying_cache[sym]}")
        _close_0dte_cached(sig, underlying_cache[sym], now, db_path)

    # 2. Mark + recommend open swings
    open_swings = signal_db.get_open_signals(scanner_type="SWING", db_path=db_path)
    # Count unique chains needed
    unique_chains = set((s["symbol"], s["expiration"]) for s in open_swings)
    log.info(f"Swing signals to reprice: {len(open_swings)} ({len(unique_chains)} unique chains)")
    swing_reports = []
    for i, sig in enumerate(open_swings, 1):
        if i % 25 == 0 or i == 1:
            log.info(f"  Repricing {i}/{len(open_swings)}...")
        mark, rec = _mark_and_recommend_swing(sig, client, now, db_path)
        swing_reports.append({"signal": sig, "mark": mark, "rec": rec})

    # Potential-trade journal: settle prior days off today's close, then render
    # the review section. Best-effort — a failure here must not lose the report.
    try:
        potential_md = daily_trade_log.settle_and_render(date_str)
    except Exception as e:
        log.warning(f"daily_trade_log review failed: {e}")
        potential_md = []

    md = _render_markdown(date_str, swing_reports, db_path, perf_db_path,
                          extra_md_lines=potential_md)
    out_path = reports_dir / f"{date_str}-eod-report.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path


def _render_markdown(date_str, swing_reports, db_path, perf_db_path,
                     extra_md_lines=None):
    lines = [f"# EOD Signal Report - {date_str}", ""]

    # Summary first
    lines.append("## 1. Statistical Performance")
    lines.extend(_stats_section(date_str, db_path))

    lines.append("## 2. Hypothetical PnL")
    lines.append("")
    lines.extend(_closed_today_section(date_str, db_path))

    lines.append("### Swing Signals - Active")
    active = [s for s in swing_reports if s["rec"]["action"] == "HOLD"]
    if active:
        lines.append("| Signal | Sym | Strat | Strikes | Exp | Cr | Mark | PnL $ | Rec |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for s in active:
            sig, m, r = s["signal"], s["mark"], s["rec"]
            strikes = f"{sig['short_strike']}/{sig['long_strike']}"
            mark_str = f"{m['current_value']:.2f}" if m['current_value'] is not None else "-"
            pnl_str = f"{m['unrealized_pnl']:+.2f}" if m['unrealized_pnl'] is not None else "-"
            lines.append(f"| {sig['signal_id']} | {sig['symbol']} | {sig['strategy']} | "
                         f"{strikes} | {sig['expiration']} | {sig['entry_credit']:.2f} | "
                         f"{mark_str} | {pnl_str} | {r['action']} |")
    else:
        lines.append("_No active swing signals._")
    lines.append("")

    lines.append("## 3. Swing Recommendations")
    for action in ("TAKE_PROFIT", "CUT", "HOLD"):
        bucket = [s for s in swing_reports if s["rec"]["action"] == action]
        lines.append(f"### {action} ({len(bucket)})")
        for s in bucket:
            sig, r = s["signal"], s["rec"]
            lines.append(f"- **{sig['signal_id']}** {sig['symbol']} {sig['strategy']} "
                         f"{sig['short_strike']}/{sig['long_strike']} exp {sig['expiration']} "
                         f"- {r['reason']}")
        lines.append("")

    lines.append("## 4. Streaming Lifecycle (paper trades)")
    lines.extend(_streaming_section(date_str, perf_db_path))

    if extra_md_lines:
        lines.append("")
        lines.extend(extra_md_lines)

    return "\n".join(lines) + "\n"


def _closed_today_section(date_str, db_path):
    """Every signal closed today — intraday TP/CUT auto-closes AND the report's
    own settlement closes — sourced from signal_outcomes (the frozen close
    price; closed trades are never repriced again). Subtotal matches the
    "Closed today" stat in section 1 because both read signal_outcomes.
    """
    conn = signal_db.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT o.signal_id, o.exit_value, o.realized_pnl, o.exit_reason,
                      s.scanner_type, s.symbol, s.strategy,
                      s.short_strike, s.long_strike, s.entry_credit, s.entry_score
               FROM signal_outcomes o
               LEFT JOIN signals s ON s.signal_id = o.signal_id
               WHERE o.close_date = ?
               ORDER BY o.close_ts""",
            (date_str,),
        ).fetchall()
    finally:
        conn.close()

    lines = ["### Signals Closed Today"]
    if not rows:
        lines.append("_No signals closed today._")
        lines.append("")
        return lines

    def _num(v, fmt):
        return format(v, fmt) if v is not None else "-"

    lines.append("| Signal | Type | Sym | Strat | Strikes | Cr | Exit | PnL $ | Reason | Score |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        strikes = f"{r['short_strike']}/{r['long_strike']}"
        lines.append(
            f"| {r['signal_id']} | {r['scanner_type'] or '-'} | {r['symbol'] or '-'} | "
            f"{r['strategy'] or '-'} | {strikes} | {_num(r['entry_credit'], '.2f')} | "
            f"{_num(r['exit_value'], '.2f')} | {_num(r['realized_pnl'], '+.2f')} | "
            f"{r['exit_reason'] or '-'} | {r['entry_score'] if r['entry_score'] is not None else '-'} |"
        )
    pnls = [r["realized_pnl"] for r in rows if r["realized_pnl"] is not None]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p <= 0)
    total = sum(pnls)
    wr = wins / len(rows) * 100 if rows else 0
    lines.append("")
    lines.append(f"**Subtotal:** {len(rows)} trades, {wins}W / {losses}L, "
                 f"win rate {wr:.1f}%, total PnL ${total:+.2f}")
    lines.append("")
    return lines


def _streaming_section(date_str, perf_db_path):
    """Aggregate streaming lifecycle events for the date. Distinct from the
    signal-outcome sections: this reports paper-trade (trade_id) events."""
    if not Path(perf_db_path).exists():
        return ["_No streaming performance data._", ""]
    events = trade_performance_db.events_on_date(perf_db_path, date_str)
    if not events:
        return ["_No streaming events recorded today._", ""]
    buckets = {}
    for e in events:
        buckets.setdefault(e["event_type"], []).append(e)
    lines = [
        f"- Target hit: {len(buckets.get('target_hit', []))}, "
        f"Stop hit: {len(buckets.get('stop_hit', []))}, "
        f"Short-strike tested: {len(buckets.get('strike_test', []))}",
    ]
    mins = []
    for e in buckets.get("target_hit", []):
        ent = trade_performance_db.entry_ts(perf_db_path, e["trade_id"])
        if ent:
            try:
                mins.append((datetime.fromisoformat(e["ts"])
                             - datetime.fromisoformat(ent)).total_seconds() / 60.0)
            except ValueError:
                pass
    if mins:
        lines.append(f"- Avg time-to-target: {sum(mins)/len(mins):.1f} min "
                     f"(fastest {min(mins):.1f}, slowest {max(mins):.1f})")
    lines.append("")
    return lines


def _stats_section(date_str, db_path):
    conn = signal_db.connect(db_path)
    try:
        # Today
        cur = conn.execute("SELECT scanner_type, COUNT(*) FROM signals WHERE first_seen_date=? GROUP BY scanner_type", (date_str,))
        captured = dict(cur.fetchall())

        cur = conn.execute("""SELECT COUNT(*), SUM(realized_pnl),
            SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)
            FROM signal_outcomes WHERE close_date=?""", (date_str,))
        closed_n, total_pnl, wins = cur.fetchone()
        closed_n = closed_n or 0
        total_pnl = total_pnl or 0.0
        wins = wins or 0
        wr = (wins / closed_n * 100) if closed_n else 0.0

        lines = [
            "### Today",
            f"- Signals captured: 0-DTE={captured.get('0DTE',0)}, SWING={captured.get('SWING',0)}",
            f"- Closed today: {closed_n}, wins={wins}, win rate={wr:.1f}%, PnL=${total_pnl:+.2f}",
            "",
        ]

        # Rolling 7-day
        start_date = (date.fromisoformat(date_str) - timedelta(days=6)).isoformat()
        cur = conn.execute("""SELECT COUNT(*), SUM(realized_pnl),
            SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)
            FROM signal_outcomes WHERE close_date BETWEEN ? AND ?""", (start_date, date_str))
        r7_n, r7_pnl, r7_wins = cur.fetchone()
        r7_n = r7_n or 0
        r7_pnl = r7_pnl or 0.0
        r7_wins = r7_wins or 0
        r7_wr = (r7_wins / r7_n * 100) if r7_n else 0.0
        lines.extend([
            "### Rolling 7-Day",
            f"- Closed: {r7_n}, wins={r7_wins}, win rate={r7_wr:.1f}%, PnL=${r7_pnl:+.2f}",
            "",
        ])

        # All-time
        cur = conn.execute("SELECT COUNT(*), SUM(realized_pnl), SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) FROM signal_outcomes")
        at_n, at_pnl, at_wins = cur.fetchone()
        at_n = at_n or 0
        at_pnl = at_pnl or 0.0
        at_wins = at_wins or 0
        at_wr = (at_wins / at_n * 100) if at_n else 0.0
        lines.extend([
            "### All-Time",
            f"- Closed: {at_n}, wins={at_wins}, win rate={at_wr:.1f}%, PnL=${at_pnl:+.2f}",
            "",
        ])

        # By strategy
        cur = conn.execute("""SELECT s.strategy, COUNT(*), SUM(o.realized_pnl),
            SUM(CASE WHEN o.realized_pnl>0 THEN 1 ELSE 0 END)
            FROM signal_outcomes o JOIN signals s ON s.signal_id = o.signal_id
            GROUP BY s.strategy""")
        strat_rows = cur.fetchall()
        if strat_rows:
            lines.append("**By strategy:**")
            for strat, n, pnl, w in strat_rows:
                pnl = pnl or 0.0
                w = w or 0
                lines.append(f"- {strat}: {n} trades, {w} wins, ${pnl:+.2f}")
            lines.append("")

        # By grade
        cur = conn.execute("""SELECT s.entry_grade, COUNT(*), SUM(o.realized_pnl),
            SUM(CASE WHEN o.realized_pnl>0 THEN 1 ELSE 0 END)
            FROM signal_outcomes o JOIN signals s ON s.signal_id = o.signal_id
            GROUP BY s.entry_grade""")
        grade_rows = cur.fetchall()
        if grade_rows:
            lines.append("**By grade:**")
            for grade, n, pnl, w in grade_rows:
                pnl = pnl or 0.0
                w = w or 0
                lines.append(f"- {grade}: {n} trades, {w} wins, ${pnl:+.2f}")
            lines.append("")

        # By capture date (cohort) — day-by-day breakdown of all closed trades
        # grouped by the date the signal was captured (first_seen_date). Lets a
        # post-criteria-change cohort be tracked separately from legacy positions
        # still bleeding off (swings stay open up to 15 DTE).
        cur = conn.execute("""SELECT s.first_seen_date, COUNT(*), SUM(o.realized_pnl),
            SUM(CASE WHEN o.realized_pnl>0 THEN 1 ELSE 0 END)
            FROM signal_outcomes o JOIN signals s ON s.signal_id = o.signal_id
            WHERE s.first_seen_date IS NOT NULL
            GROUP BY s.first_seen_date ORDER BY s.first_seen_date""")
        cohort_rows = cur.fetchall()
        if cohort_rows:
            lines.append("**By capture date (cohort):**")
            for cap_date, n, pnl, w in cohort_rows:
                pnl = pnl or 0.0
                w = w or 0
                wr = (w / n * 100) if n else 0.0
                lines.append(f"- {cap_date}: {n} trades, {w} wins ({wr:.0f}%), ${pnl:+.2f}")
            lines.append("")

        return lines
    finally:
        conn.close()


def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    # Lazy-construct client to keep tests client-free
    try:
        client = _create_schwab_client()
    except Exception as e:
        log.warning(f"no schwab client available: {e}")
        client = None

    path = generate(date_str=args.date, client=client)
    print(f"Report written to {path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _cli()
