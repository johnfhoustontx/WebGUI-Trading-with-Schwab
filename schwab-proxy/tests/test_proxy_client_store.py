"""The client tells the proxy who it is, may ask for an age limit, and reports
how old the answer was."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import proxy_client  # noqa: E402


class _Resp:
    status_code = 200

    def __init__(self, headers=None):
        self.headers = headers or {}

    def json(self):
        return {"ok": True}


class _Session:
    def __init__(self, headers=None):
        self.headers, self.sent, self._reply = {}, [], headers

    def get(self, url, params=None, timeout=None):
        self.sent.append((url, params))
        return _Resp(self._reply)


def _client(reply_headers=None):
    c = proxy_client.SchwabPyProxyClient("http://proxy")
    c.session = _Session(reply_headers)
    return c


def test_caller_name_is_the_service_folder_for_an_app_entrypoint(monkeypatch):
    monkeypatch.delenv("TRADING_CALLER", raising=False)
    monkeypatch.setattr(sys, "argv", ["/x/services/options_svc/app.py"])
    assert proxy_client._caller_name() == "options_svc"


def test_caller_name_is_the_script_name_for_a_tool(monkeypatch):
    monkeypatch.delenv("TRADING_CALLER", raising=False)
    monkeypatch.setattr(sys, "argv", ["/x/tools/flow_delta_instrumentation.py"])
    assert proxy_client._caller_name() == "flow_delta_instrumentation"


def test_caller_name_can_be_set_by_environment(monkeypatch):
    monkeypatch.setenv("TRADING_CALLER", "label_journal")
    assert proxy_client._caller_name() == "label_journal"


def test_both_clients_send_the_caller_header(monkeypatch):
    monkeypatch.setenv("TRADING_CALLER", "probe")
    assert proxy_client.SchwabPyProxyClient("http://p").session.headers["X-Caller"] == "probe"
    assert proxy_client.SchwabProxyClient("http://p").session.headers["X-Caller"] == "probe"


def test_no_age_limit_is_sent_unless_asked_for():
    c = _client()
    c.get_option_chain("SPY", contract_type="ALL",
                       from_date=dt.date(2026, 10, 5), to_date=dt.date(2026, 10, 12))
    assert "maxAge" not in c.session.sent[0][1]


def test_an_age_limit_is_sent_when_asked_for():
    c = _client()
    c.get_option_chain("SPY", contract_type="ALL", max_age=210)
    assert c.session.sent[0][1]["maxAge"] == 210


def test_the_answers_age_and_kind_are_readable():
    r = _client({"X-Store": "subset", "X-Store-Age": "12.5"}).get_option_chain("SPY")
    assert (r.store_kind, r.store_age) == ("subset", 12.5)


def test_an_answer_without_store_headers_reports_no_age():
    r = _client({}).get_option_chain("SPY")
    assert r.store_kind is None and r.store_age is None


def test_a_garbled_age_header_reports_no_age():
    assert _client({"X-Store-Age": "soon"}).get_option_chain("SPY").store_age is None
