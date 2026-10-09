"""``tools/show_gamma_scale_lock.py`` -- the read-only lock check after a promote.

Runs against the fake bus, so the real read path (the envelope and its payload)
is exercised and not a mock of it.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from shared.bus import Bus  # noqa: E402
from tools import show_gamma_scale_lock as tool  # noqa: E402

LOCK = {"minutes": 60, "net": 1.0, "call": 2.0, "put": 3.0, "size": 4.0}


def test_report_prints_each_views_lock():
    lines = tool.report({"symbol": "$SPX", "views": {
        "GEX": {"scale_lock": LOCK}, "Charm": {"scale_lock": None}}})
    assert lines[0] == "symbol $SPX"
    assert lines[1].startswith("GEX") and str(LOCK) in lines[1]
    assert lines[2].split() == ["Charm", "None"]


def test_a_payload_from_before_the_field_says_so():
    lines = tool.report({"symbol": "SPY", "views": {"GEX": {"flip": 1.0}}})
    assert "no scale_lock field" in lines[1]


def test_nothing_cached_and_no_views_are_one_line_each():
    assert tool.report(None) == ["cache:options:gamma: nothing cached"]
    assert len(tool.report({"symbol": "$SPX", "views": {}})) == 1


def test_main_reads_the_snapshot_through_the_envelope(capsys):
    Bus().cache_set(tool.KEY, {"symbol": "$SPX",
                               "views": {"GEX": {"scale_lock": LOCK}}})
    assert tool.main() == 0
    out = capsys.readouterr().out
    assert "symbol $SPX" in out and str(LOCK) in out


def test_main_fails_when_there_is_no_snapshot(capsys):
    from shared.bus.client import reset_fake_bus
    reset_fake_bus()
    assert tool.main() == 1
    assert "nothing cached" in capsys.readouterr().out


def test_it_never_writes():
    src = pathlib.Path(tool.__file__).read_text(encoding="utf-8")
    for call in ("cache_set(", "publish(", "xadd(", ".set("):
        assert call not in src
