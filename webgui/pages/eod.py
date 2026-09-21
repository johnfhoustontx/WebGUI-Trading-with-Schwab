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
import os
import re
import tempfile
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import bus_client
from nicegui import run, ui

from pages import ui_kit as kit
from pages.fmt import float_or  # the ONE copy (pages/fmt.py)
from pages.options import theme
from pages.ui_guard import guard, guard_async

def _num(v, default=None):
    return float_or(v, default)


# Where generated standalone reports are archived (under the gitignored data dir).
ARCHIVE_ROOT = Path(__file__).resolve().parents[1] / "data" / "eod"

_CT = ZoneInfo("America/Chicago")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The system stack used when [typography].family is empty (= keep the app
# default). Named rather than inlined so the fallback is one thing, not two.
_SYSTEM_FONT = "-apple-system, system-ui, Roboto, sans-serif"


def build_eod_css(theme_values) -> str:
    """The report's stylesheet, in the app's own colours. PURE.

    ⚠ TWO destinations, and that is why this stays raw CSS instead of becoming
    kit tokens: ``ui.add_css`` in both frames (this page's one documented
    escape hatch) and :func:`wrap_document` into the standalone
    ``summary.html`` / ``detail.html`` that ``/eod/file`` serves — RAW
    documents, with no NiceGUI, no Tailwind and no app stylesheet behind them.
    The design says those "take the navy background, IBM Plex and the button
    look in their own inline CSS", so this is RECOLOURED, not deleted and not
    routed through the kit.

    Every colour comes from ``theme`` — which is also what makes the exported
    document follow Settings → Appearance. What was here instead: Segoe UI, a
    ``#e8e8e8`` text tier, six ``opacity:`` dims, four
    ``rgba(255,255,255,…)`` hairlines and washes, a ``#64b5f6`` link, and
    ``.pos``/``.neg`` at ``#4caf50``/``#ef5350`` — a FOURTH green/red pair
    beside the three the design already counted. ``_pn_class`` returns ``""``
    for zero, which already matches "muted for zero", and is untouched.

    ⚠ Every rule must keep its ``.eod-report`` prefix: this is injected
    APP-WIDE by ``ui.add_css``, so an unscoped rule would restyle every other
    page in the app.

    ⚠ The tile's colour sits on ``.tile``, NOT on ``.tile .v`` — a specificity
    trap that a first draft walked straight into and a screenshot did not show.
    ``.eod-report .tile .v`` is THREE classes and out-specifies
    ``.eod-report .neg``, which is two, so the two P&L tiles rendered title
    white while still reading as red to the eye; measured,
    ``getComputedStyle(".v.neg").color`` was ``rgb(238,241,246)``. On ``.tile``
    an unsigned value INHERITS the title colour while a signed one is claimed
    by the directly-matching ``.pos`` / ``.neg`` rule, since a rule that
    matches an element always beats one it merely inherits.
    """
    p = theme_values["palette"]
    s = theme_values["semantic"]
    font = (theme_values["typography"].get("family") or "").strip() or _SYSTEM_FONT
    return f"""
.eod-report {{ font-family: {font}; color: {p['text']}; }}
.eod-report h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; color: {p['title']}; }}
.eod-report h2 {{ font-size: 1.05rem; margin: 1.2rem 0 .4rem; color: {p['title']};
                 border-bottom: 1px solid {p['card_border']}; padding-bottom: .2rem; }}
.eod-report h3, .eod-report h4 {{ color: {p['title']}; }}
.eod-report .meta {{ color: {p['muted']}; font-size: .8rem; margin-bottom: .6rem; }}
.eod-report table {{ border-collapse: collapse; width: 100%; font-size: .82rem; margin: .3rem 0; }}
.eod-report th, .eod-report td {{ text-align: left; padding: 4px 8px;
                 border-bottom: 1px solid {p['card_border']}; }}
.eod-report th {{ color: {p['icon']}; font-weight: 600; }}
.eod-report .tiles {{ display: flex; flex-wrap: wrap; gap: .6rem; margin: .4rem 0; }}
.eod-report .tile {{ background: {p['card_bg']}; border: 1px solid {p['card_border']};
                 border-radius: 12px; padding: .5rem .8rem; min-width: 120px;
                 color: {p['title']}; }}
.eod-report .tile .k {{ font-size: .72rem; color: {p['icon']}; }}
.eod-report .tile .v {{ font-size: 1.1rem; font-weight: 700; }}
.eod-report .pos {{ color: {s['positive']}; }}
.eod-report .neg {{ color: {s['negative']}; }}
.eod-report .none {{ color: {p['muted']}; font-style: italic; }}
.eod-report .summary-line {{ margin: .3rem 0; }}
.eod-report a {{ color: {p['primary']}; }}
.eod-report .eod-toc {{ margin: .4rem 0 .8rem; font-size: .85rem; color: {p['muted']}; }}
.eod-report .eod-toc a {{ margin-right: .2rem; }}
.eod-report details.eod-sec {{ margin: .5rem 0; border: 1px solid {p['card_border']};
    border-radius: 12px; padding: .2rem .7rem; background: {p['card_bg']}; }}
.eod-report details.eod-sec > summary {{ cursor: pointer; font-size: 1.0rem;
    font-weight: 600; padding: .35rem 0; color: {p['title']}; }}
.eod-report details.eod-sec[open] > summary {{ border-bottom: 1px solid {p['card_border']};
    margin-bottom: .3rem; }}
.eod-report .book-now {{ color: {p['muted']}; font-size: .82rem; margin: .25rem 0 .4rem; }}
.eod-report .book-note {{ color: {p['muted']}; font-size: .78rem; margin: .25rem 0 .1rem; }}
"""


# Scoped to .eod-report so the in-app add_css does not leak into the rest of the
# app. Resolved once at import, like every other theme-derived constant: the
# theme itself is read once at webgui startup.
EOD_CSS = build_eod_css(theme.THEME)

# The exported document's ground — the same three-stop radial ``build_surface_css``
# paints on ``<body>`` for every page. A body background is propagated to the
# canvas when ``<html>`` has none, so a short report still fills the window.
_P = theme.THEME["palette"]
DOC_BODY_BG = (f"radial-gradient(130% 90% at 50% -20%,{_P['page_bg1']} 0%,"
               f"{_P['page_bg2']} 55%,{_P['page_bg3']} 100%) fixed")


# ----------------------------------------------------------------------------- #
# Document wrapper + formatting helpers
# ----------------------------------------------------------------------------- #
def wrap_document(fragment: str, css: str, title: str) -> str:
    """Wrap a report fragment + CSS into a standalone HTML document for export.

    ⚠ It carries the app's web-font ``<link>`` as well as the CSS. This document
    is served by ``/eod/file`` with no app stylesheet behind it, so without the
    link the ``font-family`` would name a face the document never loads and fall
    back to the same system stack it used to hard-code — "IBM Plex" would be a
    claim the file does not keep. The cost is that the font (and only the font)
    comes off the network: opened with no connection, the document still renders
    in the stack's own fallback. ``theme.FONT_HEAD_HTML`` is ``""`` when no web
    font is configured, so this follows the config rather than pinning a URL.
    """
    return (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title>{theme.FONT_HEAD_HTML}"
        f"<style>{css}</style></head>"
        f"<body style=\"background:{DOC_BODY_BG};margin:0;padding:1.2rem\">"
        f"{fragment}</body></html>"
    )


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
        ["Symbol", "Entry score", "Current", "Drift", "Action", "Open P&L"],
        rows, empty="No captured signals.")


