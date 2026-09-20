"""Tests for the Market Regime Console theme layer (config/theme.toml [console]).

⚠ **[console] is DATA ONLY since 2026-09-19** (the app-consistency standard,
``docs/plans/2026-09-19-app-ui-consistency-design.md``). It used to carry a
whole page language — a near-black ground, a gradient card, a cell, one grey
``line`` serving every hairline and meter track at six opacities, two rules, a
condensed Rajdhani display face and a six-step neutral text ramp. All of it was
SURFACE, and the three screens that drew it (``/sentiment``, ``/desk``,
``/symbol``) wear the app's own card, borders and text steps now. The tests
those tokens had went with them; what replaces them is the retirement guard at
the foot of this file, which is what stops a source-scan elsewhere in the suite
becoming vacuous by deletion.

The class shapes still asserted here were each measured to JIT-generate in the
running app (Phase 0/2 spikes, recorded in
docs/plans/2026-08-14-sentiment-console-redesign-plan.md). That is the point of
pinning them: a class that does not generate produces NO rule, changes nothing
server-side, and so cannot be caught any other way than by having been measured
once and then held still.
"""
import re

from pages.options import theme


def _t(**console):
    base = {k: v for k, v in theme._DEFAULTS["console"].items()}
    base.update(console)
    merged = {sec: dict(vals) for sec, vals in theme._DEFAULTS.items()}
    merged["console"] = base
    return merged


# ------------------------------------------------------------------ defaults
def test_console_section_is_the_handoff_data_palette():
    """RE-AIMED 2026-09-19: it asserted ``page_bg``, which was the page GROUND
    and went with the rest of the surface. The accent and the dormant-regime
    hue are readings and stay."""
    c = theme.THEME["console"]
    assert c["accent"] == "#22e3d3"
    assert c["regime_breakout_zero"] == "#6a5c33"     # the dormant state


def test_console_is_not_in_the_settings_appearance_editor():
    """Same reason [brand] is excluded: that editor's sections are single-kind
    and this one mixes a semantic set with a regime lookup."""
    from pages import appearance
    sections = {s for _label, _kind, keys in appearance.GROUPS for s, _k in keys}
    assert "console" not in sections
    assert "brand" not in sections


# -------------------------------------------------------------------- tokens
def test_tokens_carry_no_spaces_inside_arbitrary_values():
    """A Tailwind arbitrary value cannot contain a space — underscores are the
    escape. A stray space silently splits the class in two and neither half
    generates."""
    for name, cls in theme.build_console_tokens(_t()).items():
        for arb in re.findall(r"\[[^\]]*\]", cls):
            assert " " not in arb, f"{name} has a space inside {arb}"


def test_no_var_in_any_token():
    """var() is the one arbitrary form the bundled JIT genuinely will not emit
    (measured) — which is why the nav pill is hand-written CSS."""
    for name, cls in theme.build_console_tokens(_t()).items():
        assert "var(" not in cls, name


def test_every_surviving_token_is_a_reading():
    """The whole point of the 2026-09 trim: a console token may name a COLOUR
    THAT MEANS SOMETHING and nothing else. A ground, a border, a font stack or
    a neutral text step coming back here is the regression."""
    tok = theme.build_console_tokens(_t())
    assert set(tok) == {"CON_ACCENT", "CON_POS", "CON_NEG", "CON_WARN"}
    for name, cls in tok.items():
        assert cls.startswith("text-["), f"{name} is not a text colour"


def test_alpha_hex_clamps_and_rounds():
    """``_alpha_hex`` outlived the gradient card it was written for: the meter
    fill, the chip tints and the dial's rings all still take an 8-digit hex,
    because the `/[…]` opacity modifier cannot reach a gradient stop or an SVG
    attribute."""
    assert theme._alpha_hex("#0e161e", 0.95) == "#0e161ef2"
    assert theme._alpha_hex("#000000", 0) == "#00000000"
    assert theme._alpha_hex("#000000", 1) == "#000000ff"
    assert theme._alpha_hex("#000000", 5) == "#000000ff"       # clamped
    assert theme._alpha_hex("#000000", -1) == "#00000000"


def test_tokens_follow_a_configured_palette():
    """RE-AIMED 2026-09-19: the second half read ``CONSOLE_PAGE``, which is
    gone. A configured DATA colour must still reach its token."""
    tok = theme.build_console_tokens(_t(accent="#ff0000", positive="#00ff00"))
    assert tok["CON_ACCENT"] == "text-[#ff0000]"
    assert tok["CON_POS"] == "text-[#00ff00]"


# --------------------------------------------------------------------- glow
def test_glow_is_shadow_only_and_spaceless():
    g = theme.console_glow("#35d68a", px=16, alpha=0.45)
    assert g == "shadow-[0_0_16px_rgba(53,214,138,0.45)]"
    assert " " not in g
    assert theme.console_glow("#35d68a", px=18, spread="-6px").startswith(
        "shadow-[0_0_18px_-6px_")


def test_glow_survives_a_malformed_colour():
    assert theme.console_glow("nonsense").startswith("shadow-[")


# --------------------------------------------------------------------- misc
def test_console_colors_expose_all_five_regimes_for_the_svg_builders():
    cols = theme.console_colors(_t())
    assert set(cols["regimes"]) == {"mean_reversion", "trending", "breakout",
                                    "choppy", "crisis"}
    assert cols["regimes"]["mean_reversion"] == "#6f86ff"    # console blue,
    assert cols["regimes"]["choppy"] == "#c3ccd6"            # not the [charts] set
    assert cols["regime_zero"] == "#6a5c33"


