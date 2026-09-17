"""The hourly trade idea: selection, normalisation, the card, the push, the slot.

The fixtures are shaped like the rows prod's ``cache:options:scan`` actually
carried on 2026-09-16 -- a credit row keeps its money PER SHARE and its strikes in
flat fields; a directional row keeps its money PER CONTRACT and a ``legs`` list.
Those two units are the trap this feature most easily falls into, so the
economics tests assert dollar figures from each shape.
"""
import datetime as dt
import io
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import handlers, push_notify, scheduler
from services.options_svc import trade_idea as T
from services.options_svc import trade_idea_card as C
from shared import market_calendar as mc

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 17, 10, 35, tzinfo=_CT)     # a Thursday


def pcs(**over):
    row = {"id": "SPY_PCS_2026-09-25_740.0_735.0", "symbol": "SPY", "type": "PCS",
           "trade_type": "SWING", "expiration": "2026-09-25", "dte": 8,
           "short_strike": 740.0, "long_strike": 735.0, "width": 5.0,
           "credit": 1.25, "commission": 2.6, "net_credit": 1.224,
           "pop_pct": 78.0, "underlying_price": 759.38,
           "composite_score": 70.0, "grade": "Good", "em_to_expiry": 12.7}
    row.update(over)
    return row


def long_put(**over):
    row = {"id": "MU_LONG_PUT_2026-09-21_940.0", "symbol": "MU", "type": "LONG_PUT",
           "strategy_label": "Long Put", "bias": "bearish",
           "legs": [{"kind": "put", "side": "long", "strike": 940.0,
                     "expiration": "2026-09-21", "qty": 1}],
           "expiration": "2026-09-21", "dte": 5, "pop_pct": 38.8,
           "underlying_price": 932.95, "net_debit": 2515.0, "net_credit": None,
           "commission": 1.3, "composite_score": 79.0, "grade": "Good",
           "earnings_status": "upcoming", "earnings_date": "2026-09-22"}
    row.update(over)
    return row


def long_call(**over):
    row = long_put(id="NVDA_LONG_CALL", symbol="NVDA", type="LONG_CALL",
                   strategy_label="Long Call", bias="bullish",
                   legs=[{"kind": "call", "side": "long", "strike": 190.0,
                          "expiration": "2026-09-26", "qty": 1}],
                   expiration="2026-09-26", underlying_price=186.4, net_debit=410.0,
                   commission=0.65, earnings_date=None)
    row.update(over)
    return row


# ── economics: one payoff function, both units ──────────────────────────────
def test_a_credit_row_is_per_share_so_its_dollars_are_times_100():
    idea = T.normalize(pcs())
    assert idea["entry_cash"] == pytest.approx(122.4)
    assert idea["max_profit"] == pytest.approx(122.4)
    assert idea["max_loss"] == pytest.approx(377.6)       # 5 wide x 100 - 122.40
    assert idea["breakevens"] == [pytest.approx(738.78)]
    assert [(lg["side"], lg["kind"], lg["strike"]) for lg in idea["legs"]] == [
        ("short", "put", 740.0), ("long", "put", 735.0)]


def test_a_directional_row_is_per_contract_and_includes_commission():
    idea = T.normalize(long_put())
    assert idea["max_loss"] == pytest.approx(2516.3)
    assert idea["max_profit"] == pytest.approx(94000 - 2516.3)   # the stock to zero
    assert idea["breakevens"] == [pytest.approx(914.84, abs=0.01)]


def test_a_long_call_has_unlimited_profit_and_bounded_loss():
    idea = T.normalize(long_call())
    assert idea["max_profit"] is None
    assert idea["max_loss"] == pytest.approx(410.65)
    assert idea["breakevens"] == [pytest.approx(194.11, abs=0.01)]


def test_an_iron_condor_reads_its_call_side_from_call_short_and_call_long():
    idea = T.normalize(pcs(type="IC", call_short=775.0, call_long=780.0,
                           net_credit=1.42, id="SPY_IC"))
    assert sorted((lg["kind"], lg["side"], lg["strike"]) for lg in idea["legs"]) == [
        ("call", "long", 780.0), ("call", "short", 775.0),
        ("put", "long", 735.0), ("put", "short", 740.0)]
    assert idea["max_profit"] == pytest.approx(142.0)
    assert idea["max_loss"] == pytest.approx(358.0)
    assert idea["breakevens"] == [pytest.approx(738.58), pytest.approx(776.42)]


