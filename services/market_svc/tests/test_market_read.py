"""market_read: the Desk's six readings, each a tailwind, a headwind or neutral
for stocks, and the slot schedule that publishes them.

Design: docs/plans/2026-10-05-market-read-scorecard-design.md.
"""
import copy
import datetime as dt
import math
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.market_svc import market_read as mr
from shared import market_read_config

CT = ZoneInfo("America/Chicago")
T, H, N, X = mr.TAILWIND, mr.HEADWIND, mr.NEUTRAL, mr.NONE
MON = "2026-10-05"


@pytest.fixture
def cfg():
    return copy.deepcopy(market_read_config.DEFAULTS)


def _tile(name, pct=None, last=None, state="flat", **kw):
    return {"display": name, "change_pct": pct, "last": last, "color_state": state, **kw}


def _dash(**frames):
    """``_dash(Volatility=[tile, ...], ...)``; an underscore is a space."""
    return {"categories": [{"category": name.replace("_", " "), "tiles": tiles}
                           for name, tiles in frames.items()]}


def _index(spx, ndx):
    return _dash(Cash_Index=[_tile("SPX", spx, 7700.0), _tile("NDX", ndx, 31000.0)])


# --- direction ----------------------------------------------------------------

@pytest.mark.parametrize("spx,ndx,want", [
    (0.69, 0.79, T), (-0.40, -0.90, H), (0.10, 0.12, N),
    (0.60, -0.60, N),            # the two indexes disagree
    (0.25, 0.25, T),             # the bound is inclusive
    (-0.25, -0.25, H),
    (0.60, 0.10, N),             # one of them barely moved
])
def test_direction(cfg, spx, ndx, want):
    row = mr.direction(_index(spx, ndx), cfg)
    assert row["verdict"] == want
    assert row["facts"] == {"spx_pct": spx, "ndx_pct": ndx}


@pytest.mark.parametrize("dash", [
    None, {}, {"categories": 5}, _index(None, 0.5), _index(0.5, math.nan),
    _dash(Cash_Index=[_tile("SPX", 0.5)]),
])
def test_direction_without_both_indexes_is_no_reading(cfg, dash):
    assert mr.direction(dash, cfg)["verdict"] == X


def test_the_direction_threshold_is_read_from_config(cfg):
    cfg["direction"]["move_pct"] = 1.0
    assert mr.direction(_index(0.69, 0.79), cfg)["verdict"] == N


# --- breadth ------------------------------------------------------------------

def _board(up, down, flat=0):
    tiles = ([_tile(f"U{i}", 1.0, state="risk_on_mild") for i in range(up)]
             + [_tile(f"D{i}", -1.0, state="risk_off_strong") for i in range(down)]
             + [_tile(f"F{i}", 0.0) for i in range(flat)])
    return _dash(Sector_SPDR=tiles)


@pytest.mark.parametrize("up,down,want", [(29, 6, T), (6, 29, H), (10, 10, N),
                                          (6, 4, T), (4, 6, H)])
def test_breadth(cfg, up, down, want):
    row = mr.breadth(_board(up, down, flat=3), cfg)
    assert row["verdict"] == want
    assert (row["facts"]["advancing"], row["facts"]["declining"]) == (up, down)
    assert row["facts"]["share"] == pytest.approx(up / (up + down))


def test_breadth_counts_only_the_equity_frames():
    # A bid VIX and a rallying Treasury are "risk off" by the board's polarity.
    # Counted, they would cancel real equity buying.
    dash = _dash(
        Volatility=[_tile("VIX", 5.0, state="risk_off_strong")],
        **{"Fixed Income / Credit ETF": [_tile("TLT", 1.0, state="risk_off_mild")]},
        Sector_SPDR=[_tile("XLK", 1.0, state="risk_on_mild")],
        **{"Top 10": [_tile("NVDA", 1.0, state="risk_on_strong")],
           "Broad-Market ETF": [_tile("SPY", 1.0, state="risk_on_mild")],
           "Thematic / Industry ETF": [_tile("SMH", -1.0, state="risk_off_mild")]})
    facts = mr.breadth(dash, market_read_config.DEFAULTS)["facts"]
    assert (facts["advancing"], facts["declining"]) == (3, 1)


