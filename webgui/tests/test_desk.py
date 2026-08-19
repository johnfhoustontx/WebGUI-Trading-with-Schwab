"""Pure display logic for the Desk (/desk).

Every builder here takes plain dicts and returns plain dicts, so the whole
screen's arithmetic is testable without a browser — the same shape
``pages/market.py`` proved out.
"""
import pathlib

from pages import desk as d


# ── Tailwind-first guard (house standard; the shared guard file is not ours to
# edit right now, so the Desk carries its own copy of the same assertion) ─────
def test_desk_module_has_no_inline_style():
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    assert ".style(" not in src, "desk.py still uses .style()"
    assert ":style=" not in src, "desk.py still uses a Vue :style= slot binding"


# ── structure_positions ──────────────────────────────────────────────────────
def test_structure_positions_places_spot_between_the_walls():
    p = d.structure_positions(spot=100.0, flip=99.0, put_wall=95.0, call_wall=105.0)
    assert 0.0 <= p["put_wall"] < p["spot"] < p["call_wall"] <= 100.0
    assert p["spot"] == 50.0


def test_structure_positions_is_none_without_both_walls():
    assert d.structure_positions(100.0, 99.0, None, 105.0) is None
    assert d.structure_positions(100.0, 99.0, 95.0, None) is None
    assert d.structure_positions(None, 99.0, 95.0, 105.0) is None


def test_structure_positions_clamps_spot_outside_the_walls():
    p = d.structure_positions(spot=110.0, flip=99.0, put_wall=95.0, call_wall=105.0)
    assert p["spot"] == 100.0


def test_structure_positions_survives_a_degenerate_span():
    assert d.structure_positions(100.0, 100.0, 100.0, 100.0) is None


def test_structure_positions_omits_flip_when_absent_but_keeps_the_bar():
    p = d.structure_positions(100.0, None, 95.0, 105.0)
    assert p is not None and p["flip"] is None


def test_structure_positions_refuses_non_finite_inputs_rather_than_pinning_a_wall():
    """A NaN must NOT render as a position — the documented app-wide trap.

    ``min(100.0, max(0.0, nan))`` is **0.0** (every comparison against NaN is
    False, so ``max`` keeps its running value), which would draw an absent spot
    sitting exactly ON the put wall — the single most alarming thing the bar can
    say. ``+inf`` clamps the other way, onto the call wall. Both are "no
    reading" dressed as an extreme one, so the bar is withheld instead.
    """
    nan, inf = float("nan"), float("inf")
    assert d.structure_positions(nan, 99.0, 95.0, 105.0) is None
    assert d.structure_positions(inf, 99.0, 95.0, 105.0) is None
    assert d.structure_positions(100.0, 99.0, nan, 105.0) is None
    assert d.structure_positions(100.0, 99.0, 95.0, nan) is None
    # A non-finite FLIP only costs the flip tick; the bar itself still stands.
    p = d.structure_positions(100.0, nan, 95.0, 105.0)
    assert p is not None and p["flip"] is None


def test_structure_positions_never_raises_on_junk():
    assert d.structure_positions("x", None, "y", "z") is None
    assert d.structure_positions({}, [], object(), 105.0) is None


# ── dealer_rows ──────────────────────────────────────────────────────────────
def _mrow(sym, **over):
    """One ``cache:options:matrix`` row, in the shape options_svc publishes."""
    row = {"symbol": sym, "spot": 6712.81, "day_pct": 0.31,
           "trend_state": "up", "trend_dir": 0.42,
           "call_accel": "hot", "put_accel": "flat",
           "pc_ratio": 0.87, "net_prem_m": 12.4,
           "call_prem": 3e6, "put_prem": 8e5,
           "flip": 6680.0, "gex_regime": "above",
           "n_signals": 3, "n_alerts": 2, "signal": "buy",
           "signal_strength": 2, "hotness": 94, "eth_eligible": False,
           "call_wall": 6800.0, "put_wall": 6600.0, "net_gex": 1.42e9,
           "atm_iv": 13.4, "iv_state": "stable",
           "dealer_regime": "delta_wall_pin"}
    row.update(over)
    return row


def test_dealer_rows_selects_and_orders_the_desk_symbols():
    view = {"rows": [_mrow("QQQ"), _mrow("AAPL"), _mrow("$SPX")]}
    assert [r["symbol"] for r in d.dealer_rows(view, stale=False)] == ["$SPX", "QQQ"]


