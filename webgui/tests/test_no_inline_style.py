"""Guard: the converted Options pages/helpers are inline-style-free (Tailwind-only).

Phase 2 of the Tailwind-first migration removes every inline `.style()` from the
shared `pages/options/*` helpers; Phase 3a extends this to the six signal-table
screens (Scanner/Swing/Captured/Paper/Paper-Portfolio/Rescue), which ALSO drop the
Vue `:style=` bindings in their Quasar table slots — dynamic cell colors map to
stamped Tailwind `:class` bindings instead. These tests prevent a regression that
reintroduces either an inline `.style()` or a slot `:style=`.
"""
import pathlib


def test_options_helpers_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in ["detail.py", "header.py", "overlay.py", "leg_editor.py", "strategy_menu.py"]:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"


# Phase 3a signal-table screens: no `.style(` AND no `:style=` (slot bindings).
PHASE_3A_FILES = ["scanner.py", "swing.py", "captured.py", "paper.py",
                  "portfolio.py", "rescue.py"]


def test_phase3a_pages_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in PHASE_3A_FILES:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"


# Phase 3b builder/analytic screens: Calculator + Simulator on Tailwind tokens.
# The Calculator's P&L heatmap grid is a raw ``ui.html()`` HTML string with inline
# ``style=`` attributes (NOT the `.style(` method / Vue `:style=` binding) — so it
# is out of scope and the guard (which checks `.style(`/`:style=`) still passes.
PHASE_3B_FILES = ["calculator.py", "simulator.py"]


def test_phase3b_pages_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in PHASE_3B_FILES:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"


# Phase 3c chart-heavy screens: Gamma + Expected-Move on Tailwind only.
# Gamma's dynamic colors are palette-mapped and its panel flex uses a runtime
# arbitrary Tailwind class; ``EXPLAIN_CSS`` is a ``ui.add_css`` of a CSS STRING
# (styles the Explain ``ui.html()`` fragment) — NOT a `.style(`/`:style=`, so it
# passes. The Highcharts option dicts (chart colors) are out of scope. Expected-Move
# was already inline-style-free — it only joins this guard.
PHASE_3C_FILES = ["gamma.py", "expected_move.py"]


def test_phase3c_pages_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in PHASE_3C_FILES:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"


# Standalone Tier-1 reader pages: Tailwind tokens + colored-cell slots binding
# stamped `_*_class` fields (no `.style(`, no Vue `:style=`).
OPTIONS_MATRIX_FILES = ["matrix.py", "flow.py"]


def test_options_matrix_page_has_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in OPTIONS_MATRIX_FILES:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"


# Phase 4: the Trade page (the last DASHBOARD_CSS consumer) on Tailwind tokens +
# palette-maps. Verdict/bias/markov dynamic colors became `text-[…]`/`bg-[…]`
# classes (reactive verdict-card sites swap via .classes(remove=…, add=…)), so the
# page carries no `.style(` and no Vue `:style=` slot binding.
def test_trade_page_has_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages"
    src = (base / "trade.py").read_text(encoding="utf-8")
    assert ".style(" not in src, "trade.py still uses .style()"
    assert ":style=" not in src, "trade.py still uses a Vue :style= slot binding"


# Phase 5: the top-level Sentiment + Sector-Rotation pages on Tailwind only. These
# keep their OWN look (NOT the navy dashboard theme), so their dynamic colors map to
# a LOCAL 5-color Tailwind class palette (not the theme tokens); reactive in-place
# recolors swap via .classes(remove=…, add=…). The `.sent-sectors` ui.add_css block
# is gone — its borders/hover now ride Tailwind on the sector/industry rows.
def test_sentiment_pages_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages"
    for fn in ["sentiment.py", "sentiment_sectors.py", "sentiment_rotation.py",
               "sentiment_rrg.py", "sentiment_momentum.py",
               # The sector heat grid and its pure display language. The grid is
               # the strongest case in the app for the standard holding: it is
               # nothing BUT colour and measurement, and it still needs no
               # `.style()` and no `ui.add_css` block of its own.
               "sector_heat.py",
               # The Sector Rotation board and its display language. Its
               # diverging gauge is absolutely-positioned percentages — the
               # shape most likely to reach for `.style()` — and it does not:
               # runtime `left-[…%]`/`w-[…%]` arbitraries carry it, the
               # documented continuous-value exception.
               "rotation_view.py", "oklch.py",
               # The hand-drawn RRG plot. Its markers and ticks are runtime
               # percentage arbitraries; its only non-utility content is the
               # trail layer, a raw `ui.html()` SVG string — the documented
               # out-of-scope case, same as pages/rings.py.
               "rrg_view.py",
               # The Momentum page's guided layout. Its diverging component
               # bars and rank chart are runtime percentage arbitraries; the
               # rank lines are a raw `ui.html()` SVG string.
               "momentum_view.py",
               # The Market Regime Console. Its whole design is glows, hairline
               # grids and gradients — the shapes most likely to reach for
               # `.style()` — and a Phase 0/2 spike measured that every one of
               # them generates as a Tailwind arbitrary, so there is no excuse.
               "console.py", "console_cards.py", "console_regime.py",
               "console_page.py", "console_dial.py", "regime_mix.py",
               "rings.py"]:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"


# Phase 8: status.py widths → Tailwind `min-w-[Npx]`; the remaining utility pages
# (settings/terminate/manuals) carry no inline `.style()`. eod.py joins the guard
# too: its `EOD_CSS` styles an out-of-scope `ui.html()` report fragment via
# `ui.add_css` and its HTML uses bare `style=` attributes (NOT the `.style(`
# method / Vue `:style=` binding), so the guard — which checks `.style(`/`:style=`
# — correctly passes. (status.py keeps its Quasar `ui.icon().props(color=…)` props,
# out of scope.)
# The Desk joins them: its structure bar and hotness bar are absolutely-
# positioned runtime percentage arbitraries (the documented continuous-value
# exception), and its rings are a raw ``ui.html()`` SVG string built by
# ``pages/rings.py`` — the out-of-scope case, not an inline style.
PHASE_8_FILES = ["status.py", "settings.py", "terminate.py", "manuals.py",
                 "eod.py", "market.py", "ticker.py", "desk.py"]


def test_utility_pages_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages"
    for fn in PHASE_8_FILES:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"
