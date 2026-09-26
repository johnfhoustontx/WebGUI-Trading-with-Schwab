"""Pure economic-calendar builders (``services/news_svc/econ.py``).

The payload these build is read by ``webgui/pages/news_view.py``
(``calendar_groups`` / ``indicator_state``); the last test here runs the
built payload through that reader, so a shape drift fails on this side.
"""
import datetime as dt
import json

import pytest

from services.news_svc import econ

UTC = dt.timezone.utc
NOW_SEP26 = dt.datetime(2026, 9, 26, 15, 0, tzinfo=UTC)
NOW = NOW_SEP26
JAN_2 = dt.datetime(2027, 1, 2, 15, 0, tzinfo=UTC)

C = {"release_watch_min": 60, "release_poll_min": 2, "actual_fresh_h": 24,
     "fed": {"horizon_days": 45, "speech_horizon_days": 14},
     "ipo": {"min_offer_usd": 100_000_000, "lookback_days": 7}}


def at(hhmm, day="2026-10-14"):
    h, m = (int(x) for x in hhmm.split(":"))
    y, mo, d = (int(x) for x in day.split("-"))
    return dt.datetime(y, mo, d, h, m, tzinfo=UTC)


def _ev(summary, when, date=None):
    return {"summary": summary, "at": when, "date": date or (when or "")[:10],
            "description": ""}


JOLTS = _ev("Job Openings and Labor Turnover Survey", "2026-10-06T14:00:00+00:00")
CPI_EVENT = _ev("Consumer Price Index", "2026-10-14T12:30:00+00:00")
IND = {"key": "cpi", "schedule": "bls", "match": "Consumer Price Index"}
DEC_ONLY_EVENTS = [_ev("Consumer Price Index", "2026-12-10T13:30:00+00:00")]


# ---- derive ------------------------------------------------------------------

def test_pct_mom_and_its_prior():
    obs = [{"obs_date": d, "value": v} for d, v in
           [("2026-06-01", 330.0), ("2026-07-01", 331.65), ("2026-08-01", 332.98)]]
    latest, prior = econ.derive(obs, "pct_mom")
    assert latest == {"obs_date": "2026-08-01", "value": pytest.approx(0.401, abs=1e-3)}
    assert prior["value"] == pytest.approx(0.5, abs=1e-3)


def test_change_k_is_payrolls_in_thousands():
    obs = [{"obs_date": "2026-07-01", "value": 158913}, {"obs_date": "2026-08-01", "value": 159075}]
    assert econ.derive(obs, "change_k")[0]["value"] == 162


@pytest.mark.parametrize("bad", [None, float("nan"), 0.0])
def test_a_missing_or_zero_base_yields_none_never_zero(bad):
    obs = [{"obs_date": "2026-07-01", "value": bad}, {"obs_date": "2026-08-01", "value": 1.0}]
    assert econ.derive(obs, "pct_mom")[0]["value"] is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), ".", True, "3.1"])
def test_a_junk_latest_value_is_none(bad):
    obs = [{"obs_date": "2026-07-01", "value": 4.0}, {"obs_date": "2026-08-01", "value": bad}]
    latest, prior = econ.derive(obs, "level_pct")
    assert latest == {"obs_date": "2026-08-01", "value": None}
    assert prior == {"obs_date": "2026-07-01", "value": 4.0}


def test_an_overflowing_derived_figure_is_none_never_inf():
    rows = [{"obs_date": "2026-07-01", "value": 1e-300},
            {"obs_date": "2026-08-01", "value": 1e300}]
    latest, _ = econ.derive(rows, "pct_mom")
    assert latest["value"] is None
    rows = [{"obs_date": "2026-07-01", "value": -1.7e308},
            {"obs_date": "2026-08-01", "value": 1.7e308}]
    assert econ.derive(rows, "change_k")[0]["value"] is None


def test_level_k_divides_by_a_thousand_and_levels_pass_through():
    obs = [{"obs_date": "2026-09-12", "value": 201000}, {"obs_date": "2026-09-19", "value": 197000}]
    assert econ.derive(obs, "level_k") == ({"obs_date": "2026-09-19", "value": 197.0},
                                           {"obs_date": "2026-09-12", "value": 201.0})
    assert econ.derive(obs[:1], "pct_saar")[0]["value"] == 201000.0


