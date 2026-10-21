"""The Opening Scalp page (``/options/scalp``): its pure vocabulary.

The page's most common honest message is "no measured edge", so the tests pin
what keeps that message calm and truthful: the four trades are named by that
morning's contract, a base rate is the headline unless a role truly has an edge,
a role measured to LOSE more often is cautioned (never recommended), an absent
input never reads as a disagreement, and yesterday's call is never labelled as
today's. The page reads one view and one view only.
"""
import pathlib

import pytest

from pages import fmt
from pages.options import scalp

SRC = pathlib.Path(scalp.__file__).read_text(encoding="utf-8")

ROLES = ("long_with", "long_against", "credit_with", "credit_against")


def _role(structure, p=0.4, base=0.4, *, n=30, n_base=30, edge=False,
          strikes=(501.0,), entry=2.975, source="chain", win=0.81, loss=-1.16):
    return {"structure": structure, "p": p, "lo": max(0.0, p - 0.15),
            "hi": min(1.0, p + 0.15), "n": n, "p_base": base, "n_base": n_base,
            "edge": edge, "strikes": list(strikes) if strikes else None,
            "entry_value": entry, "entry_source": source,
            "win_points": win, "loss_points": loss}


def _symbol(direction=1, *, highlight=None, avoid=(), edges=(), **extra):
    """A morning's entry. ``edges`` names roles that carry an edge flag."""
    up = direction == 1
    structures = {"long_with": "long_call" if up else "long_put",
                  "long_against": "long_put" if up else "long_call",
                  "credit_with": "pcs" if up else "ccs",
                  "credit_against": "ccs" if up else "pcs"}
    roles = {r: _role(s, edge=r in edges) for r, s in structures.items()}
    entry = {"direction": direction, "bucket": "high", "alignment": 1.0,
             "note": None if highlight else "no measured edge", "points_note": None,
             "inputs": {}, "agree": {}, "roles": roles, "highlight": highlight,
             "avoid": list(avoid), "made_at": "2026-10-19T08:36:00-05:00",
             "chain": {"spot": 500.75, "atm_iv": 0.18, "expiry": "2026-10-19:0"},
             "sigma": 0.18, "sigma_source": "chain", "entry_spot": 500.75}
    entry.update(extra)
    return entry


def _by_role(rows):
    return {r["id"]: r for r in rows}


# ── naming the trades by that morning's contract ────────────────────────────
def test_role_rows_name_contracts_by_direction():
    up = _by_role(scalp.role_rows(_symbol(1)))
    assert [up[r]["trade"] for r in ROLES] == [
        "Long call", "Long put", "Put credit spread", "Call credit spread"]
    down = _by_role(scalp.role_rows(_symbol(-1)))
    assert [down[r]["trade"] for r in ROLES] == [
        "Long put", "Long call", "Call credit spread", "Put credit spread"]


def test_role_rows_keep_the_four_roles_in_order_and_say_with_or_against():
    rows = scalp.role_rows(_symbol(1))
    assert [r["id"] for r in rows] == list(ROLES)
    assert [r["side"] for r in rows] == [
        "with the opening move", "against the opening move",
        "with the opening move", "against the opening move"]


def test_no_roles_no_rows():
    assert scalp.role_rows({"direction": None, "roles": {}}) == []
    assert scalp.role_rows({}) == []
    assert scalp.role_rows(None) == []


# ── the honest default: no measured edge ────────────────────────────────────
def test_no_edge_note_shown():
    text = scalp.headline(_symbol(1)).lower()
    assert "no measured edge" in text


def test_no_edge_headline_never_recommends_a_role():
    text = scalp.headline(_symbol(1, avoid=["long_against"])).lower()
    assert "no measured edge" in text
    assert "long put" not in text and "long call" not in text


def test_the_base_rate_is_the_headline_number_and_no_row_is_marked_without_an_edge():
    rows = scalp.role_rows(_symbol(1))
    assert all(r["mark"] == "" for r in rows)
    assert all(r["tone"] == "plain" for r in rows)
    assert all(r["usual"] == "40.00%" for r in rows)


def test_a_highlighted_role_is_marked_as_the_measured_edge_with_interval_and_n():
    sym = _symbol(1, highlight="long_against", edges=("long_against",))
    sym["roles"]["long_against"].update(p=0.63, lo=0.455, hi=0.781, n=30, p_base=0.5)
    rows = _by_role(scalp.role_rows(sym))
    hit = rows["long_against"]
    assert hit["mark"] == "Measured edge" and hit["tone"] == "edge"
    assert hit["rate"] == "63.00%" and hit["interval"] == "45.50% – 78.10%"
    assert hit["n"] == "30"
    assert all(rows[r]["mark"] == "" for r in ROLES if r != "long_against")
    text = scalp.headline(sym)
    assert "Long put" in text and "63.00%" in text and "50.00%" in text
    assert "no measured edge" not in text.lower()


def test_an_avoid_role_gets_its_own_caution_not_a_recommendation():
    sym = _symbol(1, avoid=["credit_with"], edges=("credit_with",))
    rows = _by_role(scalp.role_rows(sym))
    assert rows["credit_with"]["tone"] == "caution"
    assert "lose more often than usual" in rows["credit_with"]["mark"]
    assert rows["credit_with"]["mark"] != "Measured edge"
    assert rows["long_with"]["tone"] == "plain"


def test_a_highlight_that_is_also_listed_is_still_the_edge():
    sym = _symbol(1, highlight="long_with", avoid=["credit_with"],
                  edges=("long_with", "credit_with"))
    rows = _by_role(scalp.role_rows(sym))
    assert rows["long_with"]["tone"] == "edge"
    assert rows["credit_with"]["tone"] == "caution"


def test_edge_and_caution_rows_carry_their_own_tint_and_the_kit_binds_it():
    """``_row_class`` is what ``kit.table`` reads for each row (ROW_CLASS_FN), so
    the edge and the caution rows are tinted by fixed palette classes, and a plain
    row by none."""
    from pages import ui_kit as kit
    assert "row._row_class" in kit.ROW_CLASS_FN
    sym = _symbol(1, highlight="long_with", avoid=["credit_with"],
                  edges=("long_with", "credit_with"))
    rows = _by_role(scalp.role_rows(sym))
    edge, caution = rows["long_with"]["_row_class"], rows["credit_with"]["_row_class"]
    assert edge.startswith("bg-[") and caution.startswith("bg-[")
    assert edge != caution
    assert rows["long_against"]["_row_class"] == ""
    assert rows["credit_against"]["_row_class"] == ""


# ── numbers ─────────────────────────────────────────────────────────────────
def test_probabilities_print_two_decimals_via_fmt():
    sym = _symbol(1)
    sym["roles"]["long_with"].update(p=0.5, lo=0.25, hi=0.75, p_base=0.5)
    row = _by_role(scalp.role_rows(sym))["long_with"]
    assert row["usual"] == fmt.pct(50.0) == "50.00%"
    assert row["rate"] == "50.00%"
    assert row["interval"] == "25.00% – 75.00%"
    assert ":g" not in SRC


def test_strikes_entry_and_points_print_two_decimals():
    row = _by_role(scalp.role_rows(_symbol(1)))
    assert row["long_with"]["strikes"] == fmt.strike(501.0) == "501.00"
    sym = _symbol(1)
    sym["roles"]["credit_with"].update(strikes=[500.0, 499.0], entry_value=0.1,
                                       win_points=1.266, loss_points=-0.987)
    spread = _by_role(scalp.role_rows(sym))["credit_with"]
    assert spread["strikes"] == "500.00 / 499.00"
    assert spread["entry"] == "0.10 credit · live quote"
    assert spread["target"] == "+1.27" and spread["stop"] == "-0.99"
    assert row["long_with"]["entry"] == "2.98 debit · live quote"


def test_a_modelled_entry_says_so():
    sym = _symbol(1)
    sym["roles"]["long_with"].update(entry_source="model")
    assert _by_role(scalp.role_rows(sym))["long_with"]["entry"].endswith("modelled")


def test_a_missing_figure_is_the_dash_never_zero():
    sym = _symbol(1)
    sym["roles"]["long_with"].update(p=None, lo=None, hi=None, p_base=None,
                                     strikes=None, entry_value=None,
                                     entry_source=None, win_points=None,
                                     loss_points=None)
    row = _by_role(scalp.role_rows(sym))["long_with"]
    for key in ("rate", "usual", "interval", "strikes", "entry", "target", "stop"):
        assert row[key] == fmt.NO_READING, key


# ── how it was priced, and the agreement line ───────────────────────────────
def test_pricing_line_says_chain_or_model():
    assert scalp.pricing_line(_symbol(1)) == (
        "Priced from the live chain: underlying 500.75, at-the-money volatility 18.00%.")
    assert "modelled" in scalp.pricing_line(_symbol(1, chain=None))
    assert scalp.pricing_line({"roles": {}}) == ""


def test_agreement_line_is_a_share_of_the_inputs_that_took_a_side():
    assert scalp.alignment_text(_symbol(1, alignment=0.5, bucket="mid")) == (
        "Agreement with the opening move: mid (50.00% of the inputs that took a side)")
    assert scalp.alignment_text({"bucket": None}) == ""


