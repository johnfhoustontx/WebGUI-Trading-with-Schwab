"""Swing Scanner page (Tier-3 reader).

A dedicated user-parameterized swing credit-spread scan with custom DTE / delta /
credit gates. This page holds **no engine call**: the scan pipeline
(``screen_spreads`` + ``build_iron_condors`` + ``score_all_signals``) lives in
``services/options_svc/compute.swing_scan``, and the cross-app ``scoring``
collision guard is gone (the service process loads no sentiment code). The Scan
button enqueues a ``swing_scan`` command (with the user's inputs as args) onto
the Redis bus; the service runs it and writes the result under
``cache:options:swing``; this page only **reads** that payload and formats it.

Cache view read: ``options:swing`` →
``{signals:[...], view:{...}, filtered_out: int, symbol, params}``
where each signal is the NORMALIZED multi-strategy shape (LONG_CALL/.../IRON_CONDOR
with a ``legs`` list, ``family``, ``bias``, ``net_debit``/``net_credit``,
``breakevens``, …). Results render via the multi-strategy ``strategy_table`` columns/
rows + an inferred-view banner + the shared Trade detail panel + legs-aware handoff
row actions. A fetch-free version-poll ``ui.timer`` repaints when the bus cache
version changes (graceful-empty when the service is cold).
"""
import bus_client
from pages import busy as _busy
from nicegui import ui

from pages.ui_guard import guard

from .inputs import bind_symbol_load, select_all_on_focus
from .theme import BTN_3D

from . import detail, handoff, strategy_table


def pct_to_fraction(value):
    """Convert a percent UI value to a fraction (screen_spreads wants 0.10, not 10)."""
    return float(value) / 100.0


def status_text(payload, n_rows):
    """Status line under the table. Pure.

    The service drops candidates that fail its quality cut (score below the bar
    or an excluded grade), so a scan that worked perfectly can still render an
    empty table. Naming the drop count separates that from "the scan found
    nothing" — otherwise a bare "0 swing signals." reads as a broken scan. The
    note is omitted when nothing was dropped, so a clean scan stays quiet.
    """
    if not payload:
        return ""
    line = f"{n_rows} swing signals."
    dropped = (payload or {}).get("filtered_out") or 0
    if dropped:
        line += f" {dropped} below the quality bar."
    return line


# Strategy-family checkbox options (Diagonal is a later phase — omitted).
_FAMILY_OPTIONS = {"DIRECTIONAL": "Directional", "VERTICAL": "Spreads",
                   "NEUTRAL": "Neutral"}


