"""The Signal Desk shell — header + control bar + page frame, shared by four screens.

Widgets and wiring only; every value comes from ``trade_terminal`` (pure) and
the frame from ``pages/ui_kit`` (the app's one look). ``terminal_theme`` still
carries this family's DATA colours; its surfaces and faces are gone from here.

**The symbol is a draft until committed.** Typing edits a draft; Enter always
requests, and tabbing or clicking out requests only a CHANGE — ``should_commit``
is that rule, and ``kit.symbol_field`` (``bind_symbol_load(enter_always=True)``)
is the app-wide helper that now carries it. A ring marks the field while the
draft differs from the committed symbol, so an uncommitted edit never looks
like the thing on screen. Blurring an empty field reverts rather than clearing —
an empty symbol is not a request.

The bar is identical on all four screens on purpose: it is the one piece of
state they share, and moving between screens must not feel like changing
context. The four screen NAMES differ, and each passes its own to ``page``.
"""
from nicegui import ui

import bus_client
from pages import terminal_theme as T
from pages import trade_help as th
from pages import trade_terminal as tt
from pages import ui_kit as kit
from pages.options import theme as _t
from pages.trade import should_open_tab
from pages.ui_guard import guard
from pages.view_watch import watch_view

VIEW = "trade:analysis"
POLL_SEC = 2.0

# How long a Signal Desk analysis usually takes, and the backstop that outlasts
# it. MEASURED: a COIN analysis ran 96 s end to end on 2026-08-31, against
# ``busy.BUSY_TIMEOUT_SEC`` of 30 s — sized for the Simulator's ~19 s fetch — so
# the spinner vanished at t=30 and left an empty panel for another minute, which
# the operator reasonably read as "it did not return anything". A backstop must
# outlast the work it guards; busy.py's own docstring says a premature one is
# worse than none. (These three lived on ``pages/trade.py``, the unrouted single
# page, while all four reachable screens ran the 30 s default.)
TYPICAL_ANALYZE_SEC = 90
ANALYZE_TIMEOUT_SEC = 300

# The draft/committed distinction, shown as a ring on the Symbol field: two
# static classes swapped, never a computed one — the finite-set rule. Warning
# amber because that is the app's word for "not applied yet", and deliberately
# not the focus blue, which already means "the cursor is here".
DRAFT_RING = (f"ring-2 ring-[{_t.THEME['semantic']['warning']}]/60 "
              "rounded-[8px]")

# The two report actions live in the header rather than on one screen: they act
# on the committed symbol, which is the bar's own state, and they were reachable
# from the old single page from wherever you were on it.
_REPORTS = (("Deep Dive", "deepdive", "trade:deepdive", "/trade/deepdive"),
            ("AI Query", "deepdive_query", "trade:deepdive_query",
             "/trade/deepdive-query"))


def analyzing_label(symbol, seconds, typical=TYPICAL_ANALYZE_SEC):
    """The wait message, counting up. Pure, so the wording is testable.

    Past `typical` it changes wording rather than going quiet: at that point the
    reader is deciding for themselves whether it has died, and silence is the
    least helpful answer. It never claims failure -- the analysis legitimately
    runs long, and the backstop is what handles a genuine death."""
    sym = str(symbol or "").strip() or "symbol"
    try:
        sec = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return f"Analyzing {sym}…"
    if sec > typical:
        return f"Analyzing {sym}… {sec}s (longer than usual)"
    return f"Analyzing {sym}… {sec}s"


def read_analysis():
    return bus_client.read(VIEW) or {}


def request(symbol):
    sym = (symbol or "").strip().upper()
    if sym:
        bus_client.request("trade", {"type": "analyze", "args": {"symbol": sym}})
    return sym


