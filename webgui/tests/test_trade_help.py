"""The Trade Analyzer's per-tile hover explanations.

These are prose, so the tests pin the things prose can get wrong silently: a
tile whose explanation was never written, an internal identifier leaking into a
sentence a user reads, and the two clearance states meaning the same thing.
"""
import pytest

from pages import trade_board, trade_help as th
from pages.trade import _FACTOR_LABELS


class TestClearance:
    """The user's own examples: "what does LONG - Cleared mean", "what does
    SHORT - Relative only mean". Both sides x every state the Overview's
    `_side_card` map can render."""

    STATES = ("cleared", "relative_only", "blocked", "unknown")

    @pytest.mark.parametrize("side", ["long", "short"])
    @pytest.mark.parametrize("state", STATES)
    def test_every_side_and_state_is_explained(self, side, state):
        assert len(th.clearance_help(side, state)) > 120

    def test_the_short_side_explains_WHY_it_needs_a_second_hurdle(self):
        """The load-bearing fact: the model predicts excess return vs SPY, so a
        bottom-band name is predicted to LAG the index, not to fall. Without
        that sentence "relative only" is an arbitrary restriction."""
        t = th.clearance_help("short", "relative_only").lower()
        # The prose says "the S&P" rather than the ticker — plainer for a
        # reader, and the assertion is about the CONCEPT being named, not the
        # spelling.
        assert "s&p" in t or "spy" in t or "index" in t
        assert "lag" in t

    def test_relative_only_says_HOW_to_express_it(self):
        t = th.clearance_help("short", "relative_only").lower()
        assert "pair" in t

    def test_a_cleared_long_and_a_relative_only_short_do_not_read_alike(self):
        assert th.clearance_help("long", "cleared") != \
            th.clearance_help("short", "relative_only")

    def test_the_long_side_says_it_is_never_blocked(self):
        """`market_filter` demotes rather than blocks a long, and a reader who
        never sees LONG BLOCKED should be told that is by design."""
        assert "never" in th.clearance_help("long", "blocked").lower()

    def test_an_unrecognised_state_still_explains_itself(self):
        assert th.clearance_help("long", "wat")
        assert th.clearance_help(None, None)


class TestCoverage:
    """A tile that renders without an explanation is the failure mode here, and
    it is invisible — the tooltip simply does not appear."""

    def test_every_model_and_investor_factor_is_explained(self):
        missing = [k for k in _FACTOR_LABELS if not th.factor_help(k)]
        assert missing == []

    def test_every_rank_board_column_is_explained(self):
        heads = [h for h in trade_board._HEAD if h] + ["DECILE", "DTC"]
        missing = [h for h in heads if not th.column_help(h)]
        assert missing == []

    def test_every_evidence_column_is_explained(self):
        missing = [h for h in ("FACTOR", "Z", "WEIGHT", "CONTRIBUTION", "IC")
                   if not th.column_help(h)]
        assert missing == []

    def test_every_paper_book_column_is_explained(self):
        missing = [h for h in ("SYMBOL", "SIDE", "AS", "OPENED", "P&L", "STATUS")
                   if not th.column_help(h)]
        assert missing == []

    def test_every_trade_plan_row_is_explained(self):
        rows = ("Structure", "Short strike", "Entry zone", "Stop", "Target",
                "Time stop", "Events")
        missing = [r for r in rows if not th.row_help(r)]
        assert missing == []

    def test_every_command_bar_report_button_is_explained(self):
        """Caught live: the buttons are keyed by their COMMAND name
        ("deepdive"), not by a slug I chose ("deep_dive"), so both tooltips
        silently attached nothing. Reading the keys off `_REPORTS` is what
        makes this test able to fail."""
        from pages.trade_shell import _REPORTS
        missing = [cmd for _label, cmd, _view, _route in _REPORTS
                   if not th.help_for(cmd)]
        assert missing == []

    def test_the_recommendation_tiles_are_explained(self):
        """The Position card now LEADS with an action, so the two tiles that
        qualify it have to carry their own explanation."""
        for key in ("recommendation", "confidence", "investor_confidence"):
            assert th.help_for(key), key

    def test_every_dealer_stat_is_explained(self):
        rows = ("Gamma regime", "Setup", "Flip", "Call wall", "Put wall",
                "ATM IV")
        missing = [r for r in rows if not th.row_help(r)]
        assert missing == []

    def test_lookup_is_case_and_space_insensitive(self):
        """The pages pass whatever they render — "Call wall", "CALL WALL",
        "EXP / 20D" — and a tooltip must not vanish over capitalisation."""
        assert th.row_help("CALL WALL") == th.row_help("Call wall")
        assert th.column_help("exp / 20d") == th.column_help("EXP / 20D")

    def test_an_unknown_key_is_silent_rather_than_a_placeholder(self):
        assert th.help_for("no-such-tile") == ""
        assert th.factor_help("no_such_factor") == ""
        assert th.column_help(None) == ""


class TestThePlainEnglishRule:
    """The user asked for plain English. An internal identifier reaching the
    screen is the concrete, testable half of that."""

    def _texts(self):
        out = list(th.ALL_TEXTS)
        for side in ("long", "short"):
            for st in ("cleared", "relative_only", "blocked", "unknown"):
                out.append(th.clearance_help(side, st))
        return out

    def test_no_snake_case_identifier_reaches_the_reader(self):
        import re
        bad = [t for t in self._texts()
               if re.search(r"\b[a-z]+_[a-z_]+\b", t)]
        assert bad == [], bad[:2]

    def test_each_explanation_is_a_detailed_sentence_not_a_label(self):
        short = [t for t in self._texts() if len(t) < 80]
        assert short == [], short[:3]

    def test_each_explanation_reads_as_prose(self):
        wrong = [t for t in self._texts()
                 if not t[0].isupper() or t.rstrip()[-1] not in ".?"]
        assert wrong == [], wrong[:3]


class TestThePagesAreActuallyWired:
    """`trade_help` could be flawless and attached to nothing — a tooltip that
    was never mounted looks exactly like a tooltip that has no text. These are
    source-level checks, so they do not need a browser."""

    PAGES = ("trade_overview", "trade_evidence", "trade_board",
             "trade_plan_screen", "trade_shell")

    def _src(self, name):
        import pathlib as _p
        return (_p.Path(__file__).resolve().parents[1] / "pages"
                / f"{name}.py").read_text(encoding="utf-8")

    def test_every_screen_imports_the_help_and_attaches_it(self):
        for name in self.PAGES:
            src = self._src(name)
            assert "trade_help" in src, name
            assert "tip(" in src, name

    def test_the_clearance_help_reaches_the_chips_and_the_side_cards(self):
        """The two places the user named. The Overview mounts BOTH — a compact
        chip beside Market state and a fuller card below — and each has to
        carry the explanation, because either one can be the first the reader
        hovers."""
        src = self._src("trade_overview")
        assert src.count("clearance_help") == 2

    def test_the_factor_rows_key_off_the_engine_key_not_the_label(self):
        """`humanize_factor` output would silently miss every lookup."""
        for name in ("trade_overview", "trade_evidence"):
            assert 'factor_help(' in self._src(name), name
            assert 'factor_help(r["name"])' not in self._src(name), name
