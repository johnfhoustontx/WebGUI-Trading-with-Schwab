"""Pure-transform tests for the Sentiment sector-perf additions."""
from pages import sentiment as S


def test_pct_color_buckets():
    assert S.pct_color(0.5) == S.CLR_GREEN
    assert S.pct_color(-0.5) == S.CLR_RED
    assert S.pct_color(0.01) == S.CLR_FLAT      # |pct| < 0.05 -> flat
    assert S.pct_color(None) == S.CLR_FLAT


def test_pcr_color_buckets():
    assert S.pcr_color(0.80) == S.CLR_GREEN     # call-dominated
    assert S.pcr_color(1.20) == S.CLR_RED       # put-dominated
    assert S.pcr_color(1.00) == S.CLR_FLAT      # neutral band
    assert S.pcr_color(None) == S.CLR_FLAT


def test_rrg_color_map():
    assert S.rrg_color("Leading") == S.CLR_GREEN
    assert S.rrg_color("Improving") == S.CLR_CYAN
    assert S.rrg_color("Weakening") == S.CLR_YELLOW
    assert S.rrg_color("Lagging") == S.CLR_RED
    assert S.rrg_color(None) == S.CLR_FLAT


def test_pcr_from_chain_sums_volume():
    chain = {
        "putExpDateMap": {"2026-06-20:6": {"500.0": [{"totalVolume": 30}]}},
        "callExpDateMap": {"2026-06-20:6": {"500.0": [{"totalVolume": 60}]}},
    }
    assert S.pcr_from_chain(chain) == 0.5       # 30 put / 60 call
    assert S.pcr_from_chain({}) is None
    assert S.pcr_from_chain({"callExpDateMap": {}}) is None  # cv == 0


def test_week_month_from_closes():
    closes = [float(i) for i in range(1, 31)]   # 1..30, last=30.0
    d3, wk, mo = S.week_month_from_closes(closes)
    # n=3 -> close[-4]=27 ; n=5 -> close[-6]=25 ; n=21 -> close[-22]=9
    assert round(d3, 4) == round((30 - 27) / 27 * 100, 4)
    assert round(wk, 4) == round((30 - 25) / 25 * 100, 4)
    assert round(mo, 4) == round((30 - 9) / 9 * 100, 4)


def test_week_month_short_series_returns_none():
    d3, wk, mo = S.week_month_from_closes([1.0, 2.0])
    assert d3 is None and wk is None and mo is None
