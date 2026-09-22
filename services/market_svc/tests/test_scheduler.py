import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.market_svc import scheduler as sch

_CT = ZoneInfo("America/Chicago")


def test_poll_interval_values():
    # A macro dashboard updates tiles in place, so a slightly slower cadence is
    # imperceptible but roughly halves the Schwab /quotes volume (~24k → ~12k/day).
    # Weekend stays hard-throttled (futures closed → nothing ticks).
    assert sch.RTH_INTERVAL_SEC == 3
    assert sch.OFFHOURS_INTERVAL_SEC == 15
    assert sch.WEEKEND_INTERVAL_SEC == 60


def test_fast_cadence_during_rth():
    now = dt.datetime(2026, 7, 7, 10, 0, tzinfo=_CT)  # Tue 10:00 CT
    assert sch.poll_interval(now) == sch.RTH_INTERVAL_SEC


def test_slow_cadence_off_hours():
    now = dt.datetime(2026, 7, 7, 22, 0, tzinfo=_CT)  # Tue 22:00 CT
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


def test_slow_cadence_on_weekend():
    # Saturday: the futures are CLOSED (they don't reopen until Sun 17:00 CT), so
    # nothing ticks — throttle harder than the normal off-hours pace.
    now = dt.datetime(2026, 7, 11, 10, 0, tzinfo=_CT)  # Sat
    assert sch.poll_interval(now) == sch.WEEKEND_INTERVAL_SEC


def test_slow_cadence_on_holiday():
    now = dt.datetime(2026, 7, 3, 10, 0, tzinfo=_CT)  # NYSE holiday
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


_REPORT = """<html><body><div class="slotchip">Market close &middot; 16:20 CT</div>
<h1>A rotation, not a rout</h1>
<section class="sec"><h2>Chips broke</h2></section>
<section class="sec"><h2>Software ripped</h2></section></body></html>"""


def _write_report(d, html=_REPORT, txt="2026-09-14 5 close 16:20CT\n"):
    (d / "latest.html").write_text(html, encoding="utf-8")
    (d / "latest.txt").write_text(txt, encoding="utf-8")


def test_the_summary_is_published_from_the_report(tmp_path):
    from shared.bus import Bus
    from services.market_svc import handlers
    bus = Bus()
    _write_report(tmp_path)
    stamp = sch.refresh_summary(bus, None, reports_dir=tmp_path)
    assert stamp is not None
    env = bus.cache_get(handlers.CACHE_SUMMARY)
    assert env.payload["highlights"] == ["Chips broke", "Software ripped"]
    assert env.payload["slot"] == "close"


def test_an_unchanged_report_is_not_republished(tmp_path, monkeypatch):
    from shared.bus import Bus
    from services.market_svc import handlers
    bus = Bus()
    _write_report(tmp_path)
    stamp = sch.refresh_summary(bus, None, reports_dir=tmp_path)
    calls = []
    monkeypatch.setattr(handlers, "publish_summary", lambda *a: calls.append(a))
    assert sch.refresh_summary(bus, stamp, reports_dir=tmp_path) == stamp
    assert calls == []


def test_a_replaced_report_is_republished(tmp_path):
    import os
    from shared.bus import Bus
    from services.market_svc import handlers
    bus = Bus()
    _write_report(tmp_path)
    stamp = sch.refresh_summary(bus, None, reports_dir=tmp_path)
    _write_report(tmp_path, html=_REPORT.replace("Chips broke", "Chips recovered"))
    later = os.stat(tmp_path / "latest.html").st_mtime_ns + 10**9
    os.utime(tmp_path / "latest.html", ns=(later, later))
    assert sch.refresh_summary(bus, stamp, reports_dir=tmp_path) != stamp
    assert bus.cache_get(handlers.CACHE_SUMMARY).payload["highlights"][0] == "Chips recovered"


def test_no_report_publishes_nothing(tmp_path):
    from shared.bus import Bus
    from services.market_svc import handlers
    bus = Bus()
    assert sch.refresh_summary(bus, None, reports_dir=tmp_path) is None
    assert bus.cache_get(handlers.CACHE_SUMMARY) is None


