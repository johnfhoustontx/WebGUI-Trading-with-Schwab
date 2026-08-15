"""Tests for the Market Regime Console theme layer (config/theme.toml [console]).

The class shapes asserted here were each measured to JIT-generate in the running
app (Phase 0/2 spikes, recorded in
docs/plans/2026-08-14-sentiment-console-redesign-plan.md). That is the point of
pinning them: a class that does not generate produces NO rule, changes nothing
server-side, and so cannot be caught any other way than by having been measured
once and then held still.
"""
import re

import pytest

from pages.options import theme


def _t(**console):
    base = {k: v for k, v in theme._DEFAULTS["console"].items()}
    base.update(console)
    merged = {sec: dict(vals) for sec, vals in theme._DEFAULTS.items()}
    merged["console"] = base
    return merged


# ------------------------------------------------------------------ defaults
def test_console_section_exists_with_the_handoff_palette():
    c = theme.THEME["console"]
    assert c["page_bg"] == "#05070b"
    assert c["accent"] == "#22e3d3"
    assert c["regime_breakout_zero"] == "#6a5c33"     # the dormant state


def test_console_is_not_in_the_settings_appearance_editor():
    """Same reason [brand] is excluded: that editor's sections are single-kind
    and this one mixes colours with font text."""
    from pages import settings
    assert "console" not in {sec for sec, _label, _kind in settings._THEME_SECTIONS}
    assert "brand" not in {sec for sec, _label, _kind in settings._THEME_SECTIONS}


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


def test_card_uses_an_eight_digit_hex_for_its_gradient_alpha():
    """The `/[…]` opacity modifier cannot reach a gradient STOP, so the card's
    95% comes from an 8-digit hex instead."""
    card = theme.build_console_tokens(_t())["CONSOLE_CARD"]
    assert "linear-gradient(160deg,#0e161ef2,#070a0ff2)" in card
    assert theme._alpha_hex("#0e161e", 0.95) == "#0e161ef2"


def test_alpha_hex_clamps_and_rounds():
    assert theme._alpha_hex("#000000", 0) == "#00000000"
    assert theme._alpha_hex("#000000", 1) == "#000000ff"
    assert theme._alpha_hex("#000000", 5) == "#000000ff"       # clamped
    assert theme._alpha_hex("#000000", -1) == "#00000000"


def test_hairline_and_track_use_the_specs_exact_alphas():
    """One base colour at several opacities, and the SPEC's values (0.18 / 0.09)
    rather than the nearest step on Tailwind's core scale — arbitrary opacity
    was measured to generate."""
    tok = theme.build_console_tokens(_t())
    assert tok["CONSOLE_HAIRLINE"] == "bg-[#788ca0]/[0.18]"
    assert "bg-[#788ca0]/[0.09]" in tok["CONSOLE_TRACK"]
    assert "border-[#788ca0]/[0.14]" in tok["CONSOLE_TRACK"]


def test_display_font_stack_falls_back_to_the_app_font():
    """The fallback is 21% wider than Rajdhani (measured), so it must be a real
    stack rather than a bare family — and a blank family must not emit `''`."""
    tok = theme.build_console_tokens(_t())
    assert tok["CONSOLE_DISPLAY"] == (
        "font-['Rajdhani',_'IBM_Plex_Sans',_system-ui,_sans-serif]")
    blank = theme.build_console_tokens(_t(font_family=""))["CONSOLE_DISPLAY"]
    assert blank == "font-['IBM_Plex_Sans',_system-ui,_sans-serif]"
    assert "''" not in blank


def test_tokens_follow_a_configured_palette():
    tok = theme.build_console_tokens(_t(accent="#ff0000", page_bg="#123456"))
    assert tok["CON_ACCENT"] == "text-[#ff0000]"
    assert "bg-[#123456]" in tok["CONSOLE_PAGE"]


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


def test_font_head_html_is_a_link_or_empty():
    assert 'href="https://fonts.googleapis.com/css2?family=Rajdhani' in \
        theme.build_console_font_head_html(_t())
    assert theme.build_console_font_head_html(_t(font_url="")) == ""


@pytest.mark.parametrize("bad", [{}, {"console": {}}, {"console": "x"}])
def test_font_head_html_never_raises(bad):
    assert theme.build_console_font_head_html(bad) == ""


def test_keyframes_css_is_the_only_css_and_carries_the_pulse():
    """The console's single escape-hatch rule. If this grows beyond an animation,
    the Tailwind-first rule is being eroded — push it back into tokens."""
    css = theme.CONSOLE_KEYFRAMES_CSS
    assert "@keyframes pulseDot" in css and ".con-pulse" in css
    assert css.count("@keyframes") == 1
    assert "color:" not in css and "background" not in css


def test_module_level_console_exports_are_populated():
    for name in ("CONSOLE_PAGE", "CONSOLE_CARD", "CONSOLE_CELL", "CONSOLE_TRACK",
                 "CONSOLE_DISPLAY", "CON_ACCENT", "CON_POS", "CON_NEG"):
        assert getattr(theme, name), name
    assert set(theme.CONSOLE_COLORS["regimes"]) >= {"choppy", "crisis"}
