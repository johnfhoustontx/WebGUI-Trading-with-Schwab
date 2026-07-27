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
        # Text categories — sizes in PIXELS ("20px", or a bare number like "20";
        # larger = bigger text). Defaults render identically to the framework
        # sizes they replace (20px = the old 1.25rem etc.). ``family`` empty =
        # keep the app default (Roboto). ``font_url`` empty = no web font loaded.
        # ``numeric`` empty = default figures; "tabular" = tabular-nums app-wide
        # (aligned numeric columns — the trading-terminal look). Both default to
        # the stock render, so a plain config is unchanged.
        "family": "",        # app-wide font family
        "font_url": "",      # web-font stylesheet URL (Google Fonts etc.) — "" = none
        "numeric": "",       # "tabular" = font-variant-numeric:tabular-nums app-wide
        "titles": "20px",    # page & card titles (.text-h6)
        "subtitles": "16px", # section headings (.text-subtitle1)
        "sections": "14px",  # sub-section headings (.text-subtitle2)
        "body": "14px",      # base text
        "small": "12px",     # eyebrows / captions / status lines (.text-xs)
    },
    "brand": {
        # App identity: the header lockup (monogram + two-tone wordmark). The
        # name is split into two halves so each can carry its own gradient, the
        # way the logo artwork does. The gradients + font apply to the WORDMARK
        # ONLY — the body/data font stays [typography].family, because a heavy
        # display face hurts readability in the dense signal tables.
        # Colors are sampled from webgui/static/img/neuralstrike-logo.jpg
        # (p50→p95 of each wordmark band; lower percentiles are anti-aliasing
        # against the black background and read too dark).
        "name_a": "Neural",       # first half of the wordmark (gold)
        "name_b": "Strike",       # second half (blue)
        "font_family": "Montserrat",   # "" = use the app font for the wordmark
        # Brand web font, loaded separately from [typography].font_url.
        "font_url": ("https://fonts.googleapis.com/css2"
                     "?family=Montserrat:wght@800&display=swap"),
        "font_weight": "800",
        "a_from": "#C9A356",      # "Neural" gradient — deep gold
        "a_to": "#FBEAA0",        # "Neural" gradient — highlight gold
        "b_from": "#2C6FB4",      # "Strike" gradient — deep blue
        "b_to": "#35A3F5",        # "Strike" gradient — bright blue
        "mark": "/static/img/neuralstrike-mark.png",  # "" = no logo, glyph tile
    },
    "menu": {
        # Application menu (header bar + left nav drawer). Every knob defaults
        # "" = keep the stock Quasar look; a value emits an override.
        # accent → ui.colors(primary) = Quasar-colored CONTROLS ONLY (switches,
        # sliders, color=primary buttons). NOT the header (header_bg) and NOT the
        # nav pill / tab fills / icon accent (hardcoded rgba in main._NAV_CSS).
        "accent": "",
        "header_bg": "",  # top header bar background (decoupled from accent)
        "drawer_bg": "",  # menu panel background
        "text": "",       # menu item text + icons
        "hover_bg": "",   # menu item hover wash
        "title": "",      # the drawer caption ("WORKSPACE")
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
    pr = hex_rgb(p["primary"], (107, 134, 255))       # primary glow rgb
    dr = hex_rgb(b["red_mid"], (229, 89, 91))         # solid-danger glow rgb
    # Flat "Deep Slate" buttons — built as locals so the legacy BTN_3D[_DANGER]
    # names can alias them (every existing call site flattens with no per-site edit).
    _btn_primary = (
        f"bg-[{p['primary']}] hover:brightness-110 text-[#0b1024] "
        "rounded-[9px] min-h-[34px] font-semibold "
        f"shadow-[0_4px_14px_-4px_rgba({pr[0]},{pr[1]},{pr[2]},0.6)]"
    )
    _btn_danger = (
        f"bg-[{s['negative']}]/[.13] hover:bg-[{s['negative']}]/20 "
        f"text-[{s['negative']}] border border-[{s['negative']}]/40 "
        "rounded-[9px] min-h-[34px] font-medium"
    )
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
        "EYEBROW": (f"text-[{p['icon']}] "
                    f"text-[{normalize_size(theme['typography']['small'])}] tracking-[.02em]"),
        "LABEL": f"text-[{p['title']}]",
        "MUTED": f"text-[{p['muted']}]",
        # ── Buttons — flat "Deep Slate" (mimic the redesign mockup) ───────────
        # Secondary: dark fill + faint hairline border + body text (Reload, Close,
        # Load, Test sound, Copy-to-…). Apply with color=None (drops bg-primary).
        "BTN": (
            f"bg-[{p['btn_bg']}] hover:bg-[{p['btn_hover']}] text-[{p['text']}] "
            f"border border-[{p['btn_border']}] rounded-[9px] min-h-[34px] font-medium"
        ),
        # Primary: solid blue accent + dark navy text + a soft accent glow.
        "BTN_PRIMARY": _btn_primary,
        # Danger (ghost/outlined): faint red tint + red border + red text — the
        # in-table destructive style (Delete / Delete all closed / Reset).
        "BTN_DANGER": _btn_danger,
        # Danger (solid): a full red fill + glow — the heavyweight stop action
        # (Terminate → "Stop all services"), used sparingly.
        "BTN_DANGER_SOLID": (
            f"bg-[{b['red_mid']}] hover:brightness-110 text-white "
            "rounded-[9px] min-h-[40px] font-semibold "
            f"shadow-[0_4px_14px_-4px_rgba({dr[0]},{dr[1]},{dr[2]},0.6)]"
        ),
        "STRATEGY_BTN": (
            f"bg-[{p['input_bg']}] hover:border-[{p['focus']}] "
            f"border border-[{p['input_border']}] text-[{p['input_text']}] "
            f"rounded-[8px] min-h-[34px] font-normal"
        ),
        # BTN_3D / BTN_3D_DANGER — LEGACY names kept as aliases so every existing
        # .classes(BTN_3D[_DANGER]) call site flattens to the Deep Slate look with
        # no per-site edit. BTN_3D → the flat primary; BTN_3D_DANGER → ghost danger.
        "BTN_3D": _btn_primary,
        "BTN_3D_DANGER": _btn_danger,
        # Flat metric TILE (Deep Slate) — a hairline border + 12px radius, NO bevel
        # or drop shadow. Additive (no background), so it layers over each tile's
        # own bg/color without flattening it. Name kept (legacy call sites) though
        # it is no longer "3D".
        "TILE_3D": f"rounded-[12px] border border-[{p['card_border']}] shadow-none",
        # Semantic STATE colors — the finite palette behind data-driven label
        # colors. Set reactively via .classes(remove=STATE_TEXT_CLASSES, add=TXT_*).
        "TXT_POS": state_txt[0],
        "TXT_WARN": state_txt[1],
        "TXT_NEG": state_txt[2],
        "TXT_NEUTRAL": state_txt[3],
        "STATE_TEXT_CLASSES": " ".join(state_txt),
        # Semantic BADGE fills (Deep Slate) — a translucent tint background + the
        # matching colored foreground, as ONE Tailwind class for a q-badge/pill.
        # bg-[hex]/opacity + text-[hex] both JIT-generate (verified). POS/WARN/NEG
        # follow the configured semantic palette; ACCENT (OPEN) + MUTED (closed)
        # are the Deep Slate blue-accent-text / grey pills.
        "BADGE_POS": f"bg-[{s['positive']}]/15 text-[{s['positive']}] rounded-[6px]",
        "BADGE_WARN": f"bg-[{s['warning']}]/15 text-[{s['warning']}] rounded-[6px]",
        "BADGE_NEG": f"bg-[{s['negative']}]/15 text-[{s['negative']}] rounded-[6px]",
        "BADGE_ACCENT": "bg-[#a9b6ff]/15 text-[#a9b6ff] rounded-[6px]",
        "BADGE_MUTED": "bg-white/5 text-[#8891ab] rounded-[6px]",
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


def normalize_size(v):
    """A text size as CSS: a bare number means PIXELS (``"15"`` → ``"15px"``).

    Any explicit unit ("15px", "1.1rem") passes through unchanged, so hand-set
    values keep working — the bare-number convenience is for the Settings page
    where "just type a bigger number" should do the obvious thing."""
    s = str(v or "").strip()
    return f"{s}px" if s.replace(".", "", 1).isdigit() else s


def build_typography_css(theme):
    """App-wide text-category CSS from ``[typography]``.

    The categories map onto the classes the pages already use — Quasar's
    ``.text-h6`` (titles) / ``.text-subtitle1`` (subtitles) / ``.text-subtitle2``
    (sections) and Tailwind's ``.text-xs`` (small) — plus the base ``body`` size,
    so no page needs editing to follow a size change. Sizes go through
    :func:`normalize_size` (bare number = pixels). ``!important`` beats the
    frameworks' own definitions. An empty ``family`` emits no font rule (keeps
    the app default Roboto). Injected app-wide by ``main._layout``."""
    _passthrough = ("family", "font_url", "numeric")
    ty = {k: (v if k in _passthrough else normalize_size(v))
          for k, v in theme["typography"].items()}
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
    # tabular-nums app-wide (aligned numeric columns) when [typography].numeric
    # is set — the trading-terminal look. Empty = stock figures, so the default
    # render is unchanged.
    if str(ty.get("numeric", "")).strip().lower().startswith("tab"):
        rules.append("body{font-variant-numeric:tabular-nums;}")
    return "\n".join(rules)


def build_font_head_html(theme):
    """A ``<link>`` preconnect + web-font stylesheet from ``[typography].font_url``.

    Returns the head HTML that loads the configured web font (e.g. IBM Plex from
    Google Fonts) so the ``[typography].family`` actually resolves, or ``""`` when
    no ``font_url`` is set (keeping the app default Roboto, no extra request).
    Injected app-wide by ``main._layout`` via ``ui.add_head_html``. Defensive: any
    problem yields ``""``."""
    try:
        url = str(theme["typography"].get("font_url", "")).strip()
    except Exception:  # noqa: BLE001
        url = ""
    if not url:
        return ""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{url}">'
    )


