"""Guard tests for the Tailwind design-token vocabulary (Phase 0)."""
from pages.options import theme

TOKENS = ["PAGE", "CARD", "EYEBROW", "LABEL", "MUTED", "BTN", "BTN_PRIMARY", "STRATEGY_BTN"]


def test_tokens_exist_and_are_nonempty_strings():
    for name in TOKENS:
        val = getattr(theme, name)
        assert isinstance(val, str) and val.strip(), f"{name} missing/empty"


def test_tokens_are_class_strings_not_css():
    # A token is a Tailwind utility string applied via .classes() — it must not
    # contain CSS rule syntax (the whole point of the migration).
    for name in TOKENS:
        val = getattr(theme, name)
        assert "{" not in val and ";" not in val, \
            f"{name} looks like CSS, not a class string"


def test_card_token_encodes_navy_palette():
    # Convert + light polish: tokens encode the canonical hex palette.
    assert "#101a30" in theme.CARD and "#213152" in theme.CARD


def test_dashboard_css_removed():
    # Phase 4 deleted DASHBOARD_CSS after its last consumer (the Trade page) flipped
    # to tokens. The Quasar-internal rules now live ONLY in QUASAR_INTERNAL_CSS.
    assert not hasattr(theme, "DASHBOARD_CSS")
    assert ".q-field__control" in theme.QUASAR_INTERNAL_CSS
    assert ".strat-menu-navy" in theme.QUASAR_INTERNAL_CSS


def test_quasar_internal_css_is_internal_only():
    css = theme.QUASAR_INTERNAL_CSS
    # MUST contain the Quasar-internal rules component classes can't reach.
    assert ".q-field__control" in css
    assert ".strat-menu-navy" in css
    # MUST NOT contain the now-tokenized semantic rules.
    assert ".calc-card{" not in css.replace(" ", "")
    assert ".cv2-btn" not in css
    assert ".calc-eyebrow" not in css


STATE_TOKENS = ["TXT_POS", "TXT_WARN", "TXT_NEG", "TXT_NEUTRAL"]


def test_state_color_tokens_exist_and_are_text_classes():
    for name in STATE_TOKENS:
        val = getattr(theme, name)
        assert isinstance(val, str) and val.startswith("text-["), f"{name} not a text-[] class"
        assert "{" not in val and ";" not in val


def test_state_color_tokens_preserve_exact_hex():
    # convert + light polish: exact colors preserved as arbitrary values.
    assert theme.TXT_POS == "text-[#66bb6a]"
    assert theme.TXT_WARN == "text-[#ffa726]"
    assert theme.TXT_NEG == "text-[#ef5350]"
    assert theme.TXT_NEUTRAL == "text-[#bdbdbd]"


BTN3D = ["BTN_3D", "BTN_3D_DANGER"]


def test_btn3d_tokens_are_class_strings():
    for n in BTN3D:
        v = getattr(theme, n)
        assert isinstance(v, str) and v.strip() and "{" not in v and ";" not in v


def test_btn3d_encodes_gradient_and_shadow():
    assert "linear-gradient(180deg" in theme.BTN_3D
    assert theme.BTN_3D.count("shadow-[") >= 1 and "active:" in theme.BTN_3D
    assert "#d33f3f" in theme.BTN_3D_DANGER  # red variant mid-stop


# -- Theme config (config/theme.toml) — styling without code edits (2026-07-09) --


def test_load_theme_missing_file_returns_defaults():
    t = theme.load_theme("Z:/nope/does-not-exist.toml")
    assert t["palette"]["card_bg"] == "#101a30"
    assert t["semantic"]["positive"] == "#66bb6a"
    assert t["gauge"]["needle"] == "#f5f5f5"