def page(build, title, view=VIEW):
    """Render one Signal Desk screen under the app's page frame.

    ``title`` is the screen's own name, and it is the NAV's word — the rail, the
    breadcrumb and this header must agree. Overview and Evidence had no title at
    all before: the shell drew the family name and nothing else, so a reader met
    a symbol box and a wall of panels.

    ``view`` is what the header's Updated stamp times, and it DEFAULTS to the
    analysis because that is what three of the four screens show. The Rank
    Board is the exception: its content is a universe-wide board published
    under its own key, and a stamp naming the symbol analysis would time the
    command bar rather than the thing on screen.

    ``build(state, refs)`` is called once inside the frame to lay the screen
    out; ``refs["paint"]`` collects repaint callbacks the shell fires when the
    analysis cache moves — so a screen never wires its own poller and the four
    cannot fall out of step — and ``refs["head"]`` is the header handle, so a
    screen's own page ACTION lands in the one actions row rather than in a
    container its repaint clears."""
    state = {"analysis": read_analysis()}
    state["draft"] = (state["analysis"].get("symbol") or "AAPL").upper()
    painters = []

    page_col = kit.page()
    with page_col:
        # ⚠ ``stale=False`` is a decision, not a default. ``services/trade_svc/
        # app.py`` is "on-demand only — there is no scheduler": every trade view
        # is request/response, published only in answer to an ``analyze``. An
        # age therefore says nothing about whether the page is behind, and
        # amber would be a claim the service cannot back. The stamp still
        # answers this reader's question — whether what is on screen is the
        # answer to the last symbol they committed.
        head = kit.header(title, view=view, stale=False)
        _command_bar(state, painters, head)
        # The wait covers the RESULTS only. ``build_busy(shell, …)`` scrimmed
        # the whole page column, so committing a symbol greyed out the Symbol
        # box the reader had just typed into — a full-screen overlay on a page
        # the design does not list among the three that get one.
        results = kit.region(
            "Analyzing…", timeout=ANALYZE_TIMEOUT_SEC,
            elapsed_label=lambda sec: analyzing_label(state.get("draft"), sec))
        with results.content:
            build(state, {"paint": painters, "head": head})
    state["wait"] = results

    def _repaint():
        results.busy.hide()
        for fn in painters:
            fn(state["analysis"])

    @guard
    def _on_change():
        state["analysis"] = read_analysis()
        _repaint()

    _repaint()
    watch_view(VIEW, _on_change, interval=POLL_SEC)
    ui.timer(POLL_SEC, _watch_reports(state))


def _command_bar(state, painters, head):
    """The header's report actions, the model stamp, and the symbol control bar.

    The "Signal desk" lockup goes: the rail and the breadcrumb both say where
    you are, and the header now names the screen itself."""
    # Which fitted model produced these numbers — provenance for the title, so
    # it sits in the header line rather than in a bar of its own. Built here
    # and MOVED to index 1, because a NiceGUI container appends: entering
    # ``head.row`` would put it after the Updated stamp and the actions.
    model = ui.label("").classes(f"text-xs {_t.MUTED} whitespace-nowrap")
    with model:
        tip(th.help_for("model_stamp"))
    model.move(head.row, 1)

    with head.actions:
        for label, cmd, view, _route in _REPORTS:
            btn = kit.button(label, kind="secondary",
                             on_click=_report(state, cmd, view))
            with btn:
                # Not kit.button's own ``tooltip=``: these texts are written as
                # short paragraphs and need ``whitespace-pre-line``, which the
                # kit's tooltip does not carry.
                tip(th.help_for(cmd))
            # _watch_reports is module-level and sees only ``state``, so the
            # handle it has to release lives there.
            state[f"{cmd}_btn"] = btn

    with kit.control_bar():
        sym_in = kit.symbol_field(value=state["draft"], on_load=lambda: _commit())
        with kit.field("Company"):
            name = ui.label("").classes(
                f"text-[12px] {_t.MUTED} whitespace-nowrap")
        with kit.field("Last"):
            with ui.row().classes("items-baseline gap-2 no-wrap"):
                price = ui.label("").classes(
                    f"text-[19px] font-bold tracking-[-0.01em] {_t.LABEL}")
                change = ui.label("").classes("text-[12px]")
        with kit.field("MTF bias"):
            bias = ui.label("").classes(
                "text-[12px] font-bold tracking-[0.04em]")
            with bias:
                tip(th.help_for("mtf_bias"))

    def _mark_draft():
        # The same predicate the tab-out trigger uses: "is this typed symbol a
        # different request from the one on screen?" — so the ring and the
        # commit can never disagree about what counts as a change.
        dirty = tt.should_commit(sym_in.value, state["draft"], explicit=False)
        sym_in.classes(remove=DRAFT_RING, add=DRAFT_RING if dirty else "")

    def _commit():
        """Fired by the kit's Symbol field: Enter always, tab-out only on a
        change, never on an empty box — ``should_commit``'s rule, carried by
        ``bind_symbol_load(enter_always=True)``."""
        v = (sym_in.value or "").strip().upper()
        if not v:
            return
        state["draft"] = v
        sym_in.value = v
        wait = state.get("wait")
        if wait is not None:
            wait.busy.show(analyzing_label(v, 0))
        request(v)

    @guard
    def _revert_if_empty(_e=None):
        # "I cleared the box" is not "analyze nothing": the committed symbol
        # comes back, so the field never disagrees with the panels by being
        # blank. ``focusout``, not ``blur`` — NiceGUI attaches the listener to
        # the q-input ROOT, which ``blur`` does not bubble to (the reason this
        # page's old blur handler never fired at all).
        if not (sym_in.value or "").strip():
            sym_in.value = state["draft"]
        _mark_draft()

    sym_in.on("focusout", _revert_if_empty)
    sym_in.on_value_change(lambda _e: _mark_draft())

    def _paint(a):
        vals = tt.command_bar(a)
        model.text = vals["model_stamp"]
        name.text = vals["name"]
        price.text = vals["price"]
        change.text = vals["change"]
        change.classes(remove=T.STATE_TEXT, add=vals["change_class"])
        bias.text = vals["bias"]
        bias.classes(remove=T.STATE_TEXT, add=vals["bias_class"])

    painters.append(_paint)


