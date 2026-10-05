"""The Desk's Market read panel: six readings, each marked a tailwind, a
headwind or neutral for stocks, from ``cache:market:read``.

The service decides each verdict code; this page turns the code into a word and
a fixed colour, and the numbers into a sentence. It computes no verdict itself.
Design: docs/plans/2026-10-05-market-read-scorecard-design.md.
"""
import datetime
import inspect
from zoneinfo import ZoneInfo

import pytest
from pages import desk as d

CT = ZoneInfo("America/Chicago")
MON = "2026-10-05"


def _at(hh, mm, day=MON):
    y, m, dd = (int(x) for x in day.split("-"))
    return datetime.datetime(y, m, dd, hh, mm, tzinfo=CT)


def _ts(hh, mm, day=MON):
    return int(_at(hh, mm, day).timestamp())


def _row(key, verdict, facts, prev=None, **extra):
    return {"key": key, "verdict": verdict, "facts": facts, "prev": prev, **extra}


def _structure_facts(spy_state="headwind"):
    return {"symbols": [
        {"symbol": "SPY", "spot": 774.77, "flip": 770.41, "ceiling": 775.0,
         "room_pct": 0.0297, "mode": "long", "state": spy_state},
        {"symbol": "QQQ", "spot": 755.23, "flip": 750.96, "ceiling": 756.0,
         "room_pct": 0.1020, "mode": "long", "state": "headwind"}]}


def _view(**over):
    view = {
        "date": MON, "ts": _ts(12, 45), "slot": "12:45", "interval_min": 15,
        "next_slot": "13:00", "final": False, "public": True,
        "tally": {"tailwind": 2, "headwind": 2, "neutral": 1, "none": 1},
        "rows": [
            _row("direction", "tailwind", {"spx_pct": 0.69, "ndx_pct": 0.79}),
            _row("breadth", "tailwind", {"advancing": 29, "declining": 6, "share": 29 / 35}),
            _row("structure", "headwind", _structure_facts()),
            _row("volatility", "headwind", {"vix_level": 15.55, "vix_pct": 1.57, "vix1d": 8.49,
                                            "vix3m": 18.04, "spx_pct": 0.69}),
            _row("flow", "neutral", {"call_lean": 1.7, "put_lean": 12.0, "calls": 10,
                                     "puts": 4, "contracts": 14}, estimate=True),
            _row("cross_asset", "none", {"tiles": [
                {"name": "TLT", "pct": -0.92, "state": "on"},
                {"name": "$DXY", "pct": None, "state": None},
                {"name": "HYG", "pct": None, "state": None}], "risk_on": 1, "risk_off": 0}),
        ],
        "history": [{"slot": "12:45", "verdicts": {}}],
    }
    view.update(over)
    return view


def _rows(view=None):
    return {r["key"]: r for r in d.read_rows(view if view is not None else _view())}


# --- words and colours ---------------------------------------------------------

def test_every_verdict_code_has_a_word_and_a_fixed_chip():
    assert d.READ_WORDS == {"tailwind": "Tailwind", "headwind": "Headwind",
                            "neutral": "Neutral", "none": "No reading"}
    assert set(d.READ_CHIPS) == set(d.READ_WORDS)
    # Four codes, four different looks: a missing reading must not wear the
    # neutral chip.
    assert len(set(d.READ_CHIPS.values())) == 4


def test_an_unknown_code_reads_as_no_reading_never_as_neutral():
    view = _view(rows=[_row("direction", "banana", {"spx_pct": 0.1, "ndx_pct": 0.1})])
    (row,) = d.read_rows(view)
    assert row["verdict_word"] == "No reading"
    assert row["chip_class"] == d.READ_CHIPS["none"]


def test_rows_come_in_the_services_order_with_whole_word_questions():
    rows = d.read_rows(_view())
    assert [r["key"] for r in rows] == ["direction", "breadth", "structure",
                                        "volatility", "flow", "cross_asset"]
    assert [r["question"] for r in rows] == ["Direction", "Breadth", "Structure",
                                             "Volatility", "Flow", "Cross-asset"]
    assert [r["verdict_word"] for r in rows] == ["Tailwind", "Tailwind", "Headwind",
                                                 "Headwind", "Neutral", "No reading"]


# --- the reading text ----------------------------------------------------------

def test_direction_reading():
    assert _rows()["direction"]["reading"] == "$SPX +0.69% · $NDX +0.79%"


def test_breadth_reading():
    assert _rows()["breadth"]["reading"] == "29 advancing · 6 declining (82.86%)"


