"""Pure-transform tests for the Sentiment sector-perf additions."""
from pages import sentiment as S


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
        # Real v4.3 shape: pc_equity ($CPCE) was retired and is always blank; the
        # cap-weighted sector P/C lives in interpretation + sector_pcr.
        "options": {"pc_equity": "",
                    "interpretation": "Cap-weighted sector P/C 0.77 (11/11) — call-dominated"},
        "sector_pcr": 0.766,
        "breadth": {"interpretation": "Advancing"},
        "rotation": {"interpretation": "Cyc rank 6.1 vs Def rank 5.8 (spread -0.4) — risk-off"},
    }


# v4.3 weights (credit_pulse out of composite) — mirrors the service-computed
# derived["weights"] dict now passed to the page transform.
_WEIGHTS = {"vix_complex": 0.20, "put_call": 0.20, "breadth": 0.20,
            "rotation": 0.15, "sector_perf": 0.25}


def test_component_table_rows_contrib():
    rows = S.component_table_rows(_full_snap(6.81), _WEIGHTS, rotation_value=None,
                                  sector_value="+0.70%")
    by = {r["name"]: r for r in rows}
    assert "Credit Pulse" not in by          # not in weights -> excluded
    vix = by["VIX Complex"]
    assert vix["score"] == 4 and vix["weight"] == "20%"
    assert abs(vix["contrib"] - 0.20 * 4 * 1.0) < 1e-9     # w*s*conf
    assert by["Sector Performance"]["value"] == "+0.70%"
    # Put/Call value reads the cap-weighted sector-P/C interp (NOT the dead
    # pc_equity field), so the value is consistent with the score.
    assert "0.77" in by["Put/Call (sectors)"]["value"]
    assert by["Put/Call (sectors)"]["value"] != "—"
    # Rotation value comes from the snapshot's OWN dual run (matches the score),
    # NOT the separate sectors-cache string passed as rotation_value.
    assert "Cyc rank" in by["Rotation"]["value"]
    rows2 = S.component_table_rows(_full_snap(6.81, sector_perf=7.6), _WEIGHTS,
                                   sector_value="+0.70%")
    assert next(r for r in rows2 if r["name"] == "Sector Performance")["score"] == 7.6


def test_put_call_value_falls_back_to_sector_pcr():
    # If the interp is blank but sector_pcr is present, show the ratio (never a
    # blank value next to a real score — the reported bug).
    snap = _full_snap(6.0)
    snap["options"] = {"pc_equity": ""}          # no interp
    rows = S.component_table_rows(snap, _WEIGHTS)
    v = next(r for r in rows if r["name"] == "Put/Call (sectors)")["value"]
    assert v != "—" and "0.77" in v


def test_rotation_value_prefers_snapshot_over_stale_sectors_cache():
    # The score comes from the snapshot's dual run; a stale sectors-cache string
    # ("no sector returns available") must NOT be shown next to that score.
    snap = _full_snap(6.0)
    rows = S.component_table_rows(snap, _WEIGHTS,
                                  rotation_value="no sector returns available")
    v = next(r for r in rows if r["name"] == "Rotation")["value"]
    assert v == "Cyc rank 6.1 vs Def rank 5.8 (spread -0.4) — risk-off"


def test_component_table_rows_cold_cache_empty():
    # No weights (cold cache) -> no rows produced (graceful-empty).
    assert S.component_table_rows(_full_snap(6.81), None) == []


def test_tiles_uses_service_band():
    # size/bias/signal now arrive from the service-computed derived band.
    t = S.tiles(_full_snap(6.81), prev_total=6.81,
                band=("1.00x", "Neutral", "Neutral"))
    assert t["modifier"] == "1.00x" and t["bias"] == "Neutral" and t["signal"] == "Neutral"
    assert t["yesterday"] == "6.81"
    assert t["change"] == "+0.00"


def test_tiles_cold_cache_placeholders():
    # No band (cold cache) -> size/bias/signal show '—'.
    t = S.tiles(_full_snap(6.81), None)
    assert t["modifier"] == "—" and t["bias"] == "—" and t["signal"] == "—"
    assert t["yesterday"] == "—"


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
