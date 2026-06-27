"""EOD Report — pure builders + thin render().

Reads the already-published Redis caches (``options:*`` and ``driver:*``) and
builds a Summary and a Detailed end-of-day report as an HTML *fragment* + a
shared CSS string (one source of truth for both the in-app view and the exported
standalone ``.html`` files). Mirrors the ``gamma.py`` Explain pattern: NiceGUI's
``ui.html`` strips ``<style>``, so the CSS goes through ``ui.add_css`` in-app and
is inlined into the document on export.

The webgui reads Redis only — no app-engine imports — so this page honors the
3-tier rule (webgui imports only ``nicegui`` + ``shared.bus`` + ``shared.contracts``).
Every builder is defensive: a missing/empty cache renders a "No data" note rather
than raising.
"""
from __future__ import annotations

import datetime as dt
import re
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import bus_client
from nicegui import ui

# Where generated standalone reports are archived (under the gitignored data dir).
ARCHIVE_ROOT = Path(__file__).resolve().parents[1] / "data" / "eod"

_CT = ZoneInfo("America/Chicago")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Scoped to .eod-report so the in-app add_css does not leak into the rest of the app.
EOD_CSS = """
.eod-report { font-family: -apple-system, Segoe UI, Roboto, sans-serif; color: #e8e8e8; }
.eod-report h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
.eod-report h2 { font-size: 1.05rem; margin: 1.2rem 0 .4rem; opacity: .85;
                 border-bottom: 1px solid rgba(255,255,255,.12); padding-bottom: .2rem; }
.eod-report .meta { opacity: .6; font-size: .8rem; margin-bottom: .6rem; }
.eod-report table { border-collapse: collapse; width: 100%; font-size: .82rem; margin: .3rem 0; }
.eod-report th, .eod-report td { text-align: left; padding: 4px 8px;
                 border-bottom: 1px solid rgba(255,255,255,.08); }
.eod-report th { opacity: .7; font-weight: 600; }
.eod-report .tiles { display: flex; flex-wrap: wrap; gap: .6rem; margin: .4rem 0; }
.eod-report .tile { background: rgba(255,255,255,.05); border-radius: 10px;
                 padding: .5rem .8rem; min-width: 120px; }
.eod-report .tile .k { font-size: .72rem; opacity: .6; }
.eod-report .tile .v { font-size: 1.1rem; font-weight: 700; }
.eod-report .pos { color: #4caf50; } .eod-report .neg { color: #ef5350; }
.eod-report .none { opacity: .5; font-style: italic; }
.eod-report .summary-line { margin: .3rem 0; }
.eod-report a { color: #64b5f6; }
"""


# ----------------------------------------------------------------------------- #
# Document wrapper + formatting helpers
# ----------------------------------------------------------------------------- #
def wrap_document(fragment: str, css: str, title: str) -> str:
    """Wrap a report fragment + CSS into a self-contained HTML document for export."""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title><style>{css}</style></head>"
        f"<body style=\"background:#1e1e1e;margin:0;padding:1.2rem\">{fragment}</body></html>"
    )


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _money(v) -> str:
    n = _num(v)
    if n is None:
        return "—"  # em dash
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"


def _cell(v) -> str:
    return f"<td>{escape('' if v is None else str(v))}</td>"


def _pn_class(v) -> str:
    """CSS class for a signed number ('pos'/'neg'/'')."""
    n = _num(v)
    return "" if n is None or n == 0 else ("pos" if n > 0 else "neg")


def _table(headers, rows_html, empty="No data") -> str:
    if not rows_html:
        return f'<p class="none">{escape(empty)}</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join(rows_html)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


# ----------------------------------------------------------------------------- #
# Per-section builders (each returns a fragment; tolerates None / empty caches)
# ----------------------------------------------------------------------------- #
def captured_section(cache) -> str:
    sigs = cache.get("signals", []) if isinstance(cache, dict) else []
    rows = []
    for s in sigs:
        drift = _num(s.get("score_drift"))
        drift_txt = f"{drift:.2f}" if drift is not None else "—"
        rows.append(
            "<tr>"
            + _cell(s.get("symbol"))
            + _cell(s.get("score"))
            + _cell(s.get("current_score"))
            + f'<td class="{_pn_class(drift)}">{drift_txt}</td>'
            + _cell(s.get("recommendation"))
            + f'<td class="{_pn_class(s.get("unrealized_pnl"))}">{_money(s.get("unrealized_pnl"))}</td>'
            + "</tr>"
        )
    return _table(
        ["Symbol", "Entry score", "Current", "Drift", "Rec", "Unrealized P&L"],
        rows, empty="No captured signals.")


