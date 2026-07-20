"""Options Matrix Display — Tier-1 reader of cache:options:matrix.

Pure builders (columns/rows/color maps) are module-level and NiceGUI-free for
testing. ``render()`` mounts the sortable table and version-polls the bus. This is
a Tier-1 page: it imports ONLY ``nicegui`` + ``bus_client`` + ``pages.*`` helpers —
no engine/service imports.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import bus_client
from nicegui import run, ui

from pages.ui_guard import guard_async

from .theme import CARD, EYEBROW, LABEL, PAGE, QUASAR_INTERNAL_CSS

VIEW = "options:matrix"

# The payload ``ts`` is emitted in UTC by the service; the app shows trading times
# in Central (America/Chicago), matching the rest of the UI.
_CT_TZ = ZoneInfo("America/Chicago")

_SIGNAL_CLASS = {
    "buy": "bg-emerald-600/80 text-white",
    "neutral": "bg-slate-600/40 text-slate-200",
    "sell": "bg-rose-600/80 text-white",
}
_REGIME_CLASS = {
    "above": "text-emerald-400",
    "below": "text-rose-400",
    "na": "text-slate-500",
}
_TREND_CLASS = {
    "strong_up": "text-emerald-400", "up": "text-emerald-300",
    "flat": "text-slate-400",
    "down": "text-rose-300", "strong_down": "text-rose-400",
}
_TREND_ARROW = {
    "strong_up": "▲▲", "up": "▲", "flat": "▬",
    "down": "▼", "strong_down": "▼▼",
}
_ACCEL_CLASS = {"hot": "text-emerald-400", "cool": "text-rose-400",
                "steady": "text-slate-400", "flat": "text-slate-500"}
_ACCEL_ARROW = {"hot": "▲", "cool": "▼", "steady": "▬", "flat": "·"}
_SIGNAL_LABEL = {"buy": "Buy", "neutral": "Neutral", "sell": "Sell"}


def signal_class(s):
    return _SIGNAL_CLASS.get(s, _SIGNAL_CLASS["neutral"])


def daypct_class(v):
    if not v:
        return "text-slate-400"
    return "text-emerald-400" if v > 0 else "text-rose-400"


def matrix_columns():
    spec = [
        ("symbol", "Ticker"), ("spot", "Spot"), ("day_pct", "Day %"),
        ("trend", "Trend"), ("call_accel_disp", "Call"), ("put_accel_disp", "Put"),
        ("pc_ratio", "P/C"), ("net_prem_m", "Net $M"), ("gex_regime", "GEX"),
        ("n_signals", "Sig"), ("n_alerts", "Flow"), ("signal_label", "Signal"),
        ("hotness", "Hot"),
    ]
    return [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]


def matrix_rows(payload):
    rows = []
    for r in (payload or {}).get("rows") or []:
        t_state = r.get("trend_state", "flat")
        rows.append({
            "symbol": r.get("symbol", ""),
            "spot": r.get("spot"),
            "day_pct": r.get("day_pct"),
            "_daypct_class": daypct_class(r.get("day_pct")),
            "trend": _TREND_ARROW.get(t_state, "▬"),
            "_trend_class": _TREND_CLASS.get(t_state, "text-slate-400"),
            "call_accel_disp": _ACCEL_ARROW.get(r.get("call_accel"), "·"),
            "_call_class": _ACCEL_CLASS.get(r.get("call_accel"), "text-slate-500"),
            "put_accel_disp": _ACCEL_ARROW.get(r.get("put_accel"), "·"),
            "_put_class": _ACCEL_CLASS.get(r.get("put_accel"), "text-slate-500"),
            "pc_ratio": r.get("pc_ratio"),
            "net_prem_m": r.get("net_prem_m"),
            "gex_regime": r.get("gex_regime", "na"),
            "_regime_class": _REGIME_CLASS.get(r.get("gex_regime"), "text-slate-500"),
            "n_signals": r.get("n_signals", 0),
            "n_alerts": r.get("n_alerts", 0),
            "signal_label": _SIGNAL_LABEL.get(r.get("signal"), "Neutral"),
            "_signal_class": signal_class(r.get("signal")),
            "hotness": r.get("hotness", 0),
        })
    return rows


def _short_ts(iso):
    """A UTC ISO timestamp -> short Central-time clock like '1:32 PM'; '' on None/failure.

    The service emits ``ts`` in UTC (tz-aware); convert to Central before formatting so
    the displayed 'updated' time matches the user's trading timezone. A naive timestamp
    (no offset) is assumed to be UTC.
    """
    if not iso:
        return ""
    try:
        dt = _dt.datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_CT_TZ).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return ""


def status_text(payload):
    """Status-bar text for a matrix payload: symbol count + session date + updated
    clock, appending the ``error`` if the service degraded. Empty payload → the
    waiting note."""
    p = payload or {}
    if not p:
        return "Waiting for the options service…"
    n = len(p.get("rows") or [])
    parts = [f"{n} symbol" + ("" if n == 1 else "s")]
    if p.get("session_date"):
        parts.append(f"session {p['session_date']}")
    ts = _short_ts(p.get("ts"))
    if ts:
        parts.append(f"updated {ts}")
    text = " · ".join(parts)
    if p.get("error"):
        text += f" · {p['error']}"
    return text


# ── Colored-cell slots (Tailwind-first): each binds a stamped ``_*_class`` field
# from ``matrix_rows`` via ``:class`` — no inline style. Raw ``<q-td>`` templates
# (the scanner.py add_slot idiom).
_SIGNAL_SLOT = r'''
  <q-td :props="props">
    <q-badge :class="props.row._signal_class + ' px-2 py-1'" :label="props.value"/>
  </q-td>
'''
_DAYPCT_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._daypct_class">{{ props.value == null ? '—' : props.value + '%' }}</span>
  </q-td>
'''
_TREND_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._trend_class">{{ props.value }}</span>
  </q-td>
'''
_CALL_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._call_class">{{ props.value }}</span>
  </q-td>
'''
_PUT_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._put_class">{{ props.value }}</span>
  </q-td>
'''
_REGIME_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._regime_class">{{ props.value }}</span>
  </q-td>
'''


def render():
    """Build the Options Matrix page body: a sortable table of every watchlist
    symbol, version-polling ``cache:options:matrix`` and repainting on change.

    Tier-1 (engine-free). The service already sorts rows hotness-desc, so the
    payload order leads with the hottest; column headers stay click-sortable.
    Graceful-empty: a cold service leaves the "Waiting…" status until the first
    publish.
    """
    ui.add_css(QUASAR_INTERNAL_CSS)
    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):
        with ui.column().classes(f"{CARD} w-full gap-2"):
            ui.label("Matrix").classes(f"text-h6 {LABEL}")
            ui.label("Every watchlist stock at a glance — sorted by hotness") \
                .classes(EYEBROW)
            status = ui.label("Waiting for the options service…").classes(EYEBROW)
            table = ui.table(columns=matrix_columns(), rows=[], row_key="symbol",
                             pagination={"rowsPerPage": 0}) \
                .classes("w-full matrix-table").props("dense")
            table.add_slot("body-cell-signal_label", _SIGNAL_SLOT)
            table.add_slot("body-cell-day_pct", _DAYPCT_SLOT)
            table.add_slot("body-cell-trend", _TREND_SLOT)
            table.add_slot("body-cell-call_accel_disp", _CALL_SLOT)
            table.add_slot("body-cell-put_accel_disp", _PUT_SLOT)
            table.add_slot("body-cell-gex_regime", _REGIME_SLOT)

    state = {"version": None}

    def _paint(payload):
        table.rows = matrix_rows(payload)
        table.update()
        status.text = status_text(payload)

    @guard_async
    async def _poll():
        # Cheap :ver probe off the loop; the full payload read only on a change.
        v = await run.io_bound(bus_client.read_version, VIEW)
        if v is None or v == state["version"]:
            return
        payload = await run.io_bound(bus_client.read, VIEW)
        if payload:
            state["version"] = v
            _paint(payload)

    payload, version = bus_client.read_full(VIEW)
    if payload:
        state["version"] = version
        _paint(payload)
    ui.timer(2.0, _poll)
