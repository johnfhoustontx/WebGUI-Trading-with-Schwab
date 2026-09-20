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

Apply to a ``.calc-v2`` page (a NEW page builds from ``pages/ui_kit.py`` instead,
and needs no scope class for boxed fields)::

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
boxed=True)``), ``TXT_*`` (semantic state text colors), ``BTN_QUIET`` (text-only),
``BTN_3D*`` (legacy aliases), ``TILE_3D`` (metric tiles). The CSS-only hooks
``QUASAR_INTERNAL_CSS`` styles: ``.calc-v2`` (scope), ``.strat-menu-navy`` (the
teleported Strategy popup — GLOBAL, mounts on ``<body>`` outside ``.calc-v2``),
``.leg-head`` / ``.leg-row`` / ``.leg-strike`` (leg-table chrome). The **full
palette reference** lives in ``config/theme.toml`` (every knob, commented) and
the root ``CLAUDE.md`` "App theme — dark-navy 'dashboard'" section. Since
2026-09-19 pages build their controls through ``pages/ui_kit.py``, and the
boxed-field rules also ship app-wide as ``APP_FIELD_CSS`` under ``.ns-app``.
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
        # The solid danger fill (Stop all services, a destructive confirm). Was
        # [buttons_3d].red_mid - the one key of that retired section anything read.
        "danger": "#d33f3f",
    },
    "semantic": {
        # positive / caution / negative / neutral state colors (labels, tiles)
        "positive": "#66bb6a", "warning": "#ffa726",
        "negative": "#ef5350", "neutral": "#bdbdbd",
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
        # Flat since 2026-09-07: the wordmark carries one accent, not the
        # old artwork's gold/blue split. These MUST track config/theme.toml
        # [brand] — they are what a missing or malformed file falls back to,
        # and a fallback that restores a retired brand is worse than a crash
        # because nothing looks wrong.
        # (p50→p95 of each wordmark band; lower percentiles are anti-aliasing
        # against the black background and read too dark).
        "name_a": "Neural",       # first half of the wordmark (gold)
        "name_b": "Strike",       # second half (blue)
        "font_family": "Montserrat",   # "" = use the app font for the wordmark
        # Brand web font, loaded separately from [typography].font_url.
        "font_url": ("https://fonts.googleapis.com/css2"
                     "?family=Montserrat:wght@800&display=swap"),
        "font_weight": "800",
        "a_from": "#eef1f6",      # "Neural" — the title tone, flat
        "a_to": "#eef1f6",
        "b_from": "#6b86ff",      # "Strike" — the menu accent, flat
        "b_to": "#6b86ff",
        "tracking": ".14em",      # uppercase wordmark: capitals need the air
        "mark": "/static/img/neuralstrike-mark.svg",  # "" = no logo, glyph tile
    },
    "menu": {
        # Application menu (header bar + left nav drawer). Every knob defaults
        # "" = keep the stock Quasar look; a value emits an override.
        # accent → ui.colors(primary) (switches, sliders, color=primary
        # buttons) AND the active nav pill / tab fill / icon accent, which
        # build_nav_css re-emits in it. NOT the header (header_bg).
        "accent": "",
        "header_bg": "",  # top header bar background (decoupled from accent)
        "drawer_bg": "",  # menu panel background
        "text": "",       # menu item text + icons
        "hover_bg": "",   # menu item hover wash
        "title": "",      # the drawer caption ("WORKSPACE")
    },
    # ── The Market Regime Console DATA colours (/sentiment, /desk, /symbol) ──
    # ⚠ DATA ONLY since 2026-09-19. This was a whole page language — near-black
    # ground, gradient cards, square corners, one grey `line` at six opacities,
    # a six-step text ramp and a condensed display face — and all of it retired
    # with the consistency standard: those screens wear the app's own surface
    # now. What is left is the half that encodes a READING, which is why the
    # section survives at all.
    #
    # Colors are HEX ONLY, like every other section.
    #
    # NOT surfaced in Settings → Appearance, for the same reason [brand] is not:
    # that editor's sections are single-kind, and this one mixes a semantic set
    # with a regime lookup.
    "console": {
        "accent": "#22e3d3",         # the dial arc; the one reading-bearing hue
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
        # ⚠ DATA ONLY since 2026-09-19 (the consistency standard). The board's
        # own panel/grid/edge grounds, its dim/faint text steps, its radial
        # wash stop and its three Google faces went with the page's surface.
        # ``void`` and ``txt`` survive as REFERENCES rather than as paint: the
        # legibility guard composites the tile heat over ``void`` and reads
        # ``txt`` as the price step, which is how it proves the Skin-B ramp
        # still clears 4.5:1 on the hottest tile.
        "void": "#03060D", "tile": "#0A1020",
        "txt": "#DCE8F8",
        # Skin-B (Heat Lattice) text ramp. The tile's heat fill reaches
        # rgba(0,229,160,.36) over the void, i.e. ~#015642 -- a background bright
        # enough that the dark `dim`/`faint` ramp above collapses onto it: the
        # skew line ("Call 31%") measured 1.08:1 live and the symbol 2.2:1, both
        # effectively invisible. These two lift only the lattice skin (Skin A's
        # tiles stay dark, so its ramp is unchanged) and keep the reading order
        # price > symbol > descriptor while clearing 4.5:1 on the hottest tile.
        "lattice_sym": "#C6D6EA", "lattice_desc": "#A9BFDA",
        "up": "#00E5A0", "dn": "#FF4D6D", "flat": "#5C6F8C", "cyan": "#35E0FF",
        "sat_ceiling": 0.45,
    },
    # ⚠ DATA ONLY since 2026-09-19, same sweep: the grid's near-black ground,
    # its two border steps, its three-step grey ramp and its two Google faces
    # went with the page's surface. The heat ramp was never here — it is the
    # oklch cell map in ``pages/sector_heat.py``, which is data and untouched.
    "sectors": {
        "up": "#3FD98A", "dn": "#E8697B", "warn": "#E0A63C",
    },
    # The Options Strategy Calculator's four SIGNAL colours — NOT surfaced in
    # Settings → Appearance, and not the app palette.
    # ⚠ DATA ONLY since 2026-09-20: this was a whole page-scoped surface
    # language (a near-black ground and glow, frame / tile / chip / button skins,
    # a seven-step text ramp and a Google mono face) behind a `.calc-v3` scope
    # hook. It retired with the page's migration onto pages/ui_kit.py. Each knob
    # left encodes a READING — profit, loss, the primary signal, caution — that
    # no app-wide token carries.
    "calc": {
        "pos": "#2dd4a7", "neg": "#fb5f7c", "accent": "#22d3ee", "warn": "#f5b841",
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
        from shared import config_toml
        data = config_toml.read_layered(path)   # tracked file + local override
        for sec, vals in data.items():
            if sec in merged and isinstance(vals, dict):
                for k, v in vals.items():
                    if k in merged[sec] and isinstance(v, str) and v.strip():
                        merged[sec][k] = v.strip()
        # [buttons_3d] retired 2026-09-19. A file that still carries its one live
        # key and no [palette].danger keeps that colour rather than silently
        # falling back to the default red. The operator's override file is
        # checked FIRST, then the MERGED view (tracked + override) - not the
        # tracked file alone. Override first because the tracked file ships a
        # danger, so the merged view always has one and would hide a red_mid an
        # older override still holds.
        def _set(v):
            return isinstance(v, str) and v.strip()
        for layer in (config_toml.read_overrides(path), data):
            own = (layer.get("palette") or {}).get("danger")
            legacy = (layer.get("buttons_3d") or {}).get("red_mid")
            if _set(own):
                break                       # this layer names its danger itself
            if _set(legacy):
                merged["palette"]["danger"] = legacy.strip()
                break
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
    p, s = theme["palette"], theme["semantic"]
    state_txt = [f"text-[{s[k]}]" for k in ("positive", "warning", "negative", "neutral")]
    pr = hex_rgb(p["primary"], (107, 134, 255))       # primary glow rgb
    dr = hex_rgb(p["danger"], (211, 63, 63))          # solid-danger glow rgb
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
            f"bg-[{p['danger']}] hover:brightness-110 text-white "
            "rounded-[9px] min-h-[40px] font-semibold "
            f"shadow-[0_4px_14px_-4px_rgba({dr[0]},{dr[1]},{dr[2]},0.6)]"
        ),
        # Quiet: text only, for a small link beside a control ("Why no trade?",
        # "Change") that must not read as the page's action.
        "BTN_QUIET": (f"bg-transparent text-[{p['muted']}] hover:text-[{p['title']}] "
                      "rounded-[9px] min-h-[34px] px-2 font-medium"),
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


