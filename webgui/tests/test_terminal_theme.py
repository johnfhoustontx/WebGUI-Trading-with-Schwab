"""``terminal_theme`` after its SURFACE half retired (2026-09-20).

The module never had a test file of its own: every token was pinned only where
a Signal Desk screen happened to use it, so a surface token could be deleted
and nothing would say so until a render raised ``AttributeError``. This is that
file, and it asserts BOTH halves.

* The retired names are **gone from the module** — the positive form
  ``test_theme.py`` uses for ``[rotation]``, which can actually fail. An
  ``assert "T.PAGE" not in src`` cannot: once the attribute does not exist the
  line is unfalsifiable.
* Every DATA encoding is still here and still the colour it was, byte for byte.
  That half is what the four screens read a value through, and the migration's
  one hard rule is that it does not move.

What replaced the surface half is the app's own palette, reached through
``pages.options.theme``. What replaced ``MONO`` is nothing at all: the app sets
``font-variant-numeric: tabular-nums`` on ``body`` from ``[typography] numeric``,
so the columns these screens align were never depending on a monospaced FACE —
only on tabular figures, which IBM Plex Sans has.
"""
import pathlib
import re

import pytest

from pages import terminal_theme as T
from pages import trade_shell as sh
from pages.options import theme

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"
SCREENS = ("trade_shell", "trade_board", "trade_evidence", "trade_overview",
           "trade_plan_screen")
_P = theme.THEME["palette"]

# Everything the module carried that drew a SURFACE, a FACE or a CONTROL.
RETIRED = ("FONT_HTML", "PAGE", "SHELL", "PANEL", "MONO", "EYEBROW",
           "PANEL_TITLE", "SCREEN_TITLE", "SUBTLE", "NOTE", "BODY", "LABEL",
           "VALUE", "HAIRLINE", "RULE", "BTN_PRIMARY", "BTN_GHOST", "TOOLTIP")

# Everything that encodes a VALUE (plus the scroller, which is geometry the
# nine-column tables cannot do without). These are the tokens the four screens
# read a reading through, and their hexes are the assertion: a data colour that
# drifts is the one thing this migration must not do.
KEPT = {
    "POS": "text-[#34d399]",
    "NEG": "text-[#f87171]",
    "WARN": "text-[#fbbf24]",
    "DIM": "text-[#7d8db0]",
    "OFF": "text-[#4a5b7d]",
    "BAR_POS": "bg-[#34d399]",
    "BAR_NEG": "bg-[#f87171]",
    "BAR_DIM": "bg-[#4a5b7d]",
    "CHIP_POS": "border-[#1f6b52] bg-[rgba(52,211,153,0.08)] text-[#34d399]",
    "CHIP_WARN": "border-[#4a3c17] bg-[rgba(251,191,36,0.08)] text-[#fbbf24]",
    "CHIP_NEG": "border-[#5b2733] bg-[rgba(248,113,113,0.08)] text-[#f87171]",
    "CHIP_OFF": "border-[#263353] bg-[rgba(15,23,40,0.6)] text-[#8b9bb4]",
    "SCROLL_X": "w-full overflow-x-auto min-w-0",
}


def _src(name):
    return (PAGES / f"{name}.py").read_text(encoding="utf-8")