def paper_section(trades_cache, account_cache) -> str:
    account_cache = account_cache if isinstance(account_cache, dict) else {}
    snap = account_cache.get("snapshot") if account_cache.get("has_account") else None
    if isinstance(snap, dict):
        summary = (
            '<p class="summary-line">'
            f'Equity {_money(snap.get("equity"))} · '
            f'Session P&L <span class="{_pn_class(snap.get("session_pnl"))}">'
            f'{_money(snap.get("session_pnl"))}</span> · '
            f'Realized {_money(snap.get("realized_pnl"))} · '
            f'Open unrealized <span class="{_pn_class(snap.get("open_unrealized"))}">'
            f'{_money(snap.get("open_unrealized"))}</span> · '
            f'{int(_num(snap.get("open_count"), 0))} open'
            "</p>"
        )
    else:
        summary = '<p class="none">No paper account.</p>'

    trades = trades_cache.get("trades", []) if isinstance(trades_cache, dict) else []
    rows = []
    for t in trades:
        rows.append(
            "<tr>"
            + _cell(t.get("symbol"))
            + _cell(t.get("strategy"))
            + _cell(t.get("status"))
            + _cell((t.get("entry_time") or "")[:19])
            + f'<td class="{_pn_class(t.get("realized_pnl"))}">{_money(t.get("realized_pnl"))}</td>'
            + "</tr>"
        )
    table = _table(
        ["Symbol", "Strategy", "Status", "Entry", "Realized P&L"], rows,
        empty="No paper trades.")
    return summary + table


def scanner_section(scan_cache) -> str:
    scan_cache = scan_cache if isinstance(scan_cache, dict) else {}

    def _signal_table(sigs):
        rows = []
        for s in (sigs or []):
            rows.append("<tr>" + _cell(s.get("symbol"))
                        + _cell(s.get("composite_score")) + "</tr>")
        return _table(["Symbol", "Score"], rows, empty="No signals.")

    return (
        "<h2>0-DTE</h2>" + _signal_table(scan_cache.get("signals_0dte"))
        + "<h2>Swing</h2>" + _signal_table(scan_cache.get("signals_swing"))
    )


def driver_section(approvals_cache, perf_cache) -> str:
    appr = approvals_cache if isinstance(approvals_cache, dict) else {}
    perf = perf_cache if isinstance(perf_cache, dict) else {}
    if not appr and not perf:
        return '<p class="none">No driver activity.</p>'

    parts = []
    if appr:
        parts.append(
            '<p class="summary-line">'
            f'Grade <b>{escape(str(appr.get("grade") or "—"))}</b> · '
            f'Status {escape(str(appr.get("status") or "—"))} · '
            f'Decision {escape(str(appr.get("decision") or "—"))} · '
            f'P&L today <span class="{_pn_class(appr.get("pnl_today"))}">'
            f'{_money(appr.get("pnl_today"))}</span>'
            "</p>"
        )
        rows = []
        for tr in (appr.get("proposed_trades") or []):
            rows.append(
                "<tr>" + _cell(tr.get("symbol")) + _cell(tr.get("bucket"))
                + _cell(tr.get("side")) + _cell(tr.get("quantity")) + "</tr>")
        parts.append(_table(
            ["Symbol", "Bucket", "Side", "Qty"], rows, empty="No proposed trades."))

    summary = perf.get("summary") if isinstance(perf.get("summary"), dict) else {}
    if summary:
        win = _num(summary.get("win_rate"))
        win_txt = f"{win * 100:.0f}%" if win is not None else "—"
        parts.append(
            '<p class="summary-line">'
            f'Win rate {win_txt} · '
            f'Realized P&L <span class="{_pn_class(summary.get("realized_pnl"))}">'
            f'{_money(summary.get("realized_pnl"))}</span> · '
            f'{int(_num(summary.get("trades"), 0))} trades'
            "</p>"
        )
    return "".join(parts)


# ----------------------------------------------------------------------------- #
# Trade normalization + period/breakdown aggregation (additive)
# ----------------------------------------------------------------------------- #
def _date_of(ts):
    """YYYY-MM-DD from an ISO timestamp string, or None."""
    s = str(ts or "")
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else None