# "Entry", matching the Paper Ledger and Captured Signals: this book holds
# debits too, and a debit is stored as a negative credit.
_CLOSED_COLS = ["Symbol", "Strategy", "Entry", "Exit", "Realized", "Reason",
                "Time (CT)"]


def _closed_time(ts) -> str:
    """HH:MM from an ISO ``close_ts`` (written in CT by close_signal_manually)."""
    s = str(ts or "")
    return s[11:16] if len(s) >= 16 else "—"


def _dec2(v) -> str:
    n = _num(v)
    return f"{n:.2f}" if n is not None else "—"


def captured_closed_rows(cache) -> list:
    """Row HTML strings for today's closed captured outcomes."""
    closed = cache.get("closed", []) if isinstance(cache, dict) else []
    rows = []
    for c in closed:
        rows.append(
            "<tr>"
            + _cell(c.get("symbol"))
            + _cell(c.get("strategy"))
            + _cell(_dec2(c.get("entry_credit")))
            + _cell(_dec2(c.get("exit_value")))
            + f'<td class="{_pn_class(c.get("realized_pnl"))}">{_money(c.get("realized_pnl"))}</td>'
            + _cell(c.get("exit_reason"))
            + _cell(_closed_time(c.get("close_ts")))
            + "</tr>"
        )
    return rows


