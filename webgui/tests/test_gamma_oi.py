"""The Open Interest tab on Dealer Positioning (``pages/options/gamma_oi``)."""
import json
import re

import pytest

from pages.options import gamma_oi as oi
from test_flow_panels import _dompurify_allowlist

CFG = {"span_pct": 10.0, "max_rows": 48, "cap_quantile": 0.92}
EXPS = ["2026-11-16", "2026-11-20", "2026-12-18"]


def _cell(calls, puts):
    return {"call_oi": calls, "put_oi": puts, "net_gex_usd": 0.0}


def _grid():
    """Three expiries around a 100.00 price. Keys are STRINGS, as Redis
    returns them."""
    near = {"90.0": _cell(10, 400), "95.0": _cell(50, 900), "100.0": _cell(300, 300),
            "105.0": _cell(800, 40), "110.0": _cell(500, 5)}
    return {"underlying_price": 100.0, "expirations": list(EXPS),
            "cells": {EXPS[0]: near,
                      EXPS[1]: {"95.0": _cell(20, 100), "105.0": _cell(60, 10)},
                      EXPS[2]: {"100.0": _cell(5, 5), "150.0": _cell(7000, 0)}}}


def _model(grid=None, spot=100.0, cfg=CFG):
    mdl, reason = oi.model(_grid() if grid is None else grid, spot, cfg)
    assert mdl is not None, reason
    return mdl


# ── the model ────────────────────────────────────────────────────────────────

def test_rows_run_from_the_highest_strike_down_and_columns_follow_the_expiries():
    mdl = _model()
    assert mdl["rows"] == [110.0, 105.0, 100.0, 95.0, 90.0]
    assert [e["date"] for e in mdl["expiries"]] == EXPS
    assert [e["label"] for e in mdl["expiries"]] == ["Nov 16", "Nov 20", "Dec 18"]


def test_a_cell_holds_that_expirys_calls_and_puts_at_that_strike():
    near = _model()["expiries"][0]
    assert near["cells"][1] == (800.0, 40.0)         # the 105.00 row
    assert _model()["expiries"][1]["cells"][0] == (0.0, 0.0)   # nothing at 110


def test_walls_are_the_largest_call_above_and_the_largest_put_below():
    near = _model()["expiries"][0]
    assert (near["call_wall"], near["put_wall"]) == (105.0, 95.0)
    assert near["wall_rows"] == (1, 3)


def test_a_wall_at_the_price_itself_is_neither_above_nor_below():
    grid = _grid()
    grid["cells"][EXPS[0]]["100.0"] = _cell(99999, 99999)
    near = _model(grid)["expiries"][0]
    assert (near["call_wall"], near["put_wall"]) == (105.0, 95.0)


def test_totals_and_walls_cover_the_whole_chain_not_only_the_rows_drawn():
    """150.00 is outside the span, so it has no row, and it still counts."""
    far = _model()["expiries"][2]
    assert 150.0 not in _model()["rows"]
    assert far["calls"] == 7005.0 and far["call_wall"] == 150.0
    assert far["wall_rows"][0] is None               # nothing to outline


def test_put_call_ratio_is_puts_over_calls_and_absent_without_calls():
    assert _model()["expiries"][1]["ratio"] == pytest.approx(110 / 80)
    grid = _grid()
    grid["cells"][EXPS[1]] = {"95.0": _cell(0, 100)}
    assert _model(grid)["expiries"][1]["ratio"] is None


def test_max_pain_is_the_strike_where_the_open_contracts_are_worth_least():
    strikes = {90.0: (0.0, 100.0), 100.0: (10.0, 10.0), 110.0: (100.0, 0.0)}
    # Settling at 100 pays nothing to the 110 calls or the 90 puts.
    assert oi.max_pain(strikes) == 100.0
    by_hand = {s: sum(max(0.0, s - k) * c + max(0.0, k - s) * p
                      for k, (c, p) in strikes.items()) for s in strikes}
    assert oi.max_pain(strikes) == min(by_hand, key=by_hand.get)


