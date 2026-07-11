"""Shared **dark-navy "dashboard" theme** for the app pages (Tier-1).

The theme is expressed as **Tailwind design tokens** (reusable ``.classes()``
utility strings encoding the palette) plus a slim **``QUASAR_INTERNAL_CSS``**
escape-hatch for the Quasar/Highcharts-internal DOM that component ``.classes()``
strings can't reach (the boxed ``q-field`` control, the leg-table cells, the
``q-tab`` chrome, and the body-mounted ``.strat-menu-navy`` popup), scoped under
the historical ``.calc-v2`` scope hook.

**Every color comes from ``config/theme.toml``** (``repo_paths.THEME_TOML``) —
edit that file + restart the webgui to restyle the app WITHOUT touching code.
A missing file / key / malformed value falls back to the built-in dark-navy
defaults below, so the config is always safe to edit. The shared speedometer
gauge (``pages/gauge.py``) and the Sentiment/Rotation chart palette
(``pages/sentiment.py``) read the same ``THEME`` dict, so the whole look moves
together.

Apply to a new page::

    from pages.options.theme import QUASAR_INTERNAL_CSS, PAGE, CARD, EYEBROW, BTN_PRIMARY
    ui.add_css(QUASAR_INTERNAL_CSS)
    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):   # .calc-v2 = scope hook
        ui.label("Title").classes(f"text-h6 {LABEL}")
        with ui.column().classes(f"{CARD} w-full gap-3"):       # bordered navy panel
            ui.input("Symbol")                                   # auto-boxed (q-field)
            ui.button("Go", color=None).props("no-caps").classes(BTN_PRIMARY)

Inputs / selects / tabs inside ``.calc-v2`` are auto-restyled by
``QUASAR_INTERNAL_CSS``; **buttons need ``color=None``** (drops Quasar's
``bg-primary``) + a ``BTN`` / ``BTN_PRIMARY`` token. Token vocabulary: ``PAGE``
(navy gradient page wrap), ``CARD`` (bordered navy panel), ``EYEBROW`` (muted
label), ``LABEL`` / ``MUTED`` (text), ``BTN`` / ``BTN_PRIMARY`` (buttons),
``STRATEGY_BTN`` (boxed Strategy trigger box, applied alongside the
``strategy-menu-btn`` scope hook via ``strategy_menu.build_strategy_menu(
boxed=True)``), ``TXT_*`` (semantic state text colors), ``BTN_3D*`` (3D gradient
buttons), ``TILE_3D`` (raised metric tiles). The CSS-only hooks
``QUASAR_INTERNAL_CSS`` styles: ``.calc-v2`` (scope), ``.strat-menu-navy`` (the
teleported Strategy popup — GLOBAL, mounts on ``<body>`` outside ``.calc-v2``),
``.leg-head`` / ``.leg-row`` / ``.leg-strike`` (leg-table chrome). The **full
palette reference** lives in ``config/theme.toml`` (every knob, commented) and
the root ``CLAUDE.md`` "App theme — dark-navy 'dashboard'" section.
"""
import pathlib
import sys
import tomllib

