# Options Strategy Calculator Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild `/options/calculator` to the supplied design — a three-step numbered layout (① STRATEGY · ② SYMBOL · ③ LEGS) in a 424 px input column beside a results column of six metric cards over the P&L matrix — in a new page-scoped `[calc]` palette, with the shared leg editor moved to a card layout on both the Calculator and the Simulator.

**Architecture:** The page stays a **Tier-1 reader**. `options_svc` is **not touched**: `calc_load` / `calc_compute` / `calc_iv` and their three cache views keep their current contracts. Every readout the design adds — per-leg delta, the net/max-loss strip, the status pill, the matrix `%` — is derived page-side from the payloads already cached. The palette becomes a config-driven page-scoped language mirroring `[console]` / `[macro]` / `[sectors]` / `[rotation]`.

**Tech Stack:** NiceGUI (Tailwind-first, `.classes()` only — `.style()` is banned and guarded by `test_no_inline_style.py`), Quasar internals via the one documented `ui.add_css` escape hatch, pytest.

**Design doc:** [`docs/plans/2026-08-19-calculator-redesign-design.md`](2026-08-19-calculator-redesign-design.md)

---

## Before you start

**Python is at the repo root, not in this worktree:**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest --version
```

**Run the webgui suite from inside `webgui/`, in a subshell:**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q)
```

**Baseline before touching anything** — record the number and the failing set, because you compare the *set*, never the count:

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q -rf)
```

Expected: **1986 passed** (2026-08-19 baseline), no failures.

**Read first:** `webgui/pages/options/theme.py` (the `[sectors]` / `[rotation]` builders are the pattern you copy), `webgui/pages/options/leg_editor.py`, `webgui/pages/options/calculator.py`.

### ⚠ The `UNLIMITED` sentinel — read before Task 3

`options-scanner/options_calculator.py:24` defines **`UNLIMITED = 999999`**, a magic
placeholder — *not* infinity. `calc_summary` returns it verbatim:

| structure | field | value |
|---|---|---|
| `LONG_CALL` | `max_profit` | `999999` |
| `NAKED_CALL` | `max_loss` | `999999` |

So a naive `pnl / max_profit` on a long call yields ~0.0% for **every** cell, painting
`+0.0%` down the whole matrix — a confident fake measurement of exactly the kind this
redesign exists to avoid — and the MAX RETURN tile would read `$999,999`.

**Every task that touches `max_profit` / `max_loss` must treat `999999` as "unlimited"**
and render it as such, never as a number and never as a percentage denominator.

⚠ The webgui is Tier-1 and imports **only** `nicegui` + `shared.bus` + `shared.contracts`
— it may **not** import `options_calculator`. So declare the sentinel locally in
`calculator.py` with a comment naming its source, the same way the repo already mirrors
`REGIME_DISPLAY` across four tiers:

```python
# Mirror of the scanner's options-calculator UNLIMITED (999999) — a magic
# placeholder the service returns verbatim for an uncapped max_profit
# (LONG_CALL) or max_loss (NAKED_CALL). Tier-1 may not import that module, so
# the value is restated here; keep the two in step.
UNLIMITED = 999999


def is_unlimited(v):
    """Whether a summary figure is the service's uncapped sentinel."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == UNLIMITED
```

⚠ **Write that comment WITHOUT the bare module token.** `webgui/tests/test_options_calculator.py::test_calculator_holds_no_engine_imports` is a substring guard on the Tier-1 no-engine-imports rule, and it trips on the literal name even inside a comment. The fix is to reword the comment — **never** to relax the guard.

**The reference design** is unpacked at `C:/Users/john_/AppData/Local/Temp/claude/D--WebGUI-Trading-with-Schwab--claude-worktrees-inspiring-sinoussi-b1cc78/cad3bc3f-8cf0-4798-aa5a-75471fb99d96/scratchpad/design_body.html` — the markup and its logic. Consult it for exact geometry and colours; **do not port its Black-Scholes or its mock `MKT`/`EXPIRIES` data.**

---

## Task 1: The `[calc]` theme section

**Files:**
- Modify: `webgui/pages/options/theme.py` (`_DEFAULTS` ~line 244; new builders after `build_rotation_tokens` ~line 1015; exports ~line 1100)
- Modify: `config/theme.toml`
- Test: `webgui/tests/test_theme_calc.py` (create)

**Step 1: Write the failing test**

```python
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
    # escape. A space inside [...] silently produces no rule at all.
    tk = T.build_calc_tokens(T._DEFAULTS)
    for key, val in tk.items():
        for chunk in val.split():
            if "[" in chunk:
                assert " " not in chunk[chunk.index("["):], f"{key}: {chunk}"


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
```

**Step 2: Run it and watch it fail**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_theme_calc.py -q)
```

Expected: `KeyError: 'calc'` / `AttributeError: module 'pages.options.theme' has no attribute 'build_calc_tokens'`.

**Step 3: Add the defaults**

In `theme.py` `_DEFAULTS`, after the `"rotation"` block:

```python
    # The Options Strategy Calculator's own language: a near-black ground with
    # cyan/green/amber signal colours and a mono face, deliberately unlike the
    # app-wide dark navy. Page-scoped (.calc-v3) — NOT surfaced in
    # Settings → Appearance, and not the app palette.
    "calc": {
        "void": "#05070a", "glow": "#0b1a24",
        "frame_a": "#0b1118", "frame_b": "#06090d",
        "edge": "#26505c", "edge_idle": "#1d2937", "chip_bg": "#06080b",
        "tile_a": "#0d141c", "tile_b": "#070b0f", "tile_edge": "#1d2937",
        "input_bg": "#0a1219", "input_edge": "#2a3846",
        "bright": "#eaf2f9", "txt": "#cfdae8", "soft": "#dce7f3",
        "label": "#7189a0", "muted": "#8aa0b4", "body": "#93a8bb", "dim": "#6f8598",
        "pos": "#2dd4a7", "neg": "#fb5f7c", "accent": "#22d3ee", "warn": "#f5b841",
        "btn_bg": "#111b25", "btn_edge": "#3a5060", "btn_txt": "#d3e0ec",
        "off_bg": "#101720", "off_edge": "#26313d", "off_txt": "#5c6d7e",
        "font_url": ("https://fonts.googleapis.com/css2"
                     "?family=JetBrains+Mono:wght@400;500;700&display=swap"),
    },
```

**Step 4: Add the builders**

After `build_rotation_font_head_html`:

```python
def build_calc_tokens(theme):
    """Tailwind class-string vocabulary for the Strategy Calculator.

    Namespaced ``CALC_*`` so a calculator token can never be mistaken for one
    of the app-wide dark-navy tokens — this page keeps its own near-black
    ground. ⚠ Every ``[...]`` arbitrary value uses ``_`` for spaces: a Tailwind
    arbitrary value containing a real space generates NO rule at all, silently.
    """
    c = theme["calc"]
    frame_bg = f"bg-[linear-gradient(180deg,{c['frame_a']},{c['frame_b']})]"
    return {
        "CALC_MONO": "font-['JetBrains_Mono',ui-monospace,monospace]",
        "CALC_PAGE": (
            f"text-[{c['txt']}] tracking-[.02em] p-4 rounded-[3px] "
            f"bg-[radial-gradient(1100px_560px_at_14%_-12%,"
            f"{c['glow']}_0%,{c['void']}_62%)]"
        ),
        # The numbered frames. The label chip is positioned by the page
        # (relative frame + absolute -top-1.5 chip) — CALC_CHIP is the chip skin.
        "CALC_FRAME": f"relative rounded-[3px] border border-[{c['edge']}] {frame_bg}",
        "CALC_FRAME_IDLE": f"relative rounded-[3px] border border-[{c['edge_idle']}] {frame_bg}",
        "CALC_CHIP": (f"px-1.5 bg-[{c['chip_bg']}] text-[9px] tracking-[.2em] "
                      f"font-bold whitespace-nowrap"),
        "CALC_TILE": (f"rounded-[3px] border border-[{c['tile_edge']}] "
                      f"bg-[linear-gradient(180deg,{c['tile_a']},{c['tile_b']})]"),
        "CALC_INPUT": f"bg-[{c['input_bg']}] border border-[{c['input_edge']}] rounded-[2px]",
        "CALC_BTN": (f"bg-[{c['btn_bg']}] border border-[{c['btn_edge']}] "
                     f"text-[{c['btn_txt']}] rounded-[2px] text-[9px] tracking-[.16em]"),
        "CALC_BTN_PRIMARY": (f"bg-[rgba(34,211,238,.22)] border border-[{c['accent']}] "
                             f"text-[#c8f4fd] rounded-[2px] text-[9px] tracking-[.16em]"),
        "CALC_BTN_OFF": (f"bg-[{c['off_bg']}] border border-[{c['off_edge']}] "
                         f"text-[{c['off_txt']}] rounded-[2px] text-[9px] tracking-[.16em] "
                         f"cursor-not-allowed"),
        "CALC_EYEBROW": f"text-[8px] tracking-[.18em] text-[{c['label']}] whitespace-nowrap",
        "CALC_VALUE": f"text-[{c['bright']}] font-medium",
        "CALC_SOFT": f"text-[{c['soft']}]",
        "CALC_BODY": f"text-[{c['body']}]",
        "CALC_MUTED": f"text-[{c['muted']}]",
        "CALC_DIM": f"text-[{c['dim']}]",
        "CALC_POS": f"text-[{c['pos']}]",
        "CALC_NEG": f"text-[{c['neg']}]",
        "CALC_ACCENT": f"text-[{c['accent']}]",
        "CALC_WARN": f"text-[{c['warn']}]",
        "CALC_EDGE_POS": f"border-l-2 border-l-[{c['pos']}]",
        "CALC_EDGE_NEG": f"border-l-2 border-l-[{c['neg']}]",
        "CALC_EDGE_ACCENT": f"border-l-2 border-l-[{c['accent']}]",
        "CALC_EDGE_WARN": f"border-l-2 border-l-[{c['warn']}]",
    }


# The finite state->class maps behind the two data-driven colours the page sets
# at runtime. Mapping a known state onto a static class is the documented
# alternative to a runtime-built arbitrary value.
CALC_STATE_TEXT = ("CALC_POS", "CALC_NEG", "CALC_ACCENT", "CALC_WARN", "CALC_DIM")


def build_calc_css(theme):
    """Quasar-internal escape-hatch CSS for the Calculator, scoped ``.calc-v3``.

    Reaches only the DOM component ``.classes()`` cannot: the boxed q-field
    control and its leg-card variants, and the body-mounted cascading strategy
    popup (``.strat-menu-calc``, which is teleported OUT of the scope)."""
    c = theme["calc"]
    return f"""
