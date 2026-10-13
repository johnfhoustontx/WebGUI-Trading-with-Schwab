"""Overview and Evidence on the page kit (Phase 5, Task 4).

These two screens own no buttons, tables, dialogs or toasts — verified, and
neither has ever had a guard entry — so what is theirs to get right is the
PANELS: one heading style, and the app's neutral ladder under them.

⚠ The seam between this task and Task 5 is deliberate and worth stating, since
both touch the same lines. Task 4 retires the ladder these pages wrote out BY
HAND — the eight neutral hexes spelled inline. Task 5 retires the NAMED
``terminal_theme`` tokens (``NOTE``, ``BODY``, ``EYEBROW``, ``MONO``, …) across
all four screens at once. A test here that asserted a ``T.*`` token was gone
would be doing Task 5's work halfway.

The data colours are the half that must NOT change: the band rail's
red→neutral→green ground, the dealer ladder's mark colours, the contribution
bars and every ``T.POS`` / ``T.NEG`` / ``T.WARN`` reading.
"""
import pathlib

from nicegui import ui

from pages import terminal_theme as T
from pages import ui_kit as kit
from pages.options import theme as _t

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"

# The heading the kit draws. Checked as classes rather than by eye, because the
# whole point is that every panel head in the app wears the same one.
SECTION = ("text-subtitle2", "font-semibold", _t.LABEL)


def _src(name):
    return (PAGES / f"{name}.py").read_text(encoding="utf-8")


def _render(module_name):
    """Render one screen and return ONLY the elements IT built.

    ``ui.context.client.elements`` is the auto-index client the whole module
    shares, so a plain ``elements.values()`` also hands back the panels the
    PREVIOUS test's render left behind — and "this screen's panel heads" then
    means both screens' (the Phase 3 Task 1 measurement)."""
    import importlib
    module = importlib.import_module(f"pages.{module_name}")
    before = set(ui.context.client.elements)
    with ui.card():
        module.render()
    return [e for i, e in ui.context.client.elements.items() if i not in before]


def _headed(els, text):
    """The label carrying ``text``, if it wears the kit's section heading."""
    for e in els:
        if isinstance(e, ui.label) and e.text == text:
            cls = " ".join(e.classes)
            if all(c in cls for c in SECTION):
                return e
    return None


class TestEveryPanelHeadIsTheKitsSectionTitle:
    def test_the_overview_s_four_panel_heads(self):
        els = _render("trade_overview")
        for head in ("Short Term", "Long Term", "Dealer positioning & volatility",
                     "Top stocks in the industry"):
            assert _headed(els, head) is not None, head

    def test_the_evidence_s_three_panel_heads(self):
        els = _render("trade_evidence")
        for head in ("Why — validated factors", "Model track record",
                     "This name's history"):
            assert _headed(els, head) is not None, head

    # ⚠ The Short Term / Long Term rename guard is NOT repeated here. It lives
    # in ``test_trade_recommendation.TestTheRenameIsComplete``, beside the
    # prose check it belongs with, and was re-aimed at ``kit.section_title``
    # when these two heads stopped being literal ``ui.label``s.


_COIN = {"symbol": "COIN", "price": 180.39, "classification": {
    "name": "Coinbase Global Inc.", "sector": "Finance",
    "industry": "Finance: Consumer Services", "sub_industry": "Finance Services",
    "industry_rank": 4, "industry_count": 173,
    "leaders": [
        {"rank": 1, "symbol": "AXP", "name": "American Express Company",
         "market_cap": 2.1e11},
        {"rank": 2, "symbol": "SOFI", "name": "SoFi Technologies Inc.",
         "market_cap": 3.0e10},
        {"rank": 3, "symbol": "COF", "name": "Capital One Financial Corporation",
         "market_cap": 1.4e11},
        {"rank": 4, "symbol": "COIN", "name": "Coinbase Global Inc.",
         "market_cap": 4.7e10},
        {"rank": 5, "symbol": "SYF", "name": "Synchrony Financial",
         "market_cap": 2.6e10},
    ]}}


def _texts(els):
    return [e.text for e in els if isinstance(e, ui.label)]


def _panel_of(label):
    """The card a heading sits in: the nearest ancestor wearing the app's card."""
    node = label
    while node is not None:
        if isinstance(node, ui.column) and _t.CARD.split()[0] in node.classes:
            return node
        slot = getattr(node, "parent_slot", None)
        node = slot.parent if slot is not None else None
    return None


