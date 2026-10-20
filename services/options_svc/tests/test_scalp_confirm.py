"""The intraday confirmation reading (scalp_confirm).

Fixtures are the shapes prod served on 2026-10-20 (captured in the plan's
``real_view_samples.json``): a matrix row carries ``spot``, ``call_wall``,
``put_wall``, ``flip``, ``net_gex``; the net premium view's ``series[symbol]``
is ``[[epoch, call, put], ...]``; a flow alert carries ``type``, ``side``,
``symbol``, ``ts`` and ``id``, and the bought / sold estimate lives in a
separate view keyed by alert id; the sentiment composite's trend state is at
``derived.trend.state``. Every source goes through the fake bus and
``sources``, the one reader, so the tests cover what the service will read.
"""
import ast
import datetime as dt
import pathlib
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import scalp_confirm as sc
from shared import scalp_config, symbols
from shared.bus import Bus
from shared.bus.client import reset_fake_bus

CT = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 20, 9, 6, tzinfo=CT)
ENTRY = int(NOW.timestamp())
BIG10 = list(symbols.big10())
SVC = pathlib.Path(__file__).resolve().parents[1]

# The SPY row exactly as prod served it, less the fields this module never reads.
SPY_ROW = {
    "symbol": "SPY", "spot": 769.44, "flip": 766.76, "call_wall": 775.0,
    "put_wall": 760.0, "net_gex": 2464351492.8659897,
}


def iso(minutes_ago=0.0):
    return (NOW.astimezone(UTC) - dt.timedelta(minutes=minutes_ago)).isoformat()


def row(symbol, spot, call_wall, put_wall, flip=None, net_gex=1.0e6):
    return {"symbol": symbol, "spot": spot, "call_wall": call_wall,
            "put_wall": put_wall, "flip": flip, "net_gex": net_gex}


def big_rows(roomy=7, spot=200.0):
    """Ten members: ``roomy`` with a call wall 1% up and a put wall 1% down,
    the rest with walls 0.1% away."""
    out = []
    for i, sym in enumerate(BIG10):
        gap = 0.01 if i < roomy else 0.001
        out.append(row(sym, spot, spot * (1 + gap), spot * (1 - gap)))
    return out


def matrix(rows, minutes_ago=0.0):
    return {"date": "2026-10-20", "session_date": "2026-10-20",
            "ts": iso(minutes_ago), "error": None, "rows": rows}


def netprem(series, minutes_ago=0.0):
    return {"session_date": "2026-10-20", "ts": iso(minutes_ago), "error": None,
            "series": series}


def alert(typ, side, symbol, minutes_ago=5, aid=None, **extra):
    ts = ENTRY - int(minutes_ago * 60)
    return {"type": typ, "side": side, "symbol": symbol, "ts": ts,
            "id": aid or f"{symbol}|{typ}|{side}|{ts}", **extra}


def flow(alerts):
    return {"date": "2026-10-20", "alerts": alerts}


def sentiment(state, minutes_ago=0.0, score=61.0):
    return {"composite_at": iso(minutes_ago), "proxy_up": True,
            "derived": {"trend": {"state": state, "score": score}}}


def tally(bought, sold, unlabelled=0.0):
    return {"poll": {"bought": bought, "sold": sold, "unlabelled": unlabelled},
            "stream": None}


def read(*, mx=None, np=None, fl=None, sent=None, sides=None, now=NOW):
    """Publish whichever views the test gives, then read them as the service does."""
    reset_fake_bus()
    bus = Bus(fake=True)
    for key, payload in ((sc.MATRIX, mx), (sc.NETPREM, np), (sc.FLOW, fl),
                         (sc.SENTIMENT, sent), (sc.FLOW_SIDES, sides)):
        if payload is not None:
            bus.cache_set(key, payload)
    return sc.sources(bus, now)


def full_matrix(call_wall=775.0, put_wall=760.0, roomy=7):
    return matrix([{**SPY_ROW, "call_wall": call_wall, "put_wall": put_wall}]
                  + big_rows(roomy))


# --- flow ----------------------------------------------------------------