# ── the whole-word headline for a symbol with no roles ──────────────────────
def test_headline_says_why_there_is_no_call():
    flat = {"direction": None, "roles": {}, "note": "opened flat — no direction"}
    assert "opened flat" in scalp.headline(flat).lower()
    missing = {"direction": None, "roles": {}, "note": "no opening bar yet"}
    assert "no opening bar yet" in scalp.headline(missing).lower()
    missed = {"direction": None, "roles": {}, "note": "missed the call window"}
    assert "missed the call window" in scalp.headline(missed).lower()
    assert scalp.headline({}) != "" and scalp.headline(None) != ""


def test_direction_in_words():
    assert scalp.direction_words(1) == "Opened up"
    assert scalp.direction_words(-1) == "Opened down"
    assert scalp.direction_words(None) == "No direction"


# ── the inputs panel ────────────────────────────────────────────────────────
def test_absent_input_shows_absent_not_disagree():
    sym = _symbol(1, inputs={"fut_asia": None, "fut_gap": -0.2},
                  agree={"fut_asia": None, "fut_gap": -1})
    rows = {r["id"]: r for r in scalp.input_rows(sym)}
    assert rows["fut_asia"]["verdict"] == "Absent"
    assert rows["fut_asia"]["reading"] == fmt.NO_READING
    assert rows["fut_gap"]["verdict"] == "Disagrees"
    assert rows["fut_gap"]["reading"] == "-0.20%"


def test_agreement_words_are_whole_words():
    sym = _symbol(1, inputs={"fut_asia": 0.4, "fut_europe": 0.01, "fut_gap": -0.3,
                             "vol_change": None},
                  agree={"fut_asia": 1, "fut_europe": 0, "fut_gap": -1,
                         "vol_change": None})
    rows = {r["id"]: r for r in scalp.input_rows(sym)}
    assert rows["fut_asia"]["verdict"] == "Agrees"
    assert rows["fut_europe"]["verdict"] == "No side taken (dead zone)"
    assert rows["fut_gap"]["verdict"] == "Disagrees"
    assert rows["vol_change"]["verdict"] == "Absent"
    assert all(len(r["label"]) > 12 for r in rows.values())


def test_an_input_without_a_direction_is_information_only():
    sym = _symbol(1, inputs={"open_range_ratio": 1.5},
                  agree={})
    row = {r["id"]: r for r in scalp.input_rows(sym)}["open_range_ratio"]
    assert row["verdict"] == "Information only"
    assert row["reading"] == "1.50×"


def test_input_readings_use_their_own_units():
    sym = _symbol(1, inputs={"fut_range_pos": 0.8, "open_close_loc": 0.25,
                             "open_broke_pm": 1.0, "etf:XLK": 0.5},
                  agree={"fut_range_pos": 1, "open_close_loc": -1,
                         "open_broke_pm": 1, "etf:XLK": 1})
    rows = {r["id"]: r for r in scalp.input_rows(sym)}
    assert rows["fut_range_pos"]["reading"] == "80.00%"
    assert rows["open_close_loc"]["reading"] == "25.00%"
    assert "above" in rows["open_broke_pm"]["reading"]
    assert "XLK" in rows["etf:XLK"]["label"] and rows["etf:XLK"]["reading"] == "+0.50%"


def test_a_flat_open_reads_no_opening_direction_not_absent():
    """No direction means the service scores nothing, so every ``agree`` is None
    beside real readings. Those are NOT absent: the reading is there."""
    flat = {"direction": None, "roles": {}, "note": "opened flat — no direction",
            "inputs": {"fut_asia": 0.05, "fut_europe": None, "open_close_loc": 0.5,
                       "open_broke_pm": 0.0, "open_range_ratio": 0.8},
            "agree": {"fut_asia": None, "fut_europe": None, "open_close_loc": None,
                      "open_broke_pm": None}}
    rows = {r["id"]: r for r in scalp.input_rows(flat)}
    assert rows["fut_asia"]["verdict"] == "No opening direction"
    assert rows["fut_asia"]["reading"] == "+0.05%"
    assert rows["open_close_loc"]["verdict"] == "No opening direction"
    assert rows["open_broke_pm"]["verdict"] == "No opening direction"
    # The reading itself is missing: that, and only that, is Absent.
    assert rows["fut_europe"]["verdict"] == "Absent"
    assert rows["fut_europe"]["reading"] == fmt.NO_READING
    # Unchanged: the opening range ratio has no direction by nature.
    assert rows["open_range_ratio"]["verdict"] == "Information only"
    # An agree map with no keys at all (nothing was scored) reads the same way.
    bare = {"direction": None, "inputs": {"fut_gap": 0.1}, "agree": {}}
    assert scalp.input_rows(bare)[0]["verdict"] == "No opening direction"


def test_the_inputs_title_does_not_count_unscored_readings_as_absent():
    flat = {"direction": None, "inputs": {"fut_asia": 0.05, "fut_europe": None},
            "agree": {}}
    text = scalp.input_summary(scalp.input_rows(flat))
    assert "no opening direction" in text and "1 absent" in text
    assert "0 agree" not in text


def test_the_unscored_count_is_singular_for_one_and_plural_for_more():
    one = {"direction": None, "inputs": {"fut_asia": 0.05}, "agree": {}}
    assert scalp.input_summary(scalp.input_rows(one)) \
        == "1 reading, no opening direction to compare"
    two = {"direction": None, "inputs": {"fut_asia": 0.05, "fut_gap": 0.1},
           "agree": {}}
    assert scalp.input_summary(scalp.input_rows(two)) \
        == "2 readings, no opening direction to compare"
    gap = {"direction": None, "inputs": {"fut_asia": 0.05, "fut_gap": None},
           "agree": {}}
    assert scalp.input_summary(scalp.input_rows(gap)) \
        == "1 reading, no opening direction to compare · 1 absent"


def test_a_symbol_with_a_direction_still_counts_a_missing_reading_as_absent():
    rows = scalp.input_rows(_symbol(1, inputs={"fut_asia": None},
                                    agree={"fut_asia": None}))
    assert rows[0]["verdict"] == "Absent"


def test_an_unlisted_input_still_shows():
    sym = _symbol(1, inputs={"mystery": 3.0}, agree={"mystery": 1})
    rows = {r["id"]: r for r in scalp.input_rows(sym)}
    assert rows["mystery"]["verdict"] == "Agrees"


# ── the state line and whose call it is ─────────────────────────────────────
# "Today" is the Central CLOCK's date, never the view's own ``day`` (the service
# publishes waiting / closed only when the 08:36 slot runs or Refresh is pressed,
# so the view keeps yesterday's ``call`` payload overnight and over a weekend).
import datetime as _dt
from zoneinfo import ZoneInfo

_CT = ZoneInfo("America/Chicago")


def _at(y, m, d, hh, mm=0):
    return _dt.datetime(y, m, d, hh, mm, tzinfo=_CT)


def _friday_view():
    """The view as it stands after Friday 2026-10-16's 08:36 call."""
    return {"state": "call", "day": "2026-10-16", "call_day": "2026-10-16",
            "next_call": "2026-10-19T08:36:00-05:00"}


def test_state_lines():
    nxt = "2026-10-20T08:36:00-05:00"
    tue_pm = _at(2026, 10, 20, 12)      # a trading day after the call time
    assert scalp.state_line({"state": "call"}, tue_pm) is None
    fresh = {"day": "2026-10-20", "next_call": nxt}
    assert scalp.state_line(dict(fresh, state="waiting"), tue_pm) == "Next call at 08:36 CT"
    closed = scalp.state_line(dict(fresh, state="closed"), tue_pm)
    assert closed.startswith("Market closed — next call ") and "08:36 CT" in closed
    assert "Tue" in closed and "2026-10-20" in closed
    assert scalp.state_line(dict(fresh, state="error"), tue_pm)         == "The last refresh failed — showing the last call"
    assert scalp.state_line({"state": "waiting", "day": "2026-10-20", "next_call": None},
                            tue_pm) == "Waiting for the morning call"
    assert scalp.state_line({"state": "closed", "day": "2026-10-20",
                             "next_call": "junk"}, tue_pm) == "Market closed"


def test_a_stale_waiting_or_closed_view_is_not_believed():
    # Published Saturday as "closed"; the page is read on a trading afternoon.
    stale = {"state": "closed", "day": "2026-10-17",
             "next_call": "2026-10-19T08:36:00-05:00"}
    assert scalp.state_line(stale, _at(2026, 10, 19, 12)) is None


def test_friday_call_read_monday_before_the_open_is_not_todays():
    view, now = _friday_view(), _at(2026, 10, 19, 7)
    label = scalp.call_label(view, _symbol(1), now)
    assert label.startswith("Call of 2026-10-16") and "not today's call" in label
    assert scalp.state_line(view, now) == "Next call at 08:36 CT"


def test_friday_call_read_on_saturday_names_monday():
    view, now = _friday_view(), _at(2026, 10, 17, 11)
    assert "not today's call" in scalp.call_label(view, _symbol(1), now)
    line = scalp.state_line(view, now)
    assert line.startswith("Market closed — next call ")
    assert "Mon 2026-10-19" in line and "08:36 CT" in line


def test_the_same_view_on_friday_is_todays_call():
    view, now = _friday_view(), _at(2026, 10, 16, 10)
    assert scalp.call_label(view, _symbol(1), now) == "Today's call · made 08:36 CT"
    assert scalp.state_line(view, now) is None


