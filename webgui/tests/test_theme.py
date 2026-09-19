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
    # Tokens encode the palette they are built from. Test the MAPPING against the
    # built-in defaults (built fresh) so this can't break when config/theme.toml
    # is re-themed (the live theme.CARD follows the config, by design).
    card = theme.build_tokens(theme.load_theme("Z:/nope.toml"))["CARD"]
    assert "#101a30" in card and "#213152" in card


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
    # The state tokens are text-[<semantic hex>] classes. Test the MAPPING against
    # the built-in defaults (built fresh) — the live tokens follow config/theme.toml.
    toks = theme.build_tokens(theme.load_theme("Z:/nope.toml"))
    assert toks["TXT_POS"] == "text-[#66bb6a]"
    assert toks["TXT_WARN"] == "text-[#ffa726]"
    assert toks["TXT_NEG"] == "text-[#ef5350]"
    assert toks["TXT_NEUTRAL"] == "text-[#bdbdbd]"


BTN_TOKENS = ["BTN", "BTN_PRIMARY", "BTN_DANGER", "BTN_DANGER_SOLID",
              "BTN_3D", "BTN_3D_DANGER", "BTN_QUIET"]


def test_button_tokens_are_class_strings():
    for n in BTN_TOKENS:
        v = getattr(theme, n)
        assert isinstance(v, str) and v.strip() and "{" not in v and ";" not in v


def test_buttons_are_flat_deep_slate():
    # The Deep Slate redesign flattened the buttons: no 3D gradient/lip/press, just
    # solid/tinted fills. Primary = blue accent + dark text + a soft glow.
    toks = theme.build_tokens(theme.load_theme("Z:/nope.toml"))
    assert "linear-gradient" not in toks["BTN_3D"]      # flattened
    assert "active:translate-y" not in toks["BTN_3D"]
    assert toks["BTN_PRIMARY"] == toks["BTN_3D"]        # legacy alias → flat primary
    assert "text-[#0b1024]" in toks["BTN_PRIMARY"] and "shadow-[0_4px_14px" in toks["BTN_PRIMARY"]
    # Danger: ghost (tint + border + red text); the alias points at it.
    assert toks["BTN_DANGER"] == toks["BTN_3D_DANGER"]
    assert "border" in toks["BTN_DANGER"] and "text-[#ef5350]" in toks["BTN_DANGER"]
    # Solid danger (Terminate): full red fill from [palette].danger + glow.
    assert "#d33f3f" in toks["BTN_DANGER_SOLID"] and "text-white" in toks["BTN_DANGER_SOLID"]


# -- Theme config (config/theme.toml) — styling without code edits (2026-07-09) --


def test_load_theme_missing_file_returns_defaults():
    t = theme.load_theme("Z:/nope/does-not-exist.toml")
    assert t["palette"]["card_bg"] == "#101a30"
    assert t["semantic"]["positive"] == "#66bb6a"


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
        '[semantic]\npositive = "#00ff00"\n', encoding="utf-8")
    toks = theme.build_tokens(theme.load_theme(p))
    assert "bg-[#222831]" in toks["CARD"]
    assert "bg-[#00aa55]" in toks["BTN_PRIMARY"]
    assert "bg-[#00aa55]" in toks["BTN_3D"]
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


def test_badge_tokens_are_tinted_pills():
    # Deep Slate badges: translucent tint bg + matching colored fg, from the
    # default semantic palette; test the mapping against freshly-built defaults.
    toks = theme.build_tokens(theme.load_theme("Z:/nope.toml"))
    assert toks["BADGE_POS"] == "bg-[#66bb6a]/15 text-[#66bb6a] rounded-[6px]"
    assert toks["BADGE_WARN"] == "bg-[#ffa726]/15 text-[#ffa726] rounded-[6px]"
    assert toks["BADGE_NEG"] == "bg-[#ef5350]/15 text-[#ef5350] rounded-[6px]"
    assert toks["BADGE_ACCENT"] == "bg-[#a9b6ff]/15 text-[#a9b6ff] rounded-[6px]"
    assert toks["BADGE_MUTED"] == "bg-white/5 text-[#8891ab] rounded-[6px]"
    for name in ("BADGE_POS", "BADGE_WARN", "BADGE_NEG", "BADGE_ACCENT", "BADGE_MUTED"):
        assert isinstance(getattr(theme, name), str) and getattr(theme, name).strip()


def test_font_url_head_html():
    # Empty font_url → no head HTML (keeps app default, no extra request).
    assert theme.build_font_head_html(theme.load_theme("Z:/nope.toml")) == ""
    t = theme.load_theme("Z:/nope.toml")
    t["typography"]["font_url"] = "https://fonts.example/css2?family=IBM+Plex+Sans"
    html = theme.build_font_head_html(t)
    assert "preconnect" in html
    assert 'href="https://fonts.example/css2?family=IBM+Plex+Sans"' in html
    assert 'rel="stylesheet"' in html


def test_typography_numeric_tabular_nums(tmp_path):
    # numeric "" = default figures (no rule); "tabular" = tabular-nums app-wide.
    assert "tabular-nums" not in theme.build_typography_css(theme.load_theme("Z:/nope.toml"))
    p = tmp_path / "theme.toml"
    p.write_text('[typography]\nnumeric = "tabular"\n', encoding="utf-8")
    css = theme.build_typography_css(theme.load_theme(p)).replace(" ", "")
    assert "font-variant-numeric:tabular-nums" in css


def test_menu_defaults_emit_no_rules():
    # All [menu] knobs default "" = keep today's exact Quasar look — the nav CSS
    # must emit NOTHING so the defaults can never drift from the stock render.
    t = theme.load_theme("Z:/nope.toml")
    assert all(v == "" for v in t["menu"].values())
    assert theme.build_nav_css(t).strip() == ""


