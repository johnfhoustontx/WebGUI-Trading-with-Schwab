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
    # ── The Market Regime Console palette (/sentiment only) ──────────────────
    # A hard-edge "console" language deliberately scoped to ONE page: square
    # corners, near-black ground, teal accent, glow-only shadows. Source of
    # truth: docs/design/2026-08-14-market-regime-console/README.md.
    #
    # Colors are HEX ONLY, like every other section — the spec quotes several as
    # rgba(120,140,160,α), and the ALPHA lives in the token layer instead
    # (Tailwind's `/[0.18]` modifier, verified to generate). That keeps this file
    # editable by the Settings colour pickers' hex contract.
    #
    # NOT surfaced in Settings → Appearance, for the same reason [brand] is not:
    # that editor's sections are single-kind, and this one mixes colours with
    # font text.
    "console": {
        # surfaces + lines
        "page_bg": "#05070b",        # the page ground
        "wash": "#0b1620",           # inner stop of the radial page wash
        "card_from": "#0e161e",      # card gradient, 160deg, both stops at 95%
        "card_to": "#070a0f",
        "cell_bg": "#0a0e14",        # stat/readout cells inside a hairline grid
        "line": "#788ca0",           # ONE base for every hairline/border/track;
                                     # = the spec's rgba(120,140,160,·)
        "accent": "#22e3d3",         # primary accent: dial arc, links, rules
        # text ramp, lightest → faintest
        "text_primary": "#e7edf3",
        "text_secondary": "#a9bac7",
        "text_muted": "#8fa1b0",
        "text_label": "#6b7d8d",
        "text_dim": "#5d6f7e",
        "text_faint": "#4b5a67",
        # data colours
        "positive": "#35d68a",       # bullish / positive change / high scores
        "negative": "#f2646b",       # stressed / negative change / divergence
        "warning": "#e0b74e",        # caution, previous close, mid-low scores
        "olive": "#b9cf6a",          # mid-high score band
        "yellow": "#d7d76a",         # trend day band
        # regime hues. NOTE these are the CONSOLE's, and they differ from the
        # [charts] set the old panel used (balanced cyan→blue, whipsaw
        # flat-grey→light grey, breakout yellow→amber). The handoff is
        # authoritative for this page; nothing else moves.
        "regime_mean_reversion": "#6f86ff",
        "regime_trending": "#35d68a",
        "regime_breakout": "#f0b83c",
        "regime_breakout_zero": "#6a5c33",   # the dormant/0.0% muted state
        "regime_choppy": "#c3ccd6",
        "regime_crisis": "#f2646b",
        # display face — condensed, for headings/names/hero numerals. Loaded
        # separately from [typography] and [brand], the same way those two are
        # separate from each other. "" loads nothing and falls back to the app
        # font (measured 21% WIDER than Rajdhani, so letter-spaced headings
        # reflow on swap — do not pack them to the Rajdhani metric).
        "font_family": "Rajdhani",
        "font_url": ("https://fonts.googleapis.com/css2"
                     "?family=Rajdhani:wght@500;600;700&display=swap"),
    },
    # ── The Macro Board redesign palette (/market only) ──────────────────────
    # A dense notched "instrument" language scoped to ONE page. Source of truth:
    # the approved macro-board redesign spec + reference prototype. HEX ONLY;
    # alphas live in the token layer. Not surfaced in Settings → Appearance.
    # ── Options Flow panels (the /options/gamma Flow + Net Prem views only) ──
    # The Premium Divergence + Flow Field redesign. Page-scoped like [console]
    # and [macro], and not surfaced in Settings → Appearance for the same reason.
    #
    # The call/put pair is DELIBERATELY identical to gamma.POS_COLOR/NEG_COLOR:
    # these panels sit behind the same subtab strip as the plasma heatmap, and a
    # cyan that meant "call" on one tab and something else on the next would be
    # worse than no colour coding at all. Source: the 2026-08-15 handoff spec.
    "flow": {
        "call": "#35C8FF",       # call premium — the plasma positive
        "put": "#FF4D8D",        # put premium — the plasma negative
        "spot": "#EAF6FF",       # underlying price. Was yellow, which read as a
                                 # THIRD premium series beside the other two.
        "call_line": "#7FDCFF",  # the hairline drawn over the call glow
        "put_line": "#FF86B3",
        "call_deep": "#2A76E0",  # far stop of the call ribbon gradient
        "put_deep": "#96247A",
        "live": "#5EF0B8",       # the streaming pill + its pulsing dot
        "panel_from": "#0B1A2C",  # 150deg panel wash, three stops
        "panel_mid": "#060E1A",
        "panel_to": "#04080F",
        "title": "#E8F7FF",
        "label": "#AECDE8",      # every muted label, used at several alphas
        "ice": "#BEF8FF",        # chip rings, the cursor, the FLAT rule
        "grid": "#78AAD2",       # gridlines (rendered at ~6% — a hair, not a rule)
    },
    "macro": {
        "void": "#03060D", "panel": "#080D18", "tile": "#0A1020",
        "grid": "#0E1728", "edge": "#182741", "edge_hi": "#26405F",
        "txt": "#DCE8F8", "dim": "#6B7F9E", "faint": "#3D4F6B",
        "up": "#00E5A0", "dn": "#FF4D6D", "flat": "#5C6F8C", "cyan": "#35E0FF",
        "wash_in": "#0A1830",
        "sat_ceiling": 0.45,
        "font_url": ("https://fonts.googleapis.com/css2"
                     "?family=Rajdhani:wght@500;600;700"
                     "&family=Chakra+Petch:wght@600;700"
                     "&family=IBM+Plex+Mono:wght@400;500;600&display=swap"),
    },
    "sectors": {
        "void": "#080808", "edge": "#1A1A1A", "edge_hi": "#2C2C2C",
        "txt": "#F2F2F2", "dim": "#8C8C8C", "faint": "#5A5A5A",
        "up": "#3FD98A", "dn": "#E8697B", "warn": "#E0A63C",
        "font_url": ("https://fonts.googleapis.com/css2"
                     "?family=Instrument+Sans:wght@400;500;600;700"
                     "&family=JetBrains+Mono:wght@400;500;600&display=swap"),
    },
    "rotation": {
        # Only the two grounds and the faces. Every other colour on this board
        # is a WARM-NEUTRAL LADDER at oklch(L 0.006-0.01 90) plus the four
        # quadrant hues — both derived in ``pages/rotation_view.py``, because
        # both are data/design ramps rather than palette knobs (the same call
        # made for the [sectors] heat ramp).
        "void": "#08090B",             # page ground
        "panel": "#0B0C0E",            # quadrant panel fill, one step lighter
        "font_url": ("https://fonts.googleapis.com/css2"
                     "?family=Instrument+Sans:wght@400;500;600;700"
                     "&family=JetBrains+Mono:wght@400;500;600&display=swap"),
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
  width: 44px; height: 44px; border-radius: 12px; flex: none;
  object-fit: cover; display: block;
}}
"""


# --- Market Regime Console (/sentiment) -------------------------------------
# The spec's alphas, kept as named constants so a hairline is never a magic
# number scattered across builders. Applied as Tailwind's arbitrary opacity
# modifier (`/[0.18]`), which was measured to generate — so these are the
# spec's EXACT values, not the nearest step on Tailwind's core scale.
CONSOLE_ALPHA = {"hairline": 0.18, "border": 0.2, "track": 0.09,
                 "track_border": 0.14, "rule": 0.18, "card": 0.95}


def _alpha_hex(value, alpha):
    """'#0e161e' + 0.95 -> '#0e161ef2' — an 8-digit hex, which Tailwind accepts
    inside a gradient arbitrary (measured). Used where the alpha belongs to a
    gradient STOP, which the `/[…]` modifier cannot reach."""
    a = max(0, min(255, int(round(float(alpha) * 255))))
    return f"{value}{a:02x}"


def console_glow(value, px=16, alpha=0.45, spread=None):
    """A glow-only box-shadow class — the console has no elevation shadows.

    rgba rather than a hex because the alpha is the point; both forms were
    measured to generate (see the Phase 0 spike in the redesign plan)."""
    r, g, b = hex_rgb(value, (255, 255, 255))
    sp = f"_{spread}" if spread else ""
    return f"shadow-[0_0_{px}px{sp}_rgba({r},{g},{b},{alpha})]"


def build_console_tokens(theme):
    """Tailwind token vocabulary for the console page.

    Every value is a class string applied with ``.classes(...)`` — the page is
    Tailwind-first like the rest of the app, and the ONE ``ui.add_css`` it
    injects carries only the ``pulseDot`` keyframes (an animation cannot be
    expressed as a utility)."""
    c = theme["console"]
    a = CONSOLE_ALPHA
    line = c["line"]
    fam = str(c.get("font_family", "")).strip()
    # Underscores are Tailwind's space escape; the stack was measured to resolve
    # as `Rajdhani, "IBM Plex Sans", system-ui, sans-serif`.
    display = (f"font-['{fam}',_'IBM_Plex_Sans',_system-ui,_sans-serif]"
               if fam else "font-['IBM_Plex_Sans',_system-ui,_sans-serif]")
    return {
        # surfaces
        "CONSOLE_PAGE": (
            f"bg-[{c['page_bg']}] "
            f"bg-[radial-gradient(1200px_700px_at_22%_10%,{c['wash']},"
            f"{c['page_bg']}_62%)]"),
        "CONSOLE_CARD": (
            f"bg-[linear-gradient(160deg,{_alpha_hex(c['card_from'], a['card'])},"
            f"{_alpha_hex(c['card_to'], a['card'])})] "
            f"border border-[{line}]/[{a['border']}]"),
        "CONSOLE_CELL": f"bg-[{c['cell_bg']}]",
        # A hairline GRID is `gap-px` over this background — the gap IS the rule,
        # which is the handoff's own technique and avoids per-cell borders.
        "CONSOLE_HAIRLINE": f"bg-[{line}]/[{a['hairline']}]",
        "CONSOLE_TRACK": (f"bg-[{line}]/[{a['track']}] "
                          f"border border-[{line}]/[{a['track_border']}]"),
        "CONSOLE_RULE": f"border-[{c['accent']}]/[{a['rule']}]",
        "CONSOLE_DIVIDER": f"border-[{line}]/[{a['border']}]",
        # type
        "CONSOLE_DISPLAY": display,
        "CON_TXT": f"text-[{c['text_primary']}]",
        "CON_TXT_SECONDARY": f"text-[{c['text_secondary']}]",
        "CON_TXT_MUTED": f"text-[{c['text_muted']}]",
        "CON_TXT_LABEL": f"text-[{c['text_label']}]",
        "CON_TXT_DIM": f"text-[{c['text_dim']}]",
        "CON_TXT_FAINT": f"text-[{c['text_faint']}]",
        # data
        "CON_ACCENT": f"text-[{c['accent']}]",
        "CON_POS": f"text-[{c['positive']}]",
        "CON_NEG": f"text-[{c['negative']}]",
        "CON_WARN": f"text-[{c['warning']}]",
    }


def console_colors(theme):
    """The console's raw hexes, for the SVG builders (which take attributes, not
    classes) and for per-regime lookups."""
    c = theme["console"]
    return {
        "accent": c["accent"], "line": c["line"], "cell": c["cell_bg"],
        "positive": c["positive"], "negative": c["negative"],
        "warning": c["warning"], "olive": c["olive"], "yellow": c["yellow"],
        "text": c["text_primary"], "muted": c["text_muted"],
        "label": c["text_label"], "dim": c["text_dim"],
        "regimes": {k: c[f"regime_{k}"] for k in
                    ("mean_reversion", "trending", "breakout", "choppy",
                     "crisis")},
        "regime_zero": c["regime_breakout_zero"],
    }


def build_console_font_head_html(theme):
    """A ``<link>`` for the console DISPLAY font (``[console].font_url``), or "".

    Separate from ``[typography]`` and ``[brand]`` for the same reason those two
    are separate from each other: a condensed display face suits headings and
    hero numerals, not the dense data tables the body font serves."""
    try:
        url = str(theme["console"].get("font_url", "")).strip()
    except Exception:  # noqa: BLE001
        return ""
    if not url:
        return ""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{url}">'
    )


# The console's ONE escape-hatch rule. A keyframes animation genuinely cannot be
# a utility class, which is the same justification the market ticker's marquee
# uses. Everything else on the page is Tailwind.
CONSOLE_KEYFRAMES_CSS = """
@keyframes pulseDot { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
.con-pulse { animation: pulseDot 2.4s ease-in-out infinite; }
"""


# ── Options Flow panels (/options/gamma Flow + Net Prem) helpers ─────────────
# These two views are built as ONE ``ui.html`` fragment each (SVG chart + chrome)
# rather than from NiceGUI components, so they consume RAW HEXES, not Tailwind
# token strings — a raw HTML-string fragment is the documented out-of-scope case
# for the Tailwind-first rule (the same exemption the Calculator's P&L heatmap
# and the Gamma Explain block already use). Alphas are applied at the use site,
# so this stays a plain hex vocabulary the Settings colour contract could accept.
_FLOW_KEYS = ("call", "put", "spot", "call_line", "put_line", "call_deep",
              "put_deep", "live", "panel_from", "panel_mid", "panel_to",
              "title", "label", "ice", "grid")


def flow_colors(theme):
    """The Options Flow panels' raw hexes, defaults filling anything missing.

    Total: a malformed ``[flow]`` section (or none at all) yields the built-in
    palette rather than raising — styling must never break a page whose whole
    job is showing where the money went."""
    section = theme.get("flow") if isinstance(theme, dict) else None
    section = section if isinstance(section, dict) else {}
    out = {}
    for key in _FLOW_KEYS:
        value = section.get(key)
        out[key] = value if isinstance(value, str) and value.strip() \
            else _DEFAULTS["flow"][key]
    return out


# The Flow panels' ONE ``ui.add_css`` escape-hatch. A keyframes animation
# genuinely cannot be a utility class or an inline style — the same
# justification CONSOLE_KEYFRAMES_CSS and the market ticker's marquee use.
# Everything else on these panels is an inline style inside the fragment.
#
# ``.fx-pulse`` rather than reusing ``.con-pulse``: that one is injected only on
# /sentiment, and a shared name across two page-scoped blocks would make either
# page's animation silently depend on the other having been visited.
FLOW_KEYFRAMES_CSS = """
@keyframes fxPulseDot { 0%,100% { opacity: 1; transform: scale(1); }
                        50% { opacity: .35; transform: scale(.72); } }
.fx-pulse { animation: fxPulseDot 1.7s ease-in-out infinite; }
.fx-panel { cursor: crosshair; }
"""


# ── Macro Board (/market) redesign helpers ───────────────────────────────────
# Page-scoped, mirroring the console pattern: raw hexes for computed values +
# a Tailwind token vocabulary + ONE ``ui.add_css`` escape-hatch block (clip-path
# notches, keyframes, the radial background, and per-tile custom-prop washes —
# exactly the four things the house rule names as un-expressible in Tailwind).
def macro_colors(theme):
    """The macro board's raw hexes + the saturation ceiling, for the page's
    computed values (heat/wash alphas, direction colours, breadth bar)."""
    m = theme["macro"]
    out = {k: m[k] for k in ("void", "panel", "tile", "grid", "edge", "edge_hi",
                             "txt", "dim", "faint", "up", "dn", "flat", "cyan",
                             "wash_in")}
    try:
        out["sat_ceiling"] = float(m.get("sat_ceiling", 0.45) or 0.45)
    except (TypeError, ValueError):
        out["sat_ceiling"] = 0.45
    return out


def build_macro_tokens(theme):
    """Tailwind class-string vocabulary for the macro board (Tailwind-first).

    Layout/spacing/flex/colour stay in ``.classes(...)``; only clip-path,
    keyframes, the radial page ground and the per-tile custom-prop washes live in
    ``build_macro_css``. Fonts use the ``font-[...]`` arbitrary (underscores =
    the Tailwind space escape)."""
    m = theme["macro"]
    return {
        "MB_TITLE": "font-['Chakra_Petch',system-ui,sans-serif]",
        "MB_SYM": "font-['Rajdhani',system-ui,sans-serif]",
        "MB_MONO": "font-['IBM_Plex_Mono',ui-monospace,monospace]",
        "MB_TXT": f"text-[{m['txt']}]",
        "MB_DIM": f"text-[{m['dim']}]",
        "MB_FAINT": f"text-[{m['faint']}]",
        "MB_UP": f"text-[{m['up']}]",
        "MB_DN": f"text-[{m['dn']}]",
        "MB_FLAT": f"text-[{m['flat']}]",
        "MB_CYAN": f"text-[{m['cyan']}]",
        "MB_PANEL_BG": f"bg-[{m['panel']}]",
        "MB_TILE_BG": f"bg-[{m['tile']}]",
        "MB_RAIL_BG": f"bg-[{m['panel']}]",
        "MB_EDGE": f"border-[{m['edge']}]",
        "MB_EDGE_HI": f"border-[{m['edge_hi']}]",
        "MB_TRACK_BG": f"bg-[{m['grid']}]",
    }


def build_macro_font_head_html(theme):
    """``<link>``s for the macro board's three faces (``[macro].font_url``), or ""
    — Chakra Petch (title) / Rajdhani (symbols) / IBM Plex Mono (numbers), tuned
    to their tracking. Injected only on the /market page."""
    try:
        url = str(theme["macro"].get("font_url", "")).strip()
    except Exception:  # noqa: BLE001
        return ""
    if not url:
        return ""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{url}">'
    )


def build_macro_css(theme):
    """The macro board's ONE ``ui.add_css`` escape-hatch block, scoped under
    ``.macro-board``. Carries exactly what Tailwind cannot express: the radial
    page ground, clip-path notches (panels + tiles), the flash keyframes
    (ignition bar + price flare, and the Skin-B bloom), the per-tile custom-prop
    washes, and the sheared breadth bar / pulsing live dot. Colours come from
    ``[macro]`` so the palette stays config-driven."""
    m = theme["macro"]
    return f"""
.macro-board{{
  background:
    radial-gradient(1400px 700px at 50% -12%, {m['wash_in']} 0%, transparent 62%),
    {m['void']};
}}
/* notched top rail */
.macro-board .mb-rail{{
  clip-path:polygon(18px 0,100% 0,100% calc(100% - 18px),calc(100% - 18px) 100%,0 100%,0 18px);
  background:linear-gradient(90deg,rgba(53,224,255,.07),transparent 42%),{m['panel']};
}}
/* notched group panels + left accent bar (per-panel --mb-acc) */
.macro-board .mb-panel{{
  position:relative;
  clip-path:polygon(14px 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%,0 14px);
}}
.macro-board .mb-panel::before{{
  content:"";position:absolute;left:0;top:0;width:2px;height:100%;
  background:linear-gradient(180deg,var(--mb-acc,{m['cyan']}),transparent 78%);opacity:.9;
}}
/* notched tiles */
.macro-board .mb-tile{{
  position:relative;overflow:hidden;
  clip-path:polygon(0 0,100% 0,100% calc(100% - 8px),calc(100% - 8px) 100%,0 100%);
}}
/* ignition bar + price flare — fire ONLY when .fl is (re)applied on change */
.macro-board .mb-ig{{position:absolute;left:0;top:0;height:2px;width:100%;
  transform:scaleX(0);transform-origin:left;opacity:0;background:var(--c,{m['flat']})}}
.macro-board .mb-tile.fl .mb-ig{{animation:mbig .95s cubic-bezier(.2,.7,.3,1)}}
@keyframes mbig{{0%{{transform:scaleX(0);opacity:1}}30%{{transform:scaleX(1);opacity:1}}100%{{transform:scaleX(1);opacity:0}}}}
.macro-board .mb-tile.fl .mb-px{{animation:mbpx .95s ease-out}}
@keyframes mbpx{{0%{{color:var(--c,{m['flat']});text-shadow:0 0 14px var(--c,{m['flat']})}}100%{{color:{m['txt']};text-shadow:none}}}}
/* Skin A — Instrument: subtle magnitude-scaled wash over the tile fill */
.macro-board.macro-a .mb-tile{{background-image:linear-gradient(160deg,var(--wash,transparent),transparent 62%)}}
/* Skin B — Heat Lattice: no panel chrome, continuous heat fill, bloom on change */
.macro-board.macro-b .mb-panel{{background:transparent !important;border-color:transparent !important;clip-path:none}}
.macro-board.macro-b .mb-panel::before{{width:100%;height:1px;background:linear-gradient(90deg,var(--mb-acc,{m['cyan']}),transparent 55%)}}
.macro-board.macro-b .mb-tile{{clip-path:none;background:var(--heat,{m['tile']}) !important;box-shadow:inset 0 0 0 1px rgba(255,255,255,.045)}}
.macro-board.macro-b .mb-tile.fl{{animation:mblat .95s ease-out}}
@keyframes mblat{{0%{{box-shadow:inset 0 0 0 1px var(--c,{m['flat']}),0 0 22px -4px var(--c,{m['flat']})}}100%{{box-shadow:inset 0 0 0 1px rgba(255,255,255,.045),0 0 0 0 transparent}}}}
.macro-board.macro-b .mb-ig{{display:none}}
/* breadth bar shear + pulsing live dot */
.macro-board .mb-shear{{transform:skewX(-16deg)}}
.macro-board .mb-dot{{animation:mbbp 2s ease-in-out infinite}}
@keyframes mbbp{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.3;transform:scale(.8)}}}}
@media (prefers-reduced-motion:reduce){{.macro-board *{{animation:none !important}}}}
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

