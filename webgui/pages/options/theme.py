"""Shared **dark-navy "dashboard" theme** for the app pages (Tier-1).

The theme is now expressed as **Tailwind design tokens** (reusable ``.classes()``
utility strings encoding the navy palette) plus a slim **``QUASAR_INTERNAL_CSS``**
escape-hatch for the Quasar/Highcharts-internal DOM that component ``.classes()``
strings can't reach (the boxed ``q-field`` control, the leg-table cells, the
``q-tab`` chrome, and the body-mounted ``.strat-menu-navy`` popup), scoped under
the historical ``.calc-v2`` scope hook.

The **Calculator**, **Simulator**, and **Trade** pages inject only
``QUASAR_INTERNAL_CSS`` and apply the tokens — so the look never drifts. (The
class name is historical — ``calc-v2`` — kept stable as the CSS scope hook; the
theme is page-agnostic and promotable app-wide.)

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
buttons). The CSS-only hooks ``QUASAR_INTERNAL_CSS`` styles: ``.calc-v2`` (scope),
``.strat-menu-navy`` (the teleported Strategy popup — GLOBAL, mounts on
``<body>`` outside ``.calc-v2``), ``.leg-head`` / ``.leg-row`` / ``.leg-strike``
(leg-table chrome). The **full palette reference** lives in the root ``CLAUDE.md``
"App theme — dark-navy 'dashboard'" section (canonical).
"""

# ---------------------------------------------------------------------------
# Tailwind design tokens (the dark-navy "dashboard" theme). Each is a reusable
# .classes() utility string encoding the navy palette. Apply with `.classes(CARD)`
# (replaces the old `.classes("calc-card")` + a CSS rule). The CSS-only escape
# hatch lives in QUASAR_INTERNAL_CSS below. Palette source: CLAUDE.md "App theme —
# dark-navy 'dashboard'".
# ---------------------------------------------------------------------------
PAGE = (
    "rounded-[14px] border border-[#1d2942] p-[18px_20px_22px] text-[#cdd8ee] "
    "bg-[radial-gradient(130%_90%_at_50%_-20%,#16243f_0%,#0c1424_55%,#0a0f1c_100%)]"
)
# px-4 py-3.5 = 16px/14px = .calc-card padding (14px 16px) — axis mapping is deliberate.
CARD = "bg-[#101a30] border border-[#213152] rounded-[12px] px-4 py-3.5"
EYEBROW = "text-[#8794b4] text-[12px] tracking-[.02em]"
LABEL = "text-[#eaf0fb]"
MUTED = "text-[#7f8db0]"
BTN = (
    "bg-[#15213b] hover:bg-[#1b2950] text-[#cdd8ee] border border-[#2a3a5c] "
    "rounded-[9px] min-h-[40px] font-medium"
)
BTN_PRIMARY = (
    "bg-[#2563eb] hover:bg-[#1d4fd1] text-white rounded-[9px] min-h-[40px] font-semibold"
)
STRATEGY_BTN = (
    "bg-[#0c1426] hover:border-[#3b82f6] border border-[#243353] text-[#e7edf8] "
    "rounded-[8px] min-h-[40px] font-normal"
)

# Shared 3D gradient buttons (the Scanner "Run scan" + Paper action buttons). Apply
# with color=None so Quasar's bg-primary doesn't compete. Replaces SCAN_CSS .scan-btn
# and PAPER_CSS .pt-btn / .pt-danger (byte-identical gradients).
BTN_3D = (
    "bg-[linear-gradient(180deg,#5aa0e6_0%,#3a7bc0_55%,#316eac_100%)] text-white "
    "rounded-[7px] font-semibold min-h-[34px] "
    "shadow-[0_4px_0_0_#244e78,0_6px_10px_rgba(0,0,0,.4)] "
    "hover:brightness-110 active:translate-y-[4px] "
    "active:shadow-[0_1px_0_0_#244e78,0_2px_4px_rgba(0,0,0,.4)] "
    "transition-[transform,box-shadow,filter] duration-100"
)
BTN_3D_DANGER = (
    "bg-[linear-gradient(180deg,#ef6b6b_0%,#d33f3f_55%,#b53030_100%)] text-white "
    "rounded-[7px] font-semibold min-h-[34px] "
    "shadow-[0_4px_0_0_#7a1f1f,0_6px_10px_rgba(0,0,0,.4)] "
    "hover:brightness-110 active:translate-y-[4px] "
    "active:shadow-[0_1px_0_0_#7a1f1f,0_2px_4px_rgba(0,0,0,.4)] "
    "transition-[transform,box-shadow,filter] duration-100"
)

# Raised "3D" effect for static metric TILES (the app-wide tile standard). ADDITIVE
# only — a top-edge bevel highlight + a hard bottom "lip" + a soft drop shadow, with
# NO background, so it layers over each tile's own bg (incl. the dynamic sentiment
# traffic-light colors) without flattening it. Mirrors the buttons' 3D lip aesthetic
# but static (no hover/press). Apply with ``.classes(TILE_3D)`` on a ``ui.card``.
TILE_3D = (
    "rounded-[8px] "
    "shadow-[0_3px_0_0_rgba(0,0,0,.3),0_6px_14px_rgba(0,0,0,.42)]"
)

