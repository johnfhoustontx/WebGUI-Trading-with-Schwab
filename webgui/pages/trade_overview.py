"""Signal Desk — Overview.

Market state and its gate chips, the Position rank on a decile rail with a
per-side gate card each, the Investor verdict over centred factor bars, the
dealer ladder with its volatility stats, and where the name sits among peers.

Widgets only: values come from ``trade_terminal``, classes from
``terminal_theme``. The four screens share `trade_shell.page`, so this module
never wires a poller.
"""
from nicegui import ui

from pages import fmt
from pages import terminal_theme as T
from pages import trade_help as th
from pages import trade_shell as sh
from pages import trade_terminal as tt
from pages.trade import (dealer_rows, gate_rows, short_gate_rows,
                         swing_headline, verdict_text_class)


def render():
    sh.page(_build)


def _build(state, refs):
    paint = refs["paint"]

    # ── market state + gates ────────────────────────────────────────────────
    # One line, not a stacked block: the eyebrow, the sentence and both chips
    # are each short, and stacking them cost a third of the fold for four words.
    with ui.row().classes("w-full items-center gap-3 flex-wrap rounded-[10px] "
                          "border border-[#1c2740] "
                          "bg-[linear-gradient(180deg,#0e1626,#0b1220)] "
                          "px-[14px] py-2"):
        with ui.label("MARKET STATE").classes(T.EYEBROW):
            sh.tip(th.help_for("market_state"))
        market = ui.label("").classes("text-[12.5px] text-[#cfdaee] min-w-0")
        ui.element("div").classes("flex-1 min-w-[8px]")
        chips = ui.row().classes("gap-[7px] flex-wrap")

    # ── position | investor ─────────────────────────────────────────────────
    with ui.element("div").classes(
            "w-full grid gap-4 items-stretch "
            "[grid-template-columns:repeat(auto-fit,minmax(440px,1fr))]"):

        with sh.panel():
            with ui.row().classes("w-full items-baseline justify-between gap-3"):
                with ui.row().classes("items-baseline gap-[10px]"):
                    with ui.label("Position").classes(T.PANEL_TITLE):
                        sh.tip(th.help_for("position_panel"))
                    ui.label("1–8 weeks").classes("text-[12px] text-[#6b7b9c]")
                ui.label("validated factor model").classes(T.SUBTLE)

            with ui.column().classes("w-full gap-[11px]"):
                with ui.row().classes("items-baseline gap-[10px] flex-wrap"):
                    pctl = ui.label("").classes(T.BIG_NUM)
                    pctl_note = ui.label("").classes("text-[13px] text-[#8b9bb4]")
                pctl_stats = ui.label("").classes(f"{T.MONO} text-[12.5px] text-[#7d8db0]")
                with pctl_stats:
                    sh.tip(th.help_for("band_stats"))

                # The decile rail: a red→neutral→green ground with a marker.
                with ui.element("div").classes(
                        "relative h-[7px] w-full rounded-[4px] mt-[3px] "
                        "bg-[linear-gradient(90deg,#b4404f_0%,#4a4a63_46%,#2fa87a_100%)]"
                ) as rail:
                    marker = ui.element("div").classes(
                        "absolute -top-1 -bottom-1 w-[3px] rounded-[2px] "
                        "bg-white shadow-[0_0_10px_rgba(255,255,255,0.6)] left-1/2")
                with ui.row().classes("w-full justify-between text-[10.5px] "
                                      "text-[#56678a]"):
                    # Not "decile" — there are five bands, and they are cut from
                    # the model's own score history, not from today's names.
                    ui.label("weakest band")
                    ui.label("model band")
                    ui.label("strongest band")
                with rail:
                    rail_tip = ui.tooltip("").classes(T.TOOLTIP)

            side_cards = ui.element("div").classes(
                "w-full grid grid-cols-2 gap-3")
            pos_foot = ui.label("").classes("text-[11px] leading-[1.5] "
                                            "text-[#6b7b9c] mt-auto pt-[3px]")

        with sh.panel():
            with ui.row().classes("w-full items-baseline justify-between gap-3"):
                with ui.row().classes("items-baseline gap-[10px]"):
                    with ui.label("Investor").classes(T.PANEL_TITLE):
                        sh.tip(th.help_for("investor_panel"))
                    ui.label("months+").classes("text-[12px] text-[#6b7b9c]")
                ui.label("fundamentals + relative strength").classes(T.SUBTLE)

            with ui.row().classes("items-baseline gap-[14px] flex-wrap"):
                verdict = ui.label("").classes(
                    "text-[34px] font-extrabold leading-none tracking-[-0.02em]")
                with verdict:
                    sh.tip(th.help_for("investor_verdict"))
                verdict_score = ui.label("").classes(
                    f"{T.MONO} text-[13px] text-[#7d8db0]")
            inv_bars = ui.column().classes("w-full gap-[9px]")
            inv_foot = ui.label("").classes("text-[11px] leading-[1.5] "
                                            "text-[#6b7b9c] mt-auto pt-[3px]")

    # ── dealer ──────────────────────────────────────────────────────────────
    dealer_panel = sh.panel("Dealer positioning & volatility",
                            help=th.help_for("dealer_panel"))
    with dealer_panel:
        ladder = ui.element("div").classes("relative h-[62px] mx-1 w-full")
        dstats = ui.element("div").classes(
            "w-full grid gap-[14px] "
            "[grid-template-columns:repeat(auto-fit,minmax(160px,1fr))]")
    dealer_empty = ui.label("").classes(f"{T.NOTE} px-5 pb-4")

    # ── peers ───────────────────────────────────────────────────────────────
    peer_panel = sh.panel("Where it sits among its peers",
                          help=th.help_for("peers_panel"))
    with peer_panel:
        peer_wrap = ui.element("div").classes(
            "w-full grid gap-3 "
            "[grid-template-columns:repeat(auto-fit,minmax(168px,1fr))]")

    def _paint(a):
        clearance = a.get("direction_clearance") or {}
        sm = a.get("swing_model") or {}

        market.text = _market_line(a)
        chips.clear()
        with chips:
            for c in tt.gate_chips(clearance):
                with ui.row().classes(
                        f"{T.CHIP_BASE} {c['chip_class']} "
                        "px-[10px] py-[3px] gap-[6px] text-[10.5px]"):
                    ui.label(c["icon"]).classes("text-[11px]")
                    ui.label(c["label"])
                    # "LONG CLEARED" / "SHORT RELATIVE ONLY" are the two labels
                    # that most need explaining, and the chip is where a reader
                    # first meets them.
                    sh.tip(th.clearance_help(
                        c["side"], (clearance.get(c["side"]) or {}).get("state")))

        rail_vals = tt.percentile_rail(sm)
        pctl.text = rail_vals["percentile"]
        pctl_note.text = rail_vals["note"]
        rail_tip.text = rail_vals["tip"]
        pctl_stats.text = rail_vals["stats"]
        marker.classes(remove="left-1/2", add=f"left-[{rail_vals['pos_pct']:.1f}%]")

        side_cards.clear()
        with side_cards:
            _side_card("LONG", clearance.get("long"),
                       gate_rows(a.get("position_verdict")))
            _side_card("SHORT", clearance.get("short"),
                       short_gate_rows(a.get("position_verdict")))

        head = swing_headline(sm) if sm else None
        pos_foot.text = (
            "The model predicts 20-day excess return vs SPY — a ranked tilt, "
            "not a trade call." if head else
            "No validated model reading; the legacy heuristic is in use.")

        iv = a.get("investor_verdict") or {}
        verdict.text = (iv.get("verdict") or "—").upper()
        verdict.classes(remove=" ".join(
            ["text-[#2e7d32]", "text-[#c62828]", "text-[#f9a825]"]),
            add=verdict_text_class(iv.get("verdict")))
        score = fmt.num(iv.get("score"))
        verdict_score.text = f"score {score:.0f}" if score is not None else ""
        inv_bars.clear()
        with inv_bars:
            for b in tt.investor_bars(iv):
                with ui.element("div").classes(
                        "w-full grid items-center gap-3 "
                        "[grid-template-columns:minmax(96px,152px)_1fr_46px]"):
                    with ui.label(b["label"]).classes(
                            f"{T.LABEL} leading-[1.35]"):
                        sh.tip(th.factor_help(b["key"]))
                    if b["track_text"]:
                        # A zero-width bar and an empty value column would read
                        # as "measured, and it came to nothing". Say why instead.
                        ui.label(b["track_text"]).classes(
                            f"{T.OFF} text-[11px] italic leading-[1.35]")
                    else:
                        sh.centred_bar(b["left_pct"], b["width_pct"],
                                       b["bar_class"])
                    ui.label(b["value"]).classes(
                        f"{T.MONO} text-[12.5px] text-right {b['value_class']}")
        inv_foot.text = (
            "Schwab publishes no earnings surprises and no company guidance, so "
            "Earnings trajectory can never score — 15 of the 100 points are off "
            "the table for every stock, and the score reads low because of it. "
            "Free cash flow is missing too, so the check that would cap a stock "
            "at HOLD on negative cash flow never runs.")

        marks = tt.dealer_ladder(a.get("dealer_context"), a.get("price"))
        dealer_panel.set_visibility(bool(marks))
        dealer_empty.set_visibility(not marks)
        dealer_empty.text = ("Dealer positioning is withheld — not collected for "
                             "this symbol, or the snapshot is stale.")
        ladder.clear()
        with ladder:
            ui.element("div").classes(
                "absolute left-0 right-0 top-[27px] h-px bg-[#22304c]")
            for m in marks:
                with ui.element("div").classes(
                        "absolute top-0 bottom-0 flex flex-col items-center "
                        "gap-[5px] -translate-x-1/2 "
                        f"left-[{m['pos_pct']:.2f}%]"):
                    if m["emphasis"]:
                        ui.label(m["label"]).classes(
                            f"{T.MONO} text-[11px] font-bold {m['text_class']} "
                            "whitespace-nowrap")
                    ui.element("div").classes(
                        f"h-5 rounded-[2px] {'w-[3px]' if m['emphasis'] else 'w-[2px]'} "
                        + _mark_bg(m["kind"]))
                    if not m["emphasis"]:
                        ui.label(m["label"]).classes(
                            f"{T.MONO} text-[11px] {m['text_class']} "
                            "whitespace-nowrap")
        dstats.clear()
        with dstats:
            # `dealer_rows` yields {"label", "value"} dicts, and a WITHHELD
            # level is simply absent from the list rather than present as None.
            for row in dealer_rows(a.get("dealer_context")):
                with ui.column().classes(
                        "gap-[6px] pl-3 border-l-2 border-[#22304c]"):
                    with ui.label(str(row.get("label", "")).upper()).classes(
                            T.EYEBROW):
                        sh.tip(th.row_help(row.get("label")))
                    ui.label(str(row.get("value", "—"))).classes(
                        f"{T.MONO} text-[16px] font-bold text-[#cfdaee]")

        peers = a.get("peers") or {}
        ranked = peers.get("ranked") or []
        peer_panel.set_visibility(bool(ranked))
        peer_wrap.clear()
        with peer_wrap:
            for p in ranked[:8]:
                _peer_card(p, a.get("symbol"))

    paint.append(_paint)