def build_quasar_css(theme, scope=".calc-v2"):
    """The Quasar-internal / teleported escape-hatch CSS from a theme dict.

    These rules style the Quasar-internal DOM that component ``.classes()``
    strings can't reach: the boxed q-field control (incl. the leg-table
    variants), the q-tab chrome, and the body-mounted ``.strat-menu-navy``
    popup. Scoped under ``scope`` (``.calc-v2`` for the pages that still wrap
    themselves; ``.ns-app`` app-wide)."""
    p = theme["palette"]
    focus_rgb = hex_rgb(p["focus"], (59, 130, 246))
    return f"""
/* Boxed dark inputs — restyle the standard q-field control into a filled box. */
{scope} .q-field__control{{
  background:{p['input_bg']};border:1px solid {p['input_border']};border-radius:8px;padding:0 10px;min-height:40px;
}}
{scope} .q-field__control:before,{scope} .q-field__control:after{{border:0!important;}}
{scope} .q-field--focused .q-field__control{{
  border-color:{p['focus']};box-shadow:0 0 0 2px rgba({focus_rgb[0]},{focus_rgb[1]},{focus_rgb[2]},.28);
}}
/* Quasar's `borderless` is the opt-out: a field drawn inside a frame of its own
   (the Trade Signal Desk's symbol pill) gets no second box. AFTER the focused
   rule, at the same specificity, so a focused borderless field stays clear. */
{scope} .q-field--borderless .q-field__control{{background:transparent;border:0;padding:0;box-shadow:none;}}
{scope} .q-field__label{{color:{p['muted']};}}
{scope} .q-field__native,{scope} .q-field__native input,
{scope} .q-field__native textarea,{scope} .q-field__native span{{color:{p['input_text']}!important;}}
{scope} .q-field__append .q-icon,{scope} .q-field__prepend .q-icon{{color:{p['icon']};}}
/* Strategy menu button internals — the q-btn__content layout + icon color are
   Quasar-internal (component .classes() can't reach them), so they must survive
   when a later phase swaps the button BOX style to the STRATEGY_BTN token. The
   base box rule (bg/border/radius/min-height) is intentionally NOT here. */
{scope} .strategy-menu-btn .q-btn__content{{justify-content:space-between;flex:1;text-transform:none;}}
{scope} .strategy-menu-btn .q-icon{{color:{p['icon']};}}
/* Leg table header row */
{scope} .leg-head{{color:{p['muted']};font-size:12px;padding:0 2px 4px;}}
/* Leg table rows — compact cells (less top/bottom padding, shorter height) and
   tighter side padding so "call"/"put" are not horizontally clipped. */
{scope} .leg-row .q-field__control{{min-height:32px;padding:0 6px;}}
{scope} .leg-row .q-field__control .q-field__native,
{scope} .leg-row .q-field__marginal{{min-height:32px;padding-top:0;padding-bottom:0;}}
{scope} .leg-row .q-field__append{{padding-left:0;}}
{scope} .leg-row .q-field__native{{font-size:13px;}}
/* Leg table rows — dropdowns in narrow grid tracks: slim side padding and a
   smaller dropdown arrow, so "Sep 14", "570" and "Mark" fit. Sizes only. */
{scope} .leg-trow .q-field__control{{padding:0 4px;}}
{scope} .leg-trow .q-field__append{{padding-left:0;}}
{scope} .leg-trow .q-field__append .q-icon{{font-size:12px;}}
/* Centered strike value in the leg table. */
{scope} .leg-strike .q-field__native{{justify-content:center;text-align:center;}}
/* ...and 11px text in the row's fields, AFTER the strike rule so it wins the
   tie (both are one class plus an element, so source order decides), because
   "Mark" and a four-digit strike do not fit their tracks at the default size.
   App-wide since 2026-09-20: the Calculator, the Simulator and Rescue all
   mount these narrow tracks, so one rule serves all three. */
{scope} .leg-trow .q-field__native,{scope} .leg-trow .q-field__native input,
{scope} .leg-trow .q-field__native span{{font-size:11px;}}
/* Tabs (Simulator) — light labels, accent indicator, transparent panels so the
   dark-transparent Highcharts panels sit on the page gradient. */
{scope} .q-tabs{{color:{p['icon']};}}
{scope} .q-tab__label{{font-weight:500;}}
{scope} .q-tab--active{{color:{p['input_text']};}}
{scope} .q-tab__indicator{{background:{p['focus']};}}
{scope} .q-tab-panels,{scope} .q-tab-panel,{scope} .q-panel{{background:transparent!important;}}
/* Cascading Strategy menu popup — teleported to <body>, so NOT under {scope}.
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


def build_surface_css(theme):
    """The app-wide SURFACE, painted once for every page by both entrypoints.

    Raw CSS for what no page's ``.classes()`` reaches: the ``<body>`` ground
    (Quasar's flat #121212 until 2026-09-19 - what a page with no wrapper sat
    on), the default ``q-card`` frame, and the selected-row accent that
    ``ui_kit.table`` stamps. Every rule yields to a page's own look: each card
    rule skips an element carrying its own ``border`` / ``rounded`` /
    ``shadow``-or-``ring`` class, and a page that paints its own ground simply
    covers the body's."""
    p = theme["palette"]
    r, g, b = hex_rgb(p["focus"], (107, 134, 255))
    return (
        f"body.body--dark{{background:radial-gradient(130% 90% at 50% -20%,"
        f"{p['page_bg1']} 0%,{p['page_bg2']} 55%,{p['page_bg3']} 100%) fixed;"
        f"color:{p['text']};}}\n"
        f'.ns-app .q-card--dark:not([class*="border"]){{border:1px solid '
        f"{p['card_border']};}}\n"
        f'.ns-app .q-card--dark:not([class*="rounded"]){{border-radius:12px;}}\n'
        f'.ns-app .q-card--dark:not([class*="shadow"]):not([class*="ring"])'
        f"{{box-shadow:none;}}\n"
        f".ns-app .kit-row-selected > td{{background:rgba({r},{g},{b},.08);}}\n"
        f".ns-app .kit-row-selected > td:first-child{{box-shadow:inset 3px 0 0 "
        f"{p['focus']};}}\n"
    )


