"""The four DATA colours ``[calc]`` keeps, and the P&L matrix's chrome.

⚠ ``[calc]`` used to be a whole page-scoped SURFACE language — a near-black
ground, a mono face, five frame/tile/button skins and an ``ui.add_css``
escape-hatch block scoped ``.calc-v3``. Phase 2 Task 3 retired all of it with
the Calculator's migration onto ``pages/ui_kit.py``; what survives is the part
no app-wide token carries: the profit / loss / caution / signal hues the six
metric cards and the legs strip encode a READING in.

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
    "CALC_POS", "CALC_NEG", "CALC_ACCENT", "CALC_WARN", "CALC_STATE_TEXT",
    "CALC_EDGE_POS", "CALC_EDGE_NEG", "CALC_EDGE_ACCENT", "CALC_EDGE_WARN",
)

#: The four knobs the section is now allowed to hold. Anything else is surface.
CALC_DATA_KEYS = {"pos", "neg", "accent", "warn"}

#: What the retirement deleted. Named one by one rather than as a prefix sweep:
#: a prefix test would pass the day someone re-added one under a new name.
RETIRED = (
    "CALC_MONO", "CALC_PAGE", "CALC_FRAME", "CALC_FRAME_IDLE", "CALC_CHIP",
    "CALC_TILE", "CALC_INPUT", "CALC_BTN", "CALC_BTN_PRIMARY", "CALC_BTN_OFF",
    "CALC_STRATEGY_BTN", "CALC_EYEBROW", "CALC_VALUE", "CALC_SOFT", "CALC_BODY",
    "CALC_MUTED", "CALC_DIM", "CALC_CSS", "CALC_KEYFRAMES_CSS",
    "CALC_FONT_HEAD_HTML", "build_calc_css", "build_calc_font_head_html",
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


def test_the_calc_section_holds_only_data_colours_now():
    """The positive form: this FAILS if a surface knob comes back, where an
    ``assert "void" not in …`` per retired key could only ever go vacuous.
    A colour that encodes a VALUE stays; a colour that draws a FRAME does not,
    and a font URL never did belong to a page."""
    assert set(T._DEFAULTS["calc"]) == CALC_DATA_KEYS
    assert set(T.THEME["calc"]) == CALC_DATA_KEYS


def test_the_shipped_theme_toml_calc_section_holds_only_data_colours():
    """The tracked file and the defaults have to agree in BOTH directions:
    ``load_theme`` ignores a key that is not in ``_DEFAULTS``, so a surface knob
    left in the TOML would be an operator control with no consumer at all — and
    nothing else fails."""
    from repo_paths import THEME_TOML
    with open(THEME_TOML, "rb") as f:
        shipped = tomllib.load(f)["calc"]
    assert set(shipped) == CALC_DATA_KEYS
    assert "font_url" not in shipped, "the page loads no font of its own"


def test_the_page_scoped_calc_surface_vocabulary_is_gone():
    """``build_calc_css`` was the Calculator's own ``ui.add_css`` block, scoped
    ``.calc-v3``: boxed q-fields, the Strategy trigger internals, the leg-table
    track sizes and the teleported ``.strat-menu-calc`` popup. Every rule in it
    now exists app-wide under ``.ns-app`` (``build_quasar_css``), so the block
    and its tokens go rather than being injected twice."""
    for name in RETIRED:
        assert not hasattr(T, name), f"theme.{name} is a retired surface value"
    assert ".calc-v3" not in T.APP_FIELD_CSS
    assert ".strat-menu-calc" not in T.APP_FIELD_CSS
    assert ".ns-app .leg-trow" in T.APP_FIELD_CSS, \
        "the leg-table rules the calc block carried must exist app-wide"


def test_build_calc_tokens_returns_tailwind_class_strings():
    tk = T.build_calc_tokens(T._DEFAULTS)
    for key in CALC_TOKEN_KEYS:
        assert key in tk, f"missing token {key}"
        assert isinstance(tk[key], str) and tk[key].strip()
    assert set(tk) == set(CALC_TOKEN_KEYS), "token set drifted from the contract"


def test_calc_tokens_carry_the_configured_colours():
    tk = T.build_calc_tokens(_theme(pos="#001122"))
    assert "#001122" in tk["CALC_POS"]
    assert "#001122" in tk["CALC_EDGE_POS"]


def test_calc_tokens_hold_no_hardcoded_hex():
    """Every colour must come from the theme dict, or a knob is a dead knob —
    a half-working knob (border follows, fill does not) is worse than none."""
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
    for name in ("CALC_POS", "CALC_NEG", "CALC_ACCENT", "CALC_WARN"):
        assert tk[name] in parts, f"{name} missing from the removable state set"


def test_calc_state_text_follows_the_config():
    tk = T.build_calc_tokens(_theme(pos="#001122"))
    assert "text-[#001122]" in tk["CALC_STATE_TEXT"].split()


def test_load_theme_survives_a_malformed_calc_section(tmp_path):
    bad = tmp_path / "theme.toml"
    bad.write_text('[calc]\npos = 12345\nwarn = ""\n', encoding="utf-8")
    theme = T.load_theme(bad)
    assert theme["calc"]["pos"] == T._DEFAULTS["calc"]["pos"]
    assert theme["calc"]["warn"] == T._DEFAULTS["calc"]["warn"]


def test_module_exports_every_calc_token():
    """The export block is hand-maintained; drift from the builder is invisible
    without this."""
    tk = T.build_calc_tokens(T.THEME)
    for key, val in tk.items():
        assert hasattr(T, key), f"theme.{key} not exported"
        assert getattr(T, key) == val, f"theme.{key} is stale"


# ── the P&L matrix's chrome ─────────────────────────────────────────────────
# The matrix is ONE raw-HTML fragment (a few hundred cells as NiceGUI
# components would be a few hundred Vue elements), so it needs VALUES, not
# Tailwind classes — which is why these are plain CSS colours rather than
# tokens. The ramp itself is not here: a data-driven colour map is the one
# category config/theme.toml deliberately keeps out of the palette.
MATRIX_TOKEN_KEYS = ("MATRIX_HEAD_BG", "MATRIX_HEAD_RULE", "MATRIX_ROW_RULE",
                     "MATRIX_LABEL_FG", "MATRIX_VOID", "MATRIX_PRICE_FG",
                     "MATRIX_EMPTY_FG")


def test_build_matrix_tokens_returns_css_values_not_classes():
    tk = T.build_matrix_tokens(T._DEFAULTS)
    assert set(tk) == set(MATRIX_TOKEN_KEYS)
    for key, val in tk.items():
        assert isinstance(val, str) and val.strip(), key
        assert not val.startswith(("text-", "bg-", "border-")), \
            f"{key} is a Tailwind class; the matrix is raw HTML and needs a value"


def test_matrix_chrome_follows_the_app_palette():
    """It is FRAME, not data: the sticky header's ground and rule, the rules
    between price rows, a heading that is not the expiry, the price ladder and
    a cell with no reading. Near-black literals mirroring the retired ``[calc]``
    would now punch a hole in the app's navy card."""
    p = dict(T._DEFAULTS["palette"], card_bg="#001122", card_border="#003344",
             muted="#005566", title="#007788", page_bg3="#009900")
    tk = T.build_matrix_tokens(dict(T._DEFAULTS, palette=p))
    assert tk["MATRIX_HEAD_BG"] == "#001122"
    assert tk["MATRIX_HEAD_RULE"] == "#003344"
    assert tk["MATRIX_LABEL_FG"] == "#005566"
    assert tk["MATRIX_EMPTY_FG"] == "#005566"
    assert tk["MATRIX_PRICE_FG"] == "#007788"
    assert tk["MATRIX_VOID"] == "#009900"
    assert "#003344" in tk["MATRIX_ROW_RULE"]


def test_matrix_tokens_hold_no_hardcoded_hex():
    p = {k: "#123456" for k in T._DEFAULTS["palette"]}
    for key, val in T.build_matrix_tokens(dict(T._DEFAULTS, palette=p)).items():
        stray = [h for h in re.findall(r"#[0-9a-fA-F]{3,8}", val)
                 if not h.startswith("#123456")]
        assert not stray, f"{key}: hardcoded hex {stray}"


def test_module_exports_every_matrix_token():
    tk = T.build_matrix_tokens(T.THEME)
    for key, val in tk.items():
        assert hasattr(T, key), f"theme.{key} not exported"
        assert getattr(T, key) == val, f"theme.{key} is stale"
