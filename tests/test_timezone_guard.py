"""The stack refuses to start on a host whose clock is not Central.

WHY THIS EXISTS. 95 naive ``datetime.now()`` / ``date.today()`` calls in
non-test code mean CENTRAL time — that is a documented project-wide invariant,
and 78 modules build on it. On Windows it held because the box was set to CT.
Nothing enforced it.

On a Linux host it is one command to get right and one omission to get wrong,
and getting it wrong is SILENT: every session window, roll-date and expiry
calculation shifts five or six hours and each one degrades to a plausible
number rather than an error. A container makes it worse — UTC is the default.
This is the same class as the naive-vs-aware settlement bug that priced 0-DTE
options an hour past the close, except it would hit all 95 sites at once.

A stack that refuses to start is recoverable in seconds. One that runs on the
wrong clock looks healthy for a day.
"""
import datetime as dt
import sys
from zoneinfo import ZoneInfo

import pytest

import repo_paths


CT = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc

# One instant in CDT (UTC-5) and one in CST (UTC-6), so the guard is pinned
# across the DST boundary rather than only in the half of the year it was
# written in.
SUMMER = dt.datetime(2026, 8, 29, 18, 0, tzinfo=UTC)   # CDT, -5
WINTER = dt.datetime(2026, 12, 15, 18, 0, tzinfo=UTC)  # CST, -6


@pytest.mark.parametrize("when,offset_hours", [(SUMMER, -5), (WINTER, -6)])
def test_a_central_offset_is_accepted_in_both_dst_halves(when, offset_hours):
    assert repo_paths._is_central(dt.timedelta(hours=offset_hours), when)


@pytest.mark.parametrize("when", [SUMMER, WINTER])
def test_utc_is_rejected(when):
    """The container default, and the one that silently shifts every naive call."""
    assert not repo_paths._is_central(dt.timedelta(0), when)


def test_the_wrong_half_of_the_year_is_rejected(when=SUMMER):
    """-6 is Central in December and NOT Central in August. Comparing against a
    fixed offset instead of the zone's offset AT THAT INSTANT would pass this."""
    assert not repo_paths._is_central(dt.timedelta(hours=-6), SUMMER)
    assert not repo_paths._is_central(dt.timedelta(hours=-5), WINTER)


def test_the_check_is_against_the_zone_not_a_hardcoded_number():
    """Non-vacuity: the zone really does move between the two instants, so the
    two tests above are testing something."""
    assert SUMMER.astimezone(CT).utcoffset() != WINTER.astimezone(CT).utcoffset()


def test_the_guard_is_inert_under_pytest():
    """`repo_paths` is imported by ~40 modules and by conftest at COLLECTION
    time. If the guard fired there, a CI runner or a contributor's laptop on any
    other zone could not even collect the suite.

    ⚠ It keys on ``"pytest" in sys.modules``, NOT ``PYTEST_CURRENT_TEST``.
    Measured 2026-08-29: that env var is absent at import/collection time and
    only set once a test is RUNNING, so keying on it would leave the guard live
    during collection — exactly when repo_paths is imported."""
    assert "pytest" in sys.modules
    assert repo_paths.assert_central_time() is None   # does not raise


def test_forcing_the_guard_on_a_wrong_clock_raises_and_says_what_is_wrong(monkeypatch):
    """The message has to carry both offsets. 'wrong timezone' with no numbers
    sends someone to the wrong host setting."""
    monkeypatch.setattr(repo_paths, "_is_central", lambda *a, **k: False)
    with pytest.raises(RuntimeError) as e:
        repo_paths.assert_central_time(_force=True)
    msg = str(e.value)
    assert "America/Chicago" in msg
    assert "timedatectl set-timezone" in msg   # the actual fix, not just the fault


def test_forcing_the_guard_on_a_right_clock_is_silent(monkeypatch):
    """Non-vacuity partner: _force alone must not raise."""
    monkeypatch.setattr(repo_paths, "_is_central", lambda *a, **k: True)
    assert repo_paths.assert_central_time(_force=True) is None


def test_the_zone_name_is_the_one_the_pricing_code_already_uses():
    """Not a new constant. `options_calculator.NAIVE_WALLCLOCK_TZ` and
    `shared/market_calendar` already settle on this zone; a second spelling
    here would be a mirror free to drift."""
    assert repo_paths.NAIVE_WALLCLOCK_TZ == "America/Chicago"


@pytest.mark.parametrize("hours,shown", [
    (-5, "UTC-05:00"), (-6, "UTC-06:00"), (0, "UTC+00:00"), (5.5, "UTC+05:30"),
])
def test_offsets_render_readably(hours, shown):
    """`str(timedelta(hours=-5))` is `-1 day, 19:00:00` — correct, and useless
    in a message whose whole job is to say which way the clock is wrong."""
    assert repo_paths._fmt_offset(dt.timedelta(hours=hours)) == shown


def test_a_missing_offset_does_not_crash_the_error_path():
    """A naive astimezone() can yield None. The guard must still be able to
    RAISE — an exception inside the error path would replace a clear diagnosis
    with a confusing one."""
    assert repo_paths._fmt_offset(None) == "unknown"
