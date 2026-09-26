"""FRED: fredgraph.csv, the official API's JSON, the release-calendar HTML,
and redaction of the API key.

Fixtures: ``fredgraph_cpi.csv``, ``fredgraph_missing.csv`` (DGS10 over two
holidays - FRED now serves a missing value as an EMPTY cell) and
``fred_calendar_rid9.html`` (two live calendar pages, trimmed) are real
bytes fetched 2026-09-26 with the repo feed User-Agent. ``fred_obs_cpi.json``
and ``fred_release_dates.json`` are HAND-WRITTEN from FRED's documented
response shape (no API key exists in the build container); the CPI values are
the real ones from the CSV.
"""
import json
import math
import pathlib

import pytest

from services.news_svc.adapters import fred

FIX = pathlib.Path(__file__).parent / "fixtures"
CAL = (FIX / "fred_calendar_rid9.html").read_bytes()
# The calendar fixture is an unfiltered page: many rids share it.
BODY_WITH_TWO_RIDS = CAL
KEY = "SECRETKEY123"


# ---- fredgraph.csv -------------------------------------------------------

def test_csv_rows_ascending_floats():
    obs = fred.parse_csv(FIX.joinpath("fredgraph_cpi.csv").read_bytes(), "CPIAUCSL")
    assert obs[-1] == {"obs_date": "2026-08-01", "value": 334.131}
    assert [o["obs_date"] for o in obs] == sorted(o["obs_date"] for o in obs)
    assert len(obs) == 15


def test_a_blank_cell_is_missing_not_zero():
    obs = fred.parse_csv(FIX.joinpath("fredgraph_missing.csv").read_bytes(), "DGS10")
    by_date = {o["obs_date"]: o["value"] for o in obs}
    assert by_date["2025-12-25"] is None and by_date["2026-01-01"] is None
    assert by_date["2025-12-26"] == 4.14


def test_a_dot_is_missing_not_zero():
    obs = fred.parse_csv(b"observation_date,X\n2026-07-01,1.5\n2026-08-01,.\n", "X")
    assert obs == [{"obs_date": "2026-07-01", "value": 1.5},
                   {"obs_date": "2026-08-01", "value": None}]


def test_non_finite_values_are_missing():
    obs = fred.parse_csv(b"observation_date,X\n2026-06-01,nan\n2026-07-01,inf\n2026-08-01,-Infinity\n", "X")
    assert [o["value"] for o in obs] == [None, None, None]


def test_csv_with_the_wrong_series_column_is_refused():
    with pytest.raises(ValueError):
        fred.parse_csv(b"observation_date,UNRATE\n2026-08-01,4.1\n", "CPIAUCSL")


def test_csv_that_is_not_a_csv_is_refused():
    for body in (b"", b"<html>blocked</html>", b"\xff\xfe\x00", None, "text"):
        with pytest.raises(ValueError):
            fred.parse_csv(body, "CPIAUCSL")


def test_csv_bom_and_crlf_and_bad_rows():
    body = b"\xef\xbb\xbfobservation_date,X\r\n2026-08-01,2\r\nnot-a-date,3\r\n2026-07-01\r\n2026-06-01,1\r\n"
    assert fred.parse_csv(body, "X") == [{"obs_date": "2026-06-01", "value": 1.0},
                                        {"obs_date": "2026-08-01", "value": 2.0}]


# ---- API JSON --------------------------------------------------------------

def test_api_json_is_ascending_like_the_csv():
    body = FIX.joinpath("fred_obs_cpi.json").read_bytes()
    obs = fred.parse_api_observations(body)
    assert [o["obs_date"] for o in obs] == sorted(o["obs_date"] for o in obs)
    assert obs == fred.parse_csv(FIX.joinpath("fredgraph_cpi.csv").read_bytes(), "CPIAUCSL")


def test_api_dot_is_missing():
    body = json.dumps({"observations": [{"date": "2026-08-01", "value": "."},
                                        {"date": "2026-07-01", "value": "2.5"},
                                        {"date": "junk", "value": "1"},
                                        "not a row"]}).encode()
    assert fred.parse_api_observations(body) == [{"obs_date": "2026-07-01", "value": 2.5},
                                                 {"obs_date": "2026-08-01", "value": None}]