def test_an_unparseable_report_keeps_the_last_summary_and_is_not_reread(tmp_path, monkeypatch):
    import os
    from shared.bus import Bus
    from services.market_svc import handlers, report_summary
    bus = Bus()
    _write_report(tmp_path)
    sch.refresh_summary(bus, None, reports_dir=tmp_path)
    _write_report(tmp_path, html="<html><body>half a page")
    later = os.stat(tmp_path / "latest.html").st_mtime_ns + 10**9
    os.utime(tmp_path / "latest.html", ns=(later, later))
    stamp = sch.refresh_summary(bus, "old", reports_dir=tmp_path)
    assert stamp != "old"
    assert bus.cache_get(handlers.CACHE_SUMMARY).payload["headline"] == "A rotation, not a rout"
    reads = []
    monkeypatch.setattr(report_summary, "read_report", lambda **k: reads.append(k))
    sch.refresh_summary(bus, stamp, reports_dir=tmp_path)
    assert reads == []


def test_the_loop_makes_no_claude_call():
    """The change-driven Claude sentence was retired 2026-09-16."""
    import inspect
    src = inspect.getsource(sch)
    assert "generate_summary" not in src and "anthropic" not in src.lower()
    assert "refresh_summary" in inspect.getsource(sch.loop)


def test_poll_interval_throttles_deep_weekend():
    import datetime as dt
    # Saturday: futures closed all day -> slow throttle.
    sat = dt.datetime(2026, 7, 18, 10, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sat) == sch.WEEKEND_INTERVAL_SEC
    # Sunday morning: still closed (futures reopen 17:00 CT Sunday).
    sun_am = dt.datetime(2026, 7, 19, 10, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sun_am) == sch.WEEKEND_INTERVAL_SEC
    # Sunday evening after the futures reopen: back to the normal off-hours pace.
    sun_pm = dt.datetime(2026, 7, 19, 18, 0, tzinfo=sch._CT)
    assert sch.poll_interval(sun_pm) == sch.OFFHOURS_INTERVAL_SEC


def _x_commands(bus):
    """Every x_post_report command enqueued on cmd:options, decoded."""
    from shared.contracts.envelope import Command
    out = []
    for _id, fields in bus._r.xrange("cmd:options"):
        cmd = Command.from_json(fields["data"])
        if cmd.type == "x_post_report":
            out.append(cmd)
    return out


def test_a_new_report_is_handed_to_options_svc_for_x(tmp_path):
    import os
    from shared.bus import Bus
    bus = Bus()
    _write_report(tmp_path)
    stamp = sch.refresh_summary(bus, None, reports_dir=tmp_path)
    cmds = _x_commands(bus)
    assert len(cmds) == 1
    args = cmds[0].args
    assert args["report"]["headline"] == "A rotation, not a rout"
    assert isinstance(args["mtime"], float)
    assert args["mtime"] == os.stat(tmp_path / "latest.html").st_mtime
    # The same report, seen again, is not handed over twice.
    assert sch.refresh_summary(bus, stamp, reports_dir=tmp_path) == stamp
    assert len(_x_commands(bus)) == 1


def test_an_unparseable_report_is_not_handed_to_x(tmp_path):
    from shared.bus import Bus
    bus = Bus()
    _write_report(tmp_path, html="<html><body>half a page")
    assert sch.refresh_summary(bus, None, reports_dir=tmp_path) is not None
    assert _x_commands(bus) == []


def test_an_x_enqueue_failure_still_publishes_the_summary(tmp_path, monkeypatch):
    from shared.bus import Bus
    from services.market_svc import handlers
    bus = Bus()
    _write_report(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("redis down")
    monkeypatch.setattr(bus, "enqueue_command", boom)
    stamp = sch.refresh_summary(bus, None, reports_dir=tmp_path)
    assert stamp is not None
    assert bus.cache_get(handlers.CACHE_SUMMARY).payload["headline"] == "A rotation, not a rout"
