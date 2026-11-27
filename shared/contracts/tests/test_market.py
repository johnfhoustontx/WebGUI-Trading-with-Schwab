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
    rows = {"all": {"52w": {"highs": 121, "lows": 85},
                    "13w": {"highs": 205, "lows": 120},
                    "4w": {"highs": 347, "lows": 158}},
            "over5": {"52w": {"highs": 120, "lows": 12},
                      "13w": {"highs": 203, "lows": 27},
                      "4w": {"highs": 314, "lows": 55}}}
    p = {"enabled": True, "source": "TradingView", "universe": 4984,
         "session": "2026-11-27", "min_price": 5.0,
         "exchanges": ["NYSE", "NASDAQ", "AMEX"], "public": True, "rows": rows}
    p.update(over)
    return p


def test_new_highs_round_trip():
    from shared.contracts.market import MarketNewHighs
    m = MarketNewHighs(**_new_highs_payload())
    d = MarketNewHighs.from_json(m.to_json()).model_dump()
    assert d["rows"]["over5"]["52w"] == {"highs": 120, "lows": 12}
    assert d["universe"] == 4984 and d["session"] == "2026-11-27"
    assert MarketNewHighs(enabled=False).rows == {}


@pytest.mark.parametrize("bad", [-1, 1.5, "3", True, None])
def test_new_highs_rejects_bad_counts(bad):
    from pydantic import ValidationError
    from shared.contracts.market import MarketNewHighs
    p = _new_highs_payload()
    p["rows"]["all"]["4w"]["lows"] = bad
    with pytest.raises(ValidationError):
        MarketNewHighs(**p)


def test_new_highs_rejects_a_bad_universe():
    from pydantic import ValidationError
    from shared.contracts.market import MarketNewHighs
    with pytest.raises(ValidationError):
        MarketNewHighs(**_new_highs_payload(universe=-5))