def test_menu_header_bg_emits_decoupled_header_rule(tmp_path):
    # [menu].header_bg styles the top bar independently of accent (the Deep Slate
    # dark-header decouple): a .q-header background rule, not the Quasar primary.
    p = tmp_path / "theme.toml"
    p.write_text('[menu]\nheader_bg = "#111731"\n', encoding="utf-8")
    css = theme.build_nav_css(theme.load_theme(p)).replace(" ", "")
    assert ".q-header{background:#111731" in css


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


# -- save_theme_values: the Appearance editor writes the OVERRIDE file ----------
# (config/local/theme.toml), never the tracked theme.toml, which would dirty the
# prod checkout and make tools/promote.sh refuse every later promote.


def _theme_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    p = tmp_path / "theme.toml"
    p.write_text('# shipped\n[palette]\ncard_bg = "#101a30"\ntext = "#cdd8ee"\n',
                 encoding="utf-8")
    return p


def test_save_theme_values_writes_the_override_not_the_tracked_file(tmp_path,
                                                                    monkeypatch):
    p = _theme_files(tmp_path, monkeypatch)
    before = p.read_text(encoding="utf-8")
    t = theme.save_theme_values({"palette": {"card_bg": "#222831",
                                             "text": "#cdd8ee"}}, path=p)
    assert p.read_text(encoding="utf-8") == before          # tracked file untouched
    over = (tmp_path / "local" / "theme.toml").read_text(encoding="utf-8")
    assert "#222831" in over
    assert "#cdd8ee" not in over        # a shipped value is not an override
    assert t["palette"]["card_bg"] == "#222831"


def test_reset_theme_drops_the_override(tmp_path, monkeypatch):
    p = _theme_files(tmp_path, monkeypatch)
    theme.save_theme_values({"palette": {"card_bg": "#222831"}}, path=p)
    t = theme.reset_theme(path=p)
    assert not (tmp_path / "local" / "theme.toml").exists()
    assert t["palette"]["card_bg"] == "#101a30"









def test_knob_label_humanizes_keys():
    assert theme.knob_label("card_bg") == "Card background"
    assert theme.knob_label("btn_hover") == "Button hover"
    assert theme.knob_label("blue_top") == "Blue top"
    assert theme.knob_label("page_bg1") == "Page background 1"
    assert theme.knob_label("drawer_bg") == "Drawer background"


# -- boxed inputs under .calc-v2 --------------------------------------------

def _decls(css, selector):
    """Declaration blocks of every rule whose selector LIST contains ``selector``."""
    import re
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)   # comments carry selectors' words
    out = []
    for chunk in css.split("}"):
        if "{" not in chunk:
            continue
        sel, _, body = chunk.partition("{")
        if selector in [s.strip() for s in sel.replace("\n", " ").split(",")]:
            out.append(body)
    return out


def test_the_generic_boxed_input_is_left_alone():
    """The generic boxed input keeps its height — the Simulator
    (symbol, look-back, the Trade page) keeps the 40px boxed input."""
    body = " ".join(_decls(theme.build_quasar_css(theme._DEFAULTS),
                           ".calc-v2 .q-field__control")).replace(" ", "")
    assert "min-height:40px" in body


def test_no_leg_card_rules_survive_the_card_layout():
    """The two-line card leg layout was removed 2026-09-12; its compaction
    rules would match nothing, in either scope."""
    assert ".leg-card" not in theme.build_quasar_css(theme._DEFAULTS)
    assert ".leg-card" not in theme.build_calc_css(theme._DEFAULTS)


# -- [buttons_3d] retired 2026-09-19: the solid danger fill is [palette].danger --


def test_danger_is_a_palette_colour(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text('[palette]\ndanger = "#123456"\n', encoding="utf-8")
    toks = theme.build_tokens(theme.load_theme(p))
    assert "bg-[#123456]" in toks["BTN_DANGER_SOLID"]


def test_a_saved_buttons_3d_red_mid_still_sets_danger(tmp_path):
    """An override written before the retirement may still carry red_mid -
    the one key of that section anything read. It keeps its colour."""
    p = tmp_path / "theme.toml"
    p.write_text('[buttons_3d]\nred_mid = "#654321"\n', encoding="utf-8")
    t = theme.load_theme(p)
    assert t["palette"]["danger"] == "#654321"
    assert "buttons_3d" not in t


def test_palette_danger_wins_over_the_retired_key(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text('[palette]\ndanger = "#111111"\n[buttons_3d]\nred_mid = "#222222"\n',
                 encoding="utf-8")
    assert theme.load_theme(p)["palette"]["danger"] == "#111111"


def test_quiet_button_is_text_only():
    toks = theme.build_tokens(theme.load_theme("Z:/nope.toml"))
    q = toks["BTN_QUIET"]
    assert "bg-transparent" in q and "text-[#7f8db0]" in q     # default muted
    assert "border" not in q


def test_quasar_css_scope_is_a_parameter():
    t = theme.load_theme("Z:/nope.toml")
    app = theme.build_quasar_css(t, scope=".ns-app")
    assert ".ns-app .q-field__control{" in app
    assert ".calc-v2" not in app
    assert ".calc-v2 .q-field__control{" in theme.build_quasar_css(t)   # default unchanged
    assert ".strat-menu-navy.q-menu{" in app       # the teleported popup stays global


def test_app_field_css_is_the_app_scope():
    assert theme.APP_FIELD_CSS == theme.build_quasar_css(theme.THEME, scope=".ns-app")