def test_flow_sense_maps_every_alert_type():
    # Pinned against the real `side` values the detectors write
    # (flow_alerts.py: uoa/big_delta call|put, crossover calls_over|puts_over;
    # hiro.py: hiro_surge dealers_buying|dealers_selling, hiro_flip
    # to_buying|to_selling; gamma_flip to_positive|to_negative).
    assert sc.flow_sense(alert("crossover", "calls_over", "SPY")) == 1
    assert sc.flow_sense(alert("crossover", "puts_over", "SPY")) == -1
    assert sc.flow_sense(alert("hiro_surge", "dealers_buying", "SPY")) == 1
    assert sc.flow_sense(alert("hiro_surge", "dealers_selling", "SPY")) == -1
    assert sc.flow_sense(alert("hiro_flip", "to_buying", "SPY")) == 1
    assert sc.flow_sense(alert("hiro_flip", "to_selling", "SPY")) == -1
    assert sc.flow_sense(alert("gamma_flip", "to_positive", "SPY")) is None
    assert sc.flow_sense(alert("gamma_flip", "to_negative", "SPY")) is None
    assert sc.flow_sense(alert("something_new", "x", "SPY")) is None
    assert sc.flow_sense("not an alert") is None


def test_a_sold_call_is_bearish():
    call, put = alert("uoa", "call", "SPY"), alert("uoa", "put", "SPY")
    assert sc.flow_sense(call, tally(900, 100)) == 1       # a call bought
    assert sc.flow_sense(call, tally(100, 900)) == -1      # a call SOLD
    assert sc.flow_sense(put, tally(900, 100)) == -1       # a put bought
    assert sc.flow_sense(put, tally(100, 900)) == 1        # a put SOLD
    big = alert("big_delta", "call", "NVDA")
    assert sc.flow_sense(big, tally(1, 9)) == -1           # big_delta reads it too
    assert sc.flow_sense(big, {"bought": 9, "sold": 1}) == 1   # a bare tally works


def test_no_estimate_is_not_directional():
    call = alert("uoa", "call", "SPY")
    assert sc.flow_sense(call) is None
    assert sc.flow_sense(call, tally(0, 0, 500)) is None   # all unlabelled
    assert sc.flow_sense(call, tally(300, 300)) is None    # a tie takes no side
    assert sc.flow_sense(call, tally(float("nan"), 5)) is None
    assert sc.flow_sense(call, {"poll": None, "stream": None}) is None


def test_flow_counts_directional_alerts_only():
    alerts = [
        alert("uoa", "call", "SPY", 5, aid="u1"),                  # bought: +1
        alert("uoa", "call", "SPY", 6, aid="u2"),                  # unlabelled
        alert("gamma_flip", "to_negative", "SPY", 7),              # never directional
        alert("crossover", "calls_over", "SPY", 8),                # +1
        alert("hiro_surge", "dealers_buying", "NVDA", 9),          # BIG10 member: +1
        alert("big_delta", "put", "AMZN", 10, aid="b1"),           # put sold: +1
        alert("hiro_flip", "to_selling", "QQQ", 11),               # other index: out
        alert("uoa", "put", "IWM", 12, aid="u3"),                  # not BIG10: out
        alert("crossover", "calls_over", "SPY", 45),               # before the window
        alert("crossover", "puts_over", "SPY", -2),                # after entry: out
    ]
    sides = {"date": "2026-10-20", "contracts": {
        "u1": tally(800, 100), "b1": tally(50, 700), "u3": tally(900, 0)}}
    src = read(fl=flow(alerts), sides=sides, mx=full_matrix())
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)
    raw = out["flow"]["raw"]
    assert (raw["bull"], raw["bear"], raw["net"]) == (4, 0, 4)
    assert raw["n_alerts"] == 6                         # in scope and in the window
    assert raw["by_type"]["uoa"] == {"n": 2, "bull": 1, "bear": 0}
    assert raw["by_type"]["gamma_flip"] == {"n": 1, "bull": 0, "bear": 0}
    assert out["flow"]["verdict"] == 1                  # net 4 >= margin 2
    # the same alerts read against a DOWN call disagree
    assert sc.components(src, "SPY", -1, 3.0, ENTRY)["flow"]["verdict"] == -1