def build_quasar_colors(theme):
    """``ui.colors(**...)`` for both entrypoints.

    ``dark`` is Quasar's own card / menu / dark-table fill (#1d1d1d stock) and
    ``dark_page`` its page ground (#121212 stock); pointing them at the palette
    makes every default Quasar surface the app's card instead of a grey one.
    ``primary`` is ``[menu].accent`` when set, exactly as before."""
    p = theme["palette"]
    out = {"dark": p["card_bg"], "dark_page": p["page_bg2"]}
    accent = str((theme.get("menu") or {}).get("accent") or "").strip()
    if accent:
        out["primary"] = accent
    return out


def save_theme_values(updates, path=None):
    """Save ``updates`` (``{section: {key: value}}``) as the operator's theme.

    Written to the OVERRIDE file ``config/local/theme.toml``, never to the
    tracked ``config/theme.toml``: editing a tracked file dirties the prod
    checkout, and ``tools/promote.sh`` refuses a dirty tree, so the first saved
    colour would have blocked every later promote (2026-09-19). Only values that
    differ from the tracked file are kept, so choosing a shipped value again
    removes its override. Returns the merged theme re-loaded from disk; the
    running app picks it up on the next web GUI restart (the theme loads once)."""
    from shared import config_toml
    if path is None:
        from repo_paths import THEME_TOML
        path = THEME_TOML
    try:
        with open(path, "rb") as f:
            shipped = tomllib.load(f)
    except Exception:  # noqa: BLE001 - no tracked file: every value is an override
        shipped = {}
    current = config_toml.read_overrides(path)
    for sec, vals in (updates or {}).items():
        for key, val in vals.items():
            base = (shipped.get(sec) or {}).get(key, _DEFAULTS.get(sec, {}).get(key))
            val = str(val)
            if val == base:
                current.get(sec, {}).pop(key, None)
            else:
                current.setdefault(sec, {})[key] = val
        if sec in current and not current[sec]:
            current.pop(sec)
    # An explicit danger supersedes the retired [buttons_3d].red_mid. Drop the dead
    # section, or choosing the shipped red (which removes the danger override)
    # would let that stale colour take over again.
    if "danger" in ((updates or {}).get("palette") or {}):
        current.pop("buttons_3d", None)
    config_toml.write_overrides(path, current)
    return load_theme(path)