def test_max_pain_matches_the_slow_sum_on_an_uneven_chain():
    strikes = {float(k): (float((k * 7) % 13), float((k * 5) % 11))
               for k in (80, 85, 90, 92, 95, 100, 101, 110, 125, 150)}
    by_hand = {s: sum(max(0.0, s - k) * c + max(0.0, k - s) * p
                      for k, (c, p) in strikes.items()) for s in strikes}
    assert by_hand[oi.max_pain(strikes)] == min(by_hand.values())


def test_max_pain_is_absent_with_nothing_open():
    assert oi.max_pain({100.0: (0.0, 0.0)}) is None and oi.max_pain({}) is None


def test_a_dense_chain_is_summed_into_wider_rows_and_loses_no_contract():
    cells = {str(float(k)): _cell(10, 20) for k in range(90, 111)}       # 21 strikes
    grid = {"underlying_price": 100.0, "expirations": [EXPS[0]],
            "cells": {EXPS[0]: cells}}
    mdl = _model(grid, cfg={**CFG, "max_rows": 12})
    assert len(mdl["rows"]) <= 12 and mdl["step"] == 2.0
    drawn = mdl["expiries"][0]["cells"]
    assert (sum(c for c, _p in drawn), sum(p for _c, p in drawn)) == (210.0, 420.0)


def test_a_sparse_chain_keeps_a_row_per_strike():
    assert _model()["step"] is None


def test_the_span_cuts_the_rows_to_the_strikes_near_the_price():
    assert _model(cfg={**CFG, "span_pct": 5.0})["rows"] == [105.0, 100.0, 95.0]


def test_the_colour_cap_ignores_the_largest_cells():
    cells = {str(float(k)): _cell(10, 0) for k in range(91, 110)}
    cells["100.0"] = _cell(1_000_000, 0)
    grid = {"underlying_price": 100.0, "expirations": [EXPS[0]],
            "cells": {EXPS[0]: cells}}
    assert _model(grid, cfg={**CFG, "cap_quantile": 0.9})["cap_total"] == 10.0


@pytest.mark.parametrize("grid, spot, reason", [
    ({}, 100.0, oi.NO_CHAIN),
    (None, 100.0, oi.NO_CHAIN),
    ("junk", 100.0, oi.NO_CHAIN),
    ({"expirations": EXPS, "cells": {}}, None, oi.NO_CHAIN),
    ({"expirations": EXPS, "cells": {e: {"100.0": _cell(0, 0)} for e in EXPS}},
     100.0, oi.ALL_ZERO),
    ({"expirations": [EXPS[0]], "cells": {EXPS[0]: {"500.0": _cell(5, 5)}}},
     100.0, oi.NO_STRIKES),
])
def test_nothing_to_draw_says_why(grid, spot, reason):
    assert oi.model(grid, spot, CFG) == (None, reason)


def test_a_cell_from_before_the_service_carried_open_interest_reads_as_zero():
    """An old cached snapshot has only the gamma fields: empty, not a crash."""
    grid = {"underlying_price": 100.0, "expirations": [EXPS[0]],
            "cells": {EXPS[0]: {"100.0": {"net_gex_usd": 5.0}}}}
    assert oi.model(grid, 100.0, CFG) == (None, oi.ALL_ZERO)


def test_junk_counts_and_keys_are_dropped():
    grid = _grid()
    grid["cells"][EXPS[0]].update({"abc": _cell(1, 1), "101.0": _cell(float("nan"), -5),
                                   "102.0": "junk", "103.0": _cell(True, None)})
    near = _model(grid)["expiries"][0]
    assert near["calls"] == 1660.0 and near["puts"] == 1645.0


def test_the_price_falls_back_to_the_grids_own():
    assert _model(spot=None)["spot"] == 100.0


def test_the_spot_line_sits_between_the_rows_either_side_of_the_price():
    rows = [110.0, 105.0, 100.0, 95.0]
    centre = lambda i: oi.TOP + (i + 0.5) * oi.ROW_H              # noqa: E731
    assert oi._spot_y(rows, 105.0) == pytest.approx(centre(1))
    assert oi._spot_y(rows, 102.5) == pytest.approx((centre(1) + centre(2)) / 2)
    assert oi._spot_y(rows, 500.0) == oi.TOP
    assert oi._spot_y(rows, 1.0) == oi.TOP + 4 * oi.ROW_H


