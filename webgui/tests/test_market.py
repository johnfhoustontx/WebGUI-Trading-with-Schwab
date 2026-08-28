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