def reset_theme(path=None):
    """Drop every appearance override: back to the shipped ``config/theme.toml``."""
    from shared import config_toml
    if path is None:
        from repo_paths import THEME_TOML
        path = THEME_TOML
    config_toml.write_overrides(path, {})
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
    # Tracking is CONFIG, like every other property of this lockup. It was the
    # one value hardcoded here (at .01em, i.e. none), which is why the app's
    # wordmark sat tight while the public site's ran wide — the two surfaces
    # drifted on the one axis nobody could reach without editing this function.
    #
    # ⚠ An uppercase wordmark needs tracking; it is not a refinement. Capitals
    # are drawn to sit in lowercase words, so set solid they read as cramped.
    # Anything at or near 0 here undoes the `text-transform: uppercase` above.
    track = str(b.get("tracking", _DEFAULTS["brand"]["tracking"])).strip() \
        or _DEFAULTS["brand"]["tracking"]
    return f"""
.brand-word {{
  font-family: {stack};
  font-weight: {weight};
  font-size: 16px;
  letter-spacing: {track};
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
/* The logo mark. The artwork is a TRANSPARENT SVG since 2026-09-07, so it sits
   directly on whatever the header paints — no plate, no gradient.
   `border-radius` and `object-fit: cover` are inherited from the old raster
   lockup and now do nothing: a 64-unit square viewBox fits a 44px box exactly,
   so there is nothing to crop, and there is no ground to round off. They are
   left in place because a future mark may be a raster again. */
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
# ⚠ NOT read by any Tier-1 page since 2026-09-19: the surface tokens these
# alphas dressed retired with the consistency standard. It survives as the
# stated mirror source for ``services/options_svc/market_console.py``, the
# SEPARATE renderer behind the pushed snapshot image, which still draws the
# console look and carries its own copy of this table.
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
    """The console's four CHROMATIC text classes.

    ⚠ This used to carry the whole console page vocabulary — a ground, a
    gradient card, a cell, a hairline, a track, two rules, a condensed display
    face and a six-step neutral text ladder. All of it retired on 2026-09-19
    with the consistency standard: the screens that drew it wear the app's own
    ``CARD`` / ``LABEL`` / ``MUTED`` / palette ``icon`` and card/button borders
    now, because a card, a rule and a text step are SURFACE wherever they live.
    What survives is the half that encodes a reading."""
    c = theme["console"]
    return {
        "CON_ACCENT": f"text-[{c['accent']}]",
        "CON_POS": f"text-[{c['positive']}]",
        "CON_NEG": f"text-[{c['negative']}]",
        "CON_WARN": f"text-[{c['warning']}]",
    }


def console_colors(theme):
    """The console's raw DATA hexes, for the SVG builders (which take
    attributes, not classes), the chip maps and the per-regime lookups.

    ⚠ The neutrals it also used to expose — ``line``, ``cell``, ``text``,
    ``muted``, ``label``, ``dim`` — went with the surface vocabulary in 2026-09.
    Anything here is a reading; a groove, a hairline or a text step comes off
    ``THEME["palette"]``."""
    c = theme["console"]
    return {
        "accent": c["accent"],
        "positive": c["positive"], "negative": c["negative"],
        "warning": c["warning"], "olive": c["olive"], "yellow": c["yellow"],
        "regimes": {k: c[f"regime_{k}"] for k in
                    ("mean_reversion", "trending", "breakout", "choppy",
                     "crisis")},
        "regime_zero": c["regime_breakout_zero"],
    }


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
    """The macro board's raw DATA hexes + the saturation ceiling, for the
    page's computed values (heat/wash alphas, direction colours, breadth bar).

    ⚠ The surface half — ``panel``, ``grid``, ``edge``, ``edge_hi``, ``dim``,
    ``faint``, ``wash_in`` — went on 2026-09-19 with the page's own ground and
    faces (the consistency standard). ``void`` survives as what the tile heat
    composites OVER when the legibility ramp is measured."""
    m = theme["macro"]
    out = {k: m[k] for k in ("void", "up", "dn", "flat", "cyan")}
    try:
        out["sat_ceiling"] = float(m.get("sat_ceiling", 0.45) or 0.45)
    except (TypeError, ValueError):
        out["sat_ceiling"] = 0.45
    return out


def build_macro_tokens(theme):
    """The macro board's three DIRECTION classes.

    ⚠ This carried the board's whole instrument language until 2026-09-19 —
    three Google faces, a text ramp, a panel/tile/rail ground and two border
    steps. All of it retired with the consistency standard; the page wears the
    app surface and the app font, and what is left is the colour that says
    which way a tile moved."""
    m = theme["macro"]
    return {
        "MB_UP": f"text-[{m['up']}]",
        "MB_DN": f"text-[{m['dn']}]",
        "MB_FLAT": f"text-[{m['flat']}]",
    }


def build_macro_css(theme):
    """The macro board's ONE ``ui.add_css`` escape-hatch block, scoped under
    ``.macro-board``.

    **SPLIT on 2026-09-19** when the page moved onto the kit: what is left is
    the DATA EFFECTS and nothing else — the flash keyframes (ignition bar, price
    flare and the Skin-B bloom), the per-tile custom-prop wash and heat fill,
    the lattice's legibility ramp, and the reduced-motion opt-out. The page
    ground, the rail's face, the clip-path notches and the left accent bar were
    this page's own surface and went with the rest of it; the app surface
    carries the ground now.

    ⚠ **``.mb-tile``'s ``position:relative`` / ``overflow:hidden`` is NOT the
    notch it used to share a rule with.** ``.mb-ig`` is ``position:absolute``,
    so deleting that rule wholesale as "the notch" would position the ignition
    bar against the PAGE rather than the tile, and stop the flare's glow being
    clipped to it.

    ⚠ **``@keyframes mbpx`` ends on the app's title colour, and must.** It
    declares no ``animation-fill-mode``, so at 100% the label reverts to its
    class colour — the terminus and ``mb-px``'s resting class have to name the
    same one, or the flare finishes with a one-frame snap.

    Two rules are here rather than in ``.classes()`` and both are deliberate:
    the Skin-B text ramp and the lattice's ground-less frame are scoped on the
    WRAPPER's skin class, and ``_set_skin`` swaps that class without rebuilding
    the 69 tiles -- so a Tailwind class on the child cannot express "only when
    the lattice is on" without repainting every tile on every toggle. The
    transparent frame is the lattice's identity rather than chrome: a
    continuous field of tiles is what "Heat Lattice" means, and ``/macro`` is
    published pinned to that skin (``live_screens.SCREENS``)."""
    m, p = theme["macro"], theme["palette"]
    return f"""
