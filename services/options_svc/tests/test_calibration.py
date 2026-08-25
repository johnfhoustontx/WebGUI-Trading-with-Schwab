"""Tests for the calibration payload published to cache:options:calibration.

This is the ONLY number in the Trade detail panel whose `p` does not come from
the option's own price, so it is the only one that earns the word
"recommendation". Everything here exists to stop it saying more than the data
supports.

Run from the repo root:
    .venv\\Scripts\\python -m pytest services\\options_svc\\tests\\test_calibration.py -v
"""
import pytest

from services.options_svc import calibration as K


def _row(pnl, *, score=63.0, family="0DTE", max_loss=1.0, credit=1.0,
         delta=-0.20, day="2026-06-01", **kw):
    row = {"realized_pnl": pnl, "entry_max_loss": max_loss, "entry_credit": credit,
           "entry_short_delta": delta, "entry_score": score, "scanner_type": family,
           "strategy": "PCS", "symbol": "SPY", "first_seen_date": day,
           "exit_reason": "EXPIRED", "entry_grade": "Good"}
    row.update(kw)
    return row


def _spread(n, pnl, **kw):
    """n rows across DISTINCT days, with a little dispersion.

    ⚠ Identical returns give a bucket zero variance, so its t-stat is undefined
    and it declines to speak -- correct behaviour, but a degenerate fixture. The
    jitter keeps these fixtures realistic; `_mixed` is for testing the gate.
    """
    return [_row(pnl * (1 + 0.10 * ((i % 5) - 2)), day=f"2026-06-{1 + (i % 28):02d}", **kw)
            for i in range(n)]


def _mixed(wins, losses, *, win=50.0, loss=-100.0, **kw):
    """A realistic bucket: mostly wins at +win, the rest losing -loss, all on
    distinct days so the day clustering has something to work with."""
    return ([_row(win, day=f"2026-06-{1 + (i % 28):02d}", **kw) for i in range(wins)]
            + [_row(loss, day=f"2026-07-{1 + (i % 28):02d}", **kw) for i in range(losses)])


class TestBucketKeys:
    def test_buckets_are_keyed_family_then_score_bin(self):
        out = K.build_calibration(_spread(20, 50.0, score=63.0), min_n=5)
        assert "0DTE|60-65" in out["buckets"]

    def test_the_page_side_spelling_of_the_family_finds_the_same_bucket(self):
        """The DB says '0DTE', the page says '0-DTE'. If these ever key
        differently the panel silently shows nothing for its richest family."""
        from shared.calibration import bucket_key
        out = K.build_calibration(_spread(20, 50.0, score=63.0), min_n=5)
        assert bucket_key("0-DTE", 63.0) in out["buckets"]

    def test_two_families_do_not_share_a_bucket(self):
        rows = _spread(20, 50.0, family="0DTE") + _spread(20, -50.0, family="SWING")
        out = K.build_calibration(rows, min_n=5)
        assert out["buckets"]["0DTE|60-65"]["ev_r"] > 0
        assert out["buckets"]["SWING|60-65"]["ev_r"] < 0

    def test_a_row_with_no_usable_family_or_score_is_dropped(self):
        rows = _spread(20, 50.0) + [_row(50.0, family=None), _row(50.0, score=None)]
        out = K.build_calibration(rows, min_n=5)
        assert sum(b["n"] for b in out["buckets"].values()) == 20


class TestWhatTheBucketIsAllowedToSay:
    def test_a_bucket_thinner_than_min_n_is_dropped_entirely(self):
        assert K.build_calibration(_spread(4, 50.0), min_n=15)["buckets"] == {}

    def test_a_bucket_inside_the_t_gate_is_marked_as_not_speaking(self):
        """An EV we cannot distinguish from zero is not a recommendation. The
        bucket is still PUBLISHED -- the page may want to say how thin it is --
        but `speaks` is the flag that gates the sentence."""
        rows = ([_row(50.0, day=f"2026-06-{i:02d}") for i in range(1, 11)]
                + [_row(-52.0, day=f"2026-07-{i:02d}") for i in range(1, 11)])
        b = K.build_calibration(rows, min_n=5, t_gate=2.0)["buckets"]["0DTE|60-65"]
        assert abs(b["t_day"]) < 2.0
        assert b["speaks"] is False

    def test_a_clean_positive_bucket_speaks(self):
        b = K.build_calibration(_mixed(28, 4), min_n=5)["buckets"]["0DTE|60-65"]
        assert b["t_day"] > 2.0 and b["speaks"] is True

    def test_a_bucket_with_no_dispersion_declines_to_speak(self):
        """Every trade returning exactly the same R leaves the t-stat undefined.
        Undefined is not evidence, so it must not speak."""
        rows = [_row(50.0, day=f"2026-06-{i:02d}") for i in range(1, 21)]
        b = K.build_calibration(rows, min_n=5)["buckets"]["0DTE|60-65"]
        assert b["t_day"] is None and b["speaks"] is False

    def test_a_bucket_spanning_one_day_never_speaks(self):
        """20 signals from a single scan are one bet, not twenty."""
        rows = [_row(50.0, day="2026-06-01") for _ in range(20)]
        b = K.build_calibration(rows, min_n=5)["buckets"]["0DTE|60-65"]
        assert b["days"] == 1 and b["speaks"] is False