_MARK_BG = {"put_wall": "bg-[#f87171]", "flip": "bg-[#8b9bb4]",
            "spot": "bg-white", "call_wall": "bg-[#34d399]"}


def _mark_bg(kind):
    return _MARK_BG.get(kind, "bg-[#4a5b7d]")


def _market_line(a):
    c = (a.get("direction_clearance") or {}).get("long") or {}
    reasons = c.get("reasons") or []
    return "; ".join(reasons) if reasons else "No market-state read available."


def _side_card(side, clearance, gates):
    state = (clearance or {}).get("state") or "unknown"
    chip, _icon, word = {
        "cleared": (T.CHIP_POS, "", "Cleared"),
        "relative_only": (T.CHIP_WARN, "", "Relative only"),
        "blocked": (T.CHIP_NEG, "", "Blocked"),
    }.get(state, (T.CHIP_OFF, "", "Unknown"))
    with ui.column().classes(f"gap-[7px] rounded-[10px] border px-[14px] "
                             f"py-[13px] {chip}"):
        # The whole card is the hover target, not just the word: "Cleared" and
        # "Relative only" mean nothing without the reasons listed beneath them,
        # and a reader hovering the reasons wants the same explanation.
        sh.tip(th.clearance_help(side, state))
        ui.label(side).classes("text-[10px] font-extrabold tracking-[0.15em]")
        ui.label(word).classes("text-[14px] font-semibold text-[#e6edf7]")
        detail = "; ".join(g for g in (gates or [])) or \
            "; ".join((clearance or {}).get("reasons") or []) or "No gates fired."
        ui.label(detail).classes(f"{T.NOTE} text-[#8b9bb4]")


