"""The Opening Scalp page (``/options/scalp``): its pure vocabulary.

The page's most common honest message is "no measured edge", so the tests pin
what keeps that message calm and truthful: the four trades are named by that
morning's contract, a base rate is the headline unless a role truly has an edge,
a role measured to LOSE more often is cautioned (never recommended), an absent
input never reads as a disagreement, and yesterday's call is never labelled as
today's. The page reads one view and one view only.
"""
import pathlib

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
    assert scalp.alignment_text(_symbol(1, alignment=0.5, bucket="mid"))         == "Agreement with the opening move: mid (50.00% of the inputs that took a side)"
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


def test_an_unlisted_input_still_shows():
    sym = _symbol(1, inputs={"mystery": 3.0}, agree={"mystery": 1})
    rows = {r["id"]: r for r in scalp.input_rows(sym)}
    assert rows["mystery"]["verdict"] == "Agrees"


# ── the state line and whose call it is ─────────────────────────────────────
def test_state_lines():
    nxt = "2026-10-20T08:36:00-05:00"
    assert scalp.state_line({"state": "call"}) is None
    waiting = scalp.state_line({"state": "waiting", "next_call": nxt})
    assert waiting == "Next call at 08:36 CT"
    closed = scalp.state_line({"state": "closed", "next_call": nxt})
    assert closed.startswith("Market closed — next call ") and "08:36 CT" in closed
    assert "Tue" in closed and "2026-10-20" in closed
    err = scalp.state_line({"state": "error", "next_call": nxt})
    assert err == "The last refresh failed — showing the last call"
    assert scalp.state_line({"state": "waiting", "next_call": None}) \
        == "Waiting for the morning call"
    assert scalp.state_line({"state": "closed", "next_call": "junk"}) \
        == "Market closed"


def test_yesterdays_call_is_never_read_as_todays():
    view = {"state": "waiting", "day": "2026-10-20", "call_day": "2026-10-19"}
    label = scalp.call_label(view, _symbol(1))
    assert "2026-10-19" in label and "08:36 CT" in label
    assert "not today" in label.lower()
    today = {"state": "call", "day": "2026-10-19", "call_day": "2026-10-19"}
    assert scalp.call_label(today, _symbol(1)) == "Today's call · made 08:36 CT"


def test_call_label_survives_a_missing_stamp():
    view = {"state": "call", "day": "2026-10-19", "call_day": "2026-10-19"}
    assert scalp.call_label(view, {"direction": None}) == "Today's call"
    stale = {"state": "closed", "day": "2026-10-19", "call_day": None}
    assert scalp.call_label(stale, {}) == "Last call"


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