def test_derive_sorts_and_tolerates_nothing():
    assert econ.derive([], "pct_mom") == (None, None)
    assert econ.derive(None, "pct_mom") == (None, None)
    one = econ.derive([{"obs_date": "2026-08-01", "value": 5.0}], "pct_mom")
    assert one == ({"obs_date": "2026-08-01", "value": None}, None)   # no base, no prior
    shuffled = [{"obs_date": "2026-08-01", "value": 110.0}, "junk",
                {"obs_date": "2026-07-01", "value": 100.0}, {"obs_date": None, "value": 1}]
    assert econ.derive(shuffled, "pct_mom")[0] == {"obs_date": "2026-08-01",
                                                  "value": pytest.approx(10.0)}


def test_an_unknown_transform_is_none_not_a_guess():
    obs = [{"obs_date": "2026-08-01", "value": 5.0}]
    assert econ.derive(obs, "yoy")[0]["value"] is None


# ---- releases_for -------------------------------------------------------------

def test_schedule_match_picks_each_indicators_next_and_last_release():
    evs = [{"summary": "Consumer Price Index", "at": "2026-09-11T12:30:00+00:00", "date": "2026-09-11"},
           {"summary": "Consumer Price Index", "at": "2026-10-14T12:30:00+00:00", "date": "2026-10-14"}]
    s = econ.releases_for({"schedule": "bls", "match": "Consumer Price Index"},
                          {"bls": evs}, now=NOW_SEP26)
    assert (s["last_release_at"], s["next_release_at"]) == (evs[0]["at"], evs[1]["at"])
    assert s["next_date"] == "2026-10-14"


def test_gdp_prefix_does_not_take_gdp_by_industry():
    evs = [_ev("GDP by State, 2nd Quarter 2026", "2026-09-27T12:30:00+00:00"),
           _ev("GDP by Industry, 2nd Quarter 2026", "2026-09-28T12:30:00+00:00"),
           _ev("GDP (Advance Estimate), 3rd Quarter 2026", "2026-10-29T12:30:00+00:00")]
    s = econ.releases_for({"schedule": "bea", "match": "GDP ("}, {"bea": evs}, now=NOW)
    assert s["next_release_at"] == "2026-10-29T12:30:00+00:00"
    assert s["last_release_at"] is None


def test_january_gap_is_none_not_a_guess():
    s = econ.releases_for(IND, {"bls": DEC_ONLY_EVENTS}, now=JAN_2)
    assert s["next_release_at"] is None and s["next_date"] is None
    assert s["last_release_at"] == DEC_ONLY_EVENTS[0]["at"]


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def test_employment_situation_never_takes_the_veterans_release():
    from services.news_svc.adapters import ics
    evs = ics.parse((FIXTURES / "bls_full.ics").read_bytes())
    assert any(e["summary"].startswith("Employment Situation of Veterans") for e in evs)
    nfp = {"key": "nfp", "schedule": "bls", "match": "Employment Situation"}
    picked = econ._schedule_events(nfp, {"bls": evs})
    veterans = {e["date"] for e in evs if e["summary"].startswith("Employment Situation of")}
    assert picked and not ({d for _, d in picked} & veterans)
    real = [e for e in evs if e["summary"].strip() == "Employment Situation"]
    assert len(picked) == len(real)
    ev = econ.events_group(fed=[], ics={"bls": evs}, extra_releases=["Employment Situation"],
                           now=dt.datetime(2025, 1, 1, tzinfo=UTC),
                           cfg={**C, "fed": {"horizon_days": 3660, "speech_horizon_days": 14}})
    assert ev and all(r["title"] == "Employment Situation" for r in ev)


def test_every_2026_gdp_estimate_matches_and_gdp_by_county_does_not():
    from services.news_svc.adapters import ics
    evs = ics.parse((FIXTURES / "bea_full.ics").read_bytes())
    gdp = {"key": "gdp", "schedule": "bea", "match": "GDP ("}
    picked = {d for _, d in econ._schedule_events(gdp, {"bea": evs})}
    estimates = {e["date"] for e in evs
                 if e["summary"].startswith("GDP (") and e["date"].startswith("2026")}
    assert estimates and estimates <= picked
    county = {e["date"] for e in evs if e["summary"].startswith("GDP by")}
    assert not (county & picked)


