"""Strategy Finder page (``/options/swing``) - a Tier-3 reader.

One symbol, every structure the service can build for it, ranked on one score.
This page holds **no engine call**: the Scan button enqueues a ``swing_scan``
command (with the user's inputs as args) onto the Redis bus, the options service
runs ``compute.swing_scan`` and writes ``cache:options:swing``, and this page only
**reads** that payload.

Cache view read: ``options:swing`` →
``{signals:[...], view:{...}, filtered_out, vol_filtered, symbol, params}``; each
signal is the NORMALIZED multi-strategy shape (``legs``, ``net_debit`` /
``net_credit``, ``max_profit`` / ``max_loss``, ``pop_pct`` …) plus, since the
2026-09-13 redesign, ``group`` (which chip it belongs to) and ``payoff_curve``
(the shape drawn on each card and row). Both are optional: a payload without them
renders with no shapes and no chips.

Top to bottom: a scan bar (symbol, expiry presets, risk style, Advanced fields),
a summary strip, instant-filter strategy chips, up to four top-pick cards, and a
slim ranked list beside the shared Trade detail panel. Every sentence and number
those widgets show comes from the PURE ``finder_view`` module; this file is
widgets and wiring. Design:
``docs/plans/2026-09-13-strategy-finder-redesign-design.md``.
"""
import bus_client
from nicegui import ui

from pages import busy as _busy
from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages.ui_guard import guard

from . import detail, handoff, strategy_table
from . import finder_view as fv
from .inputs import bind_symbol_load, select_all_on_focus
from .scanner import score_zone_class
from .theme import (BADGE_ACCENT, BADGE_MUTED, BTN, BTN_3D, CARD, EYEBROW, LABEL,
                    MUTED, THEME, TXT_NEG, TXT_POS)

# Before any scan has published for this session.
EMPTY_PROMPT = "Enter a symbol and press Scan to rank every strategy for it."

# The busy backstop fired but the scan is still running: a slow scan still lands
# later, so this says so rather than calling it failed.
SCAN_SLOW = ("The scan is taking longer than expected — "
             "results will appear when it finishes.")

# The seven build groups, by chip order. Kept under its old name because
# test_strategy_table pins these labels against the Calculator's group names.
_FAMILY_OPTIONS = dict(fv.GROUPS)

# Quasar-internal escape hatch (the ONE ui.add_css this page injects):
# - the app-wide TABLE_CSS caps every table body at 65vh; this list grows with the
#   page instead (the old short scroll box hid most of a scan). Accepted cost: the
#   sticky header now scrolls away with the page, since the table body no longer
#   scrolls on its own;
# - the Advanced expansion header's q-focus-helper paints a full-width grey slab
#   on hover/focus, which no prop turns off.
FINDER_CSS = (".finder-table .q-table__middle { max-height: none; }\n"
              ".finder-advanced .q-focus-helper { display: none; }")

_PALETTE = THEME["palette"]

# The split bar's two fills - the app's P/L red and green (simulator payoff) -
# and the tick marking its zero point, in the theme's muted text colour.
_LOSS_FILL = "bg-[#f87171]"
_PROFIT_FILL = "bg-[#34d399]"
SPLIT_TICK = f"w-[2px] h-3 shrink-0 bg-[{_PALETTE['muted']}]"
_CHIP = "px-3 py-1 text-xs"
_PILL = "px-2 py-0.5 text-xs"

# Segmented pill group (Expiry presets, Risk style) - the chips' look, in a
# bordered box the height of a dense outlined input so one row lines up.
SEG_BOX = (f"inline-flex flex-nowrap items-center gap-0.5 p-[3px] h-10 "
           f"rounded-[8px] border border-[{_PALETTE['input_border']}]")
SEG_ON = f"{BADGE_ACCENT} px-2.5 min-h-[30px] text-xs font-normal"
SEG_OFF = (f"bg-transparent text-[{_PALETTE['muted']}] hover:text-[{_PALETTE['title']}] "
           "rounded-[6px] px-2.5 min-h-[30px] text-xs font-normal")
