"""The Signal Desk shell — command bar + page frame, shared by four screens.

Widgets and wiring only; every value comes from ``trade_terminal`` (pure) and
every class from ``terminal_theme`` (tokens). Kept apart from those two so they
stay importable and testable without NiceGUI.

**The symbol is a draft until committed.** Typing edits a draft; Enter, Tab or
blur commits it and enqueues an analyze. The input border turns indigo while the
draft differs from the committed symbol, so an uncommitted edit never looks
like the thing on screen. Blurring an empty field reverts rather than clearing —
an empty symbol is not a request.

The bar is identical on all four screens on purpose: it is the one piece of
state they share, and moving between screens must not feel like changing
context.
"""
from nicegui import ui

import bus_client
from pages import busy as _busy
from pages import terminal_theme as T
from pages import trade_terminal as tt
from pages.trade import should_open_tab
from pages.ui_guard import guard
from pages.view_watch import watch_view

VIEW = "trade:analysis"
POLL_SEC = 2.0
# The two report actions live in the command bar rather than on one screen:
# they act on the committed symbol, which is the bar's own state, and they were
# reachable from the old single page from wherever you were on it.
_REPORTS = (("Deep Dive", "deepdive", "trade:deepdive", "/trade/deepdive"),
            ("AI Query", "deepdive_query", "trade:deepdive_query",
             "/trade/deepdive-query"))


def read_analysis():
    return bus_client.read(VIEW) or {}


def request(symbol):
    sym = (symbol or "").strip().upper()
    if sym:
        bus_client.request("trade", {"type": "analyze", "args": {"symbol": sym}})
    return sym


def page(build):
    """Render one Signal Desk screen.

    ``build(state, refs)`` is called once inside the frame to lay the screen
    out, and ``refs["paint"]`` collects repaint callbacks the shell fires when
    the analysis cache moves — so a screen never wires its own poller and the
    four cannot fall out of step."""
    ui.add_head_html(T.FONT_HTML)
    state = {"analysis": read_analysis()}
    state["draft"] = (state["analysis"].get("symbol") or "AAPL").upper()
    painters = []

    with ui.column().classes(f"{T.PAGE} {T.SHELL}") as shell:
        bar = _command_bar(state, painters)
        build(state, {"paint": painters})

    # Committing a symbol enqueues an analyze that can take several seconds
    # against a cold cache; without a wait the whole screen looks inert.
    spinner = _busy.build_busy(shell, "Analyzing…")
    state["spinner"] = spinner

    def _repaint():
        spinner.hide()
        for fn in painters:
            fn(state["analysis"])

    @guard
    def _on_change():
        state["analysis"] = read_analysis()
        _repaint()

    _repaint()
    bar["seed"]()
    watch_view(VIEW, _on_change, interval=POLL_SEC)
    ui.timer(POLL_SEC, _watch_reports(state))