/* the tile is the ignition bar's containing block, and clips the flare */
.macro-board .mb-tile{{position:relative;overflow:hidden}}
/* ignition bar + price flare — fire ONLY when .fl is (re)applied on change */
.macro-board .mb-ig{{position:absolute;left:0;top:0;height:2px;width:100%;
  transform:scaleX(0);transform-origin:left;opacity:0;background:var(--c,{m['flat']})}}
.macro-board .mb-tile.fl .mb-ig{{animation:mbig .95s cubic-bezier(.2,.7,.3,1)}}
@keyframes mbig{{0%{{transform:scaleX(0);opacity:1}}30%{{transform:scaleX(1);opacity:1}}100%{{transform:scaleX(1);opacity:0}}}}
.macro-board .mb-tile.fl .mb-px{{animation:mbpx .95s ease-out}}
@keyframes mbpx{{0%{{color:var(--c,{m['flat']});text-shadow:0 0 14px var(--c,{m['flat']})}}100%{{color:{p['title']};text-shadow:none}}}}
/* Skin A — Instrument: subtle magnitude-scaled wash over the tile fill */
.macro-board.macro-a .mb-tile{{background-image:linear-gradient(160deg,var(--wash,transparent),transparent 62%)}}
/* Skin B — Heat Lattice: no frame ground, continuous heat fill, bloom on change */
.macro-board.macro-b .mb-panel{{background:transparent !important;border-color:transparent !important}}
.macro-board.macro-b .mb-tile{{background:var(--heat,{m['tile']}) !important;box-shadow:inset 0 0 0 1px rgba(255,255,255,.045)}}
.macro-board.macro-b .mb-tile.fl{{animation:mblat .95s ease-out}}
@keyframes mblat{{0%{{box-shadow:inset 0 0 0 1px var(--c,{m['flat']}),0 0 22px -4px var(--c,{m['flat']})}}100%{{box-shadow:inset 0 0 0 1px rgba(255,255,255,.045),0 0 0 0 transparent}}}}
.macro-board.macro-b .mb-ig{{display:none}}
/* Skin B text ramp -- the heat fill is bright enough to swallow the dark
   dim/faint ramp, so lift the symbol + descriptor here only. */
