"""Tests for the in-memory adapter of the sector-rotation tool."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app root
import sector_rotation_assessment as rt  # noqa: E402


def _ramp(start, step, n):
    return [start + step * i for i in range(n)]


def _synthetic_closes(n=130):
    # Benchmark longer than sectors to exercise tail-alignment.
    bench = _ramp(100.0, 0.10, n + 40)
    sectors = {}
    for i, etf in enumerate(rt.SECTOR_ETFS):
        sectors[etf] = _ramp(100.0, 0.10 + (i - 5) * 0.01, n)
    return sectors, bench


def test_assess_from_close_series_typical():
    sectors, bench = _synthetic_closes()
    a = rt.assess_from_close_series(sectors, bench, "2026-06-08")
    assert a is not None
    assert len(a["sectors"]) == 11
    assert a["date"] == "2026-06-08"
    assert a["headline"]["regime"] in ("Risk-ON", "Risk-OFF", "Mixed")
    assert isinstance(a["rotating_from"], list)
    assert isinstance(a["rotating_into"], list)


def test_assess_from_close_series_insufficient():
    sectors = {etf: _ramp(100.0, 0.1, 30) for etf in rt.SECTOR_ETFS}
    bench = _ramp(100.0, 0.1, 30)
    assert rt.assess_from_close_series(sectors, bench, "2026-06-08") is None


def test_assess_from_close_series_empty():
    assert rt.assess_from_close_series({}, [], "2026-06-08") is None


def test_assess_from_close_series_rejects_below_min_bars():
    # Regression: the standardized RS-Momentum needs 2*NORM_WINDOW +
    # MOM_WINDOW - 1 bars (compounding rolling windows). A 6-month-deep
    # sector cache (~126 bars) is BELOW that, so the adapter must return
    # None, NOT a half-computed assessment with an empty 'sectors' list.
    n = rt.MIN_BARS - 1
    sectors = {etf: _ramp(100.0, 0.10 + (i - 5) * 0.01, n)
               for i, etf in enumerate(rt.SECTOR_ETFS)}
    bench = _ramp(100.0, 0.10, n + 40)
    assert rt.assess_from_close_series(sectors, bench, "2026-06-08") is None


def test_assess_from_close_series_full_year_depth():
    # A full trading year (~250 bars) classifies all 11 sectors. This is the
    # depth the dashboard must supply (matching SPY) for the popup to work.
    sectors = {etf: _ramp(100.0, 0.10 + (i - 5) * 0.01, 250)
               for i, etf in enumerate(rt.SECTOR_ETFS)}
    bench = _ramp(100.0, 0.10, 290)
    a = rt.assess_from_close_series(sectors, bench, "2026-06-08")
    assert a is not None
    assert len(a["sectors"]) == 11


def test_assess_from_close_series_includes_tail():
    sectors, bench = _synthetic_closes()
    a = rt.assess_from_close_series(sectors, bench, "2026-06-08")
    assert a is not None
    for s in a["sectors"]:
        tail = s.get("tail")
        assert isinstance(tail, list) and tail, f"{s['etf']} missing tail"
        # At most TAIL_LENGTH points, oldest -> newest.
        assert len(tail) <= rt.TAIL_LENGTH
        # Each point carries the same keys as the head reading.
        assert set(tail[0]) == {"rs_ratio", "rs_momentum"}
        # The newest tail point equals the current head reading.
        assert tail[-1]["rs_ratio"] == s["rs_ratio"]
        assert tail[-1]["rs_momentum"] == s["rs_momentum"]


def test_tail_length_constant_is_30():
    assert rt.TAIL_LENGTH == 30
