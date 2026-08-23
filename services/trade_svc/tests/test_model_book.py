"""Tests for the model paper book (Phase 6).

An isolated book that trades the rank board's pools by the Trade Plan's own
rules, so the model's decisions accrue a track record without anyone placing
them. It answers the question the journal cannot: not "did the ranking
correlate?" but "what would following it have done?".

⚠ **It trades the UNDERLYING, deliberately, not the P3 options structure.** The
model predicts a 20-day excess return on the STOCK. Wrapping that in a spread
adds theta and vega P&L that has nothing to do with whether the ranking works,
so a book that lost money on correct calls would be indistinguishable from one
whose calls were wrong. The Trade Plan still tells a human which structure to
use; this measures the signal underneath it. Stated as a deviation from the
plan, not slipped past it.

The rules that matter here are all about not flattering the result:
  * the market filter is honoured — a relative-only short is opened as a PAIR
    against SPY, because that is what the model actually predicts;
  * gated names are not traded, matching what the board tells a human;
  * the time stop is the model's own horizon, so a losing position cannot be
    held indefinitely until it recovers;
  * long and short are reported SEPARATELY, because a book whose longs carry it
    is a different product from one that works on both sides.

Run from the repo root:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_model_book.py -v
"""
import datetime as dt

import pytest

from services.trade_svc import model_book as mb


def _board(**over):
    b = {
        "status": "ok", "as_of": "2026-08-03", "n": 40,
        "thin_cross_section": False, "short_expression": "directional",
        "horizon_days": 20,
        "long_pool": ["AAA", "BBB"], "short_pool": ["YYY", "ZZZ"],
        "rows": [
            {"symbol": "AAA", "composite": 0.9, "decile": 10, "pool": "long",
             "gates": [], "gated_long": False, "gated_short": False},
            {"symbol": "BBB", "composite": 0.8, "decile": 10, "pool": "long",
             "gates": ["earnings in 3 days"], "gated_long": True,
             "gated_short": True},
            {"symbol": "YYY", "composite": -0.8, "decile": 1, "pool": "short",
             "gates": [], "gated_long": False, "gated_short": False},
            {"symbol": "ZZZ", "composite": -0.9, "decile": 1, "pool": "short",
             "gates": ["squeeze risk"], "gated_long": False,
             "gated_short": True},
        ],
    }
    b.update(over)
    return b


TODAY = dt.date(2026, 8, 3)
PRICES = {"AAA": 100.0, "BBB": 50.0, "YYY": 200.0, "ZZZ": 30.0, "SPY": 500.0}


class TestWhatItOpens:
    def test_it_opens_the_ungated_names_from_both_pools(self):
        opens = mb.candidates(_board(), PRICES, today=TODAY)
        syms = {o["symbol"] for o in opens}
        assert "AAA" in syms and "YYY" in syms

    def test_a_gated_name_is_NOT_traded(self):
        """The board shows it with its reasons; the book declines it. Trading
        what the board tells a human to skip would measure a strategy nobody
        would run."""
        opens = mb.candidates(_board(), PRICES, today=TODAY)
        syms = {o["symbol"] for o in opens}
        assert "BBB" not in syms          # gated long
        assert "ZZZ" not in syms          # gated short

    def test_each_side_is_recorded_as_its_own_side(self):
        opens = {o["symbol"]: o for o in mb.candidates(_board(), PRICES, today=TODAY)}
        assert opens["AAA"]["side"] == "long"
        assert opens["YYY"]["side"] == "short"

    def test_a_thin_or_broken_board_opens_nothing(self):
        assert mb.candidates(_board(thin_cross_section=True), PRICES, today=TODAY) == []
        assert mb.candidates({"status": "legacy_snapshot"}, PRICES, today=TODAY) == []
        assert mb.candidates(None, PRICES, today=TODAY) == []

    def test_a_name_with_no_price_is_skipped_rather_than_opened_at_zero(self):
        opens = mb.candidates(_board(), {"AAA": 100.0, "SPY": 500.0}, today=TODAY)
        assert {o["symbol"] for o in opens} == {"AAA"}


class TestTheMarketFilterIsHonoured:
    def test_a_relative_only_short_is_opened_as_a_PAIR_against_spy(self):
        """The model predicts excess return vs SPY. When the tape has not
        cleared a directional short, the honest expression is the pair — and the
        book must record the SPY leg or its P&L is measuring the market."""
        opens = {o["symbol"]: o
                 for o in mb.candidates(_board(short_expression="relative"),
                                        PRICES, today=TODAY)}
        assert opens["YYY"]["expression"] == "relative"
        assert opens["YYY"]["spy_entry"] == 500.0

    def test_a_cleared_short_is_outright(self):
        opens = {o["symbol"]: o for o in mb.candidates(_board(), PRICES, today=TODAY)}
        assert opens["YYY"]["expression"] == "directional"

    def test_longs_carry_the_spy_reference_either_way(self):
        """A long's outcome is also judged as excess return, so it needs the
        entry level of the thing it is measured against."""
        opens = {o["symbol"]: o for o in mb.candidates(_board(), PRICES, today=TODAY)}
        assert opens["AAA"]["spy_entry"] == 500.0


