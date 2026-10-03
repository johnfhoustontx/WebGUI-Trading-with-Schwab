"""The client tells the proxy who it is, may ask for an age limit, and reports
how old the answer was."""
import datetime as dt
import pathlib
import re
import sys

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import proxy_client  # noqa: E402

# What a caller label may contain, here and in the proxy that counts by it.
LABEL = re.compile(r"[A-Za-z0-9_.-]{1,40}")
EM_DASH, CYRILLIC = chr(0x2014), "".join(map(chr, (0x436, 0x443, 0x440)))
# A newline, an em dash and Cyrillic: none of them can go in an HTTP header.
HOSTILE = "night" + chr(10) + "job " + EM_DASH + " " + CYRILLIC


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


#############################################
# THE LABEL CAN NEVER BREAK A REQUEST
#############################################

def _sendable(value) -> bool:
    """Whether ``requests`` can put ``value`` in a header and send it."""
    requests.Request("GET", "http://proxy/quotes",
                     headers={"X-Caller": value}).prepare()
    value.encode("latin-1")
    return True


@pytest.mark.parametrize("bad", [HOSTILE, "job " + EM_DASH, CYRILLIC, "a" + chr(10) + "b"])
def test_the_hostile_labels_really_are_unsendable(bad):
    # The premise of the tests below: unsanitized, such a label fails EVERY
    # request the process makes, and that reads as "proxy down".
    with pytest.raises((requests.exceptions.InvalidHeader, UnicodeEncodeError)):
        _sendable(bad)


def test_a_label_is_cut_to_characters_a_header_can_carry():
    got = proxy_client.caller_label(HOSTILE)
    assert got == "night_job" + "_" * 6
    assert LABEL.fullmatch(got) and _sendable(got)


def test_a_label_from_the_environment_is_sanitized(monkeypatch):
    monkeypatch.setenv("TRADING_CALLER", "label " + EM_DASH + " " + CYRILLIC)
    got = proxy_client._caller_name()
    assert got == "label" + "_" * 6
    assert LABEL.fullmatch(got) and _sendable(got)


def test_a_label_from_the_script_name_is_sanitized(monkeypatch):
    monkeypatch.delenv("TRADING_CALLER", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["/x/tools/" + CYRILLIC + " " + EM_DASH + " nightly.py"])
    got = proxy_client._caller_name()
    assert got == "_" * 6 + "nightly"
    assert LABEL.fullmatch(got) and _sendable(got)


def test_both_clients_can_send_with_a_hostile_label(monkeypatch):
    monkeypatch.setenv("TRADING_CALLER", EM_DASH + " " + CYRILLIC)
    for client in (proxy_client.SchwabPyProxyClient("http://p"),
                   proxy_client.SchwabProxyClient("http://p")):
        assert _sendable(client.session.headers["X-Caller"])


@pytest.mark.parametrize("blank", ["", "   ", chr(9) + chr(10), None])
def test_an_empty_label_is_unknown(blank):
    assert proxy_client.caller_label(blank) == "unknown"


def test_a_whitespace_only_environment_label_falls_back_to_the_script(monkeypatch):
    monkeypatch.setenv("TRADING_CALLER", "   ")
    monkeypatch.setattr(sys, "argv", ["/x/services/trade_svc/app.py"])
    assert proxy_client._caller_name() == "trade_svc"


def test_no_script_at_all_is_unknown(monkeypatch):
    monkeypatch.delenv("TRADING_CALLER", raising=False)
    for argv in ([], [""]):
        monkeypatch.setattr(sys, "argv", argv)
        assert proxy_client._caller_name() == "unknown"


def test_a_label_is_cut_to_forty_characters():
    assert proxy_client.caller_label("a" * 200) == "a" * 40


def test_a_label_keeps_dots_dashes_and_underscores():
    assert proxy_client.caller_label(" label-journal_v1.2 ") == "label-journal_v1.2"


def test_a_service_started_from_inside_its_own_folder_is_still_named(
        monkeypatch, tmp_path):
    # ``python app.py`` from services/<name>/: argv[0] has no folder in it.
    folder = tmp_path / "services" / "trade_svc"
    folder.mkdir(parents=True)
    monkeypatch.delenv("TRADING_CALLER", raising=False)
    monkeypatch.chdir(folder)
    monkeypatch.setattr(sys, "argv", ["app.py"])
    assert proxy_client._caller_name() == "trade_svc"


def test_a_dev_checkout_is_counted_apart_from_prod(monkeypatch):
    # Dev borrows prod's proxy; without the prefix both are one row.
    monkeypatch.setattr(proxy_client, "IS_DEV", True)
    assert proxy_client.caller_label("market_svc") == "dev.market_svc"
    monkeypatch.setenv("TRADING_CALLER", "probe")
    assert proxy_client._caller_name() == "dev.probe"
    monkeypatch.delenv("TRADING_CALLER")
    monkeypatch.setattr(sys, "argv", ["/x/services/options_svc/app.py"])
    assert proxy_client._caller_name() == "dev.options_svc"
    assert proxy_client.SchwabProxyClient("http://p").session.headers[
        "X-Caller"] == "dev.options_svc"


def test_the_dev_prefix_fits_inside_the_forty_characters(monkeypatch):
    monkeypatch.setattr(proxy_client, "IS_DEV", True)
    got = proxy_client.caller_label("a" * 200)
    assert got == "dev." + "a" * 36 and LABEL.fullmatch(got)
    assert proxy_client.caller_label("  ") == "dev.unknown"


def test_prod_and_tests_carry_no_prefix():
    assert proxy_client.IS_DEV is False
    assert proxy_client.caller_label("market_svc") == "market_svc"