/* Boxed inputs — the design's flat dark field. */
.calc-v3 .q-field__control{{
  background:{c['input_bg']};border:1px solid {c['input_edge']};border-radius:2px;
  padding:0 7px;min-height:30px;
}}
.calc-v3 .q-field__control:before,.calc-v3 .q-field__control:after{{border:0!important;}}
.calc-v3 .q-field--focused .q-field__control{{border-color:{c['accent']};}}
.calc-v3 .q-field__label{{color:{c['label']};font-size:8px;letter-spacing:.14em;}}
.calc-v3 .q-field__native,.calc-v3 .q-field__native input,
.calc-v3 .q-field__native span{{color:{c['soft']}!important;font-size:12px;}}
.calc-v3 .q-field__append .q-icon,.calc-v3 .q-field__prepend .q-icon{{
  color:#4a6070;font-size:14px;
}}
/* Strategy picker trigger internals. */
.calc-v3 .strategy-menu-btn .q-btn__content{{
  justify-content:space-between;flex:1;text-transform:none;
}}
.calc-v3 .strategy-menu-btn .q-icon{{color:#5b7f8c;}}
/* Leg CARD cells — two compact grid rows per leg. */
.calc-v3 .leg-card .q-field__control{{min-height:28px;padding:0 7px;}}
.calc-v3 .leg-card .q-field__control .q-field__native,
.calc-v3 .leg-card .q-field__marginal{{min-height:28px;padding-top:0;padding-bottom:0;}}
.calc-v3 .leg-card .q-field__append{{padding-left:0;}}
.calc-v3 .leg-card .q-field__native{{font-size:11px;letter-spacing:.08em;}}
.calc-v3 .leg-strike .q-field__native{{justify-content:center;text-align:center;font-size:12px;}}
/* Cascading strategy popup — teleported to <body>, so NOT under .calc-v3. */
.strat-menu-calc.q-menu{{
  background:{c['frame_a']}!important;border:1px solid {c['edge']};
  box-shadow:0 10px 28px rgba(0,0,0,.6);border-radius:3px;
}}
.strat-menu-calc .q-item,.strat-menu-calc .q-item__section,
.strat-menu-calc .q-item__label{{color:{c['soft']};}}
.strat-menu-calc .q-item:hover,.strat-menu-calc .q-item--active,
.strat-menu-calc .q-item.q-manuallyfocused{{background:{c['btn_bg']}!important;}}
.strat-menu-calc .q-icon{{color:#5b7f8c;}}
"""


CALC_KEYFRAMES_CSS = """
@keyframes blip{0%,100%{opacity:1}50%{opacity:.25}}
@keyframes scan{0%{transform:translateX(-120%)}100%{transform:translateX(320%)}}
"""


def build_calc_font_head_html(theme):
    """``<link>``s for the calculator's mono face, or "" when unset.

    JetBrains Mono is what makes ``tabular-nums`` align the matrix columns
    optically; the system fallback is a visible downgrade, not a neutral one."""
    try:
        url = str(theme["calc"].get("font_url", "")).strip()
    except Exception:  # noqa: BLE001
        return ""
    if not url:
        return ""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{url}">'
    )
```

**Step 5: Export the tokens**

At the module bottom, beside the other section exports:

```python
# ── Options Strategy Calculator — page-scoped language (.calc-v3) ────────────
_CALC_TOKENS = build_calc_tokens(THEME)
CALC_MONO = _CALC_TOKENS["CALC_MONO"]
CALC_PAGE = _CALC_TOKENS["CALC_PAGE"]
CALC_FRAME = _CALC_TOKENS["CALC_FRAME"]
CALC_FRAME_IDLE = _CALC_TOKENS["CALC_FRAME_IDLE"]
CALC_CHIP = _CALC_TOKENS["CALC_CHIP"]
CALC_TILE = _CALC_TOKENS["CALC_TILE"]
CALC_INPUT = _CALC_TOKENS["CALC_INPUT"]
CALC_BTN = _CALC_TOKENS["CALC_BTN"]
CALC_BTN_PRIMARY = _CALC_TOKENS["CALC_BTN_PRIMARY"]
CALC_BTN_OFF = _CALC_TOKENS["CALC_BTN_OFF"]
CALC_EYEBROW = _CALC_TOKENS["CALC_EYEBROW"]
CALC_VALUE = _CALC_TOKENS["CALC_VALUE"]
CALC_SOFT = _CALC_TOKENS["CALC_SOFT"]
CALC_BODY = _CALC_TOKENS["CALC_BODY"]
CALC_MUTED = _CALC_TOKENS["CALC_MUTED"]
CALC_DIM = _CALC_TOKENS["CALC_DIM"]
CALC_POS = _CALC_TOKENS["CALC_POS"]
CALC_NEG = _CALC_TOKENS["CALC_NEG"]
CALC_ACCENT = _CALC_TOKENS["CALC_ACCENT"]
CALC_WARN = _CALC_TOKENS["CALC_WARN"]
CALC_EDGE_POS = _CALC_TOKENS["CALC_EDGE_POS"]
CALC_EDGE_NEG = _CALC_TOKENS["CALC_EDGE_NEG"]
CALC_EDGE_ACCENT = _CALC_TOKENS["CALC_EDGE_ACCENT"]
CALC_EDGE_WARN = _CALC_TOKENS["CALC_EDGE_WARN"]
CALC_CSS = build_calc_css(THEME)
CALC_FONT_HEAD_HTML = build_calc_font_head_html(THEME)
```

**Step 6: Add the `[calc]` block to `config/theme.toml`**

Append, with a comment header matching the file's house style — one commented knob per line, mirroring the `_DEFAULTS` values exactly.

**Step 7: Run the tests**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_theme_calc.py -q)
```

Expected: all pass.

**Step 8: Confirm nothing else moved**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_theme.py -q)
```

**Step 9: Commit**

```bash
git add webgui/pages/options/theme.py webgui/tests/test_theme_calc.py config/theme.toml
git commit -m "feat(theme): a page-scoped [calc] language for the Calculator"
```

---

## Task 2: Strategy tags and thesis blurbs

Every strategy code gets the design's tag chips and one-line thesis. It lives in `strategies.py` — the shared, pure model — so the Calculator and Simulator cannot drift, and so a new strategy cannot ship untagged.

**Files:**
- Modify: `webgui/pages/options/strategies.py` (append after `strategy_label`)
- Test: `webgui/tests/test_strategies.py` (append)

**Step 1: Write the failing test**

```python
def test_every_template_has_tags_and_a_blurb():
    # The guard that matters: a strategy added to STRATEGY_TEMPLATES without
    # tags/blurb would render a bare frame with no thesis, and nothing else
    # would fail.
    for code in S.STRATEGY_TEMPLATES:
        assert S.strategy_tags(code), f"{code} has no tags"
        assert S.strategy_blurb(code), f"{code} has no blurb"


def test_tags_lead_with_the_cash_flow_direction():
    assert S.strategy_tags("PCS")[0] == "CREDIT"
    assert S.strategy_tags("VERT_CALL_DEBIT")[0] == "DEBIT"


def test_tags_state_the_leg_count_matching_the_template():
    for code, specs in S.STRATEGY_TEMPLATES.items():
        n = len(specs)
        assert f"{n} LEG" in S.strategy_tags(code) or f"{n} LEGS" in S.strategy_tags(code), \
            f"{code}: leg-count tag does not match its {n}-leg template"


def test_unknown_code_degrades_and_does_not_raise():
    assert S.strategy_tags("NOPE") == []
    assert S.strategy_blurb("NOPE") == ""
```

**Step 2: Run it**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_strategies.py -q)
```

Expected: `AttributeError: module 'pages.options.strategies' has no attribute 'strategy_tags'`.

**Step 3: Implement**

Add to `strategies.py`. Write the leg-count tag by **deriving** it from `STRATEGY_TEMPLATES` rather than typing it, so the test above cannot be satisfied by a lie:

```python
# Tag chips + a one-line thesis per strategy — the ① STRATEGY frame's copy.
# The FIRST tag is always the cash-flow direction (CREDIT/DEBIT), which is what
# the frame colours; the leg-count tag is DERIVED from the template, never typed.
_STRATEGY_FACTS = {
    "LONG_CALL":  ("DEBIT",  ["DIRECTIONAL", "BULLISH"],
                   "Buy a call outright. Unlimited upside, the whole premium at risk, and theta works against you every day."),
    "LONG_PUT":   ("DEBIT",  ["DIRECTIONAL", "BEARISH"],
                   "Buy a put outright. Pays on a downside move; the premium is the entire loss if it never comes."),
    "NAKED_CALL": ("CREDIT", ["UNDEFINED RISK", "BEARISH"],
                   "Sell an out-of-the-money call uncovered. Collects premium against unlimited upside risk."),
    "NAKED_PUT":  ("CREDIT", ["ENTRY", "BULLISH"],
                   "Short put held against cash. Either keeps the credit or takes assignment at a discount."),
    "PCS": ("CREDIT", ["DEFINED RISK", "BULLISH"],
            "Sell the near put, buy further out-of-the-money. Collects credit; max loss is spread width less credit."),
    "CCS": ("CREDIT", ["DEFINED RISK", "BEARISH"],
            "Sell the near call, buy further out. Profits if spot stays below the short strike into expiry."),
    "VERT_PUT_DEBIT":  ("DEBIT", ["DEFINED RISK", "BEARISH"],
                        "Buy the near put, sell further out to finance it. Directional with a capped payoff."),
    "VERT_CALL_DEBIT": ("DEBIT", ["DEFINED RISK", "BULLISH"],
                        "Buy the near call, sell further out. The cheapest way to express a measured upside move."),
    "IC": ("CREDIT", ["RANGE", "THETA"],
           "Short strangle wrapped in long wings. Wants spot pinned between the short strikes."),
    "CONDOR_CALL": ("DEBIT", ["RANGE", "THETA"],
                    "All-call condor. A defined-risk bet that spot finishes inside the two middle strikes."),
    "CONDOR_PUT": ("DEBIT", ["RANGE", "THETA"],
                   "All-put condor. Same range thesis as the call condor, built on the put side."),
    "BUTTERFLY_CALL": ("DEBIT", ["PIN", "THETA"],
                       "1-2-1 call fly. Cheap, narrow, and pays most when spot pins the body at expiry."),
    "BUTTERFLY_PUT": ("DEBIT", ["PIN", "THETA"],
                      "1-2-1 put fly. The put-side mirror of the call butterfly."),
    "IRON_BUTTERFLY": ("CREDIT", ["PIN", "THETA"],
                       "Both shorts at the money. Maximum credit, narrowest profit zone — for a hard pin thesis."),
    "CALENDAR_CALL": ("DEBIT", ["TERM STRUCTURE", "THETA"],
                      "Sell the front expiry, buy the back at the same strike. Harvests front-month decay against a longer tail."),
    "CALENDAR_PUT": ("DEBIT", ["TERM STRUCTURE", "THETA"],
                     "The put-side calendar. Same term-structure thesis, put strikes."),
    "DIAGONAL_CALL": ("DEBIT", ["TERM STRUCTURE", "BULLISH"],
                      "A calendar with the short leg rolled out of the money — decay plus a directional lean."),
    "DIAGONAL_PUT": ("DEBIT", ["TERM STRUCTURE", "BEARISH"],
                     "A put calendar skewed downside. Decay plus a bearish lean."),
}


def strategy_tags(code):
    """Tag chips for a strategy: [cash-flow, leg count, …descriptors].

    The leg-count chip is derived from ``STRATEGY_TEMPLATES`` so it can never
    disagree with the legs the template actually builds."""
    facts = _STRATEGY_FACTS.get(code)
    if not facts:
        return []
    flow, extra, _blurb = facts
    n = len(STRATEGY_TEMPLATES.get(code) or [])
    return [flow, f"{n} LEG" if n == 1 else f"{n} LEGS"] + list(extra)


def strategy_blurb(code):
    """One-line thesis for a strategy, or "" for an unknown code."""
    facts = _STRATEGY_FACTS.get(code)
    return facts[2] if facts else ""
```

**Step 4: Run the tests**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_strategies.py -q)
```

Expected: all pass.

**Step 5: Commit**

```bash
git add webgui/pages/options/strategies.py webgui/tests/test_strategies.py
git commit -m "feat(strategies): tag chips + a one-line thesis per strategy"
```

---

## Task 3: The page-side pure helpers

Five pure functions. This is where the design's added readouts actually come from.

**Files:**
- Modify: `webgui/pages/options/calculator.py` (add beside the existing extractors, ~line 205)
- Test: `webgui/tests/test_options_calculator.py` (append)

**Step 1: Write the failing test**

```python
# ── the redesign's page-side readouts ────────────────────────────────────────

def _chain(delta=-0.31):
    return {"putExpDateMap": {"2026-08-21:2": {"660.0": [
        {"mark": 2.4, "delta": delta, "volatility": 14.2}]}}}


def test_extract_delta_reads_the_chains_own_delta():
    assert C.extract_delta(_chain(), "put", 660.0, "2026-08-21") == -0.31


def test_extract_delta_is_none_when_the_contract_has_no_delta():
    # Index chains read hollow outside regular hours. A missing delta must
    # render as an em-dash, NOT as 0.00 — a confident wrong number on a row
    # that otherwise looks live.
    chain = {"putExpDateMap": {"2026-08-21:2": {"660.0": [{"mark": 2.4}]}}}
    assert C.extract_delta(chain, "put", 660.0, "2026-08-21") is None


def test_extract_delta_is_none_for_an_absent_strike():
    assert C.extract_delta(_chain(), "put", 999.0, "2026-08-21") is None


def test_extract_delta_tolerates_junk():
    assert C.extract_delta(None, "put", 660.0, None) is None
    assert C.extract_delta({}, "call", None, None) is None


def test_position_delta_flips_sign_for_a_short_leg():
    assert C.position_delta(-0.31, "long") == -0.31
    assert C.position_delta(-0.31, "short") == 0.31
    assert C.position_delta(None, "short") is None


def test_net_premium_is_positive_for_a_net_credit():
    legs = [{"side": "short", "premium": 3.0, "qty": 1},
            {"side": "long", "premium": 1.2, "qty": 1}]
    assert C.net_premium(legs) == 180.0          # (3.0 - 1.2) * 1 * 100


def test_net_premium_is_negative_for_a_net_debit():
    legs = [{"side": "long", "premium": 3.0, "qty": 2},
            {"side": "short", "premium": 1.0, "qty": 2}]
    assert C.net_premium(legs) == -400.0


def test_net_premium_treats_missing_values_as_zero():
    assert C.net_premium([{"side": "long"}]) == 0.0
    assert C.net_premium([]) == 0.0
    assert C.net_premium(None) == 0.0


def test_max_loss_estimate_for_a_credit_spread_is_width_less_credit():
    legs = [{"option_type": "put", "side": "short", "strike": 660, "premium": 3.0, "qty": 1},
            {"option_type": "put", "side": "long", "strike": 655, "premium": 1.2, "qty": 1}]
    # width 5 * 100 * 1 contract = 500, less the 180 credit
    assert C.max_loss_estimate(legs) == 320.0


def test_max_loss_estimate_for_a_net_debit_is_the_debit():
    legs = [{"option_type": "call", "side": "long", "strike": 670, "premium": 4.0, "qty": 1}]
    assert C.max_loss_estimate(legs) == 400.0


def test_matrix_pct_of_max_is_a_share_of_max_return():
    assert C.matrix_pct_of_max(90.0, 180.0) == 50.0
    assert C.matrix_pct_of_max(-90.0, 180.0) == -50.0


def test_matrix_pct_of_max_is_none_without_a_positive_max():
    # No max return means the ratio has no denominator — render an em-dash,
    # never a 0.0% that reads like a real measurement.
    assert C.matrix_pct_of_max(90.0, 0) is None
    assert C.matrix_pct_of_max(90.0, None) is None
    assert C.matrix_pct_of_max(None, 180.0) is None


def test_matrix_pct_of_max_refuses_the_unlimited_sentinel():
    # A long call's max_profit comes back as the 999999 placeholder. Dividing by
    # it would paint +0.0% down the entire matrix — a fake measurement on every
    # cell. There is no percentage of an uncapped return.
    assert C.matrix_pct_of_max(90.0, C.UNLIMITED) is None


def test_is_unlimited_identifies_only_the_sentinel():
    assert C.is_unlimited(999999) is True
    assert C.is_unlimited(999998) is False
    assert C.is_unlimited(None) is False
    assert C.is_unlimited(True) is False        # bool is an int subclass


def test_chain_status_facts_reports_the_three_phases():
    idle = C.chain_status_facts(loading=False, symbol="", chain=None)
    assert idle["label"] == "AWAITING SYMBOL" and idle["hint"] == "NOT LOADED"
    assert idle["state"] == "idle"

    busy = C.chain_status_facts(loading=True, symbol="SPY", chain=None)
    assert busy["label"] == "LOADING CHAIN" and busy["state"] == "loading"

    live = C.chain_status_facts(loading=False, symbol="SPY", chain=_chain())
    assert live["label"] == "CHAIN LOADED · SPY"
    assert live["hint"] == "LIVE" and live["state"] == "ready"


def test_chain_status_facts_does_not_claim_ready_on_an_empty_chain():
    # An empty dict is a chain that arrived carrying nothing — that is not
    # "loaded", and saying so would paint the frame green over no data.
    facts = C.chain_status_facts(loading=False, symbol="SPY", chain={})
    assert facts["state"] == "idle"
```

**Step 2: Run it**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_options_calculator.py -q)
```

Expected: `AttributeError` on `extract_delta`.

**Step 3: Implement** in `calculator.py`, after `extract_premium`:

```python
def extract_delta(chain, option_type, strike, expiry=None):
    """Per-contract delta for one leg from the cached chain, or ``None``.

    Reads the chain's OWN ``delta`` (the same field ``flow_alerts`` uses), so
    this is market delta rather than a second pricing model living in Tier 1.

    ⚠ Returns ``None`` — never ``0.0`` — when the contract carries no delta.
    Index option chains read hollow outside regular hours, and a ``0.00`` on an
    otherwise live-looking row is a confident wrong number."""
    if not isinstance(chain, dict) or not isinstance(strike, (int, float)):
        return None
    exp_iso = None
    if expiry is not None:
        exp_iso = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    map_key = "callExpDateMap" if option_type == "call" else "putExpDateMap"
    for exp_key, strikes in (chain.get(map_key) or {}).items():
        if exp_iso and exp_key.split(":")[0] != exp_iso:
            continue
        for strike_str, contracts in (strikes or {}).items():
            try:
                sk = float(strike_str)
            except (ValueError, TypeError):
                continue
            if abs(sk - strike) < 0.51 and isinstance(contracts, list) and contracts:
                d = contracts[0].get("delta")
                return float(d) if isinstance(d, (int, float)) else None
    return None


def position_delta(delta, side):
    """Contract delta signed for the POSITION: a short leg inverts it."""
    if not isinstance(delta, (int, float)):
        return None
    return -delta if side == "short" else delta


def net_premium(legs):
    """Net cash at entry in dollars: positive = credit received, negative = debit
    paid. ``None`` while ANY leg is unpriced — a fresh template carries
    ``premium: None`` on every leg, and "$0" would state a figure the page does
    not have. No legs at all IS zero: no position, no cash."""
    # (See the shipped implementation — this returns None on an unpriced leg.)


def max_loss_estimate(legs):
    """Worst case at expiration, in dollars.

    ⚠ SUPERSEDED DRAFT REMOVED. The width-of-the-widest-spread heuristic
    originally drafted here is WRONG: it returns a NEGATIVE max loss for a naked
    single, and mis-scales a net-credit ratio spread via max(qty). What shipped
    instead is the minimum of the EXPIRATION PAYOFF over {0} u strikes — exact,
    because a vanilla portfolio at one expiry is piecewise-linear with corners
    only at the strikes. Verified against a brute-force payoff scan for eleven
    structures. Returns None where no honest number exists: net-short call
    quantity (unbounded above), a short leg outliving a long, or a missing
    price/strike."""


def matrix_pct_of_max(pnl, max_profit):
    """A matrix cell's P&L as a percentage of the structure's MAX RETURN.

    Replaces the service's ``pnl_pct`` (which is a share of premium received) so
    the column reads directly against the MAX RETURN tile above the matrix.
    ``None`` when there is no positive max return — an em-dash, not a 0.0% that
    would read like a measurement."""
    if not isinstance(pnl, (int, float)):
        return None
    if not isinstance(max_profit, (int, float)) or max_profit <= 0:
        return None
    return pnl / max_profit * 100.0


def chain_status_facts(loading, symbol, chain):
    """The title-bar status pill + the ② SYMBOL frame's hint.

    ``{state, label, hint}`` where state is ``idle`` / ``loading`` / ``ready``.
    An EMPTY chain dict is ``idle``, not ``ready`` — a chain that arrived
    carrying nothing is not a loaded chain, and colouring the frame for it
    would announce data the page does not have."""
    if loading:
        return {"state": "loading", "label": "LOADING CHAIN", "hint": "···"}
    if _has_contracts(chain):
        sym = (symbol or "").strip().upper()
        return {"state": "ready",
                "label": f"CHAIN LOADED · {sym}" if sym else "CHAIN LOADED",
                "hint": "LIVE"}
    return {"state": "idle", "label": "AWAITING SYMBOL", "hint": "NOT LOADED"}
```

**Step 4: Run the tests**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_options_calculator.py -q)
```

Expected: all pass.

**Step 5: Commit**

```bash
git add webgui/pages/options/calculator.py webgui/tests/test_options_calculator.py
git commit -m "feat(calculator): the redesign's page-side readouts as pure functions"
```

---

## Task 4: Card layout in the shared leg editor

`build_leg_editor` gains `layout="card"`. The palette enters as a `tokens` argument so the Simulator can adopt the geometry without adopting the Calculator's colours.

**Files:**
- Modify: `webgui/pages/options/leg_editor.py`
- Test: `webgui/tests/test_leg_editor.py` (append)

**Step 1: Write the failing test**

```python
def test_default_tokens_cover_every_key_the_card_renders():
    # A missing token key would raise mid-render, on a page that looks fine in
    # every test that never mounts it.
    for key in ("frame", "eyebrow", "accent_long", "accent_short",
                "num", "delta", "remove", "remove_off", "add", "reset"):
        assert key in LE.DEFAULT_CARD_TOKENS
        assert isinstance(LE.DEFAULT_CARD_TOKENS[key], str)


def test_card_tokens_merge_over_the_defaults():
    merged = LE.card_tokens({"accent_long": "border-l-[#123456]"})
    assert merged["accent_long"] == "border-l-[#123456]"
    assert merged["frame"] == LE.DEFAULT_CARD_TOKENS["frame"]   # untouched


def test_card_tokens_ignores_unknown_keys():
    assert "bogus" not in LE.card_tokens({"bogus": "x"})


def test_can_remove_respects_the_min_legs_floor():
    assert LE.can_remove(3, min_legs=2) is True
    assert LE.can_remove(2, min_legs=2) is False
    assert LE.can_remove(1, min_legs=1) is False
    assert LE.can_remove(2, min_legs=0) is True


def test_delta_text_formats_signed_two_places():
    assert LE.delta_text(-0.31) == "-0.31"
    assert LE.delta_text(0.44) == "+0.44"
    assert LE.delta_text(0.0) == "+0.00"


def test_delta_text_renders_an_em_dash_for_no_reading():
    assert LE.delta_text(None) == "—"
    assert LE.delta_text("junk") == "—"
```

**Step 2: Run it**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_leg_editor.py -q)
```

Expected: `AttributeError: … has no attribute 'DEFAULT_CARD_TOKENS'`.

**Step 3: Implement the pure parts**

Add near the top of `leg_editor.py`:

```python
# Card-layout palette. Enters as an argument so the Calculator can pass its own
# near-black CALC_* tokens while the Simulator keeps the app-wide dark navy —
# the two pages share the GEOMETRY, not the colours.
DEFAULT_CARD_TOKENS = {
    "frame": "border border-[#213152] rounded-[2px] bg-[rgba(9,14,20,.55)]",
    "eyebrow": "text-[8px] tracking-[.14em] text-[#7f8db0] whitespace-nowrap truncate",
    "accent_long": "border-l-2 border-l-[#22d3ee]",
    "accent_short": "border-l-2 border-l-[#2dd4a7]",
    "num": "text-[10px] text-[#7189a0]",
    "delta": "text-[11px] text-[#cdd8ee] whitespace-nowrap",
    "remove": "text-[10px] text-[#9db0c2] border border-[#3a4a5b] rounded-[2px]",
    "remove_off": "text-[10px] text-[#4e5f70] border border-[#26313d] rounded-[2px] cursor-not-allowed",
    "add": "text-[9px] tracking-[.18em] text-[#a7dceb] border border-dashed border-[#3a6070] rounded-[2px]",
    "reset": "text-[9px] tracking-[.18em] text-[#8aa0b4] border border-[#2c3b4b] rounded-[2px]",
}


def card_tokens(overrides=None):
    """``DEFAULT_CARD_TOKENS`` with known keys overridden. Unknown keys are
    ignored, so a typo cannot silently introduce a token nothing reads."""
    out = dict(DEFAULT_CARD_TOKENS)
    for k, v in (overrides or {}).items():
        if k in out and isinstance(v, str) and v.strip():
            out[k] = v
    return out


def can_remove(leg_count, min_legs):
    """Whether the remove button is live at this leg count."""
    return leg_count > max(int(min_legs or 0), 1) or (int(min_legs or 0) == 0 and leg_count > 0)


def delta_text(delta):
    """Signed 2-dp delta, or an em-dash when there is no reading.

    Never renders 0.00 for a missing delta — see ``calculator.extract_delta``."""
    if not isinstance(delta, (int, float)) or isinstance(delta, bool):
        return "—"
    return f"{delta:+.2f}"
```

**Step 4: Run the pure tests**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_leg_editor.py -q)
```

Expected: all pass.

**Step 5: Add the card renderer**

Extend `build_leg_editor`'s signature:

```python
def build_leg_editor(container, *, strikes_for, expiries_for, show_premium,
                     on_change=lambda: None, spot_getter=lambda: 0.0, header=False,
                     layout="row", tokens=None, delta_for=None, min_legs=1,
                     on_reset=None):
```

Split `_render` into `_render_row()` (today's body, unchanged) and `_render_card()`, dispatched on `layout`. `_render_card` emits, per leg:

- a `ui.row` carrying `leg-card` (the CSS hook) + `tk["frame"]` + the side accent (`tk["accent_long"]` / `tk["accent_short"]`, chosen from the finite `{long, short}` set — never a runtime-built colour),
- the two-digit leg number in `tk["num"]`,
- **grid row 1** — a `grid grid-cols-[72px_78px_minmax(0,1fr)] gap-x-2` block: eyebrow labels TYPE / SIDE / EXPIRY, then the three selects,
- **grid row 2** — `grid grid-cols-[minmax(0,1.25fr)_46px_minmax(0,1fr)_44px] gap-x-2`: STRIKE / QTY / PREMIUM / DELTA, then the strike select (with `leg-strike`), the qty number, the premium number, and a `ui.label(delta_text(delta_for(leg) if delta_for else None))`,
- a remove `ui.button("✕")` styled `tk["remove"]` or `tk["remove_off"]`, disabled per `can_remove(len(state["legs"]), min_legs)`, with the tooltip `f"minimum {min_legs} legs"` when locked.

Below the stack, an ADD LEG button (`tk["add"]`) and — when `on_reset` is given — a RESET TO TEMPLATE button (`tk["reset"]`).

**Critical: keep the existing coercion.** Both renderers must run the same `coerce_choice` / `coerce_strike` pass over each leg *before* mounting its selects, and write the coerced values back to `state["legs"]`. `ui.select` raises `ValueError: Invalid value` on a value absent from its options — this is the code path behind the "legs don't transfer" bug.

`_sync_row_strikes`, `set_legs`, `get_legs`, `apply_template`, `apply_expiry`, `refresh_options` and the `dirty` flag are all **unchanged**.

**Step 6: Run the leg-editor and calculator suites**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_leg_editor.py tests/test_options_calculator.py tests/test_options_simulator.py -q)
```

**Step 7: Commit**

```bash
git add webgui/pages/options/leg_editor.py webgui/tests/test_leg_editor.py
git commit -m "feat(leg-editor): a card layout, with the palette injected per page"
```

---

## Task 5: The Simulator adopts the card layout

**Files:**
- Modify: `webgui/pages/options/simulator.py:408-411`
- Test: `webgui/tests/test_options_simulator.py` (append)

**Step 1: Write the failing test**

```python
def test_simulator_mounts_the_card_leg_editor_with_the_navy_palette():
    # The Simulator shares the card GEOMETRY but keeps the app-wide dark navy —
    # a Calculator restyle must not repaint this page. Passing no tokens is what
    # holds that, so assert the call site rather than the rendered colour.
    import inspect
    from pages.options import simulator as SIM
    src = inspect.getsource(SIM.render)
    assert 'layout="card"' in src
    assert "tokens=" not in src, "the Simulator must take the DEFAULT card tokens"
```

**Step 2: Run it — expect FAIL.**

**Step 3: Implement** — change the call site to pass `layout="card"` and drop `header=True`. Leave `show_premium=False`, `spot_getter` and `on_change` as they are; pass no `tokens` and no `delta_for` (the Simulator has no cached chain to read delta from).

**Step 4: Run the simulator suite**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_options_simulator.py -q)
```

**Step 5: Commit**

```bash
git add webgui/pages/options/simulator.py webgui/tests/test_options_simulator.py
git commit -m "feat(simulator): adopt the card leg layout, keeping the navy palette"
```

---

## Task 6: The P&L matrix

The matrix is a raw `ui.html()` fragment — **the documented out-of-scope case** for the Tailwind-first rule, so it keeps inline `style=` attributes inside the HTML string. `test_no_inline_style.py` only inspects `.style(` calls and `:style=` props, which this does not use.

**Files:**
- Modify: `webgui/pages/options/calculator.py` `_render_grid` (~line 297) and `_CELL_COLORS`
- Test: `webgui/tests/test_options_calculator.py` (append)

**Step 1: Write the failing test**

```python
def test_matrix_cell_facts_tints_by_magnitude_against_the_grid_extremes():
    hot = C.matrix_cell_facts(100.0, 180.0, g_max=100.0, g_min=-50.0)
    cool = C.matrix_cell_facts(10.0, 180.0, g_max=100.0, g_min=-50.0)
    assert hot["bg"].startswith("rgba(45,212,167,")
    assert cool["bg"].startswith("rgba(45,212,167,")
    assert hot["alpha"] > cool["alpha"]


def test_matrix_cell_facts_uses_the_loss_hue_below_zero():
    cell = C.matrix_cell_facts(-40.0, 180.0, g_max=100.0, g_min=-50.0)
    assert cell["bg"].startswith("rgba(251,95,124,")


def test_matrix_cell_pct_is_a_share_of_max_return():
    cell = C.matrix_cell_facts(90.0, 180.0, g_max=100.0, g_min=-50.0)
    assert cell["pct"] == "+50.0%"


def test_matrix_cell_pct_is_an_em_dash_without_a_max_return():
    cell = C.matrix_cell_facts(90.0, 0.0, g_max=100.0, g_min=-50.0)
    assert cell["pct"] == "—"


def test_matrix_headers_flag_the_expiry_column():
    hdrs = C.matrix_headers(["Now", "08/21", "08/23", "Exp"])
    assert [h["expiry"] for h in hdrs] == [False, False, False, True]
    assert hdrs[0]["label"] == "NOW $"


def test_matrix_headers_on_an_empty_grid():
    assert C.matrix_headers([]) == []
```

**Step 2: Run it — expect `AttributeError`.**

**Step 3: Implement**

```python
# The matrix's two hues — profit green / loss red at the design's alpha ramp.
_MATRIX_PROFIT_RGB = "45,212,167"
_MATRIX_LOSS_RGB = "251,95,124"
_MATRIX_PROFIT_FG = "#b8f5e4"
_MATRIX_LOSS_FG = "#ffd0d9"


def matrix_cell_facts(pnl, max_profit, g_max, g_min):
    """One matrix cell: ``{dollars, pct, bg, fg, alpha}``.

    Tint magnitude is relative to the grid's own extremes, so the strongest cell
    on screen is always fully saturated whatever the structure's scale. The pct
    is a share of MAX RETURN (see ``matrix_pct_of_max``) — an em-dash, never a
    0.0%, when there is no max return to divide by."""
    if not isinstance(pnl, (int, float)):
        return {"dollars": "—", "pct": "—", "bg": "transparent",
                "fg": "#6f8598", "alpha": 0.0}
    scale = g_max if pnl >= 0 else abs(g_min or 0)
    ratio = min(abs(pnl) / scale, 1.0) if scale else 0.0
    alpha = round(0.10 + ratio * 0.42, 3)
    rgb = _MATRIX_PROFIT_RGB if pnl >= 0 else _MATRIX_LOSS_RGB
    pct = matrix_pct_of_max(pnl, max_profit)
    return {
        "dollars": f"{pnl:+,.0f}",
        "pct": "—" if pct is None else (">999%" if pct > 999 else f"{pct:+.1f}%"),
        "bg": f"rgba({rgb},{alpha})",
        "fg": _MATRIX_PROFIT_FG if pnl >= 0 else _MATRIX_LOSS_FG,
        "alpha": alpha,
    }


def matrix_headers(eval_labels):
    """Column headers; the LAST column is the expiry and is flagged so the page
    can colour it amber (the design's one emphasised column)."""
    labels = list(eval_labels or [])
    out = []
    for i, lab in enumerate(labels):
        text = "NOW $" if str(lab).lower() == "now" else f"{lab} $"
        out.append({"label": text.upper(), "expiry": i == len(labels) - 1})
    return out
```

Then rewrite `_render_grid` to build its table from `matrix_headers` and `matrix_cell_facts`, passing `summary["max_profit"]` in. Keep the sticky header, the sticky price column, the amber spot row and the existing `_CENTER_SPOT_JS` scroll-to-spot timer. Add: amber text on the expiry column header, the `#080d13` header ground, `#22303e` header underline, `rgba(19,31,43,.7)` row rules, and `tabular-nums` on the figures.

`_render_grid`'s signature gains `max_profit`; update `_apply_result` to pass `(result.get("summary") or {}).get("max_profit")`.

**Step 4: Run the tests**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_options_calculator.py -q)
```

**Step 5: Commit**

```bash
git add webgui/pages/options/calculator.py webgui/tests/test_options_calculator.py
git commit -m "feat(calculator): matrix % of max return, on the design's palette"
```

---

## Task 7: The page rebuild

The `render()` rewrite. Everything below the widgets — `_capture` / `_restore`, `load_symbol`, `fetch_premiums`, `fetch_iv`, `do_calc`, `send_to_em`, the three version polls, `_prefill` and the two handoff paths — **is unchanged behaviour**. Preserve every one of them; this task moves widgets, not wiring.

**Files:**
- Modify: `webgui/pages/options/calculator.py` `render()` (lines ~359-460 for the layout; `_render_summary` ~274)
- Test: `webgui/tests/test_options_calculator.py`, `webgui/tests/test_shell.py` (verify the route still registers)

**Step 1: Write the failing test** — the metric-card facts, which is the only new pure surface here:

```python
def test_metric_cards_lead_with_credit_or_debit():
    credit = C.metric_cards({"entry_credit": 180.0, "max_loss": 320.0,
                             "max_profit": 180.0, "return_on_risk": 56.3,
                             "breakevens": [658.2], "pop": 71.4},
                            legs=[], spot=668.41, max_dte=9)
    assert credit[0]["label"] == "ENTRY CREDIT"
    assert credit[0]["accent"] == "pos"

    debit = C.metric_cards({"entry_credit": -400.0}, legs=[], spot=100.0, max_dte=9)
    assert debit[0]["label"] == "ENTRY DEBIT"
    assert debit[0]["accent"] == "accent"


def test_unlimited_max_return_reads_as_unlimited_not_as_a_number():
    # $999,999 is the service's placeholder, not a dollar figure. Printing it
    # would be a wrong number stated confidently.
    cards = C.metric_cards({"max_profit": C.UNLIMITED}, legs=[], spot=1.0, max_dte=9)
    ret = next(c for c in cards if c["label"] == "MAX RETURN")
    assert ret["value"] == "Unlimited"


def test_unlimited_max_risk_reads_as_unlimited():
    cards = C.metric_cards({"max_loss": C.UNLIMITED}, legs=[], spot=1.0, max_dte=9)
    risk = next(c for c in cards if c["label"] == "MAX RISK")
    assert risk["value"] == "Unlimited"


def test_return_on_risk_is_an_em_dash_when_either_side_is_uncapped():
    # calc_summary already zeroes return_on_risk in this case; a bare "0.0%"
    # would read as a measured zero return rather than "not defined".
    cards = C.metric_cards({"max_profit": C.UNLIMITED, "return_on_risk": 0.0},
                           legs=[], spot=1.0, max_dte=9)
    ror = next(c for c in cards if c["label"] == "RETURN ON RISK")
    assert ror["value"] == "—"


def test_metric_cards_are_always_six_and_accent_from_a_finite_set():
    cards = C.metric_cards({}, legs=[], spot=0, max_dte=0)
    assert len(cards) == 6
    assert {c["accent"] for c in cards} <= {"pos", "neg", "accent", "warn", "dim"}


def test_return_on_risk_card_reports_a_per_day_figure():
    cards = C.metric_cards({"return_on_risk": 56.0}, legs=[], spot=1.0, max_dte=7)
    ror = next(c for c in cards if c["label"] == "RETURN ON RISK")
    assert "8.00% per day" in ror["sub"]


def test_return_on_risk_per_day_does_not_divide_by_zero():
    cards = C.metric_cards({"return_on_risk": 56.0}, legs=[], spot=1.0, max_dte=0)
    ror = next(c for c in cards if c["label"] == "RETURN ON RISK")
    assert "per day" in ror["sub"]


def test_breakeven_card_reports_distance_from_spot():
    cards = C.metric_cards({"breakevens": [658.2]}, legs=[], spot=668.41, max_dte=9)
    be = next(c for c in cards if c["label"] == "BREAKEVEN(S)")
    assert "-1.53%" in be["sub"]


def test_breakeven_card_without_a_crossing():
    cards = C.metric_cards({"breakevens": []}, legs=[], spot=668.41, max_dte=9)
    be = next(c for c in cards if c["label"] == "BREAKEVEN(S)")
    assert be["value"] == "—"
```

**Step 2: Run it — expect FAIL.**

**Step 3: Implement `metric_cards`** in `calculator.py` — a pure function returning six `{label, value, sub, accent}` dicts, `accent` drawn from the finite `{pos, neg, accent, warn, dim}` set the page maps onto `CALC_EDGE_*` / `CALC_POS` classes. Order: ENTRY CREDIT/DEBIT · MAX RISK · MAX RETURN · RETURN ON RISK · BREAKEVEN(S) · PROB OF PROFIT. Every value degrades to `"—"` rather than to a zero.

**Step 4: Rebuild the layout**

Replace `_render_summary` with a `_render_metrics(box, summary, legs, spot, max_dte)` that mounts `metric_cards` output as `CALC_TILE` cards with the accent left border.

Then rewrite the `with ui.column()` block. Structure:

```python
ui.add_css(CALC_CSS + CALC_KEYFRAMES_CSS)
if CALC_FONT_HEAD_HTML:
    ui.add_head_html(CALC_FONT_HEAD_HTML)

with ui.column().classes(f"calc-v3 {CALC_PAGE} {CALC_MONO} w-full gap-[15px]"):
    # TITLE BAR — name + the live status pill
    with ui.row().classes("w-full items-center justify-between "
                          "border-b border-b-[#131d29] pb-2.5"):
        ui.label("STRATEGY CALCULATOR").classes(
            f"text-[14px] font-bold tracking-[.13em] text-[#eaf2f9]")
        status_pill = ui.row().classes("items-center gap-2 px-[11px] py-1 "
                                       "border rounded-[2px]")

    with ui.row().classes("w-full items-start gap-[18px] no-wrap"):
        # LEFT — 424px input column: ① STRATEGY, ③ LEGS, actions
        with ui.column().classes("shrink-0 w-[424px] min-w-[380px] gap-[15px]"):
            ...
        # RIGHT — ② SYMBOL, metric cards, P&L matrix
        with ui.column().classes("flex-1 min-w-0 gap-[13px]"):
            ...
```

The numbered frame helper — use it for all three:

```python
def _frame(number_label):
    """A numbered frame: the label chip sits ON the border line."""
    box = ui.column().classes(f"{CALC_FRAME} w-full gap-0 px-3 pt-5 pb-3")
    with box:
        with ui.row().classes("absolute -top-1.5 left-3 right-3 "
                              "items-center justify-between gap-2.5"):
            ui.label(number_label).classes(f"{CALC_CHIP} text-[#8fc6d6]")
    return box
```

Widget placement:

- **① STRATEGY** — the existing `strategy_menu.build_strategy_menu(...)` (pass `boxed=True`; give its popup the `strat-menu-calc` class), then a `flex-wrap` row of tag chips from `S.strategy_tags(code)`, then the `S.strategy_blurb(code)` paragraph. Rebuild the chips and blurb inside the existing `strategy_sel.on_value_change` handler.
- **② SYMBOL** — `symbol_in`, a spot readout, then `price_in` / `iv_in` / `rate_in` / `ivchg_in` / `contracts_in` / `nstrikes_in` / `expiry_sel` as labelled cells, then LOAD CHAIN + IV UPDATE, then the scan bar (`animate-[scan_1.1s_linear_infinite]`, hidden via `set_visibility` when not loading) and the status line.
- **③ LEGS** — the frame's right-hand strip shows `{n} LEGS`, `NET {net}` coloured by sign, `MAX LOSS {…}` in amber, recomputed in the editor's `on_change` from `net_premium` / `max_loss_estimate`. `leg_box` mounts the card editor with `layout="card"`, `tokens=` the `CALC_*` set, `delta_for=` a closure over the cached chain, `min_legs=2`, `on_reset=_seed_template`.

  ⚠ **`net_premium` and `max_loss_estimate` both return `None`, and the strip must render an em-dash when they do.** As shipped in Task 3: `net_premium` is `None` while ANY leg is unpriced — and `build_default_legs` sets `premium: None`, so that is the state of every fresh template before Fetch Premiums. `max_loss_estimate` is `None` when the loss is genuinely unbounded (net-short calls) or undecidable (a short leg outliving a long). Printing `$0` in either case states a number the page does not have. It also returns real, large figures — a naked put on a 660 strike is `65,700` — so format the strip compactly rather than letting it overflow the frame.
- **Actions** — a `grid-cols-2` of FETCH PREMIUMS · CALCULATE · EXPECTED MOVE · COPY TO SIMULATOR, plus the status line spanning both columns.
- **Right column below ②** — `metrics_box` + `grid_box`, both `set_visibility(False)` until a result lands, with a dashed empty panel shown in their place. Its copy comes from `chain_status_facts`: *AWAITING CHAIN* before load, *AWAITING CALCULATION* after.

**Two traps this page has hit before:**

1. **A `ui.highchart` is not involved here, but `set_visibility(False)` at build still matters** — nothing in this page mounts a chart, so no reflow set is needed. Do not add one.
2. **The `expiry_sel` programmatic writes must stay inside the `state["applying"]` guard**, or `_on_expiry_change` fires during `_apply_chain` and re-propagates expiries mid-load.

**Step 5: Run the whole webgui suite**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q -rf)
```

Expected: the Task-0 baseline plus the new tests, **no failures**. Compare the failing *set*, not the count.

**Step 6: Confirm the Tailwind-first guard still passes**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_no_inline_style.py -q)
```

**Step 7: Commit**

```bash
git add webgui/pages/options/calculator.py webgui/tests/test_options_calculator.py
git commit -m "feat(calculator): rebuild the screen to the three-step design"
```

---

## Task 8: Documentation

The manuals rot silently — nothing fails when they go stale, so they are updated in this change, not after it.

**Files:**
- Modify: `docs/webgui-routes.md` (`## /options/calculator`, line ~76)
- Modify: `webgui/page_help.py` (`"/options/calculator"`, line ~116)
- Modify: `CLAUDE.md` (the page-scoped theme-sections paragraph — add `[calc]` beside `[flow]`/`[console]`/`[macro]`/`[sectors]`/`[rotation]`)
- Modify: `docs/CHANGELOG.md` (a dated entry)

**Step 1:** Rewrite the `docs/webgui-routes.md` section: the three-step layout, the `[calc]` palette and `.calc-v3` scope, the six metric cards, the matrix's 7 columns and its **% of max return** meaning, per-leg delta from the chain (and its em-dash off-hours), and the unchanged `calc_load` / `calc_compute` / `calc_iv` flow.

**Step 2:** Rewrite the `page_help.py` entry to describe what the screen now shows. Check every claim against the code — this file is the most-read prose in the app and the least likely to be touched when a feature moves.

**Step 3:** Add `[calc]` to CLAUDE.md's list of page-scoped languages, noting it is the Calculator only and is not surfaced in Settings → Appearance.

**Step 4:** Add the CHANGELOG entry: what shipped, the design/plan pair, and the two deliberate semantic changes (matrix % of max return; the top-level Expiry kept against the mock).

**Step 5: Commit**

```bash
git add docs/webgui-routes.md webgui/page_help.py CLAUDE.md docs/CHANGELOG.md
git commit -m "docs: record the Calculator redesign across the four homes"
```

---

## Task 9: Verify it running

**Tests passing is not "verified in dev."** The DEV chip, the Status-page restart gating and the launcher guards were all green in tests and wrong in practice.

**Step 1: Full suite, failing set compared by name**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q -rf)
```

**Step 2: The service suite, because `strategies.py` is shared**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q
```

Expected: 1177 passed (2026-08-19 baseline). ⚠ `test_flow_alert_window.py::test_gth_signal_still_fires_at_the_open` is known-flaky.

**Step 3: Land it in dev**

⚠ **Do not preview from this worktree.** A worktree has no `config/env.local.toml`, so `repo_paths` resolves it to **prod** and it binds `:8500` — where the live prod stack already is. Commit here, fast-forward `Using_Highcharts` and `main` in the dev checkout, and preview there on `:9500`.

**Step 4: Restart the dev webgui and read the launcher log**

A failed bind is **silent** — the new server exits and the old one keeps serving, so you verify stale code while everything looks healthy. Confirm the port is genuinely free after killing (`Get-NetTCPConnection -LocalPort 9500 -State Listen`) and read the log for `[Errno 10048] error while attempting to bind`.

**Step 5: Drive the page**

```
preview_start {name: "webgui-dev"}   →  navigate to /options/calculator
```

Walk the flow: pick a strategy (tags + blurb change), type `SPY` and tab out (the pill goes amber → green, the scan bar runs, legs resolve to real strikes and show deltas), press CALCULATE (six cards + the matrix appear, spot row amber, last column amber).

**Step 6: Check the console and confirm the palette actually applied**

`read_console_messages`, then verify computed colours by DOM eval. ⚠ **Inject `* { transition: none !important; animation: none !important; }` before measuring** — the automation tab is backgrounded, so `document.timeline.currentTime` stays 0, every transition sits pinned at its START value, and `getComputedStyle` reports pre-change values forever. The status pill and scan bar are both animated, so this bites here.

Also confirm none of the `CALC_*` arbitrary values silently produced no rule: a Tailwind arbitrary containing a real space generates nothing. Spot-check the frame background and the tile gradient in the computed styles.

**Step 7: Screenshot and report**

`computer {action: "screenshot"}`. If it times out, verify via DOM eval instead and say so.

**Step 8: Final commit**

```bash
git add -A && git commit -m "chore(calculator): verified live in dev"
```

---

## Done when

- The screen matches the design's layout, palette and readouts.
- `options_svc` is untouched.
- The Simulator shows card legs in the app's navy palette, unchanged otherwise.
- The full webgui suite is green and the failing *set* elsewhere is unchanged.
- `docs/webgui-routes.md`, `webgui/page_help.py`, `CLAUDE.md` and the CHANGELOG all describe what shipped.
- The page has been driven end-to-end in **dev**, not merely tested.