def test_breadth_skips_a_basket_tile():
    dash = _dash(**{"Top 10": [
        _tile("BIG10", state="risk_on_mild", basket=True),
        _tile("NVDA", 1.0, state="risk_on_mild")]})
    assert mr.breadth(dash, market_read_config.DEFAULTS)["facts"]["advancing"] == 1


@pytest.mark.parametrize("dash", [None, {}, _board(0, 0, flat=5),
                                  _dash(Sector_SPDR=[_tile("XLK", state="no_data")])])
def test_breadth_with_nothing_moving_is_no_reading(cfg, dash):
    assert mr.breadth(dash, cfg)["verdict"] == X


# --- structure ----------------------------------------------------------------

def _mrow(sym, spot, flip, ceiling, regime="above", net_gex=1e9):
    return {"symbol": sym, "spot": spot, "flip": flip, "call_wall": ceiling,
            "put_wall": None, "net_gex": net_gex, "gex_regime": regime}


def _matrix(*rows):
    return {"rows": list(rows)}


def test_structure_headwind_at_the_ceiling_in_long_gamma(cfg):
    row = mr.structure(_matrix(_mrow("SPY", 774.77, 770.41, 775.0),
                               _mrow("QQQ", 755.23, 750.96, 756.0)), cfg)
    assert row["verdict"] == H
    spy = row["facts"]["symbols"][0]
    assert (spy["symbol"], spy["mode"], spy["state"]) == ("SPY", "long", H)
    assert spy["room_pct"] == pytest.approx((775.0 - 774.77) / 774.77 * 100)


def test_structure_tailwind_above_the_flip_with_room(cfg):
    row = mr.structure(_matrix(_mrow("SPY", 770.0, 765.0, 780.0),
                               _mrow("QQQ", 750.0, 745.0, 760.0)), cfg)
    assert row["verdict"] == T


def test_structure_below_the_flip_is_a_headwind_whatever_the_room(cfg):
    row = mr.structure(_matrix(_mrow("SPY", 760.0, 765.0, 790.0, regime="below"),
                               _mrow("QQQ", 740.0, 745.0, 770.0, regime="below")), cfg)
    assert row["verdict"] == H
    assert row["facts"]["symbols"][0]["mode"] == "short"


def test_structure_between_the_two_bounds_is_neutral(cfg):
    # 0.25% < room < 0.50% on both
    row = mr.structure(_matrix(_mrow("SPY", 770.0, 765.0, 773.0),
                               _mrow("QQQ", 750.0, 745.0, 753.0)), cfg)
    assert row["verdict"] == N


def test_structure_through_the_ceiling_counts_as_at_it(cfg):
    row = mr.structure(_matrix(_mrow("SPY", 776.0, 770.0, 775.0),
                               _mrow("QQQ", 757.0, 750.0, 756.0)), cfg)
    assert row["verdict"] == H
    assert row["facts"]["symbols"][0]["room_pct"] < 0


def test_structure_is_neutral_when_the_two_symbols_disagree(cfg):
    row = mr.structure(_matrix(_mrow("SPY", 774.9, 770.0, 775.0),      # at the ceiling
                               _mrow("QQQ", 750.0, 745.0, 760.0)), cfg)  # room
    assert row["verdict"] == N
    assert [s["state"] for s in row["facts"]["symbols"]] == [H, T]


@pytest.mark.parametrize("bad", [
    _mrow("QQQ", 750.0, 745.0, 760.0, net_gex=0.0),     # the after-hours artefact
    _mrow("QQQ", 750.0, None, 760.0),                   # no flip
    _mrow("QQQ", 750.0, 745.0, None),                   # long gamma, no ceiling
    _mrow("QQQ", 750.0, 745.0, 760.0, regime="na"),
    _mrow("QQQ", math.nan, 745.0, 760.0),
    None,                                               # not on the board at all
])
def test_structure_without_a_reading_on_every_symbol_is_no_reading(cfg, bad):
    rows = [_mrow("SPY", 770.0, 765.0, 780.0)] + ([bad] if bad else [])
    row = mr.structure(_matrix(*rows), cfg)
    assert row["verdict"] == X
    assert [s["symbol"] for s in row["facts"]["symbols"]] == ["SPY", "QQQ"]


