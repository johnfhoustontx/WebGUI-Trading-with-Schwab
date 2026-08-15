"""Market Dashboard page (/market) — Tier-1, engine-free. Macro Board redesign.

Reads cache:market:dashboard (published by market_svc) and renders the "Macro
Board": ~90 quote tiles in notched, accent-barred category panels under a top
rail (title / live dot / clock / breadth meter / skin toggle). Repaints IN PLACE
on the ~2 s version bump; only tiles whose displayed value actually changed
flash (ignition bar + price flare). Two skins — A (Instrument, default) and B
(Heat Lattice) — toggled by a segmented control and persisted in app_settings.

Design principle: spend the intensity budget only on tiles that changed. The
flat majority stays recessed; colour/glow/motion are reserved for movers.

Tailwind-first per house rules: layout / spacing / colour tokens are Tailwind
classes; the ONE ``ui.add_css`` block (``theme.MACRO_CSS``) carries only what
Tailwind cannot express — clip-path notches, the flash keyframes, the radial
page ground and the per-tile custom-prop washes. Colours come from
``config/theme.toml [macro]``.

IMPORTANT — colour semantics: direction (up/down/flat) keys on the service's
``color_state``, NOT the raw sign of %change. This board is polarity-aware (VIX
up = risk-off = red, TLT up = red, …), which the prototype's naïve pct-sign
colouring would break. Wash MAGNITUDE still scales with |%change|.
"""
import app_settings
import bus_client
from nicegui import run, ui
from pages.options.theme import (
    MACRO_COLORS as _MC,
    MACRO_CSS,
    MACRO_FONT_HEAD_HTML,
    MACRO_TOKENS as _T,
)
from pages.ui_guard import guard, guard_async

VIEW = "market:dashboard"

# category → left-accent hex (from the reference prototype's G array, keyed by
# the REAL market_svc category names). Pure presentation; cyan fallback.
_ACCENT = {
    "Volatility": "#FFB627",
    "Options Sentiment": "#C86BFF",
    "Market Internals / Breadth": "#35E0FF",
    "Currency": "#FF7A3D",
    "Cash Index": "#A9C3E8",
    "Equity Index Futures": "#A9C3E8",
    "Broad-Market ETF": "#3D7BFF",
    "Top 10": "#00E5A0",
    "Sector SPDR": "#22D3EE",
    "Thematic / Industry ETF": "#C86BFF",
    "Factor / Momentum ETF": "#FFB627",
    "Fixed Income / Credit ETF": "#6E82A3",
    "Crypto / Alternatives": "#FF3DCB",
    "Countries": "#35E0FF",
}


def accent_of(cat):
    return _ACCENT.get(cat, _MC["cyan"])


# color_state → direction. Preserves the board's semantic polarity (a red VIX on
# an up move is risk_off_* → "dn"), unlike a raw pct-sign read.
_DIR = {
    "risk_on_strong": "up", "risk_on_mild": "up",
    "risk_off_strong": "dn", "risk_off_mild": "dn",
    "flat": "flat", "no_data": "flat",
}
_RGB = {"up": "0,229,160", "dn": "255,77,109"}
_HEAT_FLAT = "rgba(20,30,48,0.55)"
_BUCKETS = 6  # magnitude quantisation levels (0..5) → a finite class palette


def tile_direction(t):
    """up / dn / flat from the service's polarity-aware color_state."""
    return _DIR.get(t.get("color_state"), "flat")


def _tile_pct(t):
    """The tile's %-change (avg for the basket), or None (value-only / external)."""
    v = t.get("avg_pct") if t.get("basket") else t.get("change_pct")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tile_magnitude(t):
    """0..1 heat magnitude = |%change| / saturation-ceiling, capped. Tiles with no
    numeric %change (internals, put/call, net-prem) fall back to the color_state
    intensity tier so a 'strong' still reads hotter than a 'mild'."""
    pct = _tile_pct(t)
    if pct is not None:
        return min(abs(pct) / _MC["sat_ceiling"], 1.0)
    st = t.get("color_state") or ""
    if st.endswith("strong"):
        return 1.0
    if st.endswith("mild"):
        return 0.5
    return 0.0


def _bucket(mag):
    return max(0, min(_BUCKETS - 1, int(round(mag * (_BUCKETS - 1)))))


def dir_color_class(direction):
    """`[--c:#hex]` — the direction colour the ignition bar + price flare use."""
    hexs = {"up": _MC["up"], "dn": _MC["dn"], "flat": _MC["flat"]}
    return f"[--c:{hexs.get(direction, _MC['flat'])}]"