.macro-board.macro-b .mb-tile .mb-sym{{color:{m['lattice_sym']}}}
.macro-board.macro-b .mb-tile .mb-desc{{color:{m['lattice_desc']}}}
@media (prefers-reduced-motion:reduce){{.macro-board *{{animation:none !important}}}}
"""


def build_nav_css(theme):
    """Application-menu override CSS from ``[menu]`` (drawer bg / text / hover /
    caption). Emits a rule ONLY for a non-empty knob, so all-default config
    produces an empty string and the stock Quasar look can never drift. The
    ``accent`` knob feeds ``ui.colors(primary=…)`` (via ``build_quasar_colors``)
    AND, since 2026-09-19, the active nav pill, tab-strip fill and active icon
    below. Injected app-wide by ``main._layout``."""
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
    a = str(m.get("accent") or "").strip()
    if a:
        # The ONE accent reaches the active pill, the tab-strip fill and the
        # active icon too. Raw CSS may carry any value; the JIT limit that once
        # kept these hard-coded applies to Tailwind classes only. The browser
        # mixes the wash (color-mix), so ANY CSS colour works - #f80, a name,
        # rgba(...) - where parsing a 6-digit hex here fell back to the stock
        # blue for the pill and tab while the icon followed the value.
        # No !important on the two washes: their _NAV_CSS rivals are normal
        # declarations injected EARLIER, so these win on order. The icon needs
        # it to beat the [menu].text rule's !important above.
        rules += [
            f".nav-drawer .nav-active{{background:color-mix(in srgb,{a} 13%,transparent);}}",
            f".nav-drawer .nav-active .nav-icon{{color:{a}!important;}}",
            f".compact-tabs .q-tab--active{{background:color-mix(in srgb,{a} 16%,transparent);}}",
        ]
    return "\n".join(rules)

# ── Sector & Industry heat grid (/sentiment/sectors) helpers ─────────────────
# Page-scoped, and deliberately the *lightest* of these blocks: the grid needs no
# ``ui.add_css`` escape-hatch at all. Everything it draws — the fractional column
# tracks, the flush tiles, the truncation, the scroll wrapper — is a Tailwind
# utility, so this file contributes only the chrome palette and the two faces.
# The heat ramp itself lives in ``pages/sector_heat.py``: it is a data-driven
# cell map, the category CLAUDE.md excludes from the config-driven palette.
def build_sector_tokens(theme):
    """The heat grid's regime TONE, as text and as a dot.

    ⚠ Namespaced ``SC_*`` because this page used to keep its own near-black
    ground, two Google faces and a three-step grey ramp. Those retired on
    2026-09-19 with the consistency standard — a ground, a face and a text step
    are surface wherever they live — and the heat ramp itself was never here
    (it is the oklch cell map in ``pages/sector_heat.py``). What is left is the
    regime word's colour and the dot beside it."""
    s = theme["sectors"]
    return {
        "SC_UP": f"text-[{s['up']}]",
        "SC_DN": f"text-[{s['dn']}]",
        "SC_WARN": f"text-[{s['warn']}]",
        "SC_WARN_BG": f"bg-[{s['warn']}]",
        "SC_UP_BG": f"bg-[{s['up']}]",
        "SC_DN_BG": f"bg-[{s['dn']}]",
    }


