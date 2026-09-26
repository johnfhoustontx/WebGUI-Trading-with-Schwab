"""The Federal Reserve Board's calendar.json adapter.

The fixture is a trimmed but REAL subset of
https://www.federalreserve.gov/json/calendar.json as fetched 2026-09-26 with
the feed User-Agent - rows copied verbatim, the leading UTF-8 BOM kept. In the
live file no FOMC / Beige / Speeches / Testimony row carries a blank time or a
comma ``days`` list (only Stat / Other / events / Conferences rows do), so the
expansion and blank-time cases are driven by the real Stat and Other rows.
"""
import json
import pathlib

from services.news_svc.adapters import fed_calendar

FIX = pathlib.Path(__file__).parent / "fixtures"
BODY = (FIX / "fed_calendar.json").read_bytes()
TYPES = ("FOMC", "Beige", "Speeches", "Testimony")
ALL = TYPES + ("Stat", "Other")


def test_the_fixture_really_starts_with_a_bom():
    assert BODY.startswith(b"\xef\xbb\xbf")
    # json.loads(bytes) sniffs the BOM itself; decoded as plain utf-8 text it refuses.
    try:
        json.loads(BODY.decode("utf-8"))
    except json.JSONDecodeError:
        pass
    else:                                            # pragma: no cover
        raise AssertionError("json.loads accepted a BOM - the fixture lost it")


def test_bom_is_stripped():
    assert fed_calendar.parse(BODY, types=TYPES)


def test_fomc_rows_are_labelled_and_eastern():
    evs = fed_calendar.parse(BODY, types=TYPES)
    stmt = next(e for e in evs if e["kind"] == "fomc_statement" and e["date"] == "2026-10-28")
    assert stmt["at"] == "2026-10-28T18:00:00+00:00" and stmt["title"] == "FOMC statement"
    press = next(e for e in evs if e["kind"] == "fomc_press" and e["date"] == "2026-10-28")
    assert press["at"] == "2026-10-28T18:30:00+00:00" and press["title"] == "Press conference"
    mins = next(e for e in evs if e["kind"] == "fomc_minutes")
    assert mins["date"] == "2026-10-07" and mins["at"] == "2026-10-07T18:00:00+00:00"
    assert mins["title"] == "FOMC minutes"


def test_winter_fomc_is_est_not_edt():                # 2:00 p.m. EST = 19:00Z
    evs = fed_calendar.parse(BODY, types=TYPES)
    stmt = next(e for e in evs if e["kind"] == "fomc_statement" and e["date"] == "2026-12-09")
    assert stmt["at"] == "2026-12-09T19:00:00+00:00"


def test_beige_and_testimony_kinds():
    evs = fed_calendar.parse(BODY, types=TYPES)
    beige = next(e for e in evs if e["kind"] == "beige")
    assert (beige["date"], beige["title"]) == ("2026-10-14", "Beige Book")
    t = next(e for e in evs if e["kind"] == "testimony")
    assert t["title"] == "Testimony - Chairman Kevin Warsh"
    assert t["at"] == "2026-07-14T14:00:00+00:00"
    assert t["location"] == "Before the U.S. House Financial Services Committee"


def test_speech_titles_are_stripped_and_entities_unescape():
    sp = [e for e in fed_calendar.parse(BODY, types=TYPES) if e["kind"] == "speech"]
    assert len(sp) == 3 and {e["date"] for e in sp} == {"2026-10-01"}
    assert all(e["title"] == e["title"].strip() for e in sp)
    assert "Discussion - Governor Lisa D. Cook" in {e["title"] for e in sp}
    assert all("&#" not in e["location"] and "&amp;" not in e["location"] for e in sp)
    locs = " ".join(e["location"] for e in sp)
    assert "Atlantic Council’s" in locs and "Q&A" in locs


def test_description_is_unescaped_and_untagged():
    mins = next(e for e in fed_calendar.parse(BODY, types=TYPES) if e["kind"] == "fomc_minutes")
    assert mins["description"] == "Meeting of September 15-16"


def test_days_list_expands_one_event_per_day():
    h41 = [e for e in fed_calendar.parse(BODY, types=ALL) if e["title"].startswith("H.4.1")]
    assert [e["date"] for e in h41] == ["2026-10-01", "2026-10-08", "2026-10-15",
                                         "2026-10-22", "2026-10-29"]
    assert h41[0]["at"] == "2026-10-01T20:30:00+00:00"          # 4:30 p.m. EDT
    assert all(e["kind"] == "stat" for e in h41)


def test_blank_time_is_date_only():
    evs = fed_calendar.parse(BODY, types=ALL)
    g20 = next(e for e in evs if e["title"].startswith("G.20"))
    assert g20["at"] is None and g20["date"] == "2026-12-31"
    hol = next(e for e in evs if e["kind"] == "other")
    assert hol["at"] is None and hol["date"] == "2026-11-11"


def test_types_filter():
    evs = fed_calendar.parse(BODY, types=TYPES)
    assert not any(e["kind"] in ("stat", "other") for e in evs)
    assert fed_calendar.parse(BODY, types=()) == []


