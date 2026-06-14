"""
approval_server.py - FastAPI approval gateway: notifies John, waits for APPROVE/SKIP
Version: 1.0.0
Last Updated: 2026-05-23

Version 1.0.0 Changes:
- Initial implementation
- Receives pending trade from morning_agent
- Displays formatted trade sheet in browser (localhost:8300)
- Exposes /approve and /skip endpoints
- Times out after APPROVAL_TIMEOUT_MIN and auto-skips
- Calls order_executor on approval
"""

import json
import logging
import os
import threading
import time
import webbrowser
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from config import (
    APPROVAL_HOST,
    APPROVAL_PORT,
    APPROVAL_TIMEOUT_MIN,
    PENDING_TRADE,
    LOG_DIR,
)
from perf_report import build_report

#############################################
# LOGGING
#############################################

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, f"approval_{datetime.today().date()}.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

#############################################
# APP STATE
#############################################

app = FastAPI(title="Claude is the Driver — Approval Server")

state = {
    "pending":    None,     # Current pending trade payload
    "decision":   None,     # 'approved' | 'skipped' | None
    "arrived_at": None,     # datetime when pending trade arrived
    "error":      None,
    "skip_msg":   None,
}

#############################################
# HELPER: FORMAT TRADE SHEET
#############################################

def _order_line_html(t: dict) -> str:
    pv = t.get("preview")
    if not pv:
        return ""
    inst = t.get("instrument", "")
    side = pv.get("side", "")
    qty = pv.get("quantity")
    if qty is None:
        return '<div style="font-size:13px;color:#E24B4A">Could not size — no live price</div>'
    parts = [f"<b>{side} {qty} {inst}</b>"]
    if pv.get("est_entry") is not None:
        parts.append(f"est. entry ~${pv['est_entry']:.2f}")
    if pv.get("limit") is not None:
        parts.append(f"limit ${pv['limit']:.2f}")
    if pv.get("stop") is not None:
        parts.append(f"stop ${pv['stop']}")
    if pv.get("target") is not None:
        parts.append(f"target ${pv['target']}")
    if pv.get("profit_target_dollars"):
        parts.append(f"profit target ${pv['profit_target_dollars']:.0f}")
    parts.append(f"max risk ${pv.get('max_risk', 0):.0f}")
    body = " &middot; ".join(parts)
    return (f'<div style="font-size:14px;margin-top:8px">{body}</div>'
            f'<div style="font-size:11px;color:#aaa">Estimate — re-priced at fill</div>')


