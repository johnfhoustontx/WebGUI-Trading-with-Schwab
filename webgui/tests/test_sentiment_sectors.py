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


def test_is_rth():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    assert S.is_rth(datetime(2026, 6, 15, 10, 0, tzinfo=ct))      # Mon 10:00
    assert not S.is_rth(datetime(2026, 6, 15, 7, 0, tzinfo=ct))   # Mon pre-open
    assert not S.is_rth(datetime(2026, 6, 15, 15, 30, tzinfo=ct)) # Mon post-close
    assert not S.is_rth(datetime(2026, 6, 14, 10, 0, tzinfo=ct))  # Sunday


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


def _full_snap(total, **comp):
    base = {"vix_complex": 4, "put_call": 8, "breadth": 7,
            "rotation": 7, "sector_perf": 8, "credit_pulse": 6}
    base.update(comp)
    return {
        "date": "2026-06-12",
        "composite": {"total_score": f"{total:.2f}", "bias": "Neutral",
                      "size_modifier": "1.00x", "aggregate_confidence": 0.8},
        "component_scores": base,
        "component_confidence": {k: 1.0 for k in base},
        "volatility": {"interpretation": "term backwardation"},
        "options": {"pc_equity": "0.860"},
        "breadth": {"interpretation": "Advancing"},
        "rotation": {"interpretation": "Day 7 · 3d 6 · Wk 7"},
    }


def test_component_table_rows_contrib():
    rows = S.component_table_rows(_full_snap(6.81), rotation_value=None,
                                  sector_value="+0.70%")
    by = {r["name"]: r for r in rows}
    assert "Credit Pulse" not in by          # out-of-composite excluded
    vix = by["VIX Complex"]
    assert vix["score"] == 4 and vix["weight"] == "20%"
    assert abs(vix["contrib"] - 0.20 * 4 * 1.0) < 1e-9     # w*s*conf
    assert by["Sector Performance"]["value"] == "+0.70%"
    assert by["Put/Call (sectors)"]["value"] == "0.860"
    rows2 = S.component_table_rows(_full_snap(6.81, sector_perf=7.6), sector_value="+0.70%")
    assert next(r for r in rows2 if r["name"] == "Sector Performance")["score"] == 7.6


def test_tiles_from_score_band():
    t = S.tiles(_full_snap(6.81), prev_total=6.81)
    # 6.81 is in the >=5 band -> 1.00x / Neutral / Neutral
    assert t["modifier"] == "1.00x" and t["bias"] == "Neutral" and t["signal"] == "Neutral"
    assert t["yesterday"] == "6.81"
    assert t["change"] == "+0.00"


def test_tiles_strong_bands():
    assert S.tiles(_full_snap(9.2), None)["signal"] == "Strong Bull"
    assert S.tiles(_full_snap(2.0), None)["signal"] == "Strong Bear"
    assert S.tiles(_full_snap(2.0), None)["yesterday"] == "—"


def test_rolling_averages_label():
    a5, a20, label = S.rolling_averages([5.0] * 4 + [6.0])
    assert label in ("Rising", "Falling", "Stable")
    rising = S.rolling_averages([4.0] * 19 + [9.0] * 6)
    assert rising[2] == "Rising"


def test_sector_industry_etfs():
    etfs = S.sector_industry_etfs(_sector_data(), "Information Technology")
    assert etfs == ["SMH"]
    assert S.sector_industry_etfs(_sector_data(), "Utilities") == []


def test_industry_rows_built():
    quotes = {"SMH": {"change_pct": 2.5}}
    trends = {"SMH": {"week_pct": 4.0, "month_pct": 9.0}}
    rows = S.industry_rows(_sector_data(), "Information Technology", quotes, trends)
    assert len(rows) == 1
    r = rows[0]
    assert r["etf"] == "SMH" and r["day"] == 2.5 and r["week"] == 4.0 and r["month"] == 9.0
    assert r["pcr"] is None and r["rrg"] is None
    assert r["label"] == "Semis"
    assert r.get("is_industry") is True


def test_industry_rows_missing_data_blank():
    rows = S.industry_rows(_sector_data(), "Information Technology", {}, {})
    assert rows[0]["day"] is None and rows[0]["week"] is None and rows[0]["month"] is None


def test_industry_rows_with_pcr_rrg():
    quotes = {"SMH": {"change_pct": 2.5}}
    trends = {"SMH": {"week_pct": 4.0, "month_pct": 9.0}}
    pcr = {"SMH": 0.92}
    quads = {"SMH": "Leading"}
    rows = S.industry_rows(_sector_data(), "Information Technology", quotes, trends, pcr, quads)
    assert rows[0]["pcr"] == 0.92 and rows[0]["rrg"] == "Leading"


def test_industry_rows_blank_when_no_pcr_rrg():
    rows = S.industry_rows(_sector_data(), "Information Technology", {}, {})
    assert rows[0]["pcr"] is None and rows[0]["rrg"] is None


def test_traffic_color_bands():
    assert S.traffic_color(7.0) == S.CLR_GREEN
    assert S.traffic_color(6.5) == S.CLR_GREEN
    assert S.traffic_color(3.0) == S.CLR_RED
    assert S.traffic_color(4.5) == S.CLR_RED
    assert S.traffic_color(5.5) == S.CLR_YELLOW
    assert S.traffic_color("bad") == S.CLR_YELLOW


def test_refresh_cache_sync_populates_cache(monkeypatch):
    """_refresh_cache_sync updates _CACHE off any UI (loaders + bridge stubbed)."""
    snap = _full_snap(6.81)
    monkeypatch.setattr(S, "_load_snapshots", lambda *a, **k: ([snap], [1.0, 2.0]))
    monkeypatch.setattr(S, "_load_live", lambda *a, **k: None)
    monkeypatch.setattr(S, "is_rth", lambda now: False)
    monkeypatch.setattr(S, "_load_sector_perf", lambda spy: {"sector_data": [], "quotes": {}})
    monkeypatch.setattr(S, "_proxy_up", lambda: True)
    wrote = {}
    monkeypatch.setattr(S, "build_and_write_bridge",
                        lambda snaps, spy, live, sector: wrote.update(
                            {"snaps": snaps, "spy": spy, "live": live, "sector": sector}))
    S._refresh_cache_sync(with_sectors=True)
    assert S._CACHE["snaps"] == [snap]
    assert S._CACHE["spy"] == [1.0, 2.0]
    assert S._CACHE["live"] is None
    assert S._CACHE["sector"] == {"sector_data": [], "quotes": {}}
    assert S._CACHE["proxy_up"] is True
    assert S._CACHE["composite_at"] is not None
    assert wrote["snaps"] == [snap] and wrote["live"] is None


def test_start_background_refresh_idempotent(monkeypatch):
    """start_background_refresh creates the loop task exactly once."""
    import asyncio
    calls = {"n": 0}

    def _fake_create_task(coro):
        calls["n"] += 1
        coro.close()  # avoid "coroutine never awaited" warning

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)
    S._BG["started"] = False
    try:
        S.start_background_refresh()
        S.start_background_refresh()
        assert calls["n"] == 1
        assert S._BG["started"] is True
    finally:
        S._BG["started"] = False