# Repo root on sys.path -> repo_paths importable (same pattern as webgui/proxy.py).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Built-in defaults — the canonical dark-navy palette. config/theme.toml
# overrides these key-by-key (unknown keys / non-string values are ignored, so
# a typo can never break the app). Sections mirror the TOML.
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "palette": {
        # page surface
        "page_bg1": "#16243f", "page_bg2": "#0c1424", "page_bg3": "#0a0f1c",
        "page_border": "#1d2942",
        # cards / frames
        "card_bg": "#101a30", "card_border": "#213152",
        # text tiers
        "text": "#cdd8ee", "title": "#eaf0fb", "muted": "#7f8db0",
        "icon": "#8794b4", "input_text": "#e7edf8",
        # inputs (boxed q-field) + focus ring
        "input_bg": "#0c1426", "input_border": "#243353", "focus": "#3b82f6",
        # buttons
        "btn_bg": "#15213b", "btn_hover": "#1b2950", "btn_border": "#2a3a5c",
        "primary": "#2563eb", "primary_hover": "#1d4fd1",
    },
    "semantic": {
        # positive / caution / negative / neutral state colors (labels, tiles)
        "positive": "#66bb6a", "warning": "#ffa726",
        "negative": "#ef5350", "neutral": "#bdbdbd",
    },
    "buttons_3d": {
        # the standard 3D gradient buttons (Run scan / Paper actions / app-wide)
        "blue_top": "#5aa0e6", "blue_mid": "#3a7bc0",
        "blue_bottom": "#316eac", "blue_lip": "#244e78",
        "red_top": "#ef6b6b", "red_mid": "#d33f3f",
        "red_bottom": "#b53030", "red_lip": "#7a1f1f",
    },
    "gauge": {
        # the speedometer face ramp (left → right) + needle/pivot
        "low": "#ef5350", "mid": "#ffd54f", "high": "#66bb6a",
        "needle": "#f5f5f5",
    },
    "charts": {
        # Sentiment / Sector-Rotation value colors (lines, zones, table text)
        "green": "#66bb6a", "red": "#ef5350", "yellow": "#ffd54f",
        "flat": "#9e9e9e", "cyan": "#3fb6c7",
    },
    "typography": {
        # Text categories — sizes accept any CSS length ("15px" / "1.1rem").
        # ``family`` empty = keep the app default (Roboto).
        "family": "",            # app-wide font family
        "titles": "1.25rem",     # page & card titles (.text-h6)
        "subtitles": "1rem",     # section headings (.text-subtitle1)
        "sections": "0.875rem",  # sub-section headings (.text-subtitle2)
        "body": "14px",          # base text
        "small": "12px",         # eyebrows / captions / status lines (.text-xs)
    },
    "menu": {
        # Application menu (header bar + left nav drawer). Every knob defaults
        # "" = keep the stock Quasar look; a value emits an override.
        "accent": "",     # header bar + selected item pill (the Quasar primary)
        "drawer_bg": "",  # menu panel background
        "text": "",       # menu item text + icons
        "hover_bg": "",   # menu item hover wash
        "title": "",      # the "SCHWAB TRADING" drawer caption
    },
}


def load_theme(path=None):
    """``config/theme.toml`` merged over the built-in defaults.

    Defensive by construction: a missing/unreadable/malformed file → pure
    defaults; unknown sections/keys and non-string values are ignored — only a
    known key with a string value overrides. Never raises."""
    merged = {sec: dict(vals) for sec, vals in _DEFAULTS.items()}
    try:
        if path is None:
            from repo_paths import THEME_TOML
            path = THEME_TOML
        with open(path, "rb") as f:
            data = tomllib.load(f)
        for sec, vals in data.items():
            if sec in merged and isinstance(vals, dict):
                for k, v in vals.items():
                    if k in merged[sec] and isinstance(v, str) and v.strip():
                        merged[sec][k] = v.strip()
    except Exception:  # noqa: BLE001 — styling must never break app startup.
        pass
    return merged


def hex_rgb(hexstr, default=(0, 0, 0)):
    """``#rrggbb`` → (r, g, b) ints; the default on any malformed value."""
    try:
        s = str(hexstr).lstrip("#")
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:  # noqa: BLE001
        return default