def test_a_holiday_names_the_next_trading_day():
    # Labor Day, Monday 2026-09-07: the next call is Tuesday's.
    view = {"state": "call", "day": "2026-09-04", "call_day": "2026-09-04"}
    line = scalp.state_line(view, _at(2026, 9, 7, 9))
    assert "Tue 2026-09-08" in line and "08:36 CT" in line


def test_across_the_daylight_saving_change():
    # The clocks fall back on Sunday 2026-11-01: Friday's call, read on Sunday
    # and on Monday morning, is still Friday's and the next call is 08:36 CT.
    view = {"state": "call", "day": "2026-10-30", "call_day": "2026-10-30"}
    sunday = _at(2026, 11, 1, 12)
    assert "Mon 2026-11-02" in scalp.state_line(view, sunday)
    monday = _at(2026, 11, 2, 7)
    assert "not today's call" in scalp.call_label(view, _symbol(1), monday)
    assert scalp.state_line(view, monday) == "Next call at 08:36 CT"
    # a clock given in UTC lands on the same Central date
    utc = _dt.datetime(2026, 11, 2, 13, 0, tzinfo=_dt.timezone.utc)   # 07:00 CST
    assert scalp.state_line(view, utc) == "Next call at 08:36 CT"


def test_yesterdays_call_is_never_read_as_todays():
    view = {"state": "waiting", "day": "2026-10-20", "call_day": "2026-10-19"}
    label = scalp.call_label(view, _symbol(1), _at(2026, 10, 20, 7))
    assert "2026-10-19" in label and "08:36 CT" in label
    assert "not today" in label.lower()
    today = {"state": "call", "day": "2026-10-19", "call_day": "2026-10-19"}
    assert scalp.call_label(today, _symbol(1), _at(2026, 10, 19, 9))         == "Today's call · made 08:36 CT"


def test_call_label_survives_a_missing_stamp():
    view = {"state": "call", "day": "2026-10-19", "call_day": "2026-10-19"}
    assert scalp.call_label(view, {"direction": None}, _at(2026, 10, 19, 9))         == "Today's call"
    stale = {"state": "closed", "day": "2026-10-19", "call_day": None}
    assert scalp.call_label(stale, {}, _at(2026, 10, 19, 9)) == "Last call"


# ── context: third-party text ───────────────────────────────────────────────
def test_headlines_link_only_to_web_addresses():
    ctx = {"news": [
        {"title": "Stocks open higher", "url": "https://x.example/a",
         "source": "wire", "published_at": "2026-10-19T13:00:00Z"},
        {"title": "Click me", "url": "javascript:alert(1)", "source": "bad"},
        {"title": "", "url": None, "source": "none"}]}
    rows = scalp.news_rows(ctx)
    assert rows[0]["href"] == "https://x.example/a"
    assert rows[1]["href"] is None and rows[1]["title"] == "Click me"
    assert rows[2]["title"] == "(untitled)"
    assert scalp.news_rows({}) == [] and scalp.news_rows(None) == []


def test_the_report_link_is_checked_too():
    assert scalp.report_href({"report_url": "https://neuralstrike.co/report.html"}) \
        == "https://neuralstrike.co/report.html"
    assert scalp.report_href({"report_url": "javascript:alert(1)"}) is None
    assert scalp.report_href({}) is None


def test_flow_rows_carry_a_central_time():
    ctx = {"flow": [{"ts": 1792420800, "symbol": "SPY", "type": "unusual_volume",
                     "side": "calls", "text": "SPY 500C <b>big</b>"}]}
    rows = scalp.flow_rows(ctx)
    assert rows[0]["symbol"] == "SPY" and rows[0]["text"] == "SPY 500C <b>big</b>"
    assert len(rows[0]["time"]) == 5 and rows[0]["time"][2] == ":"
    assert scalp.flow_rows({}) == []


# ── the page itself ─────────────────────────────────────────────────────────
def test_page_reads_only_scalp_view():
    """The only view named in the source is ``cache:options:scalp`` (bus_client
    spells it without the ``cache:`` prefix), and the page reads it - not the
    news feed or the flow alerts the service already folded into ``context``."""
    import re
    keys = {k.removeprefix("cache:") for k in re.findall(
        r"(?:cache:)?(?:options|sentiment|market|news|portfolio|trade|blog):[a-z_]+",
        SRC)}
    assert keys == {"options:scalp"}, keys
    assert scalp.VIEW == "options:scalp"
    assert "cache:options:scalp" in SRC


def test_third_party_text_never_reaches_ui_html():
    assert "ui.html" not in SRC
    assert ".style(" not in SRC and ":style=" not in SRC


def test_the_refresh_enqueues_scalp_refresh_on_the_options_stream():
    assert '"type": "scalp_refresh"' in SRC
    assert 'bus_client.request("options"' in SRC


def test_the_page_imports_no_engine_or_config_module():
    import re
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", SRC, flags=re.M)
    for name in imports:
        root = name.split(".")[0]
        assert root not in {"services", "scalp_config", "scalp_call"}, name
        assert "scalp_config" not in name and "sqlite3" not in name, name


# ── the scorecard ────────────────────────────────────────────────────────────
def _roles_card(**over):
    base = {"n": 10, "win": 4, "loss": 5, "timeout": 1, "win_rate": 0.4,
            "chain_n": 8, "chain_win_rate": 0.375}
    return {r: dict(base, **over.get(r, {})) for r in ROLES}


def _card(**extra):
    card = {"days": 10,
            "edge": {"days": 2, "wins": 1, "rate": 0.5, "p_base": 0.4125},
            "caution": {"days": 3, "wins": 1, "rate": 1 / 3, "p_base": 0.55},
            "roles": _roles_card()}
    card.update(extra)
    return card


def _scorecard(**symbols):
    return {"days": 10, "window": 20, "symbols": symbols or {"SPY": _card()}}


def test_edge_line_counts_highlighted_days_beside_the_usual_rate():
    line = scalp.edge_line(_card())
    assert "won 1 of 2 highlighted days" in line
    assert "(50.00%)" in line and "against 41.25% usually" in line


def test_edge_line_with_no_highlighted_day_says_so_instead_of_a_rate():
    line = scalp.edge_line(_card(edge={"days": 0, "wins": 0, "rate": None,
                                       "p_base": None}))
    assert line.startswith("Edge calls: none")
    assert "%" not in line and "0 of 0" not in line
    assert scalp.edge_line({}).startswith("Edge calls: none")


def test_caution_line_sets_the_win_rate_beside_the_usual_rate():
    line = scalp.caution_line(_card())
    assert "marked to avoid won 1 of 3 times" in line
    assert "(33.33%)" in line and "against 55.00% usually" in line
    assert scalp.caution_line({}).startswith("Cautions: none")


def test_scorecard_rows_keep_the_four_roles_in_order_with_counts_and_rates():
    rows = scalp.scorecard_rows(_card(roles=_roles_card(
        long_with={"chain_n": 0, "chain_win_rate": None})))
    assert [r["id"] for r in rows] == list(ROLES)
    first = rows[0]
    assert (first["n"], first["win"], first["loss"], first["timeout"]) == (
        "10", "4", "5", "1")
    assert first["rate"] == "40.00%"
    assert first["chain"] == fmt.NO_READING             # no chain grade: a dash, not 0%
    assert rows[1]["chain"] == "37.50% of 8"
    assert "with the open" in rows[0]["role"] and "against the open" in rows[1]["role"]


def test_scorecard_symbols_and_heading_tolerate_the_empty_card():
    assert scalp.scorecard_symbols({}) == [] and scalp.scorecard_symbols(None) == []
    assert scalp.scorecard_symbols({"symbols": {"SPY": "junk"}}) == []
    got = scalp.scorecard_symbols(_scorecard(SPY=_card(), QQQ=_card()))
    assert [n for n, _ in got] == ["SPY", "QQQ"]
    assert "10 days graded" in scalp.scorecard_heading(_scorecard())
    assert "20 days" in scalp.scorecard_heading(_scorecard())


def test_the_panel_keeps_the_empty_line_and_uses_the_kit_table():
    assert "SCORECARD_EMPTY" in SRC and "No graded days yet." in SRC
    assert "kit.table(scorecard_columns()" in SRC


# ── the intraday calls (every 15 minutes) ────────────────────────────────────
# The payload is ``view["intraday"]`` (services/options_svc/scalp_intraday_call):
# ``{"day", "slot", "latest": {symbol: entry}, "timeline": [...], "next_slot"}``.
# Its rates are over EVERY stored day, its sample counts are DAYS (``n_days``),
# and the confirmation word is recorded to be tested - not in the probability.
def _irole(structure, p=0.5, base=0.5, *, n_days=30, n_base_days=90, edge=False,
           strikes=(504.0,), entry=2.95, win=0.74, loss=-1.07, walls=()):
    return {"structure": structure, "p": p, "lo": max(0.0, p - 0.15),
            "hi": min(1.0, p + 0.15), "p_base": base, "edge": edge,
            "strikes": list(strikes), "entry_value": entry, "entry_source": "chain",
            "win_points": win, "loss_points": loss, "n_days": n_days,
            "n_base_days": n_base_days, "wall_context": list(walls)}