def normalize_trades(raw, *, kind):
    """Map a book's raw trade dicts into one uniform shape:
    {symbol, strategy, trade_type, status, entry_date, exit_date, realized_pnl, credit}.
    ``kind`` = 'ledger' (manual paper_trades) | 'driver' (driver positions)."""
    out = []
    for t in raw or []:
        t = t or {}
        if kind == "driver":
            entry, exit_ = "entry_ts", "exit_ts"
            qty = _num(t.get("quantity"), 1) or 1
            per = _num(t.get("entry_credit"))
            credit = round(per * qty * 100, 2) if per is not None else None
            trade_type = t.get("trade_type")  # normally absent on driver positions
        else:
            entry, exit_ = "entry_time", "exit_time"
            credit = _num(t.get("entry_credit_total"))
            trade_type = t.get("trade_type")
        out.append({
            "symbol": t.get("symbol"),
            "strategy": t.get("strategy"),
            "trade_type": trade_type,
            "status": t.get("status"),
            "entry_date": _date_of(t.get(entry)),
            "exit_date": _date_of(t.get(exit_)),
            "realized_pnl": _num(t.get("realized_pnl")),
            "credit": credit,
        })
    return out


def _period_ranges(today):
    """(start_date, end_date) inclusive for daily / weekly(WTD) / mtd."""
    return {
        "daily": (today, today),
        "weekly": (today - dt.timedelta(days=today.weekday()), today),  # Monday→today
        "mtd": (today.replace(day=1), today),
    }


def _parse_date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _in_range(date_str, lo, hi):
    d = _parse_date(date_str)
    return d is not None and lo <= d <= hi


def period_buckets(norm_trades, today):
    """{daily, weekly, mtd} each {realized, closed, wins, losses, win_rate, opened,
    credit}. Realized/closed bucket by EXIT date; opened/credit by ENTRY date."""
    out = {}
    for name, (lo, hi) in _period_ranges(today).items():
        realized = wins = losses = closed = opened = 0.0
        credit = 0.0
        for t in norm_trades or []:
            if _in_range(t.get("exit_date"), lo, hi):
                closed += 1
                pnl = _num(t.get("realized_pnl"))
                if pnl is not None:
                    realized += pnl
                    if pnl > 0:
                        wins += 1
                    elif pnl < 0:
                        losses += 1
            if _in_range(t.get("entry_date"), lo, hi):
                opened += 1
                c = _num(t.get("credit"))
                if c is not None:
                    credit += c
        decided = wins + losses
        out[name] = {
            "realized": round(realized, 2), "closed": int(closed),
            "wins": int(wins), "losses": int(losses),
            "win_rate": (wins / decided) if decided else None,
            "opened": int(opened), "credit": round(credit, 2),
        }
    return out


def breakdown_rows(norm_trades, key):
    """Group normalized trades by ``key`` ('strategy'|'trade_type'|'status').
    Each row: {group, trades, open, closed, realized, wins, losses, win_rate}.
    Sorted by trade count desc. A missing/None key value groups under '—'."""
    groups = {}
    for t in norm_trades or []:
        g = t.get(key) or "—"
        b = groups.setdefault(g, {"group": g, "trades": 0, "open": 0, "closed": 0,
                                  "realized": 0.0, "wins": 0, "losses": 0})
        b["trades"] += 1
        st = (t.get("status") or "").upper()
        if st == "OPEN":
            b["open"] += 1
        else:
            b["closed"] += 1
        pnl = _num(t.get("realized_pnl"))
        if pnl is not None:
            b["realized"] += pnl
            if pnl > 0:
                b["wins"] += 1
            elif pnl < 0:
                b["losses"] += 1
    rows = []
    for b in groups.values():
        decided = b["wins"] + b["losses"]
        b["realized"] = round(b["realized"], 2)
        b["win_rate"] = (b["wins"] / decided) if decided else None
        rows.append(b)
    return sorted(rows, key=lambda r: r["trades"], reverse=True)


def _pct(frac):
    n = _num(frac)
    return "—" if n is None else f"{n * 100:.0f}%"


def toc(sections):
    """An anchor-link table of contents: sections = [(id, title), ...]."""
    links = " · ".join(f'<a href="#{escape(i)}">{escape(t)}</a>' for i, t in sections)
    return f'<nav class="eod-toc">{links}</nav>'


