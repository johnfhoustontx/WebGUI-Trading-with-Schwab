"""The homepage weather card's payload (services/market_svc/site_weather.py).

Design: docs/plans/2026-11-19-market-weather-card-design.md. The fixture is the
real Market read published at the close on Thursday 2026-11-19, so the storm
card below is the one the design's sample shows.
"""
import copy
import json

import pytest

from services.market_svc import site_weather
from shared import market_read_config

CFG = market_read_config.load()
STRESSED = {"label": "Stressed", "committed_label": "crisis"}

READING = {
    "enabled": True, "date": "2026-11-19", "ts": 1795122002, "slot": "15:00",
    "interval_min": 15, "glow_min": 5.0, "next_slot": None, "final": True, "public": True,
    "tally": {"tailwind": 0, "headwind": 5, "neutral": 1, "none": 1},
    "rows": [
        {"key": "direction", "verdict": "headwind",
         "facts": {"spx_pct": -2.01471128, "ndx_pct": -2.20977018}},
        {"key": "breadth", "verdict": "headwind",
         "facts": {"advancing": 2, "declining": 35, "flat": 0, "share": 0.05405405405405406}},
        {"key": "structure", "verdict": "headwind",
         "facts": {"symbols": [
             {"symbol": "SPY", "spot": 716.14, "flip": 721.96, "ceiling": 731.0,
              "room_pct": None, "mode": "short", "state": "headwind"},
             {"symbol": "QQQ", "spot": 645.39, "flip": 658.86, "ceiling": 647.0,
              "room_pct": None, "mode": "short", "state": "headwind"}], "stale": False}},
        {"key": "volatility", "verdict": "headwind",
         "facts": {"vix_level": 28.66, "vix_pct": 16.69381107, "vix1d": 31.6, "vix3m": 27.14,
                   "spx_pct": -2.01471128, "dspx": 37.14, "dspx_pct": -2.18593627}},
        {"key": "flow", "verdict": "neutral",
         "facts": {"call_lean": 0.585, "put_lean": -5.607, "calls": 39, "puts": 161,
                   "contracts": 200}, "estimate": True, "public": True},
        {"key": "cross_asset", "verdict": "headwind",
         "facts": {"tiles": [{"name": "TLT", "pct": 0.16205435, "state": "off"},
                             {"name": "$DXY", "pct": 0.03376097, "state": "flat"},
                             {"name": "HYG", "pct": -0.15655577, "state": "off"}],
                   "risk_on": 0, "risk_off": 2}},
        {"key": "volume", "verdict": "none",
         "facts": {"symbols": [], "sector_up_share": None, "through": None, "pace": None,
                   "range_hint": None}},
    ],
}


def _reading(**verdicts):
    """The fixture with some rows' verdicts replaced: ``_reading(flow="headwind")``."""
    r = copy.deepcopy(READING)
    for row in r["rows"]:
        if row["key"] in verdicts:
            row["verdict"] = verdicts[row["key"]]
    return r


def _row(reading, key):
    return next(r for r in reading["rows"] if r["key"] == key)


def _keys(out):
    return [r["key"] for r in out["readings"]]