def wash_class(direction, mag):
    """Skin-A magnitude wash `[--wash:rgba(...)]` (alpha 0.03 + m·0.11), or
    transparent for flat. Quantised to a finite palette (Tailwind-first)."""
    if direction == "flat" or mag <= 0:
        return "[--wash:transparent]"
    b = _bucket(mag)
    alpha = round(0.03 + (b / (_BUCKETS - 1)) * 0.11, 3)
    return f"[--wash:rgba({_RGB[direction]},{alpha})]"


def heat_class(direction, mag):
    """Skin-B heat fill `[--heat:rgba(...)]` (alpha 0.06 + m·0.30); a dim slate
    for flat. Quantised to a finite palette."""
    if direction == "flat" or mag <= 0:
        return f"[--heat:{_HEAT_FLAT}]"
    b = _bucket(mag)
    alpha = round(0.06 + (b / (_BUCKETS - 1)) * 0.30, 3)
    return f"[--heat:rgba({_RGB[direction]},{alpha})]"


def border_class(direction):
    return {
        "up": "border-[rgba(0,229,160,0.26)]",
        "dn": "border-[rgba(255,77,109,0.26)]",
        "flat": f"border-[{_MC['edge']}]",
    }[direction]


def change_text_class(direction):
    return {"up": _T["MB_UP"], "dn": _T["MB_DN"], "flat": _T["MB_FLAT"]}[direction]


def _fmt(v, nd=2):
    try:
        return f"{float(v):.{nd}f}"
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


def tile_text(t):
    """The tile's price + change strings: {last, change}."""
    if t.get("net_prem"):
        pct = t.get("skew_pct")
        if pct is None:
            return {"last": "—", "change": ""}
        return {"last": _skew_word(pct), "change": _net_prem_sub(t.get("net_m"))}
    if t.get("basket"):
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


_DESC_MAX = 22


def descriptor_line(t):
    """The tile's third line: the option-skew where a tile has it (SPX/NDX/broad
    ETFs / Top-10 / BIG10), else the symbol's description (uppercased, truncated).
    Preserves the skew line the spec requires while giving every other tile a
    glanceable descriptor."""
    if "prem_skew_pct" in t:
        return _skew_word(t.get("prem_skew_pct"))
    d = (t.get("description") or "").strip().upper()
    if len(d) <= _DESC_MAX:
        return d
    return d[:_DESC_MAX - 1] + "…"


def order_class(i):
    """Tailwind flex `order-N` mirroring the payload rank (leaderboard frames)."""
    n = i + 1
    return f"order-{n}" if n <= 12 else f"order-[{n}]"


def tile_signature(t):
    """A comparable value for change detection — the displayed price+change. A
    repaint with an identical signature must NOT flash (else every tile strobes
    every 2 s and the signal is worthless)."""
    tx = tile_text(t)
    return (tx["last"], tx["change"])


def breadth_counts(payload):
    """(advancing, declining) across every tile on the board, by direction."""
    up = dn = 0
    for cat in payload.get("categories", []):
        for t in cat.get("tiles", []):
            d = tile_direction(t)
            if d == "up":
                up += 1
            elif d == "dn":
                dn += 1
    return up, dn


def flex_class(weight):
    """A proportional flex-grow class for the breadth bar (continuous value →
    runtime arbitrary, the documented exception; reset via remove/add)."""
    return f"flex-[{max(0, int(weight))}_1_0%]"


def _reactive_classes(t):
    """Every value-driven class a tile swaps on update, as a dict keyed by role
    (so each can be diffed + removed/added individually — no stacking)."""
    d = tile_direction(t)
    mag = tile_magnitude(t)
    return {
        "cvar": dir_color_class(d),
        "wash": wash_class(d, mag),
        "heat": heat_class(d, mag),
        "border": border_class(d),
        "chtext": change_text_class(d),
        "opacity": "opacity-[.62]" if d == "flat" else "opacity-100",
    }