def captured_closed_section(cache) -> str:
    """A table of today's auto/manually closed captured trades + a day realized total.

    Graceful-empty ("No captured trades closed today."). Reads
    ``cache:options:captured_closed`` = ``{"closed": [...], "total_realized": …}``."""
    cache = cache if isinstance(cache, dict) else {}
    table = _table(_CLOSED_COLS, captured_closed_rows(cache),
                   empty="No captured trades closed today.")
    total = cache.get("total_realized")
    total_line = (
        '<p class="summary-line">Day realized: '
        f'<span class="{_pn_class(total)}">{_money(total)}</span></p>'
    )
    return table + total_line


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
    ``kind`` = 'ledger' (manual paper_trades) | 'driver' (driver positions)
    | 'captured' (scanner signals tracked to a close)."""
    out = []
    for t in raw or []:
        t = t or {}
        if kind == "captured":
            # A captured row carries the signal's OWN timestamp names, so this
            # branch only points ``_date_of`` at them - the same job it does for
            # the other two books, not a second date parser.
            #
            # The credit arrives already multiplied: ``compute.captured_perf_rows``
            # puts it on the ONE-CONTRACT basis that ``realized_pnl`` uses, since
            # a captured signal is never sized and the two figures on a row must
            # share a basis to be read together.
            entry, exit_ = "first_seen_ts", "close_ts"
            credit = _num(t.get("entry_credit_total"))
            trade_type = t.get("trade_type")
        elif kind == "driver":
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
# Per-book performance (shared by summary + detail)
# ----------------------------------------------------------------------------- #
def _books(snap):
    """[(label, norm_trades, now_snapshot)] for each book.

    Three of them: the two paper books, plus the captured signals scored from
    2026-09-01. Captured passes ``None`` for the snapshot because it has no
    account behind it - it is a TRACKING book, and ``_book_now_line`` already
    renders nothing for an absent snapshot rather than inventing an equity."""
    snap = snap or {}
    led = normalize_trades((snap.get("paper_trades") or {}).get("trades"), kind="ledger")
    dacc = snap.get("driver_paper_account") or {}
    drv_raw = list(dacc.get("positions") or []) + list(dacc.get("closed_positions") or [])
    drv = normalize_trades(drv_raw, kind="driver")
    cap = normalize_trades((snap.get("captured_perf") or {}).get("rows"),
                           kind="captured")
    return [
        ("Manual paper", led, (snap.get("paper_account") or {}).get("snapshot")),
        ("Driver", drv, dacc.get("snapshot")),
        ("Captured signals", cap, None),
    ]


def _book_now_line(snapshot):
    """Point-in-time figures from a book's snapshot (defensive; '' when absent)."""
    s = snapshot if isinstance(snapshot, dict) else {}
    if not s:
        return ""
    return (
        '<p class="book-now">'
        f'Equity {_money(s.get("equity"))} · '
        f'Session P&L <span class="{_pn_class(s.get("session_pnl"))}">{_money(s.get("session_pnl"))}</span> · '
        f'Open unrealized <span class="{_pn_class(s.get("open_unrealized"))}">{_money(s.get("open_unrealized"))}</span> · '
        f'{int(_num(s.get("open_count"), 0))} open'
        "</p>"
    )


# ⚠ Captured figures assume ONE contract, because a captured signal is never
# sized - ``/desk`` refuses to print a quantity for one on the grounds that a
# printed number would be inventing a position. Without this line a -$1,251
# month reads as an account loss rather than as what it is: the scanner's picks
# scored under the auto-manage rules.
CAPTURED_BASIS_NOTE = (
    "Figures assume ONE contract per signal - a captured signal is never sized. "
    "These are the scanner's picks scored under the auto-manage rules, not "
    "trades that were taken."
)


def _book_slug(label):
    """Deterministic anchor slug from a book label ('Manual paper' → 'manual')."""
    return label.split()[0].lower()