def test_summary_names_the_tab_and_the_totals():
    text = oi.summary(_model())
    assert text.startswith("Open Interest") and "3 expiries" in text
    assert "calls 8,745" in text and "puts 1,760" in text
    assert oi.summary(None) == "Open Interest"


# ── the fragment ─────────────────────────────────────────────────────────────

def _panel(grid=None):
    return oi.panel(_grid() if grid is None else grid, 100.0, "spy", "oimap", CFG)


def test_the_panel_carries_every_id_the_script_writes_to():
    html, mdl = _panel()
    ids = set(re.findall(r'id="([^"]+)"', html))
    want = {"oimap-m-total", "oimap-m-net", "oimap-ph", "oimap-ch", "oimap-x"}
    for j in range(len(mdl["expiries"])):
        want |= {f"oimap-{part}-{j}" for part in ("rail", "side", "e", "h")}
    for i in range(len(mdl["rows"])):
        want |= {f"oimap-{part}-{i}" for part in ("p", "c", "pr", "cr", "t")}
    assert want <= ids
    script = oi.bind_js("oimap", mdl)
    data = json.loads(script.split("var D=", 1)[1].split(", id=D.id", 1)[0])
    assert data["dates"] == EXPS and data["id"] == "oimap"
    assert data["cells"][0][1] == [800.0, 40.0] and data["strikes"][1] == "105.00"
    used = set(re.findall(r"el\('([a-z]+)", script))
    assert used == {"rail", "side", "e", "h", "p", "c", "t", "ph", "ch", "m", "x"}
    assert "['pr-'+r,'cr-'+r]" in script


def test_the_script_draws_the_bars_from_the_counts_in_either_mode():
    script = oi.bind_js("oimap", _panel()[1])
    assert "net ? Math.max(0,p-c) : p" in script
    assert "net ? Math.max(0,c-p) : c" in script
    assert "net ? 'Net puts' : 'Puts'" in script


def test_only_the_first_expirys_rail_and_read_show_before_the_script_runs():
    html, _ = _panel()
    assert 'id="oimap-rail-0" style="background' in html
    assert re.search(r'id="oimap-rail-0" style="[^"]*display:flex', html)
    assert re.search(r'id="oimap-rail-1" style="[^"]*display:none', html)
    assert 'id="oimap-side-0" style="display:flex' in html
    assert 'id="oimap-side-1" style="display:none' in html
    assert 'id="oimap-h-0" style="cursor:pointer;opacity:1;' in html
    assert 'id="oimap-h-1" style="cursor:pointer;opacity:0.55;' in html


def test_every_column_gives_a_strike_the_same_height():
    """The cells, the bars and the tags share one strike axis only while a
    row is the same height in each. A cell with a margin above AND below
    loses half of it to margin collapse (seen in the browser, 2026-11-14:
    the cells ran 15 px short of the bars by the tenth row)."""
    html, _ = _panel()
    cell = re.search(r'title="Nov 16 · 110\.00[^"]*" style="([^"]*)"', html).group(1)
    assert f"height:{oi.CELL_H:.0f}px" in cell
    assert f"margin-bottom:{oi.ROW_H - oi.CELL_H:.0f}px" in cell
    assert "margin:" not in cell.replace("margin-bottom:", "")
    assert f"height:{oi.ROW_H:.0f}px" in re.search(
        r'id="oimap-pr-0" title="[^"]*" style="([^"]*)"', html).group(1)


def test_the_first_expirys_bars_are_drawn_before_the_script_runs():
    """105.00 holds the most calls (800) and 95.00 the most puts (900): the
    longest bar is the 100% one and the rest are its share."""
    html, _ = _panel()
    assert re.search(r'id="oimap-p-3" style="[^"]*width:100\.0%', html)
    assert re.search(r'id="oimap-c-1" style="[^"]*width:88\.9%', html)
    assert re.search(r'id="oimap-c-4" style="[^"]*width:1\.1%', html)


def test_the_panel_names_the_symbol_and_prints_strikes_to_two_decimals():
    html, _ = _panel()
    assert "SPY · by strike and expiry" in html
    assert "105.00" in html and "Put / call ratio · Nov 16" in html
    assert "Where the price sits" in html and "Strike map" in html
    assert "The read" in html and "Levels" in html and "Whole chain" in html