@pytest.mark.parametrize("text, prefix, ok", [
    ("Employment Situation", "Employment Situation", True),
    ("Employment Situation, August 2026", "Employment Situation", True),
    ("Employment Situation (revised)", "Employment Situation", True),
    ("Employment Situation: note", "Employment Situation", True),
    ("Employment Situation - late", "Employment Situation", True),
    ("Employment Situation of Veterans", "Employment Situation", False),
    ("Employment Situations", "Employment Situation", False),
    ("GDP (Advance Estimate), 3rd Quarter", "GDP (", True),
    ("GDP by Industry", "GDP (", False),
    ("personal income and outlays, May 2026", "Personal Income and Outlays", True),
])
def test_prefix_match_needs_a_boundary(text, prefix, ok):
    assert econ._prefix_match(text, prefix) is ok


def test_a_missing_schedule_or_junk_events_is_all_none():
    for scheds in ({}, {"bls": None}, {"bls": "x"}, {"bls": [None, 3, {"summary": 5}]}):
        assert econ.releases_for(IND, scheds, now=NOW) == {
            "last_release_at": None, "next_release_at": None, "next_date": None}


def test_fred_schedule_uses_time_ct_only_when_the_source_gives_a_date():
    ind = {"schedule": "fred", "release_id": 9, "time_ct": "07:30"}
    dated = [{"rid": 9, "date": "2026-10-15"}, {"rid": 180, "date": "2026-10-01"}]
    s = econ.releases_for(ind, {"fred": dated}, now=NOW)
    assert s["next_release_at"] == "2026-10-15T12:30:00+00:00"      # 07:30 CT, CDT
    timed = [{"rid": 9, "date": "2026-10-15", "at": "2026-10-15T13:00:00+00:00"}]
    s = econ.releases_for({**ind, "time_ct": "09:45"}, {"fred": timed}, now=NOW)
    assert s["next_release_at"] == "2026-10-15T13:00:00+00:00"      # the source's time wins
    s = econ.releases_for({**ind, "time_ct": None}, {"fred": dated}, now=NOW)
    assert (s["next_release_at"], s["next_date"]) == (None, "2026-10-15")


def test_a_bls_date_only_event_never_takes_time_ct():
    ind = {**IND, "time_ct": "07:30"}
    s = econ.releases_for(ind, {"bls": [_ev("Consumer Price Index", None, "2026-10-14")]}, now=NOW)
    assert (s["next_release_at"], s["next_date"]) == (None, "2026-10-14")


# ---- watch_due ---------------------------------------------------------------

def test_watch_due_window_and_cadence():
    st = {"last_release_at": "2026-10-14T12:30:00+00:00", "latest_first_seen": "2026-10-13T00:00:00+00:00",
          "last_watch_poll": None}
    assert econ.watch_due(st, now=at("12:29"), cfg=C) is False             # before release
    assert econ.watch_due(st, now=at("12:31"), cfg=C) is True
    assert econ.watch_due({**st, "last_watch_poll": at("12:31")}, now=at("12:32"), cfg=C) is False
    assert econ.watch_due({**st, "last_watch_poll": at("12:31")}, now=at("12:33"), cfg=C) is True
    assert econ.watch_due(st, now=at("13:31"), cfg=C) is False             # window (60 min) over
    # landed: a real (explicitly non-bootstrap) arrival ends the watch. The flag
    # is now spelled out - an ABSENT flag reads as bootstrap (strict rule, see
    # test_watch_due_reads_bootstrap_strictly), which keeps watching.
    assert econ.watch_due({**st, "latest_first_seen": at("12:40"), "latest_bootstrap": False},
                          now=at("12:45"), cfg=C) is False


def test_watch_due_keeps_watching_past_a_bootstrap_fill():
    st = {"last_release_at": "2026-10-14T12:30:00+00:00",
          "latest_first_seen": "2026-10-14T12:35:00+00:00", "latest_bootstrap": True,
          "last_watch_poll": None}
    assert econ.watch_due(st, now=at("12:40"), cfg=C) is True