def test_api_error_envelope_and_junk_are_refused():
    err = b'{"error_code":400,"error_message":"Bad Request.  Variable api_key is not set."}'
    for body in (err, b"", b"[]", b'{"observations": 5}', b"<html/>", None):
        with pytest.raises(ValueError):
            fred.parse_api_observations(body)


def test_api_release_dates():
    rows = fred.parse_api_release_dates(FIX.joinpath("fred_release_dates.json").read_bytes())
    assert rows == [{"rid": 9, "date": "2026-10-15"}, {"rid": 9, "date": "2026-11-17"},
                    {"rid": 9, "date": "2026-12-16"}]
    with pytest.raises(ValueError):
        fred.parse_api_release_dates(b'{"error_code":400,"error_message":"x"}')


# ---- calendar HTML -------------------------------------------------------

def test_calendar_html_times_are_central_and_blank_repeats():
    rows = fred.parse_calendar_html(FIX.joinpath("fred_calendar_rid9.html").read_bytes(), rid=9)
    assert rows[0]["at"] == "2026-10-15T12:30:00+00:00"    # 7:30 am CDT
    assert rows[1]["at"] is not None                          # the blank time cell
    assert all("&amp;" not in r["name"] for r in rows)


def test_calendar_blank_cells_inherit_the_row_above_across_dst():
    rows = fred.parse_calendar_html(CAL, rid=9)
    assert [(r["date"], r["at"]) for r in rows] == [
        ("2026-10-15", "2026-10-15T12:30:00+00:00"),        # CDT
        ("2026-11-17", "2026-11-17T13:30:00+00:00"),        # CST
    ]
    assert rows[0]["name"] == "Advance Monthly Sales for Retail and Food Services"


def test_calendar_names_are_unescaped():
    rows = fred.parse_calendar_html(CAL, rid=189)
    assert rows and rows[0]["name"] == "Standard & Poors"


def test_calendar_na_time_is_date_only_and_repeats():
    # rid 209 reads "N/A"; rid 189's blank cell sits under it.
    for rid in (209, 189):
        row = fred.parse_calendar_html(CAL, rid=rid)[0]
        assert row["date"] == "2026-10-15" and row["at"] is None


def test_other_rids_on_the_page_are_ignored():
    rows = fred.parse_calendar_html(BODY_WITH_TWO_RIDS, rid=9)
    assert rows and all(r["rid"] == 9 for r in rows)
    everything = fred.parse_calendar_html(CAL, rid=None)
    assert len({r["rid"] for r in everything}) > 2


def test_calendar_ids_are_stable_and_unique():
    rows = fred.parse_calendar_html(CAL, rid=None)
    assert len({r["id"] for r in rows}) == len(rows)


def test_calendar_junk_never_raises():
    for body in (b"", None, "str", b"<html></html>", b"\xff\xfe",
                 b'<span style="font-weight: bold;">Notaday 99, 2026</span>'
                 b'<td nowrap>7:30 am</td><td><a href="/release?rid=9">X</a>'):
        assert fred.parse_calendar_html(body, rid=9) == []


# ---- URLs + key redaction --------------------------------------------------

def test_api_url_and_redaction():
    url = fred.api_observations_url("https://api.stlouisfed.org/fred", "CPIAUCSL", "SECRETKEY123")
    assert "api_key=SECRETKEY123" in url and "sort_order=desc" in url and "file_type=json" in url
    assert fred.redact(f"HTTP 400 {url}", "SECRETKEY123") == fred.redact(f"HTTP 400 {url}", "SECRETKEY123")
    assert "SECRETKEY123" not in fred.redact(f"HTTP 400 {url}", "SECRETKEY123")
    assert fred.redact("no key here", None) == "no key here"


def test_redaction_is_total():
    odd = "k+y/=&ey 1"
    url = fred.api_observations_url("https://api.stlouisfed.org/fred", "CPIAUCSL", odd)
    red = fred.redact(f"GET {url} failed; key was {odd}", odd)
    for form in (odd, "k%2By%2F%3D%26ey+1", "k%2By%2F%3D%26ey%201", "k%2By/%3D%26ey%201"):
        assert form not in red
    assert "***" in red


def test_redact_takes_an_exception_and_masks_any_api_key_param():
    exc = RuntimeError(f"HTTPSConnectionPool: /fred/series/observations?api_key={KEY}&x=1")
    red = fred.redact(exc, KEY)
    assert KEY not in red and "api_key=***" in red
    # a key the caller did not pass (a stale one) is still masked by its parameter
    assert "OTHERKEY" not in fred.redact("...?series_id=X&api_key=OTHERKEY&file_type=json", None)
    assert "OTHERKEY" not in fred.redact("...?API_KEY=OTHERKEY", "")


