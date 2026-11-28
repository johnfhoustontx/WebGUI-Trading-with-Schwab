import pytest

from shared.contracts.market import MarketDashboard


def test_round_trip_and_defaults():
    md = MarketDashboard(
        categories=[{"category": "Volatility",
                     "tiles": [{"display": "VIX", "last": 16.1, "change_pct": 3.6,
                                "color_state": "risk_off_strong"}]}],
        proxy_up=True)
    d = md.model_dump()
    assert d["categories"][0]["category"] == "Volatility"
    assert d["proxy_up"] is True
    # defaults
    assert MarketDashboard().categories == []
    assert MarketDashboard().proxy_up is False
    # envelope-validation round trip
    assert MarketDashboard.from_json(md.to_json()).proxy_up is True


def test_market_read_defaults_and_shape():
    from shared.contracts.market import MarketRead
    empty = MarketRead()
    assert (empty.rows, empty.history, empty.tally) == ([], [], {})
    assert empty.public is False and empty.final is False and empty.next_slot is None
    mr = MarketRead(date="2026-10-05", ts=1, slot="12:45", interval_min=15,
                    next_slot="13:00", public=True,
                    tally={"tailwind": 1, "headwind": 0, "neutral": 0, "none": 5},
                    rows=[{"key": "direction", "verdict": "tailwind",
                           "facts": {"spx_pct": 0.69, "ndx_pct": 0.79}, "prev": None}],
                    history=[{"slot": "12:45", "verdicts": {"direction": "tailwind"}}])
    assert MarketRead.from_json(mr.to_json()) == mr
    assert mr.model_dump()["rows"][0]["facts"]["spx_pct"] == 0.69


# ── cache:market:new_highs ───────────────────────────────────────────────────
def _new_highs_payload(**over):
    counts = {"52w": {"highs": 90, "lows": 10},
              "13w": {"highs": 168, "lows": 24},
              "4w": {"highs": 266, "lows": 49}}
    p = {"enabled": True, "source": "TradingView", "universe": 3426,
         "session": "2026-11-27", "min_price": 5.0,
         "exchanges": ["NYSE", "NASDAQ", "AMEX"], "public": True, "counts": counts}
    p.update(over)
    return p


def test_new_highs_round_trip():
    from shared.contracts.market import MarketNewHighs
    m = MarketNewHighs(**_new_highs_payload())
    d = MarketNewHighs.from_json(m.to_json()).model_dump()
    assert d["counts"]["52w"] == {"highs": 90, "lows": 10}
    assert d["universe"] == 3426 and d["session"] == "2026-11-27"
    assert "rows" not in d
    assert MarketNewHighs(enabled=False).counts == {}


@pytest.mark.parametrize("bad", [-1, 1.5, "3", True, None])
def test_new_highs_rejects_bad_counts(bad):
    from pydantic import ValidationError
    from shared.contracts.market import MarketNewHighs
    p = _new_highs_payload()
    p["counts"]["4w"]["lows"] = bad
    with pytest.raises(ValidationError):
        MarketNewHighs(**p)


def test_new_highs_refuses_the_old_two_row_shape():
    # Until 2026-11-27 the view carried rows: {"all": ..., "over5": ...}. Its
    # counts meant "at a high", not "made one today": never published as today's.
    from pydantic import ValidationError
    from shared.contracts.market import MarketNewHighs
    p = _new_highs_payload()
    p["rows"] = {"all": p.pop("counts")}
    with pytest.raises(ValidationError):
        MarketNewHighs(**p)


def test_new_highs_rejects_a_bad_universe():
    from pydantic import ValidationError
    from shared.contracts.market import MarketNewHighs
    with pytest.raises(ValidationError):
        MarketNewHighs(**_new_highs_payload(universe=-5))


# ── cache:market:new_highs_detail ────────────────────────────────────────────
def _detail_payload():
    stock = {"symbol": "BK", "name": "BK Inc.", "price": 90.1, "change_pct": 2.3,
             "level": 90.4}
    return {"enabled": True, "session": "2026-11-27", "public": True,
            "lists": {"4w": {"highs": {"total": 266, "listed": 1, "sectors": [
                {"sector": "Finance", "count": 1, "stocks": [stock]}]}}}}


def test_new_highs_detail_round_trip():
    from shared.contracts.market import MarketNewHighsDetail
    m = MarketNewHighsDetail(**_detail_payload())
    d = MarketNewHighsDetail.from_json(m.to_json()).model_dump()
    assert d["lists"]["4w"]["highs"]["sectors"][0]["stocks"][0]["symbol"] == "BK"
    assert MarketNewHighsDetail(enabled=False).lists == {}


@pytest.mark.parametrize("field, bad", [
    ("total", -1), ("listed", 1.5), ("total", "3"), ("listed", True)])
def test_new_highs_detail_rejects_bad_totals(field, bad):
    from pydantic import ValidationError
    from shared.contracts.market import MarketNewHighsDetail
    p = _detail_payload()
    p["lists"]["4w"]["highs"][field] = bad
    with pytest.raises(ValidationError):
        MarketNewHighsDetail(**p)


@pytest.mark.parametrize("bad", [-1, 1.5, None, True])
def test_new_highs_detail_rejects_a_bad_sector_count(bad):
    from pydantic import ValidationError
    from shared.contracts.market import MarketNewHighsDetail
    p = _detail_payload()
    p["lists"]["4w"]["highs"]["sectors"][0]["count"] = bad
    with pytest.raises(ValidationError):
        MarketNewHighsDetail(**p)