def test_console_colors_expose_no_neutral_at_all():
    """The other half of the trim. ``line`` / ``cell`` / ``text`` / ``muted`` /
    ``label`` / ``dim`` used to come out of here and were read as raw hexes by
    the dial, the wall bar and the Desk's chips — a groove, a caption and a
    chip label, all surface. Every one of them takes ``THEME["palette"]`` now,
    and nothing may reintroduce a neutral through this door."""
    cols = theme.console_colors(_t())
    assert set(cols) == {"accent", "positive", "negative", "warning", "olive",
                         "yellow", "regimes", "regime_zero"}


def test_keyframes_css_is_the_only_css_and_carries_the_pulse():
    """The console's single escape-hatch rule. If this grows beyond an animation,
    the Tailwind-first rule is being eroded — push it back into tokens.

    It outlived the vocabulary it shipped with: ``/desk`` wears ``con-pulse``
    on its feed-freshness dot and still injects this block."""
    css = theme.CONSOLE_KEYFRAMES_CSS
    assert "@keyframes pulseDot" in css and ".con-pulse" in css
    assert css.count("@keyframes") == 1
    assert "color:" not in css and "background" not in css


def test_module_level_console_exports_are_populated():
    for name in ("CON_ACCENT", "CON_POS", "CON_NEG", "CON_WARN"):
        assert getattr(theme, name), name
    assert set(theme.CONSOLE_COLORS["regimes"]) >= {"choppy", "crisis"}


# ------------------------------------------------- the retirement guard ------
# ⚠ WITHOUT THIS THE SOURCE SCANS ELSEWHERE IN THE SUITE ARE VACUOUS.
# ``test_desk.py``, ``test_symbol_page.py``, ``test_sentiment.py``,
# ``test_sentiment_sectors.py`` and ``test_market.py`` each assert that a
# retired token name does NOT appear in a page's source. Once the name is gone
# from ``theme`` entirely, those assertions can no longer fail whatever the
# page does — they became documentation the moment the constant was deleted.
# What still CAN fail is this: the name must not come back.
RETIRED = (
    # the console's ground, card, cell, grid rule, track and two rules
    "CONSOLE_PAGE", "CONSOLE_CARD", "CONSOLE_CELL", "CONSOLE_HAIRLINE",
    "CONSOLE_TRACK", "CONSOLE_RULE", "CONSOLE_DIVIDER",
    # its condensed display face and the <link> that fetched it
    "CONSOLE_DISPLAY", "CONSOLE_FONT_HEAD_HTML", "build_console_font_head_html",
    # its six-step neutral text ladder
    "CON_TXT", "CON_TXT_SECONDARY", "CON_TXT_MUTED", "CON_TXT_LABEL",
    "CON_TXT_DIM", "CON_TXT_FAINT",
    # the Macro Board's own ground, ramp, borders and three Google faces
    "MACRO_FONT_HEAD_HTML", "build_macro_font_head_html",
    # the Sector & Industry grid's ground, ramp, borders and two faces
    "SECTOR_FONT_HEAD_HTML", "build_sector_font_head_html",
)

RETIRED_MACRO_TOKENS = ("MB_TITLE", "MB_SYM", "MB_MONO", "MB_TXT", "MB_DIM",
                        "MB_FAINT", "MB_CYAN", "MB_PANEL_BG", "MB_TILE_BG",
                        "MB_RAIL_BG", "MB_EDGE", "MB_EDGE_HI", "MB_TRACK_BG")

RETIRED_SECTOR_TOKENS = ("SC_SANS", "SC_MONO", "SC_VOID_BG", "SC_TXT",
                         "SC_DIM", "SC_FAINT", "SC_DIM_BG", "SC_EDGE",
                         "SC_EDGE_HI")


def test_the_retired_page_vocabularies_are_gone_from_the_theme():
    for name in RETIRED:
        assert not hasattr(theme, name), \
            f"{name} was retired in 2026-09; the app's own token replaces it"


def test_no_page_scoped_surface_token_survives_in_macro_or_sectors():
    """The Macro Board and the heat grid kept their DATA maps and lost their
    surface, exactly as the console did."""
    assert set(theme.MACRO_TOKENS) == {"MB_UP", "MB_DN", "MB_FLAT"}
    assert set(theme.SECTOR_TOKENS) == {"SC_UP", "SC_DN", "SC_WARN",
                                        "SC_UP_BG", "SC_DN_BG", "SC_WARN_BG"}
    for name in RETIRED_MACRO_TOKENS:
        assert name not in theme.MACRO_TOKENS, name
    for name in RETIRED_SECTOR_TOKENS:
        assert name not in theme.SECTOR_TOKENS, name


def test_the_three_page_scoped_sections_hold_only_data_keys():
    """``config/theme.toml`` is what an OPERATOR edits, so the retirement has
    to reach it too: a surface key left in the file is an invitation to tune a
    colour nothing reads any more.

    ``[macro]`` keeps two keys that are references rather than paint —
    ``tile`` is the fallback behind the Skin-B heat custom property, and
    ``sat_ceiling`` is the tuning number the heat wash scales on."""
    import pathlib
    import tomllib
    from repo_paths import THEME_TOML
    raw = tomllib.loads(pathlib.Path(THEME_TOML).read_text(encoding="utf-8"))
    assert set(raw["console"]) == {
        "accent", "positive", "negative", "warning", "olive", "yellow",
        "regime_mean_reversion", "regime_trending", "regime_breakout",
        "regime_breakout_zero", "regime_choppy", "regime_crisis"}
    assert set(raw["macro"]) == {"tile", "up", "dn", "flat", "cyan",
                                 "sat_ceiling"}
    assert set(raw["sectors"]) == {"up", "dn", "warn"}
    assert "rotation" not in raw, "[rotation] retired in Task 5"
