"""Signal Desk — Trade plan.

Two cards side by side. The cleared plan states structure, legs, entry zone,
stop, target, time stop and events, with the **time stop highlighted** as the
model's own horizon. Beside it, a no-trade card states what would change the
verdict and how to express the view anyway if you want the exposure.

Both are always rendered. A blocked side with its reasons is a research finding,
and hiding it would leave the screen looking like the model simply had nothing
to say — which is a different claim entirely.
"""
from nicegui import ui

from pages import fmt
from pages import terminal_theme as T
from pages import trade_shell as sh
from pages.trade import plan_headline, plan_rows

# The one row the design lifts out of the list, because it is the only field
# nothing else in the app enforces.
_KEY_LABEL = "Time stop"


def render():
    sh.page(_build)


def _build(state, refs):
    ui.label("Trade plan").classes(T.SCREEN_TITLE)

    with ui.element("div").classes(
            "w-full grid gap-4 items-start "
            "[grid-template-columns:repeat(auto-fit,minmax(430px,1fr))]"):
        plan_card = sh.panel()
        with plan_card:
            plan_head = ui.row().classes("w-full items-center justify-between "
                                         "gap-3 flex-wrap")
            plan_body = ui.column().classes("w-full gap-0")
            actions = ui.row().classes("gap-[11px] flex-wrap")

        no_trade = sh.panel()
        with no_trade:
            nt_head = ui.row().classes("items-center gap-[11px] flex-wrap")
            nt_summary = ui.element("div").classes(
                "w-full rounded-[10px] border border-[#22304c] "
                "bg-[rgba(15,23,40,0.7)] px-4 py-[14px]")
            nt_conditions = ui.column().classes("w-full gap-3")
            nt_alt = ui.column().classes("w-full gap-[9px] pt-[15px] "
                                         f"border-t border-[#1c2740]")

    def _paint(a):
        plan = a.get("trade_plan") or {}
        clearance = a.get("direction_clearance") or {}
        sm = a.get("swing_model") or {}
        headline, kind = plan_headline(plan)
        rows = plan_rows(plan)
        actionable = bool(rows) and plan.get("action") not in (None, "", "none")

        plan_card.set_visibility(actionable)
        plan_head.clear()
        with plan_head:
            with ui.row().classes("items-center gap-[11px] min-w-0"):
                ui.element("div").classes(
                    "w-[3px] h-[17px] rounded-[2px] bg-[#34d399]")
                ui.label(headline or "Plan").classes(
                    "text-[17px] font-bold tracking-[-0.01em] text-[#f2f6fc]")
                if plan.get("action"):
                    ui.label(str(plan["action"]).upper()).classes(
                        f"{T.CHIP_BASE} {T.CHIP_POS} text-[10.5px] "
                        "tracking-[0.13em] px-[11px] py-[3px]")
            pct = fmt.num(sm.get("percentile"))
            ui.label(f"{int(pct)}th percentile" if pct is not None else "").classes(
                f"{T.MONO} text-[12.5px] text-[#7d8db0]")

        plan_body.clear()
        with plan_body:
            for label, value in rows:
                key = str(label).strip().lower() == _KEY_LABEL.lower()
                cls = ("rounded-lg bg-[rgba(99,102,241,0.08)]" if key else "")
                with ui.element("div").classes(
                        f"w-full grid items-baseline gap-[14px] px-[10px] "
                        f"py-[11px] {T.HAIRLINE} {cls} "
                        "[grid-template-columns:108px_minmax(0,1fr)]"):
                    ui.label(str(label).upper()).classes(
                        "text-[9.5px] font-bold tracking-[0.14em] "
                        + ("text-[#818cf8]" if key else "text-[#56678a]"))
                    ui.label(str(value)).classes(
                        f"text-[15px] font-medium text-[#e6edf7] "
                        + (T.MONO if _looks_numeric(value) else ""))

        actions.clear()
        with actions:
            ui.button("Send to paper trade", color=None).props("no-caps") \
                .classes(T.BTN_PRIMARY)
            ui.button("Open in calculator", color=None).props("no-caps") \
                .classes(T.BTN_GHOST)

        # ── the no-trade side ───────────────────────────────────────────────
        blocked_side = _blocked_side(clearance)
        nt_head.clear()
        with nt_head:
            ui.element("div").classes(
                "w-[3px] h-[17px] rounded-[2px] bg-[#fbbf24]")
            ui.label("No trade" if not actionable else
                     f"{blocked_side.title()} side").classes(
                "text-[17px] font-bold tracking-[-0.01em] text-[#f2f6fc]")
            ui.label(_badge(clearance, blocked_side)).classes(
                f"{T.CHIP_BASE} {T.CHIP_WARN} text-[10.5px] "
                "tracking-[0.13em] px-[11px] py-[3px]")

        nt_summary.clear()
        with nt_summary:
            ui.label(_summary(plan, clearance, actionable)).classes(
                "text-[14px] leading-[1.6] text-[#cfdaee]")

        nt_conditions.clear()
        with nt_conditions:
            ui.label("WHAT WOULD CHANGE IT").classes(T.EYEBROW)
            for c in _conditions(plan, clearance, blocked_side):
                with ui.element("div").classes(
                        "w-full grid items-start gap-3 "
                        "[grid-template-columns:22px_minmax(0,1fr)]"):
                    ui.label("–").classes(
                        "flex items-center justify-center w-[22px] h-[22px] "
                        "rounded-md border border-[#4a3c17] "
                        "bg-[rgba(251,191,36,0.09)] text-[13px] text-[#fbbf24]")
                    ui.label(c).classes(
                        "text-[13.5px] font-semibold text-[#e6edf7]")

        nt_alt.clear()
        with nt_alt:
            ui.label("IF YOU WANT THE EXPOSURE ANYWAY").classes(T.EYEBROW)
            ui.label(_alternative(clearance, blocked_side)).classes(T.BODY)

    refs["paint"].append(_paint)


