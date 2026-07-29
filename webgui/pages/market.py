"""Market Dashboard page (/market) — Tier-1, engine-free.

Reads cache:market:dashboard (published by market_svc), renders one framed
panel per category with tiles colored by risk-on/off condition. Repaints in
place on the ~2 s version bump. Tailwind-first: data-driven colors map from the
finite color_state set to fixed background classes (no inline styling).
"""
import bus_client
from pages.ui_guard import guard, guard_async
from nicegui import run, ui

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


def _net_prem_sub(net_m):
    """Compact net-$ subline: '+$2.98B' / '-$540M' from net_m (in $M)."""
    try:
        m = float(net_m)
    except (TypeError, ValueError):
        return ""
    sign = "+" if m >= 0 else "-"
    a = abs(m)
    return f"{sign}${a / 1000:.2f}B" if a >= 1000 else f"{sign}${a:.0f}M"


def _skew_word(pct):
    """Signed skew % -> 'Call 31%' / 'Put 22%' / 'Even' / '—'."""
    if pct is None:
        return "—"
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return "—"
    if abs(p) < 1:
        return "Even"
    return f"Call {p:.0f}%" if p > 0 else f"Put {abs(p):.0f}%"


def prem_line(t):
    """Per-symbol premium subline for a ``prem``-flagged tile (``prem_skew_pct``):
    'Call 31%' / 'Put 22%' / 'Even' / '—' (no data). '' when the tile has no
    premium subline at all (so a min-height keeps every tile the same size)."""
    if "prem_skew_pct" not in t:
        return ""
    return _skew_word(t.get("prem_skew_pct"))


def tile_text(t):
    """Display strings for a tile: {last, change, prem}."""
    if t.get("net_prem"):
        # Dollar-weighted call/put premium skew (the standalone Net Prem tile):
        # headline = "Call 49%"/"Put 22%"/"Even"/"—", subline = net-$ amount.
        pct = t.get("skew_pct")
        if pct is None:
            base = {"last": "—", "change": ""}
        else:
            base = {"last": _skew_word(pct), "change": _net_prem_sub(t.get("net_m"))}
    elif t.get("basket"):
        # Composite tile (BIG10): headline = equal-weighted avg day %-move,
        # subline = breadth (e.g. "3/7 up"). A premium subline (net of the members)
        # is added below via prem_line when the tile is prem-flagged.
        try:
            head = f"{float(t.get('avg_pct')):+.2f}%"
        except (TypeError, ValueError):
            head = "—"
        base = {"last": head, "change": t.get("breadth_text", "")}
    elif t.get("last") is None:
        base = {"last": "—", "change": ""}
    elif t.get("value_only"):
        base = {"last": _fmt(t["last"], 0), "change": ""}
    else:
        last = _fmt(t["last"])
        pct = t.get("change_pct")
        chg = t.get("change")
        parts = []
        if chg is not None:
            parts.append(f"{'+' if chg >= 0 else ''}{_fmt(chg)}")
        if pct is not None:
            parts.append(f"{'+' if pct >= 0 else ''}{_fmt(pct)}%")
        base = {"last": last, "change": "  ".join(parts)}
    return {**base, "prem": prem_line(t)}


def render():
    # No page title — the drawer names the page (2026-07-11 dead-space cleanup).
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
                            # Fixed min-height so every tile in a frame is the same
                            # height whether or not it carries a premium subline.
                            container = ui.column().classes(
                                "rounded-md p-2 w-[120px] min-h-[92px] gap-0 "
                                f"{bg_class(t.get('color_state'))}")
                            with container:
                                ui.label(t.get("display", "")).classes(
                                    "text-sm font-semibold truncate")
                                last_lbl = ui.label(txt["last"]).classes(
                                    "text-base font-bold")
                                change_lbl = ui.label(txt["change"]).classes("text-xs")
                                # Per-symbol call/put premium skew (prem-flagged tiles).
                                prem_lbl = ui.label(txt["prem"]).classes(
                                    "text-[11px] opacity-60")
                                ui.tooltip(t.get("description", ""))
                            state["tiles"][t.get("display")] = {
                                "container": container, "last": last_lbl,
                                "change": change_lbl, "prem": prem_lbl,
                                "state": t.get("color_state")}
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
                if "prem" in h:
                    h["prem"].text = txt["prem"]
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

    @guard_async
    async def _poll():
        # The service publishes every ~2s during RTH, so the version gate passes
        # nearly every poll → read the full ~48-tile payload OFF the event loop.
        # The cheap :ver probe + the paint (UI, in-place tile diff) stay light.
        v = await run.io_bound(bus_client.read_version, VIEW)
        if v is None:
            return
        if v != state["version"]:
            payload = await run.io_bound(bus_client.read, VIEW)
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