def test_flow_inside_the_margin_is_neutral_and_none_is_a_real_zero():
    src = read(fl=flow([alert("crossover", "calls_over", "SPY", 5)]))
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)["flow"]
    assert out["verdict"] == 0 and out["raw"]["net"] == 1      # margin is 2
    quiet = read(fl=flow([]))                                  # no alerts is a reading
    out = sc.components(quiet, "SPY", 1, 3.0, ENTRY)["flow"]
    assert out["verdict"] == 0 and out["stale"] is False
    assert sc.components(read(), "SPY", 1, 3.0, ENTRY)["flow"]["verdict"] is None


def test_flow_window_truncated_is_marked():
    cap = sc.FLOW_CAP
    # 300 alerts, all newer than the window start: the list's oldest alert is
    # inside the window, so older ones may have been dropped.
    many = [alert("gamma_flip", "to_positive", "SPY", 10, aid=f"g{i}")
            for i in range(cap)]
    out = sc.components(read(fl=flow(many)), "SPY", 1, 3.0, ENTRY)
    assert out["flow"]["raw"]["truncated"] is True and out["truncated"] is True
    # a SHORT list starting after the window is a quiet morning, not a cut
    few = [alert("gamma_flip", "to_positive", "SPY", 10, aid=f"g{i}") for i in range(3)]
    out = sc.components(read(fl=flow(few)), "SPY", 1, 3.0, ENTRY)
    assert out["flow"]["raw"]["truncated"] is False and out["truncated"] is False
    # a full list that reaches back before the window start is complete
    old = many[:-1] + [alert("gamma_flip", "to_positive", "SPY", 90, aid="old")]
    out = sc.components(read(fl=flow(old)), "SPY", 1, 3.0, ENTRY)
    assert out["truncated"] is False


def test_flow_cap_mirrors_the_service():
    from services.options_svc import handlers
    assert sc.FLOW_CAP == handlers._FLOW_ALERTS_MAX


def test_sides_view_from_another_day_gives_no_estimate():
    al = [alert("uoa", "call", "SPY", 5, aid="u1")]
    stale = {"date": "2026-10-19", "contracts": {"u1": tally(900, 0)}}
    out = sc.components(read(fl=flow(al), sides=stale), "SPY", 1, 3.0, ENTRY)
    assert out["flow"]["raw"]["bull"] == 0


# --- net premium ---------------------------------------------------------

def test_net_premium_reads_the_level_not_a_difference():
    # The newest point is the one that counts. Both columns FELL since the
    # point before (a mark move), but calls still lead by 28%: that agrees.
    series = {"SPY": [[ENTRY - 120, 900e6, 100e6], [ENTRY - 60, 465.7e6, 259.4e6]]}
    out = sc.components(read(np=netprem(series)), "SPY", 1, 3.0, ENTRY)["netprem"]
    assert out["verdict"] == 1
    assert out["raw"]["call"] == 465.7e6 and out["raw"]["put"] == 259.4e6
    assert out["raw"]["frac"] == pytest.approx((465.7 - 259.4) / (465.7 + 259.4))
    # an older point with puts far ahead does not drag it down
    series = {"SPY": [[ENTRY - 120, 100e6, 900e6], [ENTRY - 60, 465.7e6, 259.4e6]]}
    assert sc.components(read(np=netprem(series)), "SPY", 1, 3.0, ENTRY)[
        "netprem"]["verdict"] == 1
    # a DOWN call reads the same level the other way
    assert sc.components(read(np=netprem(series)), "SPY", -1, 3.0, ENTRY)[
        "netprem"]["verdict"] == -1


def test_net_premium_inside_the_margin_is_neutral_and_missing_is_absent():
    series = {"SPY": [[ENTRY - 60, 55.0, 45.0]]}               # exactly 0.1: not above
    out = sc.components(read(np=netprem(series)), "SPY", 1, 3.0, ENTRY)["netprem"]
    assert out["verdict"] == 0
    flat = {"SPY": [[ENTRY - 60, 0.0, 0.0]]}
    assert sc.components(read(np=netprem(flat)), "SPY", 1, 3.0, ENTRY)[
        "netprem"]["verdict"] is None
    assert sc.components(read(np=netprem({"QQQ": [[1, 1.0, 2.0]]})), "SPY", 1, 3.0,
                         ENTRY)["netprem"]["verdict"] is None
    bad = {"SPY": [["x", None, "y"], [ENTRY, "nope", 2]]}
    assert sc.components(read(np=netprem(bad)), "SPY", 1, 3.0, ENTRY)[
        "netprem"]["verdict"] is None