def _performance_block(snap, today):
    """One ``<details>`` performance section per book (manual + driver).

    Returns ``(toc_entries, sections_html)`` so callers can both link to and
    render the per-book performance — identical in the summary and the detail."""
    toc_entries, sections = [], []
    for label, norm, now_snap in _books(snap):
        anchor = "perf-" + _book_slug(label)
        toc_entries.append((anchor, label))
        body = _book_now_line(now_snap) + performance_table_html(
            period_buckets(norm, today))
        if label == "Captured signals":
            body += f'<p class="book-note">{escape(CAPTURED_BASIS_NOTE)}</p>'

        sections.append(details_section(anchor, f"{label} — performance", body))
    return toc_entries, "".join(sections)


# ----------------------------------------------------------------------------- #
# Whole-report fragments
# ----------------------------------------------------------------------------- #
def detail_fragment(snap: dict, today=None) -> str:
    snap = snap or {}
    today = today or dt.datetime.now(_CT).date()

    perf_toc, perf_html = _performance_block(snap, today)

    brk_parts = []
    for label, norm, _now in _books(snap):
        brk_parts.append(f"<h3>{escape(label)}</h3>")
        for sub_label, key in (("By strategy", "strategy"),
                               ("By 0-DTE / Swing", "trade_type"),
                               ("By status", "status")):
            brk_parts.append(f"<h4>{escape(sub_label)}</h4>")
            brk_parts.append(breakdown_table_html(breakdown_rows(norm, key)))
    breakdowns_html = "".join(brk_parts)

    # The driver's realized trades + performance are surfaced per-book (the "Driver"
    # book) inside the Performance + Breakdowns sections, sourced from
    # cache:options:driver_paper_account — so there is no separate legacy driver
    # section (the old morning-agent approvals/performance views are gone).
    nav = toc([
        ("performance", "Performance"),
        ("breakdowns", "Breakdowns"),
        ("trades", "Trades"),
        ("scanner", "Scanner"),
        ("captured", "Captured"),
        ("captured-closed", "Captured closed"),
    ])

    parts = [
        '<div class="eod-report">',
        f"<h1>EOD Detailed Report — {escape(str(snap.get('date', '')))}</h1>",
        f'<div class="meta">Generated {escape(str(snap.get("generated_at", "")))}</div>',
        nav,
        details_section("performance", "Performance", perf_html),
        details_section("breakdowns", "Breakdowns", breakdowns_html),
        details_section(
            "trades", "Paper Trades",
            paper_section(snap.get("paper_trades"), snap.get("paper_account"))),
        details_section("scanner", "Scanner Signals", scanner_section(snap.get("scan"))),
        details_section("captured", "Captured Signals",
                        captured_section(snap.get("captured"))),
        details_section("captured-closed", "Captured — closed today",
                        captured_closed_section(snap.get("captured_closed"))),
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


def summary_fragment(snap: dict, detail_href: str, today=None) -> str:
    snap = snap or {}
    today = today or dt.datetime.now(_CT).date()
    scan = snap.get("scan") or {}
    n_scan = _count(scan, "signals_0dte") + _count(scan, "signals_swing")
    n_cap = _count(snap.get("captured") or {}, "signals")
    n_paper = _count(snap.get("paper_trades") or {}, "trades")
    acct = (snap.get("paper_account") or {}).get("snapshot") or {}
    session_pnl = acct.get("session_pnl")
    # Driver at-a-glance from its isolated paper account's scorecard
    # (cache:options:driver_paper_perf) — the legacy morning-agent grade/status
    # approvals are gone.
    dperf = snap.get("driver_paper_perf") or {}
    d_realized = dperf.get("realized_pnl")
    d_win = _num(dperf.get("win_rate"))
    d_win_txt = f"{d_win * 100:.0f}%" if d_win is not None else "—"
    tiles = "".join([
        _tile("Paper session P&L", _money(session_pnl), _pn_class(session_pnl)),
        _tile("Scanner signals", n_scan),
        _tile("Captured signals", n_cap),
        _tile("Paper trades", n_paper),
        _tile("Driver realized P&L", _money(d_realized), _pn_class(d_realized)),
        _tile("Driver win rate", d_win_txt),
        _tile("Driver trades", int(_num(dperf.get("total_trades"), 0))),
    ])
    perf_toc, perf_html = _performance_block(snap, today)
    nav = toc(perf_toc + [("captured-closed", "Captured closed")])
    closed_html = details_section(
        "captured-closed", "Captured — closed today",
        captured_closed_section(snap.get("captured_closed")))
    return (
        '<div class="eod-report">'
        f"<h1>EOD Summary — {escape(str(snap.get('date', '')))}</h1>"
        f'<div class="meta">Generated {escape(str(snap.get("generated_at", "")))}</div>'
        f'<div class="tiles">{tiles}</div>'
        "<h2>Performance</h2>"
        f'{nav}'
        f'{perf_html}'
        f'{closed_html}'
        f'<p><a href="{escape(detail_href)}">View detailed report →</a></p>'
        "</div>"
    )


# ----------------------------------------------------------------------------- #
# Snapshot + archive
# ----------------------------------------------------------------------------- #
# {snapshot key: cache view} — the ONE list, so ``has_data`` cannot come to
# check a different set of views than ``read_snapshot`` reads.
_CACHE_VIEWS = {
    "scan": "options:scan",
    "captured": "options:captured",
    "captured_closed": "options:captured_closed",
    "captured_perf": "options:captured_perf",
    "paper_trades": "options:paper_trades",
    "paper_account": "options:paper_account",
    "driver_paper_account": "options:driver_paper_account",
    "driver_paper_perf": "options:driver_paper_perf",
}


def read_snapshot() -> dict:
    """Snapshot the current Redis caches into one dict for the builders."""
    now = dt.datetime.now(_CT)
    snap = {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M CT"),
    }
    for key, view in _CACHE_VIEWS.items():
        snap[key] = bus_client.read(view) or {}
    return snap


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


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    Writes to a temp file in the SAME directory (so ``os.replace`` is a rename on
    the same filesystem, which is atomic), then replaces the target. A crash
    mid-write leaves the temp file, never a half-written ``path`` — so a
    concurrent reader (``/eod/file``) never serves a partial document.
    ``os.replace`` overwrites an existing target atomically, preserving the
    same-date-overwrite behavior.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)  # atomic rename; overwrites any existing file
    except BaseException:
        # Best-effort cleanup of the temp file if the replace never happened.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_archive(root, date: str, summary_doc: str, detail_doc: str) -> dict:
    """Write summary.html + detail.html into <root>/<date>/; return their paths.

    Each file is written atomically (temp file + ``os.replace``) so a crash
    mid-generate can never leave a half-written ``.html`` for ``/eod/file`` to
    serve. Same-date regeneration overwrites in place, atomically.
    """
    day = Path(root) / date
    day.mkdir(parents=True, exist_ok=True)
    summ, det = day / "summary.html", day / "detail.html"
    _atomic_write_text(summ, summary_doc)
    _atomic_write_text(det, detail_doc)
    return {"summary": summ, "detail": det}


