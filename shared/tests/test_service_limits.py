"""How old a command may be before a service refuses it (audit AR-05)."""
import datetime as dt

import pytest

from shared import service_limits as cl


def _cmd(seconds=None, ts="auto"):
    class C:
        type = "x"
    c = C()
    if ts == "auto":
        c.ts = (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(seconds=seconds)).isoformat()
    else:
        c.ts = ts
    return c


def test_the_shipped_limits():
    assert cl.side_effect_max_sec() == 180
    assert cl.replay_max_sec() == 900
    assert cl.side_effect_max_sec() < cl.replay_max_sec()


@pytest.mark.parametrize("bad", [0, -5, True, "180", float("nan"), None, 1.5e12])
def test_an_unusable_limit_reads_as_the_shipped_one(monkeypatch, bad):
    monkeypatch.setattr(cl, "load", lambda: {"age": {"side_effect_max_sec": bad,
                                                     "replay_max_sec": bad}})
    assert cl.side_effect_max_sec() == 180
    assert cl.replay_max_sec() == 900


def test_age_is_seconds_since_the_enqueue_stamp():
    assert cl.age_seconds(_cmd(120)) == pytest.approx(120, abs=2)


@pytest.mark.parametrize("ts", [None, "", "not a date", 12345])
def test_no_readable_stamp_is_no_age(ts):
    assert cl.age_seconds(_cmd(ts=ts)) is None


def test_a_naive_stamp_is_read_as_utc():
    naive = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=60)
             ).replace(tzinfo=None).isoformat()
    assert cl.age_seconds(_cmd(ts=naive)) == pytest.approx(60, abs=2)


def test_older_than_judges_only_a_known_age():
    assert cl.older_than(_cmd(1000), 900) is True
    assert cl.older_than(_cmd(100), 900) is False
    assert cl.older_than(_cmd(ts=None), 900) is False
