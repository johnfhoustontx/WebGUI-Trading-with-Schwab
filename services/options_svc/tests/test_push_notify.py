import json

import pytest

from services.options_svc import push_notify as pn


def test_load_config_reads_file(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({
        "enabled": True, "min_score": 20,
        "telegram": {"bot_token": "T", "chat_id": 5},
        "discord": {"webhook_url": "https://d"},
        "sms": {"fi_number": "5551234567", "smtp_user": "u@gmail.com",
                "smtp_app_password": "pw"},
    }))
    monkeypatch.setattr(pn, "_CONFIG_PATH", p)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = pn.load_config()
    assert cfg["min_score"] == 20
    assert cfg["telegram"]["bot_token"] == "T"
    assert cfg["sms"]["fi_number"] == "5551234567"


def test_env_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"telegram": {"bot_token": "FILE", "chat_id": 1}}))
    monkeypatch.setattr(pn, "_CONFIG_PATH", p)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ENV")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://envhook")
    cfg = pn.load_config()
    assert cfg["telegram"]["bot_token"] == "ENV"
    assert cfg["discord"]["webhook_url"] == "https://envhook"


def test_missing_file_returns_disabled_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(pn, "_CONFIG_PATH", tmp_path / "nope.json")
    for k in ("TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "FI_SMS_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    cfg = pn.load_config()
    assert cfg["telegram"]["bot_token"] == ""
    assert cfg["enabled"] is True  # default-on; channels self-gate on creds


# ── Action digest (10/1/3 CT "trades needing action") ───────────────────────
_ITEMS = {
    "captured_action": [{"symbol": "MU", "strategy": "PCS", "recommendation": "CUT",
                         "reason": "2x credit stop"}],
    "expiring_today": [{"symbol": "QQQ", "strategy": "PCS", "book": "account",
                        "expiration": "2026-06-03"}],
    "at_risk": [{"symbol": "AMD", "strategy": "CCS", "rescue_state": "critical", "heat": 82}],
    "account_near": [{"symbol": "INTC", "strategy": "PCS", "note": "44% of target"}],
}


def test_action_total_counts_all_sections():
    assert pn.action_total(_ITEMS) == 4
    assert pn.action_total({}) == 0
    assert pn.action_total({"captured_action": [], "at_risk": []}) == 0


def test_action_digest_text_includes_all_sections_and_rows():
    txt = pn.action_digest_text(_ITEMS, "Midday (1:00 PM CT)")
    assert "Midday" in txt
    for token in ("MU PCS", "CUT", "QQQ PCS", "AMD CCS", "critical", "INTC PCS", "44% of target"):
        assert token in txt


def test_action_digest_text_omits_empty_sections():
    txt = pn.action_digest_text({"captured_action": _ITEMS["captured_action"]}, "")
    assert "Captured" in txt and "MU PCS" in txt
    assert "Expiring today" not in txt and "At risk" not in txt


def test_action_digest_embed_one_field_per_nonempty_section():
    embed = pn.action_digest_embed(_ITEMS, "Morning")
    names = [f["name"] for f in embed["fields"]]
    assert len(names) == 4
    assert any("Captured" in n for n in names)


def test_send_action_digest_skips_when_disabled():
    assert pn.send_action_digest(_ITEMS, config={"enabled": False}) is False


def test_send_action_digest_skips_when_empty():
    cfg = {"enabled": True, "telegram": {"bot_token": "T", "chat_id": 1}}
    assert pn.send_action_digest({}, config=cfg) is False


def test_send_action_digest_sends_when_items_present(monkeypatch):
    sent = {"tg": 0, "dc": 0}
    monkeypatch.setattr(pn, "send_telegram", lambda *a, **k: sent.__setitem__("tg", sent["tg"] + 1))
    monkeypatch.setattr(pn, "send_discord", lambda *a, **k: sent.__setitem__("dc", sent["dc"] + 1))
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: None)
    cfg = {"enabled": True, "telegram": {"bot_token": "T", "chat_id": 1},
           "discord": {"webhook_url": "https://d"}, "sms": {}}
    assert pn.send_action_digest(_ITEMS, slot_label="Morning", config=cfg) is True
    assert sent["tg"] == 1 and sent["dc"] == 1


# ── EOD summary ──────────────────────────────────────────────────────────────
_EOD = {
    "date": "2026-07-13",
    "books": {
        "manual": {"label": "Manual", "has_account": True, "day_pnl": 120.0,
                   "equity": 25120.0, "open_count": 3, "halted": False,
                   "closed_today": 3, "wins": 2, "losses": 1, "realized_today": 180.0},
        "driver": {"label": "Driver", "has_account": True, "day_pnl": -90.0,
                   "equity": 24910.0, "open_count": 1, "halted": True,
                   "closed_today": 1, "wins": 0, "losses": 1, "realized_today": -90.0},
    },
}


def test_eod_book_line_formats_signs_and_halt():
    m = pn.eod_book_line(_EOD["books"]["manual"])
    assert "Manual" in m and "+$120 day" in m and "2-1 closed (+$180)" in m and "3 open" in m
    d = pn.eod_book_line(_EOD["books"]["driver"])
    assert "-$90 day" in d and "[HALTED]" in d


def test_eod_book_line_tolerates_none_pnl():
    line = pn.eod_book_line({"label": "Manual", "day_pnl": None})
    assert "Manual: — day" in line   # no crash on missing P&L


