"""The page-scoped [calc] palette for the Options Strategy Calculator.

Same contract as every other section builder: a missing/malformed section
degrades to the built-in defaults and NEVER raises — styling must not be able
to break app startup."""
from pages.options import theme as T


def test_calc_defaults_exist_and_are_all_strings():
    calc = T._DEFAULTS["calc"]
    assert calc, "[calc] section missing from _DEFAULTS"
    assert all(isinstance(v, str) and v for v in calc.values()), \
        "load_theme only merges non-empty string values"


def test_build_calc_tokens_returns_tailwind_class_strings():
    tk = T.build_calc_tokens(T._DEFAULTS)
    for key in ("CALC_PAGE", "CALC_FRAME", "CALC_FRAME_IDLE", "CALC_CHIP",
                "CALC_TILE", "CALC_INPUT", "CALC_BTN", "CALC_BTN_PRIMARY",
                "CALC_BTN_OFF", "CALC_EYEBROW", "CALC_VALUE", "CALC_MONO",
                "CALC_POS", "CALC_NEG", "CALC_ACCENT", "CALC_WARN", "CALC_DIM"):
        assert key in tk, f"missing token {key}"
        assert isinstance(tk[key], str) and tk[key].strip()


def test_calc_tokens_carry_the_configured_colours():
    theme = {s: dict(v) for s, v in T._DEFAULTS.items()}
    theme["calc"]["pos"] = "#001122"
    tk = T.build_calc_tokens(theme)
    assert "#001122" in tk["CALC_POS"]


def test_calc_tokens_never_contain_a_bare_space():
    # A Tailwind arbitrary value cannot contain a space — underscores are the
    # escape. A space inside [...] silently produces no rule at all. Scan each
    # token as ONE string (a whitespace-split chunk can never hold a space, so
    # splitting first would make this check vacuous).
    tk = T.build_calc_tokens(T._DEFAULTS)
    for key, val in tk.items():
        depth = 0
        for ch in val:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth = max(0, depth - 1)
            elif ch == " " and depth:
                raise AssertionError(f"{key}: space inside a [...] value: {val!r}")


def test_build_calc_css_is_scoped_to_calc_v3_and_never_to_calc_v2():
    css = T.build_calc_css(T._DEFAULTS)
    assert ".calc-v3" in css
    assert ".calc-v2" not in css, "must not restyle the Simulator/Trade scope"
    # the teleported strategy popup is body-mounted, so it gets its own scope
    assert ".strat-menu-calc" in css


def test_calc_keyframes_declare_the_two_animations():
    assert "@keyframes blip" in T.CALC_KEYFRAMES_CSS
    assert "@keyframes scan" in T.CALC_KEYFRAMES_CSS


def test_calc_font_head_html_is_empty_when_unset():
    theme = {s: dict(v) for s, v in T._DEFAULTS.items()}
    theme["calc"]["font_url"] = ""
    assert T.build_calc_font_head_html(theme) == ""


def test_calc_font_head_html_links_the_configured_face():
    html = T.build_calc_font_head_html(T._DEFAULTS)
    assert T._DEFAULTS["calc"]["font_url"] in html
    assert "fonts.gstatic.com" in html


def test_load_theme_survives_a_malformed_calc_section(tmp_path):
    bad = tmp_path / "theme.toml"
    bad.write_text('[calc]\npos = 12345\nvoid = ""\n', encoding="utf-8")
    theme = T.load_theme(bad)
    assert theme["calc"]["pos"] == T._DEFAULTS["calc"]["pos"]
    assert theme["calc"]["void"] == T._DEFAULTS["calc"]["void"]


def test_module_exports_the_calc_tokens():
    for name in ("CALC_PAGE", "CALC_FRAME", "CALC_TILE", "CALC_BTN_PRIMARY",
                 "CALC_CSS", "CALC_FONT_HEAD_HTML", "CALC_KEYFRAMES_CSS"):
        assert hasattr(T, name), f"theme.{name} not exported"