def has_data(snap: dict) -> bool:
    """True when at least one cache view in ``snap`` carried a payload.

    An all-empty snapshot is not a quiet day — every builder here is defensive,
    so a stopped stack or a bus that answered nothing renders a full report of
    "No data" notes that looks exactly like a real one. The archive is written
    per DATE and overwrites in place, so a second run against a cold cache would
    replace the day's real report with that.

    ⚠ EVERY caller checks this first. The scheduled run always did
    (``tools/generate_eod_report.py``, which prints a REFUSED line and exits 1);
    the page's Generate button did NOT until 2026-09-20 — it called
    :func:`generate` with no snapshot at all — so one click while the stack was
    stopped silently destroyed that day's report. The gate is deliberately not
    inside :func:`generate`: the tool's ``--allow-empty`` is a real escape and a
    gate one layer down would take it away.
    """
    return any(bool(snap.get(k)) for k in _CACHE_VIEWS)


def generate(snap: dict | None = None) -> dict:
    """Snapshot caches, build both standalone docs, write the dated archive.

    ``snap`` lets a caller reuse a snapshot it has already read — the scheduled
    run inspects one with :func:`has_data` before committing to write it, and a
    second ``read_snapshot()`` here would both cost a round trip and mean the
    bytes examined were not the bytes written.
    """
    snap = read_snapshot() if snap is None else snap
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
# Generate is a DESTRUCTIVE action: the archive holds one copy per date and
# ``write_archive`` replaces it in place, so it confirms like every other
# destructive control in the app.
GENERATE_TITLE = "Replace the saved report for today?"
GENERATE_BODY = ("Generate rebuilds today's saved report from the live caches "
                 "and replaces the summary and detail files already in the "
                 "archive. The archive keeps one copy per date.")