def test_redact_never_raises():
    for text in (None, 5, b"bytes " + KEY.encode(), object()):
        out = fred.redact(text, KEY)
        assert isinstance(out, str) and KEY not in out


def test_release_dates_url_carries_no_key_unless_given():
    url = fred.api_release_dates_url("https://api.stlouisfed.org/fred", 9, KEY)
    assert "release_id=9" in url and "api_key=" + KEY in url and "file_type=json" in url
    assert "include_release_dates_with_no_data=true" in url


def test_fredgraph_and_calendar_urls():
    u = fred.fredgraph_url("CPIAUCSL", "2025-06-01")
    assert u == "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL&cosd=2025-06-01"
    c = fred.calendar_url(9, "2026-09-20", "2026-12-31")
    assert c == ("https://fred.stlouisfed.org/releases/calendar?rid=9&y=2026&view=year"
                 "&vs=2026-09-20&ve=2026-12-31")
    assert "api_key" not in u + c


def test_values_are_plain_floats_or_none():
    obs = fred.parse_csv(FIX.joinpath("fredgraph_cpi.csv").read_bytes(), "CPIAUCSL")
    # the real series has no October 2025 print (the government shutdown)
    assert [o["obs_date"] for o in obs if o["value"] is None] == ["2025-10-01"]
    assert all(type(o["value"]) is float and math.isfinite(o["value"])
               for o in obs if o["value"] is not None)


# ---- hardening (review findings) -------------------------------------------

def test_safe_error_drops_the_chained_cause_that_holds_the_key():
    # fetch.http_fetch raises FetchError(...) from exc, and the CAUSE carries
    # the unredacted URL; a traceback logged with exc_info prints the chain.
    import traceback
    try:
        try:
            raise ConnectionError("GET https://api/x?series_id=A&api_key=SECRET failed")
        except ConnectionError as inner:
            raise RuntimeError("fetch failed api_key=SECRET") from inner
    except RuntimeError as exc:
        safe = fred.safe_error(exc, "SECRET")
    assert safe.__cause__ is None and safe.__context__ is None
    assert safe.__suppress_context__ is True
    text = "".join(traceback.format_exception(safe))
    assert "SECRET" not in text and "api_key=***" in text


def test_safe_error_re_raised_from_none_logs_no_key():
    # The key is built at runtime so the traceback's echoed SOURCE lines
    # cannot contain it - only a leaked exception message could.
    import traceback
    key = "".join(["SEC", "RET"])
    try:
        try:
            try:
                raise OSError(f"https://api/x?api_key={key}")
            except OSError as inner:
                raise RuntimeError(f"wrapped {key}") from inner
        except RuntimeError as exc:
            raise fred.safe_error(exc, key) from None
    except Exception as final:  # noqa: BLE001
        text = "".join(traceback.format_exception(final))
    assert key not in text and "***" in text


def test_calendar_regex_is_linear_on_an_unclosed_td():
    import time
    t0 = time.perf_counter()
    assert fred.parse_calendar_html(b"<td nowrap" * 40000, rid=None) == []
    assert time.perf_counter() - t0 < 1.0


def test_csv_error_is_a_value_error():
    # An unterminated quote swallows the rest of the body into one field past
    # csv's 131072-char limit, which raises csv.Error.
    body = b'observation_date,X\n2026-08-01,"' + b"1" * 200000 + b"\n"
    with pytest.raises(ValueError):
        fred.parse_csv(body, "X")


def test_deeply_nested_json_is_a_value_error():
    with pytest.raises(ValueError):
        fred.parse_api_observations(b"[" * 100000)
    with pytest.raises(ValueError):
        fred.parse_api_release_dates(b"[" * 100000)


def test_csv_accepts_the_legacy_date_header():
    obs = fred.parse_csv(b"DATE,X\n2026-07-01,1.5\n2026-08-01,2\n", "X")
    assert obs == [{"obs_date": "2026-07-01", "value": 1.5},
                   {"obs_date": "2026-08-01", "value": 2.0}]
    with pytest.raises(ValueError):
        fred.parse_csv(b"DATE,UNRATE\n2026-08-01,4.1\n", "X")