# ── Sector & Industry heat grid (/sentiment/sectors) helpers ─────────────────
# Page-scoped, and deliberately the *lightest* of these blocks: the grid needs no
# ``ui.add_css`` escape-hatch at all. Everything it draws — the fractional column
# tracks, the flush tiles, the truncation, the scroll wrapper — is a Tailwind
# utility, so this file contributes only the chrome palette and the two faces.
# The heat ramp itself lives in ``pages/sector_heat.py``: it is a data-driven
# cell map, the category CLAUDE.md excludes from the config-driven palette.
def build_sector_tokens(theme):
    """Tailwind class-string vocabulary for the sector heat grid.

    Namespaced ``SC_*`` so a sector token can never be mistaken for one of the
    app-wide dark-navy tokens — this page keeps its own near-black ground."""
    s = theme["sectors"]
    return {
        "SC_SANS": "font-['Instrument_Sans',system-ui,sans-serif]",
        "SC_MONO": "font-['JetBrains_Mono',ui-monospace,monospace]",
        "SC_VOID_BG": f"bg-[{s['void']}]",
        "SC_TXT": f"text-[{s['txt']}]",
        "SC_DIM": f"text-[{s['dim']}]",
        "SC_FAINT": f"text-[{s['faint']}]",
        "SC_UP": f"text-[{s['up']}]",
        "SC_DN": f"text-[{s['dn']}]",
        "SC_WARN": f"text-[{s['warn']}]",
        "SC_WARN_BG": f"bg-[{s['warn']}]",
        "SC_UP_BG": f"bg-[{s['up']}]",
        "SC_DN_BG": f"bg-[{s['dn']}]",
        "SC_DIM_BG": f"bg-[{s['dim']}]",
        "SC_EDGE": f"border-[{s['edge']}]",
        "SC_EDGE_HI": f"border-[{s['edge_hi']}]",
    }


