"""Shared **dark-navy "dashboard" theme** for the Options pages (Tier-1).

A single page-scoped CSS string, scoped under ``.calc-v2``, that converts
NiceGUI's standard underline fields into filled navy boxes, draws bordered cards,
styles the outline / primary buttons, themes the cascading Strategy menu popup,
and (for chart pages like the Simulator) makes Quasar tabs / tab-panels
transparent so the dark-transparent Highcharts panels sit on the navy gradient.

Both the **Calculator** and the **Simulator** inject this one constant
(``ui.add_css(DASHBOARD_CSS)``) and wrap their content in ``.calc-v2`` — so the
look never drifts between the two pages. (The class name is historical —
``calc-v2`` — kept stable so neither page's markup has to change; the theme is
page-agnostic and promotable app-wide.)

Apply to a new page::

    from pages.options.theme import DASHBOARD_CSS
    ui.add_css(DASHBOARD_CSS)
    with ui.column().classes("calc-v2 w-full gap-4"):
        ui.label("Title").classes("text-h6").style("color:#eaf0fb")
        with ui.column().classes("calc-card w-full gap-3"):   # bordered navy panel
            ui.input("Symbol")                                 # auto-boxed (q-field)
            ui.button("Go", color=None).props("no-caps").classes("cv2-btn-primary")

Inputs / selects / tabs inside ``.calc-v2`` are auto-restyled; **buttons need
``color=None``** (drops Quasar's ``bg-primary``) + a ``cv2-btn`` / ``cv2-btn-primary``
class. Class vocabulary: ``.calc-card`` (panel), ``.calc-eyebrow`` (muted label),
``.cv2-btn`` / ``.cv2-btn-primary`` (buttons), ``.strategy-menu-btn`` (boxed Strategy
trigger, via ``strategy_menu.build_strategy_menu(boxed=True)``), ``.strat-menu-navy``
(the teleported Strategy popup — GLOBAL, mounts on ``<body>`` outside ``.calc-v2``),
``.leg-head`` (leg-table header, via ``leg_editor.build_leg_editor(header=True)``),
``.leg-strike`` (centered strike cell). The **full palette + class reference** lives
in the root ``CLAUDE.md`` "App theme — dark-navy 'dashboard'" section (canonical).
"""

# Dark-navy "dashboard" restyle, page-scoped under .calc-v2. Converts NiceGUI's
# standard underline fields to filled navy boxes, draws bordered cards, styles the
# outline / primary buttons, and themes tabs + the teleported Strategy menu popup.
DASHBOARD_CSS = """
.calc-v2{
  background:radial-gradient(130% 90% at 50% -20%,#16243f 0%,#0c1424 55%,#0a0f1c 100%);
  border:1px solid #1d2942;border-radius:14px;padding:18px 20px 22px;color:#cdd8ee;
}
.calc-v2 .calc-card{
  background:#101a30;border:1px solid #213152;border-radius:12px;padding:14px 16px;
}
.calc-v2 .calc-eyebrow{color:#8794b4;font-size:12px;letter-spacing:.02em;}
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
/* Leg table header row */
.calc-v2 .leg-head{color:#7f8db0;font-size:12px;padding:0 2px 4px;}
/* Leg table rows — compact cells (less top/bottom padding, shorter height) and
   tighter side padding so "call"/"put" are not horizontally clipped. */
.calc-v2 .leg-row .q-field__control{min-height:32px;padding:0 6px;}
.calc-v2 .leg-row .q-field__control .q-field__native,
.calc-v2 .leg-row .q-field__marginal{min-height:32px;padding-top:0;padding-bottom:0;}
.calc-v2 .leg-row .q-field__append{padding-left:0;}
.calc-v2 .leg-row .q-field__native{font-size:13px;}
/* Buttons */
.calc-v2 .cv2-btn.q-btn{
  background:#15213b!important;color:#cdd8ee!important;border:1px solid #2a3a5c;
  border-radius:9px;min-height:40px;font-weight:500;
}
.calc-v2 .cv2-btn.q-btn:hover{background:#1b2950!important;}
.calc-v2 .cv2-btn-primary.q-btn{
  background:#2563eb!important;color:#fff!important;border-radius:9px;min-height:40px;font-weight:600;
}
.calc-v2 .cv2-btn-primary.q-btn:hover{background:#1d4fd1!important;}
/* Strategy menu button — match the boxed navy inputs (drop the blue outline). */
.calc-v2 .strategy-menu-btn.q-btn{
  background:#0c1426!important;border:1px solid #243353!important;color:#e7edf8!important;
  border-radius:8px;min-height:40px;font-weight:400;padding:0 6px 0 12px;
}
.calc-v2 .strategy-menu-btn.q-btn:hover{border-color:#3b82f6!important;}
.calc-v2 .strategy-menu-btn .q-btn__content{justify-content:space-between;flex:1;text-transform:none;}
.calc-v2 .strategy-menu-btn .q-icon{color:#8794b4;}
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