def build_tokens(theme):
    """The full Tailwind design-token vocabulary generated from a theme dict."""
    p, s, b = theme["palette"], theme["semantic"], theme["buttons_3d"]
    state_txt = [f"text-[{s[k]}]" for k in ("positive", "warning", "negative", "neutral")]
    return {
        "PAGE": (
            f"rounded-[14px] border border-[{p['page_border']}] "
            f"p-[18px_20px_22px] text-[{p['text']}] "
            f"bg-[radial-gradient(130%_90%_at_50%_-20%,{p['page_bg1']}_0%,"
            f"{p['page_bg2']}_55%,{p['page_bg3']}_100%)]"
        ),
        # px-4 py-3.5 = 16px/14px = the historical .calc-card padding.
        "CARD": (
            f"bg-[{p['card_bg']}] border border-[{p['card_border']}] "
            f"rounded-[12px] px-4 py-3.5"
        ),
        "EYEBROW": f"text-[{p['icon']}] text-[{theme['typography']['small']}] tracking-[.02em]",
        "LABEL": f"text-[{p['title']}]",
        "MUTED": f"text-[{p['muted']}]",
        "BTN": (
            f"bg-[{p['btn_bg']}] hover:bg-[{p['btn_hover']}] text-[{p['text']}] "
            f"border border-[{p['btn_border']}] rounded-[9px] min-h-[40px] font-medium"
        ),
        "BTN_PRIMARY": (
            f"bg-[{p['primary']}] hover:bg-[{p['primary_hover']}] text-white "
            f"rounded-[9px] min-h-[40px] font-semibold"
        ),
        "STRATEGY_BTN": (
            f"bg-[{p['input_bg']}] hover:border-[{p['focus']}] "
            f"border border-[{p['input_border']}] text-[{p['input_text']}] "
            f"rounded-[8px] min-h-[40px] font-normal"
        ),
        # Shared 3D gradient buttons (the app-wide button standard). Apply with
        # color=None so Quasar's bg-primary doesn't compete.
        "BTN_3D": (
            f"bg-[linear-gradient(180deg,{b['blue_top']}_0%,{b['blue_mid']}_55%,"
            f"{b['blue_bottom']}_100%)] text-white "
            "rounded-[7px] font-semibold min-h-[34px] "
            f"shadow-[0_4px_0_0_{b['blue_lip']},0_6px_10px_rgba(0,0,0,.4)] "
            "hover:brightness-110 active:translate-y-[4px] "
            f"active:shadow-[0_1px_0_0_{b['blue_lip']},0_2px_4px_rgba(0,0,0,.4)] "
            "transition-[transform,box-shadow,filter] duration-100"
        ),
        "BTN_3D_DANGER": (
            f"bg-[linear-gradient(180deg,{b['red_top']}_0%,{b['red_mid']}_55%,"
            f"{b['red_bottom']}_100%)] text-white "
            "rounded-[7px] font-semibold min-h-[34px] "
            f"shadow-[0_4px_0_0_{b['red_lip']},0_6px_10px_rgba(0,0,0,.4)] "
            "hover:brightness-110 active:translate-y-[4px] "
            f"active:shadow-[0_1px_0_0_{b['red_lip']},0_2px_4px_rgba(0,0,0,.4)] "
            "transition-[transform,box-shadow,filter] duration-100"
        ),
        # Raised "3D" bevel for static metric TILES — additive (no background) so
        # it layers over each tile's own bg without flattening it. rgba-only, so
        # it works over any palette.
        "TILE_3D": (
            "rounded-[8px] "
            "shadow-[inset_0_2px_0_0_rgba(255,255,255,.3),"
            "0_5px_0_0_rgba(0,0,0,.4),0_10px_20px_rgba(0,0,0,.5)]"
        ),
        # Semantic STATE colors — the finite palette behind data-driven label
        # colors. Set reactively via .classes(remove=STATE_TEXT_CLASSES, add=TXT_*).
        "TXT_POS": state_txt[0],
        "TXT_WARN": state_txt[1],
        "TXT_NEG": state_txt[2],
        "TXT_NEUTRAL": state_txt[3],
        "STATE_TEXT_CLASSES": " ".join(state_txt),
    }