def _conf(word="Mixed", *, flow=None, netprem=None, dealer=-1, sentiment=1,
          stale=(), dealer_line="The call wall at 503.75 (0.05%) is inside the target"):
    def comp(name, verdict):
        return {"verdict": verdict, "raw": {"state": "ok"}, "stale": name in stale}
    return {"flow": comp("flow", flow), "netprem": comp("netprem", netprem),
            "dealer": comp("dealer", dealer), "sentiment": comp("sentiment", sentiment),
            "score": -0.3333333, "word": word, "dealer_line": dealer_line,
            "truncated": False}


def _ientry(direction=1, *, highlight=None, avoid=(), word="Mixed", walls=(), **extra):
    up = direction == 1
    structures = {"long_with": "long_call" if up else "long_put",
                  "long_against": "long_put" if up else "long_call",
                  "credit_with": "pcs" if up else "ccs",
                  "credit_against": "ccs" if up else "pcs"}
    roles = {r: _irole(s, walls=walls) for r, s in structures.items()}
    entry = {"slot": "10:21", "block": "midday", "direction": direction,
             "bucket": "high", "alignment": 1.0, "entry_spot": 503.5,
             "inputs": {}, "agree": {}, "rates_basis": "all stored days",
             "chain": {"spot": 503.5, "atm_iv": 0.18, "expiry": "2026-10-19:0"},
             "roles": roles, "highlight": highlight, "avoid": list(avoid),
             "note": None if highlight else "no measured edge", "points_note": None,
             "confirmation": _conf(word), "dealer_line": _conf(word)["dealer_line"],
             "made_at": "2026-10-19T10:21:20-05:00", "inputs_failed": [],
             "inputs_note": None}
    entry.update(extra)
    return entry


def _cell(direction=1, highlight=None, word="Mixed", edge_p=None, base=None,
          note="no measured edge"):
    return {"direction": direction, "highlight": highlight, "word": word,
            "edge_p": edge_p, "edge_p_base": base, "note": note}


def _section(**over):
    sec = {"day": "2026-10-19", "slot": "10:21",
           "latest": {"SPY": _ientry(1)},
           "timeline": [
               {"slot": "09:06", "symbols": {"SPY": _cell(1), "QQQ": _cell(None, word=None,
                    note="flat over the last 15 minutes — no direction")}},
               {"slot": "09:21", "symbols": {"SPY": _cell(-1, "long_with", "Confirms",
                                                            0.63, 0.5, note=None)}}],
           "next_slot": "2026-10-19T10:36:00-05:00"}
    sec.update(over)
    return sec


def test_latest_call_names_contracts_by_direction():
    up = _by_role(scalp.intraday_role_rows(_ientry(1)))
    assert [up[r]["trade"] for r in ROLES] == [
        "Long call", "Long put", "Put credit spread", "Call credit spread"]
    down = _by_role(scalp.intraday_role_rows(_ientry(-1)))
    assert [down[r]["trade"] for r in ROLES] == [
        "Long put", "Long call", "Call credit spread", "Put credit spread"]
    assert [r["id"] for r in scalp.intraday_role_rows(_ientry(1))] == list(ROLES)
    assert up["long_with"]["side"] == "with the last 15 minutes' move"
    assert up["long_against"]["side"] == "against the last 15 minutes' move"
    assert scalp.intraday_role_rows({"direction": None, "roles": {}}) == []
    assert scalp.intraday_role_rows(None) == []


def test_direction_is_said_in_words_over_the_last_15_minutes():
    assert scalp.move_words(_ientry(1)) == "moving up over the last 15 minutes"
    assert scalp.move_words(_ientry(-1)) == "moving down over the last 15 minutes"
    flat = _ientry(None, roles={}, note="flat over the last 15 minutes — no direction")
    assert scalp.move_words(flat) == "flat over the last 15 minutes — no call"
    nobars = _ientry(None, roles={}, note="no bars for the last 15 minutes yet")
    assert "no bars" in scalp.move_words(nobars)


def test_the_headline_is_no_edge_with_the_base_rates_unless_a_highlight():
    assert scalp.NO_EDGE.lower() in scalp.intraday_headline(_ientry(1)).lower()
    sym = _ientry(1, avoid=["credit_with"])
    assert "no measured edge" in scalp.intraday_headline(sym).lower()
    rows = _by_role(scalp.intraday_role_rows(sym))
    assert rows["credit_with"]["tone"] == "caution"
    assert rows["credit_with"]["mark"] == scalp.CAUTION
    assert all(r["usual"] == "50.00%" for r in rows.values())
    hl = _ientry(1, highlight="long_with")
    hl["roles"]["long_with"].update(p=0.63, lo=0.45, hi=0.78, p_base=0.5, n_days=30)
    text = scalp.intraday_headline(hl)
    assert text.startswith("Measured edge") and "Long call" in text
    assert "63.00%" in text and "50.00%" in text and "30 days" in text
    rows = _by_role(scalp.intraday_role_rows(hl))
    assert rows["long_with"]["mark"] == scalp.EDGE and rows["long_with"]["tone"] == "edge"
    assert rows["long_against"]["mark"] == ""


def test_a_headline_for_a_symbol_with_no_call_is_the_services_note():
    flat = _ientry(None, roles={}, note="flat over the last 15 minutes — no direction")
    assert scalp.intraday_headline(flat).startswith("Flat over the last 15 minutes")
    missed = _ientry(1, roles={}, note="missed the call window")
    assert scalp.intraday_headline(missed) == "Missed the call window"


def test_sample_label_says_days():
    row = _by_role(scalp.intraday_role_rows(_ientry(1)))["long_with"]
    assert row["days"] == "30 days" and row["days_base"] == "90 days"
    assert row["samples"] == "30 / 90 days"
    one = _ientry(1)
    one["roles"]["long_with"].update(n_days=1)
    assert _by_role(scalp.intraday_role_rows(one))["long_with"]["days"] == "1 day"
    assert "n_days" not in {c["field"] for c in scalp.intraday_role_columns()}
    cols = " ".join(c["label"] for c in scalp.intraday_role_columns())
    assert "Days" in cols and "Samples" not in cols
    assert row["interval"] == "35.00% – 65.00%"
    missing = _ientry(1)
    del missing["roles"]["long_with"]["n_days"]
    assert _by_role(scalp.intraday_role_rows(missing))["long_with"]["days"] == fmt.NO_READING


def test_the_rates_basis_is_said_in_plain_words():
    text = scalp.RATES_BASIS_TEXT
    assert "every stored day" in text and "walk-forward" in text
    assert scalp.rates_basis_line(_ientry(1)) == text
    assert scalp.rates_basis_line(_ientry(1, roles={})) == ""


def test_dealer_line_and_wall_context_shown():
    sym = _ientry(1, walls=["Target is beyond the call wall at 503.75"])
    assert scalp.dealer_line(sym) == "The call wall at 503.75 (0.05%) is inside the target"
    rows = _by_role(scalp.intraday_role_rows(sym))
    assert rows["long_with"]["walls"] == "Target is beyond the call wall at 503.75"
    two = _ientry(1, walls=["Target is beyond the call wall at 503.75",
                            "Stop is beyond the put wall at 493.43"])
    assert scalp.intraday_role_rows(two)[0]["walls"].count("wall") == 2
    assert _by_role(scalp.intraday_role_rows(_ientry(1)))["long_with"]["walls"] == ""
    assert scalp.dealer_line(_ientry(1, dealer_line=None, confirmation={})) == ""
    # the confirmation's own copy is the fallback
    assert scalp.dealer_line(_ientry(1, dealer_line=None)) == _conf()["dealer_line"]


def test_inputs_failed_and_note_are_shown_when_present():
    ok = _ientry(1)
    assert scalp.inputs_line(ok) == ""
    sym = _ientry(1, inputs_failed=["XLV", "/NQ"],
                  inputs_note="built on fewer inputs: no fresh bars for /NQ, XLV")
    assert scalp.inputs_line(sym) == "Built on fewer inputs: no fresh bars for /NQ, XLV"
    only_list = _ientry(1, inputs_failed=["XLV"], inputs_note=None)
    assert "XLV" in scalp.inputs_line(only_list)


def test_notes_are_shown_but_the_headline_is_not_repeated():
    missed = _ientry(1, roles={}, note="missed the call window",
                     points_note="no usable volatility: no strikes or points")
    notes = scalp.intraday_notes(missed, scalp.intraday_headline(missed))
    assert notes == ["No usable volatility: no strikes or points"]
    assert scalp.intraday_notes(_ientry(1), scalp.intraday_headline(_ientry(1))) == []


# ── the confirmation: a word, never a probability ───────────────────────────
def test_confirmation_never_shown_as_a_probability():
    sym = _ientry(1, word="Confirms")
    assert scalp.confirmation_word(sym) == "Confirms"
    assert "Not in the probability" in scalp.CONFIRMATION_TITLE
    assert scalp.CONFIRMATION_TITLE == \
        "Not in the probability — being recorded to be tested"
    rows = scalp.confirmation_rows(sym)
    assert [r["id"] for r in rows] == ["flow", "netprem", "dealer", "sentiment"]
    texts = [r[k] for r in rows for k in ("component", "verdict")]
    texts += [scalp.confirmation_word(sym), scalp.CONFIRMATION_NOTE]
    assert not any("%" in t for t in texts)
    assert not any(ch.isdigit() for t in texts for ch in t), texts   # no score at all
    assert "-0.33" not in " ".join(texts)
    for word in ("Confirms", "Mixed", "Contradicts"):
        assert scalp.confirmation_word(_ientry(1, word=word)) == word
        assert word in {"Confirms", "Mixed", "Contradicts"}
    # the page never prints the score or draws a gauge from it
    assert 'get("score")' not in SRC and "['score']" not in SRC