# ── Options Strategy Calculator (/options/calculator) DATA exports ────────
# ⚠ This WAS a page-scoped surface language on the scale of [console] or
# [macro] — a near-black ground, a mono face, frame / tile / chip / button skins
# and an ``ui.add_css`` escape-hatch block scoped ``.calc-v3``. It retired on
# 2026-09-20 when the Calculator moved onto ``pages/ui_kit.py``: every rule that
# block carried (boxed q-fields, the Strategy trigger internals, the leg-table
# track sizes) exists app-wide under ``.ns-app`` in ``build_quasar_css``, and the
# teleported popup takes the shared ``.strat-menu-navy`` skin.
#
# What is left is DATA. The four hues below encode a READING — profit, loss, the
# primary signal, caution — on the six metric cards, the legs strip and the
# STRATEGY tag chips, and no app-wide token carries the cyan ``accent``.
def build_calc_tokens(theme):
    """The Calculator's four signal colours, as Tailwind text + left-edge classes.

    Namespaced ``CALC_*`` so a calculator colour can never be mistaken for the
    app-wide semantic set: these are the page's own five-tone accent vocabulary
    (the fifth, "dim", is the app's MUTED — a card with no reading makes no
    colour claim). ``CALC_STATE_TEXT`` is the whole set as ONE class string, for
    ``.classes(remove=CALC_STATE_TEXT, add=CALC_POS)`` — so repeated repaints
    can't stack conflicting ``text-[…]`` classes. It is derived here rather than
    written down so it always follows the config, exactly as its app-wide sibling
    ``STATE_TEXT_CLASSES`` does.
    """
    c = theme["calc"]
    state_txt = [f"text-[{c[k]}]" for k in ("pos", "neg", "accent", "warn")]
    return {
        "CALC_POS": state_txt[0],
        "CALC_NEG": state_txt[1],
        "CALC_ACCENT": state_txt[2],
        "CALC_WARN": state_txt[3],
        "CALC_STATE_TEXT": " ".join(state_txt),
        "CALC_EDGE_POS": f"border-l-2 border-l-[{c['pos']}]",
        "CALC_EDGE_NEG": f"border-l-2 border-l-[{c['neg']}]",
        "CALC_EDGE_ACCENT": f"border-l-2 border-l-[{c['accent']}]",
        "CALC_EDGE_WARN": f"border-l-2 border-l-[{c['warn']}]",
    }


def build_matrix_tokens(theme):
    """The P&L matrix's CHROME, as raw CSS colours rather than classes.

    The matrix is ONE raw ``ui.html`` fragment — a few hundred cells built with
    ``.classes()`` would be a few hundred Vue elements — which is the repo's
    documented out-of-scope case for the Tailwind-first rule, so these enter as
    inline ``style=`` VALUES and cannot be tokens.

    Frame, not data: the sticky header's ground and the rule under it, the rules
    between price rows, a heading that is not the expiry, the price ladder, the
    ground behind an untinted cell, and the text of a cell with no reading. They
    were near-black literals mirroring the retired ``[calc]`` surface keys; on
    the app's navy card those would read as a hole punched in the page. The
    profit / loss ramp and the spot amber stay in ``calculator.py``: a
    data-driven colour map is the one category ``config/theme.toml``
    deliberately keeps out of the palette."""
    p = theme["palette"]
    return {
        "MATRIX_HEAD_BG": p["card_bg"],
        "MATRIX_HEAD_RULE": p["card_border"],
        # Fainter than the outer frame, so the ladder reads as rows inside one
        # table rather than as a stack of boxes.
        "MATRIX_ROW_RULE": _alpha_hex(p["card_border"], .55),
        "MATRIX_LABEL_FG": p["muted"],
        "MATRIX_VOID": p["page_bg3"],
        "MATRIX_PRICE_FG": p["title"],
        "MATRIX_EMPTY_FG": p["muted"],
    }


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
BTN_QUIET = _TOKENS["BTN_QUIET"]
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
# The same rules for EVERY page: both entrypoints put ``ns-app`` on their content
# column and inject this, so a page no longer needs a scope class of its own.
APP_FIELD_CSS = build_quasar_css(THEME, scope=".ns-app")
SURFACE_CSS = build_surface_css(THEME)       # injected by BOTH entrypoints
QUASAR_COLORS = build_quasar_colors(THEME)   # ui.colors(**QUASAR_COLORS) in both
TYPOGRAPHY_CSS = build_typography_css(THEME)   # injected app-wide by main._layout
FONT_HEAD_HTML = build_font_head_html(THEME)   # "" when no [typography].font_url
NAV_THEME_CSS = build_nav_css(THEME)           # "" when [menu] is all-default