_FIELD_PROPS = "dense outlined"


class _Segmented:
    """A row of pill buttons, at most one active; ``value`` None = none active.

    Replaces ``ui.toggle``, whose QBtnToggle internals (bold white labels, a solid
    Quasar-colour active block) no prop can restyle to the chips' look. Setting
    ``value`` from code repaints only; a click repaints and calls ``on_change``.
    """

    def __init__(self, options, value, on_change):
        self._on_change = on_change
        self._value = None
        self._buttons = {}
        with ui.element("div").classes(SEG_BOX) as self.element:
            for opt in options:
                btn = ui.button(opt, color=None).props("no-caps dense unelevated") \
                    .classes(SEG_OFF)
                btn.on("click", lambda _e, o=opt: self._clicked(o))
                self._buttons[opt] = btn
        self.value = value

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, new):
        self._value = new if new in self._buttons else None
        for opt, btn in self._buttons.items():
            if opt == self._value:
                btn.classes(remove=SEG_OFF, add=SEG_ON)
            else:
                btn.classes(remove=SEG_ON, add=SEG_OFF)

    @guard
    def _clicked(self, opt):
        if opt == self._value:
            return
        self.value = opt
        self._on_change(opt)


def pct_to_fraction(value):
    """Convert a percent UI value to a fraction (screen_spreads wants 0.10, not 10)."""
    return float(value) / 100.0


def scan_params(symbol, dte_min, dte_max, bands, min_credit_pct):
    """The ``swing_scan`` command args.

    No ``families``: every scan builds all seven groups (the service's ``None``
    default) and the chips filter them on the page, instantly and without a
    rescan.
    """
    return {
        "symbol": (symbol or "").strip().upper(),
        "dte_min": int(dte_min),
        "dte_max": int(dte_max),
        "put_d_min": float(bands["put_d_min"]),
        "put_d_max": float(bands["put_d_max"]),
        "call_d_min": float(bands["call_d_min"]),
        "call_d_max": float(bands["call_d_max"]),
        "min_cr_fraction": pct_to_fraction(min_credit_pct),
    }


def scanning_text(symbol):
    """What the placeholder cards say while a scan runs - the symbol being
    scanned, never the previous one."""
    sym = (symbol or "").strip().upper()
    return f"Scanning {sym}…" if sym else "Scanning…"


def finder_rows(signals):
    """``finder_view.finder_rows`` with the real score / grade classes and the
    ``_PAPER_TYPES`` gate, which live in widget-importing modules."""
    return fv.finder_rows(signals, score_class=score_zone_class,
                          grade_class=strategy_table.grade_class,
                          paper_types=strategy_table._PAPER_TYPES)


def card_view(sig):
    """A top-pick card's facts: ``finder_view.card_facts`` plus the legs line, the
    score / grade classes and the paper gate."""
    out = fv.card_facts(sig)
    s = sig or {}
    out["legs"] = strategy_table.legs_summary(s.get("legs"))
    out["score_class"] = score_zone_class(s.get("composite_score"))
    out["grade_class"] = strategy_table.grade_class(s.get("grade"))
    out["allow_paper"] = s.get("type") in strategy_table._PAPER_TYPES
    return out


# ------------------------------------------------------------------ table slots

# v-html skips the DOMPurify pass ui.html gets. Safe here only because
# ``finder_view.payoff_svg`` builds the string from formatted numbers and fixed
# colour constants - no text from the payload - pinned by test_finder_view.
_STRATEGY_SLOT = r'''
<q-td :props="props">
  <div class="flex flex-nowrap items-center gap-2">
    <span v-if="props.row._payoff_svg" class="inline-flex shrink-0"
          v-html="props.row._payoff_svg"></span>
    <span>{{ props.row.strategy || '—' }}</span>
  </div>
</q-td>
'''