def test_each_component_is_a_whole_word_and_stale_is_said():
    conf = _conf("Mixed", flow=1, netprem=-1, dealer=0, sentiment=None, stale=("netprem",))
    conf["netprem"]["verdict"] = None
    rows = _by_role(scalp.confirmation_rows(_ientry(1, confirmation=conf)))
    assert rows["flow"]["verdict"] == "Agrees"
    assert rows["dealer"]["verdict"] == "No side"
    assert rows["sentiment"]["verdict"] == "No reading"
    assert rows["netprem"]["verdict"] == "No reading · stale"
    assert rows["netprem"]["stale"] is True and rows["flow"]["stale"] is False
    disagrees = _by_role(scalp.confirmation_rows(_ientry(1, confirmation=_conf(dealer=-1))))
    assert disagrees["dealer"]["verdict"] == "Disagrees"


def test_a_missing_confirmation_reads_no_reading():
    assert scalp.confirmation_rows(_ientry(1, confirmation={})) == []
    assert scalp.confirmation_word(_ientry(1, confirmation={})) == "No reading"
    nodir = _ientry(None, roles={}, confirmation=_conf(word=None))
    assert scalp.confirmation_word(nodir) == "No reading"


def test_a_truncated_flow_window_is_said():
    conf = _conf()
    conf["truncated"] = True
    assert "cut short" in scalp.confirmation_flags(_ientry(1, confirmation=conf))[0]
    assert scalp.confirmation_flags(_ientry(1)) == []


# ── which day, and is the call still current ────────────────────────────────
def test_the_latest_call_line_names_its_slot_and_the_next():
    status = scalp.intraday_status(_section(), _at(2026, 10, 19, 10, 25))
    assert status["today"] is True and status["past"] is False
    assert status["line"] == "Latest call at 10:21 · next at 10:36"
    assert status["title"] == "Today's calls"


def test_a_past_slot_is_labelled_past():
    status = scalp.intraday_status(_section(), _at(2026, 10, 19, 10, 40))
    assert status["past"] is True
    assert status["line"] == "Last call at 10:21 · next at 10:36"
    assert "newer" in status["warning"].lower()
    label = scalp.latest_label(_section(), _at(2026, 10, 19, 10, 40))
    assert label.startswith("Last call at 10:21") and "past" in label
    current = scalp.latest_label(_section(), _at(2026, 10, 19, 10, 25))
    assert current == "Latest call at 10:21"


def test_a_stale_day_section_is_labelled_as_that_days_and_never_as_current():
    sec = _section()
    status = scalp.intraday_status(sec, _at(2026, 10, 20, 9, 0))
    assert status["today"] is False
    assert status["title"] == "Calls of 2026-10-19 — not today's"
    assert "not today's" in status["line"] and "2026-10-19" in status["line"]
    assert "Latest call" not in status["line"]
    label = scalp.latest_label(sec, _at(2026, 10, 20, 9, 0))
    assert "2026-10-19" in label and "not today's" in label
    assert "Latest call" not in label
    # the same payload on its own day is today's
    assert scalp.intraday_status(sec, _at(2026, 10, 19, 10, 22))["title"] == "Today's calls"
    # no day at all is never "today"
    assert scalp.intraday_status({"slot": "09:06"}, _at(2026, 10, 19, 10))["today"] is False


def test_a_stale_day_names_the_next_call_only_while_it_is_ahead():
    sec = _section(next_slot="2026-10-20T08:51:00-05:00")
    early = scalp.intraday_status(sec, _at(2026, 10, 20, 7, 0))
    assert "next at" in early["line"] and "08:51" in early["line"]
    late = scalp.intraday_status(sec, _at(2026, 10, 20, 9, 30))
    assert "next at" not in late["line"]


def test_a_missing_section_is_empty():
    assert scalp.intraday_section({}) == {}
    assert scalp.intraday_section({"intraday": "junk"}) == {}
    assert scalp.latest_entries(_section()) == [("SPY", _section()["latest"]["SPY"])]
    assert scalp.latest_entries({"latest": {"SPY": "junk"}}) == []


# ── today's calls ────────────────────────────────────────────────────────────
def test_timeline_rows():
    sec = _section()
    assert scalp.timeline_symbols(sec) == ["SPY", "QQQ"]
    cols = scalp.timeline_columns(sec)
    assert [c["label"] for c in cols] == ["Time (CT)", "SPY", "QQQ"]
    rows = scalp.timeline_rows(sec)
    assert [r["time"] for r in rows] == ["09:06", "09:21"]
    first, second = rows
    assert first["s0"] == "↑ Up" and first["s0_pick"] == "—"
    assert first["s0_word"] == "Mixed"
    assert first["s1"] == "— No direction" and first["s1_word"] == fmt.NO_READING
    assert "flat over the last 15 minutes" in first["s1_note"]
    assert second["s0"] == "↓ Down"
    assert second["s0_pick"] == "Long put · 63.00% vs 50.00% usually"
    assert second["s0_word"] == "Confirms"
    # a symbol not in a slot is dashes, not a crash
    assert second["s1"] == "—" and second["s1_pick"] == "—"
    # the usual no-edge note is the dash, not repeated on every row
    assert first["s0_note"] == ""


def test_timeline_survives_junk():
    assert scalp.timeline_rows({}) == [] and scalp.timeline_rows(None) == []
    assert scalp.timeline_rows({"timeline": ["x", {"slot": "09:06", "symbols": None}]}) == [
        {"id": "09:06", "time": "09:06"}]
    assert scalp.timeline_symbols({"timeline": "x"}) == []


def test_the_timeline_says_what_its_rates_are_over():
    assert "every stored day" in scalp.RATES_BASIS_TEXT


# ── the intraday scorecard ──────────────────────────────────────────────────
def _iblock(days=3, **over):
    block = {"days": days,
             "edge": {"days": 2, "rows": 5, "wins": 3, "rate": 0.6, "p_base": 0.5},
             "caution": {"days": 1, "rows": 1, "wins": 0, "rate": 0.0, "p_base": 0.45},
             "by_word": {
                 "Confirms": {"days": 2, "rows": 3, "wins": 2, "win_rate": 2 / 3},
                 "Mixed": {"days": 2, "rows": 2, "wins": 1, "win_rate": 0.5},
                 "Contradicts": {"days": 1, "rows": 1, "wins": 0, "win_rate": 0.0}}}
    block.update(over)
    return block


def _iscore(**blocks):
    return {"intraday": {"days": 3, "window": 20, "symbols": {
        "SPY": {"days": 3, "blocks": blocks or {"morning": _iblock()}}}}}


def test_the_intraday_scorecard_counts_days_not_rows():
    line = scalp.intraday_edge_line(_iblock())
    assert "3 of 5 calls" in line and "2 days" in line
    assert "60.00%" in line and "against 50.00% usually" in line
    assert "5 days" not in line and "3 days" not in line
    caution = scalp.intraday_caution_line(_iblock())
    assert "0 of 1 call" in caution and "1 day" in caution
    assert "0.00%" in caution and "against 45.00% usually" in caution
    assert "1 days" not in caution


def test_the_intraday_scorecard_edge_with_no_highlighted_day_says_so():
    line = scalp.intraday_edge_line(_iblock(edge={"days": 0, "rows": 0, "wins": 0,
                                                  "rate": None, "p_base": None}))
    assert line.startswith("Edge calls: none") and "%" not in line
    assert scalp.intraday_edge_line({}).startswith("Edge calls: none")
    assert scalp.intraday_caution_line({}).startswith("Cautions: none")


def test_the_scorecard_win_rates_by_word_carry_their_days():
    rows = scalp.intraday_word_rows(_iblock())
    assert [r["word"] for r in rows] == ["Confirms", "Mixed", "Contradicts"]
    first = rows[0]
    assert first["rate"] == "66.67%" and first["days"] == "2 days"
    assert first["calls"] == "2 of 3"
    assert rows[2]["days"] == "1 day" and rows[2]["rate"] == "0.00%"
    empty = scalp.intraday_word_rows(_iblock(by_word={
        "Confirms": {"days": 0, "rows": 0, "wins": 0, "win_rate": None}}))
    assert empty[0]["rate"] == fmt.NO_READING and empty[0]["days"] == "0 days"
    assert empty[0]["calls"] == fmt.NO_READING
    assert [r["word"] for r in empty] == ["Confirms", "Mixed", "Contradicts"]


def test_the_intraday_scorecard_blocks_in_the_services_order():
    card = _iscore(morning=_iblock(), midday=_iblock(days=1))
    got = scalp.intraday_scorecard_blocks(card)
    assert [(s, [b["block"] for b in blocks]) for s, _d, blocks in got] == [
        ("SPY", ["morning", "midday"])]
    assert got[0][1] == 3
    assert [b["label"] for b in got[0][2]] == ["Morning", "Midday"]
    assert scalp.intraday_scorecard_blocks({}) == []
    assert scalp.intraday_scorecard_blocks({"intraday": {"symbols": {"SPY": "x"}}}) == []
    assert scalp.intraday_scorecard_blocks({"intraday": {}}) == []