def build_quasar_css(theme):
    """The Quasar-internal / teleported escape-hatch CSS from a theme dict.

    These rules style the Quasar-internal DOM that component ``.classes()``
    strings can't reach: the boxed q-field control (incl. the leg-table
    variants), the q-tab chrome, and the body-mounted ``.strat-menu-navy``
    popup. Scoped under ``.calc-v2`` (the scope hook)."""
    p = theme["palette"]
    focus_rgb = hex_rgb(p["focus"], (59, 130, 246))
    return f"""
/* Boxed dark inputs — restyle the standard q-field control into a filled box. */
.calc-v2 .q-field__control{{
  background:{p['input_bg']};border:1px solid {p['input_border']};border-radius:8px;padding:0 10px;min-height:40px;
}}
.calc-v2 .q-field__control:before,.calc-v2 .q-field__control:after{{border:0!important;}}
.calc-v2 .q-field--focused .q-field__control{{
  border-color:{p['focus']};box-shadow:0 0 0 2px rgba({focus_rgb[0]},{focus_rgb[1]},{focus_rgb[2]},.28);
}}
.calc-v2 .q-field__label{{color:{p['muted']};}}
.calc-v2 .q-field__native,.calc-v2 .q-field__native input,
.calc-v2 .q-field__native textarea,.calc-v2 .q-field__native span{{color:{p['input_text']}!important;}}
.calc-v2 .q-field__append .q-icon,.calc-v2 .q-field__prepend .q-icon{{color:{p['icon']};}}
/* Strategy menu button internals — the q-btn__content layout + icon color are
   Quasar-internal (component .classes() can't reach them), so they must survive
   when a later phase swaps the button BOX style to the STRATEGY_BTN token. The
   base box rule (bg/border/radius/min-height) is intentionally NOT here. */
.calc-v2 .strategy-menu-btn .q-btn__content{{justify-content:space-between;flex:1;text-transform:none;}}
.calc-v2 .strategy-menu-btn .q-icon{{color:{p['icon']};}}
/* Leg table header row */
.calc-v2 .leg-head{{color:{p['muted']};font-size:12px;padding:0 2px 4px;}}
/* Leg table rows — compact cells (less top/bottom padding, shorter height) and
   tighter side padding so "call"/"put" are not horizontally clipped. */
.calc-v2 .leg-row .q-field__control{{min-height:32px;padding:0 6px;}}
.calc-v2 .leg-row .q-field__control .q-field__native,
.calc-v2 .leg-row .q-field__marginal{{min-height:32px;padding-top:0;padding-bottom:0;}}
.calc-v2 .leg-row .q-field__append{{padding-left:0;}}
.calc-v2 .leg-row .q-field__native{{font-size:13px;}}
/* Centered strike value in the leg table. */
.calc-v2 .leg-strike .q-field__native{{justify-content:center;text-align:center;}}
/* Tabs (Simulator) — light labels, accent indicator, transparent panels so the
   dark-transparent Highcharts panels sit on the page gradient. */
.calc-v2 .q-tabs{{color:{p['icon']};}}
.calc-v2 .q-tab__label{{font-weight:500;}}
.calc-v2 .q-tab--active{{color:{p['input_text']};}}
.calc-v2 .q-tab__indicator{{background:{p['focus']};}}
.calc-v2 .q-tab-panels,.calc-v2 .q-tab-panel,.calc-v2 .q-panel{{background:transparent!important;}}
/* Cascading Strategy menu popup — teleported to <body>, so NOT under .calc-v2.
   Theme it to match the cards. */
.strat-menu-navy.q-menu{{
  background:{p['card_bg']}!important;border:1px solid {p['card_border']};
  box-shadow:0 10px 28px rgba(0,0,0,.55);border-radius:10px;
}}
.strat-menu-navy .q-item{{color:{p['input_text']};border-radius:6px;}}
.strat-menu-navy .q-item__section,.strat-menu-navy .q-item__label{{color:{p['input_text']};}}
.strat-menu-navy .q-item:hover,
.strat-menu-navy .q-item--active,
.strat-menu-navy .q-item.q-manuallyfocused{{background:{p['btn_hover']}!important;}}
.strat-menu-navy .q-icon{{color:{p['icon']};}}
"""


def set_theme_values(text, updates):
    """Update ``key = "value"`` lines in a theme-TOML string, preserving comments.

    ``updates`` is ``{section: {key: new_value}}``. Line-based + section-scoped:
    only a known ``key =`` line inside the targeted ``[section]`` is rewritten
    (the quoted value is replaced; everything else on the line — alignment,
    trailing comment — is kept). A key not present in the text is a no-op, so a
    hand-trimmed config never gains surprise lines. Pure (string in/out); the
    Settings page writes through :func:`save_theme_values`."""
    import re
    lines = text.splitlines(keepends=True)
    section = None
    out = []
    for line in lines:
        m_sec = re.match(r"\s*\[([^\]]+)\]", line)
        if m_sec:
            section = m_sec.group(1).strip()
        elif section in updates:
            m_kv = re.match(r'(\s*(\w+)\s*=\s*)"[^"]*"(.*)$', line, re.DOTALL)
            if m_kv and m_kv.group(2) in updates[section]:
                new = str(updates[section][m_kv.group(2)])
                line = f'{m_kv.group(1)}"{new}"{m_kv.group(3)}'
        out.append(line)
    return "".join(out)


def save_theme_values(updates, path=None):
    """Write ``updates`` (``{section: {key: value}}``) into ``config/theme.toml``.

    Comment-preserving (see :func:`set_theme_values`). If the file is missing it
    is (re)created from the built-in defaults first so every knob line exists.
    Returns the merged theme dict re-loaded from disk. Used by the Settings
    page's Appearance section; the running app picks the change up on the next
    webgui restart (the theme loads once at import)."""
    if path is None:
        from repo_paths import THEME_TOML
        path = THEME_TOML
    p = pathlib.Path(path)
    if not p.exists():
        rows = []
        for sec, vals in _DEFAULTS.items():
            rows.append(f"[{sec}]")
            rows += [f'{k} = "{v}"' for k, v in vals.items()]
            rows.append("")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(rows), encoding="utf-8")
    p.write_text(set_theme_values(p.read_text(encoding="utf-8"), updates),
                 encoding="utf-8")
    return load_theme(path)


# Word expansions for knob_label — keys are TOML knob-name fragments.
_LABEL_WORDS = {"bg": "background", "btn": "button"}


