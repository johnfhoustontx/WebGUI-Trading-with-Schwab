"""Flow Alerts — Tier-1 reader of cache:options:flow_alerts.

The options service detects three kinds of flow alert on each 1-min GEX tick
(premium crossover, contract-level unusual activity, dealer gamma-regime flip),
pushes them to the phone, and publishes a day-scoped rolling list. Until this
page existed the webgui only chimed and toasted them, so a missed toast meant a
lost alert. This is the durable view: today's alerts, newest first.

Pure builders are module-level and NiceGUI-free for testing; ``render()`` mounts
the table and version-polls the bus. Tier-1: imports ONLY ``nicegui`` +
``bus_client`` + ``pages.*`` helpers — no engine/service imports.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

VIEW = "options:flow_alerts"

_CT_TZ = ZoneInfo("America/Chicago")

_KIND_LABEL = {"crossover": "Crossover", "uoa": "Unusual activity",
               "gamma_flip": "Gamma flip"}
_SIDE_LABEL = {"calls_over": "Calls over", "puts_over": "Puts over",
               "call": "Call", "put": "Put",
               "to_positive": "To positive", "to_negative": "To negative"}

# Direction → a FIXED Tailwind class (the finite-set mapping the UI standard
# requires — never a computed color, never an inline style).
_TONE_POS = "text-emerald-400"
_TONE_NEG = "text-rose-400"
_TONE_NEUTRAL = "text-slate-300"
_TONE = {
    ("crossover", "calls_over"): _TONE_POS,
    ("crossover", "puts_over"): _TONE_NEG,
    ("uoa", "call"): _TONE_POS,
    ("uoa", "put"): _TONE_NEG,
    # A gamma flip to POSITIVE means dealer hedging damps volatility, to NEGATIVE
    # that it amplifies it — constructive vs risky rather than bullish vs bearish,
    # but the same two colors carry it.
    ("gamma_flip", "to_positive"): _TONE_POS,
    ("gamma_flip", "to_negative"): _TONE_NEG,
}

# Colored cells bind the stamped ``_tone_class`` field via :class (Tailwind-first
# — no inline style). Raw <q-td> template, the scanner.py add_slot idiom.
_TONE_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._tone_class">{{ props.value }}</span>
  </q-td>
'''


def alert_kind_label(a):
    return _KIND_LABEL.get((a or {}).get("type"), "Flow")


def side_label(a):
    return _SIDE_LABEL.get((a or {}).get("side"), "")


def tone_class(a):
    d = a or {}
    return _TONE.get((d.get("type"), d.get("side")), _TONE_NEUTRAL)


def _money(v):
    """Compact dollars: $2.13M / $400k / $912. '' when unusable."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    a = abs(v)
    if a >= 999_500:            # rounds to >= $1.00M -> use the M form
        return f"${v/1e6:.2f}M"
    if a >= 1e3:
        return f"${v/1e3:.0f}k"
    return f"${v:,.0f}"


def _exp_short(expiry, dte):
    if dte == 0:
        return "0DTE"
    try:
        _, m, d = str(expiry).split("-")
        return f"{int(m):02d}/{int(d):02d}"
    except (AttributeError, TypeError, ValueError):
        return str(expiry or "")


def alert_detail(a):
    """The type-specific detail cell. Total — missing fields yield ''."""
    d = a or {}
    t = d.get("type")
    try:
        if t == "crossover":
            cp, pp = _money(d.get("call_prem")), _money(d.get("put_prem"))
            if not cp or not pp:
                return ""
            return f"{cp} calls vs {pp} puts"
        if t == "uoa":
            if d.get("strike") is None or d.get("volume") is None:
                return ""
            cp = "C" if d.get("side") == "call" else "P"
            return (f"{_exp_short(d.get('expiry'), d.get('dte'))} {float(d['strike']):g}{cp} · "
                    f"{int(d['volume']):,} vol / {int(d.get('oi') or 0):,} OI "
                    f"({float(d.get('vol_oi') or 0):.1f}×) · {_money(d.get('premium'))}")
        if t == "gamma_flip":
            spot, flip = d.get("spot"), d.get("flip")
            if spot is None or flip is None:
                return ""
            return f"spot {float(spot):g} vs flip {float(flip):g}"
    except (TypeError, ValueError):
        return ""
    return ""


def fmt_time(ts):
    """Unix seconds → Central 'HH:MM:SS'. '' when unusable."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return ""
    try:
        return _dt.datetime.fromtimestamp(ts, tz=_CT_TZ).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def age_text(ts, now):
    """How long ago the alert fired: 'just now' / '2m ago' / '1h 14m ago'.

    Clamped at zero — a ts slightly in the future (service/GUI clock skew) reads
    'just now', never a negative age."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return ""
    try:
        secs = now.timestamp() - float(ts)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return ""
    if secs < 60:
        return "just now"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m ago"
    return f"{mins // 60}h {mins % 60}m ago"


def alert_rows(view):
    """Display rows, NEWEST FIRST. The service appends oldest-first.

    Total over a missing/malformed view — ``render()`` does no validation."""
    alerts = (view or {}).get("alerts") if isinstance(view, dict) else None
    if not isinstance(alerts, list):
        return []
    rows = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        ts = a.get("ts")
        ts = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
        rows.append({
            # row_key: a missing/duplicate id would collapse rows in the table,
            # so fall back to a positional key rather than an empty string.
            "id": a.get("id") or f"{a.get('symbol', '')}|{len(rows)}",
            "ts": ts,
            "time": fmt_time(ts),
            "age": "",                      # filled by the live age tick
            "symbol": a.get("symbol", ""),
            "kind": alert_kind_label(a),
            "_kind_key": a.get("type") or "",
            "side": side_label(a),
            "detail": alert_detail(a),
            "text": a.get("text", ""),
            "_tone_class": tone_class(a),
        })
    rows.reverse()
    return rows


