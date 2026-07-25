"""Tests for the market-state transition push alert (sentiment domain).

Pure formatters + the ``send_state_transition`` gate. The shared channel
senders (``shared.notify.channels``) are REUSED — we monkeypatch them where
``state_alert`` references them, never touching the network.
"""
from services.sentiment_svc import state_alert


def _trend(**over):
    t = {"state": "bearish", "label": "Bearish",
         "description": "Urgent selling/liquidation — favor debit puts.",
         "evidence": ["aggression -0.60", "put-skew Δ -1.5"]}
    t.update(over)
    return t


# --- formatters ---------------------------------------------------------------

def test_transition_telegram_has_labels_description_and_evidence():
    txt = state_alert.transition_telegram(
        "Bullish", "Lack of Bullishness", "Buyers exhausted at highs.",
        ["e-one", "e-two"])
    assert "Bullish" in txt and "Lack of Bullishness" in txt
    assert "→" in txt
    assert "Buyers exhausted at highs." in txt
    assert "e-one" in txt and "e-two" in txt
    assert "<b>" in txt  # HTML formatting for Telegram


def test_transition_telegram_escapes_html():
    txt = state_alert.transition_telegram(
        "A<x>", "B&C", "desc <script>", ["ev & <b>"])
    assert "<x>" not in txt and "&lt;x&gt;" in txt
    assert "&amp;C" in txt
    assert "&lt;script&gt;" in txt
    assert "ev &amp; &lt;b&gt;" in txt


def test_transition_discord_is_embed_with_title_and_evidence():
    emb = state_alert.transition_discord(
        "Bullish", "Bearish", "bearish",
        "Urgent selling.", ["ev1", "ev2"])
    assert "Bullish" in emb["title"] and "Bearish" in emb["title"]
    assert emb["description"] == "Urgent selling."
    assert isinstance(emb["color"], int)
    # evidence surfaced in a field
    blob = "".join(f.get("value", "") for f in emb["fields"])
    assert "ev1" in blob and "ev2" in blob


def test_transition_discord_color_by_state():
    green = state_alert.transition_discord("X", "Y", "bullish", "", [])["color"]
    red = state_alert.transition_discord("X", "Y", "bearish", "", [])["color"]
    amber = state_alert.transition_discord("X", "Y", "neutral", "", [])["color"]
    assert green != red != amber != green


def test_transition_sms_is_short():
    assert state_alert.transition_sms("Bullish", "Bearish") == \
        "Market state: Bullish → Bearish"


# --- send_state_transition gate ----------------------------------------------

def _cfg(**over):
    c = {"enabled": True, "market_hours_only": False,
         "telegram": {"bot_token": "T", "chat_id": 5},
         "discord": {"webhook_url": "https://d"},
         "sms": {"fi_number": "5551234567", "smtp_user": "u@gmail.com",
                 "smtp_app_password": "pw"}}
    c.update(over)
    return c


def _spy_senders(monkeypatch):
    calls = {"tg": 0, "dc": 0, "sms": 0}
    monkeypatch.setattr(state_alert, "send_telegram",
                        lambda *a, **k: calls.__setitem__("tg", calls["tg"] + 1))
    monkeypatch.setattr(state_alert, "send_discord",
                        lambda *a, **k: calls.__setitem__("dc", calls["dc"] + 1))
    monkeypatch.setattr(state_alert, "send_sms",
                        lambda *a, **k: calls.__setitem__("sms", calls["sms"] + 1))
    return calls


def test_send_returns_false_when_disabled(monkeypatch):
    calls = _spy_senders(monkeypatch)
    assert state_alert.send_state_transition(
        "bullish", "bearish", _trend(), config=_cfg(enabled=False)) is False
    assert calls == {"tg": 0, "dc": 0, "sms": 0}


def test_send_returns_false_on_same_state(monkeypatch):
    calls = _spy_senders(monkeypatch)
    assert state_alert.send_state_transition(
        "bullish", "bullish", _trend(), config=_cfg()) is False
    assert calls == {"tg": 0, "dc": 0, "sms": 0}