def test_eod_book_count_counts_seeded():
    assert pn.eod_book_count(_EOD) == 2
    assert pn.eod_book_count({"books": {"manual": {"has_account": False}}}) == 0
    assert pn.eod_book_count({}) == 0


def test_eod_summary_text_one_line_per_seeded_book():
    txt = pn.eod_summary_text(_EOD)
    assert "2026-07-13" in txt and "Manual:" in txt and "Driver:" in txt


def test_eod_summary_text_skips_unseeded_book():
    one = {"date": "d", "books": {"manual": _EOD["books"]["manual"],
                                  "driver": {"has_account": False}}}
    txt = pn.eod_summary_text(one)
    assert "Manual:" in txt and "Driver:" not in txt


def test_eod_summary_embed_color_by_total():
    green = pn.eod_summary_embed({"books": {"manual": {"has_account": True, "day_pnl": 50}}})
    red = pn.eod_summary_embed({"books": {"manual": {"has_account": True, "day_pnl": -50}}})
    assert green["color"] == pn._D_GREEN and red["color"] == pn._D_RED


def test_send_eod_summary_skips_when_disabled_or_no_book():
    assert pn.send_eod_summary(_EOD, config={"enabled": False}) is False
    empty = {"books": {"manual": {"has_account": False}}}
    assert pn.send_eod_summary(empty, config={"enabled": True}) is False


def test_send_eod_summary_sends_when_book_present(monkeypatch):
    sent = {"tg": 0, "dc": 0, "sms": 0}
    monkeypatch.setattr(pn, "send_telegram", lambda *a, **k: sent.__setitem__("tg", sent["tg"] + 1))
    monkeypatch.setattr(pn, "send_discord", lambda *a, **k: sent.__setitem__("dc", sent["dc"] + 1))
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: sent.__setitem__("sms", sent["sms"] + 1))
    cfg = {"enabled": True, "telegram": {"bot_token": "T", "chat_id": 1},
           "discord": {"webhook_url": "https://d"}, "sms": {}}
    assert pn.send_eod_summary(_EOD, config=cfg) is True
    assert sent["tg"] == 1 and sent["dc"] == 1 and sent["sms"] == 1


def test_signal_key_stable_and_distinct():
    a = {"symbol": "SPY", "type": "PCS", "short_strike": 500, "long_strike": 495,
         "expiration": "2026-07-10"}
    b = dict(a, short_strike=499)
    assert pn.signal_key(a) == pn.signal_key(a)
    assert pn.signal_key(a) != pn.signal_key(b)


def test_signal_key_ic_folds_call_legs():
    ic1 = {"symbol": "QQQ", "type": "IC", "short_strike": 400, "long_strike": 395,
           "call_short": 420, "call_long": 425, "expiration": "2026-07-10"}
    ic2 = dict(ic1, call_short=421)
    assert pn.signal_key(ic1) != pn.signal_key(ic2)


def test_captured_key_prefers_signal_id():
    assert pn.captured_key({"signal_id": "abc", "symbol": "SPY"}) == "abc"


def _sig():
    return {"symbol": "SPY", "type": "PCS", "trade_type": "0DTE",
            "short_strike": 500, "long_strike": 495, "width": 5,
            "expiration": "2026-07-10", "credit": 1.20, "max_loss": 3.80,
            "rr_pct": 31.6, "pop_pct": 72, "short_delta": -0.18,
            "net_theta": 0.05, "short_iv": 14.2, "breakeven": 498.8,
            "composite_score": 80, "grade": "Strong"}


def test_telegram_text_has_symbol_and_credit():
    t = pn.telegram_signal_text(_sig())
    assert "SPY" in t and "PCS" in t and "1.20" in t


def test_discord_embed_has_fields():
    e = pn.discord_signal_embed(_sig())
    assert e["title"].startswith("SPY PCS")
    names = {f["name"] for f in e["fields"]}
    assert "Credit" in names and "R:R" in names


def test_sms_summary_batches_and_caps():
    sigs = [dict(_sig(), symbol=f"S{i}") for i in range(8)]
    txt = pn.sms_summary_text(sigs, kind="scanner", cap=5)
    assert txt.startswith("8 new scanner")
    assert txt.count("\n") <= 6  # header + <=5 lines
    assert "S0" in txt


def test_send_telegram_posts_to_bot_api(monkeypatch):
    calls = {}
    monkeypatch.setattr(pn.requests, "post",
                        lambda url, **kw: calls.update(url=url, json=kw.get("json")))
    pn.send_telegram("TOK", 42, "hello")
    assert calls["url"].endswith("/botTOK/sendMessage")
    assert calls["json"]["chat_id"] == 42 and calls["json"]["parse_mode"] == "HTML"


def test_send_discord_posts_embed(monkeypatch):
    calls = {}
    monkeypatch.setattr(pn.requests, "post",
                        lambda url, **kw: calls.update(url=url, json=kw.get("json")))
    pn.send_discord("https://hook", {"title": "x"})
    assert calls["url"] == "https://hook"
    assert calls["json"]["embeds"] == [{"title": "x"}]


def test_send_sms_emails_fi_gateway(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent["host"] = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, msg): sent["to"] = msg["To"]; sent["body"] = msg.get_payload()

    monkeypatch.setattr(pn.smtplib, "SMTP", FakeSMTP)
    pn.send_sms("5551234567", "u@gmail.com", "pw", "2 new signals")
    assert sent["to"] == "5551234567@msg.fi.google.com"
    assert sent["login"] == ("u@gmail.com", "pw")
    assert "2 new signals" in sent["body"]