def filter_rows(rows, kinds, symbol):
    """Rows matching the selected kind keys and symbol.

    ``kinds=None`` and ``symbol=None`` mean unfiltered; an EMPTY kinds set means
    the user deselected everything and should see nothing."""
    out = []
    for r in rows or []:
        if kinds is not None and r.get("_kind_key") not in kinds:
            continue
        if symbol and r.get("symbol") != symbol:
            continue
        out.append(r)
    return out


def symbol_options(rows):
    """Sorted distinct symbols present in today's alerts (for the filter dropdown)."""
    return sorted({r.get("symbol") for r in rows or [] if r.get("symbol")})


def status_text(view):
    """Status line. Distinguishes a quiet day from a service that isn't publishing —
    on an empty table those look identical otherwise."""
    if not isinstance(view, dict) or not view:
        return "Waiting for the options service…"
    n = len(view.get("alerts") or [])
    date = view.get("date") or ""
    if not n:
        return f"No flow alerts yet today · {date}".rstrip(" ·")
    return f"{n} alert{'' if n == 1 else 's'} today · {date}".rstrip(" ·")


def flow_columns():
    spec = [("time", "Time"), ("age", "Age"), ("symbol", "Symbol"),
            ("kind", "Type"), ("side", "Side"), ("detail", "Detail"),
            ("text", "Alert")]
    return [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]


def render():
    """Build the Flow Alerts page: today's alerts newest-first, version-polling
    ``cache:options:flow_alerts``.

    Tier-1 (engine-free). Two cadences share one 2 s timer: the payload is re-read
    only when the cache VERSION moves, while the Age column is recomputed every
    tick against the rows already on screen — so age stays live without churning
    the table."""
    import bus_client
    from nicegui import run, ui

    from pages.ui_guard import guard, guard_async

    from .handoff import send_to_gamma
    from .theme import CARD, EYEBROW, LABEL, PAGE, QUASAR_INTERNAL_CSS

    ui.add_css(QUASAR_INTERNAL_CSS)
    state = {"version": None, "rows": [], "kinds": set(_KIND_LABEL), "symbol": None}

    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):
        with ui.column().classes(f"{CARD} w-full gap-2"):
            ui.label("Flow Alerts").classes(f"text-h6 {LABEL}")
            ui.label("Today's options-flow alerts, newest first — click a row for "
                     "dealer positioning").classes(EYEBROW)

            with ui.row().classes("items-center gap-3 flex-wrap pt-1"):
                kind_sel = ui.select(
                    dict(_KIND_LABEL), value=list(_KIND_LABEL), multiple=True,
                    label="Type").classes("w-72").props("dense outlined use-chips")
                symbol_sel = ui.select(["All"], value="All", label="Symbol") \
                    .classes("w-40").props("dense outlined")

            status = ui.label("Waiting for the options service…").classes(EYEBROW)
            table = ui.table(columns=flow_columns(), rows=[], row_key="id",
                             pagination={"rowsPerPage": 0}) \
                .classes("w-full flow-table").props("dense")
            table.add_slot("body-cell-side", _TONE_SLOT)
            table.add_slot("body-cell-text", _TONE_SLOT)

    def _apply_filters():
        table.rows = filter_rows(state["rows"], state["kinds"], state["symbol"])
        table.update()

    def _tick_age():
        now = _dt.datetime.now(tz=_CT_TZ)
        for r in state["rows"]:
            r["age"] = age_text(r.get("ts"), now)
        _apply_filters()

    def _paint(payload):
        state["rows"] = alert_rows(payload)
        opts = ["All"] + symbol_options(state["rows"])
        if list(symbol_sel.options) != opts:
            symbol_sel.options = opts
            # A symbol that stopped appearing today would otherwise leave the table
            # permanently empty with no visible cause.
            if state["symbol"] is not None and state["symbol"] not in opts:
                state["symbol"] = None
                symbol_sel.value = "All"
            symbol_sel.update()
        status.text = status_text(payload)
        _tick_age()

    @guard
    def _on_kind_change(e):
        state["kinds"] = set(e.value or [])
        _apply_filters()

    @guard
    def _on_symbol_change(e):
        state["symbol"] = None if e.value in (None, "All") else e.value
        _apply_filters()

    @guard
    def _on_row_click(e):
        row = e.args[1] if len(e.args) > 1 else None
        send_to_gamma((row or {}).get("symbol"))

    kind_sel.on_value_change(_on_kind_change)
    symbol_sel.on_value_change(_on_symbol_change)
    table.on("rowClick", _on_row_click)

    @guard_async
    async def _poll():
        # Cheap :ver probe off the loop; the full payload read only on a change.
        v = await run.io_bound(bus_client.read_version, VIEW)
        if v is not None and v != state["version"]:
            payload = await run.io_bound(bus_client.read, VIEW)
            if payload:
                state["version"] = v
                _paint(payload)
                return
        _tick_age()      # no new data — just keep the ages honest

    payload, version = bus_client.read_full(VIEW)
    if payload:
        state["version"] = version
        _paint(payload)
    ui.timer(2.0, _poll)