def build_brand_font_head_html(theme):
    """A ``<link>`` for the BRAND web font (``[brand].font_url``), or ``""``.

    Separate from ``build_font_head_html`` on purpose: the brand face styles the
    header wordmark only, while ``[typography].font_url`` is the app-wide body
    font. Either may be set without the other. Defensive: any problem → ``""``
    (the wordmark then falls back down its font stack, still readable)."""
    try:
        url = str(theme["brand"].get("font_url", "")).strip()
    except Exception:  # noqa: BLE001
        url = ""
    if not url:
        return ""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{url}">'
    )


def build_brand_css(theme):
    """CSS for the header wordmark's two gradient halves.

    RAW CSS, not Tailwind classes — deliberately. The wordmark needs
    ``linear-gradient`` + ``background-clip:text``, and the bundled Tailwind JIT
    does not reliably emit arbitrary classes containing gradients or ``rgba()``
    (the documented trap that silently produced no rule for the old nav pill).

    Falls back gracefully at every step: a missing/blank ``font_family`` just
    leaves the inherited app font in front of the stack, and a malformed color
    yields its default via ``_DEFAULTS``. Never raises."""
    try:
        b = theme["brand"]
    except Exception:  # noqa: BLE001
        b = _DEFAULTS["brand"]
    fam = str(b.get("font_family", "")).strip()
    stack = (f"'{fam}', " if fam else "") + "'Segoe UI', system-ui, sans-serif"
    weight = str(b.get("font_weight", "800")).strip() or "800"
    return f"""
.brand-word {{
  font-family: {stack};
  font-weight: {weight};
  font-size: 16px;
  letter-spacing: .01em;
  text-transform: uppercase;
  line-height: 1;
  white-space: nowrap;
}}
/* Two halves, each carrying its own gradient — the logo's gold/blue split.
   -webkit- prefix first: Chromium/WebKit still need it for background-clip. */
.brand-word .a, .brand-word .b {{
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}}
.brand-word .a {{
  background-image: linear-gradient(180deg,{b.get('a_to')} 0%,{b.get('a_from')} 100%);
}}
.brand-word .b {{
  background-image: linear-gradient(180deg,{b.get('b_to')} 0%,{b.get('b_from')} 100%);
}}
/* The logo mark. Its artwork is on black, which sits naturally on the dark
   header — so no plate/gradient behind it, unlike the old glyph tile. */
.brand-mark {{
  width: 28px; height: 28px; border-radius: 8px; flex: none;
  object-fit: cover; display: block;
}}
"""