def _report(state, cmd, view):
    """Enqueue a report for the COMMITTED symbol and open it when it lands.

    The version is baselined at click time, so a stale cached report from an
    earlier run never opens a tab — the same guard the single page used. The
    button then holds its own spinner until ``_watch_reports`` opens the tab:
    a report takes tens of seconds, and on the AI Query a second click is a
    second PAID Claude call."""
    @guard
    def _go():
        sym = state.get("draft")
        if not sym:
            return
        state[f"{cmd}_ver"] = bus_client.read_version(view)
        state[f"{cmd}_pending"] = True
        bus_client.request("trade", {"type": cmd, "args": {"symbol": sym}})
        btn = state.get(f"{cmd}_btn")
        if btn is not None:
            # The kit's 30 s default would hand the button back while the
            # report was still being written; this is the same backstop the
            # analyze wait uses.
            kit.set_busy(btn, timeout=ANALYZE_TIMEOUT_SEC)
    return _go


def _watch_reports(state):
    @guard
    def _tick():
        for _label, cmd, view, route in _REPORTS:
            v = bus_client.read_version(view)
            if should_open_tab(state.get(f"{cmd}_pending"), v,
                               state.get(f"{cmd}_ver")):
                state[f"{cmd}_pending"] = False
                state[f"{cmd}_ver"] = v
                btn = state.get(f"{cmd}_btn")
                if btn is not None:
                    kit.set_busy(btn, False)
                ui.navigate.to(f"{route}?v={v}", new_tab=True)
    return _tick


def tip(text):
    """Attach a hover explanation to the element currently being built.

    Call it inside a ``with <element>:`` block. Empty text attaches nothing —
    an empty tooltip still shows an empty box on hover, which reads as a bug.
    """
    if text:
        ui.tooltip(text).classes(T.TOOLTIP)


def panel(title=None, stamp=None, classes="", help=None):
    """A titled Signal Desk panel. Returns the column to fill.

    ``help`` attaches a hover explanation to the TITLE rather than to the whole
    panel: a tooltip covering a panel would fire wherever the pointer rested
    inside it, including over the numbers it is trying to explain."""
    col = ui.column().classes(f"{T.PANEL} w-full gap-4 {classes}")
    if title:
        with col:
            with ui.row().classes("w-full items-baseline justify-between "
                                  "gap-3 flex-wrap"):
                with ui.label(title).classes(T.PANEL_TITLE):
                    tip(help)
                if stamp:
                    ui.label(stamp).classes(T.SUBTLE)
    return col


def centred_bar(left_pct, width_pct, bar_class, height="h-[7px]"):
    """The shared bar: a centre axis with a bar grown from it.

    Percentages are genuinely continuous, so they use runtime arbitrary classes
    — the documented exception to the finite-palette rule, which binds COLOURS.
    The colour here is always one of the fixed `BAR_*` tokens."""
    with ui.element("div").classes(
            f"relative {height} w-full rounded-[4px] bg-[#17223a]"):
        ui.element("div").classes(
            "absolute top-0 bottom-0 w-px left-1/2 bg-[#29364f]")
        ui.element("div").classes(
            f"absolute top-0 bottom-0 rounded-[4px] {bar_class} "
            f"left-[{left_pct:.2f}%] w-[{width_pct:.2f}%]")