def test_structure_reads_the_configured_symbols(cfg):
    cfg["structure"]["symbols"] = ["$SPX"]
    row = mr.structure(_matrix(_mrow("$SPX", 7700.0, 7650.0, 7800.0)), cfg)
    assert row["verdict"] == T and len(row["facts"]["symbols"]) == 1


@pytest.mark.parametrize("matrix", [None, {}, {"rows": 5}, {"rows": [None, 7]}])
def test_structure_of_a_malformed_matrix_is_no_reading(cfg, matrix):
    assert mr.structure(matrix, cfg)["verdict"] == X


# --- volatility ---------------------------------------------------------------

def _vol(vix, vix_pct, vix1d=None, vix3m=None, spx=None):
    return _dash(Volatility=[_tile("VIX", vix_pct, vix), _tile("VIX1D", None, vix1d),
                             _tile("VIX3M", None, vix3m)],
                 Cash_Index=[_tile("SPX", spx, 7700.0)])


@pytest.mark.parametrize("kw,want", [
    (dict(vix=15.55, vix_pct=1.57, vix1d=8.49, vix3m=18.04, spx=0.69), H),   # fear up, stocks up
    (dict(vix=15.0, vix_pct=-3.0, vix1d=12.0, vix3m=18.0, spx=0.5), T),
    (dict(vix=15.0, vix_pct=0.2, vix1d=12.0, vix3m=18.0, spx=0.5), N),
    (dict(vix=15.0, vix_pct=4.0, vix1d=12.0, vix3m=18.0, spx=-1.2), N),      # up on a DOWN day: expected
    (dict(vix=15.0, vix_pct=-3.0, vix1d=16.0, vix3m=18.0, spx=0.5), H),      # one-day above the month
    (dict(vix=20.0, vix_pct=-3.0, vix1d=12.0, vix3m=18.0, spx=0.5), N),      # falling but inverted
    (dict(vix=15.0, vix_pct=-3.0, vix1d=12.0, vix3m=None, spx=0.5), N),      # no three-month to compare
    (dict(vix=15.0, vix_pct=1.0, vix1d=12.0, vix3m=18.0, spx=0.01), H),      # the bound is inclusive
])
def test_volatility(cfg, kw, want):
    assert mr.volatility(_vol(**kw), cfg)["verdict"] == want


@pytest.mark.parametrize("kw", [dict(vix=None, vix_pct=1.0), dict(vix=15.0, vix_pct=None),
                                dict(vix=math.nan, vix_pct=1.0)])
def test_volatility_without_the_vix_is_no_reading(cfg, kw):
    assert mr.volatility(_vol(**kw), cfg)["verdict"] == X


# --- flow ----------------------------------------------------------------------

def _flow_views(contracts, date=MON, sides_date=None):
    """``contracts``: (id, side, bought, sold, unlabelled[, osi])."""
    alerts, entries = [], {}
    for c in contracts:
        aid, side, b, s, u = c[:5]
        osi = c[5] if len(c) > 5 else aid
        alerts.append({"type": "uoa", "id": aid, "side": side, "osi": osi})
        entries[aid] = {"poll": {"bought": b, "sold": s, "unlabelled": u},
                        "stream": None, "volume": b + s + u}
    return ({"date": sides_date or date, "public": True, "contracts": entries},
            {"date": date, "alerts": alerts})


def _many(side, n, b, s, u=0, start=0):
    return [(f"{side}{start + i}", side, b, s, u) for i in range(n)]


def test_flow_tailwind_when_calls_lean_bought_and_puts_do_not(cfg):
    sides, alerts = _flow_views(_many("call", 8, 600, 400) + _many("put", 4, 500, 500))
    row = mr.flow(sides, alerts, cfg)
    assert row["verdict"] == T and row["estimate"] is True
    assert row["facts"]["call_lean"] == pytest.approx(20.0)
    assert row["facts"]["put_lean"] == pytest.approx(0.0)
    assert (row["facts"]["calls"], row["facts"]["puts"], row["facts"]["contracts"]) == (8, 4, 12)