# ⚠ The refusal NAMES what was missing. Every builder in this module degrades
# to a "no data" note, so an all-empty snapshot renders a complete-looking
# report - and writing that over the day's real one is the thing ``has_data``
# was written to stop.
COLD_CACHE_REFUSAL = (
    "Nothing was written: every options cache read came back empty, so the "
    "report would have said no data on every line. Today's saved files are "
    "untouched. Start the stack and try again.")

# ⚠ These two open TODAY's ARCHIVED file, which does not exist until Generate
# has written it — ``main._serve_eod_file`` answers "No report for that date —
# click Generate first." in the new tab it opens. The dependency is disclosed
# HERE, in a tooltip, rather than by disabling the button: nothing is lost by
# clicking (the page you are on is untouched, and the archive list below
# already shows which dates exist), and a disabled button would need a
# per-paint ``is_file()`` check — a second source of truth about the archive,
# free to disagree with the route's own.
OPEN_SUMMARY_TIP = ("Opens today's archived summary.html in a new browser tab. "
                    "Generate writes it.")
OPEN_DETAIL_TIP = ("Opens today's archived detail.html in a new browser tab. "
                   "Generate writes it.")

# The message over the region while the two documents are built and written.
GENERATING_TEXT = "Generating the report…"
READING_TEXT = "Reading the caches…"