@pytest.mark.parametrize("bad", [10**400, 1e300, 10081, -(10**400)])
def test_watch_due_never_raises_on_a_huge_cadence(bad):
    st = {"last_release_at": "2026-10-14T12:30:00+00:00", "latest_first_seen": None,
          "last_watch_poll": "2026-10-14T12:31:00+00:00"}
    for key in ("release_watch_min", "release_poll_min"):
        cfg = {**C, key: bad}
        # a junk value is the default: 60-minute window, 2-minute poll
        assert econ.watch_due(st, now=at("12:34"), cfg=cfg) is True, key
        assert econ.watch_due(st, now=at("13:31"), cfg=cfg) is False, key


def test_finite_and_setting_treat_an_overflowing_int_as_absent():
    assert econ._finite(10**400) is None
    assert econ._setting({"x": 10**400}, "x", 7) == 7


def test_the_published_settings_are_capped():
    payload = econ.build_calendar(
        parts={"now": NOW, "cfg": {**C, "release_watch_min": 10**400, "actual_fresh_h": 8761}},
        public_symbols=None)
    assert payload["settings"] == {"release_watch_min": econ._CAL["release_watch_min"],
                                   "actual_fresh_h": econ._CAL["actual_fresh_h"]}


@pytest.mark.parametrize("flag, landed", [
    (False, True), (0, True),
    (True, False), (1, False), (None, False), ("0", False), (0.0, False), ("false", False)])
def test_watch_due_reads_bootstrap_strictly(flag, landed):
    st = {"last_release_at": "2026-10-14T12:30:00+00:00",
          "latest_first_seen": "2026-10-14T12:35:00+00:00", "latest_bootstrap": flag,
          "last_watch_poll": None}
    assert econ.watch_due(st, now=at("12:40"), cfg=C) is (not landed)


def test_obs_fact_bootstrap_is_false_only_for_false_or_int_zero():
    derived = {"obs_date": "2026-08-01", "value": 1.0}
    for flag, want in ((False, False), (0, False), (True, True), (1, True), (None, True),
                       ("0", True), (0.0, True), (2, True)):
        fact = econ._obs_fact({"2026-08-01": {"bootstrap": flag}}, derived)
        assert fact["bootstrap"] is want, flag
    assert econ._obs_fact({}, derived)["bootstrap"] is True          # no flag at all


def test_watch_due_with_nothing_to_watch_is_false():
    assert econ.watch_due({}, now=at("12:31"), cfg=C) is False
    assert econ.watch_due({"last_release_at": "junk"}, now=at("12:31"), cfg=C) is False
    assert econ.watch_due(None, now=at("12:31"), cfg=C) is False


# ---- events ------------------------------------------------------------------

def test_extra_releases_join_the_events_group_and_tracked_ones_do_not():
    ev = econ.events_group(fed=[], ics={"bls": [JOLTS, CPI_EVENT]},
                           extra_releases=["Job Openings and Labor Turnover Survey"], now=NOW, cfg=C)
    assert [e["title"] for e in ev] == ["Job Openings and Labor Turnover Survey"]
    assert ev[0] == {"title": "Job Openings and Labor Turnover Survey",
                     "at": JOLTS["at"], "date": "2026-10-06", "high": False}


def _fed(kind, title, day, when="18:00:00"):
    return {"kind": kind, "title": title, "date": day,
            "at": f"{day}T{when}+00:00" if when else None, "id": f"fed:{day}:{title}"}


def test_speech_horizon_is_shorter_than_fomc_horizon():
    fed = [_fed("speech", "Speech - Governor Cook", "2026-10-06"),        # 10 days out
           _fed("speech", "Speech - Chair", "2026-10-20"),                # 24 days: past 14
           _fed("testimony", "Testimony - Chair", "2026-10-21"),
           _fed("fomc_statement", "FOMC statement", "2026-10-28"),        # 32 days: inside 45
           _fed("fomc_minutes", "FOMC minutes", "2026-11-18"),            # 53 days: past 45
           _fed("beige", "Beige Book", "2026-09-20")]                     # the past
    ev = econ.events_group(fed=fed, ics={}, extra_releases=[], now=NOW, cfg=C)
    assert [e["title"] for e in ev] == ["Speech - Governor Cook", "FOMC statement"]