def test_senders_never_raise(monkeypatch):
    def boom(*a, **k): raise RuntimeError("net")
    monkeypatch.setattr(pn.requests, "post", boom)
    pn.send_telegram("T", 1, "x")   # no exception
    pn.send_discord("https://h", {})


def test_new_keys_diff_and_reset():
    prev = {"date": "2026-07-05", "keys": ["a", "b"]}
    # same day: only c is new
    new, nxt = pn.new_keys(["a", "b", "c"], prev, today="2026-07-05")
    assert new == ["c"] and set(nxt["keys"]) == {"a", "b", "c"}
    # new day: set resets, everything is new
    new2, nxt2 = pn.new_keys(["a"], nxt, today="2026-07-06")
    assert new2 == ["a"] and nxt2 == {"date": "2026-07-06", "keys": ["a"]}


def test_new_keys_preserves_order_and_dedups():
    new, nxt = pn.new_keys(["x", "x", "y"], None, today="2026-07-05")
    assert new == ["x", "y"]


def test_seen_roundtrip_via_bus():
    from shared.bus import Bus
    bus = Bus(fake=True)
    pn.save_seen(bus, "cache:options:notified_scan", {"date": "2026-07-05", "keys": ["k"]})
    got = pn.load_seen(bus, "cache:options:notified_scan")
    assert got["keys"] == ["k"]


