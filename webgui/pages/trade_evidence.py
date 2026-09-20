"""Signal Desk — Evidence.

The validated factors, z-scored against today's cross-section, each with its
weight, a zero-centred contribution bar and its IC, footed by the weighted
composite. Alongside: the model's track record with its live-IC warning, and
this name's last five reads with what followed.

The two right-hand cards answer different questions and must not be read as one:
the track record is about the MODEL, the history is about this SYMBOL, and five
reads of one name can never support a correlation — which is why the history
shows rows rather than a statistic.
"""
from nicegui import ui

from pages import fmt
from pages import terminal_theme as T
from pages import trade_help as th
from pages import trade_shell as sh
from pages import trade_terminal as tt
from pages.options import theme
from pages.trade import (live_ic_decay_note, live_ic_line, live_ic_split_line,
                         model_staleness, swing_exposure_note,
                         swing_model_meta, swing_regime_note)

_P = theme.THEME["palette"]
# The app's neutral ladder, which replaced the rungs this page used to spell
# out by hand: ``theme.LABEL`` for a reading, ``theme.MUTED`` for what
# qualifies it, ``_FAINT`` for the dimmest captions.
_FAINT = f"text-[{_P['icon']}]"

# ⚠ `w-full` is load-bearing. NiceGUI's column sets `align-items: flex-start`,
# so a grid child sizes to its OWN content — and `minmax(0,1fr)` then resolves
# to a different width on every row, which is exactly how the header and the
# rows ended up 34px out of step.
_GRID = ("grid w-full items-center gap-x-3 "
         "[grid-template-columns:minmax(0,1fr)_58px_66px_minmax(96px,124px)_62px]")


def render():
    sh.page(_build, "Evidence")