def test_events_are_ordered_by_instant_and_a_date_only_event_keeps_its_date():
    fed = [_fed("fomc_statement", "FOMC statement", "2026-10-28"),
           _fed("beige", "Beige Book", "2026-10-14", when=None),
           _fed("speech", "Speech - A", "2026-10-06", when="14:00:00")]
    ev = econ.events_group(fed=list(reversed(fed)), ics={}, extra_releases=[], now=NOW, cfg=C)
    assert [e["title"] for e in ev] == ["Speech - A", "Beige Book", "FOMC statement"]
    assert ev[1] == {"title": "Beige Book", "at": None, "date": "2026-10-14", "high": False}


def test_events_group_survives_junk():
    ev = econ.events_group(fed=[None, 5, {"title": ""}, {"title": "X", "date": "bad"}],
                           ics={"bls": "junk", "bea": [None]}, extra_releases="not a list",
                           now=NOW, cfg={})
    assert ev == []


HIGH = ["FOMC statement", "Press conference", "- Chair"]


def test_an_event_is_high_when_its_title_contains_a_phrase_in_any_case():
    fed = [_fed("fomc_statement", "FOMC statement", "2026-10-28"),
           _fed("fomc_press", "Press conference", "2026-10-28", when="18:30:00"),
           _fed("fomc_minutes", "FOMC minutes", "2026-10-07"),
           _fed("beige", "Beige Book", "2026-10-14"),
           _fed("speech", "Speech - Chair Jerome H. Powell", "2026-10-01"),
           _fed("testimony", "Testimony - Chairman Kevin Warsh", "2026-10-02"),
           _fed("speech", "Speech - Vice Chair Philip N. Jefferson", "2026-10-03"),
           _fed("speech", "Speech - Vice Chair for Supervision Michelle W. Bowman",
                "2026-10-04"),
           _fed("speech", "Discussion - Governor Lisa D. Cook", "2026-10-05")]
    ev = econ.events_group(fed=fed, ics={}, extra_releases=[], now=NOW, cfg=C,
                           high_events=["fomc STATEMENT", "press conference", "- Chair"])
    high = {e["title"] for e in ev if e["high"] is True}
    assert high == {"FOMC statement", "Press conference", "Speech - Chair Jerome H. Powell",
                    "Testimony - Chairman Kevin Warsh"}
    assert all(e["high"] is False for e in ev if e["title"] not in high)


def test_extra_releases_are_matched_too_and_no_phrases_highlight_nothing():
    ev = econ.events_group(fed=[], ics={"bls": [JOLTS]},
                           extra_releases=["Job Openings and Labor Turnover Survey"],
                           now=NOW, cfg=C, high_events=["job openings"])
    assert ev[0]["high"] is True
    for junk in (None, [], "Job", [3, "", None]):
        ev = econ.events_group(fed=[], ics={"bls": [JOLTS]},
                               extra_releases=["Job Openings and Labor Turnover Survey"],
                               now=NOW, cfg=C, high_events=junk)
        assert ev[0]["high"] is False, junk


def test_a_data_entry_is_high_only_when_its_indicator_says_true():
    p = econ.build_calendar(parts=PARTS, public_symbols=None)
    assert [d["high"] for d in p["data"]] == [True, False, False]   # cpi · claims · gdp
    for bad in ("yes", 1, None):
        ind = [{**INDICATORS[0], "high": bad}]
        q = econ.build_calendar(parts={**PARTS, "indicators": ind}, public_symbols=None)
        assert q["data"][0]["high"] is False, bad


def test_build_calendar_highlights_by_the_high_events_part_in_both_views():
    p = econ.build_calendar(parts=PARTS, public_symbols=None)
    q = econ.build_calendar(parts=PARTS, public_symbols={"JPM"})
    assert {e["title"]: e["high"] for e in p["events"]} == {
        "Job Openings and Labor Turnover Survey": False, "FOMC statement": True}
    assert p["events"] == q["events"]
    assert all(r["high"] is False for r in p["dividends"] + p["ipos"])
    bare = econ.build_calendar(parts={k: v for k, v in PARTS.items() if k != "high_events"},
                               public_symbols=None)
    assert all(e["high"] is False for e in bare["events"])        # a missing part is empty


# ---- IPOs and dividends ------------------------------------------------------

def _ipo(sym, status, day, price, offer, deal):
    return {"symbol": sym, "company": f"{sym} Inc.", "status": status, "date": day,
            "price": price, "offer_usd": offer, "exchange": "NASDAQ", "id": f"nasdaq_ipo:{deal}"}