def render() -> None:
    """Summary page: one header line, then a region holding the archive list
    and the in-app summary fragment.

    ⚠ NO ``view=`` and no freshness stamp. This page reads EIGHT cache views
    (``_CACHE_VIEWS``), so a stamp on any one of them would name the age of a
    key that is only part of what is on screen. The fragment's own
    "Generated … CT" meta line is this page's real freshness, and it stays.

    The three page actions live in ``head.actions`` — primary last, so Generate
    is rightmost — which also takes them OUT of the block a repaint replaces.
    """
    ui.add_css(EOD_CSS)

    def _open_file(which: str) -> None:
        ui.navigate.to(f"/eod/file?date={_ct_today()}&which={which}", new_tab=True)

    with kit.page():
        head = kit.header("EOD Report")
        # ⚠ THREE labelled actions do not fit a phone, and the failure is silent.
        # kit.header's actions row is ``no-wrap`` and a Quasar button's own
        # content row WRAPS, so a button's min-content width is one word: at
        # 375px all three compressed to ~100px with the icon stacked ABOVE a
        # three-line label - the manuals.py finding, one row over, and the first
        # time it has bitten inside ``head.actions``. Letting the ROW wrap and
        # pinning each button to its own width is the same fix for the same
        # reason: this app is used from a phone.
        head.actions.classes(remove="no-wrap", add="flex-wrap justify-end")
        with head.actions:
            kit.button("Open summary file", kind="secondary", icon="open_in_new",
                       tooltip=OPEN_SUMMARY_TIP,
                       on_click=lambda: _open_file("summary")).classes("shrink-0")
            kit.button("Open detail file", kind="secondary", icon="open_in_new",
                       tooltip=OPEN_DETAIL_TIP,
                       on_click=lambda: _open_file("detail")).classes("shrink-0")
            kit.button("Generate", kind="primary", icon="play_arrow",
                       on_click=lambda: _open_generate()).classes("shrink-0")
        region = kit.region(READING_TEXT)

    def _paint(snap: dict) -> None:
        """Draw the archive list and the summary fragment from a snapshot ALREADY
        IN HAND. It takes one rather than reading one, so the document on screen
        after a Generate is the document that was archived."""
        region.content.clear()
        with region.content:
            dates = archive_dates(ARCHIVE_ROOT)
            if dates:
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.label("Archive").classes(theme.EYEBROW)
                    for d in dates:
                        ui.link(d, f"/eod/file?date={d}&which=summary") \
                            .props("target=_blank")
            ui.html(summary_fragment(snap, "/eod/detail"))

    @guard_async
    async def _repaint() -> None:
        """The first paint. ``read_snapshot`` is EIGHT sequential bus reads and
        ran on the event loop at page build; it crosses ``run.io_bound`` now, so
        the frame appears immediately with the region's spinner over it.

        ``or {}`` because ``run.io_bound`` answers ``None`` once the app is
        stopping — and every builder in this module is defensive about an empty
        snapshot anyway, so that degrades to the "no data" page rather than to a
        traceback on the way out."""
        region.busy.show()
        try:
            _paint(await run.io_bound(read_snapshot) or {})
        finally:
            region.busy.hide()

    @guard_async
    async def _generate_now() -> None:
        """Snapshot, CHECK it, then archive — in that order, on ONE read.

        ``has_data`` is the gate ``tools/generate_eod_report.py`` has always run
        and this button never did: it called ``generate()`` with no snapshot at
        all, so a click while the stack was stopped replaced the day's real
        report with an all-"no data" document that looks exactly like a real
        one. Reading the snapshot HERE is also what keeps the bytes examined and
        the bytes written the same bytes — a second read inside ``generate``
        would mean the gate passed on one snapshot and the archive got another.

        A refusal CLOSES the dialog and reports through a warn toast. That is
        the opposite of ``terminate.py``'s refused code, deliberately: there the
        reader can fix it in the dialog by typing a better one, and here they
        cannot — the only thing a still-open dialog would offer is a Generate
        that refuses identically. The toast opens by saying nothing was written.

        ⚠ The wait shows on the REGION, not on the Generate button. The block
        this replaces is the fragment, and ``kit.set_busy`` is not needed to
        stop a second run: ``kit.confirm`` holds its own re-entrancy latch for
        as long as this coroutine is awaited, and its dialog is modal until it
        returns.
        """
        region.busy.show(GENERATING_TEXT)
        try:
            snap = await run.io_bound(read_snapshot) or {}
            if not has_data(snap):
                kit.toast("warn", COLD_CACHE_REFUSAL)
                return                 # nothing written, so nothing to repaint
            await run.io_bound(generate, snap)
            # ``generate`` writes ``snap["date"]``, so reading the date back off
            # the snapshot reports the date that was written by construction -
            # and keeps the message out of io_bound's returns-None-while-
            # stopping path.
            kit.toast("ok", f"EOD report generated for {snap['date']}.")
            _paint(snap)
        except Exception as e:  # noqa: BLE001 - defensive: never crash the page
            kit.toast("error", f"Generate failed: {e}")
        finally:
            region.busy.hide()

    @guard
    def _open_generate() -> None:
        """Name the date whose files are about to be replaced.

        Set at OPEN time rather than at build: this dialog is built once and the
        page can outlive a midnight rollover, and a confirm naming yesterday
        while it replaces today is worse than one naming no date at all."""
        gen_dlg.body.text = (
            f"Generate rebuilds the saved report for {_ct_today()} from the "
            f"live caches and replaces the summary and detail files already in "
            f"the archive. The archive keeps one copy per date.")
        gen_dlg.open()

    # Built at render()'s OWN level, never inside the region's ``content``:
    # ``_paint`` clears that, and a dialog deletes itself with its slot.
    gen_dlg = kit.confirm(GENERATE_TITLE, GENERATE_BODY, confirm_text="Generate",
                          danger=True, on_confirm=_generate_now)

    ui.timer(0.1, _repaint, once=True)


def render_detail() -> None:
    """Detailed page: the in-app detail fragment from live caches.

    ⚠ The SECOND ``render()`` in this module, and easy to miss. Same frame and
    the same reasons: no ``view=`` and no stamp over eight views, the fragment's
    own meta line is the freshness, and the eight-read snapshot crosses
    ``run.io_bound`` instead of holding up the page build."""
    ui.add_css(EOD_CSS)
    with kit.page():
        kit.header("EOD Report — Detail")
        region = kit.region(READING_TEXT)

    @guard_async
    async def _repaint_detail() -> None:
        region.busy.show()
        try:
            snap = await run.io_bound(read_snapshot) or {}
            region.content.clear()
            with region.content:
                ui.html(detail_fragment(snap))
        finally:
            region.busy.hide()

    ui.timer(0.1, _repaint_detail, once=True)
