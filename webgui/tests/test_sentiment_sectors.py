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


def _sector_data():
    return [
        {"kind": "sector", "sector": "Information Technology", "label": "Information Technology",
         "etf": "XLK", "name": "Software, semis", "sp_weight": 32.53},
        {"kind": "sector", "sector": "Utilities", "label": "Utilities",
         "etf": "XLU", "name": "Electric, gas", "sp_weight": 2.09},
        {"kind": "industry", "sector": "Information Technology", "label": "Semis",
         "etf": "SMH", "name": "Semiconductors", "sp_weight": 0.0},
    ]


def test_sector_table_rows_built_and_sorted():
    quotes = {"XLK": {"change_pct": 1.0}, "XLU": {"change_pct": 2.0}}
    trends = {"XLK": {"week_pct": 3.0, "month_pct": 5.0},
              "XLU": {"week_pct": -1.0, "month_pct": 0.5}}
    pcr = {"XLK": 0.80, "XLU": 1.20}
    quads = {"XLK": "Leading", "XLU": "Lagging"}
    rows = S.sector_table_rows(_sector_data(), quotes, trends, pcr, quads)
    assert [r["etf"] for r in rows] == ["XLU", "XLK"]   # only sectors, day% desc
    xlk = next(r for r in rows if r["etf"] == "XLK")
    assert xlk["sector"] == "Information Technology"
    assert xlk["day"] == 1.0 and xlk["week"] == 3.0 and xlk["month"] == 5.0
    assert xlk["pcr"] == 0.80 and xlk["rrg"] == "Leading"


def test_sector_summary_line():
    quotes = {"XLK": {"change_pct": 1.0}, "XLU": {"change_pct": -0.5}}
    line = S.sector_summary(_sector_data(), quotes)
    assert "% green" in line and "Cap-wtd" in line and "/10" in line


def test_rotation_banner_regimes():
    assert S.rotation_banner({"day_spread": 1.5})[0] == "STRONG RISK-ON"
    assert S.rotation_banner({"day_spread": 0.5})[0] == "RISK-ON"
    assert S.rotation_banner({"day_spread": -0.5})[0] == "RISK-OFF"
    assert S.rotation_banner({"day_spread": -1.5})[0] == "STRONG RISK-OFF"
    assert S.rotation_banner({"day_spread": 0.0})[0] == "MIXED"
    assert S.rotation_banner({})[0] == "—"
    assert S.rotation_banner(None)[0] == "—"


def test_rotation_banner_color_and_detail():
    regime, color, detail = S.rotation_banner({
        "day_spread": 0.5, "day_cyc": 0.7, "day_def": 0.2,
        "day_top3": ["Tech", "Financials", "Energy"],
        "day_bot3": ["Staples", "Utilities", "Health Care"]})
    assert color == S.CLR_GREEN
    assert detail.startswith("DAY:")
    assert "Tech" in detail and "Utilities" in detail