def test_flow_headwind_when_puts_lean_bought_and_calls_do_not(cfg):
    sides, alerts = _flow_views(_many("call", 8, 500, 500) + _many("put", 4, 700, 300))
    assert mr.flow(sides, alerts, cfg)["verdict"] == H


def test_flow_is_neutral_when_both_lean_bought(cfg):
    sides, alerts = _flow_views(_many("call", 8, 600, 400) + _many("put", 4, 700, 300))
    assert mr.flow(sides, alerts, cfg)["verdict"] == N


def test_flow_is_neutral_inside_the_threshold(cfg):
    # The first live session: calls 47.7 bought against 46.0 sold.
    sides, alerts = _flow_views(_many("call", 10, 477, 460, 63) + _many("put", 4, 480, 470, 50))
    row = mr.flow(sides, alerts, cfg)
    assert row["verdict"] == N
    assert row["facts"]["call_lean"] == pytest.approx(1.7)


def test_flow_lean_is_pooled_by_volume_not_averaged(cfg):
    # One huge even contract and nine tiny all-bought ones: an average of the
    # ten leans would say 90 points; the volume says almost nothing.
    big = [("big", "call", 500_000, 500_000, 0)]
    sides, alerts = _flow_views(big + _many("call", 9, 100, 0))
    assert mr.flow(sides, alerts, cfg)["facts"]["call_lean"] == pytest.approx(0.09, abs=0.01)


def test_flow_unlabelled_volume_dilutes_the_lean(cfg):
    sides, alerts = _flow_views(_many("call", 10, 600, 400, 1000))
    assert mr.flow(sides, alerts, cfg)["facts"]["call_lean"] == pytest.approx(10.0)


def test_flow_counts_a_contract_flagged_twice_once(cfg):
    twice = [("a|uoa", "call", 600, 400, 0, "OSI1"), ("a|big_delta", "call", 600, 400, 0, "OSI1")]
    sides, alerts = _flow_views(twice + _many("call", 9, 500, 500))
    facts = mr.flow(sides, alerts, cfg)["facts"]
    assert facts["calls"] == 10


def test_flow_below_the_minimum_is_no_reading(cfg):
    sides, alerts = _flow_views(_many("call", 9, 900, 100))
    row = mr.flow(sides, alerts, cfg)
    assert row["verdict"] == X and row["facts"]["contracts"] == 9


def test_flow_with_no_puts_flagged_can_still_be_a_tailwind(cfg):
    sides, alerts = _flow_views(_many("call", 10, 700, 300))
    row = mr.flow(sides, alerts, cfg)
    assert row["verdict"] == T and row["facts"]["put_lean"] is None


def test_flow_from_another_day_is_no_reading(cfg):
    sides, alerts = _flow_views(_many("call", 12, 700, 300), sides_date="2026-10-02")
    assert mr.flow(sides, alerts, cfg)["verdict"] == X


def test_flow_ignores_alerts_that_name_no_contract_and_unusable_tallies(cfg):
    sides, alerts = _flow_views(_many("call", 10, 700, 300))
    alerts["alerts"] += [{"type": "crossover", "id": "x", "side": "calls_over"},
                         {"type": "uoa", "id": "nan", "side": "call", "osi": "N"},
                         {"type": "uoa", "id": "neg", "side": "call", "osi": "G"},
                         "junk", None]
    sides["contracts"]["nan"] = {"poll": {"bought": math.nan, "sold": 1, "unlabelled": 1}}
    sides["contracts"]["neg"] = {"poll": {"bought": -5, "sold": 1, "unlabelled": 1}}
    assert mr.flow(sides, alerts, cfg)["facts"]["calls"] == 10


@pytest.mark.parametrize("sides,alerts", [
    (None, {"date": MON, "alerts": []}), ({"date": MON, "contracts": {}}, None),
    ({"date": MON, "contracts": 5}, {"date": MON, "alerts": []}),
    ({"date": MON, "contracts": {}}, {"date": MON, "alerts": "x"}),
])
def test_flow_with_a_missing_view_is_no_reading(cfg, sides, alerts):
    row = mr.flow(sides, alerts, cfg)
    assert row["verdict"] == X and row["estimate"] is True


# --- cross-asset ---------------------------------------------------------------

