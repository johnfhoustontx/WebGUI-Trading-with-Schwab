"""How each posted trade idea did - under the app's own exit rules.

Measured from the STOCK price only; no option quote is read or published. Each
idea's option is modelled with Black-Scholes at the implied volatility its entry
price implies, on every 1-minute stock bar since the post. The first bar that
reaches the profit target (``[stops] tp_frac``, 50%: of the DEBIT for a long
option) closes it at the target; a stop, where the structure has one, likewise.
Otherwise it settles at intrinsic on the expiry-day close.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import site_ideas as S

_CT = ZoneInfo("America/Chicago")
POSTED = dt.datetime(2026, 9, 29, 10, 35, tzinfo=_CT)
EXP = "2026-10-02"


def _long_call(**over):
    idea = {"symbol": "MU", "label": "Long Call", "grade": "Good", "type": "LONG_CALL",
            "expiration": EXP,
            "legs": [{"side": "long", "kind": "call", "strike": 190.0,
                      "expiration": EXP, "qty": 1}],
            "entry_cash": -411.0, "max_loss": 411.0, "max_profit": None, "spot": 186.4}
    idea.update(over)
    return idea


def _pcs():
    return {"symbol": "QQQ", "label": "Put Credit Spread", "grade": "Good", "type": "PCS",
            "expiration": EXP,
            "legs": [{"side": "short", "kind": "put", "strike": 740.0, "expiration": EXP, "qty": 1},
                     {"side": "long", "kind": "put", "strike": 735.0, "expiration": EXP, "qty": 1}],
            "entry_cash": 122.0, "max_loss": 378.0, "max_profit": 122.0, "spot": 759.38}


def _row(idea, posted=POSTED, **extra):
    e = S.entry(idea["symbol"], idea["label"], idea["grade"], "cap", posted)
    e.update(S.entry_facts(idea, posted=posted))
    e.update(extra)
    return e


def _manifest(*rows, day="2026-09-29"):
    return {"updated": "u", "days": [{"date": day, "ideas": list(rows)}]}


def _ms(t):
    return int(t.timestamp() * 1000)


def _bars(start, prices):
    """One 1-minute bar per price, starting at ``start``."""
    return [{"datetime": _ms(start + dt.timedelta(minutes=n)), "open": p, "high": p,
             "low": p, "close": p} for n, p in enumerate(prices)]


# ── entry facts ────────────────────────────────────────────────────────────
def test_entry_facts_keep_what_the_card_already_prints():
    f = S.entry_facts(_long_call(), posted=POSTED)
    assert f == {"legs": [{"side": "long", "kind": "call", "strike": 190.0, "qty": 1}],
                 "type": "LONG_CALL", "expiration": EXP, "entry_cash": -411.0,
                 "max_loss": 411.0, "max_profit": None, "spot": 186.4, "approx": False,
                 "posted": "2026-09-29T10:35:00-05:00"}


def test_entry_facts_are_empty_when_the_idea_cannot_be_measured():
    assert S.entry_facts(_long_call(legs=None), posted=POSTED) == {}
    assert S.entry_facts(_long_call(entry_cash=None), posted=POSTED) == {}
    assert S.entry_facts(_long_call(max_loss=None), posted=POSTED) == {}


# ── the exit rules, from config/trade_mgmt.toml ────────────────────────────
def test_a_long_option_targets_half_the_debit_and_has_no_stop():
    assert S.exit_levels(S.entry_facts(_long_call(), posted=POSTED)) == (205.5, None)


def test_a_credit_spread_targets_half_the_credit_and_stops_at_twice_it():
    assert S.exit_levels(S.entry_facts(_pcs(), posted=POSTED)) == (61.0, 244.0)


def test_the_levels_follow_the_config(monkeypatch):
    from shared import trade_mgmt
    monkeypatch.setattr(trade_mgmt, "structure_rules",
                        lambda s: {"tp_frac": 0.8, "debit_stop_frac": 0.6,
                                   "loss_rules": True, "stop_mult": 2.0})
    assert S.exit_levels(S.entry_facts(_long_call(), posted=POSTED)) == (
        pytest.approx(328.8), pytest.approx(246.6))


# ── the Market Scanner's other structures ──────────────────────────────────
# Figures from prod's scan of 2026-10-07, per contract with commission in.
def _other(kind, legs, cash, max_profit, max_loss, spot):
    return {"symbol": "X", "label": kind, "grade": "Good", "type": kind, "expiration": EXP,
            "legs": [{"side": side, "kind": k, "strike": strike, "expiration": EXP, "qty": qty}
                     for side, qty, strike, k in legs],
            "entry_cash": cash, "max_loss": max_loss, "max_profit": max_profit, "spot": spot}


_OTHER = {
    "BEAR_PUT": _other("BEAR_PUT", (("long", 1, 70.0, "put"), ("short", 1, 68.0, "put")),
                       -99.6, 100.4, 99.6, 69.08),
    "BUTTERFLY_PUT": _other("BUTTERFLY_PUT", (("long", 1, 12.0, "put"), ("short", 2, 12.5, "put"),
                                              ("long", 1, 13.0, "put")),
                            -21.2, 28.8, 21.2, 12.745),
    "IRON_BUTTERFLY": _other("IRON_BUTTERFLY",
                             (("long", 1, 12.0, "put"), ("short", 1, 12.5, "put"),
                              ("short", 1, 12.5, "call"), ("long", 1, 13.0, "call")),
                             32.8, 32.8, 17.2, 12.745),
    "LONG_STRADDLE": _other("LONG_STRADDLE", (("long", 1, 192.5, "call"),
                                              ("long", 1, 192.5, "put")),
                            -586.6, None, 586.6, 191.575),
    "PUT_BACKSPREAD": _other("PUT_BACKSPREAD", (("short", 1, 725.0, "put"),
                                                ("long", 2, 720.0, "put")),
                             0.1, 71500.1, 499.9, 724.175),
    "CALL_BACKSPREAD": _other("CALL_BACKSPREAD", (("short", 1, 725.0, "call"),
                                                  ("long", 2, 730.0, "call")),
                              5.1, None, 494.9, 724.175),
    "CALL_BACKSPREAD for a debit": _other("CALL_BACKSPREAD", (("short", 1, 725.0, "call"),
                                                              ("long", 2, 730.0, "call")),
                                          -53.9, None, 553.9, 724.175),
}


@pytest.mark.parametrize("name", ["PUT_BACKSPREAD", "CALL_BACKSPREAD",
                                  "CALL_BACKSPREAD for a debit"])
def test_a_backspread_is_held_to_expiry(name):
    """Half of a few dollars' credit is not what a backspread is for: closing it
    there would book a win of cents on a trade opened for a large move."""
    assert S.exit_levels(S.entry_facts(_OTHER[name], posted=POSTED)) == (None, None)


def _tracked_code(idea, pnl):
    """What the app's own tracked-row rule (``structure_marks.recommend``) does
    with this structure at ``pnl`` dollars a contract."""
    import sys

    import repo_paths
    if str(repo_paths.OPTIONS_SCANNER) not in sys.path:
        sys.path.insert(0, str(repo_paths.OPTIONS_SCANNER))
    import structure_marks
    mp = idea["max_profit"]
    row = {"strategy": idea["type"], "entry_credit": idea["entry_cash"] / 100.0,
           "entry_max_profit": None if mp is None else mp / 100.0,
           "legs_json": json.dumps(idea["legs"])}
    return structure_marks.recommend(row, pnl)["code"]


@pytest.mark.parametrize("name", sorted(_OTHER))
def test_the_site_follows_the_same_levels_as_the_tracked_rows(name):
    """The page says each idea is followed "under the app's own exit rules". For
    the structures the app only TRACKS, those rules are ``structure_marks``'s, so
    the two must close the same structure at the same profit and the same loss."""
    idea = _OTHER[name]
    target, stop = S.exit_levels(S.entry_facts(idea, posted=POSTED))
    if target is None:
        assert _tracked_code(idea, 1e9) == "HOLD"
    else:
        assert _tracked_code(idea, target + 0.01) == "TARGET_HIT"
        assert _tracked_code(idea, target - 0.02) == "HOLD"
    if stop is None:
        assert _tracked_code(idea, -1e9) == "HOLD"
    else:
        assert _tracked_code(idea, -stop - 0.01) == "MONEY_STOP"
        assert _tracked_code(idea, -stop + 0.02) == "HOLD"


# ── the model ──────────────────────────────────────────────────────────────
def test_the_entry_iv_reprices_the_entry():
    facts = S.entry_facts(_long_call(), posted=POSTED)
    iv = S.solve_iv(facts)
    assert 0.05 < iv < 3.0
    assert S.model_pnl(facts, iv, 186.4, POSTED) == pytest.approx(0.0, abs=0.5)


def test_no_entry_spot_means_no_model():
    assert S.solve_iv(S.entry_facts(_long_call(spot=None), posted=POSTED)) is None


def test_a_rally_through_the_target_closes_it_at_the_target_minute():
    facts = S.entry_facts(_long_call(), posted=POSTED)
    iv = S.solve_iv(facts)
    start = POSTED + dt.timedelta(minutes=1)
    bars = _bars(start, [186.4 + 0.5 * n for n in range(40)])      # to ~206
    hit, last = S.scan(facts, iv, bars, POSTED, POSTED + dt.timedelta(hours=2))
    assert hit["status"] == "target" and hit["pnl"] == 205.5 and hit["pnl_pct"] == 50.0
    closed = dt.datetime.fromisoformat(hit["closed_at"])
    assert start < closed < start + dt.timedelta(minutes=40)
    assert S.model_pnl(facts, iv, hit["spot"], closed) >= 205.5


def test_a_quiet_tape_hits_nothing_and_records_how_far_it_looked():
    facts = S.entry_facts(_long_call(), posted=POSTED)
    iv = S.solve_iv(facts)
    bars = _bars(POSTED + dt.timedelta(minutes=1), [186.0] * 30)
    hit, last = S.scan(facts, iv, bars, POSTED, POSTED + dt.timedelta(hours=1))
    assert hit is None and last == POSTED + dt.timedelta(minutes=30)


def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop(monkeypatch):
    """A stop is a market order: after an overnight gap it fills at the opening
    price, which is WORSE than the stop level. Booking the level would flatter
    every gap-down loss - the measured case on prod, where most stops fired on the
    08:30 bar."""
    from shared import trade_mgmt
    monkeypatch.setattr(trade_mgmt, "structure_rules",
                        lambda s: {"tp_frac": 0.5, "debit_stop_frac": 0.5})
    facts = S.entry_facts(_long_call(), posted=POSTED)
    iv = S.solve_iv(facts)
    next_open = dt.datetime(2026, 9, 30, 8, 30, tzinfo=_CT)
    bars = [{"datetime": _ms(next_open), "open": 170.0, "high": 171.0, "low": 169.0,
             "close": 170.5}]
    hit, _ = S.scan(facts, iv, bars, POSTED, next_open + dt.timedelta(hours=1))
    assert hit["status"] == "stop"
    assert hit["pnl"] == pytest.approx(S.model_pnl(facts, iv, 170.0, next_open), abs=0.01)
    assert hit["pnl"] < -205.5


def test_a_target_is_booked_at_the_target_even_after_a_gap_up():
    """The cautious reading on the winning side: a resting limit fills AT its
    price, so a gap past it is not credited."""
    facts = S.entry_facts(_long_call(), posted=POSTED)
    iv = S.solve_iv(facts)
    next_open = dt.datetime(2026, 9, 30, 8, 30, tzinfo=_CT)
    bars = [{"datetime": _ms(next_open), "open": 215.0, "high": 216.0, "low": 214.0,
             "close": 215.0}]
    hit, _ = S.scan(facts, iv, bars, POSTED, next_open + dt.timedelta(hours=1))
    assert hit["status"] == "target" and hit["pnl"] == 205.5


def test_bars_before_the_post_or_after_the_close_are_ignored():
    facts = S.entry_facts(_long_call(), posted=POSTED)
    iv = S.solve_iv(facts)
    early = _bars(POSTED - dt.timedelta(minutes=30), [260.0] * 10)   # before the post
    hit, _ = S.scan(facts, iv, early, POSTED, POSTED + dt.timedelta(hours=1))
    assert hit is None


# ── applying a refresh ─────────────────────────────────────────────────────
def test_an_open_idea_shows_the_modelled_value_now():
    m = _manifest(_row(_long_call()))
    now = POSTED + dt.timedelta(hours=1)
    bars = {"MU": _bars(POSTED + dt.timedelta(minutes=1), [187.0] * 30)}
    out = S.apply_results(m, {"MU": 188.0}, {}, bars, now)
    idea = out["days"][0]["ideas"][0]
    r = idea["result"]
    assert r["status"] == "open" and r["basis"] == "model"
    assert r["spot"] == 188.0 and r["target"] == 205.5
    assert r["pnl"] == pytest.approx(S.model_pnl(S.entry_facts(_long_call(), posted=POSTED),
                                                 idea["iv"], 188.0, now), abs=0.01)
    assert idea["checked_to"] == (POSTED + dt.timedelta(minutes=30)).isoformat()


def test_a_target_hit_is_final():
    m = _manifest(_row(_long_call()))
    now = POSTED + dt.timedelta(hours=1)
    bars = {"MU": _bars(POSTED + dt.timedelta(minutes=1), [186.4 + 0.5 * n for n in range(40)])}
    out = S.apply_results(m, {"MU": 200.0}, {}, bars, now)
    hit = out["days"][0]["ideas"][0]["result"]
    assert hit["status"] == "target"
    later = S.apply_results(out, {"MU": 150.0}, {("MU", EXP): 150.0},
                            {"MU": []}, dt.datetime(2026, 10, 5, 9, 0, tzinfo=_CT))
    assert later["days"][0]["ideas"][0]["result"] == hit


def test_expiry_without_a_hit_settles_at_intrinsic_once_the_scan_is_complete():
    row = _row(_long_call(), checked_to="2026-10-02T14:59:00-05:00", iv=0.5)
    now = dt.datetime(2026, 10, 5, 9, 0, tzinfo=_CT)
    out = S.apply_results(_manifest(row), {}, {("MU", EXP): 184.1}, {"MU": []}, now)
    r = out["days"][0]["ideas"][0]["result"]
    assert r["status"] == "expired" and r["pnl"] == -411.0 and r["settled"] == EXP


def test_expiry_waits_for_the_bars_before_settling():
    row = _row(_long_call(), checked_to="2026-10-01T15:00:00-05:00", iv=0.5)
    now = dt.datetime(2026, 10, 2, 16, 0, tzinfo=_CT)                 # bars not in yet
    out = S.apply_results(_manifest(row), {}, {("MU", EXP): 184.1}, {"MU": []}, now)
    assert (out["days"][0]["ideas"][0].get("result") or {}).get("status") != "expired"
    much_later = dt.datetime(2026, 10, 6, 9, 0, tzinfo=_CT)          # gave up waiting
    out = S.apply_results(_manifest(row), {}, {("MU", EXP): 184.1}, {"MU": []}, much_later)
    assert out["days"][0]["ideas"][0]["result"]["status"] == "expired"


def test_a_missing_entry_price_is_filled_from_the_minute_bars():
    row = _row(_long_call(spot=None))
    bars = {"MU": _bars(POSTED - dt.timedelta(minutes=2), [186.0, 186.2, 186.4, 186.5])}
    out = S.apply_results(_manifest(row), {"MU": 187.0}, {}, bars,
                          POSTED + dt.timedelta(minutes=30))
    idea = out["days"][0]["ideas"][0]
    assert idea["spot"] == 186.4 and idea["iv"] and idea["result"]["basis"] == "model"


def test_without_a_model_the_open_line_falls_back_to_the_expiry_payoff():
    row = _row(_long_call(spot=None))                                 # nothing to solve from
    out = S.apply_results(_manifest(row), {"MU": 196.0}, {}, {"MU": []},
                          POSTED + dt.timedelta(minutes=30))
    r = out["days"][0]["ideas"][0]["result"]
    assert r["status"] == "open" and r["basis"] == "expiry" and r["pnl"] == 189.0


def test_a_missing_quote_keeps_the_last_result():
    old = {"status": "open", "basis": "model", "spot": 190.0, "pnl": -50.0,
           "pnl_pct": -12.2, "move_pct": 1.9, "target": 205.5, "as_of": "x"}
    row = _row(_long_call(), result=dict(old), iv=0.5,
               checked_to=(POSTED + dt.timedelta(minutes=5)).isoformat())
    out = S.apply_results(_manifest(row), {"MU": None}, {}, {"MU": []},
                          POSTED + dt.timedelta(minutes=30))
    assert out["days"][0]["ideas"][0]["result"] == old


# ── what a refresh needs, and the refresh itself ───────────────────────────
def test_due_asks_for_bars_from_where_each_symbol_last_looked():
    a = _row(_long_call(), checked_to="2026-09-29T12:00:00-05:00")
    b = _row(_long_call(), POSTED + dt.timedelta(hours=1))
    m = _manifest(a, b, _row(_pcs()))
    quotes, settle, since = S.due(m, POSTED + dt.timedelta(hours=3))
    assert quotes == {"MU", "QQQ"} and settle == set()
    assert since["MU"] == POSTED + dt.timedelta(hours=1)             # the earlier of the two
    assert since["QQQ"] == POSTED


def test_minute_days_rounds_up_to_what_schwab_accepts():
    now = dt.datetime(2026, 9, 29, 12, 0, tzinfo=_CT)
    assert S.minute_days(now - dt.timedelta(hours=2), now) == 1
    assert S.minute_days(now - dt.timedelta(days=5), now) == 10
    assert S.minute_days(now - dt.timedelta(days=30), now) == 10
    assert S.minute_days(now - dt.timedelta(days=2), now) == 3   # 2 days back + today


def test_refresh_fetches_each_symbol_once_and_rewrites_the_manifest(tmp_path):
    (tmp_path / S.MANIFEST).write_text(json.dumps(_manifest(_row(_long_call()), _row(_pcs()))))
    quote_calls, minute_calls = [], []

    def quotes(symbols):
        quote_calls.append(sorted(symbols))
        return {"MU": 188.0, "QQQ": 750.0}

    def minutes(symbol, days):
        minute_calls.append((symbol, days))
        price = {"MU": 187.0, "QQQ": 758.0}[symbol]
        return _bars(POSTED + dt.timedelta(minutes=1), [price] * 5)

    now = POSTED + dt.timedelta(hours=1)
    n = S.refresh(quotes, lambda s, d: None, now, root=tmp_path, minutes_fn=minutes)
    assert n == 2 and quote_calls == [["MU", "QQQ"]]
    assert sorted(minute_calls) == [("MU", 1), ("QQQ", 1)]
    m = json.loads((tmp_path / S.MANIFEST).read_text())
    assert all(i["result"]["status"] == "open" for i in m["days"][0]["ideas"])


def test_refresh_with_nothing_open_calls_nobody(tmp_path):
    def boom(*a):
        raise AssertionError("no call expected")
    assert S.refresh(boom, boom, POSTED, root=tmp_path, minutes_fn=boom) == 0


def test_refresh_never_raises(tmp_path):
    (tmp_path / S.MANIFEST).write_text(json.dumps(_manifest(_row(_long_call()))))

    def boom(*a):
        raise RuntimeError("proxy down")
    assert S.refresh(boom, boom, POSTED, root=tmp_path, minutes_fn=boom) == 0


def test_a_post_stores_the_entry_facts(tmp_path):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (240, 135)).save(buf, "PNG")
    assert S.publish(_long_call(), buf.getvalue(), "cap", POSTED, root=tmp_path)
    first = json.loads((tmp_path / S.MANIFEST).read_text())["days"][0]["ideas"][0]
    assert first["legs"][0]["strike"] == 190.0 and first["entry_cash"] == -411.0
    assert first["spot"] == 186.4 and first["type"] == "LONG_CALL"
    assert first["posted"] == POSTED.isoformat()


# ── candles (raw Schwab /pricehistory: epoch-ms stamps) ────────────────────
def test_close_on_reads_the_candle_stamped_that_central_date():
    day = lambda d: _ms(dt.datetime(2026, 10, d, tzinfo=_CT))   # noqa: E731
    candles = [{"datetime": day(1), "close": 180.0}, {"datetime": day(2), "close": 184.1}]
    assert S.close_on(candles, "2026-10-02") == 184.1
    assert S.close_on(candles, "2026-10-03") is None
    assert S.close_on([{"datetime": "x"}, None], "2026-10-02") is None


def test_price_at_takes_the_last_bar_at_or_before_the_post():
    bars = _bars(POSTED - dt.timedelta(minutes=2), [186.1, 186.4])
    bars.append({"datetime": _ms(POSTED + dt.timedelta(minutes=1)), "close": 187.0})
    assert S.price_at(bars, POSTED) == 186.4
    assert S.price_at(bars, POSTED - dt.timedelta(hours=1)) is None
    assert S.price_at(bars, POSTED + dt.timedelta(hours=3)) is None