def test_the_intraday_scorecard_heading_and_basis_say_days():
    head = scalp.intraday_scorecard_heading(_iscore())
    assert "3 days graded" in head and "20 days" in head
    assert "distinct days" in scalp.INTRADAY_SCORECARD_NOTE
    assert "every stored day" in scalp.RATES_BASIS_TEXT


def test_the_08_36_scorecard_helpers_are_unchanged_by_the_intraday_section():
    card = dict(_scorecard(), intraday=_iscore()["intraday"])
    assert [n for n, _ in scalp.scorecard_symbols(card)] == ["SPY"]
    assert scalp.scorecard_symbols(_iscore()) == []         # intraday-only: no 08:36 cards
    assert scalp.has_scorecard(_iscore()) and scalp.has_scorecard(_scorecard())
    assert not scalp.has_scorecard({}) and not scalp.has_scorecard(None)


# ── the page ─────────────────────────────────────────────────────────────────
def test_the_page_help_explains_the_intraday_calls():
    import page_help
    text = page_help.HELP_MD["/options/scalp"]
    for needle in ("08:51", "13:51", "last 15 minutes", "distinct days",
                   "every stored day", "walk-forward", "not in the probability",
                   "being recorded to be tested", "dealer read", "Last call"):
        assert needle in text, needle


def test_the_intraday_panels_use_the_kit_and_still_read_one_view():
    assert "kit.table(intraday_role_columns()" in SRC
    assert "kit.table(timeline_columns(" in SRC
    assert "CONFIRMATION_TITLE" in SRC
    assert scalp.VIEW == "options:scalp"
    assert "ui.html" not in SRC and ".style(" not in SRC


# ── fix round 1: the clock, the trade window, and partial payloads ──────────
def _traded(section, entry_at, stop=60):
    """``section`` whose latest entries were made at ``entry_at`` with a frozen time stop."""
    ts = int(entry_at.timestamp())
    for entry in section["latest"].values():
        entry["entry_ts"] = ts
        entry["rules"] = {"time_stop_min": stop}
    return section


def test_a_call_is_current_until_its_next_slot_or_its_trade_ends():
    sec = _traded(_section(), _at(2026, 10, 19, 10, 20))        # trade over at 11:20
    assert scalp.intraday_status(sec, _at(2026, 10, 19, 10, 30))["past"] is False
    late = scalp.intraday_status(sec, _at(2026, 10, 19, 10, 40))     # 10:36 has passed
    assert late["past"] is True and late["late"] is True and late["over"] is False
    assert "newer slot" in late["warning"]
    assert scalp.latest_label(sec, _at(2026, 10, 19, 10, 40)).endswith("past")
    assert scalp.latest_label(sec, _at(2026, 10, 19, 10, 30)) == "Latest call at 10:21"


def test_the_final_call_of_the_day_is_over_when_its_hour_is_up_not_all_evening():
    sec = _traded(_section(slot="13:51", next_slot="2026-10-20T08:51:00-05:00"),
                  _at(2026, 10, 19, 13, 50))                     # trade over at 14:50
    during = scalp.intraday_status(sec, _at(2026, 10, 19, 14, 0))
    assert during["past"] is False and during["final"] is True
    assert during["line"] == "Final call of the day at 13:51 · next: Tue 2026-10-20 08:51"
    after = scalp.intraday_status(sec, _at(2026, 10, 19, 15, 0))
    assert after["past"] is True and after["over"] is True
    assert after["line"].startswith("Last call at 13:51 · over")
    assert "final call of the day" in after["line"]
    assert "next: Tue 2026-10-20 08:51" in after["line"]
    assert "newer slot" not in after["warning"] and "over" in after["warning"]
    assert scalp.latest_label(sec, _at(2026, 10, 19, 15, 0)) == "Last call at 13:51 · over"
    assert scalp.latest_label(sec, _at(2026, 10, 19, 14, 0)) == "Latest call at 13:51"


def test_a_final_slot_of_no_trades_is_over_when_its_hour_is_up():
    # Changed by the final review's M8 (this test used to pin that such a slot
    # was never over): every latest entry flat or short of bars carries no
    # rules, so the slot's own entry (13:51 -> the 13:50 bar) plus the section's
    # time stop (else 60) ends it.
    flat = _ientry(None, roles={}, note="flat over the last 15 minutes — no direction")
    sec = _section(slot="13:51", next_slot="2026-10-20T08:51:00-05:00",
                   latest={"SPY": flat, "QQQ": dict(flat)})
    assert scalp.intraday_status(sec, _at(2026, 10, 19, 14, 0))["over"] is False
    after = scalp.intraday_status(sec, _at(2026, 10, 19, 14, 50))
    assert after["over"] is True and after["past"] is True
    assert after["line"].startswith("Last call at 13:51 · over")
    assert scalp.latest_label(sec, _at(2026, 10, 19, 20, 0)) == "Last call at 13:51 · over"
    # the time stop the section carries wins over the default
    longer = dict(sec, time_stop_min=90)
    assert scalp.intraday_status(longer, _at(2026, 10, 19, 15, 0))["over"] is False
    assert scalp.intraday_status(longer, _at(2026, 10, 19, 15, 20))["over"] is True
    for bad in (0, -5, "x", None):
        odd = dict(sec, time_stop_min=bad)
        assert scalp.intraday_status(odd, _at(2026, 10, 19, 14, 50))["over"] is True, bad
    # with neither a day nor a slot nothing can be said to be over
    assert scalp._trade_end({"latest": {"SPY": flat}}) is None
    assert scalp._trade_end({"day": "2026-10-19", "slot": "junk"}) is None


def test_the_clock_alone_moves_the_page_from_current_to_past():
    """The paint is pure over the cached view: same view, later clock."""
    view = {"state": "call", "day": "2026-10-19", "call_day": "2026-10-19",
            "symbols": {"SPY": _symbol(1)}, "intraday": _section()}
    early, late = _at(2026, 10, 19, 10, 25), _at(2026, 10, 19, 10, 40)
    assert scalp.clock_signature(view, early) == scalp.clock_signature(view, early)
    assert scalp.clock_signature(view, early) != scalp.clock_signature(view, late)
    assert scalp.intraday_status(view["intraday"], early)["line"] == \
        "Latest call at 10:21 · next at 10:36"
    assert scalp.intraday_status(view["intraday"], late)["line"] == \
        "Last call at 10:21 · next at 10:36"
    # the day rolling is a clock change too, and so is the 08:36 line
    assert scalp.clock_signature(view, late) != scalp.clock_signature(view, _at(2026, 10, 20, 9))
    assert scalp.clock_signature(view, _at(2026, 10, 19, 10, 25)) == \
        scalp.clock_signature(view, _at(2026, 10, 19, 10, 26))      # nothing moved: no repaint
    assert scalp.clock_signature({}, early)[1] is None


def test_the_render_keeps_the_view_and_repaints_on_a_guarded_timer():
    body = SRC[SRC.index("def render():"):]
    assert "ui.timer(CLOCK_TICK_SEC, _tick)" in body
    assert scalp.CLOCK_TICK_SEC == 30
    tick = body[body.index("@guard\n    def _tick"):]
    assert "clock_signature(state[\"view\"]" in tick and "_paint(state[\"view\"])" in tick
    assert 'state["view"], state["sig"] = view' in body


def test_a_timeline_edge_rate_carries_its_days():
    sec = _section()
    sec["timeline"][1]["symbols"]["SPY"]["n_days"] = 34
    row = scalp.timeline_rows(sec)[1]
    assert row["s0_pick"] == "Long put · 63.00% vs 50.00% usually · 34 days"
    sec["timeline"][1]["symbols"]["SPY"]["n_days"] = 1
    assert scalp.timeline_rows(sec)[1]["s0_pick"].endswith("· 1 day")
    note = scalp.TIMELINE_NOTE
    assert "not in the probability" in note and "every stored day" in note
    assert "walk-forward" in note and "TIMELINE_NOTE" in SRC


def test_a_call_that_is_not_current_is_drawn_without_edge_colour():
    sym = _ientry(1, highlight="long_with", avoid=["credit_with"])
    live = _by_role(scalp.intraday_role_rows(sym))
    old = _by_role(scalp.intraday_role_rows(sym, muted=True))
    assert live["long_with"]["_row_class"].startswith("bg-[")
    assert old["long_with"]["_row_class"] == "" and old["credit_with"]["_row_class"] == ""
    from pages.options import theme
    for row in (old["long_with"], old["credit_with"]):
        for cls in (row["_rate_class"], row["_mark_class"]):
            assert theme.TXT_POS not in cls and theme.TXT_WARN not in cls
            assert theme.BADGE_POS not in cls and theme.BADGE_WARN not in cls
    assert old["long_with"]["_rate_class"] == theme.MUTED
    # the words and the marks stay: only the emphasis goes
    assert old["long_with"]["mark"] == scalp.EDGE and old["long_with"]["tone"] == "edge"
    assert old["credit_with"]["mark"] == scalp.CAUTION


def test_the_confirmation_names_its_day_when_it_is_not_today():
    assert scalp.confirmation_lead(_section(), _at(2026, 10, 19, 10, 25)) == ""
    lead = scalp.confirmation_lead(_section(), _at(2026, 10, 20, 9))
    assert "2026-10-19" in lead and "not today's" in lead
    assert scalp.confirmation_columns()[1]["label"] == "Against the last 15 minutes' move"


