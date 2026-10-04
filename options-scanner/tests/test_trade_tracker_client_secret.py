"""The tracker client sends the proxy secret and says who it is (audit SE-100).

The proxy's /track and /untrack now need ``X-Proxy-Secret``. A client that did
not send it would be refused on every paper trade, silently, since these calls
are fire-and-forget (the proxy's own 30-second reconcile would cover for it)."""
import trade_tracker_client as ttc


class _Resp:
    status_code = 200


def _capture(monkeypatch):
    sent = []

    def post(url, **kw):
        sent.append((url, kw))
        return _Resp()

    monkeypatch.setattr(ttc.requests, "post", post)
    return sent


TRADE = {"trade_id": "t1", "symbol": "SPY", "strategy": "PCS", "expiration": "2099-01-15",
         "entry_credit": 1.0, "short_strike": 500.0, "long_strike": 495.0}


def test_track_and_untrack_send_the_secret_when_one_is_configured(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(ttc, "_secret", lambda: "abc")
    assert ttc.track(TRADE) is True and ttc.untrack("t1") is True
    for _url, kw in sent:
        assert kw["headers"]["X-Proxy-Secret"] == "abc"
        assert kw["headers"]["X-Caller"]


def test_no_secret_configured_sends_no_secret_header(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(ttc, "_secret", lambda: None)
    ttc.track(TRADE)
    assert "X-Proxy-Secret" not in sent[0][1]["headers"]


def test_a_refusal_is_logged_without_raising(monkeypatch, caplog):
    class Refused:
        status_code = 503

    monkeypatch.setattr(ttc.requests, "post", lambda url, **kw: Refused())
    assert ttc.track(TRADE) is False and ttc.untrack("t1") is False