def test_net_premium_finds_the_dollar_index_by_either_spelling():
    series = {"$SPX": [[ENTRY - 60, 1062e6, 605e6]]}
    for name in ("$SPX", "SPX"):
        out = sc.components(read(np=netprem(series)), name, 1, 20.0, ENTRY)
        assert out["netprem"]["verdict"] == 1


# --- dealer read ---------------------------------------------------------

def test_dealer_room_agrees_when_wall_beyond_target():
    src = read(mx=full_matrix(call_wall=775.0))                # room 5.56 >= 3
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)
    d = out["dealer"]
    assert d["verdict"] == 1 and d["stale"] is False
    assert d["raw"]["room"] == pytest.approx(5.56)
    assert d["raw"]["breadth"] == {"with_room": 7, "of": 10, "share": 0.7,
                                   "verdict": 1}
    assert d["raw"]["flip"] == 766.76 and d["raw"]["above_flip"] is True
    assert out["dealer_line"] == ("Room up to the call wall at 775.00 (0.72%); "
                                  "7 of 10 big names have room")
    # a DOWN call reads the put wall: 769.44 - 760 = 9.44
    down = sc.components(src, "SPY", -1, 3.0, ENTRY)
    assert down["dealer"]["verdict"] == 1
    assert down["dealer_line"].startswith("Room down to the put wall at 760.00")


def test_wall_inside_target_disagrees():
    src = read(mx=full_matrix(call_wall=770.0))                # room 0.56 < 3
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)
    assert out["dealer"]["verdict"] == -1
    assert "inside the target" in out["dealer_line"]
    assert "call wall at 770.00" in out["dealer_line"]
    # a spot already beyond the wall is also inside it
    beyond = sc.components(read(mx=full_matrix(call_wall=765.0)), "SPY", 1, 3.0, ENTRY)
    assert beyond["dealer"]["verdict"] == -1


def test_dealer_combines_the_symbol_and_the_breadth():
    # symbol agrees, breadth disagrees (2 of 10): the symbol's own room decides
    one = sc.components(read(mx=full_matrix(roomy=2)), "SPY", 1, 3.0, ENTRY)["dealer"]
    assert one["raw"]["breadth"]["verdict"] == -1 and one["verdict"] == 1
    # symbol disagrees, breadth agrees: the symbol still decides
    two = sc.components(read(mx=full_matrix(call_wall=770.0, roomy=9)), "SPY", 1, 3.0,
                        ENTRY)["dealer"]
    assert two["raw"]["breadth"]["verdict"] == 1 and two["verdict"] == -1
    # both disagree
    three = sc.components(read(mx=full_matrix(call_wall=770.0, roomy=2)), "SPY", 1,
                          3.0, ENTRY)["dealer"]
    assert three["verdict"] == -1
    # in between (5 of 10, neither >= 0.6 nor <= 0.4) the symbol decides too
    mid = sc.components(read(mx=full_matrix(roomy=5)), "SPY", 1, 3.0, ENTRY)["dealer"]
    assert mid["raw"]["breadth"]["verdict"] == 0 and mid["verdict"] == 1


def test_breadth_compares_in_percent_of_each_names_own_spot():
    # win_points 3.0 on SPY (769.44) is 0.39% of spot. A name at 20 with a wall
    # 0.5 away has room 2.5% and counts; a name at 2000 with a wall 5 away has
    # room 0.25% and does not, however large the points.
    rows = [{**SPY_ROW}, row("NVDA", 20.0, 20.5, 19.0), row("MSFT", 2000.0, 2005.0, 1990.0)]
    out = sc.components(read(mx=matrix(rows)), "SPY", 1, 3.0, ENTRY)["dealer"]
    assert out["raw"]["breadth"]["with_room"] == 1


