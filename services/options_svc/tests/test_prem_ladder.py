"""
test_prem_ladder.py — the Premium Divergence panel's strike-ladder series.

``compute.prem_ladder`` turns raw ``gex_history_db.load_date_with_grid(…, "prem")``
rows into the per-timestamp ladder the panel scrubs through. It is PURE (rows in,
JSON-safe list out) and TOTAL over anything the DB or a partial write can produce.
"""
from services.options_svc import compute


#############################################
# FIXTURE HELPERS
#############################################

def _row(ts, spot, grid):
    """One load_date_with_grid tuple: (ts, spot, flip, top_pos, top_neg, net, grid)."""
    return (ts, spot, None, None, None, None, grid)


def _cell(call, put):
    return {"call": float(call), "put": float(put), "net": float(call - put)}


def _ladder_of(strikes, spot=100.0, ts=1000):
    """One row whose grid holds a flat 1.0/1.0 cell at each of ``strikes``."""
    return [_row(ts, spot, {float(k): _cell(1.0, 1.0) for k in strikes})]


#############################################
# WINDOWING
#############################################

def test_crops_to_n_side_strikes_around_that_rows_own_spot():
    rows = _ladder_of(range(90, 111), spot=100.0)
    out = compute.prem_ladder(rows, n_side=2)
    strikes = [r[0] for r in out[0]["rows"]]
    # 2 below + the spot strike + 2 above.
    assert strikes == [98.0, 99.0, 100.0, 101.0, 102.0]


def test_each_row_is_centred_on_its_own_spot_not_the_last_one():
    """The ladder answers "premium by strike where spot was THEN". Centring every
    row on the latest spot would slide the whole session's ladders off the money
    on a trending day — the one day the panel is most worth reading."""
    grid = {float(k): _cell(1.0, 1.0) for k in range(90, 111)}
    rows = [_row(1000, 95.0, grid), _row(2000, 105.0, grid)]
    out = compute.prem_ladder(rows, n_side=1)
    assert [r[0] for r in out[0]["rows"]] == [94.0, 95.0, 96.0]
    assert [r[0] for r in out[1]["rows"]] == [104.0, 105.0, 106.0]


def test_keeps_the_full_ladder_when_the_chain_is_narrower_than_the_window():
    out = compute.prem_ladder(_ladder_of([99.0, 100.0, 101.0]), n_side=5)
    assert [r[0] for r in out[0]["rows"]] == [99.0, 100.0, 101.0]


def test_rows_are_strike_ascending():
    grid = {103.0: _cell(1, 1), 99.0: _cell(1, 1), 101.0: _cell(1, 1)}
    out = compute.prem_ladder([_row(1000, 101.0, grid)], n_side=5)
    assert [r[0] for r in out[0]["rows"]] == [99.0, 101.0, 103.0]


#############################################
# SHAPE
#############################################

def test_emits_strike_call_put_triples_in_dollars():
    grid = {100.0: _cell(2_500_000.0, 1_000_000.0)}
    out = compute.prem_ladder([_row(1700000000, 100.0, grid)], n_side=5)
    assert out == [{"ts": 1700000000, "spot": 100.0,
                    "rows": [[100.0, 2_500_000.0, 1_000_000.0]]}]


def test_net_is_not_transmitted():
    """call - put is one subtraction in the browser; shipping it would grow the
    payload by a third for a value the panel already recomputes on every scrub."""
    out = compute.prem_ladder(_ladder_of([100.0]), n_side=5)
    assert len(out[0]["rows"][0]) == 3


def test_payload_is_json_safe():
    import json
    out = compute.prem_ladder(_ladder_of(range(90, 111)), n_side=5)
    assert json.loads(json.dumps(out)) == out


#############################################
# TOTALITY
#############################################

def test_defensive_over_junk_rows():
    assert compute.prem_ladder(None) == []
    assert compute.prem_ladder([]) == []
    assert compute.prem_ladder(["notarow", None, ()]) == []
    # Short tuples, a non-dict grid and an empty grid each skip THAT row only.
    rows = [(1000,), _row(2000, 100.0, "notagrid"), _row(3000, 100.0, {}),
            _row(4000, 100.0, {100.0: _cell(5.0, 1.0)})]
    out = compute.prem_ladder(rows, n_side=5)
    assert [r["ts"] for r in out] == [4000]


def test_row_without_a_usable_spot_is_dropped():
    """A ladder needs a centre. Emitting an uncentred one would put arbitrary
    wing strikes under a header that says "premium by strike @ <time>"."""
    grid = {float(k): _cell(1.0, 1.0) for k in range(90, 111)}
    out = compute.prem_ladder([_row(1000, None, grid), _row(2000, "x", grid),
                               _row(3000, 100.0, grid)], n_side=1)
    assert [r["ts"] for r in out] == [3000]


def test_malformed_cells_are_skipped_not_zeroed():
    """A cell that cannot be read is absent, never 0.0 — zero is a real reading
    here ("nothing traded this side")."""
    grid = {99.0: "junk", 100.0: {"call": None, "put": 1.0},
            101.0: {"call": 3.0}, 102.0: _cell(7.0, 2.0)}
    out = compute.prem_ladder([_row(1000, 100.0, grid)], n_side=5)
    assert out[0]["rows"] == [[102.0, 7.0, 2.0]]


def test_non_finite_values_are_rejected():
    """nan/inf survive a JSON round-trip through Redis and would render as a
    blank or an infinitely long bar rather than an obvious error."""
    grid = {100.0: {"call": float("nan"), "put": 1.0},
            101.0: {"call": 1.0, "put": float("inf")},
            102.0: _cell(4.0, 1.0)}
    out = compute.prem_ladder([_row(1000, 101.0, grid)], n_side=5)
    assert out[0]["rows"] == [[102.0, 4.0, 1.0]]


def test_a_row_whose_every_cell_is_unusable_is_dropped():
    out = compute.prem_ladder([_row(1000, 100.0, {100.0: "junk"})], n_side=5)
    assert out == []


def test_string_strike_keys_are_accepted():
    """Grids round-trip through JSON in the legacy storage format, which
    stringifies float keys — the same trap _refloat_keys exists for."""
    grid = {"100.0": _cell(5.0, 1.0), "101.0": _cell(2.0, 1.0)}
    out = compute.prem_ladder([_row(1000, 100.0, grid)], n_side=5)
    assert out[0]["rows"] == [[100.0, 5.0, 1.0], [101.0, 2.0, 1.0]]


def test_rows_are_ts_ascending_even_if_the_input_is_not():
    grid = {100.0: _cell(1.0, 1.0)}
    out = compute.prem_ladder([_row(3000, 100.0, grid), _row(1000, 100.0, grid)],
                              n_side=5)
    assert [r["ts"] for r in out] == [1000, 3000]