class TestThePayloadItself:
    def test_it_carries_no_per_trade_rows(self):
        """This repo has two documented unbounded-payload incidents (a 4.99 MB
        gamma key, an 8.77 MB chain). A calibration payload is ~10 small buckets
        and must never grow with the trade count."""
        out = K.build_calibration(_spread(300, 50.0), min_n=5)
        blob = repr(out)
        assert "realized_pnl" not in blob and "symbol" not in blob
        assert len(blob) < 4000

    def test_the_payload_does_not_grow_with_the_number_of_trades(self):
        """20x the trades must not mean a materially larger payload. It is not
        byte-identical -- `n` and `rows` carry more digits -- but it must stay
        within a handful of characters, never scale."""
        small = len(repr(K.build_calibration(_spread(30, 50.0), min_n=5)))
        large = len(repr(K.build_calibration(_spread(600, 50.0), min_n=5)))
        assert large - small < 10

    def test_it_carries_no_timestamp_so_an_unchanged_night_can_skip_the_write(self):
        """cache_set(skip_unchanged=True) compares payloads. A computed_at would
        force a write every night even when nothing moved; the bus already
        stamps a `{key}:ts` side key."""
        a = K.build_calibration(_spread(20, 50.0), min_n=5)
        b = K.build_calibration(_spread(20, 50.0), min_n=5)
        assert a == b

    def test_it_records_the_gates_it_was_built_with(self):
        out = K.build_calibration(_spread(20, 50.0), min_n=7, t_gate=1.5)
        assert out["min_n"] == 7 and out["t_gate"] == 1.5

    def test_no_rows_yields_an_empty_payload_rather_than_a_raise(self):
        out = K.build_calibration([])
        assert out["buckets"] == {} and out["rows"] == 0

    def test_junk_rows_do_not_raise(self):
        assert K.build_calibration([None, 42, {}, {"entry_score": "x"}])["buckets"] == {}


class TestLoad:
    def test_an_unreadable_database_yields_an_empty_payload_not_an_exception(self, tmp_path):
        """A missing signals.db is the fresh-clone case. The service must publish
        an empty calibration and carry on, never take the nightly slot down."""
        out = K.load_and_build(str(tmp_path / "nope.db"))
        assert out["buckets"] == {} and out["rows"] == 0


class TestTheNightlySlot:
    """Mirrors sentiment_svc's momentum slot: once per session date, on a trading
    day, at or after the configured time. `last_session` is the sentinel, so a
    late start still catches the day and a restart cannot re-run it."""

    @staticmethod
    def _at(y, m, d, hh, mm):
        import datetime as dt
        from zoneinfo import ZoneInfo
        return dt.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/Chicago"))

    def test_it_does_not_fire_before_its_time(self):
        from services.options_svc import scheduler
        run, _ = scheduler.calibration_due(self._at(2026, 8, 25, 15, 0), None)
        assert run is False

    def test_it_fires_once_after_its_time(self):
        from services.options_svc import scheduler
        run, session = scheduler.calibration_due(self._at(2026, 8, 25, 16, 45), None)
        assert run is True and session == "2026-08-25"

    def test_it_does_not_fire_twice_in_one_session(self):
        from services.options_svc import scheduler
        now = self._at(2026, 8, 25, 16, 45)
        _, session = scheduler.calibration_due(now, None)
        run, _ = scheduler.calibration_due(now, session)
        assert run is False

    def test_a_late_start_still_catches_the_day(self):
        from services.options_svc import scheduler
        run, _ = scheduler.calibration_due(self._at(2026, 8, 25, 22, 10), None)
        assert run is True

    def test_it_never_fires_on_a_holiday(self):
        from services.options_svc import scheduler
        run, _ = scheduler.calibration_due(self._at(2026, 7, 3, 17, 0), None)
        assert run is False          # Independence Day observed

    def test_it_runs_after_the_gex_collector_has_stopped(self):
        """Collection stops at 15:20 CT. Recomputing while the day's outcomes are
        still landing would publish a bucket table that changes under the reader."""
        import shared.market_calendar as mc
        assert mc.slot_times("calibration")["at"] > mc.window_bounds("collection")[1]