def test_no_greeks_walls_are_no_reading():
    # Schwab sent no Greeks: every wall is None. That is NOT "no room".
    rows = [{**SPY_ROW, "call_wall": None, "put_wall": None, "flip": None,
             "net_gex": None}] + [row(s, 200.0, None, None) for s in BIG10]
    src = read(mx=matrix(rows))
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)
    assert out["dealer"]["verdict"] is None
    assert out["dealer"]["raw"]["breadth"]["of"] == 0
    assert out["score"] is None and out["word"] is None
    assert "No reading" in out["dealer_line"]
    # a zero or non-finite wall is the same absence, never a wall at zero
    for wall in (0, 0.0, float("nan"), "n/a", -5.0):
        rows[0]["call_wall"] = wall
        again = sc.components(read(mx=matrix(rows)), "SPY", 1, 3.0, ENTRY)
        assert again["dealer"]["verdict"] is None


def test_the_symbol_with_no_wall_gets_no_verdict_even_if_breadth_reads():
    rows = [{**SPY_ROW, "call_wall": None}] + big_rows(8)
    out = sc.components(read(mx=matrix(rows)), "SPY", 1, 3.0, ENTRY)
    assert out["dealer"]["verdict"] is None
    assert out["dealer"]["raw"]["breadth"]["share"] == 0.8
    assert "8 of 10 big names have room" in out["dealer_line"]


def test_a_thin_breadth_sample_is_not_a_breadth():
    # 2 of 10 members read: not enough to call a share.
    rows = [{**SPY_ROW}] + [row(s, 200.0, 202.0, 198.0) for s in BIG10[:2]]
    out = sc.components(read(mx=matrix(rows)), "SPY", 1, 3.0, ENTRY)["dealer"]
    assert out["raw"]["breadth"]["verdict"] is None
    assert out["verdict"] == 1                       # the symbol's room still reads


def test_win_points_must_be_a_usable_magnitude():
    src = read(mx=full_matrix())
    for bad in (None, 0, -1.0, float("nan")):
        assert sc.components(src, "SPY", 1, bad, ENTRY)["dealer"]["verdict"] is None


def test_stale_matrix_is_no_reading():
    src = read(mx=matrix([{**SPY_ROW}] + big_rows(), minutes_ago=12))
    assert src["matrix"]["state"] == "stale"
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)
    assert out["dealer"]["verdict"] is None and out["dealer"]["stale"] is True
    assert out["score"] is None
    # and just inside the limit still reads
    fresh = read(mx=matrix([{**SPY_ROW}] + big_rows(), minutes_ago=4.5))
    assert sc.components(fresh, "SPY", 1, 3.0, ENTRY)["dealer"]["verdict"] == 1


def test_stale_net_premium_and_sentiment_are_no_reading():
    series = {"SPY": [[ENTRY - 60, 465.7e6, 259.4e6]]}
    src = read(np=netprem(series, minutes_ago=9), sent=sentiment("bullish", 9))
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)
    assert out["netprem"] == {"verdict": None, "raw": out["netprem"]["raw"], "stale": True}
    assert out["sentiment"]["verdict"] is None and out["sentiment"]["stale"] is True


# --- sentiment -----------------------------------------------------------

@pytest.mark.parametrize("state,expect_up", [
    ("bullish", 1), ("bearish", -1), ("neutral", None),
    ("lack_of_bullishness", None), ("lack_of_bearishness", None),
    ("something_new", None), (None, None)])
def test_sentiment_state_mapping(state, expect_up):
    out = sc.components(read(sent=sentiment(state)), "SPY", 1, 3.0, ENTRY)["sentiment"]
    assert out["verdict"] == expect_up
    assert out["raw"]["state"] == state
    down = sc.components(read(sent=sentiment(state)), "SPY", -1, 3.0, ENTRY)["sentiment"]
    assert down["verdict"] == (None if expect_up is None else -expect_up)


def test_sentiment_weighs_half():
    assert scalp_config.confirm_weights()["sentiment"] == 0.5
    # flow agrees (+1, weight 1), sentiment disagrees (-1, weight 0.5)
    src = read(fl=flow([alert("crossover", "calls_over", "SPY", 5),
                        alert("crossover", "calls_over", "NVDA", 6),
                        alert("hiro_surge", "dealers_buying", "AMD", 7)]),
               sent=sentiment("bearish"))
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)
    assert out["flow"]["verdict"] == 1 and out["sentiment"]["verdict"] == -1
    assert out["score"] == pytest.approx((1.0 - 0.5) / 1.5)