def test_structure_reading_says_where_price_sits_for_each_symbol():
    assert _rows()["structure"]["reading"] == (
        "SPY 0.03% under its ceiling, long gamma · "
        "QQQ 0.10% under its ceiling, long gamma")


def test_structure_reading_for_the_other_cases():
    facts = {"symbols": [
        {"symbol": "SPY", "spot": 760.0, "flip": 765.0, "ceiling": 790.0,
         "room_pct": None, "mode": "short", "state": "headwind"},
        {"symbol": "QQQ", "spot": 757.0, "flip": 750.0, "ceiling": 756.0,
         "room_pct": -0.1321, "mode": "long", "state": "headwind"},
        {"symbol": "IWM", "spot": None, "flip": None, "ceiling": None,
         "room_pct": None, "mode": None, "state": "none"}]}
    row = _rows(_view(rows=[_row("structure", "none", facts)]))["structure"]
    assert row["reading"] == ("SPY below the flip, short gamma · "
                              "QQQ 0.13% above its ceiling, long gamma · IWM —")


def test_volatility_reading():
    assert _rows()["volatility"]["reading"] == (
        "VIX 15.55 (+1.57%) · one-day 8.49 · three-month 18.04")


def test_flow_reading_is_marked_as_an_estimate():
    row = _rows()["flow"]
    assert row["reading"] == "≈ calls +1.70 · puts +12.00 points bought over sold · 14 contracts"
    assert row["estimate"] is True


def test_flow_with_too_few_contracts_says_so():
    view = _view(rows=[_row("flow", "none", {"call_lean": 40.0, "put_lean": None,
                                             "calls": 3, "puts": 0, "contracts": 3},
                            estimate=True)])
    assert _rows(view)["flow"]["reading"] == "≈ 3 contracts flagged, too few to read"


def test_cross_asset_reading_uses_plain_names_and_never_invents_a_number():
    assert _rows()["cross_asset"]["reading"] == (
        "Treasuries -0.92% risk-on · Dollar — · Credit —")


@pytest.mark.parametrize("key", ["direction", "breadth", "structure", "volatility",
                                 "flow", "cross_asset"])
def test_a_row_with_no_facts_prints_a_dash_not_a_zero(key):
    row = _rows(_view(rows=[_row(key, "none", {})]))[key]
    assert "0.00" not in row["reading"] and "0 " not in row["reading"]
    assert row["reading"] in ("—", "≈ —")


# --- since the last update ------------------------------------------------------

def test_since_is_first_reading_with_no_previous():
    assert _rows()["direction"]["since"] == "first reading"


def test_since_says_what_the_chip_was_when_it_changed():
    prev = {"verdict": "neutral", "facts": {"spx_pct": 0.10, "ndx_pct": 0.12}}
    view = _view(rows=[_row("direction", "tailwind", {"spx_pct": 0.69, "ndx_pct": 0.79}, prev)])
    assert _rows(view)["direction"]["since"] == "was Neutral"


@pytest.mark.parametrize("key,facts,prev_facts,want", [
    ("direction", {"spx_pct": 0.69, "ndx_pct": 0.79}, {"spx_pct": 0.61, "ndx_pct": 0.7},
     "$SPX +0.08 points"),
    ("direction", {"spx_pct": 0.50, "ndx_pct": 0.79}, {"spx_pct": 0.61, "ndx_pct": 0.7},
     "$SPX -0.11 points"),
    ("breadth", {"advancing": 29, "declining": 6, "share": 0.8},
     {"advancing": 27, "declining": 8, "share": 0.7}, "+2 advancing"),
    ("volatility", {"vix_level": 15.55, "vix_pct": 1.57}, {"vix_level": 15.45, "vix_pct": 0.9},
     "VIX +0.10"),
    ("flow", {"call_lean": 1.7, "put_lean": 12.0, "contracts": 14},
     {"call_lean": 0.5, "put_lean": 9.0, "contracts": 12}, "calls +1.20 points"),
    ("direction", {"spx_pct": 0.69, "ndx_pct": 0.79}, {"spx_pct": 0.69, "ndx_pct": 0.7},
     "unchanged"),
    ("direction", {"spx_pct": 0.69, "ndx_pct": 0.79}, {"spx_pct": None}, "unchanged"),
    ("structure", _structure_facts(), _structure_facts(), "unchanged"),
    ("cross_asset", {"tiles": []}, {"tiles": []}, "unchanged"),
])
def test_since_shows_the_change_in_the_rows_main_number(key, facts, prev_facts, want):
    verdict = "tailwind"
    view = _view(rows=[_row(key, verdict, facts, {"verdict": verdict, "facts": prev_facts})])
    assert _rows(view)[key]["since"] == want