def test_a_naked_short_call_has_unlimited_loss():
    row = long_call(type="SHORT_CALL", net_debit=None, net_credit=300.0,
                    legs=[{"kind": "call", "side": "short", "strike": 190.0,
                           "expiration": "2026-09-26", "qty": 1}])
    assert T.normalize(row)["max_loss"] is None


def test_a_multi_expiry_or_share_leg_row_cannot_be_drawn():
    cal = long_call(legs=[
        {"kind": "call", "side": "short", "strike": 190.0, "expiration": "2026-09-26", "qty": 1},
        {"kind": "call", "side": "long", "strike": 190.0, "expiration": "2026-10-16", "qty": 1}])
    stock = long_call(legs=[{"kind": "stock", "side": "long", "strike": None, "qty": 1}])
    assert T.normalize(cal) is None
    assert T.normalize(stock) is None
    assert T.normalize(pcs(short_strike=None)) is None


# ── selection ───────────────────────────────────────────────────────────────
def test_candidates_keep_only_eligible_grades_and_bounded_known_trades():
    naked = long_call(id="NAKED", type="SHORT_CALL", net_debit=None, net_credit=300.0,
                      grade="Strong",
                      legs=[{"kind": "call", "side": "short", "strike": 190.0,
                             "expiration": "2026-09-26", "qty": 1}])
    scan = {"signals_swing": [pcs(), pcs(id="M", grade="Marginal"),
                              pcs(id="NOPOP", pop_pct=None)],
            "signals_directional": [long_call(), naked,
                                    long_put(id="EARN", earnings_date="2026-09-18")],
            "signals_0dte": []}
    ids = {i["id"] for i in T.candidates(scan)}
    assert ids == {pcs()["id"], long_call()["id"]}


def test_a_report_after_expiration_does_not_block_a_trade():
    assert [i["id"] for i in T.candidates({"signals_directional": [long_put()]})] == [
        long_put()["id"]]


def test_a_trade_expiring_today_is_not_posted_and_dte_is_recounted():
    scan = {"signals_swing": [pcs(expiration="2026-09-17", dte=1)],
            "signals_directional": [long_call(dte=99)]}
    ideas = T.candidates(scan, today=NOW.date())
    assert [i["id"] for i in ideas] == [long_call()["id"]]
    assert ideas[0]["dte"] == 9                     # Sep 17 -> Sep 26, not the stale 99
    assert len(T.candidates(scan, today=NOW.date(), min_dte=0)) == 2


def test_min_score_is_a_floor_on_top_of_the_grade():
    scan = {"signals_swing": [pcs(composite_score=64.0)]}
    assert T.candidates(scan, min_score=65) == []


def test_pick_prefers_grade_over_score_and_never_repeats_a_trade():
    strong = T.normalize(pcs(id="S", symbol="IWM", grade="Strong", composite_score=60))
    good = T.normalize(long_call(composite_score=90))
    assert T.pick([good, strong])["id"] == "S"
    assert T.pick([good, strong], {"ids": ["S"]})["id"] == good["id"]
    assert T.pick([strong], {"ids": ["S"]}) is None


def test_pick_prefers_a_symbol_not_yet_posted_today_even_at_a_lower_grade():
    spy_strong = T.normalize(pcs(id="S", grade="Strong"))
    nvda_good = T.normalize(long_call())
    assert T.pick([spy_strong, nvda_good], {"symbols": ["SPY"]})["id"] == nvda_good["id"]
    # ...but a posted symbol still beats posting nothing.
    assert T.pick([spy_strong], {"symbols": ["SPY"]})["id"] == "S"


def test_pick_breaks_a_grade_tie_toward_a_different_structure():
    a = T.normalize(pcs(id="A", symbol="IWM", composite_score=90))
    b = T.normalize(long_call(composite_score=70))
    assert T.pick([a, b], {"last_type": "PCS"})["id"] == b["id"]