# --- score and word ------------------------------------------------------

def test_score_and_word_cuts():
    lo, hi = scalp_config.confirm_cuts()
    assert (lo, hi) == (-0.34, 0.34)
    one = {"flow": 1, "netprem": 1, "dealer": 1, "sentiment": 1}
    assert sc.combine(one) == (1.0, "Confirms")
    assert sc.combine({"flow": -1, "netprem": -1, "dealer": -1, "sentiment": -1}) == (
        -1.0, "Contradicts")
    # present-but-neutral (0) counts in the denominator, absent (None) does not
    assert sc.combine({"flow": 1, "netprem": 0, "dealer": None, "sentiment": None}) == (
        0.5, "Confirms")
    score, word = sc.combine({"flow": 1, "netprem": -1, "dealer": 0, "sentiment": None})
    assert (score, word) == (0.0, "Mixed")
    # 1/3 is inside the +/-0.34 cuts
    assert sc.combine({"flow": 1, "netprem": 0, "dealer": 0, "sentiment": None})[1] == "Mixed"
    assert sc.combine({"flow": 0, "netprem": 0, "dealer": 0, "sentiment": 0}) == (0.0, "Mixed")
    assert sc.combine({"flow": None, "netprem": None, "dealer": None,
                       "sentiment": None}) == (None, None)
    # sentiment at half weight: (1 + 1 + 1 - 0.5) / 3.5
    got, _ = sc.combine({"flow": 1, "netprem": 1, "dealer": 1, "sentiment": -1})
    assert got == pytest.approx(2.5 / 3.5)


def test_the_word_follows_the_configured_cuts(monkeypatch):
    monkeypatch.setattr(scalp_config, "confirm_cuts", lambda: (-0.9, 0.9))
    assert sc.combine({"flow": 1, "netprem": 1, "dealer": 1, "sentiment": -1})[1] == "Mixed"
    monkeypatch.setattr(scalp_config, "confirm_cuts", lambda: (-0.5, 0.5))
    assert sc.combine({"flow": 1, "netprem": 0, "dealer": None, "sentiment": None})[1] == "Confirms"
    assert sc.combine({"flow": -1, "netprem": 0, "dealer": None,
                       "sentiment": None})[1] == "Contradicts"       # both cuts are inclusive
    monkeypatch.setattr(scalp_config, "confirm_cuts", lambda: (-0.9, 0.9))
    monkeypatch.setattr(scalp_config, "confirm_weights",
                        lambda: {"flow": 1.0, "netprem": 1.0, "dealer": 1.0,
                                 "sentiment": 0.0})
    assert sc.combine({"flow": 1, "netprem": 1, "dealer": 1, "sentiment": -1}) == (
        1.0, "Confirms")


def test_the_whole_reading_for_one_call():
    series = {"SPY": [[ENTRY - 60, 465.7e6, 259.4e6]]}
    src = read(mx=full_matrix(), np=netprem(series), sent=sentiment("neutral"),
               fl=flow([alert("crossover", "calls_over", "SPY", 5),
                        alert("crossover", "calls_over", "NVDA", 6)]))
    out = sc.components(src, "SPY", 1, 3.0, ENTRY)
    assert [out[k]["verdict"] for k in ("flow", "netprem", "dealer", "sentiment")] == [
        1, 1, 1, None]
    assert out["score"] == 1.0 and out["word"] == "Confirms"


def test_no_direction_means_no_verdicts_but_the_raw_is_kept():
    series = {"SPY": [[ENTRY - 60, 465.7e6, 259.4e6]]}
    src = read(mx=full_matrix(), np=netprem(series), sent=sentiment("bullish"),
               fl=flow([alert("crossover", "calls_over", "SPY", 5)]))
    out = sc.components(src, "SPY", None, None, ENTRY)
    for key in ("flow", "netprem", "dealer", "sentiment"):
        assert out[key]["verdict"] is None
    assert out["score"] is None and out["word"] is None
    assert out["flow"]["raw"]["bull"] == 1
    assert out["netprem"]["raw"]["call"] == 465.7e6
    assert out["dealer"]["raw"]["call_wall"] == 775.0
    assert out["dealer"]["raw"]["spot"] == 769.44
    assert out["sentiment"]["raw"]["state"] == "bullish"