# ── the storm day ────────────────────────────────────────────────────────────
def test_the_storm_day_matches_the_spec_sample():
    out = site_weather.build(_reading(), STRESSED, CFG)
    assert out["v"] == 1
    assert out["sky"] == "storm" and out["label"] == "Severe storm"
    assert out["headline"] == "Cold front sweeps 35 of 37 markets; the fear gauge gusts to 28.7."
    assert out["tally"] == ["headwind"] * 4 + ["neutral", "headwind", "none"]
    assert out["tally_text"] == "5 headwinds · 1 neutral · 1 no reading"
    assert out["slot_label"] == "3:00 PM CT" and out["final"] is True
    assert (out["date"], out["slot"], out["ts"], out["next_slot"]) == ("2026-11-19", "15:00", 1795122002, None)
    assert out["interval_min"] == 15 and out["refetch_sec"] == 300
    assert out["regime"] == "Stressed"
    assert out["temp"] == {"spx_pct": -2.01471128, "ndx_pct": -2.20977018, "tone": "cold"}
    assert _keys(out) == ["temperature", "coverage", "wind", "pressure", "umbrellas", "shelter"]
    assert out["readings"][0] == {"key": "temperature", "name": "Temperature",
                                  "sub": "S&P 500 · Nasdaq-100", "value": "−2.0° · −2.2°",
                                  "tone": "cold"}
    assert out["readings"][1] == {"key": "coverage", "name": "Rain coverage",
                                  "sub": "Markets falling on our board", "value": "35 of 37",
                                  "tone": "cold"}
    assert out["readings"][2] == {"key": "wind", "name": "Wind", "sub": "VIX, up 16.7%",
                                  "value": "28.7", "tone": "cold"}
    assert out["readings"][3] == {"key": "pressure", "name": "Pressure",
                                  "sub": "SPY 716 vs the gamma flip 722", "value": "Below",
                                  "tone": "cold"}
    assert out["readings"][4] == {"key": "umbrellas", "name": "Umbrellas : sunglasses",
                                  "sub": "Puts to calls in the option flow", "value": "161 : 39",
                                  "tone": "plain"}
    assert out["readings"][5] == {"key": "shelter", "name": "Shelter",
                                  "sub": "Bonds up, high yield down", "value": "Risk-off",
                                  "tone": "cold"}


def test_the_payload_is_plain_json():
    out = site_weather.build(_reading(), STRESSED, CFG)
    assert json.loads(json.dumps(out)) == out


def test_slot_labels_read_as_a_clock():
    r = _reading()
    r["slot"] = "08:45"
    assert site_weather.build(r, None, CFG)["slot_label"] == "8:45 AM CT"
    r["slot"] = "12:00"
    assert site_weather.build(r, None, CFG)["slot_label"] == "12:00 PM CT"


def test_no_regime_is_none_not_a_guess():
    assert site_weather.build(_reading(), None, CFG)["regime"] is None


# ── the sky ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tally,regime,want", [
    (["none"] * 4 + ["headwind"] * 3, None, "fog"),
    (["headwind"] * 5 + ["tailwind"] * 2, None, "storm"),
    (["headwind"] * 3 + ["tailwind"] * 2 + ["neutral"] * 2, {"committed_label": "crisis"}, "storm"),
    (["tailwind"] * 3 + ["headwind"] * 2 + ["neutral"] * 2, {"committed_label": "crisis"}, "fair"),
    (["tailwind"] * 5 + ["neutral"] * 2, None, "clear"),
    (["tailwind"] * 5 + ["headwind"] + ["neutral"], None, "fair"),
    (["headwind"] * 4 + ["neutral"] * 3, None, "showers"),
    (["headwind"] * 2 + ["tailwind"] * 2 + ["neutral"] * 3, None, "overcast"),
    (["neutral"] * 7, None, "overcast"),
])
def test_sky_rules(tally, regime, want):
    assert site_weather.sky(tally, regime, CFG["weather"]) == want


def test_sky_thresholds_are_read_from_config():
    w = dict(CFG["weather"], storm_headwinds=4)
    assert site_weather.sky(["headwind"] * 4 + ["neutral"] * 3, None, w) == "storm"
    w = dict(CFG["weather"], min_readings=7)
    assert site_weather.sky(["headwind"] * 6 + ["none"], None, w) == "fog"


@pytest.mark.parametrize("sky,label", [("fog", "Fog"), ("storm", "Severe storm"),
                                       ("showers", "Scattered showers"), ("overcast", "Overcast"),
                                       ("fair", "Mostly sunny"), ("clear", "Clear skies")])
def test_every_sky_has_its_label(sky, label):
    assert site_weather.LABELS[sky] == label


# ── masking and absence ──────────────────────────────────────────────────────
def test_flow_not_public_is_masked_everywhere():
    r = _reading(flow="headwind", cross_asset="neutral")
    _row(r, "flow")["public"] = False
    out = site_weather.build(r, None, CFG)
    assert out["tally"][4] == "none"
    assert "umbrellas" not in _keys(out)
    # Four headwinds once flow is masked: showers, where five would be a storm.
    assert out["sky"] == "showers"
    assert out["tally_text"] == "4 headwinds · 1 neutral · 2 no reading"


def test_a_none_row_is_left_out_but_keeps_its_bar():
    r = _reading(volatility="none")
    out = site_weather.build(r, None, CFG)
    assert "wind" not in _keys(out)
    assert out["tally"][3] == "none"


