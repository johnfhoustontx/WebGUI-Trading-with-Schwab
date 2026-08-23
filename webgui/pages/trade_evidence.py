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
from pages import trade_shell as sh
from pages import trade_terminal as tt
from pages.trade import (live_ic_decay_note, live_ic_line, live_ic_split_line,
                         model_staleness, swing_exposure_note,
                         swing_model_meta, swing_regime_note)

_GRID = ("grid items-center gap-x-3 "
         "[grid-template-columns:minmax(0,1fr)_58px_66px_minmax(96px,124px)_62px]")


def render():
    sh.page(_build)


def _build(state, refs):
    with ui.element("div").classes(
            "w-full grid gap-4 items-start "
            "[grid-template-columns:minmax(0,1.55fr)_minmax(300px,1fr)]"):

        factors = sh.panel("Why — validated factors")
        with factors:
            table = ui.column().classes("w-full gap-0 min-w-0")

        with ui.column().classes("w-full gap-4 min-w-0"):
            track = sh.panel("Model track record")
            with track:
                track_rows = ui.column().classes("w-full gap-[10px]")
                warn = ui.column().classes(f"{T.CALLOUT} w-full")
            hist = sh.panel("This name's history")
            with hist:
                hist_note = ui.label("").classes("text-[11.5px] text-[#6b7b9c]")
                hist_rows = ui.column().classes("w-full gap-0")

    def _paint(a):
        sm = a.get("swing_model") or {}
        rows = tt.evidence_rows(sm)

        table.clear()
        with table:
            with ui.element("div").classes(
                    f"{_GRID} px-1 pb-[9px] {T.RULE} "
                    "text-[9.5px] font-bold tracking-[0.14em] text-[#56678a]"):
                ui.label("FACTOR")
                for h in ("Z", "WEIGHT", "CONTRIBUTION", "IC"):
                    ui.label(h).classes("text-right")
            if not rows:
                ui.label("No validated model reading for this symbol — the "
                         "Position card is on its legacy heuristic.").classes(
                    f"{T.NOTE} pt-3")
            for r in rows:
                with ui.element("div").classes(
                        f"{_GRID} px-1 py-[9px] {T.HAIRLINE}"):
                    ui.label(r["name"]).classes(
                        "text-[13px] font-medium text-[#e6edf7] truncate min-w-0")
                    ui.label(r["z"]).classes(f"{T.VALUE} text-right")
                    ui.label(r["weight"]).classes(
                        f"{T.MONO} text-[12.5px] text-right {r['weight_class']}")
                    with ui.row().classes("items-center gap-[9px] min-w-0 w-full"):
                        sh.centred_bar(r["left_pct"], r["width_pct"],
                                       r["bar_class"], height="h-[9px]")
                        ui.label(r["contribution"]).classes(
                            f"{T.MONO} text-[12.5px] whitespace-nowrap "
                            + ("text-[#34d399]" if r["bar_class"] == T.BAR_POS
                               else "text-[#f87171]"))
                    ui.label(r["ic"]).classes(
                        f"{T.MONO} text-[12.5px] text-right {r['ic_class']}")
            comp = tt.evidence_composite(sm)
            if comp is not None:
                with ui.row().classes("w-full items-baseline justify-between "
                                      "gap-[14px] px-1 pt-[15px]"):
                    ui.label("weighted composite").classes(
                        "text-[12.5px] text-[#8b9bb4]")
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
                entries.append(("Live tracking", line))
            split = live_ic_split_line(lic)
            if split:
                entries.append(("By market direction", split))
            for label, value in entries:
                with ui.row().classes("w-full items-baseline justify-between "
                                      "gap-[14px]"):
                    ui.label(label).classes("text-[12.5px] text-[#8b9bb4]")
                    ui.label(str(value)).classes(
                        f"{T.MONO} text-[13px] text-[#cfdaee] text-right")

        # The exposure line is the loudest thing this model has to say about
        # itself, so it sits in the warning slot rather than in the list.
        note = swing_exposure_note(sm) or model_staleness(
            (meta or {}).get("version", "")) or live_ic_decay_note(lic)
        warn.clear()
        warn.set_visibility(bool(note))
        if note:
            with warn:
                with ui.row().classes("gap-[11px]"):
                    ui.label("⚠").classes("text-[13px] text-[#fbbf24]")
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
                        f"{T.MONO} text-[12.5px] text-[#a8b6cf]")
                    pct = fmt.num(h.get("percentile"))
                    ui.label(f"{int(pct)}th" if pct is not None else "—").classes(
                        f"{T.MONO} text-[12.5px] text-[#cfdaee]")
                    res = fmt.num(h.get("result"))
                    ui.label("pending" if h.get("pending") else f"{res:+.2%}").classes(
                        f"{T.MONO} text-[12.5px] min-w-[72px] text-right "
                        + (T.OFF if h.get("pending") else T.sign_text(res)))

    refs["paint"].append(_paint)