def build_rotation_tokens(theme):
    """Tailwind class-string vocabulary for the Sector Rotation board.

    Namespaced ``RT_*``. Only the two grounds and the two faces live here — the
    warm-neutral ladder and the quadrant hues are derived in
    ``pages/rotation_view.py``, so this stays the restyle surface and nothing
    else."""
    r = theme["rotation"]
    return {
        "RT_SANS": "font-['Instrument_Sans',system-ui,sans-serif]",
        "RT_MONO": "font-['JetBrains_Mono',ui-monospace,monospace]",
        "RT_VOID_BG": f"bg-[{r['void']}]",
        "RT_PANEL_BG": f"bg-[{r['panel']}]",
    }


def build_rotation_font_head_html(theme):
    """``<link>``s for the rotation board's two faces, or "" when unset.

    Same pair as the Sector & Industry grid — the two screens are one design
    family — but declared per page so either can be restyled without silently
    changing the other."""
    try:
        url = str(theme["rotation"].get("font_url", "")).strip()
    except Exception:  # noqa: BLE001
        return ""
    if not url:
        return ""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{url}">'
    )


def build_sector_font_head_html(theme):
    """``<link>``s for the grid's two faces (``[sectors].font_url``), or "".

    Instrument Sans for names, JetBrains Mono for every figure — the mono face
    is what makes ``tabular-nums`` align digits optically down a column, so the
    system fallback is a visible downgrade rather than a neutral one."""
    try:
        url = str(theme["sectors"].get("font_url", "")).strip()
    except Exception:  # noqa: BLE001
        return ""
    if not url:
        return ""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{url}">'
    )


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