def _format_trade_sheet_html(payload: dict) -> str:
    """Render a clean HTML trade sheet for the browser approval page."""
    grade    = payload.get("grade", "?")
    date_str = payload.get("date", "")
    pnl_today= payload.get("pnl_today", 0)
    pnl_week = payload.get("pnl_week", 0)
    reasons  = payload.get("grade_reasons", [])
    trades   = payload.get("proposed_trades", [])
    cond     = payload.get("conditions", {})

    grade_colors = {"A": "#1D9E75", "B": "#185FA5", "C": "#BA7517", "X": "#E24B4A"}
    g_color = grade_colors.get(grade, "#888")

    def pnl_span(v):
        c = "#1D9E75" if v >= 0 else "#E24B4A"
        sign = "+" if v >= 0 else ""
        return f'<span style="color:{c};font-weight:500">{sign}${v:.2f}</span>'

    reason_html = "".join(f"<li>{r}</li>" for r in reasons)

    trade_cards = ""
    for t in trades:
        bucket    = t.get("bucket","?")
        notes     = t.get("notes","")
        strikes   = t.get("strikes", {})
        structure = t.get("structure","")
        strike_detail = ""
        if strikes:
            strike_detail = "<br>".join(f"<b>{k}:</b> {v}" for k,v in strikes.items() if k != "structure")

        trade_cards += f"""
        <div style="border:1px solid #ddd;border-radius:8px;padding:16px;margin-bottom:12px">
            <div style="font-size:11px;font-weight:600;color:{g_color};text-transform:uppercase;margin-bottom:4px">
                Bucket {bucket}
            </div>
            <div style="font-size:16px;font-weight:500;margin-bottom:4px">{structure.replace('_',' ').title() if structure else t.get('instrument','')}</div>
            <div style="font-size:13px;color:#666;margin-bottom:8px">{notes}</div>
            {f'<div style="font-size:12px;font-family:monospace;color:#444;line-height:1.6">{strike_detail}</div>' if strike_detail else ''}
            {_order_line_html(t)}
        </div>"""

    timeout_min = APPROVAL_TIMEOUT_MIN

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>Trade Approval — {date_str}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 580px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1 {{ font-size: 20px; font-weight: 500; margin-bottom: 4px; }}
  .grade {{ display:inline-block;padding:4px 14px;border-radius:6px;font-weight:600;font-size:18px;color:#fff;background:{g_color};margin-bottom:20px }}
  .meta {{ font-size:13px;color:#888;margin-bottom:20px }}
  .section-label {{ font-size:11px;font-weight:600;text-transform:uppercase;color:#888;margin:20px 0 8px }}
  ul {{ margin:0;padding-left:20px;font-size:13px;color:#444;line-height:1.8 }}
  .btn {{ display:inline-block;padding:12px 32px;border-radius:8px;font-size:15px;font-weight:500;cursor:pointer;border:none;margin-right:12px;margin-top:20px;text-decoration:none }}
  .approve {{ background:#1D9E75;color:#fff }}
  .skip    {{ background:#f0f0f0;color:#444 }}
  .btn:hover {{ opacity:0.88 }}
  .timeout {{ font-size:12px;color:#aaa;margin-top:12px }}
  .pnl-row {{ display:flex;gap:24px;margin-bottom:8px;font-size:13px }}
</style>
</head>
<body>
<h1>Claude is the Driver</h1>
<div style="margin-bottom:8px">Day grade: <span class="grade">{grade}</span></div>
<div class="pnl-row">
  <span>Today: {pnl_span(pnl_today)}</span>
  <span>Week: {pnl_span(pnl_week)}</span>
  <span>VIX: <b>{cond.get('vix','—')}</b></span>
  <span>SPX: <b>{cond.get('spx_spot','—')}</b></span>
</div>

<div class="section-label">Grade rationale</div>
<ul>{reason_html}</ul>

<div class="section-label">Proposed trades ({len(trades)})</div>
{trade_cards}

<div>
  <a class="btn approve" href="/approve">APPROVE</a>
  <a class="btn skip"    href="/skip">SKIP</a>
</div>
<div style="margin-top:16px"><a href="/performance" style="font-size:13px">View performance &rarr;</a></div>
<div class="timeout">Auto-skips in {timeout_min} minutes if no response.</div>

<script>
let secs = {timeout_min * 60};
const el = document.querySelector('.timeout');
const iv = setInterval(() => {{
  secs--;
  const m = Math.floor(secs/60), s = secs%60;
  el.textContent = `Auto-skips in ${{m}}m ${{s}}s if no response.`;
  if (secs <= 0) {{ clearInterval(iv); window.location='/skip'; }}
}}, 1000);
</script>
</body>
</html>"""

def _format_performance_html(rep: dict) -> str:
    s = rep["summary"]
    rows = ""
    for t in rep["trades"]:
        pnl = t["pnl"]
        color = "#1D9E75" if pnl >= 0 else "#E24B4A"
        rows += (f"<tr><td>{t['date']}</td><td>{t['trade_id']}</td>"
                 f"<td>{t['bucket']}</td><td>{t['instrument']}</td>"
                 f"<td>{t['side']}</td><td>{t['status']}</td>"
                 f"<td>{t.get('exit_reason') or '—'}</td>"
                 f"<td>{t['source']}</td>"
                 f"<td style='color:{color}'>${pnl:.2f}</td></tr>")
    buckets = " · ".join(f"{k}: ${v:.2f}" for k, v in s["pnl_by_bucket"].items())
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Performance</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:900px;margin:40px auto;padding:0 20px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #eee}}
.cards{{display:flex;gap:24px;margin:16px 0;font-size:14px}}</style></head><body>
<h1>Performance</h1>
<div class="cards">
  <div>Trades: <b>{s['total_trades']}</b></div>
  <div>Win rate: <b>{s['win_rate']}%</b> ({s['wins']}-{s['losses']})</div>
  <div>Realized P&amp;L: <b>${s['realized_pnl']:.2f}</b></div>
  <div>By bucket: {buckets}</div>
</div>
<table><tr><th>Date</th><th>Trade</th><th>Bkt</th><th>Inst</th><th>Side</th>
<th>Status</th><th>Exit</th><th>Source</th><th>P&amp;L</th></tr>{rows}</table>
<p style="font-size:11px;color:#aaa">Bucket A = streamed (proxy LEVELONE_OPTIONS); B/C = polled.</p>
</body></html>"""

#############################################
# ENDPOINTS
#############################################

@app.post("/pending")
async def receive_pending(payload: dict):
    """Receive a pending trade from morning_agent."""
    state["pending"]    = payload
    state["decision"]   = None
    state["arrived_at"] = datetime.now()
    state["error"]      = None
    state["skip_msg"]   = None
    logger.info(f"Pending trade received — grade {payload.get('grade')}, {len(payload.get('proposed_trades',[]))} trade(s)")
    webbrowser.open(f"http://{APPROVAL_HOST}:{APPROVAL_PORT}/")
    return {"status": "pending received, browser opened"}


@app.post("/notify_skip")
async def notify_skip(payload: dict):
    """Receive a no-trade notification."""
    state["pending"]  = None
    state["decision"] = "skipped"
    state["skip_msg"] = payload.get("reasons", [])
    logger.info(f"No-trade notification: {payload.get('grade')} — {payload.get('reasons')}")
    webbrowser.open(f"http://{APPROVAL_HOST}:{APPROVAL_PORT}/")
    return {"status": "ok"}


@app.post("/notify_error")
async def notify_error(payload: dict):
    state["error"] = payload.get("error")
    logger.error(f"Pipeline error: {state['error']}")
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Browser approval page."""
    if state["error"]:
        return HTMLResponse(f"<h2 style='color:red'>Pipeline error</h2><pre>{state['error']}</pre>")

    if state["decision"] == "approved":
        return HTMLResponse("<h2 style='color:#1D9E75'>✓ Approved — order sent to executor.</h2>")

    if state["decision"] == "skipped":
        reasons = "<br>".join(state.get("skip_msg") or ["Manual skip."])
        return HTMLResponse(f"<h2 style='color:#888'>Skipped for today.</h2><p>{reasons}</p>")

    if not state["pending"]:
        return HTMLResponse("<h2>No pending trade.</h2><p>Morning agent has not run yet, or today is a no-trade day.</p>")

    return HTMLResponse(_format_trade_sheet_html(state["pending"]))


@app.get("/approve", response_class=HTMLResponse)
async def approve():
    """John clicks APPROVE — fire the order executor."""
    if not state["pending"]:
        return HTMLResponse("<p>No pending trade to approve.</p>")

    state["decision"] = "approved"
    payload = state["pending"]
    logger.info("APPROVED — firing order executor")

    # Fire executor in background thread so HTTP response returns immediately
    def _execute():
        try:
            from order_executor import execute_trades
            results = execute_trades(payload["proposed_trades"])
            logger.info(f"Execution results: {results}")
        except Exception as exc:
            logger.error(f"Order execution failed: {exc}", exc_info=True)

    threading.Thread(target=_execute, daemon=True).start()
    return HTMLResponse("<h2 style='color:#1D9E75'>✓ Approved — orders being sent now.</h2><p>Check logs for execution details.</p>")


@app.get("/skip", response_class=HTMLResponse)
async def skip():
    """John clicks SKIP — or timeout fires."""
    state["decision"] = "skipped"
    logger.info("SKIPPED — no orders placed today")
    return HTMLResponse("<h2 style='color:#888'>Skipped for today.</h2>")


@app.get("/performance", response_class=HTMLResponse)
async def performance():
    return HTMLResponse(_format_performance_html(build_report()))


@app.get("/status")
async def status():
    """Machine-readable status endpoint."""
    return JSONResponse({
        "decision":    state["decision"],
        "has_pending": state["pending"] is not None,
        "arrived_at":  state["arrived_at"].isoformat() if state["arrived_at"] else None,
    })

#############################################
# TIMEOUT WATCHDOG
#############################################

def _timeout_watchdog():
    """Background thread: auto-skip if no decision within timeout window."""
    while True:
        time.sleep(30)
        if state["pending"] and state["decision"] is None and state["arrived_at"]:
            elapsed = (datetime.now() - state["arrived_at"]).total_seconds() / 60
            if elapsed >= APPROVAL_TIMEOUT_MIN:
                logger.warning(f"Approval timeout ({APPROVAL_TIMEOUT_MIN} min) — auto-skipping")
                state["decision"] = "skipped"

#############################################
# ENTRY POINT
#############################################

if __name__ == "__main__":
    threading.Thread(target=_timeout_watchdog, daemon=True).start()
    logger.info(f"Approval server running at http://{APPROVAL_HOST}:{APPROVAL_PORT}")
    uvicorn.run(app, host=APPROVAL_HOST, port=APPROVAL_PORT, log_level="warning")