# --- wall context --------------------------------------------------------

def test_wall_context_names_walls_in_the_path():
    src = read(mx=matrix([{**SPY_ROW, "call_wall": 771.0, "put_wall": 767.0}]))
    # an UP trade from 769.44: target 772 passes the call wall; stop 768 is above
    # the put wall at 767, so the stop does not reach it
    up = sc.wall_context(src, "SPY", 769.44, 772.0, 768.0)
    assert up == ["Target is beyond the call wall at 771.00"]
    # a stop that does reach the put wall
    up = sc.wall_context(src, "SPY", 769.44, 770.0, 766.0)
    assert up == ["Stop is beyond the put wall at 767.00"]
    # a DOWN trade: target 766 passes the put wall, stop 772 passes the call wall
    down = sc.wall_context(src, "SPY", 769.44, 766.0, 772.0)
    assert down == ["Target is beyond the put wall at 767.00",
                    "Stop is beyond the call wall at 771.00"]
    # a wall exactly at the target is not beyond it; none in the path is empty
    assert sc.wall_context(src, "SPY", 769.44, 771.0, 768.5) == []
    # no wall (no Greeks), a stale matrix or unusable prices is empty, not wrong
    none = read(mx=matrix([{**SPY_ROW, "call_wall": None, "put_wall": None}]))
    assert sc.wall_context(none, "SPY", 769.44, 772.0, 766.0) == []
    old = read(mx=matrix([{**SPY_ROW}], minutes_ago=30))
    assert sc.wall_context(old, "SPY", 769.44, 780.0, 760.0) == []
    assert sc.wall_context(src, "SPY", None, 772.0, 766.0) == []
    assert sc.wall_context(src, "QQQ", 769.44, 772.0, 766.0) == []


# --- snapshot ------------------------------------------------------------

def test_snapshot_holds_every_big10_member():
    series = {"SPY": [[ENTRY - 60, 465.7e6, 259.4e6]],
              "$SPX": [[ENTRY - 60, 1062e6, 605e6]],
              "BIG10": [[ENTRY - 60, 644e6, 309e6]]}
    al = [alert("uoa", "call", "SPY", 5, aid="u1"),
          alert("gamma_flip", "to_positive", "SPY", 6),
          alert("crossover", "puts_over", "QQQ", 7)]
    src = read(mx=full_matrix(), np=netprem(series), sent=sentiment("bullish", score=63.5),
               fl=flow(al), sides={"date": "2026-10-20", "contracts": {"u1": tally(9, 1)}})
    snap = sc.snapshot(src, ["$SPX", "SPY", "QQQ"])
    assert set(BIG10) <= set(snap["matrix"])
    assert snap["matrix"]["SPY"] == {"spot": 769.44, "call_wall": 775.0,
                                     "put_wall": 760.0, "flip": 766.76,
                                     "net_gex": 2464351492.8659897}
    assert snap["matrix"]["NVDA"]["spot"] == 200.0
    assert snap["matrix"]["QQQ"] is None                       # asked for, no row
    assert snap["netprem"]["SPY"] == {"call": 465.7e6, "put": 259.4e6,
                                      "ts": ENTRY - 60}
    assert snap["netprem"]["BIG10"]["call"] == 644e6           # the basket
    assert snap["netprem"]["QQQ"] is None
    assert snap["sentiment"] == {"state": "bullish", "score": 63.5}
    assert snap["flow"]["by_type"]["uoa"] == {"n": 1, "bull": 1, "bear": 0}
    assert snap["flow"]["by_type"]["gamma_flip"] == {"n": 1, "bull": 0, "bear": 0}
    assert snap["flow"]["by_type"]["crossover"] == {"n": 1, "bull": 0, "bear": 1}
    assert snap["sources"]["matrix"]["state"] == "ok"