_SCORE_SLOT = r'''
<q-td :props="props">
  <q-badge :class="props.row._score_class + ' text-[#111]'" :label="props.value ?? '—'"/>
</q-td>
'''

_EXPIRY_SLOT = r'''
<q-td :props="props">{{ props.row.expiry }}</q-td>
'''

_MAX_PROFIT_SLOT = r'''
<q-td :props="props">{{ props.row.max_profit }}</q-td>
'''

# A naked short's max loss is a margin proxy, not a cap - say so on the cell.
_MAX_LOSS_SLOT = r'''
<q-td :props="props">
  {{ props.row.max_loss }}
  <q-badge v-if="props.row._undefined_risk" label="undefined risk"
           class="q-ml-xs text-[9px] px-1 py-0 bg-[#b71c1c] text-white"/>
</q-td>
'''

_POP_SLOT = r'''
<q-td :props="props">
  <div v-if="props.row._pop" class="flex flex-nowrap items-center gap-2">
    <div class="w-16 h-1.5 rounded overflow-hidden bg-white/10">
      <div :class="'h-full ' + props.row._pop.class + ' ' + props.row._pop_fill"></div>
    </div>
    <span class="text-xs">{{ props.row.pop }}</span>
  </div>
  <span v-else>—</span>
</q-td>
'''

_GRADE_SLOT = r'''
<q-td :props="props">
  <span :class="props.row._grade_class">{{ props.value || '—' }}</span>
  <q-tooltip v-if="props.row.grade_reason">{{ props.row.grade_reason }}</q-tooltip>
</q-td>
'''


