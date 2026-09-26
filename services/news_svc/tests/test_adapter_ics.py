import pathlib

from services.news_svc.adapters import ics

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_bls_eastern_times_become_aware_instants():
    evs = ics.parse(FIX.joinpath("bls_sample.ics").read_bytes())
    cpi = next(e for e in evs if e["summary"] == "Consumer Price Index")
    assert cpi["at"] == "2026-10-14T12:30:00+00:00"        # 08:30 EDT
    assert cpi["date"] == "2026-10-14"


def test_bea_utc_and_escapes():
    evs = ics.parse(FIX.joinpath("bea_sample.ics").read_bytes())
    gdp = next(e for e in evs if e["summary"].startswith("GDP (Advance"))
    assert gdp["at"] == "2026-10-29T12:30:00+00:00"
    assert "\\," not in gdp["summary"] and "3rd Quarter 2026" in gdp["summary"]


def test_folded_lines_are_unfolded():
    evs = ics.parse(FIX.joinpath("bls_sample.ics").read_bytes())
    assert any(e["summary"] == "Job Openings and Labor Turnover Survey" for e in evs)  # folded in the fixture


def test_date_only_event_has_no_time():
    e = next(e for e in ics.parse(FIX.joinpath("bls_sample.ics").read_bytes()) if e["at"] is None)
    assert e["date"]


def test_est_winter_offset():   # 08:30 EST = 13:30Z
    body = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;TZID=US-Eastern:20261210T083000\r\nSUMMARY:Consumer Price Index\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    assert ics.parse(body)[0]["at"] == "2026-12-10T13:30:00+00:00"


def test_junk_never_raises():
    assert ics.parse(b"") == [] and ics.parse(b"\xff\xfe garbage") == []
    assert ics.parse(b"BEGIN:VEVENT\nDTSTART:notadate\nSUMMARY:x\nEND:VEVENT") == []


# --- beyond the plan: the real, whole files and the edge cases they carry ---

def test_a_fold_inside_an_escape_is_unfolded_before_unescaping():
    # The real BEA file folds between the backslash and the comma of "\,".
    evs = ics.parse(FIX.joinpath("bea_sample.ics").read_bytes())
    third = next(e for e in evs if e["summary"].startswith("GDP (Third Estimate)"))
    assert third["summary"] == ("GDP (Third Estimate), Industries, Corporate Profits, State GDP, "
                                "and State Personal Income, 2nd Quarter 2026; State PCE, 2025")
    assert third["at"] == "2026-09-30T12:30:00+00:00"


def test_value_date_time_param_is_a_timed_event():
    evs = ics.parse(FIX.joinpath("bea_sample.ics").read_bytes())
    old = next(e for e in evs if e["summary"].startswith("Gross Domestic Product, 4th Quarter"))
    assert old["summary"].endswith("(Advance Estimate)")
    assert old["at"] == "2025-01-30T13:30:00+00:00"          # DTSTART;VALUE=DATE-TIME:...Z


def test_whole_real_bls_file_parses_every_event():
    evs = ics.parse(FIX.joinpath("bls_full.ics").read_bytes())
    assert len(evs) == 313
    assert all(e["at"] and e["at"].endswith("+00:00") for e in evs)
    # the VTIMEZONE block's own DTSTARTs are not events
    assert not any(e["date"].startswith("2007") for e in evs)


def test_whole_real_bea_file_parses_every_event():
    evs = ics.parse(FIX.joinpath("bea_full.ics").read_bytes())
    assert len(evs) == 119
    assert all("\\" not in e["summary"] for e in evs)


def test_unknown_tzid_skips_only_that_event():
    body = (b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;TZID=Mars/Olympus:20261210T083000\nSUMMARY:a\nEND:VEVENT\n"
            b"BEGIN:VEVENT\nDTSTART;TZID=America/New_York:20261210T083000\nSUMMARY:b\nEND:VEVENT\nEND:VCALENDAR\n")
    evs = ics.parse(body)
    assert [e["summary"] for e in evs] == ["b"]
    assert evs[0]["at"] == "2026-12-10T13:30:00+00:00"


def test_escapes_and_description():
    body = (b"BEGIN:VEVENT\r\nDTSTART:20261029T123000Z\r\nSUMMARY:A\\; B\\\\C\r\n"
            b"DESCRIPTION:line1\\nline2\r\nEND:VEVENT\r\n")
    e = ics.parse(body)[0]
    assert e["summary"] == "A; B\\C" and e["description"] == "line1\nline2"


def test_non_bytes_and_missing_summary_never_raise():
    assert ics.parse(None) == [] and ics.parse("text") == []
    assert ics.parse(b"BEGIN:VEVENT\nDTSTART:20261029T123000Z\nEND:VEVENT") == []