def test_notify_signals_sends_per_channel_and_updates_seen(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 0,
           "telegram": {"bot_token": "T", "chat_id": 1},
           "discord": {"webhook_url": "https://h"},
           "sms": {"fi_number": "555", "smtp_user": "u", "smtp_app_password": "p"}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg, dc, sms = [], [], []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    monkeypatch.setattr(pn, "send_discord", lambda *a: dc.append(a))
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: sms.append(a))

    sigs = [_sig(), dict(_sig(), symbol="QQQ")]
    pn.notify_signals(bus, sigs, kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert len(tg) == 2 and len(dc) == 2   # one per signal
    assert len(sms) == 1                    # one batched summary
    # second call with same signals: nothing new
    pn.notify_signals(bus, sigs, kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert len(tg) == 2


def test_min_score_gate_scanner(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 90,
           "telegram": {"bot_token": "T", "chat_id": 1},
           "discord": {"webhook_url": ""}, "sms": {}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    pn.notify_signals(bus, [_sig()], kind="scanner",   # score 80 < 90
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert tg == []


def test_seed_run_does_not_send(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 0,
           "telegram": {"bot_token": "T", "chat_id": 1}, "discord": {}, "sms": {}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    pn.notify_signals(bus, [_sig()], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05", seed=True)
    assert tg == []  # seeded silently
    # now a real run with the same signal also stays silent (already seen)
    pn.notify_signals(bus, [_sig()], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert tg == []


def test_disabled_config_no_send(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    monkeypatch.setattr(pn, "load_config",
                        lambda: {"enabled": False, "telegram": {"bot_token": "T", "chat_id": 1}})
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    pn.notify_signals(bus, [_sig()], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert tg == []


def test_market_hours_gate_is_bool():
    assert isinstance(pn._in_market_hours(), bool)


# --- C1: new_keys must never raise on a malformed prev ---

def test_new_keys_malformed_prev_missing_keys():
    new, nxt = pn.new_keys(["a"], {"date": "2026-07-05"}, today="2026-07-05")
    assert new == ["a"]
    assert nxt == {"date": "2026-07-05", "keys": ["a"]}


def test_new_keys_malformed_prev_nonlist_keys():
    new, nxt = pn.new_keys(["a"], {"date": "2026-07-05", "keys": "oops"},
                           today="2026-07-05")
    # non-list prior treated as empty → "a" is new
    assert new == ["a"]
    assert nxt == {"date": "2026-07-05", "keys": ["a"]}


def test_notify_signals_survives_malformed_seen(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 0,
           "telegram": {"bot_token": "T", "chat_id": 1}, "discord": {}, "sms": {}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    monkeypatch.setattr(pn, "send_telegram", lambda *a: None)
    monkeypatch.setattr(pn, "send_discord", lambda *a: None)
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: None)
    pn.save_seen(bus, "cache:options:notified_scan",
                 {"date": "2026-07-05", "keys": None})
    # must not raise even though the stored seen-set is malformed
    pn.notify_signals(bus, [_sig()], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")


# --- I1: mark-seen happens before the gate (mirrors webgui watcher) ---

def test_seen_marked_even_when_gated_off(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    cfg = {"enabled": False, "market_hours_only": False, "min_score": 0,
           "telegram": {"bot_token": "T", "chat_id": 1}, "discord": {}, "sms": {}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    monkeypatch.setattr(pn, "send_discord", lambda *a: None)
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: None)
    sig = _sig()
    pn.notify_signals(bus, [sig], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert tg == []  # disabled → nothing sent
    seen = pn.load_seen(bus, "cache:options:notified_scan")
    assert pn.signal_key(sig) in seen["keys"]  # but the key IS marked seen


# --- M1: duplicate-key signals notify only once ---

def test_duplicate_key_signals_notify_once(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 0,
           "telegram": {"bot_token": "T", "chat_id": 1}, "discord": {}, "sms": {}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    monkeypatch.setattr(pn, "send_discord", lambda *a: None)
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: None)
    sigs = [_sig(), _sig()]  # identical → same key
    pn.notify_signals(bus, sigs, kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert len(tg) == 1  # only one send despite two duplicate signals


# --- M4: min_score applies to scanner only, not captured ---

def test_min_score_not_applied_to_captured(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 90,
           "telegram": {"bot_token": "T", "chat_id": 1}, "discord": {}, "sms": {}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    monkeypatch.setattr(pn, "send_discord", lambda *a: None)
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: None)
    # captured signal with no composite_score still notifies despite min_score=90
    cap = {"signal_id": "cap1", "symbol": "SPY", "type": "PCS",
           "short_strike": 500, "long_strike": 495, "expiration": "2026-07-10"}
    pn.notify_signals(bus, [cap], kind="captured",
                      seen_key="cache:options:notified_captured", today="2026-07-05")
    assert len(tg) == 1


# --- Task 3: Telegram/Discord push for options-flow alerts ---

def test_flow_alert_telegram_and_discord_shape():
    from services.options_svc import push_notify as pn
    a = {"type": "crossover", "side": "calls_over", "symbol": "$SPX",
         "text": "$SPX: call premium overtook puts — $260 vs $200 (bullish flip)"}
    assert "$SPX" in pn.flow_alert_telegram_text(a)
    e = pn.flow_alert_discord_embed(a)
    assert e["color"] == 0x2ECC71 and "$SPX" in e["title"] + str(e.get("description", ""))
    a2 = {"type": "uoa", "side": "put", "symbol": "MU", "text": "MU: unusual put activity"}
    assert pn.flow_alert_discord_embed(a2)["color"] == 0xE74C3C   # put/bearish → red


def test_flow_alert_uoa_color_by_side():
    from services.options_svc import push_notify as pn
    call = {"type": "uoa", "side": "call", "symbol": "SPY", "text": "x"}
    put = {"type": "uoa", "side": "put", "symbol": "SPY", "text": "y"}
    assert pn.flow_alert_discord_embed(call)["color"] == 0x2ECC71   # green
    assert pn.flow_alert_discord_embed(put)["color"] == 0xE74C3C    # red


def test_send_flow_alert_noop_when_disabled(monkeypatch):
    from services.options_svc import push_notify as pn
    calls = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a, **k: calls.append("tg"))
    monkeypatch.setattr(pn, "send_discord", lambda *a, **k: calls.append("dc"))
    a = {"type": "uoa", "side": "call", "symbol": "SPY", "text": "x"}
    assert pn.send_flow_alert(a, config={"enabled": False}) is False
    assert calls == []
    pn.send_flow_alert(a, config={"enabled": True, "telegram": {}, "discord": {}})
    assert set(calls) == {"tg", "dc"}


def test_flow_webhook_routes_by_type():
    from services.options_svc import push_notify as pn
    dc = {"webhook_url": "gen", "flow_uoa_webhook_url": "uoa",
          "flow_crossover_webhook_url": "xo"}
    assert pn.flow_webhook(dc, {"type": "uoa"}) == "uoa"
    assert pn.flow_webhook(dc, {"type": "crossover"}) == "xo"
    # Unknown type → general webhook; missing per-type key → fall back to general.
    assert pn.flow_webhook(dc, {"type": "other"}) == "gen"
    assert pn.flow_webhook({"webhook_url": "gen"}, {"type": "uoa"}) == "gen"
    assert pn.flow_webhook({"webhook_url": "gen", "flow_uoa_webhook_url": ""},
                           {"type": "uoa"}) == "gen"
    assert pn.flow_webhook({}, {"type": "uoa"}) == ""


def test_send_flow_alert_uses_per_type_webhook(monkeypatch):
    from services.options_svc import push_notify as pn
    urls = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a, **k: None)
    monkeypatch.setattr(pn, "send_discord", lambda url, embed: urls.append(url))
    cfg = {"enabled": True, "telegram": {},
           "discord": {"webhook_url": "gen", "flow_uoa_webhook_url": "uoa",
                       "flow_crossover_webhook_url": "xo"}}
    pn.send_flow_alert({"type": "uoa", "side": "call", "symbol": "SPY", "text": "x"}, config=cfg)
    pn.send_flow_alert({"type": "crossover", "side": "calls_over", "symbol": "$SPX", "text": "y"}, config=cfg)
    assert urls == ["uoa", "xo"]


#############################################
# Twitter — public tweet formatter
#############################################

def _ic_sig():
    return {"symbol": "SPX", "type": "IC", "trade_type": "0DTE",
            "short_strike": 5500, "long_strike": 5490,
            "call_short": 5600, "call_long": 5610,
            "expiration": "2026-07-10", "credit": 2.40, "max_loss": 7.60,
            "rr_pct": 31.6, "pop_pct": 68, "short_delta": -0.16,
            "composite_score": 82}


def test_twitter_text_within_tweet_char_budget():
    """A tweet is hard-capped at 280 chars — the whole point of a separate
    formatter vs. the multi-line Telegram/Discord versions."""
    for s in (_sig(), _ic_sig()):
        assert len(pn.twitter_signal_text(s)) <= 280, s["type"]


def test_twitter_text_has_symbol_type_and_credit():
    t = pn.twitter_signal_text(_sig())
    assert "SPY" in t and "PCS" in t and "1.20" in t


def test_twitter_text_carries_a_disclaimer():
    """A public post of a trade signal must carry a not-advice disclaimer."""
    t = pn.twitter_signal_text(_sig()).lower()
    assert "not advice" in t


def test_twitter_text_ic_shows_both_wings():
    t = pn.twitter_signal_text(_ic_sig())
    assert "5500" in t and "5600" in t     # put short + call short


def test_twitter_text_never_exceeds_budget_even_with_long_fields():
    """Adversarial: a long symbol + huge numbers must still be truncated to 280."""
    s = dict(_sig(), symbol="TESTINGLONG", credit=12345.6789,
             short_strike=999999, long_strike=888888)
    assert len(pn.twitter_signal_text(s)) <= 280


#############################################
# Twitter — configurable static footer (hashtags / Discord link / extra text)
#############################################

def test_twitter_text_includes_configured_hashtags():
    t = pn.twitter_signal_text(_sig(), hashtags=["#options", "#0DTE", "#SPX"])
    assert "#options" in t and "#0DTE" in t and "#SPX" in t


def test_twitter_text_includes_discord_link():
    t = pn.twitter_signal_text(_sig(), discord_url="https://discord.gg/abc123")
    assert "https://discord.gg/abc123" in t


def test_twitter_text_includes_extra_static_text():
    t = pn.twitter_signal_text(_sig(), extra_text="Join the room:")
    assert "Join the room:" in t


def test_twitter_text_with_full_footer_stays_within_budget():
    t = pn.twitter_signal_text(
        _sig(), hashtags=["#options", "#0DTE", "#SPX", "#trading", "#spy"],
        discord_url="https://discord.gg/abcdefgh", extra_text="Join the community:")
    assert len(t) <= 280


def test_twitter_footer_preserved_when_body_would_overflow():
    """The footer (link + hashtags + disclaimer) is what the user added for reach,
    so an overflowing signal body is truncated and the footer survives intact."""
    s = dict(_sig(), symbol="VERYLONGSYMBOL", short_strike=123456, long_strike=123400)
    t = pn.twitter_signal_text(
        s, hashtags=["#options", "#0DTE", "#SPX"],
        discord_url="https://discord.gg/abc123", extra_text="Join the community:")
    assert len(t) <= 280
    assert "https://discord.gg/abc123" in t          # link survived
    assert "#SPX" in t                                # hashtags survived
    assert "not advice" in t.lower()                  # disclaimer survived


def test_twitter_no_footer_config_matches_base_behavior():
    """No hashtags/link/extra configured -> just body + disclaimer (unchanged)."""
    t = pn.twitter_signal_text(_sig())
    assert "#" not in t and "discord" not in t.lower()
    assert "not advice" in t.lower()


#############################################
# Grade on the notification formatters
#############################################

def test_telegram_text_shows_grade():
    assert "Strong" in pn.telegram_signal_text(_sig())


def test_discord_embed_has_grade_field():
    e = pn.discord_signal_embed(_sig())
    fields = {f["name"]: f["value"] for f in e["fields"]}
    assert "Grade" in fields and fields["Grade"] == "Strong"


def test_twitter_text_shows_grade():
    assert "Strong" in pn.twitter_signal_text(_sig())


def test_formatters_tolerate_missing_grade():
    """A signal without a grade must not crash any formatter."""
    s = {k: v for k, v in _sig().items() if k != "grade"}
    pn.telegram_signal_text(s)
    pn.discord_signal_embed(s)
    pn.twitter_signal_text(s)


#############################################
# Twitter sender — send_twitter (tweepy wrapper)
#############################################

class _FakeTweepyClient:
    """Records create_tweet calls; optionally raises to simulate API errors."""
    last = None

    def __init__(self, *a, **k):
        _FakeTweepyClient.last = self
        self.posted = []
        self.raise_exc = None

    def create_tweet(self, *, text):
        if self.raise_exc:
            raise self.raise_exc
        self.posted.append(text)
        return {"data": {"id": "123", "text": text}}


def _tw_creds():
    return {"api_key": "K", "api_secret": "S",
            "access_token": "AT", "access_secret": "ATS"}


def test_send_twitter_posts_text(monkeypatch):
    fake = {}
    monkeypatch.setattr(pn, "_twitter_client",
                        lambda creds: _FakeTweepyClient())
    ok = pn.send_twitter(_tw_creds(), "hello world")
    assert ok is True
    assert _FakeTweepyClient.last.posted == ["hello world"]


def test_send_twitter_noop_without_creds():
    assert pn.send_twitter({}, "hi") is False
    assert pn.send_twitter({"api_key": "K"}, "hi") is False   # partial creds


def test_send_twitter_dry_run_does_not_post(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(pn, "_twitter_client",
                        lambda creds: called.__setitem__("n", called["n"] + 1))
    ok = pn.send_twitter(_tw_creds(), "hi", dry_run=True)
    assert ok is True            # attempted (logged), but
    assert called["n"] == 0      # no client built, nothing posted


def test_send_twitter_swallows_api_error(monkeypatch):
    c = _FakeTweepyClient()
    c.raise_exc = RuntimeError("duplicate content (187)")
    monkeypatch.setattr(pn, "_twitter_client", lambda creds: c)
    # best-effort: an API error must never raise into the caller
    assert pn.send_twitter(_tw_creds(), "dup") is False


#############################################
# Twitter fan-out — notify_twitter (gating + daily cap)
#############################################

def _tw_cfg(**over):
    cfg = {"enabled": True, "dry_run": True, "min_score": 70, "daily_cap": 3,
           "api_key": "K", "api_secret": "S", "access_token": "AT", "access_secret": "ATS",
           "hashtags": ["#options"], "discord_url": "https://discord.gg/x"}
    cfg.update(over)
    return cfg


def test_notify_twitter_disabled_posts_nothing(monkeypatch):
    from shared.bus import Bus
    posts = []
    monkeypatch.setattr(pn, "send_twitter", lambda *a, **k: posts.append(a) or True)
    out = pn.notify_twitter(Bus(fake=True), [_sig()], today="2026-07-05",
                            count_key="ck", cfg={"twitter": {"enabled": False}})
    assert out == [] and posts == []


def test_notify_twitter_applies_its_own_min_score(monkeypatch):
    from shared.bus import Bus
    posts = []
    monkeypatch.setattr(pn, "send_twitter", lambda creds, text, **k: posts.append(text) or True)
    weak = dict(_sig(), symbol="WK", composite_score=50)   # below twitter min_score 70
    strong = dict(_sig(), symbol="STG", composite_score=85)
    pn.notify_twitter(Bus(fake=True), [weak, strong], today="2026-07-05",
                      count_key="ck", cfg={"twitter": _tw_cfg()})
    assert len(posts) == 1 and "STG" in posts[0] and "WK" not in posts[0]


def test_notify_twitter_respects_daily_cap_across_calls(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    posts = []
    monkeypatch.setattr(pn, "send_twitter", lambda creds, text, **k: posts.append(text) or True)
    sigs = [dict(_sig(), symbol=f"S{i}", short_strike=500 + i, composite_score=90)
            for i in range(5)]
    cfg = {"twitter": _tw_cfg(daily_cap=3)}
    pn.notify_twitter(bus, sigs[:2], today="2026-07-05", count_key="ck", cfg=cfg)
    pn.notify_twitter(bus, sigs[2:], today="2026-07-05", count_key="ck", cfg=cfg)
    assert len(posts) == 3          # cap enforced across the two calls, not reset


def test_notify_twitter_cap_resets_next_day(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    posts = []
    monkeypatch.setattr(pn, "send_twitter", lambda creds, text, **k: posts.append(text) or True)
    sigs = [dict(_sig(), symbol=f"S{i}", short_strike=500 + i, composite_score=90) for i in range(4)]
    cfg = {"twitter": _tw_cfg(daily_cap=2)}
    pn.notify_twitter(bus, sigs[:2], today="2026-07-05", count_key="ck", cfg=cfg)
    pn.notify_twitter(bus, sigs[2:], today="2026-07-06", count_key="ck", cfg=cfg)   # new day
    assert len(posts) == 4          # 2 + 2, cap reset on the date change


def test_notify_twitter_passes_footer_config(monkeypatch):
    from shared.bus import Bus
    posts = []
    monkeypatch.setattr(pn, "send_twitter", lambda creds, text, **k: posts.append(text) or True)
    pn.notify_twitter(Bus(fake=True), [dict(_sig(), composite_score=90)],
                      today="2026-07-05", count_key="ck",
                      cfg={"twitter": _tw_cfg(hashtags=["#zzz"], discord_url="https://discord.gg/yyy")})
    assert "#zzz" in posts[0] and "https://discord.gg/yyy" in posts[0]


def test_notify_signals_also_posts_to_twitter_when_enabled(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    posts = []
    monkeypatch.setattr(pn, "send_twitter", lambda creds, text, **k: posts.append(text) or True)
    monkeypatch.setattr(pn, "send_telegram", lambda *a: None)
    monkeypatch.setattr(pn, "send_discord", lambda *a: None)
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: None)
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 0,
           "telegram": {}, "discord": {}, "sms": {},
           "twitter": {"enabled": True, "dry_run": True, "min_score": 0, "daily_cap": 10,
                       "api_key": "K", "api_secret": "S", "access_token": "AT", "access_secret": "ATS"}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    pn.notify_signals(bus, [dict(_sig(), composite_score=90)], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert len(posts) == 1 and "SPY" in posts[0]


def test_notify_signals_captured_does_not_post_to_twitter(monkeypatch):
    from shared.bus import Bus
    bus = Bus(fake=True)
    posts = []
    monkeypatch.setattr(pn, "send_twitter", lambda *a, **k: posts.append(a) or True)
    monkeypatch.setattr(pn, "send_telegram", lambda *a: None)
    monkeypatch.setattr(pn, "send_discord", lambda *a: None)
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: None)
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 0,
           "telegram": {}, "discord": {}, "sms": {},
           "twitter": {"enabled": True, "dry_run": True, "daily_cap": 10,
                       "api_key": "K", "api_secret": "S", "access_token": "AT", "access_secret": "ATS"}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    cap = {"symbol": "MU", "type": "PCS", "signal_id": "sig-1", "short_strike": 100,
           "long_strike": 95, "expiration": "2026-07-10", "credit": 1.0}
    pn.notify_signals(bus, [cap], kind="captured",
                      seen_key="cache:options:notified_captured", today="2026-07-05")
    assert posts == []      # captured signals are never tweeted


def test_flow_alert_gamma_flip_color_by_side():
    from services.options_svc import push_notify as pn
    pos = {"type": "gamma_flip", "side": "to_positive", "symbol": "$SPX", "text": "x"}
    neg = {"type": "gamma_flip", "side": "to_negative", "symbol": "$SPX", "text": "y"}
    assert pn.flow_alert_discord_embed(pos)["color"] == 0x2ECC71   # positive gamma → green
    assert pn.flow_alert_discord_embed(neg)["color"] == 0xE74C3C   # negative gamma → red


def test_flow_webhook_routes_gamma_flip():
    from services.options_svc import push_notify as pn
    dc = {"webhook_url": "gen", "flow_gamma_flip_webhook_url": "gf"}
    assert pn.flow_webhook(dc, {"type": "gamma_flip"}) == "gf"
    # missing per-type key → general webhook.
    assert pn.flow_webhook({"webhook_url": "gen"}, {"type": "gamma_flip"}) == "gen"


def test_briefing_caption_full():
    res = {"analysis": {"regime": "Positive gamma · pinned",
                        "bias": 18.0,
                        "headline": "SPX pinned between 6350 and 6400 into the close."}}
    cap = pn.briefing_caption(res, "midday")
    assert cap.startswith("Gamma · Midday · Positive gamma · pinned · Bias +18")
    assert "SPX pinned between 6350 and 6400" in cap


def test_briefing_caption_missing_fields():
    assert pn.briefing_caption({"analysis": {}}, "open") == "Gamma · After open"
    assert pn.briefing_caption({}, "close") == "Gamma · At close"
    assert pn.briefing_caption({"analysis": {}}, "") == "Gamma · Briefing"


def test_briefing_caption_negative_bias_signed():
    res = {"analysis": {"bias": -42}}
    assert "Bias -42" in pn.briefing_caption(res, "premarket")


def test_briefing_caption_non_numeric_bias_dropped():
    res = {"analysis": {"bias": "very bearish"}}
    assert "Bias" not in pn.briefing_caption(res, "premarket")


def test_briefing_caption_truncates_headline_not_lead():
    res = {"analysis": {"regime": "Neg gamma", "bias": 5, "headline": "z" * 4000}}
    cap = pn.briefing_caption(res, "close")
    assert len(cap) <= 1024
    assert cap.startswith("Gamma · At close · Neg gamma · Bias +5")
    assert cap.endswith("…")


def test_briefing_filename_uses_generated_at_date():
    res = {"generated_at": "2026-07-23T11:30:04-05:00"}
    assert pn.briefing_filename(res, "midday") == "gamma-briefing-2026-07-23-midday.png"


def test_briefing_filename_falls_back_to_now():
    import datetime as dt
    now = dt.datetime(2026, 7, 23, 9, 0)
    assert pn.briefing_filename({}, "open", now=now) == "gamma-briefing-2026-07-23-open.png"


def test_briefing_filename_sanitizes_slot():
    res = {"generated_at": "2026-07-23T09:00:00"}
    assert pn.briefing_filename(res, "adhoc 18:42") == "gamma-briefing-2026-07-23-adhoc-18-42.png"


@pytest.fixture
def briefing_cfg():
    return {"enabled": True,
            "telegram": {"bot_token": "TOK", "chat_id": 7},
            "discord": {"webhook_url": "https://main"},
            "gamma_briefing": {"enabled": True,
                               "slots": ["premarket", "open", "midday", "close"],
                               "webhook_url": "https://briefings"}}


@pytest.fixture
def briefing_res():
    return {"html": "<html>doc</html>",
            "analysis": {"regime": "Pinned", "bias": 10, "headline": "hi"},
            "generated_at": "2026-07-23T11:30:00"}


_FAKE_PNG = b"\x89PNG\r\n\x1a\nrendered-briefing"


def _capture(monkeypatch, png=_FAKE_PNG):
    """Stub every send path + the renderer. `png=None` simulates a render failure."""
    sent = {"photo": [], "file": [], "tg_text": [], "dc_embed": [], "rendered": []}

    def _render(html, **kw):
        sent["rendered"].append(html)
        return png

    monkeypatch.setattr(pn.briefing_image, "render_html_png", _render)
    monkeypatch.setattr(pn, "send_telegram_photo", lambda *a, **k: sent["photo"].append(a))
    monkeypatch.setattr(pn, "send_discord_file", lambda *a, **k: sent["file"].append((a, k)))
    monkeypatch.setattr(pn, "send_telegram", lambda *a, **k: sent["tg_text"].append(a))
    monkeypatch.setattr(pn, "send_discord", lambda *a, **k: sent["dc_embed"].append(a))
    return sent


def test_send_gamma_briefing_happy_path(monkeypatch, briefing_cfg, briefing_res):
    """The payload is a RENDERED PNG, not the HTML: Discord auto-previews an .html
    attachment as syntax-highlighted raw source (a wall of CSS above the card)."""
    sent = _capture(monkeypatch)
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is True
    assert sent["rendered"] == ["<html>doc</html>"]      # the doc is the render source
    tok, chat, name, content, caption = sent["photo"][0]
    assert (tok, chat) == ("TOK", 7)
    assert name == "gamma-briefing-2026-07-23-midday.png"
    assert content == _FAKE_PNG
    assert caption.startswith("Gamma · Midday")
    (hook, dname, dcontent, dcaption), kw = sent["file"][0]
    assert hook == "https://briefings"          # dedicated webhook wins
    assert (dname, dcontent) == (name, content)
    assert kw["content_type"] == "image/png"    # or Discord previews it as source
    # the image IS the briefing — no duplicate text push alongside it
    assert sent["tg_text"] == [] and sent["dc_embed"] == []


def test_send_gamma_briefing_never_sends_the_html_document(monkeypatch, briefing_cfg,
                                                           briefing_res):
    """The HTML payload was explicitly scrapped, so the document sender is no
    longer even imported here — it cannot be reached by accident."""
    assert not hasattr(pn, "send_telegram_document")
    sent = _capture(monkeypatch)
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is True
    assert sent["photo"][0][3] == _FAKE_PNG                 # not the html bytes
    assert sent["file"][0][0][2] == _FAKE_PNG


def test_send_gamma_briefing_falls_back_to_main_webhook(monkeypatch, briefing_cfg,
                                                        briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg["gamma_briefing"]["webhook_url"] = ""
    pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg)
    assert sent["file"][0][0][0] == "https://main"


def test_send_gamma_briefing_render_failure_sends_text(monkeypatch, briefing_cfg,
                                                       briefing_res):
    """No browser / a wedged render must not go silent — the read still arrives as
    text. It must NOT fall back to the rejected HTML attachment."""
    sent = _capture(monkeypatch, png=None)
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is True
    assert sent["photo"] == [] and sent["file"] == []
    tok, chat, text = sent["tg_text"][0]
    assert (tok, chat) == ("TOK", 7)
    assert text.startswith("Gamma · Midday")
    hook, embed = sent["dc_embed"][0]
    assert hook == "https://briefings"
    assert "Gamma · Midday" in (embed.get("description") or "") + (embed.get("title") or "")


def test_send_gamma_briefing_text_fallback_escapes_html(monkeypatch, briefing_cfg,
                                                        briefing_res):
    """send_telegram posts with parse_mode=HTML, and the caption carries a
    MODEL-WRITTEN headline — a bare '<' or '&' would 400 the whole fallback."""
    sent = _capture(monkeypatch, png=None)
    briefing_res["analysis"]["headline"] = "SPX < 6400 & pinned"
    pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg)
    text = sent["tg_text"][0][2]
    assert "&lt; 6400 &amp; pinned" in text
    assert "< 6400" not in text
    # Discord renders plain text, so its embed keeps the readable original.
    assert "< 6400 & pinned" in sent["dc_embed"][0][1]["description"]


def test_send_gamma_briefing_render_failure_is_logged(monkeypatch, briefing_cfg,
                                                      briefing_res, caplog):
    _capture(monkeypatch, png=None)
    with caplog.at_level("WARNING"):
        pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg)
    assert "render" in caplog.text.lower() and "midday" in caplog.text


def test_send_gamma_briefing_master_gate(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg["enabled"] = False
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["photo"] == [] and sent["file"] == [] and sent["rendered"] == []


def test_send_gamma_briefing_feature_gate(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg["gamma_briefing"]["enabled"] = False
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["photo"] == [] and sent["rendered"] == []


def test_send_gamma_briefing_slot_not_selected(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg["gamma_briefing"]["slots"] = ["premarket", "close"]
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["photo"] == [] and sent["rendered"] == []


def test_send_gamma_briefing_skips_degraded_run(monkeypatch, briefing_cfg):
    """A no-chains / no-API-key run still produces readable HTML but carries no
    `analysis` — pushing 'no chains available' 4x/day is exactly the spam to avoid."""
    sent = _capture(monkeypatch)
    degraded = {"html": "<html>No chains available</html>", "analysis": None}
    assert pn.send_gamma_briefing(degraded, slot="midday", config=briefing_cfg) is False
    assert sent["photo"] == [] and sent["file"] == [] and sent["tg_text"] == []


def test_send_gamma_briefing_skips_empty_html(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_res["html"] = ""
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["rendered"] == []               # nothing to render


def test_send_gamma_briefing_skips_oversize(monkeypatch, briefing_cfg, briefing_res):
    """The size guard now measures the PNG — that is what gets uploaded."""
    sent = _capture(monkeypatch, png=b"z" * (pn._BRIEFING_MAX_BYTES + 1))
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["photo"] == [] and sent["file"] == []


def test_send_gamma_briefing_large_html_is_fine(monkeypatch, briefing_cfg, briefing_res):
    """A big DOC is irrelevant now — only the rendered image is uploaded."""
    sent = _capture(monkeypatch)
    briefing_res["html"] = "z" * (pn._BRIEFING_MAX_BYTES + 1)
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is True
    assert sent["photo"][0][3] == _FAKE_PNG


def test_send_gamma_briefing_missing_block_defaults_on(monkeypatch, briefing_cfg,
                                                       briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg.pop("gamma_briefing")
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is True
    assert sent["file"][0][0][0] == "https://main"


def test_send_gamma_briefing_empty_slots_mutes_every_slot(monkeypatch, briefing_cfg,
                                                          briefing_res):
    """An operator who sets "slots": [] means "mute all four". A truthiness test
    would skip the gate entirely and push every slot — the exact inverse."""
    sent = _capture(monkeypatch)
    briefing_cfg["gamma_briefing"]["slots"] = []
    for slot in ("premarket", "open", "midday", "close"):
        assert pn.send_gamma_briefing(briefing_res, slot=slot, config=briefing_cfg) is False
    assert sent["photo"] == [] and sent["file"] == []


def test_send_gamma_briefing_absent_slots_key_pushes_all(monkeypatch, briefing_cfg,
                                                         briefing_res):
    """Distinct from []: no `slots` key at all means "no filter", so every slot pushes."""
    sent = _capture(monkeypatch)
    briefing_cfg["gamma_briefing"].pop("slots")
    for slot in ("premarket", "open", "midday", "close"):
        assert pn.send_gamma_briefing(briefing_res, slot=slot, config=briefing_cfg) is True
    assert len(sent["photo"]) == 4


def test_briefing_caption_bool_bias_dropped():
    """`True` is an int in Python, so without the explicit bool exclusion a
    malformed `"bias": true` renders a fabricated "Bias +1"."""
    assert "Bias" not in pn.briefing_caption({"analysis": {"bias": True}}, "open")
    assert "Bias" not in pn.briefing_caption({"analysis": {"bias": False}}, "open")