def test_posted_state_accumulates_within_a_day_and_resets_the_next():
    idea = T.normalize(pcs())
    first = T.next_posted({}, idea, "2026-09-17")
    second = T.next_posted(first, T.normalize(long_call()), "2026-09-17")
    assert second["ids"] == [idea["id"], long_call()["id"]]
    assert second["symbols"] == ["NVDA", "SPY"]
    assert second["last_type"] == "LONG_CALL"
    assert T.todays_posted(second, "2026-09-18") == {}
    assert T.next_posted(second, idea, "2026-09-18")["ids"] == [idea["id"]]


def test_scan_age_reads_the_payload_stamp():
    scan = {"timestamp": "2026-09-17T10:31:00-05:00"}
    assert T.scan_age_min(scan, NOW) == pytest.approx(4.0)
    assert T.scan_age_min({}, NOW) is None
    assert T.scan_age_min({"timestamp": "yesterday"}, NOW) is None


def test_caption_names_everything_the_card_does():
    text = T.caption(T.normalize(long_call()))
    for part in ("NVDA", "Long Call", "Sep 26", "+190C", "Grade Good",
                 "Risk $411", "Profit Unlimited", "POP 39%"):
        assert part in text


# ── the card ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("row", [pcs(), long_put(), long_call(),
                                 pcs(type="IC", call_short=775.0, call_long=780.0)])
def test_the_card_renders_a_2x_16_by_9_png(row):
    Image = pytest.importorskip("PIL.Image")
    png = C.render_trade_idea_png(T.normalize(row), now=NOW)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(png)).size == (C.WIDTH * C.SCALE, C.HEIGHT * C.SCALE)


def test_the_card_never_raises_on_garbage():
    assert C.render_trade_idea_png({"legs": "nope"}, now=NOW) is None


def test_the_chart_window_shows_every_strike_the_spot_and_the_breakevens():
    idea = T.normalize(pcs(type="IC", call_short=775.0, call_long=780.0))
    lo, hi = C.chart_window(idea)
    for mark in [lg["strike"] for lg in idea["legs"]] + idea["breakevens"] + [idea["spot"]]:
        assert lo < mark < hi


# ── the push ────────────────────────────────────────────────────────────────
def _cfg(**block):
    base = {"enabled": True, "telegram": {"bot_token": "t", "chat_id": 1},
            "discord": {"webhook_url": "https://discord/global"},
            "trade_idea": {"enabled": True, "footer": "neuralstrike.co"}, "routes": {}}
    base["trade_idea"].update(block)
    return base


def test_send_posts_the_image_to_both_channels(monkeypatch):
    sent = []
    monkeypatch.setattr(push_notify, "send_telegram_photo",
                        lambda tok, chat, name, png, cap: sent.append(("tg", chat, name, cap)))
    monkeypatch.setattr(push_notify, "send_discord_file",
                        lambda hook, name, png, cap, content_type: sent.append(("dc", hook, content_type)))
    assert push_notify.send_trade_idea(T.normalize(pcs()), now=NOW, config=_cfg()) is True
    assert sent[0][:2] == ("tg", 1) and sent[0][2].endswith("-SPY.png")
    assert sent[1] == ("dc", "https://discord/global", "image/png")


def test_a_trade_idea_route_overrides_the_global_channels(monkeypatch):
    hooks = []
    monkeypatch.setattr(push_notify, "send_telegram_photo", lambda *a: hooks.append(a[1]))
    monkeypatch.setattr(push_notify, "send_discord_file", lambda *a, **k: hooks.append(a[0]))
    cfg = _cfg()
    cfg["routes"] = {"trade_idea": {"discord": "https://discord/ideas", "telegram_chat_id": 9}}
    push_notify.send_trade_idea(T.normalize(pcs()), now=NOW, config=cfg)
    assert hooks == [9, "https://discord/ideas"]


def test_send_is_gated_by_both_switches():
    idea = T.normalize(pcs())
    assert push_notify.send_trade_idea(idea, now=NOW, config=_cfg(enabled=False)) is False
    off = _cfg()
    off["enabled"] = False
    assert push_notify.send_trade_idea(idea, now=NOW, config=off) is False


def test_a_render_failure_falls_back_to_text(monkeypatch):
    texts = []
    monkeypatch.setattr(push_notify.trade_idea_card, "render_trade_idea_png", lambda *a, **k: None)
    monkeypatch.setattr(push_notify, "send_telegram", lambda tok, chat, text: texts.append(text))
    monkeypatch.setattr(push_notify, "send_discord", lambda hook, embed: texts.append(embed))
    assert push_notify.send_trade_idea(T.normalize(pcs()), now=NOW, config=_cfg()) is True
    assert len(texts) == 2 and "SPY" in texts[0]