@pytest.mark.parametrize("change", ["no_reading", "off", "not_public", "weather_off"])
def test_hidden_when_switched_off_or_not_public(change):
    r, cfg = _reading(), CFG
    if change == "no_reading":
        r = None
    elif change == "off":
        r = {"enabled": False, "date": "2026-11-19", "ts": 1795122002, "public": False}
    elif change == "not_public":
        r["public"] = False
    else:
        cfg = dict(CFG, weather=dict(CFG["weather"], enabled=False))
    assert site_weather.build(r, STRESSED, cfg) == {"v": 1, "hidden": True}
    assert site_weather.HIDDEN == {"v": 1, "hidden": True}


def test_a_fog_card_has_no_headline():
    r = _reading(direction="none", breadth="none", structure="none", volatility="none")
    out = site_weather.build(r, None, CFG)
    assert out["sky"] == "fog" and out["headline"] == ""


def test_a_non_finite_fact_is_absent():
    r = _reading()
    _row(r, "volatility")["facts"]["vix_level"] = float("nan")
    out = site_weather.build(r, STRESSED, CFG)
    assert out["headline"] == "Cold front sweeps 35 of 37 markets."
    assert "wind" not in _keys(out)


# ── the headline ─────────────────────────────────────────────────────────────
def test_wind_clause_dropped_without_a_vix():
    r = _reading()
    del _row(r, "volatility")["facts"]["vix_level"]
    assert site_weather.build(r, STRESSED, CFG)["headline"] == "Cold front sweeps 35 of 37 markets."


@pytest.mark.parametrize("pct,phrase", [(-3.0, "the fear gauge sinks to 28.7"),
                                        (0.4, "the fear gauge holds near 28.7"),
                                        (1.0, "the fear gauge gusts to 28.7")])
def test_the_wind_follows_the_vix_move(pct, phrase):
    r = _reading()
    _row(r, "volatility")["facts"]["vix_pct"] = pct
    assert site_weather.build(r, STRESSED, CFG)["headline"].endswith(phrase + ".")


def test_coverage_says_all_when_one_side_is_empty():
    r = _reading()
    _row(r, "breadth")["facts"].update(advancing=0, declining=37, flat=0)
    assert site_weather.build(r, STRESSED, CFG)["headline"].startswith("Cold front sweeps all 37 markets;")


def test_coverage_total_counts_flat_tiles():
    r = _reading()
    _row(r, "breadth")["facts"].update(advancing=2, declining=30, flat=5)
    out = site_weather.build(r, STRESSED, CFG)
    assert out["headline"].startswith("Cold front sweeps 30 of 37 markets;")
    assert out["readings"][1]["value"] == "30 of 37"


def test_coverage_falls_back_to_the_board():
    r = _reading(breadth="none")
    assert site_weather.build(r, STRESSED, CFG)["headline"].startswith("Cold front sweeps the board;")


def test_warm_sky_counts_advancing():
    r = _reading(direction="tailwind", breadth="tailwind", structure="tailwind",
                 volatility="neutral", flow="tailwind", cross_asset="neutral", volume="headwind")
    _row(r, "breadth")["facts"].update(advancing=26, declining=11, flat=0)
    _row(r, "volatility")["facts"].update(vix_level=14.1, vix_pct=-4.1)
    out = site_weather.build(r, None, CFG)
    assert out["sky"] == "fair"
    assert out["headline"] == "Sun breaks through over 26 of 37 markets; the fear gauge sinks to 14.1."
    assert out["readings"][1]["name"] == "Sun coverage"
    assert out["readings"][1]["sub"] == "Markets rising on our board"
    assert out["readings"][1]["tone"] == "warm"


@pytest.mark.parametrize("verdicts,start", [
    ({"direction": "headwind", "breadth": "headwind", "structure": "neutral",
      "volatility": "neutral", "flow": "neutral", "cross_asset": "neutral", "volume": "neutral"},
     "Light rain falls over "),
    ({k: "neutral" for k in ("direction", "breadth", "structure", "volatility", "flow",
                             "cross_asset", "volume")}, "No front either way; "),
    ({k: "tailwind" for k in ("direction", "breadth", "structure", "volatility", "flow",
                              "cross_asset", "volume")}, "A warm front lifts "),
])
def test_each_sky_opens_with_its_front(verdicts, start):
    out = site_weather.build(_reading(**verdicts), None, CFG)
    assert out["headline"].startswith(start)