def render():
    from datetime import datetime

    ui.add_head_html(MACRO_FONT_HEAD_HTML)
    ui.add_css(MACRO_CSS)

    skin = "B" if str(app_settings.get("macro_skin") or "A").upper() == "B" else "A"
    state = {"version": None, "built": False, "tiles": {}, "skin": skin}

    # ── shell ────────────────────────────────────────────────────────────────
    wrap = ui.column().classes(
        f"macro-board macro-{skin.lower()} w-full gap-[18px] pb-16")

    with wrap:
        # ---- top rail ----
        with ui.row().classes(
                f"mb-rail {_T['MB_EDGE']} border items-center gap-6 flex-wrap "
                "w-full px-5 py-3.5"):
            with ui.row().classes("items-baseline gap-3"):
                ui.label("MACRO BOARD").classes(
                    f"{_T['MB_TITLE']} {_T['MB_TXT']} text-[17px] font-bold "
                    "tracking-[.18em]")
                ui.label("LIVE TAPE").classes(
                    f"{_T['MB_MONO']} {_T['MB_FAINT']} text-[10px] tracking-[.24em]")
            with ui.row().classes("items-center gap-2"):
                ui.element("div").classes(
                    f"mb-dot w-[7px] h-[7px] {_T['MB_UP'].replace('text-', 'bg-')} "
                    "shadow-[0_0_10px_#00E5A0]")
                ui.label("STREAMING").classes(
                    f"{_T['MB_MONO']} {_T['MB_UP']} text-[10.5px] tracking-[.2em]")
            with ui.column().classes("gap-[3px]"):
                ui.label("SESSION").classes(
                    f"{_T['MB_MONO']} {_T['MB_FAINT']} text-[9px] tracking-[.2em]")
                clock_lbl = ui.label("—").classes(
                    f"{_T['MB_MONO']} {_T['MB_TXT']} text-[14px] tabular-nums")
            # breadth meter
            with ui.column().classes("gap-[5px] min-w-[190px]"):
                with ui.row().classes(
                        f"{_T['MB_MONO']} {_T['MB_FAINT']} text-[9.5px] "
                        "tracking-[.14em] justify-between w-full"):
                    ui.label("ADVANCING")
                    ui.label("DECLINING")
                with ui.row().classes("h-[7px] gap-[2px] w-full") as bbar:
                    bar_up = ui.element("div").classes(
                        f"mb-shear {_T['MB_UP'].replace('text-', 'bg-')} "
                        "flex-[1_1_0%] shadow-[0_0_8px_rgba(0,229,160,0.5)]")
                    bar_dn = ui.element("div").classes(
                        f"mb-shear {_T['MB_DN'].replace('text-', 'bg-')} "
                        "flex-[1_1_0%] shadow-[0_0_8px_rgba(255,77,109,0.5)]")
                with ui.row().classes(
                        f"{_T['MB_MONO']} text-[9.5px] tracking-[.14em] "
                        "justify-between w-full"):
                    bup_lbl = ui.label("0").classes(_T["MB_UP"])
                    bdn_lbl = ui.label("0").classes(_T["MB_DN"])
            ui.space()
            # skin toggle (segmented)
            with ui.row().classes(f"{_T['MB_EDGE']} border") as seg:
                btn_a = ui.button("INSTRUMENT", on_click=lambda: _set_skin("A")) \
                    .props("flat no-caps").classes(
                        f"{_T['MB_SYM']} text-[11.5px] tracking-[.18em] rounded-none")
                btn_b = ui.button("HEAT LATTICE", on_click=lambda: _set_skin("B")) \
                    .props("flat no-caps").classes(
                        f"{_T['MB_SYM']} text-[11.5px] tracking-[.18em] rounded-none")
        board = ui.row().classes("flex-wrap gap-[13px] items-start w-full")

    _SEG_ON = f"{_T['MB_CYAN']} bg-[rgba(53,224,255,0.13)]"
    _SEG_OFF = _T["MB_DIM"]

    def _paint_seg():
        on, off = _SEG_ON.split(), _SEG_OFF.split()
        btn_a.classes(remove=" ".join(on + off),
                      add=_SEG_ON if state["skin"] == "A" else _SEG_OFF)
        btn_b.classes(remove=" ".join(on + off),
                      add=_SEG_ON if state["skin"] == "B" else _SEG_OFF)

    @guard
    def _set_skin(s):
        state["skin"] = s
        wrap.classes(remove="macro-a macro-b", add=f"macro-{s.lower()}")
        app_settings.set("macro_skin", s)
        _paint_seg()

    # ── tiles ──────────────────────────────────────────────────────────────
    def _build(payload):
        board.clear()
        state["tiles"] = {}
        with board:
            for cat in payload.get("categories", []):
                name = cat.get("category", "")
                with ui.column().classes(
                        f"mb-panel {_T['MB_PANEL_BG']} {_T['MB_EDGE']} border "
                        f"[--mb-acc:{accent_of(name)}] px-[11px] pt-[10px] "
                        "pb-[11px] gap-[9px]"):
                    with ui.row().classes("items-center gap-[9px] w-full pl-[9px]"):
                        ui.label(name.upper()).classes(
                            f"{_T['MB_MONO']} {_T['MB_DIM']} text-[9.5px] "
                            "tracking-[.24em]")
                        ui.element("div").classes(
                            f"flex-1 h-px bg-gradient-to-r from-[{_MC['edge']}] "
                            "to-transparent")
                    with ui.row().classes("flex-wrap gap-[7px]"):
                        for i, t in enumerate(cat.get("tiles", [])):
                            _build_tile(t, i)
        state["built"] = True

    def _build_tile(t, i):
        tx = tile_text(t)
        rc = _reactive_classes(t)
        oc = order_class(i)
        container = ui.column().classes(
            f"mb-tile {_T['MB_TILE_BG']} border {rc['border']} {rc['opacity']} "
            f"{rc['cvar']} {rc['wash']} {rc['heat']} {oc} "
            "min-w-[104px] px-[11px] pt-[9px] pb-[8px] gap-0")
        with container:
            ui.element("div").classes("mb-ig")
            ui.label(t.get("display", "")).classes(
                f"mb-sym {_T['MB_SYM']} {_T['MB_DIM']} text-[11px] font-bold "
                "tracking-[.13em]")
            px_lbl = ui.label(tx["last"]).classes(
                f"mb-px {_T['MB_MONO']} {_T['MB_TXT']} text-[17px] font-medium "
                "tabular-nums mt-[3px] leading-tight")
            ch_lbl = ui.label(tx["change"]).classes(
                f"{_T['MB_MONO']} {rc['chtext']} text-[10.5px] tabular-nums mt-[2px]")
            ex_lbl = ui.label(descriptor_line(t)).classes(
                f"{_T['MB_MONO']} {_T['MB_FAINT']} text-[9px] tracking-[.05em] "
                "mt-[2px] truncate max-w-[128px]")
            ui.tooltip(t.get("description", ""))
        state["tiles"][t.get("display")] = {
            "el": container, "px": px_lbl, "ch": ch_lbl, "ex": ex_lbl,
            "rc": rc, "order": oc, "sig": tile_signature(t)}

    def _update(payload):
        flashed = []
        for cat in payload.get("categories", []):
            for i, t in enumerate(cat.get("tiles", [])):
                h = state["tiles"].get(t.get("display"))
                if not h:  # structure changed → rebuild
                    _build(payload)
                    return
                oc = order_class(i)
                if oc != h["order"]:
                    h["el"].classes(remove=h["order"], add=oc)
                    h["order"] = oc
                tx = tile_text(t)
                h["px"].text = tx["last"]
                h["ch"].text = tx["change"]
                h["ex"].text = descriptor_line(t)
                # diff the value-driven classes, swapping only what moved
                new_rc = _reactive_classes(t)
                for role, cls in new_rc.items():
                    if cls != h["rc"].get(role):
                        h["el"].classes(remove=h["rc"][role], add=cls)
                        if role == "chtext":
                            h["ch"].classes(remove=h["rc"][role], add=cls)
                        h["rc"][role] = cls
                # change detection → flash only actual movers
                sig = tile_signature(t)
                if sig != h["sig"]:
                    h["sig"] = sig
                    flashed.append(h["el"].id)
        if flashed:
            _flash(flashed)
        _paint_breadth(payload)

    def _flash(ids):
        # Retrigger the CSS flash on changed tiles in ONE round-trip. Removing +
        # re-adding the class in the same frame won't restart a CSS animation —
        # force a reflow (void offsetWidth) between, per the spec.
        js = ("for(const id of %s){const e=getElement(id);"
              "if(e){e.classList.remove('fl');void e.offsetWidth;"
              "e.classList.add('fl');}}" % ids)
        ui.run_javascript(js)

    def _paint_breadth(payload):
        up, dn = breadth_counts(payload)
        bup_lbl.text, bdn_lbl.text = str(up), str(dn)
        nu, nd = flex_class(up or 1), flex_class(dn or 1)
        if nu != state.get("bar_up"):
            bar_up.classes(remove=state.get("bar_up", "flex-[1_1_0%]"), add=nu)
            state["bar_up"] = nu
        if nd != state.get("bar_dn"):
            bar_dn.classes(remove=state.get("bar_dn", "flex-[1_1_0%]"), add=nd)
            state["bar_dn"] = nd

    def _paint(payload):
        if state["built"]:
            _update(payload)
        else:
            _build(payload)
            _paint_breadth(payload)

    @guard
    def _tick_clock():
        clock_lbl.text = datetime.now().strftime("%H:%M:%S")

    @guard_async
    async def _poll():
        v = await run.io_bound(bus_client.read_version, VIEW)
        if v is None or v == state["version"]:
            return
        payload = await run.io_bound(bus_client.read, VIEW)
        if payload:
            state["version"] = v
            _paint(payload)

    _paint_seg()
    _tick_clock()
    payload, version = bus_client.read_full(VIEW)
    if payload:
        state["version"] = version
        _paint(payload)
    else:
        with board:
            ui.label("Waiting for the market service…").classes(_T["MB_DIM"])
    ui.timer(1.0, _tick_clock)
    ui.timer(2.0, _poll)