def _peer_card(p, symbol):
    sym = (p.get("symbol") or "").upper()
    is_self = sym == (symbol or "").upper()
    pct = fmt.num(p.get("percentile"))
    border = "border-[#3a3f7a] bg-[rgba(99,102,241,0.08)]" if is_self \
        else "border-[#22304c] bg-[rgba(15,23,40,0.5)]"
    with ui.column().classes(f"gap-[9px] rounded-[10px] border px-[14px] "
                             f"py-[13px] {border}"):
        ui.label("THIS SYMBOL" if is_self else "PEER").classes(
            f"{T.EYEBROW} " + ("text-[#818cf8]" if is_self else ""))
        with ui.row().classes("items-baseline gap-[9px]"):
            ui.label(sym).classes(
                f"{T.MONO} text-[15px] font-bold "
                + ("text-[#818cf8]" if is_self else "text-[#cfdaee]"))
            ui.label(f"{int(pct)}th" if pct is not None else "—").classes(
                f"{T.MONO} text-[12px] text-[#7d8db0]")
        with ui.element("div").classes("h-[3px] w-full rounded-[2px] bg-[#17223a]"):
            ui.element("div").classes(
                "h-[3px] rounded-[2px] "
                + ("bg-[#818cf8]" if is_self else "bg-[#4a5b7d]")
                + f" w-[{(pct if pct is not None else 0):.0f}%]")