def test_ipo_filter_min_offer_and_lookback():
    rows = [_ipo("TINY", "upcoming", "2026-09-30", "4.00-5.00", 20_000_000, "1"),
            _ipo("OLD", "priced", "2026-09-18", "12.00", 300_000_000, "2"),
            _ipo("NEW", "priced", "2026-09-22", "17.00", 240_000_000, "3"),
            _ipo("OURA", "upcoming", "2026-09-30", "40.00-44.00", 2_530_000_000, "4"),
            _ipo("NOSIZE", "upcoming", "2026-10-02", "10.00", None, "5")]
    out = econ.ipos_group(rows, now=NOW, cfg=C)
    assert [i["symbol"] for i in out] == ["NEW", "OURA"]
    assert out[0] == {"symbol": "NEW", "company": "NEW Inc.", "date": "2026-09-22",
                      "price": 17.0, "price_range": "", "offer_usd": 240_000_000,
                      "high": False}
    assert out[1] == {"symbol": "OURA", "company": "OURA Inc.", "date": "2026-09-30",
                      "price": None, "price_range": "40.00-44.00", "offer_usd": 2_530_000_000,
                      "high": False}


def test_ipos_from_two_months_are_one_row_per_deal_the_priced_one():
    rows = [_ipo("OURA", "upcoming", "2026-09-30", "40.00-44.00", 2_530_000_000, "4"),
            _ipo("OURA", "priced", "2026-10-01", "45.00", 2_600_000_000, "4")]
    out = econ.ipos_group(rows, now=NOW, cfg=C)
    assert len(out) == 1 and out[0]["price"] == 45.0 and out[0]["date"] == "2026-10-01"


@pytest.mark.parametrize("price", ["nan", "inf", "0.00", "-3", None, 7])
def test_an_ipo_price_that_is_not_a_positive_number_is_none(price):
    out = econ.ipos_group([_ipo("A", "priced", "2026-09-25", price, 500_000_000, "9")],
                          now=NOW, cfg=C)
    assert out[0]["price"] is None and out[0]["price_range"] == ""


def test_dividends_are_clean_and_unknown_amounts_are_none():
    rows = [{"symbol": "jpm", "ex_date": "2026-10-06", "pay_date": "2026-10-31", "amount": 1.4},
            {"symbol": "KO", "ex_date": "2026-10-01", "pay_date": None, "amount": 0.0},
            {"symbol": "XOM", "ex_date": "2026-10-02", "pay_date": "bad", "amount": float("nan")},
            {"symbol": "T", "ex_date": "2026-10-02", "pay_date": None, "amount": "0.28"},
            {"symbol": "", "ex_date": "2026-10-02"}, {"symbol": "IBM", "ex_date": None}, None]
    out = econ.dividends_group(rows, public_symbols=None)
    assert out == [
        {"symbol": "KO", "ex_date": "2026-10-01", "pay_date": None, "amount": None,
         "high": False},
        {"symbol": "T", "ex_date": "2026-10-02", "pay_date": None, "amount": None,
         "high": False},
        {"symbol": "XOM", "ex_date": "2026-10-02", "pay_date": None, "amount": None,
         "high": False},
        {"symbol": "JPM", "ex_date": "2026-10-06", "pay_date": "2026-10-31", "amount": 1.4,
         "high": False}]


# ---- source state ------------------------------------------------------------

def test_source_state_words():
    ok = {"last_ok": "2026-09-26T14:00:00+00:00", "last_poll": "2026-09-26T14:00:00+00:00",
          "error": None, "payload": []}
    assert econ.source_state(ok, enabled=True) == "ok"
    assert econ.source_state(ok, enabled=False) == "off"
    assert econ.source_state({**ok, "error": "HTTP 403"}, enabled=True) == "stale"
    assert econ.source_state({"last_ok": None, "error": "HTTP 403"}, enabled=True) == "never"
    assert econ.source_state({**ok, "payload": None}, enabled=True) == "never"
    assert econ.source_state(None, enabled=True) == "never"


# ---- build_calendar ------------------------------------------------------------