class TestPricingAPosition:
    def _pos(self, **over):
        p = {"symbol": "AAA", "side": "long", "entry": 100.0, "spy_entry": 500.0,
             "expression": "directional", "opened_on": "2026-08-03",
             "time_stop_on": "2026-08-31", "status": "open"}
        p.update(over)
        return p

    def test_a_long_that_rose_shows_a_gain(self):
        m = mb.mark(self._pos(), 110.0, 500.0)
        assert m["pnl_pct"] == pytest.approx(0.10)

    def test_a_short_that_fell_shows_a_gain(self):
        m = mb.mark(self._pos(side="short", entry=200.0), 180.0, 500.0)
        assert m["pnl_pct"] == pytest.approx(0.10)

    def test_a_relative_position_nets_out_the_market(self):
        """Both legs rise 10%: a directional long shows +10%, the pair shows 0.
        This is the whole reason the SPY entry is stored."""
        outright = mb.mark(self._pos(), 110.0, 550.0)
        pair = mb.mark(self._pos(expression="relative"), 110.0, 550.0)
        assert outright["pnl_pct"] == pytest.approx(0.10)
        assert pair["pnl_pct"] == pytest.approx(0.0, abs=1e-9)

    def test_a_missing_price_leaves_the_mark_unknown_not_flat(self):
        m = mb.mark(self._pos(), None, 500.0)
        assert m["pnl_pct"] is None


class TestClosing:
    def _pos(self, **over):
        p = {"symbol": "AAA", "side": "long", "entry": 100.0, "spy_entry": 500.0,
             "expression": "directional", "opened_on": "2026-08-03",
             "time_stop_on": "2026-08-31", "stop": 92.0, "target": 108.0,
             "status": "open"}
        p.update(over)
        return p

    def test_the_stop_closes_it(self):
        why = mb.close_reason(self._pos(), 91.0, 500.0, dt.date(2026, 8, 10))
        assert why == "stop"

    def test_the_target_closes_it(self):
        assert mb.close_reason(self._pos(), 109.0, 500.0, dt.date(2026, 8, 10)) == "target"

    def test_the_TIME_STOP_closes_it_even_at_a_loss(self):
        """The model predicts 20 trading days. Holding past that turns a
        measured edge into a hope, and a book that does it reports a number the
        model never claimed."""
        assert mb.close_reason(self._pos(), 97.0, 500.0, dt.date(2026, 9, 1)) == "time"

    def test_an_open_position_inside_its_bounds_stays_open(self):
        assert mb.close_reason(self._pos(), 101.0, 500.0, dt.date(2026, 8, 10)) is None

    def test_a_short_stops_out_ABOVE_its_entry(self):
        p = self._pos(side="short", entry=200.0, stop=210.0, target=190.0)
        assert mb.close_reason(p, 211.0, 500.0, dt.date(2026, 8, 10)) == "stop"
        assert mb.close_reason(p, 189.0, 500.0, dt.date(2026, 8, 10)) == "target"

    def test_a_position_with_no_stop_still_honours_the_time_stop(self):
        p = self._pos(stop=None, target=None)
        assert mb.close_reason(p, 97.0, 500.0, dt.date(2026, 9, 1)) == "time"
        assert mb.close_reason(p, 97.0, 500.0, dt.date(2026, 8, 5)) is None


class TestTheSummarySplitsTheSides:
    def _closed(self, side, pnl):
        return {"symbol": "X", "side": side, "status": "closed",
                "pnl_pct": pnl, "close_reason": "time"}

    def test_long_and_short_are_reported_separately(self):
        s = mb.summary([self._closed("long", 0.05), self._closed("long", -0.02),
                        self._closed("short", -0.03)])
        assert s["long"]["n"] == 2 and s["short"]["n"] == 1
        assert s["long"]["mean_pnl"] == pytest.approx(0.015)
        assert s["short"]["mean_pnl"] == pytest.approx(-0.03)

    def test_a_side_with_no_trades_reports_None_not_zero(self):
        s = mb.summary([self._closed("long", 0.05)])
        assert s["short"]["n"] == 0
        assert s["short"]["mean_pnl"] is None

    def test_open_positions_do_not_count_toward_realized(self):
        s = mb.summary([self._closed("long", 0.05),
                        {"symbol": "Y", "side": "long", "status": "open",
                         "pnl_pct": 0.99}])
        assert s["long"]["n"] == 1
        assert s["open"] == 1

    def test_it_reports_the_hit_rate_per_side(self):
        s = mb.summary([self._closed("long", 0.05), self._closed("long", -0.02)])
        assert s["long"]["hit_rate"] == pytest.approx(0.5)

    def test_an_empty_book_is_shaped_but_empty(self):
        s = mb.summary([])
        assert s["long"]["n"] == 0 and s["short"]["n"] == 0 and s["open"] == 0