def test_a_tooltip_gives_the_exact_counts():
    html, _ = _panel()
    assert 'title="Nov 16 · 105.00 · calls 800 · puts 40"' in html
    assert 'title="Dec 18 · 100.00 · calls 5 · puts 5"' in html


def test_the_walls_and_max_pain_are_tagged_on_their_rows():
    mdl = _model()
    assert oi.tags(mdl, mdl["expiries"][0]) == {
        1: ("Call wall", "call"), 2: ("Max pain", "pain"), 3: ("Put wall", "put")}
    # 150.00 has no row, so the far expiry's call wall has no tag.
    assert all(kind != "call" for _t, kind in
               oi.tags(mdl, mdl["expiries"][2]).values())


def test_a_summed_row_names_the_strike_and_two_levels_share_a_tag():
    cells = {str(float(k)): _cell(10, 20) for k in range(90, 111)}
    cells["103.0"] = _cell(900, 20)
    grid = {"underlying_price": 100.0, "expirations": [EXPS[0]],
            "cells": {EXPS[0]: cells}}
    mdl = _model(grid, cfg={**CFG, "max_rows": 12})
    exp = mdl["expiries"][0]
    found = oi.tags(mdl, exp)
    assert found[exp["wall_rows"][0]][0].startswith("Call wall 103.00")
    exp = {**exp, "pain_row": exp["wall_rows"][0], "max_pain": 104.0}
    assert oi.tags(mdl, exp)[exp["wall_rows"][0]] == (
        "Call wall 103.00 · Max pain 104.00", "call")


def test_an_expiry_says_its_weekday_and_which_one_is_next_or_monthly():
    assert [e["sub"] for e in _model()["expiries"]] == [
        "Mon · next", "Fri · monthly", "Fri · monthly"]


def test_the_read_states_the_counts_and_distances():
    mdl = _model()
    lines = oi.read(mdl, mdl["expiries"][0])
    assert lines[0] == "The price is at max pain (100.00)."
    assert ("On Nov 16 calls outnumber puts 1.01 to 1. Over all 3 expiries calls "
            "outnumber puts 4.97 to 1.") in lines
    assert lines[-1] == ("The put wall at 95.00 is 5.00% below the price and "
                         "the call wall at 105.00 is 5.00% above it.")


def test_the_read_says_when_the_largest_strike_is_on_the_far_side():
    grid = _grid()
    grid["cells"][EXPS[0]]["108.0"] = _cell(0, 5000)
    mdl = _model(grid)
    lines = oi.read(mdl, mdl["expiries"][0])
    assert ("The strike with the most Nov 16 puts is above the price, at "
            "108.00.") in lines
    assert any(line.startswith("The price is ") and "under max pain" in line
               for line in lines)


def test_the_read_copes_with_one_side_missing():
    grid = {"underlying_price": 100.0, "expirations": [EXPS[0]],
            "cells": {EXPS[0]: {"105.0": _cell(50, 0)}}}
    mdl = _model(grid)
    lines = oi.read(mdl, mdl["expiries"][0])
    assert "On Nov 16 only calls are open." in lines
    assert lines[-1] == "The call wall at 105.00 is 5.00% above the price."
    assert "Put wall" not in oi.panel(grid, 100.0, "SPY", "oimap", CFG)[0].split("Strike map")[0]


def test_the_rail_gives_each_levels_distance_with_a_true_minus():
    html, _ = _panel()
    rail = html.split('id="oimap-rail-0"', 1)[1].split('id="oimap-rail-1"', 1)[0]
    assert "+5.00 · +5.00%" in rail and "−5.00 · −5.00%" in rail
    assert "50% calls / 50% puts" in rail


def test_the_empty_state_keeps_the_frame_and_has_no_script():
    html, mdl = oi.panel({}, 100.0, "SPY", "oimap", CFG)
    assert mdl is None and oi.NO_CHAIN in html and "fx-panel" in html
    assert oi.bind_js("oimap", mdl) == ""


def test_the_symbol_is_escaped():
    html, _ = oi.panel(_grid(), 100.0, "<b>", "oimap", CFG)

    assert "&lt;B&gt;" in html