def test_dealer_rows_regime_word_comes_only_from_gex_regime():
    above = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime="above", net_gex=-5.0)]},
                          stale=False)[0]
    assert above["regime_word"] == "LONG GAMMA · PINS"
    below = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime="below")]}, stale=False)[0]
    assert below["regime_word"] == "SHORT GAMMA · RUNS"


def test_dealer_rows_regime_word_is_a_dash_when_the_side_is_unknown():
    for regime in ("na", None, "", "sideways"):
        row = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime=regime)]},
                            stale=False)[0]
        assert row["regime_word"] == "—"


def test_dealer_rows_net_gex_is_carried_but_never_names_the_regime():
    """The magnitude is displayed; the WORD is ``gex_regime``'s alone.

    They can legitimately disagree, and printing two conflicting regime claims
    in one row is the sectors-vs-rotation bug all over again."""
    row = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime="below", net_gex=9.9e9)]},
                        stale=False)[0]
    assert row["net_gex"] == 9.9e9
    assert row["regime_word"] == "SHORT GAMMA · RUNS"


def test_dealer_rows_withhold_walls_when_stale():
    row = d.dealer_rows({"rows": [_mrow("$SPX")]}, stale=True)[0]
    assert row["call_wall"] is None and row["put_wall"] is None
    assert row["structure"] is None
    assert row["stale"] is True
    # The spot / flip read still stands — only the WALLS are untrustworthy.
    assert row["spot"] == 6712.81 and row["flip"] == 6680.0


def test_dealer_rows_withhold_walls_on_the_all_zero_grid_signature():
    """Index option OI reads 0 after hours → an all-zero GEX grid → ARBITRARY
    walls. A withheld wall is honest; a confident wrong one is not."""
    row = d.dealer_rows({"rows": [_mrow("$SPX", net_gex=0.0)]}, stale=False)[0]
    assert row["call_wall"] is None and row["put_wall"] is None
    assert row["structure"] is None


def test_dealer_rows_keeps_walls_when_net_gex_is_merely_absent():
    """Absent ≠ zero. A symbol that simply doesn't publish net GEX has not
    exhibited the all-zero-grid signature, so its walls are still usable."""
    row = d.dealer_rows({"rows": [_mrow("$SPX", net_gex=None)]}, stale=False)[0]
    assert row["call_wall"] == 6800.0 and row["put_wall"] == 6600.0
    assert row["structure"] is not None


def test_dealer_rows_flip_side_and_distance():
    above = d.dealer_rows({"rows": [_mrow("$SPX", spot=6700.0, flip=6600.0)]},
                          stale=False)[0]
    assert above["flip_side"] == "above"
    # A percent-of-flip magnitude, so $SPX and SPY compare at a glance.
    assert abs(above["flip_distance"] - 100.0 / 6600.0 * 100.0) < 1e-3
    below = d.dealer_rows({"rows": [_mrow("$SPX", spot=6500.0, flip=6600.0)]},
                          stale=False)[0]
    assert below["flip_side"] == "below"
    assert below["flip_distance"] > 0        # a magnitude; the SIDE carries sign


def test_dealer_rows_flip_side_is_none_without_a_usable_flip():
    row = d.dealer_rows({"rows": [_mrow("$SPX", flip=None)]}, stale=False)[0]
    assert row["flip_side"] is None and row["flip_distance"] is None


def test_dealer_rows_never_pins_a_flip_side_on_a_nan():
    row = d.dealer_rows({"rows": [_mrow("$SPX", flip=float("nan"))]}, stale=False)[0]
    assert row["flip"] is None and row["flip_side"] is None
    assert row["flip_distance"] is None


def test_dealer_rows_is_empty_for_a_missing_view():
    assert d.dealer_rows(None, stale=False) == []
    assert d.dealer_rows({}, stale=False) == []
    assert d.dealer_rows({"rows": "nonsense"}, stale=False) == []


def test_dealer_rows_tolerates_a_row_missing_every_new_key():
    rows = d.dealer_rows({"rows": [{"symbol": "$SPX"}]}, stale=False)
    assert rows[0]["symbol"] == "$SPX" and rows[0]["structure"] is None
    assert rows[0]["regime_word"] == "—"
    assert rows[0]["call_wall"] is None and rows[0]["net_gex"] is None


def test_dealer_rows_keeps_the_first_row_per_symbol_and_skips_junk():
    view = {"rows": ["junk", None, _mrow("SPY", spot=1.0), _mrow("SPY", spot=2.0)]}
    rows = d.dealer_rows(view, stale=False)
    assert [r["symbol"] for r in rows] == ["SPY"]
    assert rows[0]["spot"] == 1.0