# ── The tick (compute + store) ───────────────────────────────────────────────

class TestTheTick:
    def _run(self, tmp_path, board, prices, today):
        from services.trade_svc import compute as C
        return C.run_model_book(board=board, prices=prices, today=today,
                                db_path=tmp_path / "book.db")

    def test_it_opens_marks_and_reports(self, tmp_path):
        out = self._run(tmp_path, _board(), PRICES, TODAY)
        syms = {p["symbol"] for p in out["positions"]}
        assert syms == {"AAA", "YYY"}
        assert out["summary"]["open"] == 2

    def test_a_second_tick_the_same_day_does_not_double_up(self, tmp_path):
        """The tick runs more than once a day, so it has to be idempotent —
        and re-opening would reset the entry price to whatever the latest tick
        saw, silently improving every position that had moved against it."""
        self._run(tmp_path, _board(), PRICES, TODAY)
        moved = dict(PRICES, AAA=130.0)
        out = self._run(tmp_path, _board(), moved, TODAY)
        aaa = next(p for p in out["positions"] if p["symbol"] == "AAA")
        assert aaa["entry"] == 100.0
        assert len(out["positions"]) == 2

    def test_a_later_tick_marks_the_open_position(self, tmp_path):
        self._run(tmp_path, _board(), PRICES, TODAY)
        out = self._run(tmp_path, _board(), dict(PRICES, AAA=110.0),
                        TODAY + dt.timedelta(days=1))
        aaa = next(p for p in out["positions"] if p["symbol"] == "AAA")
        assert aaa["pnl_pct"] == pytest.approx(0.10)
        assert aaa["status"] == "open"

    def test_the_time_stop_closes_it_and_it_stops_being_open(self, tmp_path):
        self._run(tmp_path, _board(), PRICES, TODAY)
        out = self._run(tmp_path, _board(), dict(PRICES, AAA=105.0),
                        TODAY + dt.timedelta(days=60))
        aaa = next(p for p in out["positions"] if p["symbol"] == "AAA")
        assert aaa["status"] == "closed" and aaa["close_reason"] == "time"
        assert out["summary"]["open"] == 0
        assert out["summary"]["closed"] == 2

    def test_a_name_already_held_is_not_re_entered_on_a_later_day(self, tmp_path):
        self._run(tmp_path, _board(), PRICES, TODAY)
        out = self._run(tmp_path, _board(as_of="2026-08-04"), PRICES,
                        TODAY + dt.timedelta(days=1))
        assert len([p for p in out["positions"] if p["symbol"] == "AAA"]) == 1

    def test_it_is_skipped_under_pytest_without_an_explicit_path(self):
        from services.trade_svc import compute as C
        out = C.run_model_book(board=_board(), prices=PRICES, today=TODAY)
        assert out["positions"] == []


class TestTheTickFetchesItsOwnPrices:
    def test_a_tick_with_no_injected_prices_still_opens(self, tmp_path, monkeypatch):
        """The bug live verification found. Candidates were built BEFORE the
        quotes were fetched, so on the real path every one was dropped for want
        of a price and the book stayed empty — while every unit test passed,
        because they all inject prices."""
        from services.trade_svc import compute as C
        monkeypatch.setattr(C, "_quotes_for", lambda syms: dict(PRICES))
        out = C.run_model_book(board=_board(), prices=None, today=TODAY,
                               db_path=tmp_path / "book.db")
        assert {p["symbol"] for p in out["positions"]} == {"AAA", "YYY"}

    def test_it_quotes_only_the_symbols_involved(self, tmp_path, monkeypatch):
        """Not the whole universe: the board has 78 rows and the book needs the
        two it would trade plus SPY."""
        from services.trade_svc import compute as C
        asked = {}

        def _q(syms):
            asked["syms"] = set(syms)
            return dict(PRICES)

        monkeypatch.setattr(C, "_quotes_for", _q)
        C.run_model_book(board=_board(), prices=None, today=TODAY,
                         db_path=tmp_path / "book.db")
        assert asked["syms"] == {"AAA", "YYY", "SPY"}
        assert "BBB" not in asked["syms"]      # gated - never even quoted


class TestWantedSymbols:
    def test_it_names_the_ungated_pool_members(self):
        assert set(mb.wanted_symbols(_board())) == {"AAA", "YYY"}

    def test_a_broken_board_wants_nothing(self):
        assert mb.wanted_symbols({"status": "legacy_snapshot"}) == []
        assert mb.wanted_symbols(None) == []