def _cross(tlt, dxy, hyg):
    def t(name, state):
        return _tile(name, 0.5, 100.0, state) if state else _tile(name, state="no_data")
    return _dash(**{"Fixed Income / Credit ETF": [t("TLT", tlt), t("HYG", hyg)],
                    "Currency": [t("$DXY", dxy)]})


@pytest.mark.parametrize("tlt,dxy,hyg,want", [
    ("risk_on_mild", "risk_on_strong", "flat", T),
    ("risk_off_mild", "risk_off_strong", "risk_on_mild", H),
    ("risk_on_mild", "risk_off_mild", "flat", N),
    ("flat", "flat", "flat", N),
    ("risk_on_mild", None, "risk_on_mild", T),      # two usable tiles are enough
])
def test_cross_asset_counts_the_boards_own_colours(cfg, tlt, dxy, hyg, want):
    row = mr.cross_asset(_cross(tlt, dxy, hyg), cfg)
    assert row["verdict"] == want
    assert [t["name"] for t in row["facts"]["tiles"]] == ["TLT", "$DXY", "HYG"]


@pytest.mark.parametrize("dash", [None, {}, _cross("risk_on_mild", None, None),
                                  _cross(None, None, None)])
def test_cross_asset_with_fewer_than_two_tiles_is_no_reading(cfg, dash):
    assert mr.cross_asset(dash, cfg)["verdict"] == X


# --- slots ---------------------------------------------------------------------

def _at(day, hh, mm, ss=0):
    y, m, d = (int(x) for x in day.split("-"))
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=CT)


@pytest.mark.parametrize("hhmm,want", [
    ((8, 29), None), ((8, 30), None),        # nothing has traded at the open itself
    ((8, 44), None), ((8, 45), "08:45"), ((8, 52), "08:45"),
    ((12, 45), "12:45"), ((12, 59), "12:45"), ((13, 0), "13:00"),
    ((14, 59), "14:45"), ((15, 0), "15:00"), ((15, 14), "15:00"),
    ((15, 15), None), ((18, 0), None),
])
def test_slot_due_through_a_session(hhmm, want):
    assert mr.slot_due(_at(MON, *hhmm), None, 15) == want


def test_a_slot_fires_once():
    assert mr.slot_due(_at(MON, 12, 46), "12:45", 15) is None
    assert mr.slot_due(_at(MON, 13, 0, 2), "12:45", 15) == "13:00"


@pytest.mark.parametrize("hhmm,want", [((8, 45), None), ((9, 0), "09:00"),
                                       ((9, 29), "09:00"), ((9, 30), "09:30"),
                                       ((15, 0), "15:00")])
def test_slot_due_at_thirty_minutes(hhmm, want):
    assert mr.slot_due(_at(MON, *hhmm), None, 30) == want


@pytest.mark.parametrize("day", ["2026-10-03", "2026-10-04", "2026-11-26"])
def test_no_slot_on_a_weekend_or_a_holiday(day):
    assert mr.slot_due(_at(day, 12, 45), None, 15) is None


def test_a_naive_clock_is_central_time():
    assert mr.slot_due(dt.datetime(2026, 10, 5, 12, 46), None, 15) == "12:45"


def test_a_clock_in_another_zone_is_converted():
    eastern = dt.datetime(2026, 10, 5, 13, 46, tzinfo=ZoneInfo("America/New_York"))
    assert mr.slot_due(eastern, None, 15) == "12:45"


def test_next_slot_and_the_final_one():
    assert mr.next_slot(MON, "12:45", 15) == "13:00"
    assert mr.next_slot(MON, "14:45", 15) == "15:00"
    assert mr.next_slot(MON, "15:00", 15) is None
    assert mr.next_slot(MON, "14:30", 30) == "15:00"
    assert mr.is_final(MON, "15:00") is True
    assert mr.is_final(MON, "14:45") is False


# --- a reading -----------------------------------------------------------------