def _rel_luminance(hexstr):
    r, g, b = theme.hex_rgb(hexstr, (0, 0, 0))

    def _c(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * _c(r) + 0.7152 * _c(g) + 0.0722 * _c(b)


def contrast(fg, bg):
    """WCAG contrast ratio between two hex colours. PURE."""
    a, b = _rel_luminance(fg), _rel_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ── the surface half is gone ────────────────────────────────────────────────
@pytest.mark.parametrize("name", RETIRED)
def test_the_retired_token_is_gone_from_the_module(name):
    """Gone, not merely unused. A second page-scoped ground, face, panel and
    button set is what made the Signal Desk the seventh visual family."""
    assert not hasattr(T, name), \
        f"terminal_theme.{name} is a surface value and must not survive"


@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("name", RETIRED)
def test_no_screen_still_asks_the_module_for_a_surface(screen, name):
    assert not re.search(rf"\bT\.{name}\b", _src(screen)), \
        f"{screen} still reads terminal_theme.{name}"


@pytest.mark.parametrize("screen", SCREENS)
def test_no_screen_declares_a_face_of_its_own(screen):
    """``MONO`` outlived its font. Task 1 deleted the JetBrains Mono link, so
    every numeric it marked had been falling back to the SYSTEM mono — a face
    that matches nothing else on the page. The columns it was there for align
    on tabular figures, not on a monospaced face.

    ``font-[`` is the precise test, not the font's NAME: the Tailwind
    font-family arbitrary is the only way a page here can set one, and the
    retired rank-board comment still names the two faces it was measured in,
    which is history worth keeping."""
    src = _src(screen)
    assert "font-[" not in src, f"{screen} declares a font family of its own"
    assert "fonts.googleapis.com" not in src, screen


def test_the_columns_align_on_the_app_s_own_tabular_figures():
    """Non-vacuity for deleting ``MONO``: if ``[typography] numeric`` ever
    stopped emitting this, every numeric column on these four screens would
    lose its alignment and nothing else would notice."""
    css = theme.build_typography_css(theme.THEME)
    assert "font-variant-numeric:tabular-nums" in css.replace(" ", "")


# ── the data half is untouched ──────────────────────────────────────────────
@pytest.mark.parametrize("name,value", sorted(KEPT.items()))
def test_the_data_half_survives_with_its_exact_colour(name, value):
    assert getattr(T, name) == value


def test_the_composite_state_token_still_lists_every_text_state():
    """``STATE_TEXT`` is what a repainted label passes to ``.classes(remove=)``;
    a state missing from it stacks two colours on one label."""
    assert T.STATE_TEXT.split() == [T.POS, T.NEG, T.WARN, T.DIM, T.OFF]


def test_the_callout_vocabulary_survives():
    assert T.CALLOUT.startswith("flex gap-[11px] rounded-[10px] border")
    assert T.CALLOUT_TEXT == "text-[12px] leading-[1.6] text-[#cbb98a]"
    assert T.CHIP_BASE.startswith("inline-flex items-center gap-2")


def test_the_rank_board_toggle_keeps_its_selected_state():
    """``FILTER_ON``/``FILTER_OFF`` are the one guard entry Phase 5 keeps: the
    Hide gated toggle carries a selected state AND a label that changes with
    it, which ``kit.button``'s four kinds express neither of."""
    assert "bg-[#151f36]" in T.FILTER_ON
    assert "bg-transparent" in T.FILTER_OFF


def test_the_three_pure_helpers_survive():
    assert T.sign_text(1) == T.POS and T.sign_text(-1) == T.NEG
    assert T.sign_text(0) == T.DIM and T.sign_text(None) == T.OFF
    assert T.sign_bar(1) == T.BAR_POS and T.sign_bar(-1) == T.BAR_NEG
    assert T.centred(0.5, 1.0) == (50.0, 25.0)


# ── what replaced it ────────────────────────────────────────────────────────
SHELL_VOCAB = ("EYEBROW", "SUBTLE", "NOTE", "VALUE", "HAIRLINE", "RULE",
               "TOOLTIP")


@pytest.mark.parametrize("name", SHELL_VOCAB)
def test_the_family_s_type_geometry_moved_to_the_shell(name):
    """The sizes, tracking and leading are genuinely this family's — every
    migrated page keeps its own (``desk.py``'s strip vocabulary is the
    precedent). What must not be a second language is the PALETTE, so these
    carry app tokens and no ladder of their own."""
    assert isinstance(getattr(sh, name), str)


@pytest.mark.parametrize("name", SHELL_VOCAB)
def test_the_shell_vocabulary_writes_no_ladder_of_its_own(name):
    """Every rung the module used to carry, and the three frames beside them."""
    gone = ("#56678a", "#7d8db0", "#a8b6cf", "#cfdaee", "#e6edf7", "#f2f6fc",
            "#131d31", "#1c2740", "#0e1626", "#0b1220")
    value = getattr(sh, name)
    for hexa in gone:
        assert hexa not in value, f"sh.{name} still carries {hexa}"


def test_the_shell_vocabulary_reaches_for_the_app_s_palette():
    assert _P["icon"] in sh.EYEBROW and _P["icon"] in sh.SUBTLE
    assert _P["muted"] in sh.NOTE
    assert _P["title"] in sh.VALUE
    assert _P["card_border"] in sh.RULE
    # The tooltip was always pure geometry — no colour at all — so it moves
    # rather than changing. It is the only one of the seven that does.
    assert "whitespace-pre-line" in sh.TOOLTIP and "#" not in sh.TOOLTIP


def test_the_family_s_dimmest_text_reads_better_than_it_did():
    """MEASURED, not assumed. The retired ladder's bottom rung (#56678a) read
    3.2:1 on the app's card — under the 4.5:1 AA floor and under the 3:1 large-
    text floor by less than a fifth. The app's own dimmest text is brighter.

    ⚠ ``T.OFF`` (#4a5b7d, 2.7:1) is NOT covered here and is not a defect this
    task may fix: it is a DATA colour meaning "no reading", it is in the KEEP
    list above, and rule 8 forbids touching one. It is flagged, not changed."""
    card = _P["card_bg"]
    assert contrast("#56678a", card) < 3.3            # what it was
    assert contrast(_P["icon"], card) > 4.0           # what it is
    assert contrast(_P["muted"], card) > 5.5
    assert contrast(_P["title"], card) > 12.0


# ── the ladder on the two screens Task 4 did not reach ──────────────────────
class TestTheRankBoardAndTradePlanJoinTheAppLadder:
    """Task 4 collapsed Overview's and Evidence's hand-written rungs onto the
    app's two text tokens and its card border. The Rank Board and the Trade
    Plan still spelled theirs out — and those rungs ARE the ladder the tokens
    retired here were the spine of, so leaving them would ship one family
    speaking two greys."""

    _GONE = ("#56678a", "#7d8db0", "#8b9bb4", "#a8b6cf", "#cfdaee", "#e6edf7",
             "#f2f6fc", "#22304c", "#1c2740")

    @pytest.mark.parametrize("screen", ("trade_board", "trade_plan_screen"))
    def test_the_screen_writes_no_neutral_of_its_own(self, screen):
        src = _src(screen)
        for hexa in self._GONE:
            assert hexa not in src, (screen, hexa)
        assert "rgba(15,23,40,0.7)" not in src, "the no-trade card's own ground"

    @pytest.mark.parametrize("screen", ("trade_board", "trade_plan_screen"))
    def test_the_screen_reaches_for_the_app_s_text_tokens(self, screen):
        src = _src(screen)
        assert "theme.LABEL" in src and "theme.MUTED" in src, screen

    @pytest.mark.parametrize("screen", ("trade_board", "trade_plan_screen"))
    def test_a_restated_data_hex_goes_back_to_its_token(self, screen):
        """The amber glyphs and the accent rules were byte-identical
        restatements of ``T.WARN`` / ``T.BAR_POS``. The finite-set rule."""
        src = _src(screen)
        assert "#fbbf24" not in src, screen
        assert "#34d399" not in src, screen
        assert "#f87171" not in src, screen
        assert re.search(r"\bT\.(WARN|BAR_WARN|CHIP_WARN)\b", src), screen
        assert re.search(r"\bT\.BAR_POS\b", src), screen

    def test_the_rank_board_keeps_its_own_signed_readings(self):
        """The half that must not change: score, expectancy and P&L classes
        still come from the pure builders' finite palette."""
        src = _src("trade_board")
        for cls in ("score_class", "exp_class", "pnl_class", "gate_chip",
                    "dealer_class", "iv_class"):
            assert cls in src, cls


class TestTheSelectionAccentIsTheApps:
    """Overview's peer card and the Trade Plan's key row both mean "this is the
    one you are looking at" — a SELECTION, not a datum. The app has exactly one
    colour for that: ``[palette] focus``, which is what ``ui_kit.table``'s own
    selected row wears (``build_surface_css``'s ``.kit-row-selected``) and what
    ``sentiment_momentum``'s selected chip ring already took. The indigo said
    the same thing in a colour the app speaks nowhere else — and Task 1 retired
    that very indigo from the Symbol field, so leaving it here would have left
    the app speaking it in two places and not a third."""

    @pytest.mark.parametrize("screen", ("trade_overview", "trade_plan_screen"))
    def test_the_indigo_is_gone(self, screen):
        src = _src(screen)
        for hexa in ("#818cf8", "#3a3f7a", "rgba(99,102,241"):
            assert hexa not in src, (screen, hexa)

    @pytest.mark.parametrize("screen", ("trade_overview", "trade_plan_screen"))
    def test_the_app_s_selection_accent_took_its_place(self, screen):
        assert "sh.SELECTED_" in _src(screen), screen

    def test_the_shell_s_selection_tokens_are_the_app_s_focus_colour(self):
        assert sh.SELECTED_TEXT == f"text-[{_P['focus']}]"
        assert sh.SELECTED_BAR == f"bg-[{_P['focus']}]"
        assert _P["focus"] in sh.SELECTED_BOX
        # The kit's own selected row is an accent EDGE over an 8% wash; the
        # peer card and the key row are built the same way rather than in a
        # second dialect of the same idea.
        assert ",0.08)]" in sh.SELECTED_BOX

    def test_the_kit_s_own_selected_row_wears_the_same_colour(self):
        """Non-vacuity: if the kit ever moved off ``focus``, the assertions
        above would be pinning a colour that means nothing any more."""
        css = theme.build_surface_css(theme.THEME)
        assert f"inset 3px 0 0 {_P['focus']}" in css