# ── Market Regime Console (/sentiment only) ─────────────────────────────────
# Namespaced CONSOLE_*/CON_* so a console token can never be mistaken for one of
# the app-wide dark-navy tokens above — the two palettes coexist deliberately.
_CONSOLE_TOKENS = build_console_tokens(THEME)
CONSOLE_PAGE = _CONSOLE_TOKENS["CONSOLE_PAGE"]
CONSOLE_CARD = _CONSOLE_TOKENS["CONSOLE_CARD"]
CONSOLE_CELL = _CONSOLE_TOKENS["CONSOLE_CELL"]
CONSOLE_HAIRLINE = _CONSOLE_TOKENS["CONSOLE_HAIRLINE"]
CONSOLE_TRACK = _CONSOLE_TOKENS["CONSOLE_TRACK"]
CONSOLE_RULE = _CONSOLE_TOKENS["CONSOLE_RULE"]
CONSOLE_DIVIDER = _CONSOLE_TOKENS["CONSOLE_DIVIDER"]
CONSOLE_DISPLAY = _CONSOLE_TOKENS["CONSOLE_DISPLAY"]
CON_TXT = _CONSOLE_TOKENS["CON_TXT"]
CON_TXT_SECONDARY = _CONSOLE_TOKENS["CON_TXT_SECONDARY"]
CON_TXT_MUTED = _CONSOLE_TOKENS["CON_TXT_MUTED"]
CON_TXT_LABEL = _CONSOLE_TOKENS["CON_TXT_LABEL"]
CON_TXT_DIM = _CONSOLE_TOKENS["CON_TXT_DIM"]
CON_TXT_FAINT = _CONSOLE_TOKENS["CON_TXT_FAINT"]
CON_ACCENT = _CONSOLE_TOKENS["CON_ACCENT"]
CON_POS = _CONSOLE_TOKENS["CON_POS"]
CON_NEG = _CONSOLE_TOKENS["CON_NEG"]
CON_WARN = _CONSOLE_TOKENS["CON_WARN"]
CONSOLE_COLORS = console_colors(THEME)         # raw hexes for the SVG builders
CONSOLE_FONT_HEAD_HTML = build_console_font_head_html(THEME)  # "" when no url

# ── Options Flow panels (/options/gamma Flow + Net Prem) ────────────────────
FLOW_COLORS = flow_colors(THEME)               # raw hexes for the SVG builders

# ── Macro Board (/market) page-scoped exports ────────────────────────────────
MACRO_COLORS = macro_colors(THEME)             # raw hexes + sat_ceiling
MACRO_TOKENS = build_macro_tokens(THEME)       # Tailwind class-string vocabulary
MACRO_CSS = build_macro_css(THEME)             # the ONE ui.add_css escape-hatch
MACRO_FONT_HEAD_HTML = build_macro_font_head_html(THEME)  # "" when no url

# ── Sector & Industry heat grid (/sentiment/sectors) page-scoped exports ─────
SECTOR_TOKENS = build_sector_tokens(THEME)     # Tailwind class-string vocabulary
SECTOR_FONT_HEAD_HTML = build_sector_font_head_html(THEME)  # "" when no url

# ── Sector Rotation board (/sentiment/rotation) page-scoped exports ──────────
ROTATION_TOKENS = build_rotation_tokens(THEME)
ROTATION_FONT_HEAD_HTML = build_rotation_font_head_html(THEME)  # "" when no url
