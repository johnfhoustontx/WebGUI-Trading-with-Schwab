"""Market Dashboard page (/market) — Tier-1, engine-free.

Reads cache:market:dashboard (published by market_svc), renders one framed
panel per category with tiles colored by risk-on/off condition. Repaints in
place on the ~2 s version bump. Tailwind-first: data-driven colors map from the
finite color_state set to fixed background classes (no inline styling).
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


# Union of every class token in _BG — removed before adding the current state's
# class on an in-place recolor so bg/text utilities never stack (sentiment-page
# idiom). dict.fromkeys dedups while preserving order.
_ALL_BG_CLASSES = " ".join(dict.fromkeys(" ".join(_BG.values()).split()))


def _fmt(v, nd=2):
    try:
        f = float(v)
        return f"{f:.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def tile_text(t):
    """Display strings for a tile: {last, change}."""
    if t.get("basket"):
        # Composite tile (MAG7): headline = equal-weighted avg day %-move,
        # subline = breadth (e.g. "3/7 up").
        try:
            head = f"{float(t.get('avg_pct')):+.2f}%"
        except (TypeError, ValueError):
            head = "—"
        return {"last": head, "change": t.get("breadth_text", "")}
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
    # tiles: display -> {"container", "last", "change", "state"} element handles.
    state = {"version": None, "built": False, "tiles": {}}

    def _build(payload):
        """First paint: build the framed board ONCE, stash per-tile handles."""
        board.clear()
        state["tiles"] = {}
        with board:
            for cat in payload.get("categories", []):
                with ui.column().classes(
                        "rounded-lg border border-slate-700 bg-slate-900/40 p-3 gap-2"):
                    ui.label(cat.get("category", "")).classes(
                        "text-xs uppercase tracking-wide text-slate-400")
                    with ui.row().classes("flex-wrap gap-2"):
                        for t in cat.get("tiles", []):
                            txt = tile_text(t)
                            container = ui.column().classes(
                                "rounded-md p-2 w-[120px] gap-0 "
                                f"{bg_class(t.get('color_state'))}")
                            with container:
                                ui.label(t.get("display", "")).classes(
                                    "text-sm font-semibold truncate")
                                last_lbl = ui.label(txt["last"]).classes(
                                    "text-base font-bold")
                                change_lbl = ui.label(txt["change"]).classes("text-xs")
                                ui.tooltip(t.get("description", ""))
                            state["tiles"][t.get("display")] = {
                                "container": container, "last": last_lbl,
                                "change": change_lbl, "state": t.get("color_state")}
        state["built"] = True

    def _update(payload):
        """Subsequent paints: update label text + swap bg class IN PLACE."""
        for cat in payload.get("categories", []):
            for t in cat.get("tiles", []):
                h = state["tiles"].get(t.get("display"))
                if not h:  # a new tile appeared → structure changed; rebuild.
                    _build(payload)
                    return
                txt = tile_text(t)
                h["last"].text = txt["last"]
                h["change"].text = txt["change"]
                new_state = t.get("color_state")
                if new_state != h["state"]:
                    h["container"].classes(
                        remove=_ALL_BG_CLASSES, add=bg_class(new_state))
                    h["state"] = new_state

    def _paint(payload):
        if state["built"]:
            _update(payload)
        else:
            _build(payload)

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

    payload, version = bus_client.read_full(VIEW)
    if payload:
        state["version"] = version
        _paint(payload)
    else:
        with board:
            ui.label("Waiting for the market service…").classes("text-slate-400")
    ui.timer(2.0, _poll)
