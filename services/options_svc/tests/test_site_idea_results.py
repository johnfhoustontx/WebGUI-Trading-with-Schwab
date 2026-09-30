"""How each posted trade idea did, measured from the STOCK price alone.

No option quote is ever read or published: an open idea shows the stock's move and
the expiry payoff at today's price; an expired one settles at intrinsic against the
expiry-day close.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import site_ideas as S

_CT = ZoneInfo("America/Chicago")
POSTED = dt.datetime(2026, 9, 29, 10, 35, tzinfo=_CT)


def _long_call(**over):
    idea = {"symbol": "MU", "label": "Long Call", "grade": "Good", "expiration": "2026-10-02",
            "legs": [{"side": "long", "kind": "call", "strike": 190.0,
                      "expiration": "2026-10-02", "qty": 1}],
            "entry_cash": -411.0, "max_loss": 411.0, "spot": 186.4}
    idea.update(over)
    return idea


def _pcs():
    return {"symbol": "QQQ", "label": "Put Credit Spread", "grade": "Good",
            "expiration": "2026-10-02",
            "legs": [{"side": "short", "kind": "put", "strike": 740.0, "expiration": "2026-10-02", "qty": 1},
                     {"side": "long", "kind": "put", "strike": 735.0, "expiration": "2026-10-02", "qty": 1}],
            "entry_cash": 122.0, "max_loss": 378.0, "spot": 759.38}


def _manifest(*facts_rows, day="2026-09-29"):
    ideas = []
    for n, (idea, extra) in enumerate(facts_rows):
        e = S.entry(idea["symbol"], idea["label"], idea["grade"], "cap",
                    POSTED + dt.timedelta(hours=n))
        e.update(S.entry_facts(idea))
        e.update(extra)
        ideas.append(e)
    return {"updated": "u", "days": [{"date": day, "ideas": ideas}]}


# ── entry facts ────────────────────────────────────────────────────────────
def test_entry_facts_keep_what_the_card_already_prints():
    f = S.entry_facts(_long_call())
    assert f == {"legs": [{"side": "long", "kind": "call", "strike": 190.0, "qty": 1}],
                 "expiration": "2026-10-02", "entry_cash": -411.0, "max_loss": 411.0,
                 "spot": 186.4, "approx": False}


def test_entry_facts_are_empty_when_the_idea_cannot_be_measured():
    assert S.entry_facts(_long_call(legs=None)) == {}
    assert S.entry_facts(_long_call(entry_cash=None)) == {}
    assert S.entry_facts(_long_call(max_loss=None)) == {}


# ── one result ─────────────────────────────────────────────────────────────
def test_an_open_long_call_reads_the_payoff_at_this_price():
    r = S.open_result(S.entry_facts(_long_call()), 196.0, POSTED)
    assert r == {"status": "open", "spot": 196.0, "move_pct": 5.15, "pnl": 189.0,
                 "pnl_pct": 46.0, "as_of": POSTED.isoformat(timespec="seconds")}


def test_an_open_credit_spread_above_the_short_keeps_the_credit():
    r = S.open_result(S.entry_facts(_pcs()), 750.0, POSTED)
    assert r["pnl"] == 122.0 and r["pnl_pct"] == 32.3       # one decimal


def test_a_settled_long_call_below_the_strike_loses_the_whole_debit():
    r = S.settled_result(S.entry_facts(_long_call()), 184.1, "2026-10-02", POSTED)
    assert r["status"] == "expired" and r["settled"] == "2026-10-02"
    assert r["pnl"] == -411.0 and r["pnl_pct"] == -100.0 and r["spot"] == 184.1


def test_no_entry_spot_means_no_move_never_a_zero():
    facts = dict(S.entry_facts(_long_call()), spot=None)
    assert S.open_result(facts, 196.0, POSTED)["move_pct"] is None


# ── what a refresh needs ───────────────────────────────────────────────────
def test_due_splits_open_quotes_from_settlements():
    m = _manifest((_long_call(), {}), (_pcs(), {}))
    before_close = dt.datetime(2026, 10, 2, 14, 0, tzinfo=_CT)
    assert S.due(m, before_close) == ({"MU", "QQQ"}, set())
    after_close = dt.datetime(2026, 10, 2, 15, 5, tzinfo=_CT)
    assert S.due(m, after_close) == (set(), {("MU", "2026-10-02"), ("QQQ", "2026-10-02")})
    next_day = dt.datetime(2026, 10, 5, 9, 0, tzinfo=_CT)
    assert S.due(m, next_day) == (set(), {("MU", "2026-10-02"), ("QQQ", "2026-10-02")})


def test_due_skips_settled_ideas_and_ideas_without_facts():
    settled = {"result": {"status": "expired", "pnl": -411.0}}
    m = _manifest((_long_call(), settled), (_pcs(), {}))
    m["days"][0]["ideas"].append(S.entry("AAPL", "Long Call", "Good", "cap", POSTED))
    assert S.due(m, POSTED) == ({"QQQ"}, set())


# ── applying a refresh ─────────────────────────────────────────────────────
def test_apply_marks_open_ideas_and_settles_expired_ones():
    m = _manifest((_long_call(), {}), (_pcs(), {}))
    now = dt.datetime(2026, 10, 2, 15, 10, tzinfo=_CT)
    out = S.apply_results(m, {}, {("MU", "2026-10-02"): 184.1, ("QQQ", "2026-10-02"): 751.0}, now)
    mu, qqq = out["days"][0]["ideas"]
    assert mu["result"]["status"] == "expired" and mu["result"]["pnl"] == -411.0
    assert qqq["result"]["status"] == "expired" and qqq["result"]["pnl"] == 122.0


def test_a_missing_quote_keeps_the_last_result_and_its_time():
    old = {"status": "open", "spot": 190.0, "move_pct": 1.93, "pnl": -411.0,
           "pnl_pct": -100.0, "as_of": "2026-09-29T11:00:00-05:00"}
    m = _manifest((_long_call(), {"result": dict(old)}))
    out = S.apply_results(m, {"MU": None}, {}, POSTED)
    assert out["days"][0]["ideas"][0]["result"] == old


def test_a_settlement_with_no_close_yet_stays_open_until_the_next_slot():
    m = _manifest((_long_call(), {}))
    now = dt.datetime(2026, 10, 2, 15, 6, tzinfo=_CT)
    out = S.apply_results(m, {}, {("MU", "2026-10-02"): None}, now)
    assert "result" not in out["days"][0]["ideas"][0]


def test_an_expired_result_is_final():
    final = {"status": "expired", "spot": 184.1, "pnl": -411.0, "pnl_pct": -100.0,
             "move_pct": -1.23, "as_of": "x", "settled": "2026-10-02"}
    m = _manifest((_long_call(), {"result": dict(final)}))
    out = S.apply_results(m, {"MU": 250.0}, {("MU", "2026-10-02"): 250.0},
                          dt.datetime(2026, 10, 5, 9, 0, tzinfo=_CT))
    assert out["days"][0]["ideas"][0]["result"] == final


# ── the refresh itself (disk + lock) ───────────────────────────────────────
def test_refresh_reads_quotes_once_and_rewrites_the_manifest(tmp_path):
    (tmp_path / S.MANIFEST).write_text(json.dumps(_manifest((_long_call(), {}), (_pcs(), {}))))
    calls = []

    def quotes(symbols):
        calls.append(sorted(symbols))
        return {"MU": 196.0, "QQQ": 750.0}

    n = S.refresh(quotes, lambda sym, day: None, POSTED, root=tmp_path)
    assert n == 2 and calls == [["MU", "QQQ"]]
    m = json.loads((tmp_path / S.MANIFEST).read_text())
    assert [i["result"]["pnl"] for i in m["days"][0]["ideas"]] == [189.0, 122.0]


def test_refresh_with_nothing_open_calls_nobody(tmp_path):
    def boom(*a):
        raise AssertionError("no call expected")
    assert S.refresh(boom, boom, POSTED, root=tmp_path) == 0      # no manifest at all


def test_refresh_never_raises(tmp_path):
    (tmp_path / S.MANIFEST).write_text(json.dumps(_manifest((_long_call(), {}))))

    def boom(symbols):
        raise RuntimeError("proxy down")
    assert S.refresh(boom, boom, POSTED, root=tmp_path) == 0


def test_a_post_stores_the_entry_facts(tmp_path):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (240, 135)).save(buf, "PNG")
    assert S.publish(_long_call(), buf.getvalue(), "cap", POSTED, root=tmp_path)
    first = json.loads((tmp_path / S.MANIFEST).read_text())["days"][0]["ideas"][0]
    assert first["legs"][0]["strike"] == 190.0 and first["entry_cash"] == -411.0
    assert first["spot"] == 186.4 and first["approx"] is False


# ── candles (raw Schwab /pricehistory: epoch-ms stamps) ────────────────────
def _ms(y, mo, d, h=0, mi=0, tz=_CT):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=tz).timestamp() * 1000)


def test_close_on_reads_the_candle_stamped_that_central_date():
    candles = [{"datetime": _ms(2026, 10, 1), "close": 180.0},
               {"datetime": _ms(2026, 10, 2), "close": 184.1},
               {"datetime": _ms(2026, 10, 5), "close": 190.0}]
    assert S.close_on(candles, "2026-10-02") == 184.1
    assert S.close_on(candles, "2026-10-03") is None
    assert S.close_on([{"datetime": "x"}, None], "2026-10-02") is None


def test_price_at_takes_the_last_bar_at_or_before_the_post():
    candles = [{"datetime": _ms(2026, 9, 29, 10, 33), "close": 186.1},
               {"datetime": _ms(2026, 9, 29, 10, 34), "close": 186.4},
               {"datetime": _ms(2026, 9, 29, 10, 36), "close": 187.0}]
    assert S.price_at(candles, POSTED) == 186.4
    assert S.price_at(candles, POSTED - dt.timedelta(hours=1)) is None
    # a bar hours stale is not "the price at the post"
    assert S.price_at(candles, POSTED + dt.timedelta(hours=3)) is None