def test_ids_are_stable_and_unique():
    for types in (TYPES, ALL):
        evs = fed_calendar.parse(BODY, types=types)
        assert len({e["id"] for e in evs}) == len(evs)
        assert [e["id"] for e in evs] == [e["id"] for e in fed_calendar.parse(BODY, types=types)]
    stmt = next(e for e in fed_calendar.parse(BODY, types=TYPES) if e["kind"] == "fomc_statement")
    assert stmt["id"] == "fed:2026-10-28:fomc-meeting:14:00"
    g20 = next(e for e in fed_calendar.parse(BODY, types=ALL) if e["title"].startswith("G.20"))
    assert g20["id"].endswith(":day")


def test_one_bad_row_is_skipped_never_the_batch():
    doc = {"events": [
        {"month": "2026-10", "days": "14", "time": "2:00 p.m.", "type": "Beige", "title": "Beige Book"},
        {"month": "2026-13", "days": "1", "time": "2:00 p.m.", "type": "Beige", "title": "Bad month"},
        {"month": "2026-10", "days": "32", "time": "2:00 p.m.", "type": "Beige", "title": "Bad day"},
        {"month": "2026-10", "days": "15", "time": "25:99 p.m.", "type": "Beige", "title": "Bad time"},
        {"month": "2026-10", "days": "x, 16", "time": "2:00 p.m.", "type": "Beige", "title": "Half bad"},
        "not a dict",
        {},
    ]}
    evs = fed_calendar.parse(json.dumps(doc).encode(), types=TYPES)
    assert [e["date"] for e in evs] == ["2026-10-14", "2026-10-16"]


def test_noon_and_midnight_hours():
    doc = {"events": [
        {"month": "2026-10", "days": "5", "time": "12:00 p.m.", "type": "Speeches", "title": "Noon"},
        {"month": "2026-10", "days": "6", "time": "12:15 a.m.", "type": "Speeches", "title": "Late"},
    ]}
    evs = fed_calendar.parse(json.dumps(doc).encode(), types=TYPES)
    assert evs[0]["at"] == "2026-10-05T16:00:00+00:00"
    assert evs[1]["at"] == "2026-10-06T04:15:00+00:00"


def test_junk_never_raises():
    for b in (b"", b"{}", b'{"events": 5}', b'{"events": [{"month": "x"}]}',
              b"\xff\xfe garbage", b"[1, 2]", b"null", None, 42):
        assert fed_calendar.parse(b, types=TYPES) == []
    assert fed_calendar.parse(BODY, types=None) == []


# ── review findings (2026-09-26) ──────────────────────────────────────────────
def _one(**kw):
    base = {"month": "2026-10", "days": "5", "time": "2:00 p.m.", "type": "Speeches",
            "title": "A speech"}
    base.update(kw)
    return fed_calendar.parse(json.dumps({"events": [base]}).encode(), types=TYPES)


def test_a_live_link_with_a_leading_space_is_kept():
    ev, = _one(live=" https://www.federalreserve.gov/live.htm ")
    assert ev["link"] == "https://www.federalreserve.gov/live.htm"
    ev, = _one(link="  https://a.example/x", live="https://b.example/y")
    assert ev["link"] == "https://a.example/x"


def test_http_and_scheme_less_links_are_dropped():
    for bad in ("http://www.federalreserve.gov/x", "www.federalreserve.gov/x",
                "/newsevents/x.htm", "javascript:alert(1)", 5):
        ev, = _one(link=bad)
        assert ev["link"] == "", bad


def test_an_unreadable_time_keeps_the_row_as_date_only(caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger=fed_calendar.__name__)
    for raw in ("noon", "14:00", "TBA", "Afternoon"):
        ev, = _one(time=raw)
        assert ev["at"] is None and ev["id"].endswith(":day") and ev["date"] == "2026-10-05", raw
    assert "noon" in caplog.text


def test_a_time_range_takes_its_start():
    ev, = _one(time="2:00 p.m. - 3:00 p.m.")
    assert ev["at"] == "2026-10-05T18:00:00+00:00" and ev["id"].endswith(":14:00")
    ev, = _one(time="9:30 a.m.-10:30 a.m.")
    assert ev["at"] == "2026-10-05T13:30:00+00:00"


def test_a_time_range_with_dashes_or_to_takes_its_start():
    for raw in ("2:00 p.m. \u2013 3:00 p.m.", "2:00 p.m.\u20143:00 p.m.",
                "2:00 p.m. to 3:00 p.m.", "2:00 p.m. TO 3:30 p.m. ET"):
        ev, = _one(time=raw)
        assert ev["at"] == "2026-10-05T18:00:00+00:00", raw


def test_a_trailing_eastern_zone_word_is_allowed():
    for raw in ("2:00 p.m. ET", "2:00 p.m. EDT", "2:00 p.m. est", "2:00 p.m. (ET)",
                "2:00 p.m. ET - 3:00 p.m. ET"):
        ev, = _one(time=raw)
        assert ev["at"] == "2026-10-05T18:00:00+00:00" and ev["id"].endswith(":14:00"), raw


def test_another_zone_word_is_still_date_only():
    for raw in ("2:00 p.m. PT", "2:00 p.m. CET", "2:00 p.m. today"):
        ev, = _one(time=raw)
        assert ev["at"] is None and ev["id"].endswith(":day"), raw


def test_types_as_a_bare_string_is_one_type_not_its_letters():
    evs = fed_calendar.parse(BODY, types="FOMC")
    assert evs and {e["type"] for e in evs} == {"FOMC"}
    assert fed_calendar.parse(BODY, types="F") == []