def _build(state, refs):
    with ui.element("div").classes(
            "w-full grid gap-4 items-start "
            "[grid-template-columns:minmax(0,1.55fr)_minmax(300px,1fr)]"):

        factors = sh.panel("Why — validated factors",
                           help=th.help_for("position_panel"))
        with factors:
            table = ui.column().classes("w-full gap-0 min-w-0")

        with ui.column().classes("w-full gap-4 min-w-0"):
            track = sh.panel("Model track record",
                             help=th.help_for("track_record"))
            with track:
                track_rows = ui.column().classes("w-full gap-[10px]")
                warn = ui.column().classes(f"{T.CALLOUT} w-full")
            hist = sh.panel("This name's history",
                            help=th.help_for("symbol_history"))
            with hist:
                hist_note = ui.label("").classes(
                    f"text-[11.5px] {theme.MUTED}")
                hist_rows = ui.column().classes("w-full gap-0")

    def _paint(a):
        sm = a.get("swing_model") or {}
        rows = tt.evidence_rows(sm)

        table.clear()
        with table:
            with ui.element("div").classes(
                    f"{_GRID} px-1 pb-[9px] {T.RULE} "
                    f"text-[9.5px] font-bold tracking-[0.14em] {_FAINT}"):
                with ui.label("FACTOR"):
                    sh.tip(th.column_help("FACTOR"))
                for h in ("Z", "WEIGHT", "CONTRIBUTION", "IC"):
                    with ui.label(h).classes("text-right"):
                        sh.tip(th.column_help(h))
            if not rows:
                ui.label("No validated model reading for this symbol — the "
                         "Short Term card is on its legacy heuristic.").classes(
                    f"{T.NOTE} pt-3")
            for r in rows:
                with ui.element("div").classes(
                        f"{_GRID} px-1 py-[9px] {T.HAIRLINE}"):
                    with ui.label(r["name"]).classes(
                            f"text-[13px] font-medium {theme.LABEL} "
                            "truncate min-w-0"):
                        sh.tip(th.factor_help(r["key"]))
                    ui.label(r["z"]).classes(f"{T.VALUE} text-right")
                    ui.label(r["weight"]).classes(
                        f"{T.MONO} text-[12.5px] text-right {r['weight_class']}")
                    with ui.row().classes("items-center gap-[9px] min-w-0 w-full"):
                        sh.centred_bar(r["left_pct"], r["width_pct"],
                                       r["bar_class"], height="h-[9px]")
                        ui.label(r["contribution"]).classes(
                            f"{T.MONO} text-[12.5px] whitespace-nowrap "
                            # The finite palette, not a restatement of its
                            # hexes: this is the same reading ``sign_text``
                            # gives, on the bar beside it.
                            + (T.POS if r["bar_class"] == T.BAR_POS
                               else T.NEG))
                    ui.label(r["ic"]).classes(
                        f"{T.MONO} text-[12.5px] text-right {r['ic_class']}")
            comp = tt.evidence_composite(sm)
            if comp is not None:
                with ui.row().classes("w-full items-baseline justify-between "
                                      "gap-[14px] px-1 pt-[15px]"):
                    with ui.label("weighted composite").classes(
                            f"text-[12.5px] {theme.MUTED}"):
                        sh.tip(th.help_for("composite"))
                    ui.label(f"{comp:+.3f}").classes(
                        f"{T.MONO} text-[21px] font-bold "
                        + T.sign_text(comp))

        meta = swing_model_meta(sm)
        lic = a.get("live_ic")
        track_rows.clear()
        with track_rows:
            entries = []
            if meta:
                entries.append(("Artifact", meta["version"]))
                entries.append(("Out-of-sample IC", meta["oos_ic"]))
            entries.append(("Scored under", (swing_regime_note(sm) or "—")))
            line = live_ic_line(lic)
            if line:
                # The shared line is written for a standalone context and names
                # itself; here it sits beside a label that already does.
                entries.append(("Live tracking",
                                line.replace("Live tracking: ", "", 1)))
            split = live_ic_split_line(lic)
            if split:
                entries.append(("By market direction", split))
            for label, value in entries:
                with ui.row().classes("w-full items-baseline justify-between "
                                      "gap-[14px]"):
                    ui.label(label).classes(f"text-[12.5px] {theme.MUTED}")
                    ui.label(str(value)).classes(
                        f"{T.MONO} text-[13px] {theme.LABEL} text-right")

        # The exposure line is the loudest thing this model has to say about
        # itself, so it sits in the warning slot rather than in the list.
        note = swing_exposure_note(sm) or model_staleness(
            (meta or {}).get("version", "")) or live_ic_decay_note(lic)
        warn.clear()
        warn.set_visibility(bool(note))
        if note:
            with warn:
                with ui.row().classes("gap-[11px]"):
                    ui.label("⚠").classes(f"text-[13px] {T.WARN}")
                    ui.label(note).classes(T.CALLOUT_TEXT)

        history = a.get("symbol_history") or []
        sym = a.get("symbol") or "this name"
        hist_note.text = (f"last {len(history)} read(s) of {sym}"
                          if history else
                          f"no journalled reads of {sym} yet — the record "
                          "starts from the first analysis")
        hist_rows.clear()
        with hist_rows:
            for h in history:
                with ui.element("div").classes(
                        f"w-full grid items-baseline gap-[14px] py-[9px] "
                        f"{T.HAIRLINE} [grid-template-columns:1fr_auto_auto]"):
                    ui.label(h.get("date") or "—").classes(
                        f"{T.MONO} text-[12.5px] {theme.MUTED}")
                    pct = fmt.num(h.get("percentile"))
                    ui.label(f"{int(pct)}th" if pct is not None else "—").classes(
                        f"{T.MONO} text-[12.5px] {theme.LABEL}")
                    res = fmt.num(h.get("result"))
                    ui.label("pending" if h.get("pending") else f"{res:+.2%}").classes(
                        f"{T.MONO} text-[12.5px] min-w-[72px] text-right "
                        + (T.OFF if h.get("pending") else T.sign_text(res)))

    refs["paint"].append(_paint)
