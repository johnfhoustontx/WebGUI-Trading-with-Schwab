"""``tools/measure_flow_remark.py`` -- how much of the Flow ribbon, the Net Prem
line and the crossover alert is re-marking rather than trading.

The arithmetic is tested on synthetic rows; no database is opened here.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import measure_flow_remark as tool  # noqa: E402

SETTINGS = {"band": 0.02, "min_premium": 10000, "cooldown_sec": 1800}


def _rows(pairs, spots=None, vols=None, step=60):
    """``pairs`` is [(call premium, put premium), ...], one a minute."""
    out = []
    for i, (call, put) in enumerate(pairs):
        spot = spots[i] if spots else 100.0
        call_vol, put_vol = vols[i] if vols else (0, 0)
        out.append((1000 + step * i, spot, call_vol, put_vol, call, put))
    return out


# ---- the rows the ribbon draws ------------------------------------------------

def test_usable_keeps_rows_with_both_premiums_in_time_order():
    rows = [(1120, 1.0, 0, 0, 5.0, 4.0), (1060, 1.0, 0, 0, None, 4.0),
            (1000, 1.0, 0, 0, 3.0, 2.0), (1180, 1.0, 0, 0, float("nan"), 1.0),
            (None, 1.0, 0, 0, 1.0, 1.0), (1240,)]
    assert [r[0] for r in tool.usable(rows)] == [1000, 1120]
    assert tool.usable(None) == []


# ---- one line -----------------------------------------------------------------

def test_a_line_that_only_rises_never_fell():
    s = tool.line_stats([10.0, 12.0, 15.0])
    assert (s["fell_pct"], s["fallen"], s["risen"]) == (0.0, 0.0, 5.0)
    assert (s["peak"], s["last"]) == (15.0, 15.0)


def test_falls_are_counted_against_rises_and_the_peak_is_kept():
    s = tool.line_stats([10.0, 14.0, 11.0, 12.0])
    assert s["steps"] == 3 and s["fell_pct"] == 33.3
    assert (s["risen"], s["fallen"], s["fallen_pct_of_risen"]) == (5.0, 3.0, 60.0)
    assert (s["peak"], s["last"]) == (14.0, 12.0)


# ---- the net line -------------------------------------------------------------

def test_a_net_moved_only_by_trades_holds_no_certain_remarking():
    n = tool.net_stats(_rows([(10.0, 10.0), (14.0, 11.0), (15.0, 15.0)]))
    assert n["certain_remark_pct"] == 0.0 and n["steps"] == 2


def test_a_net_moved_only_by_a_falling_side_is_all_remarking():
    """Calls flat, puts falling: the net rises and no trade made it rise."""
    n = tool.net_stats(_rows([(10.0, 10.0), (10.0, 8.0), (10.0, 5.0)]))
    assert n["certain_remark_pct"] == 100.0


def test_the_certain_share_is_the_falls_part_of_each_step():
    # Step 1: calls +4, puts -2 -> net +6, of which 2 is the put falling.
    # Step 2: calls -3, puts +1 -> net -4, of which 3 is the call falling.
    n = tool.net_stats(_rows([(10.0, 10.0), (14.0, 8.0), (11.0, 9.0)]))
    assert n["certain_remark_pct"] == 50.0           # (2 + 3) of (6 + 4)


def test_the_net_is_set_against_price_and_against_net_contracts():
    # The net rises and falls exactly with spot; net contracts do the opposite.
    rows = _rows([(10.0, 10.0), (12.0, 9.0), (10.0, 11.0), (13.0, 9.0), (11.0, 10.0)],
                 spots=[100.0, 101.0, 100.0, 101.5, 100.5],
                 vols=[(0, 0), (1, 5), (9, 6), (10, 12), (20, 13)])
    n = tool.net_stats(rows)
    assert n["with_price_pct"] == 100.0
    assert n["price_corr"] == pytest.approx(1.0, abs=0.05)
    assert n["volume_price_corr"] < 0


def test_a_step_with_no_spot_is_left_out_of_the_price_figures():
    rows = _rows([(10.0, 10.0), (12.0, 9.0), (10.0, 11.0)], spots=[100.0, None, 101.0])
    n = tool.net_stats(rows)
    assert n["price_corr"] is None and n["with_price_pct"] == 0.0
    assert n["steps"] == 2


# ---- the alert ----------------------------------------------------------------

def test_a_crossover_made_by_trading_does_not_need_a_fall():
    rows = _rows([(40_000.0, 50_000.0), (60_000.0, 51_000.0)])
    r = tool.replay(rows, **SETTINGS)
    assert [(a["side"], a["needs_fall"]) for a in r["alerts"]] == [("calls_over", False)]


def test_a_crossover_made_by_a_side_falling_needs_the_fall():
    """Calls did not move. Puts fell under them: the mark, not a trade."""
    rows = _rows([(50_000.0, 60_000.0), (50_000.0, 40_000.0)])
    r = tool.replay(rows, **SETTINGS)
    assert [(a["side"], a["needs_fall"]) for a in r["alerts"]] == [("calls_over", True)]


def test_the_cooldown_is_the_detectors_own():
    flip = [(50_000.0, 60_000.0), (70_000.0, 61_000.0), (71_000.0, 90_000.0),
            (99_000.0, 91_000.0)]
    assert len(tool.replay(_rows(flip), **SETTINGS)["alerts"]) == 1
    assert len(tool.replay(_rows(flip), **dict(SETTINGS, cooldown_sec=60))["alerts"]) == 3


def test_a_crossover_under_the_band_or_the_floor_is_not_an_alert():
    under_band = _rows([(50_000.0, 50_500.0), (50_600.0, 50_500.0)])      # leads by 0.2%
    assert tool.replay(under_band, **SETTINGS)["alerts"] == []
    tiny = _rows([(4_000.0, 5_000.0), (6_000.0, 5_000.0)])                # under $10,000
    assert tool.replay(tiny, **SETTINGS)["alerts"] == []


def test_removing_falls_can_never_add_a_crossover():
    """Why the tool reports no "a fall hid one" count: holding a fallen side at
    its previous value only raises both lines, so the pair with falls removed
    never fires where the stored pair does not. Checked over a grid of pairs."""
    levels = [30_000.0, 45_000.0, 50_000.0, 55_000.0, 70_000.0]
    fired = 0
    for c0 in levels:
        for p0 in levels:
            for c1 in levels:
                for p1 in levels:
                    floored = _rows([(c0, p0), (max(c0, c1), max(p0, p1))])
                    if tool.replay(floored, **SETTINGS)["alerts"]:
                        fired += 1
                        stored = _rows([(c0, p0), (c1, p1)])
                        assert tool.replay(stored, **SETTINGS)["alerts"], (c0, p0, c1, p1)
    assert fired                                     # the grid does hold crossovers


def test_the_settings_come_from_the_live_detectors_config(monkeypatch):
    cfg = tool.flow_alerts._merge(tool.flow_alerts._DEFAULTS,
                                  {"crossover": {"band": 0.05, "cooldown_min": 10}})
    monkeypatch.setattr(tool.flow_alerts, "load_thresholds", lambda: cfg)
    s = tool.crossover_settings()
    assert (s["band"], s["cooldown_sec"]) == (0.05, 600)
    assert s["min_premium"] == tool.flow_alerts._DEFAULTS["crossover"]["min_premium"]


# ---- beside what traded -------------------------------------------------------

def test_the_stored_change_less_what_traded_is_the_remarking():
    rows = _rows([(100.0, 80.0), (104.0, 78.0), (103.0, 79.0)],
                 spots=[10.0, 10.5, 10.2])
    traded = [(1000, 0.0, 0.0), (1060, 6.0, 1.0), (1120, 7.0, 3.0)]
    cmp = tool.against_traded(rows, traded)
    assert (cmp["from_ts"], cmp["to_ts"], cmp["rows"]) == (1000, 1120, 3)
    assert cmp["call"] == {"stored_change": 3.0, "traded": 7.0, "remark": -4.0}
    assert cmp["put"] == {"stored_change": -1.0, "traded": 3.0, "remark": -4.0}
    assert cmp["net"] == {"stored_change": 4.0, "traded": 4.0, "remark": 0.0}
    # Stored net steps: +6, -2. Traded net steps: +5, -1. Same direction twice.
    assert cmp["net_steps_agree_pct"] == 100.0


def test_the_alert_is_replayed_on_both_sets_of_lines():
    """Stored: puts fall under flat calls, so it fires. Traded: nothing crossed."""
    rows = _rows([(50_000.0, 60_000.0), (50_000.0, 40_000.0), (50_000.0, 39_000.0)])
    traded = [(1000, 50_000.0, 60_000.0), (1060, 50_000.0, 60_500.0),
              (1120, 50_100.0, 60_500.0)]
    cmp = tool.against_traded(rows, traded, SETTINGS)
    assert [a["side"] for a in cmp["stored_alerts"]] == ["calls_over"]
    assert cmp["traded_alerts"] == []
    assert "alert on the traded lines: none" in " ".join(tool.report_traded("SPY", cmp))
    assert "stored_alerts" not in tool.against_traded(rows, traded)


def test_only_minutes_both_have_are_compared():
    rows = _rows([(100.0, 80.0), (104.0, 78.0), (103.0, 79.0)])
    assert tool.against_traded(rows, [(1060, 0.0, 0.0)]) is None
    assert tool.against_traded(rows, []) is None
    cmp = tool.against_traded(rows, [(940, 0.0, 0.0), (1060, 1.0, 0.0), (1120, 2.0, 0.0)])
    assert (cmp["from_ts"], cmp["rows"]) == (1060, 2)
    assert cmp["call"]["traded"] == 1.0              # from the first SHARED row


# ---- printing -----------------------------------------------------------------

def test_the_report_names_an_alert_that_needed_a_fall():
    rows = _rows([(50_000.0, 60_000.0), (50_000.0, 40_000.0)])
    text = "\n".join(tool.report("SPY", "2026-10-05", rows, SETTINGS))
    assert "1 crossover alert(s), 1 needing a fall" in text
    assert "would not have fired without a fall" in text


def test_the_report_says_when_there_is_too_little():
    text = "\n".join(tool.report("SPY", "2026-10-05", _rows([(1.0, 1.0)]), SETTINGS))
    assert "fewer than two flow rows" in text


def test_the_summary_counts_across_symbols():
    per = {"SPY": {"alerts": [{"needs_fall": True}, {"needs_fall": False}]},
           "QQQ": {"alerts": [{"needs_fall": True}]}}
    (line,) = tool.summary("2026-10-05", per)
    assert "2 symbols, 3 crossover alert(s)" in line
    assert "2 (66.7%) would not have fired without a fall" in line


def test_no_traded_rows_is_said_plainly():
    assert "no tprem rows" in "\n".join(tool.report_traded("SPY", None))