# ── the panel builders on partial payloads (M7) ─────────────────────────────
class _Rec:
    """Stands in for a NiceGUI element: records what the builders draw."""
    def __init__(self, log, kind, text=""):
        self.log, self.kind, self.text = log, kind, text
        log.append((kind, text))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def classes(self, *a, **k):
        return self

    def props(self, *a, **k):
        return self

    def add_slot(self, *a, **k):
        return self


@pytest.fixture
def drawn(monkeypatch):
    from nicegui import ui

    from pages import ui_kit as kit
    log = []
    for kind in ("column", "row", "space"):
        monkeypatch.setattr(ui, kind, lambda *a, _k=kind, **k: _Rec(log, _k))
    monkeypatch.setattr(ui, "expansion",
                        lambda text="", *a, **k: _Rec(log, "expansion", text))
    monkeypatch.setattr(ui, "label", lambda text="", *a, **k: _Rec(log, "label", text))
    monkeypatch.setattr(kit, "section_title", lambda text: _Rec(log, "title", text))
    monkeypatch.setattr(kit, "table",
                        lambda cols, rows=None, **k: _Rec(log, "table", len(list(rows or []))))
    return log


def _texts(log):
    return [t for kind, t in log if kind in ("label", "title", "expansion")]


def _folds(log):
    return [t for kind, t in log if kind == "expansion"]


@pytest.mark.parametrize("view", [
    {}, {"intraday": {}}, {"intraday": "junk"}, {"intraday": {"day": "2026-10-19"}},
    {"intraday": {"day": "2026-10-19", "slot": "09:06", "latest": {"SPY": {}}}},
    {"intraday": {"day": "2026-10-19", "slot": "09:06", "timeline": "x",
                  "latest": {"SPY": "junk", "QQQ": {"direction": None, "roles": {}}}}},
    {"symbols": {"SPY": "junk", "QQQ": {"direction": True, "roles": "x"}},
     "intraday": {"day": "2026-10-19", "slot": "09:06", "latest": {"QQQ": []}}},
])
def test_the_glance_and_the_details_never_raise_on_a_partial_payload(drawn, view):
    scalp._glance(view, _at(2026, 10, 19, 9, 10))
    scalp._details(view, _at(2026, 10, 19, 9, 10))
    scalp.glance_rows(view, _at(2026, 10, 19, 9, 10))


def test_an_empty_view_draws_no_glance_and_no_symbol_section(drawn):
    assert scalp._glance({}, _at(2026, 10, 19, 9, 10)) is None
    assert scalp._glance({"intraday": {}}, _at(2026, 10, 19, 9, 10)) is None
    assert drawn == []
    scalp._details({}, _at(2026, 10, 19, 9, 10))
    scalp._details({"intraday": {}}, _at(2026, 10, 19, 9, 10))
    folds = _folds(drawn)
    assert not any(f.endswith("details") for f in folds)
    assert "Today's calls" not in folds
    assert folds.count("Scorecard") == 2


def test_no_timeline_and_no_symbol_say_so_in_words(drawn):
    scalp._details({"intraday": {"day": "2026-10-19", "slot": "09:06"}},
                   _at(2026, 10, 19, 9, 10))
    texts = _texts(drawn)
    assert "No calls published yet." in texts and "Today's calls" in texts
    assert not any(t.startswith("Confirmation ·") for t in texts)
    assert not any(kind == "table" for kind, _ in drawn)


def test_a_symbol_with_no_roles_draws_its_note_and_no_table(drawn):
    flat = _ientry(None, roles={}, note="flat over the last 15 minutes — no direction",
                   confirmation={}, dealer_line=None)
    scalp._details({"intraday": _section(latest={"$SPX": flat})},
                   _at(2026, 10, 19, 10, 25))
    texts = _texts(drawn)
    assert "Flat over the last 15 minutes — no direction" in texts
    assert "$SPX details" in _folds(drawn)
    assert not any(t.startswith("Confirmation ·") for t in texts)
    assert not any(t.startswith("Dealer read") for t in texts)
    # the timeline still has its table; the flat card has none of its own
    assert [t for k, t in drawn if k == "table"] == [2]


def test_a_stale_day_draws_neutral_labels_and_the_confirmation_names_the_day(drawn):
    scalp._details({"intraday": _section()}, _at(2026, 10, 20, 9, 0))
    texts = _texts(drawn)
    assert "Calls of 2026-10-19 — not today's" in _folds(drawn)
    assert "Latest call" not in texts
    assert any(t.startswith("From the calls of 2026-10-19") for t in texts)


def test_the_scorecard_panel_handles_partial_scorecards(drawn):
    scalp._scorecard_panel({})
    scalp._scorecard_panel({"intraday": {}})
    assert _texts(drawn).count(scalp.SCORECARD_EMPTY) == 2
    scalp._scorecard_panel(_iscore())
    assert any(t.startswith("Edge calls") for t in _texts(drawn))


def test_a_non_string_highlight_does_not_raise():
    rows = scalp.intraday_role_rows(_ientry(1, highlight=["long_with"]))
    assert [r["tone"] for r in rows] == ["plain"] * 4
    assert "highlight in {r" in SRC and "isinstance(highlight, str)" in SRC



# ── final review: sources count, a muted old timeline, the 08:36 card ────────
def test_the_word_says_how_many_sources_it_rests_on():
    conf = dict(_conf("Mixed"), n_present=3, n_sources=4, word_note=None)
    sym = _ientry(1, confirmation=conf)
    assert scalp.confirmation_sources(sym) == "3 of 4 sources"
    assert scalp.confirmation_summary(sym) == "Mixed · 3 of 4 sources"
    few = dict(_conf(None), n_present=1, n_sources=4, word_note="too few sources")
    one = _ientry(1, confirmation=few)
    assert scalp.confirmation_word(one) == "No reading"
    assert scalp.confirmation_summary(one) == "No reading · 1 of 4 sources (too few sources)"
    # an entry from before the count: the word alone
    assert scalp.confirmation_summary(_ientry(1, word="Confirms")) == "Confirms"
    assert scalp.confirmation_sources(_ientry(1, confirmation={})) == ""


def test_the_latest_card_and_the_confirmation_show_the_sources_count(drawn):
    conf = dict(_conf("Confirms"), n_present=2, n_sources=4, word_note=None)
    sec = _section(latest={"SPY": _ientry(1, confirmation=conf)})
    scalp._details({"intraday": sec}, _at(2026, 10, 19, 10, 25))
    texts = _texts(drawn)
    assert "Confirmation · Confirms · 2 of 4 sources" in texts
    assert scalp.CONFIRMATION_TITLE in texts
    assert any(t.startswith("Confirmation: Confirms · 2 of 4 sources") for t in texts)


def test_another_days_timeline_draws_no_edge_colour(drawn, monkeypatch):
    from pages import ui_kit as kit
    from pages.options import theme
    seen = []
    monkeypatch.setattr(kit, "table", lambda cols, rows=None, **k: (
        seen.append(list(rows or [])), _Rec(drawn, "table", len(list(rows or []))))[1])
    sec = _section()
    scalp._details({"intraday": sec}, _at(2026, 10, 19, 10, 25))      # today
    today = next(rows for rows in seen if rows and "s0_pick" in rows[0])
    assert theme.TXT_POS in today[1]["s0_pick_class"]
    seen.clear()
    scalp._details({"intraday": sec}, _at(2026, 10, 20, 9, 0))        # next day
    old = next(rows for rows in seen if rows and "s0_pick" in rows[0])
    assert old[1]["s0_pick"] == "Long put · 63.00% vs 50.00% usually"     # words kept
    assert theme.TXT_POS not in old[1]["s0_pick_class"]
    assert old[1]["s0_pick_class"] == theme.MUTED
    assert scalp.timeline_rows(sec, muted=True)[1]["s0_pick_class"] == theme.MUTED


def test_the_0836_card_survives_a_list_valued_highlight(drawn):
    sym = _symbol(1)
    sym["highlight"] = ["long_with"]
    scalp._symbol_card({"state": "call", "day": "2026-10-19"}, "SPY", sym,
                       _at(2026, 10, 19, 8, 40))
    assert "SPY" in _texts(drawn)
    body = SRC[SRC.index("def _symbol_card"):SRC.index("def _latest_card")]
    assert "isinstance(highlight, str)" in body


def test_the_first_paint_is_guarded_like_the_timer():
    body = SRC[SRC.index("def render():"):]
    assert "@guard\n    def _paint(view):" in body


# ── the glance: one row per symbol at the top, the details below ────────────
def _glance_view(**over):
    view = {"state": "call", "day": "2026-10-19", "call_day": "2026-10-19",
            "symbols": {"SPY": _symbol(-1)}, "intraday": _section()}
    view.update(over)
    return view


