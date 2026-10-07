"""Display builders for the Captured Signals page's TRACKED section (PURE).

The options service publishes ``cache:options:tracked``: the structures the
Market Scanner recorded that are not credit spreads - debit spreads, straddles
and strangles, butterflies and condors, calendars, backspreads, the Directional
tab's single legs. They are MEASURED, never traded: none is in a paper book and
no setting opens one. This module turns that payload into table rows; it holds
no ``ui.`` call and is tested in ``webgui/tests/test_tracked_view.py``.

What the store holds, and what a reader must not get wrong:

* ``entry_credit`` and ``current_value`` are PER SHARE and SIGNED. A debit is a
  negative ``entry_credit``; ``current_value`` is the cost to close, so a long
  position that is worth money carries a NEGATIVE one. Every cell here says
  which way the money moves in words rather than leaving a sign to be read.
* ``entry_max_loss`` is per share too. For a structure whose loss is not capped
  it is a margin estimate, never a worst case, and the row says so.
* A calendar that could not be valued at its front expiry closes with NO result.
  It is counted as that and never as a scratch trade.
"""
from pages import fmt as _fmt

from .strategy_table import _short_exp, legs_summary
from .theme import BADGE_MUTED, BADGE_WARN, TXT_NEG, TXT_POS

EXPLAINER = ("Structures the scanner recorded to measure how they turn out. "
             "They are not trades: none is in a paper book, and nothing opens "
             "one automatically.")
EMPTY_OPEN = "No tracked structure is open."
EMPTY_RESULTS = "No tracked structure has closed yet."

MULTIPLIER = 100.0

# Whole words for the scanner's structure codes. A code this table has never
# seen is shown as its own words (``NEW_THING`` -> "New thing"), never hidden.
_LABELS = {
    "LONG_CALL": "Long call", "LONG_PUT": "Long put",
    "SHORT_CALL": "Short call", "SHORT_PUT": "Short put",
    "BULL_CALL": "Bull call spread", "BEAR_PUT": "Bear put spread",
    "LONG_STRADDLE": "Long straddle", "SHORT_STRADDLE": "Short straddle",
    "LONG_STRANGLE": "Long strangle", "SHORT_STRANGLE": "Short strangle",
    "BUTTERFLY_CALL": "Call butterfly", "BUTTERFLY_PUT": "Put butterfly",
    "CONDOR_CALL": "Call condor", "CONDOR_PUT": "Put condor",
    "IRON_BUTTERFLY": "Iron butterfly", "IRON_CONDOR": "Iron condor",
    "CALENDAR_CALL": "Call calendar", "CALENDAR_PUT": "Put calendar",
    "DIAGONAL_CALL": "Call diagonal", "DIAGONAL_PUT": "Put diagonal",
    "CALL_BACKSPREAD": "Call backspread", "PUT_BACKSPREAD": "Put backspread",
}

# The manage loop's codes, in the reader's words.
_STATUS = {"HOLD": "Holding", "TARGET_HIT": "Target reached",
           "MONEY_STOP": "Stopped", "FRONT_EXPIRY": "Front leg expiring"}
_REASONS = {"TARGET_HIT": "Target reached", "MONEY_STOP": "Stopped",
            "FRONT_EXPIRY": "Closed before the front leg expired",
            "EXPIRED": "Settled at expiry", "UNMARKABLE": "Could not be valued"}

NOTE_UNCAPPED = "Loss not capped"
NOTE_EARNINGS = "Open through earnings"
NOT_CAPPED = "Not capped"


def label(code):
    """A structure code in whole words; ``""`` for nothing."""
    if not isinstance(code, str) or not code.strip():
        return ""
    key = code.strip().upper()
    return _LABELS.get(key) or key.replace("_", " ").capitalize()


def entry_text(entry_credit):
    """``"5.40 paid"`` / ``"1.35 received"`` per share, or the dash."""
    v = _fmt.num(entry_credit)
    if v is None:
        return _fmt.NO_READING
    return f"{_fmt.price(abs(v))} {'paid' if v < 0 else 'received'}"


def now_text(current_value):
    """What closing would do now, per share: ``"5.60 to sell"`` for a position
    worth money, ``"1.40 to buy back"`` for one that costs money to close."""
    v = _fmt.num(current_value)
    if v is None:
        return _fmt.NO_READING
    return f"{_fmt.price(abs(v))} {'to sell' if v < 0 else 'to buy back'}"


def risk_text(row):
    """Max loss per contract in dollars, or :data:`NOT_CAPPED`."""
    if (row or {}).get("unbounded"):
        return NOT_CAPPED
    v = _fmt.num((row or {}).get("entry_max_loss"))
    return _fmt.NO_READING if v is None else _fmt.money(v * MULTIPLIER)


def _pnl_class(value):
    v = _fmt.num(value)
    if v is None or v == 0:
        return ""
    return TXT_POS if v > 0 else TXT_NEG


def _notes(row):
    out = []
    if row.get("unbounded"):
        out.append(NOTE_UNCAPPED)
    if row.get("entry_spans_earnings"):
        out.append(NOTE_EARNINGS)
    return out


def _opened(ts):
    s = str(ts or "")
    return f"{s[:10]} {s[11:16]}" if "T" in s and len(s) >= 16 else s[:16]