# ── Brand identity (header lockup) ──────────────────────────────────────────
BRAND_NAME_A = THEME["brand"]["name_a"]        # "Neural" — the gold half
BRAND_NAME_B = THEME["brand"]["name_b"]        # "Strike" — the blue half
BRAND_NAME = f"{BRAND_NAME_A}{BRAND_NAME_B}"   # plain text: browser titles, logs
BRAND_MARK = THEME["brand"]["mark"]            # "" = no logo image
BRAND_CSS = build_brand_css(THEME)
BRAND_FONT_HEAD_HTML = build_brand_font_head_html(THEME)  # "" when no font_url

# ── Market Regime Console (/sentiment, /desk, /symbol) ──────────────────────
# ⚠ WHAT IS LEFT HERE IS DATA. The console's SURFACE vocabulary retired on
# 2026-09-19 with the consistency standard: the page ground, the gradient card,
# the cell, the hairline, the track, the two rules, the condensed display face
# and the whole six-step neutral TEXT ladder are gone, and so is the `<link>`
# that loaded the face. Every screen that drew them now wears the app's own
# `CARD` / `LABEL` / `MUTED` / palette `icon` and card/button borders.
#
# What a console token may still be is a READING: the four chromatic text
# colours below, and the raw hexes the SVG builders and chip maps take.
_CONSOLE_TOKENS = build_console_tokens(THEME)
CON_ACCENT = _CONSOLE_TOKENS["CON_ACCENT"]
CON_POS = _CONSOLE_TOKENS["CON_POS"]
CON_NEG = _CONSOLE_TOKENS["CON_NEG"]
CON_WARN = _CONSOLE_TOKENS["CON_WARN"]
CONSOLE_COLORS = console_colors(THEME)         # raw hexes for the SVG builders

# ── Options Flow panels (/options/gamma Flow + Net Prem) ────────────────────
FLOW_COLORS = flow_colors(THEME)               # raw hexes for the SVG builders

# ── Macro Board (/market) page-scoped DATA exports ───────────────────────────
# ⚠ The board's own three Google faces went on 2026-09-19 with its ground and
# its text ramp: the page wears the app surface and the app font.
MACRO_COLORS = macro_colors(THEME)             # raw hexes + sat_ceiling
MACRO_TOKENS = build_macro_tokens(THEME)       # the three direction classes
MACRO_CSS = build_macro_css(THEME)             # the ONE ui.add_css escape-hatch

# ── Sector & Industry heat grid (/sentiment/sectors) DATA exports ────────────
SECTOR_TOKENS = build_sector_tokens(THEME)     # the regime tone, text + dot

# ── Options Strategy Calculator — the four signal colours it still owns ────
_CALC_TOKENS = build_calc_tokens(THEME)
CALC_POS = _CALC_TOKENS["CALC_POS"]
CALC_NEG = _CALC_TOKENS["CALC_NEG"]
CALC_ACCENT = _CALC_TOKENS["CALC_ACCENT"]
CALC_WARN = _CALC_TOKENS["CALC_WARN"]
CALC_STATE_TEXT = _CALC_TOKENS["CALC_STATE_TEXT"]
CALC_EDGE_POS = _CALC_TOKENS["CALC_EDGE_POS"]
CALC_EDGE_NEG = _CALC_TOKENS["CALC_EDGE_NEG"]
CALC_EDGE_ACCENT = _CALC_TOKENS["CALC_EDGE_ACCENT"]
CALC_EDGE_WARN = _CALC_TOKENS["CALC_EDGE_WARN"]

# ── the P&L matrix's chrome — raw CSS values for a raw-HTML fragment ──────
_MATRIX_TOKENS = build_matrix_tokens(THEME)
MATRIX_HEAD_BG = _MATRIX_TOKENS["MATRIX_HEAD_BG"]
MATRIX_HEAD_RULE = _MATRIX_TOKENS["MATRIX_HEAD_RULE"]
MATRIX_ROW_RULE = _MATRIX_TOKENS["MATRIX_ROW_RULE"]
MATRIX_LABEL_FG = _MATRIX_TOKENS["MATRIX_LABEL_FG"]
MATRIX_VOID = _MATRIX_TOKENS["MATRIX_VOID"]
MATRIX_PRICE_FG = _MATRIX_TOKENS["MATRIX_PRICE_FG"]
MATRIX_EMPTY_FG = _MATRIX_TOKENS["MATRIX_EMPTY_FG"]
