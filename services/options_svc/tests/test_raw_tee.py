"""``tee``: today's collector also writes each collected minute to the raw store.

docs/plans/2026-10-14-raw-feed-and-catch-up-design.md §7. Nothing here may cost
a Schwab call or break collection: the minute is the one the poll already
fetched, and a store failure is a degrade, never an exception."""
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import compute, raw_tee
from services.options_svc.tests.test_collection_tiers import RTH, _collector
from shared import feed_config, raw_store
from shared.raw_minute import RawMinute

CT = ZoneInfo("America/Chicago")
TS = int(dt.datetime(2026, 10, 14, 10, 42, tzinfo=CT).timestamp())


def _minute(ts=TS):
    return RawMinute(ts=ts, chains=[("SPY", {"symbol": "SPY"})], symbols=["SPY"])


def test_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "off")
    assert raw_tee.maybe_write(_minute(), root=tmp_path) is False
    assert raw_store.sessions(root=tmp_path) == []


def test_on_writes_nothing_either(tmp_path, monkeypatch):
    # In "on" feed_svc owns the raw store; the collector must not write it too.
    monkeypatch.setattr(feed_config, "mode", lambda: "on")
    assert raw_tee.maybe_write(_minute(), root=tmp_path) is False
    assert raw_store.sessions(root=tmp_path) == []


def test_tee_writes_the_minute_under_its_ct_date(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")
    assert raw_tee.maybe_write(_minute(), root=tmp_path) is True
    assert raw_store.complete_minutes("2026-10-14", root=tmp_path) == [TS]


def test_a_late_evening_minute_files_under_its_ct_date_not_utc(tmp_path, monkeypatch):
    # 19:30 CT is already the next day in UTC.
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")
    late = int(dt.datetime(2026, 10, 14, 19, 30, tzinfo=CT).timestamp())
    raw_tee.maybe_write(_minute(late), root=tmp_path)
    assert raw_store.sessions(root=tmp_path) == ["2026-10-14"]


def test_a_store_failure_never_reaches_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(raw_store, "write_minute", _boom)
    assert raw_tee.maybe_write(_minute(), root=tmp_path) is False


def test_old_sessions_are_pruned_once_a_new_session_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")
    monkeypatch.setattr(feed_config, "keep_sessions", lambda: 2)
    monkeypatch.setattr(raw_tee, "_PRUNED", set())
    for day in (9, 10, 13, 14):
        ts = int(dt.datetime(2026, 10, day, 10, 0, tzinfo=CT).timestamp())
        raw_tee.maybe_write(_minute(ts), root=tmp_path)
    assert raw_store.sessions(root=tmp_path) == ["2026-10-13", "2026-10-14"]


def test_the_collection_hands_the_poll_a_minute_writer_only_in_tee(monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "off")
    rec = _collector(monkeypatch)
    compute.collect_gex_snapshots(now=RTH)
    assert "on_minute" not in rec["kw"]

    monkeypatch.setattr(feed_config, "mode", lambda: "tee")
    rec = _collector(monkeypatch)
    compute.collect_gex_snapshots(now=RTH)
    assert rec["kw"]["on_minute"] is raw_tee.maybe_write


def test_off_keeps_the_collector_signature_it_had(monkeypatch):
    # A stand-in with the pre-feed signature must still be called cleanly.
    monkeypatch.setattr(feed_config, "mode", lambda: "off")
    rec = _collector(monkeypatch, strict=True)
    compute.collect_gex_snapshots(now=RTH)
    assert rec["poll_n"] == 1
