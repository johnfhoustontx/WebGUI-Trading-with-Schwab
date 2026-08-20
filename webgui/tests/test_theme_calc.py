"""The page-scoped [calc] palette for the Options Strategy Calculator.

Same contract as every other section builder: a missing/malformed section
degrades to the built-in defaults and NEVER raises — styling must not be able
to break app startup."""
import re
import tomllib

from pages.options import theme as T

# Every token ``build_calc_tokens`` is contracted to produce. Listed in full,
# on purpose: this is the guard that a token cannot silently disappear when the
# builder is edited, which a ``len()`` check could not do.
CALC_TOKEN_KEYS = (
    "CALC_MONO", "CALC_PAGE", "CALC_FRAME", "CALC_FRAME_IDLE", "CALC_CHIP",
    "CALC_TILE", "CALC_INPUT", "CALC_BTN", "CALC_BTN_PRIMARY", "CALC_BTN_OFF",
    "CALC_STRATEGY_BTN", "CALC_EYEBROW", "CALC_VALUE", "CALC_SOFT", "CALC_BODY",
    "CALC_MUTED", "CALC_DIM", "CALC_POS", "CALC_NEG", "CALC_ACCENT", "CALC_WARN",
    "CALC_EDGE_POS", "CALC_EDGE_NEG", "CALC_EDGE_ACCENT", "CALC_EDGE_WARN",
    "CALC_STATE_TEXT",
)


def _theme(**calc_overrides):
    """A deep-enough copy of the defaults with [calc] knobs overridden."""
    theme = {s: dict(v) for s, v in T._DEFAULTS.items()}
    theme["calc"].update(calc_overrides)
    return theme


def _repainted():
    """The defaults with every [calc] COLOUR set to one value, so anything else
    left in a builder's output is a literal it baked in."""
    return _theme(**{k: "#123456" for k, v in T._DEFAULTS["calc"].items()
                     if v.startswith("#")})


def test_calc_defaults_exist_and_are_all_strings():
    calc = T._DEFAULTS["calc"]
    assert calc, "[calc] section missing from _DEFAULTS"
    assert all(isinstance(v, str) and v for v in calc.values()), \
        "load_theme only merges non-empty string values"


def test_build_calc_tokens_returns_tailwind_class_strings():
    tk = T.build_calc_tokens(T._DEFAULTS)
    for key in CALC_TOKEN_KEYS:
        assert key in tk, f"missing token {key}"
        assert isinstance(tk[key], str) and tk[key].strip()
    assert set(tk) == set(CALC_TOKEN_KEYS), "token set drifted from the contract"


def test_calc_tokens_carry_the_configured_colours():
    tk = T.build_calc_tokens(_theme(pos="#001122"))
    assert "#001122" in tk["CALC_POS"]


def test_calc_tokens_hold_no_hardcoded_hex():
    """Every colour must come from the theme dict, or a knob is a dead knob —
    a half-working knob (border follows, fill does not) is worse than none.
    Alpha-suffixed forms of the repaint (``_alpha_hex``) are the same colour."""
    for key, val in T.build_calc_tokens(_repainted()).items():
        stray = [h for h in re.findall(r"#[0-9a-fA-F]{3,8}", val)
                 if not h.startswith("#123456")]
        assert not stray, f"{key}: hardcoded hex {stray}"


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


def test_calc_tokens_never_reference_a_css_variable():
    """The bundled Tailwind JIT does not generate an arbitrary containing
    ``var(...)`` — it emits no rule at all, silently. Same failure class as the
    bare space above, and the likelier one to be introduced later."""
    for key, val in T.build_calc_tokens(T._DEFAULTS).items():
        assert "var(" not in val, f"{key}: var(...) never generates: {val!r}"


def test_calc_state_text_is_a_class_string_not_token_names():
    """``CALC_STATE_TEXT`` is passed to ``Element.classes(remove=...)``, which
    takes real classes — the sibling ``STATE_TEXT_CLASSES`` sets the shape."""
    tk = T.build_calc_tokens(T._DEFAULTS)
    parts = tk["CALC_STATE_TEXT"].split()
    assert parts, "CALC_STATE_TEXT is empty"
    assert not any(p.startswith("CALC_") for p in parts), \
        "holds token NAMES; Element.classes(remove=) needs the classes themselves"
    for name in ("CALC_POS", "CALC_NEG", "CALC_ACCENT", "CALC_WARN", "CALC_DIM"):
        assert tk[name] in parts, f"{name} missing from the removable state set"


def test_calc_state_text_follows_the_config():
    tk = T.build_calc_tokens(_theme(pos="#001122"))
    assert "text-[#001122]" in tk["CALC_STATE_TEXT"].split()


def test_build_calc_css_is_scoped_to_calc_v3_and_never_to_calc_v2():
    css = T.build_calc_css(T._DEFAULTS)
    assert ".calc-v3" in css
    assert ".calc-v2" not in css, "must not restyle the Simulator/Trade scope"
    # the teleported strategy popup is body-mounted, so it gets its own scope
    assert ".strat-menu-calc" in css


def test_calc_css_carries_the_configured_colours():
    css = T.build_calc_css(_theme(input_bg="#001122", icon="#003344",
                                  icon_soft="#005566"))
    for hexv in ("#001122", "#003344", "#005566"):
        assert hexv in css, f"{hexv} never reached the CSS"


def test_calc_css_holds_no_hardcoded_hex():
    """The sibling builders (build_quasar_css / build_macro_css) contain not one
    literal hex; a neutral black/white rgba wash is the accepted exception."""
    stray = sorted({h for h in re.findall(r"#[0-9a-fA-F]{3,8}",
                                          T.build_calc_css(_repainted()))
                    if not h.startswith("#123456")})
    assert not stray, f"hardcoded hex in build_calc_css: {stray}"


def test_calc_keyframes_declare_the_two_animations():
    assert "@keyframes blip" in T.CALC_KEYFRAMES_CSS
    assert "@keyframes scan" in T.CALC_KEYFRAMES_CSS


def test_calc_font_head_html_is_empty_when_unset():
    assert T.build_calc_font_head_html(_theme(font_url="")) == ""


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


def test_shipped_toml_calc_section_matches_the_defaults():
    """``load_theme`` ignores an unknown key BY DESIGN, so a typo in the shipped
    config/theme.toml silently does nothing and nothing fails. This is the test
    that fails instead."""
    from repo_paths import THEME_TOML
    with open(THEME_TOML, "rb") as f:
        shipped = tomllib.load(f)["calc"]
    unknown = sorted(set(shipped) - set(T._DEFAULTS["calc"]))
    assert not unknown, f"[calc] keys in theme.toml that _DEFAULTS ignores: {unknown}"
    missing = sorted(set(T._DEFAULTS["calc"]) - set(shipped))
    assert not missing, f"[calc] knobs missing from theme.toml: {missing}"


def test_module_exports_every_calc_token():
    """The export block is hand-maintained; drift from the builder is invisible
    without this."""
    tk = T.build_calc_tokens(T.THEME)
    for key, val in tk.items():
        assert hasattr(T, key), f"theme.{key} not exported"
        assert getattr(T, key) == val, f"theme.{key} is stale"


def test_module_exports_the_calc_css_and_font():
    for name in ("CALC_CSS", "CALC_FONT_HEAD_HTML", "CALC_KEYFRAMES_CSS"):
        assert hasattr(T, name), f"theme.{name} not exported"
