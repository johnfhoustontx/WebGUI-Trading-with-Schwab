"""``tools/cache_age.py`` -- the staleness reporter.

Everything here runs against the fake bus, which under pytest is shared per test
(see the Test infrastructure notes in CLAUDE.md), so these exercise the real read
path rather than a mock of it.
"""
import datetime
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from shared.bus import Bus  # noqa: E402
from tools import cache_age  # noqa: E402


def test_the_cache_prefix_is_optional():
    """Requiring `cache:` on every argument is ceremony -- it is on every key.
    But a key pasted from a log or from this repo's docs carries it, so both
    spellings have to land on the same view."""
    assert cache_age.normalize_key("sentiment:momentum") == "cache:sentiment:momentum"
    assert cache_age.normalize_key("cache:sentiment:momentum") == "cache:sentiment:momentum"
    assert cache_age.normalize_key("  options:matrix  ") == "cache:options:matrix"


def test_age_is_formatted_at_the_scale_that_matters():
    """The useful question changes with scale: a 90-second tick view is fine, a
    90-minute one is not, and a nightly view is SUPPOSED to be hours old."""
    assert cache_age.format_age(12) == "12s ago"
    assert cache_age.format_age(600) == "10 min ago"
    assert cache_age.format_age(7200) == "2.0 h ago"
    assert cache_age.format_age(200000) == "2.3 days ago"
    assert cache_age.format_age(None) == "never written"


def test_a_view_that_was_never_written_is_not_reported_as_fresh():
    """The failure this whole script exists to surface. A missing key must never
    render as an age -- `0s ago` on a view that has never been written would be
    the exact opposite of the truth."""
    bus = Bus(fake=True)
    rows = cache_age.ages(bus, ["cache:nothing:here"])
    assert rows == [("cache:nothing:here", None, None)]
    assert "never written" in "\n".join(cache_age.render(rows))


def test_age_is_measured_from_the_ts_side_key_not_the_payload():
    """`cache_set` writes a tiny `{key}:ts` beside the payload precisely so a
    freshness check costs one small read. Deserialising a 5 MB view to read its
    timestamp would cost more than the thing being checked."""
    bus = Bus(fake=True)
    written = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=42)
    bus._r.set("cache:demo:view:ts", written.isoformat())
    # Deliberately NO payload written -- the age must still resolve.
    rows = cache_age.ages(bus, ["cache:demo:view"])
    key, secs, raw = rows[0]
    assert key == "cache:demo:view"
    assert 2500 < secs < 2600
    assert raw == written.isoformat()


def test_discover_lists_views_not_their_side_keys():
    """`keys('cache:*')` returns the `:ts` and `:ver` side keys too. Reporting
    `cache:a:b:ts` as though it were a view would triple the output and name
    things that are not views."""
    bus = Bus(fake=True)
    bus._r.set("cache:a:one:ts", "2026-08-31T00:00:00+00:00")
    bus._r.set("cache:a:one:ver", "7")
    bus._r.set("cache:a:one", "{}")
    bus._r.set("cache:b:two:ts", "2026-08-31T00:00:00+00:00")
    assert cache_age.discover(bus) == ["cache:a:one", "cache:b:two"]


def test_stale_views_are_marked_and_fresh_ones_are_not():
    rows = [("cache:fresh", 60, "x"), ("cache:old", 60 * 60 * 5, "x")]
    out = "\n".join(cache_age.render(rows, stale_after_min=30))
    assert "cache:fresh" in out and "stale?" not in out.split("\n")[0]
    assert "stale?" in [ln for ln in out.split("\n") if "cache:old" in ln][0]


def test_an_auth_error_names_the_missing_environment_not_just_the_symptom():
    """Redis gained a requirepass on 2026-08-31. The raw exception talks about
    HELLO and AUTH and never mentions MEMURAI_PASSWORD, which is the actual
    cause -- so the operator reads a protocol error and learns nothing. This is
    the one piece of behaviour that justifies the script over a one-liner."""
    class Boom:
        def __init__(self):
            raise RuntimeError("HELLO must be called with the client already authenticated")

    real, cache_age.Bus = cache_age.Bus, Boom
    try:
        import io
        err, sys.stderr = sys.stderr, io.StringIO()
        try:
            rc = cache_age.main([])
            message = sys.stderr.getvalue()
        finally:
            sys.stderr = err
    finally:
        cache_age.Bus = real
    assert rc == 2
    assert "MEMURAI_PASSWORD" in message or ". ./.env" in message


def test_discover_mode_does_not_mark_anything_stale():
    """The cadences here span 30s to nightly, so one threshold flags about half
    the views and a marker that fires on everything says nothing. Naming a key
    IS the expectation; discovering them all is not."""
    rows = [("cache:nightly", 60 * 60 * 20, "x"), ("cache:tick", 5, "x")]
    assert "stale?" not in "\n".join(cache_age.render(rows, stale_after_min=None))
    assert "stale?" in "\n".join(cache_age.render(rows, stale_after_min=30))


def test_named_keys_are_judged_but_discovered_ones_are_not(monkeypatch):
    """The distinction has to hold end to end, not just in render()."""
    import datetime
    bus = Bus(fake=True)
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(hours=20)).isoformat()
    bus._r.set("cache:nightly:thing:ts", old)
    monkeypatch.setattr(cache_age, "Bus", lambda *a, **k: bus)

    import io
    for argv, expect_mark in (["nightly:thing"], True), ([], False):
        out, sys.stdout = sys.stdout, io.StringIO()
        try:
            cache_age.main(argv)
            printed = sys.stdout.getvalue()
        finally:
            sys.stdout = out
        assert ("stale?" in printed) is expect_mark, (argv, printed)