def details_section(anchor, title, body, *, open=True):
    """A collapsible section (native <details>, no JS — works in-app + exported)."""
    op = " open" if open else ""
    return (f'<details id="{escape(anchor)}" class="eod-sec"{op}>'
            f'<summary>{escape(title)}</summary>{body}</details>')


_PERIOD_LABELS = [("daily", "Daily"), ("weekly", "Weekly (WTD)"), ("mtd", "MTD")]


def performance_table_html(buckets):
    """Daily/Weekly/MTD rows: Realized | Closed (W-L) | Win% | Opened | Credit."""
    buckets = buckets or {}
    rows = []
    for key, label in _PERIOD_LABELS:
        b = buckets.get(key) or {}
        wl = f'{int(b.get("wins", 0))}-{int(b.get("losses", 0))}'
        rows.append(
            "<tr>"
            f"<td><b>{escape(label)}</b></td>"
            f'<td class="{_pn_class(b.get("realized"))}">{_money(b.get("realized"))}</td>'
            f'<td>{int(b.get("closed", 0))} ({wl})</td>'
            f'<td>{_pct(b.get("win_rate"))}</td>'
            f'<td>{int(b.get("opened", 0))}</td>'
            f'<td>{_money(b.get("credit"))}</td>'
            "</tr>")
    return _table(["Period", "Realized P&L", "Closed (W-L)", "Win %", "Opened",
                   "Credit collected"], rows, empty="No activity.")


def breakdown_table_html(rows):
    """A breakdown table: Group | Trades | Open | Closed | Realized | Win%."""
    body = []
    for r in rows or []:
        body.append(
            "<tr>"
            f"<td>{escape(str(r.get('group', '—')))}</td>"
            f"<td>{int(r.get('trades', 0))}</td>"
            f"<td>{int(r.get('open', 0))}</td>"
            f"<td>{int(r.get('closed', 0))}</td>"
            f'<td class="{_pn_class(r.get("realized"))}">{_money(r.get("realized"))}</td>'
            f'<td>{_pct(r.get("win_rate"))}</td>'
            "</tr>")
    return _table(["Group", "Trades", "Open", "Closed", "Realized P&L", "Win %"],
                  body, empty="No trades.")


# ----------------------------------------------------------------------------- #
# Whole-report fragments
# ----------------------------------------------------------------------------- #
def detail_fragment(snap: dict) -> str:
    snap = snap or {}
    parts = [
        '<div class="eod-report">',
        f"<h1>EOD Detailed Report — {escape(str(snap.get('date', '')))}</h1>",
        f'<div class="meta">Generated {escape(str(snap.get("generated_at", "")))}</div>',
        "<h2>Captured Signals</h2>", captured_section(snap.get("captured")),
        "<h2>Paper Trades</h2>",
        paper_section(snap.get("paper_trades"), snap.get("paper_account")),
        "<h2>Scanner Signals</h2>", scanner_section(snap.get("scan")),
        "<h2>Driver — Trades & Performance</h2>",
        driver_section(snap.get("driver_approvals"), snap.get("driver_performance")),
        "</div>",
    ]
    return "".join(parts)


def _count(d, *keys) -> int:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return 0
        cur = cur.get(k)
    return len(cur) if isinstance(cur, list) else 0


def _tile(k, v, cls="") -> str:
    return (f'<div class="tile"><div class="k">{escape(k)}</div>'
            f'<div class="v {cls}">{v}</div></div>')


def summary_fragment(snap: dict, detail_href: str) -> str:
    snap = snap or {}
    scan = snap.get("scan") or {}
    n_scan = _count(scan, "signals_0dte") + _count(scan, "signals_swing")
    n_cap = _count(snap.get("captured") or {}, "signals")
    n_paper = _count(snap.get("paper_trades") or {}, "trades")
    appr = snap.get("driver_approvals") or {}
    perf = (snap.get("driver_performance") or {}).get("summary") or {}
    acct = (snap.get("paper_account") or {}).get("snapshot") or {}
    session_pnl = acct.get("session_pnl")
    win = _num(perf.get("win_rate"))
    win_txt = f"{win * 100:.0f}%" if win is not None else "—"
    tiles = "".join([
        _tile("Paper session P&L", _money(session_pnl), _pn_class(session_pnl)),
        _tile("Scanner signals", n_scan),
        _tile("Captured signals", n_cap),
        _tile("Paper trades", n_paper),
        _tile("Driver grade", escape(str(appr.get("grade") or "—"))),
        _tile("Driver status", escape(str(appr.get("status") or "—"))),
        _tile("Driver win rate", win_txt),
    ])
    return (
        '<div class="eod-report">'
        f"<h1>EOD Summary — {escape(str(snap.get('date', '')))}</h1>"
        f'<div class="meta">Generated {escape(str(snap.get("generated_at", "")))}</div>'
        f'<div class="tiles">{tiles}</div>'
        f'<p><a href="{escape(detail_href)}">View detailed report →</a></p>'
        "</div>"
    )