def _inputs():
    dash = _dash(
        Cash_Index=[_tile("SPX", 0.69, 7776.0), _tile("NDX", 0.79, 31050.0)],
        Volatility=[_tile("VIX", 1.57, 15.55), _tile("VIX1D", -21.0, 8.49),
                    _tile("VIX3M", 0.2, 18.04)],
        Sector_SPDR=[_tile(f"X{i}", 1.0, state="risk_on_mild") for i in range(10)]
        + [_tile("XLRE", -0.3, state="risk_off_mild")],
        **{"Fixed Income / Credit ETF": [_tile("TLT", -0.9, 76.8, "risk_on_mild"),
                                          _tile("HYG", -0.1, 76.8, "flat")],
           "Currency": [_tile("$DXY", 0.45, 29.0, "risk_off_mild")]})
    sides, alerts = _flow_views(_many("call", 10, 477, 460, 63) + _many("put", 4, 560, 440))
    return {"dashboard": dash,
            "matrix": _matrix(_mrow("SPY", 774.77, 770.41, 775.0),
                              _mrow("QQQ", 755.23, 750.96, 756.0)),
            "sides": sides, "alerts": alerts}


def test_build_assembles_six_rows_in_order(cfg):
    out = mr.build(_inputs(), cfg, date=MON, slot="12:45", ts=1000)
    assert [r["key"] for r in out["rows"]] == list(mr.ROW_KEYS)
    assert [r["verdict"] for r in out["rows"]] == [T, T, H, H, H, N]
    assert out["tally"] == {"tailwind": 2, "headwind": 3, "neutral": 1, "none": 0}
    assert (out["date"], out["slot"], out["next_slot"], out["final"]) \
        == (MON, "12:45", "13:00", False)
    assert out["interval_min"] == 15 and out["public"] is True and out["ts"] == 1000
    assert all(r["prev"] is None for r in out["rows"])
    assert out["history"] == [{"slot": "12:45", "verdicts": {
        "direction": T, "breadth": T, "structure": H, "volatility": H,
        "flow": H, "cross_asset": N}}]


def test_build_carries_the_previous_reading_and_the_days_history(cfg):
    first = mr.build(_inputs(), cfg, date=MON, slot="12:45", ts=1000)
    later = _inputs()
    later["dashboard"] = _index(0.10, 0.12)
    second = mr.build(later, cfg, date=MON, slot="13:00", ts=2000, previous=first)
    d = second["rows"][0]
    assert d["verdict"] == N
    assert d["prev"] == {"verdict": T, "facts": {"spx_pct": 0.69, "ndx_pct": 0.79}}
    assert [h["slot"] for h in second["history"]] == ["12:45", "13:00"]


def test_a_new_day_starts_with_no_history_and_no_previous(cfg):
    friday = mr.build(_inputs(), cfg, date="2026-10-02", slot="15:00", ts=1)
    monday = mr.build(_inputs(), cfg, date=MON, slot="08:45", ts=2, previous=friday)
    assert [h["slot"] for h in monday["history"]] == ["08:45"]
    assert all(r["prev"] is None for r in monday["rows"])


def test_the_close_reading_is_final_and_has_no_next_slot(cfg):
    out = mr.build(_inputs(), cfg, date=MON, slot="15:00", ts=1)
    assert out["final"] is True and out["next_slot"] is None


def test_missing_views_make_their_rows_no_reading_not_neutral(cfg):
    out = mr.build({"dashboard": None, "matrix": None, "sides": None, "alerts": None},
                   cfg, date=MON, slot="12:45", ts=1)
    assert [r["verdict"] for r in out["rows"]] == [X] * 6
    assert out["tally"] == {"tailwind": 0, "headwind": 0, "neutral": 0, "none": 6}


def test_one_row_raising_costs_only_that_row(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(area))
    monkeypatch.setattr(mr, "breadth", lambda *a: (_ for _ in ()).throw(ValueError("x")))
    out = mr.build(_inputs(), cfg, date=MON, slot="12:45", ts=1)
    assert [r["verdict"] for r in out["rows"]] == [T, X, H, H, H, N]
    assert seen == ["market.read.breadth"]


def test_public_is_true_only_for_a_real_true(cfg):
    cfg["public"] = "true"
    assert mr.build(_inputs(), cfg, date=MON, slot="12:45", ts=1)["public"] is False


def test_tally_counts_an_unknown_code_as_no_reading():
    assert mr.tally([{"verdict": "tailwind"}, {"verdict": "banana"}, {}]) \
        == {"tailwind": 1, "headwind": 0, "neutral": 0, "none": 2}