def test_the_glance_row_reads_the_latest_15_minute_call():
    from pages.options import theme
    row, = scalp.glance_rows(_glance_view(), _at(2026, 10, 19, 10, 25))
    assert row["symbol"] == "SPY" and row["price"] == fmt.price(503.5)
    assert row["now"] == "↑ Up" and row["when"] == "10:21 call"
    assert row["_now_class"] == theme.TXT_POS
    assert row["word"] == "Mixed" and row["_word_class"] == theme.BADGE_WARN
    assert row["trade"] == f"Long call {fmt.strike(504.0)}" and row["side"] == "with the move"
    # the headline number is the USUAL rate, not the bucket's own
    assert row["rate"] == "50.00%" and row["rate_note"] == "" and row["edge"] is False
    assert row["room"] == "The call wall at 503.75 (0.05%) is inside the target"
    assert row["morning"] == "↓ Down"


def test_the_room_is_the_dealer_lines_own_first_clause():
    entry = _ientry(1, dealer_line="Room up to the call wall at 510.00 (1.29%); "
                                   "8 of 10 big names have room")
    row = scalp.glance_row("SPY", entry, "intraday", current=True, when="10:21 call")
    assert row["room"] == "Room up to the call wall at 510.00 (1.29%)"
    flat = _ientry(None, roles={}, note="flat over the last 15 minutes — no direction")
    row = scalp.glance_row("SPY", flat, "intraday", current=True, when="10:21 call")
    assert row["room"] == fmt.NO_READING and row["now"] == "No direction"
    assert row["trade"] == "Flat over the last 15 minutes — no call"
    assert row["rate"] == fmt.NO_READING and row["side"] == ""


def test_a_call_whose_time_has_passed_keeps_its_words_and_loses_its_colour():
    from pages.options import theme
    view = _glance_view()
    view["intraday"]["latest"]["SPY"]["highlight"] = "long_against"
    live, = scalp.glance_rows(view, _at(2026, 10, 19, 10, 25))
    assert live["edge"] is True and live["rate_note"] == scalp.EDGE
    assert live["trade"] == f"Long put {fmt.strike(504.0)}"
    assert live["side"] == "against the move"
    assert live["rate"] == "50.00% vs 50.00% usually"
    assert theme.TXT_POS in live["_rate_class"]
    past, = scalp.glance_rows(view, _at(2026, 10, 19, 10, 40))
    assert past["when"] == "10:21 call · past" and past["edge"] is False
    assert past["rate_note"] == scalp.EDGE and past["_rate_class"] == ""
    assert past["_now_class"] == theme.MUTED and past["_word_class"] == theme.BADGE_MUTED
    assert past["_morning_class"] == theme.MUTED


def test_a_caution_is_said_beside_the_rate_never_as_an_edge():
    from pages.options import theme
    view = _glance_view()
    view["intraday"]["latest"]["SPY"]["avoid"] = ["long_with"]
    row, = scalp.glance_rows(view, _at(2026, 10, 19, 10, 25))
    assert row["rate_note"] == scalp.CAUTION and row["edge"] is False
    assert row["_rate_class"] == theme.TXT_WARN


def test_before_the_first_15_minute_call_the_0836_call_leads():
    view = _glance_view(day="2026-10-20", call_day="2026-10-20")
    assert scalp.intraday_leads(view) is False
    row, = scalp.glance_rows(view, _at(2026, 10, 20, 8, 40))
    assert row["when"] == "08:36 call" and row["now"] == "↓ Down"
    assert row["word"] == fmt.NO_READING and row["_word_class"] == ""
    assert row["room"] == fmt.NO_READING
    assert row["trade"] == f"Long put {fmt.strike(501.0)}" and row["rate"] == "40.00%"
    assert scalp.glance_line(view, _at(2026, 10, 20, 8, 40)) == \
        ("Today's call · made 08:36 CT", "")


def test_yesterdays_call_is_labelled_not_todays_in_the_glance():
    from pages.options import theme
    row, = scalp.glance_rows(_glance_view(), _at(2026, 10, 20, 8, 0))
    assert row["when"] == "2026-10-19 10:21 call · not today's"
    assert row["_now_class"] == theme.MUTED and row["edge"] is False
    morning, = scalp.glance_rows(_glance_view(intraday={}), _at(2026, 10, 20, 8, 0))
    assert morning["when"] == "2026-10-19 08:36 call · not today's"


def test_a_symbol_only_one_call_carries_still_has_a_row():
    view = _glance_view(symbols={"SPY": _symbol(-1), "QQQ": _symbol(1)})
    rows = scalp.glance_rows(view, _at(2026, 10, 19, 10, 25))
    assert [r["symbol"] for r in rows] == ["SPY", "QQQ"]
    assert rows[0]["when"] == "10:21 call" and rows[1]["when"] == "08:36 call"
    only = _glance_view(symbols={})
    assert [r["symbol"] for r in scalp.glance_rows(only, _at(2026, 10, 19, 10, 25))] == ["SPY"]


def test_the_glance_line_and_the_edge_summary():
    view = _glance_view()
    assert scalp.glance_line(view, _at(2026, 10, 19, 10, 25)) == \
        ("Latest call at 10:21 · next at 10:36", "")
    line, warning = scalp.glance_line(view, _at(2026, 10, 19, 10, 40))
    assert line.startswith("Last call at 10:21") and "newer slot" in warning
    rows = scalp.glance_rows(view, _at(2026, 10, 19, 10, 25))
    assert scalp.edge_summary(rows) == scalp.GLANCE_EDGE_NONE
    view["intraday"]["latest"]["SPY"]["highlight"] = "long_with"
    rows = scalp.glance_rows(view, _at(2026, 10, 19, 10, 25))
    assert scalp.edge_summary(rows) == "Measured edge now: SPY"
    flat = _ientry(None, roles={}, note="flat over the last 15 minutes — no direction")
    rows = scalp.glance_rows(_glance_view(symbols={}, intraday=_section(latest={"SPY": flat})),
                             _at(2026, 10, 19, 10, 25))
    assert scalp.edge_summary(rows) == ""


def test_the_countdown_runs_to_the_next_slot_today_only():
    view = _glance_view()
    assert scalp.countdown_text(view, _at(2026, 10, 19, 10, 25)) == "next call in 11 min"
    almost = _dt.datetime(2026, 10, 19, 10, 35, 30, tzinfo=_CT)
    assert scalp.countdown_text(view, almost) == "next call in 1 min"
    assert scalp.countdown_text(view, _at(2026, 10, 19, 10, 40)) == ""
    final = _glance_view(intraday=_section(next_slot="2026-10-20T08:51:00-05:00"))
    assert scalp.countdown_text(final, _at(2026, 10, 19, 14, 0)) == ""
    assert scalp.countdown_text({}, _at(2026, 10, 19, 10, 25)) == ""


def test_the_glance_draws_one_kit_table_and_returns_its_countdown(drawn):
    label = scalp._glance(_glance_view(symbols={"SPY": _symbol(-1), "QQQ": _symbol(1)}),
                          _at(2026, 10, 19, 10, 25))
    assert label is not None and label.text == "next call in 11 min"
    assert [t for k, t in drawn if k == "table"] == [2]
    assert "Latest call at 10:21 · next at 10:36" in _texts(drawn)
    assert "kit.table(glance_columns(), rows)" in SRC
    assert {c["name"] for c in scalp.glance_columns()} == set(scalp._GLANCE_SLOTS)


def test_each_symbol_has_one_closed_section_with_its_calls(drawn):
    view = _glance_view(symbols={"SPY": _symbol(-1), "QQQ": _symbol(1)})
    scalp._details(view, _at(2026, 10, 19, 10, 25))
    assert _folds(drawn) == ["SPY details", "QQQ details", "Today's calls",
                             "Not in the probability: headlines, flow alerts and the report",
                             "Scorecard"]
    texts = _texts(drawn)
    # inside SPY's section the 15-minute call comes first, then the 08:36 call
    spy = texts[texts.index("SPY details"):texts.index("QQQ details")]
    assert spy.index("15-minute call") < spy.index("08:36 call")
    assert any(t.startswith("Confirmation ·") for t in spy)
    qqq = texts[texts.index("QQQ details"):texts.index("Today's calls")]
    assert "08:36 call" in qqq and "15-minute call" not in qqq


def test_before_the_first_15_minute_call_the_0836_card_comes_first(drawn):
    view = _glance_view(day="2026-10-20", call_day="2026-10-20")
    scalp._details(view, _at(2026, 10, 20, 8, 40))
    texts = _texts(drawn)
    spy = texts[texts.index("SPY details"):]
    assert spy.index("08:36 call") < spy.index("15-minute call")


def test_an_open_section_stays_open_across_a_repaint(monkeypatch):
    from nicegui import ui
    made = []

    def expansion(text="", *a, value=False, on_value_change=None, **k):
        made.append((text, value, on_value_change))
        return _Rec([], "expansion", text)
    monkeypatch.setattr(ui, "expansion", expansion)
    toggled = []
    exp = scalp._fold("SPY details", "symbol:SPY", {"symbol:SPY"},
                      lambda key, is_open: toggled.append((key, is_open)))
    assert made[0][:2] == ("SPY details", True)
    made[0][2](type("E", (), {"value": False})())
    assert toggled == [("symbol:SPY", False)] and exp.text == "SPY details"
    scalp._fold("Scorecard", "scorecard", set())
    assert made[1][1] is False
    body = SRC[SRC.index("def render():"):]
    assert '"open": set()' in body
    assert '_details(view, now, state["open"], _toggle)' in body
    assert "@guard\n    def _toggle(" in body
    tick = body[body.index("@guard\n    def _tick"):]
    assert 'state["countdown"].text = countdown_text(state["view"], now)' in tick