# --- the header ------------------------------------------------------------------

def test_header_for_a_live_reading():
    h = d.read_header(_view(), _at(12, 50))
    assert h["state"] == "live"
    assert h["text"] == "12:45 CT · next 13:00"
    assert h["tally"] == "2 tailwinds · 2 headwinds · 1 neutral · 1 no reading"


def test_the_tally_leaves_out_what_is_zero_and_counts_in_the_singular():
    h = d.read_header(_view(tally={"tailwind": 1, "headwind": 5, "neutral": 0, "none": 0}),
                      _at(12, 50))
    assert h["tally"] == "1 tailwind · 5 headwinds"


def test_header_for_the_close_reading():
    view = _view(slot="15:00", ts=_ts(15, 0), next_slot=None, final=True)
    h = d.read_header(view, _at(17, 30))
    assert (h["state"], h["text"]) == ("close", "15:00 CT · the close")


def test_a_reading_two_intervals_old_in_the_session_is_stale():
    assert d.read_header(_view(), _at(13, 14))["state"] == "live"
    h = d.read_header(_view(), _at(13, 16))
    assert (h["state"], h["text"]) == ("stale", "12:45 CT · not updating")


def test_the_stale_limit_follows_the_readings_own_interval():
    view = _view(interval_min=30, next_slot="13:15")
    assert d.read_header(view, _at(13, 40))["state"] == "live"
    assert d.read_header(view, _at(13, 50))["state"] == "stale"


def test_a_reading_from_another_day_is_stale_and_names_its_day():
    view = _view(date="2026-10-02", slot="15:00", ts=_ts(15, 0, "2026-10-02"),
                 next_slot=None, final=True)
    h = d.read_header(view, _at(8, 0))
    assert (h["state"], h["text"]) == ("stale", "Fri 2 Oct · 15:00 CT · previous session")


@pytest.mark.parametrize("view", [None, {}, 5, {"rows": []}, {"date": MON}])
def test_header_with_no_reading_is_waiting(view):
    h = d.read_header(view, _at(12, 50))
    assert h["state"] == "waiting" and h["text"] == "" and h["tally"] == ""


@pytest.mark.parametrize("view", [None, {}, 5, {"rows": 5}, {"rows": [None, 7, "x"]}])
def test_rows_of_a_malformed_view_are_empty(view):
    assert d.read_rows(view) == []


# --- public ------------------------------------------------------------------------

def test_the_public_origin_shows_only_a_view_marked_public(monkeypatch):
    import shell
    monkeypatch.setattr(shell, "is_public", lambda: True)
    assert d.read_view_shown(_view(public=True)) is not None
    assert d.read_view_shown(_view(public=False)) is None
    assert d.read_view_shown(_view(public="true")) is None
    no_flag = _view()
    del no_flag["public"]
    assert d.read_view_shown(no_flag) is None
    monkeypatch.setattr(shell, "is_public", lambda: False)
    assert d.read_view_shown(_view(public=False)) is not None


# --- wiring ------------------------------------------------------------------------

def test_the_view_is_polled_and_has_its_own_region():
    assert d.READ_VIEW == "market:read"
    assert d.READ_VIEW in d.VIEWS
    assert d._REGION_VIEWS["read"] == (d.READ_VIEW,)


def test_the_panel_sits_between_the_headlines_and_the_market_summary():
    src = inspect.getsource(d.render)
    news = src.index('PANEL_HEADS["news"]')
    read = src.index('PANEL_HEADS["read"]')
    summary = src.index('ui.label("MARKET SUMMARY")')
    assert news < read < summary


def test_the_painter_is_module_level_and_wired_without_a_new_nested_function():
    assert callable(d.paint_read)
    src = inspect.getsource(d.render)
    assert '"read": lambda: paint_read(' in src
    # ...and the one-second clock re-checks it, so a reading that stops
    # updating greys without waiting for a new one to arrive.
    tick = src[src.index("def _tick_clock("):src.index("async def _poll(")]
    assert "paint_read(" in tick and "force=False" in tick


def test_the_panel_head_says_what_it_is_and_what_it_is_not():
    title, use_line = d.PANEL_HEADS["read"]
    assert title == "Market read"
    assert "for stocks" in use_line and "not a forecast" in use_line