# ── the handler ─────────────────────────────────────────────────────────────
class _Env:
    def __init__(self, payload):
        self.payload = payload


class _Bus:
    def __init__(self, caches):
        self.caches = dict(caches)

    def cache_get(self, key):
        return _Env(self.caches[key]) if key in self.caches else None

    def cache_set(self, key, payload, **kw):
        self.caches[key] = payload
        return 1


def _scan(**over):
    scan = {"timestamp": "2026-09-17T10:31:00-05:00", "signals_0dte": [],
            "signals_swing": [pcs()], "signals_directional": [long_call()]}
    scan.update(over)
    return scan


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push_notify, "trade_idea_config", lambda config=None: {"enabled": True})
    monkeypatch.setattr(push_notify, "send_trade_idea",
                        lambda idea, now, config=None: sent.append(idea["id"]) or True)
    return sent


def test_run_posts_one_trade_and_records_it(pushes):
    bus = _Bus({"cache:options:scan": _scan()})
    out = handlers.run_trade_idea(bus, "h1035", now=NOW)
    assert out["status"] == "posted" and len(pushes) == 1
    state = bus.caches[handlers.CACHE_TRADE_IDEA]
    assert state["posted"]["ids"] == pushes
    # The next hour, over a fresh scan, posts the OTHER trade - never the same one.
    bus.caches["cache:options:scan"] = _scan(timestamp="2026-09-17T11:31:00-05:00")
    handlers.run_trade_idea(bus, "h1135", now=NOW + dt.timedelta(hours=1))
    assert len(set(pushes)) == 2


def test_run_skips_a_stale_scan_and_says_so(pushes):
    bus = _Bus({"cache:options:scan": _scan(timestamp="2026-09-17T09:00:00-05:00")})
    out = handlers.run_trade_idea(bus, "h1035", now=NOW)
    assert out["status"] == "skipped" and out["reason"].startswith("stale scan")
    assert pushes == []


def test_run_skips_when_nothing_is_eligible(pushes):
    bus = _Bus({"cache:options:scan": _scan(signals_swing=[pcs(grade="Weak")],
                                            signals_directional=[])})
    out = handlers.run_trade_idea(bus, "h1035", now=NOW)
    assert out["reason"] == "nothing eligible" and pushes == []


def test_run_does_nothing_when_disabled(monkeypatch):
    monkeypatch.setattr(push_notify, "trade_idea_config", lambda config=None: {})
    bus = _Bus({"cache:options:scan": _scan()})
    assert handlers.run_trade_idea(bus, "h1035", now=NOW)["reason"] == "disabled"


def test_run_never_raises_on_a_dead_bus(pushes):
    class _Boom:
        def cache_get(self, k):
            raise RuntimeError("down")

        def cache_set(self, *a, **k):
            raise RuntimeError("down")

    assert handlers.run_trade_idea(_Boom(), "h1035", now=NOW)["reason"] == "no scan"


# ── the slot ────────────────────────────────────────────────────────────────
def test_the_scheduler_reads_the_configured_slots():
    assert scheduler._TRADE_IDEA_SLOTS == {
        k: (t.hour, t.minute) for k, t in mc.slot_times("trade_idea").items()}
    assert len(scheduler._TRADE_IDEA_SLOTS) == 7
    assert min(scheduler._TRADE_IDEA_SLOTS.values()) == (8, 35)
    assert max(scheduler._TRADE_IDEA_SLOTS.values()) == (14, 35)


def test_a_slot_fires_once_within_grace_and_never_on_a_holiday():
    ran = set()
    at = dt.datetime(2026, 9, 17, 10, 38, tzinfo=_CT)
    slot = scheduler.trade_idea_due(at, ran)
    assert slot == "h1035"
    ran.add((at.date().isoformat(), slot))
    assert scheduler.trade_idea_due(at, ran) is None
    assert scheduler.trade_idea_due(dt.datetime(2026, 9, 17, 10, 50, tzinfo=_CT), set()) is None
    assert scheduler.trade_idea_due(dt.datetime(2026, 12, 25, 10, 36, tzinfo=_CT), set()) is None