def render():
    """The Strategy Finder: scan bar, summary, chips, top picks, ranked list."""
    # No page title - the tab strip names the page (2026-07-11 dead-space cleanup).
    ui.add_css(FINDER_CSS)
    sync = {"on": False}        # True while code writes linked controls

    with ui.column().classes("w-full gap-3"):
        # 1 - Scan bar. One bottom-aligned row that wraps: every group carries the
        # same EYEBROW label above a 40px-tall control (dense outlined inputs and
        # the pill boxes share that height), so the labels and the controls each
        # sit on one line. Scan is last.
        with ui.column().classes(f"{CARD} w-full gap-1"):
            with ui.row().classes("w-full items-end gap-x-5 gap-y-3 flex-wrap"):
                with ui.column().classes("gap-1"):
                    ui.label("Symbol").classes(EYEBROW)
                    symbol_in = select_all_on_focus(
                        ui.input(value="SPY")
                        .props(f"{_FIELD_PROPS} autofocus aria-label=Symbol")
                        .classes("w-28"))
                with ui.column().classes("gap-1"):
                    ui.label("Expiry").classes(EYEBROW)
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        expiry_seg = _Segmented(
                            [label for label, _lo, _hi in fv.EXPIRY_PRESETS],
                            fv.expiry_preset_for(0, 120),
                            lambda v: _on_expiry_choice(v))
                        dte_min = ui.number(value=0, min=0) \
                            .props(f'{_FIELD_PROPS} aria-label="DTE min"').classes("w-20")
                        ui.label("to").classes(f"text-xs {MUTED}")
                        dte_max = ui.number(value=120, min=1) \
                            .props(f'{_FIELD_PROPS} aria-label="DTE max"').classes("w-20")
                        ui.label("days").classes(f"text-xs {MUTED}")
                with ui.column().classes("gap-1"):
                    ui.label("Risk style").classes(EYEBROW)
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        risk_seg = _Segmented(list(fv.RISK_STYLES), fv.RISK_DEFAULT,
                                              lambda v: _on_risk_choice(v))
                        # Read-only: shown when the Advanced fields match no style.
                        # It is never an option, so it can never be "chosen".
                        custom_badge = ui.label(fv.RISK_CUSTOM) \
                            .classes(f"{BADGE_MUTED} {_PILL}")
                        custom_badge.set_visibility(False)
                scan_btn = ui.button("Scan", icon="search", color=None) \
                    .props("no-caps").classes(f"{BTN_3D} h-10 px-4")
                status = ui.label("").classes(f"text-sm {MUTED} self-center")
            # Collapsed and deliberately quiet: a small muted header, no hover slab
            # (FINDER_CSS). The delta bands govern every SHORT leg the Finder sells
            # (the credit spreads, the naked short put/call, the short strangle and
            # the covered call or collar call); the credit floor is the spreads' alone.
            with ui.expansion("Advanced — delta bands and credit floor") \
                    .classes("finder-advanced w-full") \
                    .props(f'dense header-class="px-1 min-h-[32px] text-xs '
                           f'text-[{_PALETTE["muted"]}]"'):
                start = fv.risk_bands(fv.RISK_DEFAULT)
                with ui.row().classes("items-end gap-2 flex-wrap pt-1"):
                    put_dmin = ui.number("Put Δ min", value=start["put_d_min"],
                                         format="%.2f").classes("w-24")
                    put_dmax = ui.number("Put Δ max", value=start["put_d_max"],
                                         format="%.2f").classes("w-24")
                    call_dmin = ui.number("Call Δ min", value=start["call_d_min"],
                                          format="%.2f").classes("w-24")
                    call_dmax = ui.number("Call Δ max", value=start["call_d_max"],
                                          format="%.2f").classes("w-24")
                    mincr = ui.number("Min credit %", value=10.0,
                                      format="%.1f").classes("w-28")

        # 2 - Summary strip (hidden until a scan has published).
        summary_box = ui.row().classes(f"{CARD} w-full items-center gap-3 flex-wrap")
        summary_box.set_visibility(False)
        # 3 - Strategy chips.
        chips_row = ui.row().classes("w-full items-center gap-2 flex-wrap")
        # 4 - Top picks: four across, two on a narrow screen, one on a phone.
        picks_grid = ui.element("div").classes(
            "grid w-full gap-3 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4")
        # 5 + 6 - The ranked list beside the shared detail panel.
        with ui.row().classes("w-full no-wrap gap-4 items-start"):
            list_box = ui.column().classes("flex-grow min-w-0 gap-2 min-h-[120px]")
            with list_box:
                empty_line = ui.label(EMPTY_PROMPT).classes(f"text-sm {MUTED}")
                table = ui.table(columns=fv.finder_columns(), rows=[], row_key="id") \
                    .classes("finder-table w-full")
            detail_panel = detail.render()
    # The list keeps its width until there is something to show.
    detail_panel.collapse()
    scan_busy = _busy.build_busy(list_box, "Scanning…")

    by_id: dict = {}
    # scanning: the seq of the scan whose busy backstop is armed.
    # scan_symbol: the symbol a scan is waiting on (None = nothing waiting); only
    # a payload for it may replace the placeholders.
    state = {"payload": None, "symbol": None, "active": None, "version": None,
             "scanning": None, "scan_seq": 0, "scan_symbol": None}

    # --------------------------------------------------------------- controls

    @guard
    def _on_expiry_choice(value):
        if sync["on"]:
            return
        rng = fv.expiry_range_for(value)
        if rng is None:            # a cleared toggle writes nothing
            return
        sync["on"] = True
        try:
            dte_min.value, dte_max.value = rng
        finally:
            sync["on"] = False

    @guard
    def _refresh_expiry_toggle(_e=None):
        if sync["on"]:
            return
        expiry_seg.value = fv.expiry_preset_for(dte_min.value, dte_max.value)

    def _bands():
        return {"put_d_min": put_dmin.value, "put_d_max": put_dmax.value,
                "call_d_min": call_dmin.value, "call_d_max": call_dmax.value}

    @guard
    def _on_risk_choice(value):
        if sync["on"]:
            return
        bands = fv.bands_for_choice(value)     # never risk_bands("Custom")
        if bands is None:
            return
        sync["on"] = True
        try:
            put_dmin.value = bands["put_d_min"]
            put_dmax.value = bands["put_d_max"]
            call_dmin.value = bands["call_d_min"]
            call_dmax.value = bands["call_d_max"]
        finally:
            sync["on"] = False
        custom_badge.set_visibility(False)

    @guard
    def _refresh_risk_toggle(_e=None):
        if sync["on"]:
            return
        b = _bands()
        value = fv.risk_toggle_value(b["put_d_min"], b["put_d_max"],
                                     b["call_d_min"], b["call_d_max"])
        custom_badge.set_visibility(value is None)
        risk_seg.value = value

    dte_min.on_value_change(_refresh_expiry_toggle)
    dte_max.on_value_change(_refresh_expiry_toggle)
    for field in (put_dmin, put_dmax, call_dmin, call_dmax):
        field.on_value_change(_refresh_risk_toggle)

    # -------------------------------------------------------------- selection

    @guard
    def _select_signal(sig):
        if not sig:
            return
        # Every selection opens the panel: a click that updated a collapsed panel
        # invisibly would read as a click that did nothing.
        detail_panel.open()
        detail_panel.update(strategy_table.detail_signal(sig))

    def _on_row_click(event):
        args = event.args
        row = args[1] if isinstance(args, list) and len(args) > 1 else args
        if isinstance(row, dict):
            _select_signal(by_id.get(row.get("id")))

    table.on("rowClick", _on_row_click)
    # Per-row Calculator / Paper (gated) / Expected Move - legs-aware.
    handoff.add_strategy_row_actions(table, lambda row: by_id.get((row or {}).get("id")))
    table.add_slot("body-cell-strategy", _STRATEGY_SLOT)
    table.add_slot("body-cell-composite_score", _SCORE_SLOT)
    table.add_slot("body-cell-expiry", _EXPIRY_SLOT)
    table.add_slot("body-cell-max_profit", _MAX_PROFIT_SLOT)
    table.add_slot("body-cell-max_loss", _MAX_LOSS_SLOT)
    table.add_slot("body-cell-pop", _POP_SLOT)
    table.add_slot("body-cell-grade", _GRADE_SLOT)

    # ---------------------------------------------------------------- painters

    def _paint_summary(payload):
        facts = fv.summary_facts(payload)
        summary_box.clear()
        summary_box.set_visibility(facts is not None)
        if facts is None:
            return
        with summary_box:
            ui.label(facts["symbol"]).classes(f"text-h6 font-bold {LABEL}")
            if facts["price"]:
                ui.label(facts["price"]).classes(f"text-subtitle1 {MUTED}")
            for pill in facts["pills"]:
                ui.label(pill).classes(f"{BADGE_MUTED} {_PILL}")
            if facts["vol_rank"]:
                ui.label(facts["vol_rank"]).classes(f"{BADGE_ACCENT} {_PILL}")
            ui.label(facts["counts"]).classes(f"text-sm {MUTED} ml-auto")

    @guard
    def _on_chip(code):
        state["active"] = fv.toggle_chip(state["active"], code)
        _paint_results()

    def _chip(label, code):
        tone = BADGE_ACCENT if fv.chip_is_active(state["active"], code) else BADGE_MUTED
        ui.button(label, color=None).props("no-caps dense unelevated") \
            .classes(f"{tone} {_CHIP}").on("click", lambda _e, c=code: _on_chip(c))

    def _paint_chips(signals):
        chips_row.clear()
        counts = fv.chip_counts(signals)
        if not counts:
            return
        with chips_row:
            _chip(f"All {len(signals)}", fv.ALL_CHIP)
            for code, label, n in counts:
                _chip(f"{label} {n}", code)

    def _split_bar(rr):
        # Loss grows leftwards from the centre tick, profit rightwards, both scaled
        # to the larger of the two (finder_view.risk_reward_bar). The tick makes
        # the zero point explicit, so an empty left half reads as "little risk"
        # rather than as a gap.
        with ui.element("div").classes("flex flex-nowrap items-center w-full h-3"):
            with ui.element("div").classes(
                    "flex flex-nowrap justify-end flex-1 h-2 rounded-l overflow-hidden "
                    "bg-white/5"):
                ui.element("div").classes(f"h-full {_LOSS_FILL} {rr['loss_class']}")
            ui.element("div").classes(SPLIT_TICK)
            with ui.element("div").classes(
                    "flex flex-nowrap flex-1 h-2 rounded-r overflow-hidden bg-white/5"):
                ui.element("div").classes(f"h-full {_PROFIT_FILL} {rr['profit_class']}")
        with ui.row().classes("w-full justify-between no-wrap"):
            ui.label(f"Max loss {rr['loss_label']}").classes(f"text-xs {TXT_NEG}")
            ui.label(f"Max profit {rr['profit_label']}").classes(f"text-xs {TXT_POS}")

    def _pick_card(sig):
        c = card_view(sig)
        card = ui.column().classes(
            f"{CARD} w-full gap-2 cursor-pointer hover:border-[#3b82f6]")
        card.on("click", lambda _e, s=sig: _select_signal(s))
        with card:
            with ui.row().classes("w-full items-start justify-between no-wrap gap-2"):
                ui.label(c["title"]).classes(f"text-sm font-bold {LABEL}")
                with ui.row().classes("items-center gap-1 no-wrap shrink-0"):
                    ui.label(c["score_text"]).classes(
                        f"{c['score_class']} text-[#111] text-xs font-bold rounded px-1.5")
                    if c["grade"]:
                        ui.label(c["grade"]).classes(f"text-xs {c['grade_class']}")
            ui.label(c["expiry"]).classes(f"text-xs {MUTED}")
            ui.label(c["legs"]).classes("text-xs")
            if c["payoff_svg"]:
                ui.html(c["payoff_svg"]).classes("self-center max-w-full overflow-hidden")
            if c["rr"]:
                _split_bar(c["rr"])
            if c["pop"]:
                with ui.element("div").classes(
                        "w-full h-1.5 rounded overflow-hidden bg-white/5"):
                    ui.element("div").classes(f"h-full {c['pop']['class']} {c['pop_fill']}")
                ui.label(f"{c['pop']['label']} probability of profit") \
                    .classes(f"text-xs {MUTED}")
            ui.label(c["cost"]).classes(f"text-sm {LABEL}")
            with ui.row().classes("gap-2 no-wrap"):
                # click.stop: a button press is not also a card selection.
                ui.button("Calculator", icon="calculate", color=None) \
                    .props("no-caps dense").classes(BTN) \
                    .on("click.stop", lambda _e, s=sig: handoff.send_signal_to_calculator(s))
                if c["allow_paper"]:
                    ui.button("Paper", icon="request_quote", color=None) \
                        .props("no-caps dense").classes(BTN) \
                        .on("click.stop", lambda _e, s=sig: _open_paper(s))

    @guard
    def _open_paper(sig):
        # A ui.dialog leaves a canary in the slot it is built from and deletes
        # itself when that canary goes. Built from a pick card, the next repaint
        # (picks_grid.clear()) would close it under the user; list_box is never
        # cleared, and it is where the table rows' own Paper dialog is built.
        with list_box:
            handoff.send_to_paper(sig)

    def _paint_cards(picks):
        picks_grid.clear()
        with picks_grid:
            for sig in picks:
                _pick_card(sig)

    def _paint_placeholders(symbol):
        picks_grid.clear()
        with picks_grid:
            for _ in range(4):
                with ui.column().classes(
                        f"{CARD} w-full h-40 items-center justify-center animate-pulse"):
                    ui.label(scanning_text(symbol)).classes(f"text-sm {MUTED}")

    def _signals():
        return [s for s in ((state["payload"] or {}).get("signals") or []) if s]

    def _paint_results():
        """Chips, cards and list from the CACHED payload - no scan."""
        signals = _signals()
        has_scan = bool((state["payload"] or {}).get("symbol"))
        _paint_chips(signals)
        visible = fv.filter_groups(signals, state["active"])
        _paint_cards(fv.top_picks(visible))
        table.rows = finder_rows(visible)
        table.props(f'no-data-label="{fv.no_data_label(state["payload"])}"')
        table.update()
        empty_line.set_visibility(not has_scan)
        table.set_visibility(has_scan)

    def _paint_payload(payload):
        payload = payload or {}
        signals = [s for s in (payload.get("signals") or []) if s]
        symbol = payload.get("symbol")
        # Chip choices survive a repaint; a new symbol starts back at All.
        state["active"] = fv.carry_chips(state["active"], state["symbol"], symbol, signals)
        state["symbol"] = symbol
        state["payload"] = payload
        state["scanning"] = None
        state["scan_symbol"] = None
        by_id.clear()
        for s in signals:
            if s.get("id"):
                by_id[s["id"]] = s
        status.text = ""
        scan_busy.hide()
        _paint_summary(payload)
        _paint_results()

    # ------------------------------------------------------------------- scan

    @guard
    def _scan_timed_out(seq):
        if state["scanning"] != seq:
            return                              # the result landed, or a newer scan
        state["scanning"] = None
        scan_busy.hide()
        if (state["payload"] or {}).get("symbol"):
            # Still waiting: the placeholders stay and scan_symbol stays set, so
            # the late result paints when it lands and nothing else does.
            status.text = SCAN_SLOW
        else:
            # Nothing has published this session at all - the feed, not the scan.
            state["scan_symbol"] = None
            empty_line.text = _copy.WAITING_OPTIONS
            _paint_summary(state["payload"])
            _paint_results()

    @guard
    def _request_scan():
        params = scan_params(symbol_in.value, dte_min.value, dte_max.value, _bands(),
                             mincr.value)
        bus_client.request("options", {"type": "swing_scan", "args": params})
        state["scan_seq"] += 1
        seq = state["scan_seq"]
        state["scanning"] = seq
        state["scan_symbol"] = params["symbol"]
        status.text = ""
        # The panel described a row of the previous scan.
        detail_panel.clear()
        # The old cards and rows belong to the previous scan (maybe another
        # symbol), so they go - rather than reading as this scan's result.
        summary_box.set_visibility(False)
        chips_row.clear()
        _paint_placeholders(params["symbol"])
        table.rows = []
        table.props(f'no-data-label="{scanning_text(params["symbol"])}"')
        table.update()
        empty_line.set_visibility(False)
        table.set_visibility(True)
        scan_busy.show(scanning_text(params["symbol"]))
        ui.timer(_busy.BUSY_TIMEOUT_SEC, lambda: _scan_timed_out(seq), once=True)

    scan_btn.on_click(_request_scan)
    # Enter OR tab/click-out of the Symbol field triggers the scan (mirrors the Scan
    # button), deduped so tabbing through an unchanged symbol won't re-scan.
    bind_symbol_load(symbol_in, _request_scan)

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    state["version"] = bus_client.read_version("options:swing")
    _paint_payload(bus_client.read("options:swing"))

    # A symbol handed over from the Trade Plan: seed the input and scan at once,
    # the same one-shot pattern Dealer Positioning uses. After the initial paint,
    # so the cached result cannot overwrite the scanning placeholders.
    _handoff_sym = handoff.take_pending_swing()
    if _handoff_sym:
        symbol_in.value = _handoff_sym
        _request_scan()

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps it when a requested
        # swing scan finishes.
        version = bus_client.read_version("options:swing")
        if version == state["version"]:
            return
        state["version"] = version
        payload = bus_client.read("options:swing")
        if not fv.payload_answers_scan(state["scan_symbol"], payload):
            return      # another symbol's result - keep waiting for ours
        _paint_payload(payload)

    ui.timer(2.0, _maybe_repaint)