# Semantic STATE colors (positive / caution / negative / neutral) — the finite
# palette behind data-driven label colors in detail.py/header.py. Exact hexes from
# detail.py's GREEN/AMBER/RED/NEUTRAL constants, preserved as arbitrary-value classes
# so the look is unchanged. Set reactively via .classes(remove=STATE_TEXT, add=TXT_*)
# so repeated repaints don't stack conflicting text-[...] classes.
TXT_POS = "text-[#66bb6a]"
TXT_WARN = "text-[#ffa726]"
TXT_NEG = "text-[#ef5350]"
TXT_NEUTRAL = "text-[#bdbdbd]"
# Space-joined set for the remove= arg of a reactive color swap.
STATE_TEXT_CLASSES = "text-[#66bb6a] text-[#ffa726] text-[#ef5350] text-[#bdbdbd]"

# ---------------------------------------------------------------------------
# Quasar-internal / teleported escape-hatch CSS. These rules style the
# Quasar/Highcharts-internal DOM that component `.classes()` strings can't reach:
# the boxed q-field control (incl. the leg-table variants), the q-tab chrome, and
# the body-mounted `.strat-menu-navy` popup. Scoped under `.calc-v2` (the scope
# hook), so a page injects ONLY this slim block + applies the tokens above. The
# semantic styling (`.calc-v2` page bg, the old `.calc-card`/`.calc-eyebrow`/
# `.cv2-btn*`/`.strategy-menu-btn` box rules) is OMITTED here — it lives in the
# tokens (PAGE/CARD/EYEBROW/BTN/BTN_PRIMARY/STRATEGY_BTN).
# ---------------------------------------------------------------------------
QUASAR_INTERNAL_CSS = """
/* Boxed dark inputs — restyle the standard q-field control into a filled box. */
.calc-v2 .q-field__control{
  background:#0c1426;border:1px solid #243353;border-radius:8px;padding:0 10px;min-height:40px;
}
.calc-v2 .q-field__control:before,.calc-v2 .q-field__control:after{border:0!important;}
.calc-v2 .q-field--focused .q-field__control{
  border-color:#3b82f6;box-shadow:0 0 0 2px rgba(59,130,246,.28);
}
.calc-v2 .q-field__label{color:#7f8db0;}
.calc-v2 .q-field__native,.calc-v2 .q-field__native input,
.calc-v2 .q-field__native textarea,.calc-v2 .q-field__native span{color:#e7edf8!important;}
.calc-v2 .q-field__append .q-icon,.calc-v2 .q-field__prepend .q-icon{color:#8794b4;}
/* Strategy menu button internals — the q-btn__content layout + icon color are
   Quasar-internal (component .classes() can't reach them), so they must survive
   when a later phase swaps the button BOX style to the STRATEGY_BTN token. The
   base box rule (bg/border/radius/min-height) is intentionally NOT here. */
.calc-v2 .strategy-menu-btn .q-btn__content{justify-content:space-between;flex:1;text-transform:none;}
.calc-v2 .strategy-menu-btn .q-icon{color:#8794b4;}
/* Leg table header row */
.calc-v2 .leg-head{color:#7f8db0;font-size:12px;padding:0 2px 4px;}
/* Leg table rows — compact cells (less top/bottom padding, shorter height) and
   tighter side padding so "call"/"put" are not horizontally clipped. */
.calc-v2 .leg-row .q-field__control{min-height:32px;padding:0 6px;}
.calc-v2 .leg-row .q-field__control .q-field__native,
.calc-v2 .leg-row .q-field__marginal{min-height:32px;padding-top:0;padding-bottom:0;}
.calc-v2 .leg-row .q-field__append{padding-left:0;}
.calc-v2 .leg-row .q-field__native{font-size:13px;}
/* Centered strike value in the leg table. */
.calc-v2 .leg-strike .q-field__native{justify-content:center;text-align:center;}
/* Tabs (Simulator) — light labels, blue indicator, transparent panels so the
   dark-transparent Highcharts panels sit on the navy gradient. */
.calc-v2 .q-tabs{color:#8794b4;}
.calc-v2 .q-tab__label{font-weight:500;}
.calc-v2 .q-tab--active{color:#e7edf8;}
.calc-v2 .q-tab__indicator{background:#3b82f6;}
.calc-v2 .q-tab-panels,.calc-v2 .q-tab-panel,.calc-v2 .q-panel{background:transparent!important;}
/* Cascading Strategy menu popup — teleported to <body>, so NOT under .calc-v2.
   Theme it to match the navy cards. */
.strat-menu-navy.q-menu{
  background:#101a30!important;border:1px solid #213152;
  box-shadow:0 10px 28px rgba(0,0,0,.55);border-radius:10px;
}
.strat-menu-navy .q-item{color:#e7edf8;border-radius:6px;}
.strat-menu-navy .q-item__section,.strat-menu-navy .q-item__label{color:#e7edf8;}
.strat-menu-navy .q-item:hover,
.strat-menu-navy .q-item--active,
.strat-menu-navy .q-item.q-manuallyfocused{background:#1b2950!important;}
.strat-menu-navy .q-icon{color:#8794b4;}
"""