# ----------------------------------------------------------------------------- #
# Snapshot + archive
# ----------------------------------------------------------------------------- #
def read_snapshot() -> dict:
    """Snapshot the current Redis caches into one dict for the builders."""
    now = dt.datetime.now(_CT)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M CT"),
        "scan": bus_client.read("options:scan") or {},
        "captured": bus_client.read("options:captured") or {},
        "paper_trades": bus_client.read("options:paper_trades") or {},
        "paper_account": bus_client.read("options:paper_account") or {},
        "driver_approvals": bus_client.read("driver:approvals") or {},
        "driver_performance": bus_client.read("driver:performance") or {},
    }


def _ct_today() -> str:
    """Current CT trading date as YYYY-MM-DD (matches the app's CT clock)."""
    return dt.datetime.now(_CT).strftime("%Y-%m-%d")


def archive_dates(root) -> list[str]:
    """Dated archive subfolders (YYYY-MM-DD), newest first."""
    root = Path(root)
    if not root.is_dir():
        return []
    dates = [p.name for p in root.iterdir() if p.is_dir() and _DATE_RE.match(p.name)]
    return sorted(dates, reverse=True)


def write_archive(root, date: str, summary_doc: str, detail_doc: str) -> dict:
    """Write summary.html + detail.html into <root>/<date>/; return their paths."""
    day = Path(root) / date
    day.mkdir(parents=True, exist_ok=True)
    summ, det = day / "summary.html", day / "detail.html"
    summ.write_text(summary_doc, encoding="utf-8")
    det.write_text(detail_doc, encoding="utf-8")
    return {"summary": summ, "detail": det}


def generate() -> dict:
    """Snapshot caches, build both standalone docs, write the dated archive."""
    snap = read_snapshot()
    date = snap["date"]
    summary_doc = wrap_document(
        summary_fragment(snap, "detail.html"), EOD_CSS, f"EOD Summary {date}")
    detail_doc = wrap_document(
        detail_fragment(snap), EOD_CSS, f"EOD Detail {date}")
    write_archive(ARCHIVE_ROOT, date, summary_doc, detail_doc)
    return {"date": date}


# ----------------------------------------------------------------------------- #
# Thin page functions (logic lives in the tested builders above)
# ----------------------------------------------------------------------------- #
def render() -> None:
    """Summary page: action bar + archive list + in-app summary fragment."""
    ui.add_css(EOD_CSS)
    container = ui.column().classes("w-full gap-2")

    def _open_file(which: str) -> None:
        ui.navigate.to(f"/eod/file?date={_ct_today()}&which={which}", new_tab=True)

    def _repaint() -> None:
        container.clear()
        with container:
            with ui.row().classes("items-center gap-2"):
                ui.button("Generate", icon="play_arrow", on_click=_on_generate)
                ui.button("Open summary file", icon="open_in_new",
                          on_click=lambda: _open_file("summary")).props("flat")
                ui.button("Open detail file", icon="open_in_new",
                          on_click=lambda: _open_file("detail")).props("flat")
            dates = archive_dates(ARCHIVE_ROOT)
            if dates:
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.label("Archive:").classes("opacity-60")
                    for d in dates:
                        ui.link(d, f"/eod/file?date={d}&which=summary").props("target=_blank")
            ui.html(summary_fragment(read_snapshot(), "/eod/detail"))

    def _on_generate() -> None:
        try:
            out = generate()
            ui.notify(f"EOD report generated for {out['date']}", type="positive")
        except Exception as e:  # defensive: never crash the page
            ui.notify(f"Generate failed: {e}", type="negative")
        _repaint()

    _repaint()


def render_detail() -> None:
    """Detailed page: in-app detail fragment from live caches."""
    ui.add_css(EOD_CSS)
    ui.html(detail_fragment(read_snapshot()))
