"""Options Matrix Display — Tier-1 reader of cache:options:matrix.

Pure builders (columns/rows/color maps) are module-level and NiceGUI-free for
testing. ``render()`` mounts the sortable table and version-polls the bus. This is
a Tier-1 page: it imports ONLY ``nicegui`` + ``bus_client`` + ``pages.*`` helpers —
no engine/service imports.

Built on the page kit (``pages/ui_kit.py``, the 2026-09-19 consistency
standard): the header line carries the title and the Updated stamp, the status
line carries counts only, and one region covers the table a publish replaces.
The stamp is ``stale``-aware here — the board is published on the autoscan
cadence, so its age is a real reading rather than a number that says nothing.
"""
from __future__ import annotations

from urllib.parse import quote as _quote

import bus_client
import shell as _shell
from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import ui_kit as kit
from nicegui import run, ui

from pages.ui_guard import guard, guard_async
from shared.symbols import clean_symbol

from .theme import EYEBROW

VIEW = "options:matrix"

# ── the symbol opens its Symbol Dossier ─────────────────────────────────────
# ``/symbol`` is served by the PRIVATE app only (it enqueues a fetch, which the
# public process refuses), while this board is also published as
# ``/opportunity``. So the affordance is drawn only where ``shell.can_navigate``
# says the route exists, and the click goes through ``shell.navigate_to``.
DOSSIER_ROUTE = "/symbol"        # pages/symbol.py ROUTE (not imported: that
                                 # module would join the public import closure)
DOSSIER_EVENT = "open_dossier"
# No colour of its own — the cell keeps the table's text colour; the dotted
# underline and the pointer are the affordance.
DOSSIER_LINK_CLASS = ("cursor-pointer underline decoration-dotted "
                      "underline-offset-4 hover:decoration-solid")


def dossier_route(raw):
    """``/symbol?symbol=<SYM>`` for a symbol ``shared.symbols.clean_symbol``
    accepts, else None. A row value never reaches a URL unchecked."""
    sym = clean_symbol(raw)
    return None if sym is None else f"{DOSSIER_ROUTE}?symbol={_quote(sym)}"


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
            # The allow-listed symbol the dossier link carries; None = no link.
            "_dossier": clean_symbol(r.get("symbol")),
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


def status_text(payload):
    """Status-bar text for a matrix payload: symbol count + session date,
    appending the ``error`` if the service degraded. Empty payload → the
    waiting note.

    ⚠ **No clock.** This line used to end "updated 5:03 PM" off the payload's
    own ``ts``. The header's Updated stamp reads the view's ``:ts`` side key —
    the time the publisher last confirmed the view current — so two clocks a
    few pixels apart could disagree about one board. The header owns the time;
    the ``_short_ts`` helper that formatted it went with the clock, and its
    UTC→Central conversion now lives in ``ui_kit.freshness``.
    """
    p = payload or {}
    if not p:
        return _copy.WAITING_OPTIONS
    n = len(p.get("rows") or [])
    parts = [f"{n} symbol" + ("" if n == 1 else "s")]
    if p.get("session_date"):
        parts.append(f"session {p['session_date']}")
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
# The private app's symbol cell: the same cell, with the symbol as a link to its
# dossier. A row whose symbol the allow-list refused (``_dossier`` None) stays
# plain text.
_SYMBOL_LINK_SLOT = (
    '\n  <q-td :props="props">\n'
    f'    <span v-if="props.row._dossier" class="{DOSSIER_LINK_CLASS}"\n'
    f'          @click.stop="() => $parent.$emit(\'{DOSSIER_EVENT}\', '
    'props.row._dossier)">{{ props.value }}'
    '<q-tooltip>Open the Symbol Dossier</q-tooltip></span>\n'
    '    <span v-else>{{ props.value }}</span>'
) + r'''
    <q-badge v-if="props.row._eth" :class="props.row._eth_class" label="ETH">
      <q-tooltip>Trades in Cboe extended hours (GTH 06:30-08:25 / Curb 15:00-15:15 CT)</q-tooltip>
    </q-badge>
  </q-td>
'''


def symbol_slot(linked):
    """The symbol cell's template: a dossier link where one can work, else the
    plain cell (the public screen, where ``/symbol`` does not exist)."""
    return _SYMBOL_LINK_SLOT if linked else _SYMBOL_SLOT


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


@guard
def _open_dossier(e):
    """The symbol cell's click. Re-checks the symbol — the event carries
    whatever the browser sent — and navigates through the seam, which is a
    no-op on an origin that does not serve ``/symbol``."""
    route = dossier_route(e.args if isinstance(e.args, str) else None)
    if route is not None:
        _shell.navigate_to(route)


def render():
    """Build the Options Matrix page body: a sortable table of every watchlist
    symbol, version-polling ``cache:options:matrix`` and repainting on change.

    Tier-1 (engine-free). The service already sorts rows hotness-desc, so the
    payload order leads with the hottest; column headers stay click-sortable.
    Graceful-empty: a cold service leaves the "Waiting…" status until the first
    publish.
    """
    # No description line: the standard keeps the body to the header, the status
    # line and the table. What it said — one row per watchlist symbol ranked by
    # how much is going on, that any column header re-sorts, and that a symbol
    # opens its dossier — lives in the page help
    # (``page_help.HELP_MD["/options/matrix"]``), which the public screen does
    # not mount and so cannot promise a dossier this origin has no route for.
    # The affordance itself stays origin-conditional: the linked cell is drawn
    # only where ``/symbol`` exists, and it names itself in its own tooltip.
    linked = _shell.can_navigate(DOSSIER_ROUTE)
    with kit.page():
        kit.header("Opportunity Board", view=VIEW, stale=True)
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            status = kit.status_line(_copy.WAITING_OPTIONS)
            ui.space()
            # Summary band: signal counts across the whole grid.
            ui.label("Signals").classes(EYEBROW + " pr-1")
            buy_chip = ui.label("Buy 0").classes(_SUM_BUY_CLASS)
            neutral_chip = ui.label("Neutral 0").classes(_SUM_NEUTRAL_CLASS)
            sell_chip = ui.label("Sell 0").classes(_SUM_SELL_CLASS)
        # A read-only board: the only wait is the FIRST payload, and until it
        # lands an empty grid is indistinguishable from "every symbol is quiet".
        board = kit.region("Loading the board…")
        with board.content:
            table = kit.table(matrix_columns(), row_key="symbol", numeric=(
                "spot", "day_pct", "pc_ratio", "net_prem_m", "n_signals",
                "n_alerts", "hotness"))
    table.add_slot("body-cell-symbol", symbol_slot(linked))
    if linked:
        table.on(DOSSIER_EVENT, _open_dossier)
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
        summ = signal_summary(payload)
        buy_chip.text = f"Buy {summ['buy']}"
        neutral_chip.text = f"Neutral {summ['neutral']}"
        sell_chip.text = f"Sell {summ['sell']}"
        status.text = status_text(payload)
        board.busy.hide()

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
        board.busy.show()
    ui.timer(2.0, _poll)