def test_the_overcast_headline_has_no_coverage():
    v = {k: "neutral" for k in ("direction", "breadth", "structure", "volatility", "flow",
                                "cross_asset", "volume")}
    out = site_weather.build(_reading(**v), None, CFG)
    assert out["headline"] == "No front either way; the fear gauge gusts to 28.7."


_FUTURE_WORDS = ("will", "ahead", "expect", "forecast", "tomorrow", "likely")


@pytest.mark.parametrize("fill", ["tailwind", "headwind", "neutral"])
def test_no_word_names_a_future_move(fill):
    v = {k: fill for k in ("direction", "breadth", "structure", "volatility", "flow",
                           "cross_asset", "volume")}
    out = site_weather.build(_reading(**v), STRESSED, CFG)
    words = [out["headline"]] + [r["sub"] for r in out["readings"]] + [r["value"] for r in out["readings"]]
    for text in words:
        assert not any(w in text.lower().split() for w in _FUTURE_WORDS), text


# ── the readings ─────────────────────────────────────────────────────────────
def test_pressure_reads_above_and_at_the_flip():
    r = _reading()
    _row(r, "structure")["facts"]["symbols"][0].update(spot=730.0, flip=721.96)
    assert site_weather.build(r, None, CFG)["readings"][3]["value"] == "Above"
    _row(r, "structure")["facts"]["symbols"][0].update(spot=721.96)
    assert site_weather.build(r, None, CFG)["readings"][3]["value"] == "At the flip"


def test_shelter_sub_when_tiles_have_no_move():
    r = _reading()
    for t in _row(r, "cross_asset")["facts"]["tiles"]:
        t["pct"] = None
    _row(r, "cross_asset")["facts"].update(risk_on=1, risk_off=1)
    shelter = site_weather.build(r, None, CFG)["readings"][-1]
    assert shelter["sub"] == "Bonds, the dollar and high yield"
    assert shelter["value"] == "Mixed"


def test_shelter_risk_on():
    r = _reading()
    tiles = _row(r, "cross_asset")["facts"]["tiles"]
    tiles[0]["pct"], tiles[2]["pct"] = -0.4, 0.3
    _row(r, "cross_asset")["facts"].update(risk_on=2, risk_off=0)
    shelter = site_weather.build(r, None, CFG)["readings"][-1]
    assert shelter["sub"] == "Bonds down, high yield up"
    assert shelter["value"] == "Risk-on"


def test_a_positive_temperature_carries_its_plus():
    r = _reading(direction="tailwind")
    _row(r, "direction")["facts"].update(spx_pct=1.44, ndx_pct=0.05)
    out = site_weather.build(r, None, CFG)
    assert out["readings"][0]["value"] == "+1.4° · +0.1°"
    assert out["temp"]["tone"] == "warm"


# ── writing the file ─────────────────────────────────────────────────────────
def test_publish_writes_atomically_and_readably(site_root):
    assert site_weather.publish({"v": 1, "sky": "fog"}) is True
    assert json.loads((site_root / "weather.json").read_text("utf-8"))["sky"] == "fog"
    assert not list(site_root.glob(".weather.json.*"))


def test_publish_can_be_pointed_elsewhere(tmp_path):
    assert site_weather.publish({"v": 1}, root=tmp_path) is True
    assert json.loads((tmp_path / "weather.json").read_text("utf-8")) == {"v": 1}


def test_publish_never_raises(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(site_weather._degrade, "degraded", lambda area: calls.append(area))
    assert site_weather.publish({"v": 1}, root=tmp_path / "missing" / "dir") is False
    assert calls == ["market.weather"]
    assert not (tmp_path / "missing").exists()


def test_the_regime_is_read_from_the_bus():
    from shared.bus import Bus
    b = Bus()
    assert site_weather.regime_of(b) is None
    b.cache_set(site_weather.REGIME_VIEW, {"label": "Stressed", "committed_label": "crisis"})
    assert site_weather.regime_of(b) == {"label": "Stressed", "committed_label": "crisis"}