@pytest.mark.parametrize("build", [
    lambda: _panel()[0],
    lambda: oi.panel({}, 100.0, "SPY", "oimap", CFG)[0],
])
def test_the_panel_emits_nothing_the_sanitizer_would_strip(build):
    allow = _dompurify_allowlist()
    out = build()
    tags = set(re.findall(r"<([a-zA-Z][\w-]*)", out))
    attrs = set(re.findall(r'([a-zA-Z][\w-]*)="', out))
    stripped = sorted(n for n in tags | attrs if n.lower() not in allow)
    assert not stripped, f"DOMPurify would strip: {stripped}"
    assert {"div", "style"} <= tags | attrs
    assert "data-" not in out and "dominant-baseline" not in out


def test_the_settings_come_from_the_heatmap_config(monkeypatch):
    from shared import gamma_heat_config
    monkeypatch.setattr(gamma_heat_config, "open_interest",
                        lambda: {**CFG, "span_pct": 5.0})
    mdl, _ = oi.model(_grid(), 100.0)
    assert mdl["rows"] == [105.0, 100.0, 95.0]


# ── type and the expiry strip ────────────────────────────────────────────────

@pytest.mark.parametrize("build", [
    lambda: _panel()[0],
    lambda: oi.panel({}, 100.0, "SPY", "oimap", CFG)[0],
])
def test_the_panel_names_no_font_and_no_size_of_its_own(build):
    """The tab wears the app's font and the app's text sizes, like the other
    pages (the user's instruction, 2026-11-14). A font named here, or a size
    in pixels, would stop following Settings -> Appearance."""
    html = build()
    assert "font-family" not in html and "font:" not in html
    assert "font-size" not in html and "letter-spacing" not in html
    assert "Plex" not in html and "Rajdhani" not in html
    sizes = set(re.findall(r'class="(text-[\w-]+)"', html))
    assert sizes and sizes <= {"text-subtitle1", "text-subtitle2", "text-h6",
                               "text-xs"}


def test_the_script_restyles_without_naming_a_font():
    script = oi.bind_js("oimap", _panel()[1])
    for banned in ("font-family", "font-size", "font:", "letter-spacing"):
        assert banned not in script


def _many(count):
    dates = [f"2026-12-{day:02d}" for day in range(1, count + 1)]
    return {"underlying_price": 100.0, "expirations": dates,
            "cells": {d: {"100.0": _cell(5, 5), "105.0": _cell(9, 1)}
                      for d in dates}}


def test_up_to_five_expiries_fill_the_strip():
    html, _ = oi.panel(_many(5), 100.0, "SPY", "oimap", CFG)
    assert "repeat(5,minmax(0,1fr))" in html
    assert "scroll sideways" not in html


def test_more_expiries_keep_their_width_and_the_strip_scrolls():
    html, mdl = oi.panel(_many(12), 100.0, "SPY", "oimap", CFG)
    assert len(mdl["expiries"]) == 12
    width = 12 * (oi.HEAT_COL_W + oi.HEAT_GAP) - oi.HEAT_GAP
    assert f"repeat(12,{oi.HEAT_COL_W:.0f}px)" in html
    assert f"width:{width:.0f}px" in html and width > oi.HEAT_W
    strip = re.search(r'id="oimap-x" style="([^"]*)"', html).group(1)
    assert "overflow-x:auto" in strip
    assert "12 expiries, scroll sideways for the rest" in html
    for j in range(12):
        assert f'id="oimap-e-{j}"' in html and f'id="oimap-h-{j}"' in html


def test_the_script_keeps_the_strips_scroll_and_brings_the_chosen_expiry_in():
    script = oi.bind_js("oimap", oi.panel(_many(12), 100.0, "SPY", "oimap", CFG)[1])
    assert "S.left=x.scrollLeft" in script and "x.scrollLeft=S.left" in script
    assert "restore(apply())" in script


def test_every_column_header_is_the_same_height():
    """The expiry buttons sit in the scrolling strip and the other columns'
    headers do not: the rows line up only while both are HEAD_H tall."""
    html, _ = _panel()
    assert html.count(f"height:{oi.HEAD_H:.0f}px") == 5
    assert f"height:{oi.HEAD_H - oi.HEAD_GAP:.0f}px" in oi._expiry_style(True)

