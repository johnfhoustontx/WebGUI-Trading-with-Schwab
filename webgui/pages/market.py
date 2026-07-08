"""Market Dashboard page (/market) — Tier-1, engine-free.

Reads cache:market:dashboard (published by market_svc), renders one framed
panel per category with tiles colored by risk-on/off condition. Repaints in
place on the ~2 s version bump. Tailwind-first: data-driven colors map from the
finite color_state set to fixed background classes (no .style()).
"""
import bus_client
from pages.ui_guard import guard
from nicegui import ui

VIEW = "market:dashboard"

# color_state → fixed Tailwind background + text classes (finite map, Tailwind-first).
_BG = {
    "risk_on_strong": "bg-emerald-600/80 text-white",
    "risk_on_mild": "bg-emerald-500/25 text-emerald-100",
    "flat": "bg-slate-600/30 text-slate-200",
    "risk_off_mild": "bg-rose-500/25 text-rose-100",
    "risk_off_strong": "bg-rose-600/80 text-white",
    "no_data": "bg-slate-700/40 text-slate-400",
}


def bg_class(state):
    """Fixed Tailwind bg/text classes for a color_state (neutral fallback)."""
    return _BG.get(state, _BG["no_data"])


def _fmt(v, nd=2):
    try:
        f = float(v)
        return f"{f:.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def tile_text(t):
    """Display strings for a tile: {last, change}."""
    if t.get("last") is None:
        return {"last": "—", "change": ""}
    if t.get("value_only"):
        return {"last": _fmt(t["last"], 0), "change": ""}
    last = _fmt(t["last"])
    pct = t.get("change_pct")
    chg = t.get("change")
    parts = []
    if chg is not None:
        parts.append(f"{'+' if chg >= 0 else ''}{_fmt(chg)}")
    if pct is not None:
        parts.append(f"{'+' if pct >= 0 else ''}{_fmt(pct)}%")
    return {"last": last, "change": "  ".join(parts)}


def render():
    ui.label("Market Dashboard").classes("text-h6 text-slate-100")
    board = ui.row().classes("w-full flex-wrap gap-4 items-start")
    state = {"version": None}

    def _paint(payload):
        board.clear()
        with board:
            for cat in payload.get("categories", []):
                with ui.column().classes(
                        "rounded-lg border border-slate-700 bg-slate-900/40 p-3 gap-2"):
                    ui.label(cat["category"]).classes(
                        "text-xs uppercase tracking-wide text-slate-400")
                    with ui.row().classes("flex-wrap gap-2"):
                        for t in cat["tiles"]:
                            txt = tile_text(t)
                            with ui.column().classes(
                                    f"rounded-md p-2 w-[120px] gap-0 {bg_class(t['color_state'])}"):
                                ui.label(t["display"]).classes("text-sm font-semibold truncate")
                                ui.label(txt["last"]).classes("text-base font-bold")
                                if txt["change"]:
                                    ui.label(txt["change"]).classes("text-xs")

    @guard
    def _poll():
        v = bus_client.read_version(VIEW)
        if v is None:
            return
        if v != state["version"]:
            payload = bus_client.read(VIEW)
            if payload:
                state["version"] = v
                _paint(payload)

    payload = bus_client.read(VIEW)
    if payload:
        state["version"] = bus_client.read_version(VIEW)
        _paint(payload)
    else:
        with board:
            ui.label("Waiting for the market service…").classes("text-slate-400")
    ui.timer(2.0, _poll)
