from pages import market


# ── price / change text (unchanged data contract) ────────────────────────────
def test_tile_text_formats_last_and_change():
    t = {"display": "VIX", "last": 16.13, "change": None, "change_pct": 3.6,
         "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "16.13"
    assert "3.6" in txt["change"] and "%" in txt["change"]


def test_tile_text_negative_change_signs():
    t = {"display": "SPX", "last": 7503.85, "change": -1.5, "change_pct": -0.8,
         "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "7503.85"
    assert "+" not in txt["change"]
    assert "-1.50" in txt["change"] and "-0.80%" in txt["change"]


def test_tile_text_value_only_hides_change():
    t = {"display": "$TICK", "last": 300.0, "change": None, "change_pct": None,
         "value_only": True}
    txt = market.tile_text(t)
    assert txt["last"] == "300" and txt["change"] == ""


def test_tile_text_no_data():
    assert market.tile_text({"last": None, "change_pct": None})["last"] == "—"


def test_tile_text_net_prem_and_basket():
    call = {"net_prem": True, "skew_pct": 49.0, "net_m": 2983.3}
    assert market.tile_text(call) == {"last": "Call 49%", "change": "+$2.98B"}
    mag = {"basket": True, "avg_pct": 0.34, "breadth_text": "8/10 up"}
    assert market.tile_text(mag) == {"last": "+0.34%", "change": "8/10 up"}


# ── direction (polarity-aware, keyed on color_state NOT raw pct) ──────────────
def test_tile_direction_is_semantic_not_raw_pct():
    # A red VIX on an up move is risk_off_* → "dn", NOT "up".
    assert market.tile_direction({"color_state": "risk_off_strong"}) == "dn"
    assert market.tile_direction({"color_state": "risk_on_mild"}) == "up"
    assert market.tile_direction({"color_state": "flat"}) == "flat"
    assert market.tile_direction({"color_state": "no_data"}) == "flat"
    assert market.tile_direction({}) == "flat"


# ── magnitude + heat/wash bucketing ──────────────────────────────────────────
def test_tile_magnitude_scales_and_caps():
    ceil = market._MC["sat_ceiling"]
    assert market.tile_magnitude({"change_pct": 0.0}) == 0.0
    assert market.tile_magnitude({"change_pct": ceil}) == 1.0
    assert market.tile_magnitude({"change_pct": ceil * 4}) == 1.0     # capped
    assert 0 < market.tile_magnitude({"change_pct": ceil / 2}) < 1
    # basket uses avg_pct
    assert market.tile_magnitude({"basket": True, "avg_pct": ceil}) == 1.0
    # no pct (internals/external) → color_state intensity tier fallback
    assert market.tile_magnitude({"color_state": "risk_off_strong"}) == 1.0
    assert market.tile_magnitude({"color_state": "risk_on_mild"}) == 0.5
    assert market.tile_magnitude({"color_state": "flat"}) == 0.0


def test_wash_and_heat_classes_are_finite_no_var_no_spaces():
    for d in ("up", "dn"):
        w = market.wash_class(d, 1.0)
        h = market.heat_class(d, 1.0)
        for cls in (w, h):
            assert cls.startswith("[--") and cls.endswith("]")
            assert "var(" not in cls and " " not in cls      # JIT-safe
    assert market.wash_class("flat", 0.0) == "[--wash:transparent]"
    assert "rgba(20,30,48" in market.heat_class("flat", 0.0)       # dim slate
    # hotter magnitude → higher alpha
    lo = market.wash_class("up", 0.1)
    hi = market.wash_class("up", 1.0)
    assert lo != hi


def test_dir_color_and_border_and_change_classes():
    assert market.dir_color_class("up").startswith("[--c:")
    assert market.dir_color_class("dn") != market.dir_color_class("up")
    assert market.border_class("up") != market.border_class("dn")
    assert " " not in market.border_class("up")
    assert market.change_text_class("up") == market._T["MB_UP"]
    assert market.change_text_class("flat") == market._T["MB_FLAT"]


# ── descriptor line: skew where present, else description ─────────────────────
def test_descriptor_line_prefers_skew_else_description():
    assert market.descriptor_line({"prem_skew_pct": 42.9}) == "Call 43%"
    assert market.descriptor_line({"prem_skew_pct": -22.0}) == "Put 22%"
    assert market.descriptor_line({"prem_skew_pct": None}) == "—"   # flagged, no data
    # no skew → the description, uppercased
    assert market.descriptor_line({"description": "20Y TSY"}) == "20Y TSY"
    assert market.descriptor_line({"description": "iShares 20+ yr"}) == "ISHARES 20+ YR"
    # long descriptions truncate with an ellipsis
    long = market.descriptor_line({"description": "x" * 40})
    assert len(long) <= market._DESC_MAX and long.endswith("…")
    assert market.descriptor_line({}) == ""


# ── Skin-B (Heat Lattice) legibility ─────────────────────────────────────────
# The lattice paints the whole tile with the heat fill, which at full magnitude
# is rgba(0,229,160,.36) over the void -- bright enough that the board's dark
# text ramp collapses onto it. Measured live on 2026-08-28 the skew line
# ("Call 31%") sat at 1.08:1 and the symbol at 2.2:1, i.e. not readable at all.
# These pin the PROPERTY (contrast + reading order), never the hexes, so a
# palette edit that re-breaks it fails here instead of shipping.
def _srgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg_hex, bg_rgb):
    a, b = _luminance(_srgb(fg_hex)), _luminance(bg_rgb)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _hottest_tile_bg(direction):
    """The Skin-B tile background at full magnitude, composited over the void —
    parsed out of `heat_class` so the test follows the real alpha ramp."""
    cls = market.heat_class(direction, 1.0)
    nums = cls[cls.index("(") + 1:cls.index(")")].split(",")
    r, g, b, alpha = (float(n) for n in nums)
    void = _srgb(market._MC["void"])
    return tuple(alpha * c + (1 - alpha) * void[i]
                 for i, c in enumerate((r, g, b)))


def test_lattice_text_ramp_is_legible_on_the_hottest_tile():
    from pages.options.theme import THEME
    m = THEME["macro"]
    for direction in ("up", "dn"):           # green is the worse of the two
        bg = _hottest_tile_bg(direction)
        desc = _contrast(m["lattice_desc"], bg)
        sym = _contrast(m["lattice_sym"], bg)
        price = _contrast(m["txt"], bg)
        assert desc >= 4.5, f"{direction} descriptor {desc:.2f}:1"
        assert sym >= 4.5, f"{direction} symbol {sym:.2f}:1"
        # reading order must survive the lift: price > symbol > descriptor
        assert price > sym > desc


def test_lattice_ramp_is_scoped_to_skin_b_and_hooks_the_real_classes():
    from pages.options.theme import MACRO_CSS, THEME
    m = THEME["macro"]
    for sel, colour in ((".mb-sym", m["lattice_sym"]),
                        (".mb-desc", m["lattice_desc"])):
        rule = f".macro-board.macro-b .mb-tile {sel}{{color:{colour}}}"
        assert rule in MACRO_CSS
    # Skin A keeps the dark ramp — the lift must not leak out of the lattice.
    assert ".macro-a .mb-tile .mb-desc" not in MACRO_CSS


# ── breadth counts over the four equity frames ───────────────────────────────
def test_breadth_counts_only_the_equity_frames():
    # The rail's advance/decline is a read on the EQUITY tape, so it counts only
    # the four stock frames — a red VIX or a bid Treasury is not a decline.
    payload = {"categories": [
        {"category": "Broad-Market ETF",
         "tiles": [{"color_state": "risk_on_strong"},
                   {"color_state": "risk_on_mild"},
                   {"color_state": "flat"}]},
        {"category": "Sector SPDR",
         "tiles": [{"color_state": "risk_off_mild"},
                   {"color_state": "no_data"}]},
        {"category": "Volatility",       # excluded frame
         "tiles": [{"color_state": "risk_off_strong"},
                   {"color_state": "risk_on_strong"}]},
        {"category": "Fixed Income / Credit ETF",   # excluded frame
         "tiles": [{"color_state": "risk_off_strong"}]},
    ]}
    assert market.breadth_counts(payload) == (2, 1)


def test_breadth_categories_are_the_four_requested_and_real_frames():
    from services.market_svc import symbols
    assert market.BREADTH_CATEGORIES == (
        "Broad-Market ETF", "Top 10", "Sector SPDR", "Thematic / Industry ETF")
    # a typo here would silently count nothing at all
    assert set(market.BREADTH_CATEGORIES) <= set(symbols.CATEGORY_ORDER)


def test_iyt_counts_in_the_advance_decline_meter_from_the_producer():
    # Driven from market_svc's own build_dashboard, not a hand-written payload:
    # IYT only reaches the meter if the service files it under a counted frame.
    from services.market_svc import compute
    for pct, expected in ((1.2, (1, 0)), (-1.2, (0, 1))):
        raw = {"IYT": {"assetMainType": "EQUITY",
                       "quote": {"lastPrice": 70.0, "netPercentChange": pct}}}
        payload = compute.build_dashboard(raw, sector_pcr=None, proxy_up=True)
        thematic = next(c for c in payload["categories"]
                        if c["category"] == "Thematic / Industry ETF")
        assert "IYT" in [t["display"] for t in thematic["tiles"]]
        assert market.breadth_counts(payload) == expected


def test_breadth_counts_skip_the_basket_composite():
    # BIG10 is the AVERAGE of the ten constituents sitting beside it in the same
    # frame — counting it too would double-count the mega-caps.
    payload = {"categories": [{"category": "Top 10", "tiles": [
        {"color_state": "risk_on_strong", "basket": True},
        {"color_state": "risk_on_strong"},
        {"color_state": "risk_off_mild"},
    ]}]}
    assert market.breadth_counts(payload) == (1, 1)


def test_breadth_counts_ignore_an_unnamed_category():
    assert market.breadth_counts(
        {"categories": [{"tiles": [{"color_state": "risk_on_strong"}]}]}) == (0, 0)


def test_flex_class_is_proportional_arbitrary():
    assert market.flex_class(7) == "flex-[7_1_0%]"
    assert market.flex_class(0) == "flex-[0_1_0%]"


# ── change detection signature (flash only on real change) ───────────────────
def test_tile_signature_changes_only_with_displayed_value():
    a = {"last": 100.0, "change": 1.0, "change_pct": 1.0}
    b = {"last": 100.0, "change": 1.0, "change_pct": 1.0}
    c = {"last": 100.5, "change": 1.5, "change_pct": 1.5}
    assert market.tile_signature(a) == market.tile_signature(b)   # identical → no flash
    assert market.tile_signature(a) != market.tile_signature(c)   # moved → flash


def test_accent_map_covers_categories_with_fallback():
    assert market.accent_of("Top 10") == "#00E5A0"
    assert market.accent_of("Volatility") == "#FFB627"
    assert market.accent_of("Unknown Category") == market._MC["cyan"]


# ── order / rank (unchanged) ─────────────────────────────────────────────────
def test_order_class_maps_payload_position():
    assert market.order_class(0) == "order-1"
    assert market.order_class(11) == "order-12"
    assert market.order_class(12) == "order-[13]"
    assert len({market.order_class(i) for i in range(15)}) == 15


# ── render wiring: change detection, flash, off-loop read ────────────────────
def test_render_flashes_only_changed_tiles_off_loop():
    import inspect
    src = inspect.getsource(market.render)
    # change detection: compare signature, flash only movers
    assert "tile_signature(" in src and 'h["sig"]' in src
    # reflow-retrigger of the CSS flash (spec §6)
    assert "void e.offsetWidth" in src and "classList.add('fl')" in src
    # in-place re-rank, not rebuild
    assert 'remove=h["order"]' in src
    # payload read off the event loop
    assert "async def _poll" in src and "run.io_bound(bus_client.read" in src
    # skin persistence
    assert 'app_settings.set("macro_skin"' in src


# ── the Macro Board on the page kit (Phases 3 & 4, Task 7) ───────────────────
# THE TRAP IN THIS MIGRATION: every rule in ``MACRO_CSS`` is scoped under
# ``.macro-board`` / ``.macro-a|b``, so the wrapper classes are what the DATA
# effects hang off. Dropping them as "chrome" would kill the ignition bar, the
# price flare, the magnitude wash, the heat fill and the lattice bloom at once.
def _board_payload():
    """A ``market:dashboard`` payload shaped like ``market_svc``'s output."""
    return {"categories": [
        {"category": "Volatility", "tiles": [
            {"display": "VIX", "last": 16.13, "change": 0.4, "change_pct": 3.6,
             "color_state": "risk_off_mild", "description": "CBOE VIX"}]},
        {"category": "Broad-Market ETF", "tiles": [
            {"display": "SPY", "last": 640.2, "change": 1.2, "change_pct": 0.19,
             "color_state": "risk_on_mild", "description": "S&P 500 ETF"},
            {"display": "QQQ", "last": 570.1, "change": -0.8, "change_pct": -0.14,
             "color_state": "risk_off_mild", "description": "Nasdaq 100 ETF"}]},
    ]}


def _render_market(monkeypatch, payload=None):
    """Build the page against the auto-index client and return ONLY the
    elements THIS render built.

    The auto-index client is shared by every test in the session, so a plain
    ``elements.values()`` would also hand back controls another page's render
    left behind, and these assertions would then pass whatever this page did
    (rule 10 of the plan, paid for on Task 1)."""
    import bus_client
    from nicegui import ui
    from pages import market as M
    monkeypatch.setattr(bus_client, "read_full",
                        lambda _v: (payload, 1) if payload else (None, None))
    monkeypatch.setattr(bus_client, "read", lambda _v: payload)
    monkeypatch.setattr(bus_client, "read_version",
                        lambda _v: 1 if payload else None)
    before = set(ui.context.client.elements)
    with ui.card():
        M.render()
    return [e for i, e in ui.context.client.elements.items() if i not in before]


def _classes(elements):
    return [" ".join(getattr(e, "_classes", []) or []) for e in elements]


def test_the_macro_frame_is_the_kit_and_carries_no_surface_of_its_own():
    """The page loses its own ground, its three faces and its text ladder.

    ``stale=True`` is honest here: ``market:dashboard`` is the scheduled view
    ``status.py`` already marks ``True``, and it is the page's only view."""
    import inspect
    src = inspect.getsource(market.render)
    assert "kit.page()" in src
    assert 'kit.header("Macro Board", view=VIEW, stale=True)' in src
    # A ground, a text step, a face and a border are surface WHEREVER they live
    # — in the frame or in a module-level helper (the db39442 lesson).
    whole = inspect.getsource(market)
    for token in ("MACRO_FONT_HEAD_HTML", "MB_MONO", "MB_TITLE", "MB_SYM",
                  "MB_TXT", "MB_DIM", "MB_FAINT", "MB_EDGE", "MB_PANEL_BG",
                  "MB_TILE_BG", "MB_CYAN"):
        assert token not in whole, f"{token} is a page-scoped surface value"


def test_the_board_wrapper_still_carries_the_classes_the_effects_hang_off(
        monkeypatch):
    """THE TRAP, pinned. ``.macro-board`` and the skin class scope EVERY rule in
    ``MACRO_CSS`` — the ignition bar, the price flare, the Skin-A magnitude
    wash, the Skin-B heat fill and the lattice bloom. They are not chrome; they
    are what the data effects hang off.

    Passes before and after the migration by design — the guard on what must
    NOT change, not a red-then-green test."""
    els = _render_market(monkeypatch, _board_payload())
    wrappers = [c for c in _classes(els) if "macro-board" in c]
    assert wrappers, "the board wrapper lost .macro-board"
    assert any("macro-a" in c or "macro-b" in c for c in wrappers), \
        "the board wrapper lost its skin class"
    joined = " ".join(_classes(els))
    for hook in ("mb-tile", "mb-ig", "mb-px", "mb-sym", "mb-desc", "mb-panel"):
        assert hook in joined, f"{hook} is a MACRO_CSS selector hook"


def test_the_streaming_claim_and_the_naive_local_clock_are_gone(monkeypatch):
    """Two honesty fixes the header makes for free. The static "STREAMING" pill
    and its pulsing dot claimed a stream the page cannot back; the SESSION clock
    rendered naive machine-local ``%H:%M:%S`` — the only clock in the app that
    is neither Central nor a data stamp. The kit stamp says the true thing."""
    import inspect
    # ``render``'s own source, not the module's: the docstring NAMES what was
    # removed and why, which is the record, not a claim on screen.
    src = inspect.getsource(market.render)
    for gone in ("STREAMING", "SESSION", "_tick_clock", "mb-dot",
                 '"%H:%M:%S"'):
        assert gone not in src, f"{gone} is a claim the page cannot back"
    els = _render_market(monkeypatch, _board_payload())
    texts = {str(getattr(e, "text", "")) for e in els}
    assert "STREAMING" not in texts and "SESSION" not in texts
    assert not any("mb-dot" in c for c in _classes(els))
    assert inspect.getsource(market.render).count("ui.timer(") == 1, \
        "the 1 s clock timer goes with the clock"


def test_the_skin_toggle_is_a_control_bar_segmented_picker_that_persists(
        monkeypatch):
    """The two skin buttons stay RAW ``ui.button``s with a written reason in the
    guard's ALLOWED — a segmented picker is mutually exclusive by construction
    and ``kit.button``'s four kinds have no selected state. They move out of the
    page's own rail into a ``kit.control_bar()``."""
    import inspect
    from nicegui import ui
    src = inspect.getsource(market.render)
    assert "kit.control_bar()" in src
    assert 'app_settings.set("macro_skin"' in src   # the literal line 265 pins
    els = _render_market(monkeypatch, _board_payload())
    buttons = [e for e in els if isinstance(e, ui.button)]
    assert len(buttons) == 2, f"expected the two skin buttons, got {buttons}"
    assert buttons[0].parent_slot is buttons[1].parent_slot, \
        "a segmented picker is one control, in one slot"
    assert set(buttons[0]._classes) != set(buttons[1]._classes), \
        "the selected half must be painted differently from the other"
    assert not any("tracking-[.18em]" in " ".join(b._classes) for b in buttons), \
        "the page's own tracked display face goes with the rest of it"


def test_the_cold_cache_line_is_the_apps_one_empty_state():
    import inspect
    src = inspect.getsource(market.render)
    assert "kit.empty(_copy.WAITING_MARKET)" in src


def test_the_category_accent_moves_onto_the_label_rule_it_still_has():
    """The clip-path-era left accent bar (``.mb-panel::before``) is chrome and
    goes; the 14 category hues are the frame's identity and survive on the
    hairline the label row already draws, so ``accent_of`` stays live rather
    than becoming a pure function nothing calls."""
    import inspect
    src = inspect.getsource(market.render)
    assert "accent_of(name)" in src
    assert "--mb-acc" not in src, "the accent bar's custom property is dead"


# ── the MACRO_CSS split: chrome out, data effects in ─────────────────────────
def test_macro_css_keeps_every_data_effect():
    from pages.options.theme import MACRO_CSS
    for rule in ("@keyframes mbig",            # the ignition bar
                 "@keyframes mbpx",            # the price flare
                 "@keyframes mblat",           # the Skin-B bloom
                 ".macro-board .mb-tile.fl .mb-ig{animation:mbig",
                 ".macro-board .mb-tile.fl .mb-px{animation:mbpx",
                 ".macro-board.macro-a .mb-tile{background-image:linear-gradient",
                 "background:var(--heat,",     # the Skin-B heat fill
                 ".macro-board.macro-b .mb-tile.fl{animation:mblat",
                 "@media (prefers-reduced-motion:reduce)"):
        assert rule in MACRO_CSS, f"{rule} is a data effect, not chrome"


def test_macro_css_drops_the_chrome_the_app_surface_now_carries():
    from pages.options.theme import MACRO_CSS
    for gone in ("radial-gradient",      # the page ground
                 "clip-path",            # the rail / panel / tile notches
                 "mb-rail",              # the rail's own gradient face
                 "::before",             # the left accent bar
                 "mb-shear",             # the sheared breadth bar
                 "mb-dot",               # the pulsing STREAMING dot
                 "@keyframes mbbp"):
        assert gone not in MACRO_CSS, f"{gone} is chrome the app surface carries"


def test_the_tile_is_still_the_ignition_bars_containing_block():
    """The SECOND trap, one rule over. ``.mb-tile``'s notch is chrome — but the
    same rule carries ``position:relative`` and ``overflow:hidden``, and
    ``.mb-ig`` is ``position:absolute``. Delete the whole rule as "the notch"
    and the ignition bar positions against the PAGE instead of the tile, and the
    price flare's glow is no longer clipped to it."""
    from pages.options.theme import MACRO_CSS
    assert ".macro-board .mb-tile{position:relative;overflow:hidden}" in MACRO_CSS
    assert ".macro-board .mb-ig{position:absolute" in MACRO_CSS


def test_skin_b_still_suppresses_the_ignition_bar_so_one_effect_fires():
    """Must-not-change. ``.macro-b .mb-ig{display:none}`` is not chrome: it is
    what routes a changed tile to the lattice bloom INSTEAD of the ignition bar.
    Without it both fire on Heat Lattice."""
    from pages.options.theme import MACRO_CSS
    assert ".macro-board.macro-b .mb-ig{display:none}" in MACRO_CSS


def test_the_lattice_panel_still_carries_no_ground_of_its_own():
    """Must-not-change. ``/macro`` is the PUBLIC screen and it is pinned to skin
    B (``live_screens.SCREENS``), so the lattice's continuous field of tiles is
    what the public site shows. A transparent panel is that skin's identity, not
    the notch chrome that used to sit around it."""
    from pages.options.theme import MACRO_CSS
    assert (".macro-board.macro-b .mb-panel{background:transparent !important;"
            "border-color:transparent !important}") in MACRO_CSS


def test_the_price_flare_ends_on_the_colour_the_price_actually_wears(monkeypatch):
    """Must-not-change, and the reason ``MB_TXT`` could not simply be dropped.
    ``@keyframes mbpx`` has no ``animation-fill-mode``, so at 100% the element
    reverts to its class colour — which means the keyframe's terminus and the
    price label's resting class must name the SAME colour, or the flare ends in
    a one-frame snap."""
    import re
    from pages.options.theme import MACRO_CSS
    end = re.search(r"@keyframes mbpx\{.*?100%\{color:(#[0-9A-Fa-f]{6})",
                    MACRO_CSS)
    assert end, "the price flare lost its terminus"
    els = _render_market(monkeypatch, _board_payload())
    price = next(e for e in els if "mb-px" in (getattr(e, "_classes", []) or []))
    assert f"text-[{end.group(1)}]" in price._classes, (
        f"the flare ends on {end.group(1)} but the price label wears "
        f"{[c for c in price._classes if c.startswith('text-[#')]}")