def build_nav_css(theme):
    """Application-menu override CSS from ``[menu]`` (drawer bg / text / hover /
    caption). Emits a rule ONLY for a non-empty knob, so all-default config
    produces an empty string and the stock Quasar look can never drift. The
    ``accent`` knob is NOT css — ``main._layout`` feeds it to ``ui.colors(
    primary=…)``, which now reaches only Quasar-colored CONTROLS (switches,
    sliders, ``color=primary`` buttons): the header bar is decoupled via
    ``header_bg`` (below), and the active nav pill + tab-strip fills are
    hardcoded rgba washes in ``main._NAV_CSS`` (the bundled Tailwind JIT does
    not reliably emit ``var()``/``rgba()`` arbitraries, so they cannot ride
    ``--q-primary``). Injected app-wide by ``main._layout``."""
    m = theme["menu"]
    rules = []
    if m.get("header_bg"):
        # The header bar is DECOUPLED from the accent: accent (ui.colors primary)
        # drives Quasar controls, while header_bg keeps the top bar dark (else a
        # blue accent would paint the whole header blue).
        rules.append(f".q-header{{background:{m['header_bg']}!important;}}")
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
BTN_DANGER = _TOKENS["BTN_DANGER"]
BTN_DANGER_SOLID = _TOKENS["BTN_DANGER_SOLID"]
STRATEGY_BTN = _TOKENS["STRATEGY_BTN"]
BTN_3D = _TOKENS["BTN_3D"]
BTN_3D_DANGER = _TOKENS["BTN_3D_DANGER"]
TILE_3D = _TOKENS["TILE_3D"]
TXT_POS = _TOKENS["TXT_POS"]
TXT_WARN = _TOKENS["TXT_WARN"]
TXT_NEG = _TOKENS["TXT_NEG"]
TXT_NEUTRAL = _TOKENS["TXT_NEUTRAL"]
STATE_TEXT_CLASSES = _TOKENS["STATE_TEXT_CLASSES"]
BADGE_POS = _TOKENS["BADGE_POS"]
BADGE_WARN = _TOKENS["BADGE_WARN"]
BADGE_NEG = _TOKENS["BADGE_NEG"]
BADGE_ACCENT = _TOKENS["BADGE_ACCENT"]
BADGE_MUTED = _TOKENS["BADGE_MUTED"]

QUASAR_INTERNAL_CSS = build_quasar_css(THEME)
TYPOGRAPHY_CSS = build_typography_css(THEME)   # injected app-wide by main._layout
FONT_HEAD_HTML = build_font_head_html(THEME)   # "" when no [typography].font_url
NAV_THEME_CSS = build_nav_css(THEME)           # "" when [menu] is all-default
MENU_ACCENT = THEME["menu"]["accent"]          # "" = keep the stock Quasar primary

# ── Brand identity (header lockup) ──────────────────────────────────────────
BRAND_NAME_A = THEME["brand"]["name_a"]        # "Neural" — the gold half
BRAND_NAME_B = THEME["brand"]["name_b"]        # "Strike" — the blue half
BRAND_NAME = f"{BRAND_NAME_A}{BRAND_NAME_B}"   # plain text: browser titles, logs
BRAND_MARK = THEME["brand"]["mark"]            # "" = no logo image
BRAND_CSS = build_brand_css(THEME)
BRAND_FONT_HEAD_HTML = build_brand_font_head_html(THEME)  # "" when no font_url