def render():
    """Multi-strategy Swing Scanner: inputs + Scan + view banner + strategy table."""
    # No page title — the tab strip names the page (2026-07-11 dead-space cleanup).

    with ui.column().classes("w-full gap-2"):
        # Primary controls span the FULL page width, so symbol + DTE + strategies
        # + Scan all sit on one line (the detail panel no longer squeezes them).
        with ui.row().classes("items-end gap-3 flex-wrap"):
            symbol_in = select_all_on_focus(
                ui.input("Symbol", value="SPY").props("autofocus").classes("w-28"))
            dte_min = ui.number("DTE min", value=0, min=0).classes("w-24")
            dte_max = ui.number("DTE max", value=120, min=1).classes("w-24")
            with ui.column().classes("gap-0"):
                ui.label("Strategies").classes("text-xs text-[#8794b4]")
                with ui.row().classes("items-center gap-3 no-wrap"):
                    family_cbs = {
                        code: ui.checkbox(label, value=True).props("dense")
                        for code, label in _FAMILY_OPTIONS.items()
                    }
            scan_btn = ui.button("Scan", icon="search", color=None).props("no-caps").classes(BTN_3D)
            status = ui.label("").classes("opacity-70")
        # Credit-spread-only gates (collapsed; constrain PCS/CCS only).
        with ui.expansion("Advanced — credit spreads").classes("w-full"):
            with ui.row().classes("items-end gap-2 flex-wrap"):
                put_dmin = ui.number("Put Δ min", value=-0.20, format="%.2f").classes("w-24")
                put_dmax = ui.number("Put Δ max", value=-0.10, format="%.2f").classes("w-24")
                call_dmin = ui.number("Call Δ min", value=0.10, format="%.2f").classes("w-24")
                call_dmax = ui.number("Call Δ max", value=0.20, format="%.2f").classes("w-24")
                mincr = ui.number("Min credit %", value=10.0, format="%.1f").classes("w-28")
        banner = ui.label(strategy_table.view_banner_text(None)).classes("opacity-80 text-sm")
        # Results table + shared detail panel side by side, below the controls.
        results_row = ui.row().classes("w-full no-wrap gap-4 items-start")
        with results_row:
            table = ui.table(columns=strategy_table.strategy_columns(), rows=[],
                             row_key="id").classes("flex-grow min-w-0")
            detail_panel = detail.render()
    # A scan can take seconds; until it lands the table still shows the PREVIOUS
    # symbol's candidates, which reads as a result rather than as stale data.
    scan_busy = _busy.build_busy(results_row, "Scanning…")

    by_id: dict = {}
    # Last-seen bus cache version for the fetch-free repaint timer.
    seen = {"version": None}

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            detail_panel.update(strategy_table.detail_signal(sig))

    table.on("rowClick", _select)
    # per-row buttons: Calculator / Paper (gated) / Expected Move — legs-aware.
    handoff.add_strategy_row_actions(table, lambda row: by_id.get(row.get("id")))
    # Quality-colored composite score chip + bias colored by direction.
    table.add_slot('body-cell-composite_score', r'''
      <q-td :props="props">
        <q-badge :class="props.row._score_class + ' text-[#111]'" :label="props.value ?? '—'"/>
      </q-td>
    ''')
    table.add_slot('body-cell-bias', r'''
      <q-td :props="props">
        <span :class="props.row._bias_class">{{ props.value || '—' }}</span>
      </q-td>
    ''')
    # A naked short's max loss is a margin proxy, not a cap — say so on the cell.
    table.add_slot('body-cell-max_loss', r'''
      <q-td :props="props">
        {{ props.value }}
        <q-badge v-if="props.row._undefined_risk" label="undefined risk"
                 class="q-ml-xs text-[9px] px-1 py-0 bg-[#b71c1c] text-white"/>
      </q-td>
    ''')
    # Quality grade colored by pass/fail band + a tooltip with the reason.
    table.add_slot('body-cell-grade', r'''
      <q-td :props="props">
        <span :class="props.row._grade_class">{{ props.value || '—' }}</span>
        <q-tooltip v-if="props.row.grade_reason">{{ props.row.grade_reason }}</q-tooltip>
      </q-td>
    ''')

    def _populate(payload):
        """Paint the table + detail map + view banner from a swing-result dict."""
        payload = payload or {}
        signals = payload.get("signals") or []
        by_id.clear()
        for s in signals:
            if s.get("id"):
                by_id[s["id"]] = s
        table.rows = strategy_table.strategy_rows(signals)
        table.update()
        banner.text = strategy_table.view_banner_text(payload.get("view"))
        status.text = status_text(payload, len(table.rows))
        scan_busy.hide()

    @guard
    def _request_scan():
        params = {
            "symbol": symbol_in.value.strip().upper(),
            "dte_min": int(dte_min.value),
            "dte_max": int(dte_max.value),
            "families": [code for code, cb in family_cbs.items() if cb.value],
            "put_d_min": float(put_dmin.value),
            "put_d_max": float(put_dmax.value),
            "call_d_min": float(call_dmin.value),
            "call_d_max": float(call_dmax.value),
            "min_cr_fraction": pct_to_fraction(mincr.value),
        }
        bus_client.request("options", {"type": "swing_scan", "args": params})
        if not params["families"]:
            # Falsy families ⇒ the service scans ALL families (the contract);
            # surface it so an all-unchecked group isn't a silent "scan everything".
            ui.notify("No strategies selected — scanning all.", type="info")
        ui.notify("Scanning — results appear when the scan finishes.")
        status.text = "Scanning…"
        scan_busy.show(f"Scanning {params['symbol']}…")

    scan_btn.on_click(_request_scan)
    # Enter OR tab/click-out of the Symbol field triggers the scan (mirrors the Scan
    # button), deduped so tabbing through an unchanged symbol won't re-scan.
    bind_symbol_load(symbol_in, _request_scan)

    # A symbol handed over from the Trade Plan: seed the input and scan at once,
    # the same one-shot pattern Dealer Positioning uses.
    _handoff_sym = handoff.take_pending_swing()
    if _handoff_sym:
        symbol_in.value = _handoff_sym
        _request_scan()

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["version"] = bus_client.read_version("options:swing")
    _populate(bus_client.read("options:swing") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps it when a requested
        # swing scan finishes.
        version = bus_client.read_version("options:swing")
        if version == seen["version"]:
            return
        seen["version"] = version
        _populate(bus_client.read("options:swing") or {})

    ui.timer(2.0, _maybe_repaint)