def _command_bar(state, painters):
    with ui.row().classes("w-full items-center gap-[18px] flex-wrap "
                          "pb-[15px] border-b border-[#17223a]"):
        with ui.row().classes("items-baseline gap-[11px]"):
            ui.label("Signal desk").classes(
                "text-[15px] font-extrabold tracking-[-0.01em] "
                "text-[#f2f6fc] whitespace-nowrap")
            stamp = ui.label("").classes(
                f"{T.MONO} text-[10px] tracking-[0.12em] text-[#56678a] "
                "whitespace-nowrap")

        ui.element("div").classes("flex-1 min-w-[20px]")

        with ui.row().classes("items-center gap-[14px] flex-wrap"):
            with ui.row().classes("items-center gap-[9px]"):
                ui.label("SYMBOL").classes(
                    "text-[10px] font-bold tracking-[0.14em] text-[#56678a]")
                with ui.row().classes(
                        "items-center gap-[9px] rounded-lg border "
                        "border-[#263353] bg-[#0c1322] pl-[6px] pr-3 py-[5px] "
                        "transition-colors") as box:
                    sym_in = ui.input(placeholder="AAPL").props(
                        "dense borderless spellcheck=false").classes(
                        f"{T.MONO} w-[86px] text-[14px] font-bold "
                        "tracking-[0.06em] text-[#f2f6fc] uppercase")
                    name = ui.label("").classes(
                        "text-[11px] text-[#6b7b9c] whitespace-nowrap")

            with ui.row().classes("items-baseline gap-[9px]"):
                price = ui.label("").classes(
                    f"{T.MONO} text-[19px] font-bold tracking-[-0.01em] "
                    "text-[#f2f6fc]")
                change = ui.label("").classes(f"{T.MONO} text-[12px]")

            with ui.row().classes("items-center gap-2 rounded-[7px] border "
                                  "border-[#263353] bg-[rgba(15,23,40,0.6)] "
                                  "px-[11px] py-[5px]"):
                ui.label("MTF BIAS").classes(
                    "text-[9.5px] font-bold tracking-[0.13em] text-[#6b7b9c]")
                bias = ui.label("").classes(
                    "text-[11.5px] font-bold tracking-[0.04em]")

            with ui.row().classes("items-center gap-[7px]"):
                for label, cmd, view, route in _REPORTS:
                    ui.button(label, color=None).props("no-caps dense")                         .classes("rounded-lg border border-[#2b3a57] px-3 py-[5px] "
                                 "text-[11.5px] font-semibold normal-case "
                                 "bg-transparent text-[#a8b6cf] "
                                 "hover:border-[#4a5b7d] hover:text-[#f2f6fc]")                         .on_click(_report(state, cmd, view, label))
                # A report takes tens of seconds. Without a line that STAYS,
                # the click reads as "nothing happened" — a toast is gone
                # before the work is.
                report_status = ui.label("").classes(
                    "text-[11px] text-[#818cf8] whitespace-nowrap")
                state["report_status"] = report_status

    # The draft/committed distinction, shown as a border colour. Two static
    # classes swapped, never a computed one — the finite-set rule.
    _DIRTY, _CLEAN = "border-[#3a3f7a]", "border-[#263353]"

    def _mark_draft():
        dirty = (sym_in.value or "").strip().upper() != state["draft"]
        box.classes(remove=f"{_DIRTY} {_CLEAN}", add=_DIRTY if dirty else _CLEAN)

    @guard
    def _commit():
        v = (sym_in.value or "").strip().upper()
        if not v:
            sym_in.value = state["draft"]          # empty is not a request
            _mark_draft()
            return
        state["draft"] = v
        sym_in.value = v
        _mark_draft()
        sp = state.get("spinner")
        if sp:
            sp.show(f"Analyzing {v}…")
        request(v)

    sym_in.on("blur", _commit)
    sym_in.on("keydown.enter", _commit)
    sym_in.on("keydown.tab", _commit)
    sym_in.on("update:model-value", lambda _e: _mark_draft())

    def _paint(a):
        vals = tt.command_bar(a)
        stamp.text = vals["model_stamp"]
        name.text = vals["name"]
        price.text = vals["price"]
        change.text = vals["change"]
        change.classes(remove=T.STATE_TEXT, add=vals["change_class"])
        bias.text = vals["bias"]
        bias.classes(remove=T.STATE_TEXT, add=vals["bias_class"])

    painters.append(_paint)
    return {"seed": lambda: _seed(sym_in, state)}


def _seed(sym_in, state):
    sym_in.value = state["draft"]


def _report(state, cmd, view, label):
    """Enqueue a report for the COMMITTED symbol and open it when it lands.

    The version is baselined at click time, so a stale cached report from an
    earlier run never opens a tab — the same guard the single page used."""
    @guard
    def _go():
        sym = state.get("draft")
        if not sym:
            return
        state[f"{cmd}_ver"] = bus_client.read_version(view)
        state[f"{cmd}_pending"] = True
        bus_client.request("trade", {"type": cmd, "args": {"symbol": sym}})
        st = state.get("report_status")
        if st:
            st.text = f"Running {label} for {sym}…"
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
                st = state.get("report_status")
                if st:
                    st.text = ""
                ui.navigate.to(f"{route}?v={v}", new_tab=True)
    return _tick


def panel(title=None, stamp=None, classes=""):
    """A titled Signal Desk panel. Returns the column to fill."""
    col = ui.column().classes(f"{T.PANEL} w-full gap-4 {classes}")
    if title:
        with col:
            with ui.row().classes("w-full items-baseline justify-between "
                                  "gap-3 flex-wrap"):
                ui.label(title).classes(T.PANEL_TITLE)
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