def knob_label(key):
    """Human label for a TOML knob key — whole words, no shorthand.

    ``card_bg`` → "Card background", ``btn_hover`` → "Button hover",
    ``page_bg1`` → "Page background 1"."""
    import re
    words = []
    for w in str(key).split("_"):
        m = re.match(r"([a-z]+)(\d+)$", w)
        suffix = ""
        if m:
            w, suffix = m.group(1), " " + m.group(2)
        words.append(_LABEL_WORDS.get(w, w) + suffix)
    return " ".join(words).capitalize()


def build_typography_css(theme):
    """App-wide text-category CSS from ``[typography]``.

    The categories map onto the classes the pages already use — Quasar's
    ``.text-h6`` (titles) / ``.text-subtitle1`` (subtitles) / ``.text-subtitle2``
    (sections) and Tailwind's ``.text-xs`` (small) — plus the base ``body`` size,
    so no page needs editing to follow a size change. ``!important`` beats the
    frameworks' own definitions. An empty ``family`` emits no font rule (keeps
    the app default Roboto). Injected app-wide by ``main._layout``."""
    ty = theme["typography"]
    rules = []
    if ty["family"]:
        rules.append(f"body{{font-family:{ty['family']}!important;}}")
    rules += [
        f"body{{font-size:{ty['body']};}}",
        f".text-h6{{font-size:{ty['titles']}!important;}}",
        f".text-subtitle1{{font-size:{ty['subtitles']}!important;}}",
        f".text-subtitle2{{font-size:{ty['sections']}!important;}}",
        f".text-xs{{font-size:{ty['small']}!important;}}",
    ]
    return "\n".join(rules)


def build_nav_css(theme):
    """Application-menu override CSS from ``[menu]`` (drawer bg / text / hover /
    caption). Emits a rule ONLY for a non-empty knob, so all-default config
    produces an empty string and the stock Quasar look can never drift. The
    ``accent`` knob is NOT css — ``main._layout`` feeds it to ``ui.colors(
    primary=…)``, which recolors the header bar AND the active nav pill (both
    ride the Quasar primary). Injected app-wide by ``main._layout``."""
    m = theme["menu"]
    rules = []
    if m["drawer_bg"]:
        rules.append(f".nav-drawer{{background:{m['drawer_bg']}!important;}}")
    if m["text"]:
        rules.append(
            f".nav-drawer a,.nav-drawer .q-item,.nav-drawer .q-item__label,"
            f".nav-drawer .q-icon{{color:{m['text']}!important;}}")
    if m["hover_bg"]:
        rules.append(f".nav-drawer a:hover{{background:{m['hover_bg']}!important;}}")
    if m["title"]:
        rules.append(f".nav-drawer .nav-title{{color:{m['title']}!important;}}")
    return "\n".join(rules)


# ---------------------------------------------------------------------------
# Module-level theme + tokens — loaded ONCE at import (restart the webgui after
# editing config/theme.toml). All existing `.classes(CARD)` / `.classes(BTN_3D)`
# call sites are unchanged; they now carry the configured palette.
# ---------------------------------------------------------------------------
THEME = load_theme()
_TOKENS = build_tokens(THEME)

PAGE = _TOKENS["PAGE"]
CARD = _TOKENS["CARD"]
EYEBROW = _TOKENS["EYEBROW"]
LABEL = _TOKENS["LABEL"]
MUTED = _TOKENS["MUTED"]
BTN = _TOKENS["BTN"]
BTN_PRIMARY = _TOKENS["BTN_PRIMARY"]
STRATEGY_BTN = _TOKENS["STRATEGY_BTN"]
BTN_3D = _TOKENS["BTN_3D"]
BTN_3D_DANGER = _TOKENS["BTN_3D_DANGER"]
TILE_3D = _TOKENS["TILE_3D"]
TXT_POS = _TOKENS["TXT_POS"]
TXT_WARN = _TOKENS["TXT_WARN"]
TXT_NEG = _TOKENS["TXT_NEG"]
TXT_NEUTRAL = _TOKENS["TXT_NEUTRAL"]
STATE_TEXT_CLASSES = _TOKENS["STATE_TEXT_CLASSES"]

QUASAR_INTERNAL_CSS = build_quasar_css(THEME)
TYPOGRAPHY_CSS = build_typography_css(THEME)   # injected app-wide by main._layout
NAV_THEME_CSS = build_nav_css(THEME)           # "" when [menu] is all-default
MENU_ACCENT = THEME["menu"]["accent"]          # "" = keep the stock Quasar primary