def _looks_numeric(value):
    s = str(value or "")
    return any(ch.isdigit() for ch in s)


def _blocked_side(clearance):
    for side in ("short", "long"):
        state = ((clearance or {}).get(side) or {}).get("state")
        if state in ("blocked", "relative_only"):
            return side
    return "short"


def _badge(clearance, side):
    state = ((clearance or {}).get(side) or {}).get("state") or "unknown"
    return {"relative_only": "RELATIVE ONLY", "blocked": "BLOCKED",
            "cleared": "CLEARED"}.get(state, "UNKNOWN").upper()


def _summary(plan, clearance, actionable):
    if not actionable:
        return (plan.get("rationale")
                or "The composite sits in the middle band, where the model has "
                   "no edge to express. There is no directional read to hold.")
    side = _blocked_side(clearance)
    state = ((clearance or {}).get(side) or {}).get("state")
    if state == "relative_only":
        return (f"The tape has not cleared a directional {side}. The model "
                "predicts excess return versus SPY, so a bottom-band name is "
                "predicted to LAG the index rather than to fall.")
    if state == "blocked":
        return f"The tape has blocked the {side} side outright."
    return "Both sides are cleared; the plan opposite is the expression."


def _conditions(plan, clearance, side):
    out = list(plan.get("what_would_change_it") or [])
    out += list(((clearance or {}).get(side) or {}).get("reasons") or [])
    return out or ["The composite moving out of the middle band."]


def _alternative(clearance, side):
    state = ((clearance or {}).get(side) or {}).get("state")
    if state == "relative_only":
        return ("Express it as a PAIR — short the name against a long in SPY — "
                "so the position measures the relative call the model actually "
                "made rather than the market's direction.")
    if state == "blocked":
        return ("Wait. A blocked side is the one case where the tape and the "
                "model disagree, and the model has no view on that.")
    return ("Size it against the 20-day horizon: past the time stop the read is "
            "unmodelled, whichever side you take.")
