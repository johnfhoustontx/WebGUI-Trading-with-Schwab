"""Quality-triggered trade ideas in shadow: the pure decision (``idea_shadow``).

Design: docs/plans/2026-11-06-trade-idea-quality-shadow-design.md. The rows are
the shapes ``test_trade_idea`` pins (a credit row PER SHARE with flat strikes, a
directional row PER CONTRACT with a ``legs`` list).
"""
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import idea_shadow as S
from services.options_svc import trade_idea as T

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 17, 10, 38, tzinfo=_CT)     # a Thursday, in session
TODAY = NOW.date().isoformat()

CFG = {"mode": "shadow", "min_grade": "Strong", "grades": ("Strong",),
       "min_vol_rank": 50.0, "max_per_hour": 2, "min_gap_min": 20,
       "max_per_day": 10, "one_per_symbol": True}
IDEA_CFG = {"max_age_min": 45, "min_dte": 1, "min_score": 0, "other_structures": True}


def call(symbol="NVDA", strike=190.0, grade="Strong", score=79.0, **over):
    row = {"id": f"{symbol}_0_LONG_CALL", "symbol": symbol, "type": "LONG_CALL",
           "strategy_label": "Long Call", "bias": "bullish",
           "legs": [{"kind": "call", "side": "long", "strike": strike,
                     "expiration": "2026-09-26", "qty": 1}],
           "expiration": "2026-09-26", "dte": 9, "pop_pct": 40.0,
           "underlying_price": 186.4, "net_debit": 410.0, "net_credit": None,
           "commission": 0.65, "composite_score": score, "grade": grade,
           "net_vega": 0.21}
    row.update(over)
    return row


def pcs(symbol="SPY", grade="Strong", iv_rank=70.0, **over):
    row = {"id": f"{symbol}_3_PCS_740.0", "symbol": symbol, "type": "PCS",
           "trade_type": "SWING", "expiration": "2026-09-25", "dte": 8,
           "short_strike": 740.0, "long_strike": 735.0, "width": 5.0,
           "credit": 1.25, "commission": 2.6, "net_credit": 1.224,
           "pop_pct": 78.0, "underlying_price": 759.38,
           "composite_score": 79.0, "grade": grade, "iv_rank": iv_rank,
           # the scanner's convention: short minus long, POSITIVE on a credit spread
           "net_vega": 0.05}
    row.update(over)
    return row


def scan(*, at=NOW - dt.timedelta(minutes=4), directional=(), swing=()):
    return {"timestamp": at.isoformat(), "signals_directional": list(directional),
            "signals_swing": list(swing)}


def seeded(prev_rows=(), posts=()):
    """Today's state after a scan that held ``prev_rows``."""
    first = S.decide(scan(at=NOW - dt.timedelta(minutes=19), directional=prev_rows),
                     None, NOW - dt.timedelta(minutes=15), CFG, IDEA_CFG)
    state = first["state"]
    state["posts"] = list(posts)
    return state


# ── the trade key ───────────────────────────────────────────────────────────
def test_the_trade_key_ignores_the_row_id_which_is_a_list_index():
    a = T.normalize(call(id="NVDA_0_LONG_CALL"))
    b = T.normalize(call(id="NVDA_7_LONG_CALL"))
    assert S.trade_key(a) == S.trade_key(b)
    assert S.trade_key(a) != S.trade_key(T.normalize(call(strike=195.0)))


# ── novelty ─────────────────────────────────────────────────────────────────
def test_the_first_scan_of_the_day_seeds_and_books_nothing():
    out = S.decide(scan(directional=[call()]), None, NOW, CFG, IDEA_CFG)
    assert out["idea"] is None
    assert out["reason"] == "first scan today"
    assert out["state"]["keys"] == [S.trade_key(T.normalize(call()))]


