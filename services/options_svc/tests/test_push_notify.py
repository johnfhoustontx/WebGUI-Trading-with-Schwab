import json

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
            "composite_score": 80}


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
