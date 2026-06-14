"""Tests for notifier.Notifier.trade_closed gating.

These patch the module-level _send_toast / _play_beep so nothing hits the
real Windows APIs, and patch Notifier._async to run synchronously so the
calls land deterministically (no sleeping / thread joins needed).
"""

import notifier


def _sync_async(monkeypatch):
    monkeypatch.setattr(notifier.Notifier, "_async", lambda self, fn, *a: fn(*a))


def test_trade_closed_sends_toast_when_enabled(monkeypatch):
    _sync_async(monkeypatch)
    calls = []
    monkeypatch.setattr(notifier, "_send_toast", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(notifier, "_play_beep", lambda *a, **k: None)

    n = notifier.Notifier(audio=False, toast=True)
    n.trade_closed("SPX", "TARGET_HIT")

    assert len(calls) == 1
    title, body, signal_type = calls[0]
    assert "SPX" in title
    assert "TARGET_HIT" in body
    assert signal_type == "trade"


def test_trade_closed_no_toast_when_disabled(monkeypatch):
    _sync_async(monkeypatch)
    calls = []
    monkeypatch.setattr(notifier, "_send_toast", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(notifier, "_play_beep", lambda *a, **k: None)

    n = notifier.Notifier(audio=False, toast=False)
    n.trade_closed("SPX", "STOPPED")

    assert calls == []


def test_trade_closed_beeps_when_audio_enabled(monkeypatch):
    _sync_async(monkeypatch)
    beeps = []
    monkeypatch.setattr(notifier, "_send_toast", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "_play_beep", lambda *a, **k: beeps.append(a))

    n = notifier.Notifier(audio=True, toast=False)
    n.trade_closed("SPX", "STOPPED")

    assert beeps == [("trade_executed",)]


def test_trade_closed_no_beep_when_audio_disabled(monkeypatch):
    _sync_async(monkeypatch)
    beeps = []
    monkeypatch.setattr(notifier, "_send_toast", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "_play_beep", lambda *a, **k: beeps.append(a))

    n = notifier.Notifier(audio=False, toast=True)
    n.trade_closed("SPX", "TARGET_HIT")

    assert beeps == []