def test_a_new_strong_candidate_is_picked():
    out = S.decide(scan(directional=[call()]), seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"]["symbol"] == "NVDA"
    assert out["reason"] == "picked"


def test_a_candidate_already_in_the_previous_scan_is_not_new():
    out = S.decide(scan(directional=[call()]), seeded([call()]), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "nothing new"


def test_a_good_grade_does_not_qualify_at_min_grade_strong():
    out = S.decide(scan(directional=[call(grade="Good")]), seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "nothing new"


def test_the_same_scan_is_checked_once():
    state = S.decide(scan(directional=[call()]), seeded(), NOW, CFG, IDEA_CFG)["state"]
    again = S.decide(scan(directional=[call()]), state, NOW, CFG, IDEA_CFG)
    assert again["idea"] is None and again["reason"] == "scan already checked"


def test_a_stale_scan_books_nothing():
    old = scan(at=NOW - dt.timedelta(minutes=50), directional=[call()])
    out = S.decide(old, seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"].startswith("stale scan")


def test_outside_the_session_the_keys_move_but_nothing_is_booked():
    out = S.decide(scan(directional=[call()]), seeded(), NOW, CFG, IDEA_CFG,
                   in_session=False)
    assert out["idea"] is None and out["reason"] == "outside the regular session"
    assert out["state"]["keys"] == [S.trade_key(T.normalize(call()))]


def test_a_new_day_starts_from_an_empty_state():
    yesterday = seeded([call()])
    yesterday["date"] = "2026-09-16"
    out = S.decide(scan(directional=[call()]), yesterday, NOW, CFG, IDEA_CFG)
    assert out["reason"] == "first scan today"


# ── already posted today ────────────────────────────────────────────────────
def _post(at, symbol, key=None, kind="clock"):
    return {"at": at.isoformat(), "symbol": symbol, "key": key, "kind": kind}


def test_a_symbol_posted_today_is_skipped():
    posts = [_post(NOW - dt.timedelta(hours=2), "NVDA")]
    out = S.decide(scan(directional=[call()]), seeded(posts=posts), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "already posted today"


def test_with_one_per_symbol_off_only_the_same_trade_is_skipped():
    cfg = {**CFG, "one_per_symbol": False}
    other = _post(NOW - dt.timedelta(hours=2), "NVDA", key="something else")
    out = S.decide(scan(directional=[call()]), seeded(posts=[other]), NOW, cfg, IDEA_CFG)
    assert out["idea"]["symbol"] == "NVDA"
    same = _post(NOW - dt.timedelta(hours=2), "NVDA", key=S.trade_key(T.normalize(call())))
    out = S.decide(scan(directional=[call()]), seeded(posts=[same]), NOW, cfg, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "already posted today"


def test_the_hourly_post_is_counted_once_it_is_seen():
    clock = {"status": "posted", "at": (NOW - dt.timedelta(minutes=5)).isoformat(),
             "idea": T.normalize(call(symbol="MU"))}
    state = S.note_clock(seeded(), clock)
    state = S.note_clock(state, clock)                 # seen twice, counted once
    assert [p["symbol"] for p in state["posts"]] == ["MU"]
    assert state["posts"][0]["kind"] == "clock"


def test_a_skipped_hourly_result_is_not_a_post():
    clock = {"status": "skipped", "at": NOW.isoformat(), "idea": None}
    assert S.note_clock(seeded(), clock)["posts"] == []


# ── the volatility floor ────────────────────────────────────────────────────
def test_a_credit_spread_below_the_vol_rank_floor_is_skipped():
    out = S.decide(scan(swing=[pcs(iv_rank=30.0)]), seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "Vol Rank below 50"


def test_a_credit_spread_above_the_floor_is_picked():
    out = S.decide(scan(swing=[pcs(iv_rank=70.0)]), seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"]["type"] == "PCS"


def test_a_long_premium_trade_is_not_held_to_the_floor():
    out = S.decide(scan(directional=[call(iv_rank=10.0)]), seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"]["symbol"] == "NVDA"


def test_an_unknown_vol_rank_skips_the_floor():
    out = S.decide(scan(swing=[pcs(iv_rank=None)]), seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"]["type"] == "PCS"


def test_the_scan_level_vol_rank_is_read_when_the_row_has_none():
    s = scan(swing=[pcs(iv_rank=None)])
    s["iv_data"] = {"SPY": {"iv_rank": 20.0}}
    out = S.decide(s, seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "Vol Rank below 50"


def test_a_floor_of_zero_is_off():
    out = S.decide(scan(swing=[pcs(iv_rank=1.0)]), seeded(), NOW,
                   {**CFG, "min_vol_rank": 0.0}, IDEA_CFG)
    assert out["idea"]["type"] == "PCS"


# ── the feed's limits ───────────────────────────────────────────────────────
def test_two_posts_in_the_last_hour_hold_the_pick():
    posts = [_post(NOW - dt.timedelta(minutes=50), "AAA"),
             _post(NOW - dt.timedelta(minutes=25), "BBB", kind="shadow")]
    out = S.decide(scan(directional=[call()]), seeded(posts=posts), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "2 posts in the last hour"


def test_a_post_inside_the_gap_holds_the_pick():
    posts = [_post(NOW - dt.timedelta(minutes=10), "AAA")]
    out = S.decide(scan(directional=[call()]), seeded(posts=posts), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "last post 10 min ago"


def test_the_day_cap_holds_the_pick():
    posts = [_post(NOW - dt.timedelta(hours=1, minutes=i), f"S{i}") for i in range(10)]
    out = S.decide(scan(directional=[call()]), seeded(posts=posts), NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "10 posts today"


def test_a_post_just_past_the_gap_and_hour_is_fine():
    posts = [_post(NOW - dt.timedelta(minutes=61), "AAA"),
             _post(NOW - dt.timedelta(minutes=20), "BBB")]
    out = S.decide(scan(directional=[call()]), seeded(posts=posts), NOW, CFG, IDEA_CFG)
    assert out["idea"]["symbol"] == "NVDA"


# ── which one, and recording it ─────────────────────────────────────────────
def test_the_higher_score_is_picked_and_only_one_per_scan():
    rows = [call(symbol="AMD", score=78.0), call(symbol="NVDA", score=80.5)]
    out = S.decide(scan(directional=rows), seeded(), NOW, CFG, IDEA_CFG)
    assert out["idea"]["symbol"] == "NVDA"


def test_a_recorded_pick_counts_toward_the_limits_and_the_symbol_rule():
    out = S.decide(scan(directional=[call()]), seeded(), NOW, CFG, IDEA_CFG)
    state = S.record_pick(out["state"], out["idea"], NOW)
    assert state["posts"][-1] == {"at": NOW.isoformat(), "symbol": "NVDA",
                                  "key": S.trade_key(out["idea"]), "kind": "shadow"}


def test_a_malformed_state_reads_as_a_fresh_day():
    out = S.decide(scan(directional=[call()]), {"date": TODAY, "posts": "junk"},
                   NOW, CFG, IDEA_CFG)
    assert out["reason"] == "first scan today"


def test_no_scan_books_nothing_and_keeps_the_state():
    state = seeded([call()])
    out = S.decide(None, state, NOW, CFG, IDEA_CFG)
    assert out["idea"] is None and out["reason"] == "no scan"
    assert out["state"]["keys"] == state["keys"]