class TestTheIndustryPanel:
    """It replaced "Where it sits among its peers" (the model's percentile for
    the ~60 names of its built-in sector map) on 2026-10-13, at the user's
    request: the top-ranked stocks of the symbol's industry instead."""

    def _render(self, monkeypatch, analysis):
        from pages import trade_shell as sh
        monkeypatch.setattr(sh, "read_analysis", lambda: analysis)
        return _render("trade_overview")

    def test_the_heading_names_the_industry(self, monkeypatch):
        els = self._render(monkeypatch, _COIN)
        assert _headed(els, "Top 5 in Finance: Consumer Services") is not None

    def test_one_card_per_leader_with_its_name_and_market_cap(self, monkeypatch):
        texts = _texts(self._render(monkeypatch, _COIN))
        for symbol, name in (("AXP", "American Express Company"),
                             ("SOFI", "SoFi Technologies Inc."),
                             ("SYF", "Synchrony Financial")):
            assert symbol in texts and name in texts, symbol
        assert "$210.00B" in texts
        assert "RANK 1" in texts and "RANK 5" in texts

    def test_the_analysed_symbol_is_marked_in_words_and_in_colour(self, monkeypatch):
        from pages import trade_shell as sh
        els = self._render(monkeypatch, _COIN)
        mine = [e for e in els if isinstance(e, ui.label)
                and e.text == "RANK 4 · THIS SYMBOL"]
        assert len(mine) == 1
        assert sh.SELECTED_TEXT in " ".join(mine[0].classes)
        assert "RANK 4" not in _texts(els)

    def test_the_symbols_own_place_is_on_the_heading_line(self, monkeypatch):
        texts = _texts(self._render(monkeypatch, _COIN))
        assert any(t.startswith("COIN ranks 4 of 173") for t in texts)

    def test_the_panel_hides_for_a_symbol_with_no_industry(self, monkeypatch):
        els = self._render(monkeypatch, {"symbol": "SPY", "price": 500.0})
        panel = _panel_of(_headed(els, "Top stocks in the industry"))
        assert panel is not None and panel.visible is False

    def test_the_panel_shows_when_there_are_leaders(self, monkeypatch):
        els = self._render(monkeypatch, _COIN)
        panel = _panel_of(_headed(els, "Top 5 in Finance: Consumer Services"))
        assert panel is not None and panel.visible is True

    def test_the_old_peers_panel_is_gone(self, monkeypatch):
        analysis = dict(_COIN, peers={"sector": "Financials", "ranked": [
            {"symbol": "JPM", "score": 80}, {"symbol": "COIN", "score": 40}]})
        texts = _texts(self._render(monkeypatch, analysis))
        assert "Where it sits among its peers" not in texts
        assert "PEER" not in texts and "JPM" not in texts
        assert "peers" not in _src("trade_overview")


class TestTheBarSaysWhereTheSymbolIsFiled:
    """Sector, Industry and Sub-industry sit in the shell's bar, so all four
    screens carry them; the Overview is simply the one rendered here."""

    _FIELDS = ["Symbol", "Company", "Sector", "Industry", "Sub-industry",
               "Last", "MTF bias"]

    def _render(self, monkeypatch, analysis):
        from pages import trade_shell as sh
        monkeypatch.setattr(sh, "read_analysis", lambda: analysis)
        return _render("trade_overview")

    def test_the_three_fields_follow_the_company(self, monkeypatch):
        texts = _texts(self._render(monkeypatch, _COIN))
        assert [t for t in texts if t in self._FIELDS] == self._FIELDS

    def test_they_carry_the_files_values(self, monkeypatch):
        texts = _texts(self._render(monkeypatch, _COIN))
        for value in ("Finance", "Finance: Consumer Services", "Finance Services"):
            assert value in texts, value

    def test_a_symbol_the_file_does_not_list_reads_as_dashes(self, monkeypatch):
        """Painted, not left empty: an empty label under "Sector" reads as a
        page that failed to load."""
        els = self._render(monkeypatch, {"symbol": "SPY", "price": 500.0})
        labels = [e for e in els if isinstance(e, ui.label)]
        for field in ("Sector", "Industry", "Sub-industry"):
            i = next(n for n, e in enumerate(labels) if e.text == field)
            assert labels[i + 1].text == "—", field


class TestNeitherScreenDrawsASecondTitle:
    """They receive their name from the shell now (Task 1) and had none before,
    so the risk runs the other way — but a shared frame is exactly what makes a
    doubled heading easy to ship, and the guard costs nothing."""

    def test_the_screen_name_appears_once(self):
        for module_name, title in (("trade_overview", "Overview"),
                                   ("trade_evidence", "Evidence")):
            els = _render(module_name)
            drawn = [e for e in els if isinstance(e, ui.label) and e.text == title]
            assert len(drawn) == 1, f"{module_name} draws {len(drawn)} titles"