INDICATORS = [
    {"key": "cpi", "label": "CPI", "series": "CPIAUCSL", "transform": "pct_mom",
     "schedule": "bls", "match": "Consumer Price Index", "tile": "CPI", "high": True},
    {"key": "claims", "label": "Jobless claims", "series": "ICSA", "transform": "level_k",
     "schedule": "fred", "release_id": 180, "time_ct": "07:30", "tile": "Jobless claims"},
    {"key": "gdp", "label": "GDP", "series": "A191RL1Q225SBEA", "transform": "pct_saar",
     "schedule": "bea", "match": "GDP (", "tile": "GDP"},
]
SEEN = "2026-09-11T12:34:00+00:00"
PARTS = {
    "now": NOW,
    "cfg": C,
    "indicators": INDICATORS,
    "fed": [_fed("fomc_statement", "FOMC statement", "2026-10-28")],
    "ics": {"bls": [JOLTS, CPI_EVENT,
                    _ev("Consumer Price Index", "2026-09-11T12:30:00+00:00")],
            "bea": []},
    "fred_calendar": [{"rid": 180, "date": "2026-10-01", "at": "2026-10-01T12:30:00+00:00"},
                      {"rid": 180, "date": "2026-09-24", "at": "2026-09-24T12:30:00+00:00"}],
    "extra_releases": ["Job Openings and Labor Turnover Survey"],
    "high_events": ["FOMC statement", "Press conference", "- Chair"],
    "obs": {
        "CPIAUCSL": [{"obs_date": "2026-06-01", "value": 330.0, "first_seen": "2026-08-01T00:00:00+00:00", "bootstrap": True},
                     {"obs_date": "2026-07-01", "value": 331.65, "first_seen": "2026-08-01T00:00:00+00:00", "bootstrap": True},
                     {"obs_date": "2026-08-01", "value": 332.98, "first_seen": SEEN, "bootstrap": False}],
        "ICSA": [{"obs_date": "2026-09-12", "value": 201000.0, "first_seen": "2026-09-18T00:00:00", "bootstrap": 1},
                 {"obs_date": "2026-09-19", "value": 197000.0, "first_seen": "2026-09-24T12:31:00+00:00", "bootstrap": 0}],
    },
    "ipos": [_ipo("OURA", "upcoming", "2026-09-30", "40.00-44.00", 2_530_000_000, "4")],
    "dividends": [{"symbol": "JPM", "ex_date": "2026-10-06", "pay_date": "2026-10-31", "amount": 1.4},
                  {"symbol": "EXTRA", "ex_date": "2026-10-07", "pay_date": None, "amount": 0.5}],
    "sources": {"fed": "ok", "bls": "stale", "bea": "never", "dividends": "ok",
                "nasdaq_ipo": "ok", "fred_calendar": "ok", "fred_api": "ok",
                "fredgraph": "ok", "bogus": "ok"},
    "fred_obs_source": "fredgraph",
}


def test_build_calendar_public_cuts_dividends_to_public_symbols():
    p = econ.build_calendar(parts=PARTS, public_symbols=None)
    q = econ.build_calendar(parts=PARTS, public_symbols={"JPM"})
    assert {d["symbol"] for d in p["dividends"]} == {"JPM", "EXTRA"}
    assert {d["symbol"] for d in q["dividends"]} == {"JPM"}
    assert p["events"] == q["events"] and p["data"] == q["data"] and p["ipos"] == q["ipos"]


def test_the_payload_carries_no_timestamp():
    p = econ.build_calendar(parts=PARTS, public_symbols=None)
    assert "ts" not in p and "generated_at" not in p
    assert set(p["sources"].values()) <= {"ok", "stale", "never", "off"}


