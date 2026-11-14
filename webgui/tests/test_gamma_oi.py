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
    want = {"oimap-sel", "oimap-g-total", "oimap-g-net", "oimap-m-total", "oimap-m-net"}
    for j in range(len(mdl["expiries"])):
        want |= {f"oimap-{part}-{j}" for part in ("h", "hl", "lad", "st", "total", "net")}
    assert want <= ids
    script = oi.bind_js("oimap", mdl)
    data = json.loads(script.split("var D=", 1)[1].split(", id=D.id", 1)[0])
    assert data["dates"] == EXPS and data["id"] == "oimap"


def test_the_script_shows_a_ladder_by_its_display_attribute():
    """A ladder is an SVG group hidden by ``display="none"``. Clearing its
    style leaves the attribute in force, so the chosen ladder stayed hidden
    (seen in the browser, 2026-11-14). The script must set the attribute."""
    script = oi.bind_js("oimap", _panel()[1])
    assert "node.setAttribute('display', on ? 'inline' : 'none')" in script
    assert "show(el('lad-'+i), i===j)" in script
    assert "node.style.display" not in script


def test_only_the_first_expirys_ladder_and_figures_show_before_the_script_runs():
    html, _ = _panel()
    assert '<g id="oimap-lad-0">' in html
    assert '<g id="oimap-lad-1" display="none">' in html
    assert 'id="oimap-st-0" style="display:grid' in html
    assert 'id="oimap-st-1" style="display:none' in html
    assert '<g id="oimap-g-net" display="none">' in html


def test_the_panel_names_the_symbol_and_prints_strikes_to_two_decimals():
    html, _ = _panel()
    assert "SPY · BY STRIKE AND EXPIRY" in html
    assert "105.00" in html and "Put / call ratio" in html and "Max pain" in html


def test_a_tooltip_gives_the_exact_counts():
    html, _ = _panel()
    assert "<title>Nov 16 · 105.00 · calls 800 · puts 40</title>" in html


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