def test_load_theme_merges_partial_override(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text('[palette]\ncard_bg = "#222831"\n', encoding="utf-8")
    t = theme.load_theme(p)
    assert t["palette"]["card_bg"] == "#222831"        # overridden
    assert t["palette"]["card_border"] == "#213152"    # untouched default
    assert t["semantic"]["negative"] == "#ef5350"      # other sections intact


def test_load_theme_ignores_unknown_keys_and_bad_values(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text('[palette]\nbogus = "#111111"\ncard_bg = 42\n[nonsense]\nx = "y"\n',
                 encoding="utf-8")
    t = theme.load_theme(p)
    assert "bogus" not in t["palette"]
    assert t["palette"]["card_bg"] == "#101a30"        # non-string value → default
    assert "nonsense" not in t


def test_build_tokens_reflect_theme_values(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text(
        '[palette]\ncard_bg = "#222831"\nprimary = "#00aa55"\n'
        '[buttons_3d]\nblue_top = "#123456"\n'
        '[semantic]\npositive = "#00ff00"\n', encoding="utf-8")
    toks = theme.build_tokens(theme.load_theme(p))
    assert "bg-[#222831]" in toks["CARD"]
    assert "bg-[#00aa55]" in toks["BTN_PRIMARY"]
    assert "#123456" in toks["BTN_3D"]
    assert "text-[#00ff00]" in toks["TXT_POS"]
    # the module-level tokens are build_tokens(THEME) — same generator.
    assert theme.CARD == theme.build_tokens(theme.THEME)["CARD"]


def test_quasar_internal_css_reflects_theme(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text('[palette]\ninput_bg = "#31363f"\n', encoding="utf-8")
    css = theme.build_quasar_css(theme.load_theme(p))
    assert "#31363f" in css
    assert ".q-field__control" in css and ".strat-menu-navy" in css


# -- [typography] + [menu] sections (fonts/sizes + app-menu styling, 2026-07-09) --


def test_typography_defaults_match_quasar():
    t = theme.load_theme("Z:/nope.toml")
    ty = t["typography"]
    assert ty["family"] == ""            # "" = keep the app default (Roboto)
    assert ty["titles"] == "20px"        # .text-h6 (20px = the framework 1.25rem)
    assert ty["body"] == "14px"
    css = theme.build_typography_css(t)
    assert ".text-h6{font-size:20px" in css.replace(" ", "")
    assert "font-family" not in css      # empty family emits no font rule


def test_typography_bare_number_means_pixels(tmp_path):
    # "just type a bigger number" works: a unitless size is treated as px.
    assert theme.normalize_size("15") == "15px"
    assert theme.normalize_size("1.1rem") == "1.1rem"   # explicit units untouched
    assert theme.normalize_size("") == ""
    p = tmp_path / "theme.toml"
    p.write_text('[typography]\ntitles = "22"\nsmall = "11"\n', encoding="utf-8")
    t = theme.load_theme(p)
    css = theme.build_typography_css(t).replace(" ", "")
    assert ".text-h6{font-size:22px" in css
    assert "text-[11px]" in theme.build_tokens(t)["EYEBROW"]


def test_typography_css_reflects_overrides(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text(
        '[typography]\nfamily = "Georgia, serif"\ntitles = "22px"\nbody = "15px"\n'
        'small = "11px"\n', encoding="utf-8")
    css = theme.build_typography_css(theme.load_theme(p))
    flat = css.replace(" ", "")
    assert "font-family:Georgia,serif" in flat
    assert ".text-h6{font-size:22px" in flat
    assert "body{" in flat and "font-size:15px" in flat
    assert ".text-xs{font-size:11px" in flat
    # EYEBROW size follows [typography].small
    toks = theme.build_tokens(theme.load_theme(p))
    assert "text-[11px]" in toks["EYEBROW"]


def test_menu_defaults_emit_no_rules():
    # All [menu] knobs default "" = keep today's exact Quasar look — the nav CSS
    # must emit NOTHING so the defaults can never drift from the stock render.
    t = theme.load_theme("Z:/nope.toml")
    assert all(v == "" for v in t["menu"].values())
    assert theme.build_nav_css(t).strip() == ""


def test_menu_css_reflects_overrides(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text(
        '[menu]\ndrawer_bg = "#0d1526"\ntext = "#9fb4d8"\nhover_bg = "#1a2745"\n'
        'title = "#ffcc00"\n', encoding="utf-8")
    css = theme.build_nav_css(theme.load_theme(p))
    flat = css.replace(" ", "")
    assert ".nav-drawer{background:#0d1526!important" in flat
    assert "#9fb4d8" in flat and "#1a2745" in flat and "#ffcc00" in flat


def test_menu_accent_is_a_plain_value_not_css():
    # accent recolors Quasar primary (header bar + active pill) via ui.colors in
    # _layout — it is NOT part of the nav CSS block.
    t = theme.load_theme("Z:/nope.toml")
    assert t["menu"]["accent"] == ""


# -- set_theme_values: comment-preserving TOML editor (Settings page, 2026-07-09) --

_TOML = """# header comment stays
[palette]                      # section comment stays
card_bg      = "#101a30"       # cards / frames
text         = "#cdd8ee"       # base body text

[menu]
accent    = ""                 # header bar
title     = ""                 # caption
"""


def test_set_theme_values_updates_value_preserving_comments():
    out = theme.set_theme_values(_TOML, {"palette": {"card_bg": "#222831"}})
    assert 'card_bg      = "#222831"       # cards / frames' in out
    assert "# header comment stays" in out
    assert 'text         = "#cdd8ee"' in out                # untouched


def test_set_theme_values_is_section_scoped():
    # a key name that exists in two sections only updates the targeted section
    out = theme.set_theme_values(_TOML, {"menu": {"title": "#ffcc00"}})
    assert 'title     = "#ffcc00"' in out
    assert 'card_bg      = "#101a30"' in out


def test_set_theme_values_missing_key_is_noop_and_multi_update():
    out = theme.set_theme_values(_TOML, {
        "palette": {"nope": "#000000", "text": "#ffffff"},
        "menu": {"accent": "#2e7d32"}})
    assert "#000000" not in out
    assert 'text         = "#ffffff"' in out
    assert 'accent    = "#2e7d32"' in out


def test_knob_label_humanizes_keys():
    assert theme.knob_label("card_bg") == "Card background"
    assert theme.knob_label("btn_hover") == "Button hover"
    assert theme.knob_label("blue_top") == "Blue top"
    assert theme.knob_label("page_bg1") == "Page background 1"
    assert theme.knob_label("drawer_bg") == "Drawer background"