def test_the_payload_shape():
    p = econ.build_calendar(parts=PARTS, public_symbols=None)
    assert set(p) == {"events", "data", "dividends", "ipos", "sources", "settings"}
    assert p["settings"] == {"release_watch_min": 60, "actual_fresh_h": 24}
    assert p["sources"] == {"fed": "ok", "bls": "stale", "bea": "never", "dividends": "ok",
                            "nasdaq_ipo": "ok", "fred_calendar": "ok", "fred_api": "off",
                            "fredgraph": "ok"}
    assert [e["title"] for e in p["events"]] == ["Job Openings and Labor Turnover Survey",
                                                 "FOMC statement"]
    assert [d["key"] for d in p["data"]] == ["cpi", "claims", "gdp"]
    cpi, claims, gdp = p["data"]
    assert cpi == {"key": "cpi", "label": "CPI", "tile": "CPI", "unit": "pct_mom",
                   "high": True,
                   "next_release_at": "2026-10-14T12:30:00+00:00", "next_date": "2026-10-14",
                   "last_release_at": "2026-09-11T12:30:00+00:00",
                   "latest": {"obs_date": "2026-08-01", "value": pytest.approx(0.401, abs=1e-3),
                              "first_seen": SEEN, "bootstrap": False},
                   "prior": {"obs_date": "2026-07-01", "value": pytest.approx(0.5, abs=1e-3),
                             "first_seen": "2026-08-01T00:00:00+00:00", "bootstrap": True}}
    assert claims["latest"]["value"] == 197.0 and claims["prior"]["value"] == 201.0
    assert claims["prior"]["first_seen"] == "2026-09-18T00:00:00+00:00"   # naive read as UTC
    assert claims["prior"]["bootstrap"] is True
    assert claims["last_release_at"] == "2026-09-24T12:30:00+00:00"
    assert gdp["latest"] is None and gdp["prior"] is None
    assert gdp["next_release_at"] is None and gdp["next_date"] is None


def test_the_key_path_marks_fredgraph_off():
    p = econ.build_calendar(parts={**PARTS, "fred_obs_source": "fred_api"}, public_symbols=None)
    assert (p["sources"]["fred_api"], p["sources"]["fredgraph"]) == ("ok", "off")


def test_an_unchanged_calendar_serialises_byte_identically():
    a = econ.build_calendar(parts=PARTS, public_symbols=None)
    shuffled = {**PARTS, "fed": list(reversed(PARTS["fed"])),
                "ics": {"bls": list(reversed(PARTS["ics"]["bls"])), "bea": []},
                "dividends": list(reversed(PARTS["dividends"]))}
    b = econ.build_calendar(parts=shuffled, public_symbols=None)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_build_calendar_survives_empty_and_junk_parts():
    for parts in ({}, {"now": NOW}, {"now": NOW, "indicators": "x", "obs": 3, "ipos": None,
                                     "dividends": "x", "sources": [], "cfg": None}):
        p = econ.build_calendar(parts=parts, public_symbols=None)
        assert p["events"] == [] and p["dividends"] == [] and p["ipos"] == []
        assert all(v in {"ok", "stale", "never", "off"} for v in p["sources"].values())
        json.dumps(p, allow_nan=False)


def test_no_nan_or_infinity_reaches_the_payload():
    obs = {"CPIAUCSL": [{"obs_date": "2026-07-01", "value": float("inf"), "first_seen": SEEN, "bootstrap": 0},
                        {"obs_date": "2026-08-01", "value": float("nan"), "first_seen": SEEN, "bootstrap": 0}]}
    p = econ.build_calendar(parts={**PARTS, "obs": obs}, public_symbols=None)
    json.dumps(p, allow_nan=False)
    assert p["data"][0]["latest"]["value"] is None


def test_the_tier1_reader_draws_the_built_payload():
    import sys
    import pathlib
    webgui = str(pathlib.Path(__file__).resolve().parents[3] / "webgui")
    if webgui not in sys.path:
        sys.path.insert(0, webgui)
    from pages import news_view
    p = econ.build_calendar(parts=PARTS, public_symbols=None)
    groups = news_view.calendar_groups(json.loads(json.dumps(p)), now=NOW)
    assert [g["title"] for g in groups] == ["Economic news/Calendar", "Dividend / IPO",
                                            "Economic data (CPI, PPI etc)"]
    data = {t["title"]: t for t in groups[2]["tiles"]}
    cpi = data["CPI"]["indicators"][0]
    assert (cpi["state"], cpi["actual"], cpi["prior"]) == ("upcoming", "—", "+0.4% m/m")
    claims = data["Jobless claims"]["indicators"][0]
    assert claims["prior"] == "197K"
    div = groups[1]["tiles"][0]
    assert div["title"] == "JPM dividend" and div["lines"][0] == "$1.40 a share"
    assert any(t["title"] == "OURA · OURA Inc. IPO" for t in groups[1]["tiles"])