def open_columns():
    spec = [("symbol", "Symbol"), ("structure", "Structure"), ("legs", "Legs"),
            ("opened", "Recorded"), ("expiry", "First expiry"),
            ("entry", "Entry, per share"), ("now", "Now, per share"),
            ("max_loss", "Max loss"), ("unrealized_pnl", "Open result"),
            ("status", "Status"), ("notes", "Notes")]
    return [{"name": f, "label": lbl, "field": f, "sortable": f != "notes",
             "align": "left"} for f, lbl in spec]


OPEN_NUMERIC = ("unrealized_pnl",)


def open_rows(payload):
    """One row per open tracked structure, newest first. Never raises on a
    malformed row: it is skipped."""
    rows = []
    for r in (payload or {}).get("open") or []:
        if not isinstance(r, dict) or not r.get("signal_id"):
            continue
        code = r.get("recommendation")
        pnl = _fmt.num(r.get("unrealized_pnl"))
        rows.append({
            "id": r["signal_id"],
            "symbol": r.get("symbol", ""),
            "structure": label(r.get("strategy")),
            "legs": legs_summary(r.get("legs")),
            "opened": _opened(r.get("first_seen_ts")),
            "_opened_raw": str(r.get("first_seen_ts") or ""),
            "expiry": _short_exp(r.get("expiration")),
            "entry": entry_text(r.get("entry_credit")),
            "now": now_text(r.get("current_value")),
            "max_loss": risk_text(r),
            "unrealized_pnl": pnl,
            "status": _STATUS.get(code, "Not marked yet" if pnl is None else ""),
            "notes": _notes(r),
            "_pnl_class": _pnl_class(pnl),
            "_pnl_text": _fmt.money(pnl, signed=True),
            "_reason": r.get("recommendation_reason") or "",
        })
    rows.sort(key=lambda row: row["_opened_raw"], reverse=True)
    return rows


def result_columns():
    spec = [("structure", "Structure"), ("n", "Closed"), ("win_pct", "Winners"),
            ("total_pnl", "Total result"), ("avg_r", "Average return on risk"),
            ("unmarkable", "Not valued"), ("notes", "Notes")]
    return [{"name": f, "label": lbl, "field": f, "sortable": f != "notes",
             "align": "left"} for f, lbl in spec]


RESULT_NUMERIC = ("n", "win_pct", "total_pnl", "avg_r", "unmarkable")


def return_on_risk_text(avg_r):
    """The mean result over the dollars at risk, as a signed percentage of that
    risk: 0.49 reads ``+49.00%``."""
    r = _fmt.num(avg_r)
    return _fmt.NO_READING if r is None else _fmt.pct(r * 100.0, signed=True)


def _result_notes(s):
    out = []
    if s.get("unbounded"):
        out.append(f"{NOTE_UNCAPPED}: return on risk uses a margin estimate")
    n = _fmt.num(s.get("through_earnings"))
    if n:
        out.append(f"{int(n)} open through earnings")
    return out


def result_rows(payload):
    """One row per structure that has closed at least once, most closes first."""
    rows = []
    for s in (payload or {}).get("stats") or []:
        if not isinstance(s, dict) or not s.get("strategy"):
            continue
        total = _fmt.num(s.get("total_pnl"))
        rows.append({
            "id": s["strategy"],
            "structure": label(s["strategy"]),
            "n": int(_fmt.num(s.get("n")) or 0),
            "win_pct": _fmt.num(s.get("win_pct")),
            "total_pnl": total,
            "avg_r": _fmt.num(s.get("avg_r")),
            "unmarkable": int(_fmt.num(s.get("unmarkable")) or 0),
            "notes": _result_notes(s),
            "_pnl_class": _pnl_class(total),
            "_pnl_text": _fmt.money(total, signed=True),
            "_win_text": _fmt.pct(s.get("win_pct")),
            "_r_text": return_on_risk_text(s.get("avg_r")),
        })
    return rows


def closed_today_lines(payload):
    """``"SPY Long straddle: target reached, +$272.50"`` for each of today's
    closes, in the order they closed."""
    out = []
    rows = [r for r in (payload or {}).get("closed_today") or [] if isinstance(r, dict)]
    rows.sort(key=lambda r: str(r.get("close_ts") or ""))
    for r in rows:
        why = _REASONS.get(r.get("exit_reason"), str(r.get("exit_reason") or "Closed"))
        pnl = _fmt.num(r.get("realized_pnl"))
        tail = "no result" if pnl is None else _fmt.money(pnl, signed=True)
        out.append(f"{r.get('symbol', '')} {label(r.get('strategy'))}: "
                   f"{why.lower()}, {tail}")
    return out


def status_text(payload):
    """``"3 open · 12 closed · 2 closed today"``, or ``""`` before a payload."""
    counts = (payload or {}).get("counts")
    if not isinstance(counts, dict):
        return ""
    return (f"{int(counts.get('open') or 0)} open · "
            f"{int(counts.get('closed') or 0)} closed · "
            f"{int(counts.get('closed_today') or 0)} closed today")


def note_class(text):
    """A note's badge: the uncapped-loss warning stands out, the rest are quiet."""
    return BADGE_WARN if str(text).startswith(NOTE_UNCAPPED) else BADGE_MUTED


def with_note_classes(rows):
    """Turn each row's ``notes`` into ``[{text, cls}]`` for the table's slot."""
    for row in rows:
        row["notes"] = [{"text": n, "cls": note_class(n)} for n in row.get("notes") or []]
    return rows