class TestTheNeutralLadderIsTheApps:
    """Eight hand-written rungs collapse onto the app's two text tokens plus
    its card border — the same move the rotation family made when
    ``rotation_view.NEUTRAL`` retired. A neutral is SURFACE wherever it lives."""

    # Frames and grounds as well as text: a panel hairline, the dealer ladder's
    # axis and an industry card's edge all draw a frame, not a value.
    _GONE = ("#cfdaee", "#e6edf7", "#6b7b9c", "#7d8db0", "#56678a",
             "#22304c", "#1c2740", "#17223a", "#0e1626", "#0b1220")

    def test_the_overview_writes_no_neutral_of_its_own(self):
        src = _src("trade_overview")
        for hexa in self._GONE:
            assert hexa not in src, hexa
        assert "rgba(15,23,40" not in src, "the peer card's own ground"
        # ⚠ ONE survives, in `_MARK_BG`: the dealer ladder's flip mark is a
        # DATA colour in a finite map, beside the put wall's red and the call
        # wall's green.
        assert src.count("#8b9bb4") == 1
        assert '"flip": "bg-[#8b9bb4]"' in src

    def test_the_evidence_writes_no_neutral_of_its_own(self):
        src = _src("trade_evidence")
        for hexa in self._GONE + ("#8b9bb4", "#a8b6cf"):
            assert hexa not in src, hexa

    def test_both_reach_for_the_app_s_text_tokens(self):
        for name in ("trade_overview", "trade_evidence"):
            src = _src(name)
            assert "theme.LABEL" in src and "theme.MUTED" in src, name


class TestTheDataColoursAreUntouched:
    """The half that must NOT change, green on both sides of the migration."""

    def test_the_band_rail_keeps_its_red_to_green_ground(self):
        src = _src("trade_overview")
        for stop in ("#b4404f", "#4a4a63", "#2fa87a"):
            assert stop in src, stop

    def test_the_dealer_ladder_keeps_its_mark_colours(self):
        src = _src("trade_overview")
        for mark in ("#f87171", "#34d399", "bg-white", "#4a5b7d"):
            assert mark in src, mark

    def test_a_signed_reading_still_comes_from_the_finite_palette(self):
        src = _src("trade_evidence")
        assert "T.sign_text(" in src
        assert "T.BAR_POS" in src
        # The contribution figure used to restate T.POS / T.NEG as bare hexes.
        assert "#34d399" not in src and "#f87171" not in src
        assert "T.POS" in src and "T.NEG" in src

    def test_the_warning_glyph_uses_the_palette_rather_than_its_hex(self):
        for name in ("trade_overview", "trade_evidence"):
            src = _src(name)
            assert "#fbbf24" not in src, name
            assert "T.WARN" in src, name

    def test_the_chip_and_callout_vocabularies_survive(self):
        """``CHIP_*`` and ``CALLOUT*`` encode a state, so Task 5 keeps them and
        this task must not pre-empt it."""
        assert "T.CHIP_POS" in _src("trade_overview")
        assert "T.CALLOUT" in _src("trade_evidence")
        for name in ("CHIP_POS", "CHIP_WARN", "CHIP_NEG", "CHIP_OFF", "CALLOUT",
                     "CALLOUT_TEXT", "POS", "NEG", "WARN", "DIM", "OFF"):
            assert hasattr(T, name), name


class TestNeitherScreenBuildsAControlOfItsOwn:
    def test_no_raw_button_table_dialog_or_toast(self):
        for name in ("trade_overview", "trade_evidence"):
            src = _src(name)
            for raw in ("ui.button(", "ui.table(", "ui.dialog(", "ui.notify(",
                        "add_head_html"):
                assert raw not in src, (name, raw)

    def test_the_help_is_still_wired(self):
        """``test_trade_help`` reads these two for ``trade_help`` and ``tip(``,
        and the Overview for ``clearance_help`` EXACTLY twice — the chip and
        the side card, either of which can be the first a reader hovers."""
        assert _src("trade_overview").count("clearance_help") == 2
        for name in ("trade_overview", "trade_evidence"):
            src = _src(name)
            assert "trade_help" in src and "tip(" in src, name


def test_the_kit_s_section_title_is_what_it_is_asserted_to_be():
    """Non-vacuity for ``SECTION``: if the kit restyled its heading, every
    assertion above would quietly stop testing anything."""
    with ui.card():
        lbl = kit.section_title("x")
    cls = " ".join(lbl.classes)
    assert all(c in cls for c in SECTION)