def test_send_returns_false_on_old_vocab_state(monkeypatch):
    """A cold-start old-vocab (or None) prior committed state is not a real
    new-vocab transition — must not fire."""
    calls = _spy_senders(monkeypatch)
    # old-vocab string
    assert state_alert.send_state_transition(
        "bull_trend", "bearish", _trend(), config=_cfg()) is False
    # None prior (very first cycle)
    assert state_alert.send_state_transition(
        None, "bearish", _trend(), config=_cfg()) is False
    # new state itself not valid
    assert state_alert.send_state_transition(
        "bullish", "garbage", _trend(), config=_cfg()) is False
    assert calls == {"tg": 0, "dc": 0, "sms": 0}


def test_send_returns_false_outside_market_hours(monkeypatch):
    calls = _spy_senders(monkeypatch)
    monkeypatch.setattr(state_alert, "_in_market_hours", lambda: False)
    assert state_alert.send_state_transition(
        "bullish", "bearish", _trend(),
        config=_cfg(market_hours_only=True)) is False
    assert calls == {"tg": 0, "dc": 0, "sms": 0}


def test_send_calls_three_shared_senders_when_valid(monkeypatch):
    calls = _spy_senders(monkeypatch)
    monkeypatch.setattr(state_alert, "_in_market_hours", lambda: True)
    ok = state_alert.send_state_transition(
        "bullish", "bearish", _trend(), config=_cfg(market_hours_only=True))
    assert ok is True
    assert calls == {"tg": 1, "dc": 1, "sms": 1}


def test_send_missing_creds_shared_senders_noop(monkeypatch):
    """With empty creds the REAL shared senders no-op — no network, returns True."""
    monkeypatch.setattr(state_alert, "_in_market_hours", lambda: True)
    cfg = _cfg(telegram={"bot_token": "", "chat_id": 0},
               discord={"webhook_url": ""},
               sms={"fi_number": "", "smtp_user": "", "smtp_app_password": ""})
    # do NOT patch senders — they must self-gate on absent creds
    assert state_alert.send_state_transition(
        "bullish", "bearish", _trend(), config=cfg) is True


def _capture_targets(monkeypatch):
    """Capture the TARGETS the senders were handed (not just call counts)."""
    got = {}
    monkeypatch.setattr(state_alert, "send_telegram",
                        lambda tok, chat, text: got.update(tok=tok, chat=chat))
    monkeypatch.setattr(state_alert, "send_discord",
                        lambda wh, embed: got.update(webhook=wh))
    monkeypatch.setattr(state_alert, "send_sms", lambda *a, **k: None)
    return got


def test_state_transition_uses_market_state_route(monkeypatch):
    """A routes.market_state override beats the global discord webhook / chat."""
    got = _capture_targets(monkeypatch)
    monkeypatch.setattr(state_alert, "_in_market_hours", lambda: True)
    cfg = _cfg(telegram={"bot_token": "T", "chat_id": 1},
               discord={"webhook_url": "GLOBAL"},
               routes={"market_state": {"discord": "MS_D",
                                        "telegram_chat_id": 77}})
    assert state_alert.send_state_transition(
        "bullish", "bearish", _trend(), config=cfg) is True
    assert got["webhook"] == "MS_D"
    assert got["chat"] == 77
    assert got["tok"] == "T"      # bot token is always the global one


def test_state_transition_without_routes_uses_global(monkeypatch):
    """Back-compat: no `routes` block → the legacy global targets, unchanged."""
    got = _capture_targets(monkeypatch)
    monkeypatch.setattr(state_alert, "_in_market_hours", lambda: True)
    assert state_alert.send_state_transition(
        "bullish", "bearish", _trend(), config=_cfg()) is True
    assert got["webhook"] == "https://d"
    assert got["chat"] == 5
    assert got["tok"] == "T"


def test_send_never_raises_even_if_a_sender_raises(monkeypatch):
    monkeypatch.setattr(state_alert, "_in_market_hours", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("channel exploded")

    monkeypatch.setattr(state_alert, "send_telegram", _boom)
    monkeypatch.setattr(state_alert, "send_discord", _boom)
    monkeypatch.setattr(state_alert, "send_sms", _boom)
    # must not raise
    assert state_alert.send_state_transition(
        "bullish", "bearish", _trend(), config=_cfg()) is True
