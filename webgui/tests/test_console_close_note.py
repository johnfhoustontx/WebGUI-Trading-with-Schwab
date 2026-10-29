"""Outside regular hours the Regime and Today trend cards say WHEN they were read.

Both are computed from regular-hours data only: the regime is recomputed on a
5-minute RTH slot and persists overnight, and the trend asks Schwab for
regular-hours bars, so before the open "today's session" is yesterday's. On
2026-10-29 at 05:50 CT the console printed yesterday's "Firming" beside a
falling premarket with nothing to say it was yesterday's.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from pages import console_cards as CC
from pages import console_page as CP

CT = ZoneInfo("America/Chicago")
THU_PREMARKET = dt.datetime(2026, 10, 29, 5, 50, tzinfo=CT)
REGIME_AS_OF = "2026-10-28T19:56:32.285406+00:00"        # Wed 14:56 CT


# ── the trend: no timestamp of its own, so the calendar names the session ────

def test_premarket_names_the_previous_session():
    assert CP.close_note(THU_PREMARKET) == "AT WED CLOSE"


def test_after_the_close_names_today():
    assert CP.close_note(dt.datetime(2026, 10, 29, 18, 0, tzinfo=CT)) == "AT THU CLOSE"


def test_a_weekend_names_friday():
    assert CP.close_note(dt.datetime(2026, 10, 31, 11, 0, tzinfo=CT)) == "AT FRI CLOSE"


def test_a_monday_premarket_names_friday():
    assert CP.close_note(dt.datetime(2026, 11, 2, 7, 0, tzinfo=CT)) == "AT FRI CLOSE"


def test_regular_hours_say_nothing():
    assert CP.close_note(dt.datetime(2026, 10, 29, 10, 0, tzinfo=CT)) == ""


# ── the regime: its own as_of, stated exactly ───────────────────────────────

def test_the_regime_states_its_own_time():
    assert CP.close_note(THU_PREMARKET, as_of=REGIME_AS_OF) == "AS OF WED 14:56 CT"


def test_the_regime_says_nothing_during_regular_hours():
    assert CP.close_note(dt.datetime(2026, 10, 29, 10, 0, tzinfo=CT),
                         as_of=REGIME_AS_OF) == ""


def test_an_unreadable_as_of_falls_back_to_the_session():
    for bad in ("nonsense", 12, None, ""):
        assert CP.close_note(THU_PREMARKET, as_of=bad) == "AT WED CLOSE"


# ── what the cards print ────────────────────────────────────────────────────

def test_the_trend_card_meta_is_the_note_off_hours():
    assert CC.card_meta("AT WED CLOSE") == "AT WED CLOSE"


def test_the_trend_card_meta_is_the_scale_in_regular_hours():
    assert CC.card_meta("") == "SCALE 0—100"


def test_the_console_hands_both_cards_a_note():
    """``apply`` is the only place the cards are built; the notes must reach it."""
    import inspect
    src = inspect.getsource(CP.apply)
    assert 'ctx.get("trend_close")' in src
    assert 'ctx.get("regime_close")' in src


def test_the_sentiment_page_builds_both_notes():
    import inspect
    from pages import sentiment as S
    src = inspect.getsource(S)
    assert '"trend_close": console_page.close_note()' in src
    assert '"regime_close": console_page.close_note(' in src
