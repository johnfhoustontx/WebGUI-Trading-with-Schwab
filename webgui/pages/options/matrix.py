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
from pages import busy as _busy
from pages import copy as _copy  # the ONE copy (pages/copy.py)
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

# Cboe extended-hours (GTH/Curb) eligibility marker. Deliberately MUTED — on a
# post-activation day only ~7 of ~45 rows carry it, and its job is quiet
# explanation, not attention: at 07:00 CT the other ~38 symbols still show the
# prior session, and without this marker that frozen majority reads as
# stale/broken rather than as "not eligible to trade yet".
ETH_BADGE_CLASS = ("bg-slate-700/50 text-slate-300 text-[10px] "
                   "font-medium px-1 py-0 ml-1 align-middle")


def signal_class(s):
    return _SIGNAL_CLASS.get(s, _SIGNAL_CLASS["neutral"])


def daypct_class(v):
    if not v:
        return "text-slate-400"
    return "text-emerald-400" if v > 0 else "text-rose-400"


# Summary-band stat chips (Buy / Neutral / Sell counts). Tailwind-first.
_SUM_CHIP = "px-3 py-1.5 rounded-lg text-sm font-semibold"
_SUM_BUY_CLASS = f"{_SUM_CHIP} bg-emerald-600/80 text-white"
_SUM_NEUTRAL_CLASS = f"{_SUM_CHIP} bg-slate-600/40 text-slate-200"
_SUM_SELL_CLASS = f"{_SUM_CHIP} bg-rose-600/80 text-white"


def signal_summary(payload):
    """Counts of rows by headline signal → ``{'buy': n, 'neutral': n, 'sell': n}``.

    An unknown/missing signal falls into the ``neutral`` bucket so the three counts
    always sum to the row total."""
    counts = {"buy": 0, "neutral": 0, "sell": 0}
    for r in (payload or {}).get("rows") or []:
        s = r.get("signal")
        counts[s if s in counts else "neutral"] += 1
    return counts


def matrix_columns():
    # Each label names the READING, not the field behind it.
    #
    # WARNING: "Call flow" / "Put flow" are not prices. Those cells hold call and
    # put ACCELERATION arrows (hot / cool / steady / flat), and a header reading
    # just "Call" gives a reader every reason to expect a price under it.
    #
    # WARNING: "Open signals" is a COUNT and "Signal" is the buy/neutral/sell
    # verdict. They sat two columns apart as "Sig" and "Signal". The COUNT is
    # what got renamed: /desk's board panel already prints SIGNAL for the
    # verdict, and a second name for one quantity is the drift these passes
    # exist to close.
    #
    # Symbol / Price / Net premium / Signal / Score are /desk's own words for
    # the same quantities its Opportunity Board panel shows. "$M" rides on the
    # premium label because this cell is a bare number in millions.
    spec = [
        ("symbol", "Symbol"), ("spot", "Price"), ("day_pct", "Day %"),
        ("trend", "Trend"), ("call_accel_disp", "Call flow"),
        ("put_accel_disp", "Put flow"), ("pc_ratio", "P/C"),
        ("net_prem_m", "Net premium $M"), ("gex_regime", "Vs flip"),
        ("n_signals", "Open signals"), ("n_alerts", "Flow alerts"),
        ("signal_label", "Signal"), ("hotness", "Score"),
    ]
    return [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]


def matrix_rows(payload):
    rows = []
    for r in (payload or {}).get("rows") or []:
        t_state = r.get("trend_state", "flat")
        rows.append({
            "symbol": r.get("symbol", ""),
            # Absent on a payload cached before the field existed (Redis keeps
            # the view across a service restart) → no badge.
            "_eth": bool(r.get("eth_eligible")),
            "_eth_class": ETH_BADGE_CLASS,
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
        return _copy.WAITING_OPTIONS
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
_SYMBOL_SLOT = r'''
  <q-td :props="props">
    <span>{{ props.value }}</span>
    <q-badge v-if="props.row._eth" :class="props.row._eth_class" label="ETH">
      <q-tooltip>Trades in Cboe extended hours (GTH 06:30-08:25 / Curb 15:00-15:15 CT)</q-tooltip>
    </q-badge>
  </q-td>
'''
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
            ui.label("Opportunity Board").classes(f"text-h6 {LABEL}")
            # This page has NO row click-through, so sorting is the only thing
            # a reader does here - and nothing on screen said so.
            ui.label("Every watchlist symbol on one row, ranked by how much is "
                     "going on. Click any column to re-sort.") \
                .classes(EYEBROW)
            # Summary band: signal counts across the whole grid.
            with ui.row().classes("items-center gap-2 flex-wrap pt-1"):
                ui.label("Signals").classes(EYEBROW + " pr-1")
                buy_chip = ui.label("Buy 0").classes(_SUM_BUY_CLASS)
                neutral_chip = ui.label("Neutral 0").classes(_SUM_NEUTRAL_CLASS)
                sell_chip = ui.label("Sell 0").classes(_SUM_SELL_CLASS)
            status = ui.label(_copy.WAITING_OPTIONS).classes(EYEBROW)
            table_box = ui.element("div").classes("w-full")
            with table_box:
                table = ui.table(columns=matrix_columns(), rows=[], row_key="symbol",
                                 pagination={"rowsPerPage": 0}) \
                    .classes("w-full matrix-table").props("dense")
            table.add_slot("body-cell-symbol", _SYMBOL_SLOT)
            table.add_slot("body-cell-signal_label", _SIGNAL_SLOT)
            table.add_slot("body-cell-day_pct", _DAYPCT_SLOT)
            table.add_slot("body-cell-trend", _TREND_SLOT)
            table.add_slot("body-cell-call_accel_disp", _CALL_SLOT)
            table.add_slot("body-cell-put_accel_disp", _PUT_SLOT)
            table.add_slot("body-cell-gex_regime", _REGIME_SLOT)

    # A read-only board: the only wait is the FIRST payload, and until it lands an
    # empty grid is indistinguishable from "every symbol is quiet".
    board_busy = _busy.build_busy(table_box, "Loading the board…")

    state = {"version": None}

    def _paint(payload):
        table.rows = matrix_rows(payload)
        table.update()
        summ = signal_summary(payload)
        buy_chip.text = f"Buy {summ['buy']}"
        neutral_chip.text = f"Neutral {summ['neutral']}"
        sell_chip.text = f"Sell {summ['sell']}"
        status.text = status_text(payload)
        board_busy.hide()

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
    else:
        board_busy.show()
    ui.timer(2.0, _poll)