def test_snapshot_never_records_a_stale_or_absent_source_as_a_value():
    src = read(mx=matrix([{**SPY_ROW}] + big_rows(), minutes_ago=30))
    snap = sc.snapshot(src, ["SPY"])
    assert snap["sources"]["matrix"]["state"] == "stale"
    assert snap["matrix"] == {}
    assert snap["sources"]["netprem"]["state"] == "absent"
    assert snap["netprem"] == {} and snap["flow"] is None and snap["sentiment"] is None


# --- sources -------------------------------------------------------------

def test_sources_marks_absent_stale_and_ok():
    src = read(mx=full_matrix(), np=netprem({}, minutes_ago=6), fl=flow([]))
    assert src["matrix"]["state"] == "ok" and src["matrix"]["age_min"] == pytest.approx(0)
    assert src["netprem"]["state"] == "stale" and src["netprem"]["age_min"] == pytest.approx(6)
    assert src["flow"]["state"] == "ok"              # an empty list is a reading
    assert src["sentiment"]["state"] == "absent" and src["sentiment"]["payload"] is None


def test_a_naive_timestamp_is_central_time():
    naive = NOW.replace(tzinfo=None) - dt.timedelta(minutes=2)
    src = read(mx={**full_matrix(), "ts": naive.isoformat()})
    assert src["matrix"]["state"] == "ok"
    assert src["matrix"]["age_min"] == pytest.approx(2)


def test_a_payload_without_a_usable_time_cannot_be_called_fresh():
    for bad in (None, "", "yesterday", 12):
        src = read(mx={**full_matrix(), "ts": bad})
        assert src["matrix"]["state"] == "stale"
        assert sc.components(src, "SPY", 1, 3.0, ENTRY)["dealer"]["verdict"] is None


def test_the_ages_come_from_the_config(monkeypatch):
    monkeypatch.setattr(scalp_config, "confirm_max_age_min", lambda: 20)
    src = read(mx=matrix([{**SPY_ROW}] + big_rows(), minutes_ago=12))
    assert src["matrix"]["state"] == "ok"


# --- never raises --------------------------------------------------------

def test_malformed_sources_degrade_to_absent_and_never_raise():
    garbage = {"rows": "nope"}
    weird = [
        dict(mx=garbage, np={"series": []}, fl={"alerts": "x"}, sent={"derived": 3}),
        dict(mx={**full_matrix(), "rows": [None, 5, {"symbol": None}]},
             np={**netprem({}), "series": {"SPY": 7}},
             fl=flow([None, 3, {"type": None}, {"ts": "x", "symbol": 1}]),
             sent={"composite_at": iso(), "derived": {"trend": []}}),
        dict(mx={"ts": iso(), "rows": [{"symbol": "SPY", "spot": "a",
                                        "call_wall": [], "put_wall": {}}]}),
    ]
    for kw in weird:
        src = read(**kw)
        out = sc.components(src, "SPY", 1, 3.0, ENTRY)
        assert out["score"] is None or -1 <= out["score"] <= 1
        sc.wall_context(src, "SPY", 769.0, 772.0, 766.0)
        sc.snapshot(src, ["SPY", "$SPX"])
    # even a source dict that is not the shape sources() builds
    assert sc.components({}, "SPY", 1, 3.0, ENTRY)["score"] is None
    assert sc.components(None, "SPY", 1, 3.0, ENTRY)["score"] is None
    assert sc.snapshot(None, ["SPY"])["matrix"] == {}


def test_a_failing_component_is_counted_as_a_degrade(monkeypatch):
    _degrade.reset()
    monkeypatch.setattr(sc, "_dealer", lambda *a, **k: 1 / 0)
    out = sc.components(read(mx=full_matrix()), "SPY", 1, 3.0, ENTRY)
    assert out["dealer"]["verdict"] is None
    assert _degrade.counts().get("options.scalp_confirm", 0) >= 1


# --- the module's own rules ----------------------------------------------

def test_the_module_imports_nothing_from_compute():
    tree = ast.parse((SVC / "scalp_confirm.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "compute" not in (node.module or "").split(".")[-1:]
            assert not any(a.name == "compute" for a in node.names)
        elif isinstance(node, ast.Import):
            assert not any(a.name.endswith("compute") for a in node.names)
