"""Columnar (numpy) encoding of the per-strike gamma grid.

The grid is a regular numeric table — ``{strike: {call, put, net}}``, ~250-290
strikes — but was stored as JSON text inside zlib. Measured on the live 1.5 GB
``gex_history.db`` (2026-08-08), that made ``json.loads`` **68% of the whole
read path**, with SQLite itself only 4%:

    load a session (437 rows) -> dict-of-dicts
      json + zlib   134.4 ms      726 KiB
      columnar f32   50.5 ms      527 KiB     <- 2.7x faster, 27% smaller
    encode one 1-min collector slot (372 rows)
      json + zlib    97.7 ms      512 KiB
      columnar f32   34.3 ms      265 KiB     <- 2.8x faster, 48% smaller

float32 is safe here because values are ALREADY rounded to
``_GRID_SIG_FIGS`` = 6 significant figures and float32 carries ~7.2. Measured
over 231,930 real cells the max relative error is 5.96e-08 — except for
denormals, see ``test_denormal_cells_flush_to_zero``.

FORWARD-ONLY, exactly like the 2026-07-25 zlib change: nothing is backfilled,
and ``_decode_grid`` still reads both legacy formats. The columnar path is
SHAPE-GATED — a grid whose cells are not plain ``{call, put, net}`` numbers
falls back to the JSON path, preserving the flexible-cell contract pinned by
``test_gex_history_efficiency.test_encode_grid_handles_non_float_and_nested_values``.
(Measured: 100% of cells across 1,500 random live snapshots are exactly that
shape, so the fast path covers all production data.)
"""

import json
import zlib

import gex_history_db as gh


def _std_grid(n=120):
    """A grid in the production shape: {strike: {call, put, net}} floats."""
    return {
        float(5000 + i * 5): {
            "call": 1234.5 * (i + 1),
            "put": -987.25 * (i + 1),
            "net": 247.25 * (i + 1),
        }
        for i in range(n)
    }


# ------------------------------------------------------------- fast path ---

def test_standard_grid_uses_columnar_format():
    """A production-shaped grid must take the columnar path, tagged by magic."""
    blob = gh._encode_grid(_std_grid())
    assert blob[:len(gh._GRID_MAGIC)] == gh._GRID_MAGIC


def test_columnar_round_trip_preserves_strikes_and_values():
    grid = _std_grid()
    decoded = gh._decode_grid(gh._encode_grid(grid))

    assert sorted(decoded) == sorted(grid)
    for k, cell in grid.items():
        for field in ("call", "put", "net"):
            assert abs(decoded[k][field] - cell[field]) / abs(cell[field]) < 1e-6


def test_columnar_decode_returns_plain_python_floats():
    """The decoded grid is JSON-serialized into the Redis cache payload, so
    numpy scalars would blow up ``json.dumps``. Keys and values must be builtins."""
    decoded = gh._decode_grid(gh._encode_grid(_std_grid(3)))

    for k, cell in decoded.items():
        assert type(k) is float
        for v in cell.values():
            assert type(v) is float
    json.dumps({str(k): v for k, v in decoded.items()})  # must not raise


def test_columnar_blob_is_smaller_than_json():
    """The size win is half the point — it shrinks the dominant on-disk cost."""
    grid = _std_grid()
    columnar = len(gh._encode_grid(grid))
    as_json = len(zlib.compress(json.dumps(grid).encode("utf-8")))

    assert columnar < as_json * 0.75, f"columnar {columnar} vs json {as_json}"


def test_strikes_round_trip_exactly():
    """Strikes are dict KEYS — an off-by-epsilon strike would silently split a
    grid into two entries and break the ±N-strike crop window."""
    grid = {2.5: {"call": 1.0, "put": -1.0, "net": 0.0},
            28000.0: {"call": 1.0, "put": -1.0, "net": 0.0},
            7719.5: {"call": 1.0, "put": -1.0, "net": 0.0}}
    decoded = gh._decode_grid(gh._encode_grid(grid))

    assert sorted(decoded) == [2.5, 7719.5, 28000.0]


# -------------------------------------------------------- fallback path ---

def test_nonstandard_cells_fall_back_to_json():
    """Nested/extra/non-numeric cells must NOT take the columnar path — the
    flexible-cell contract is pinned by test_gex_history_efficiency."""
    grid = {5000.0: {"net": 1.0, "oi": 1234, "tag": "wall", "sub": {"x": 1.0}}}
    blob = gh._encode_grid(grid)

    assert blob[:len(gh._GRID_MAGIC)] != gh._GRID_MAGIC
    assert gh._decode_grid(blob)[5000.0]["tag"] == "wall"


def test_missing_field_falls_back_to_json():
    """A cell without all three of call/put/net cannot be packed columnar."""
    blob = gh._encode_grid({5000.0: {"net": 1.0, "call": 2.0}})

    assert blob[:len(gh._GRID_MAGIC)] != gh._GRID_MAGIC
    assert gh._decode_grid(blob)[5000.0]["net"] == 1.0


def test_bool_values_fall_back_to_json():
    """bool is an int subclass — it must not be silently packed as 1.0/0.0."""
    blob = gh._encode_grid({5000.0: {"call": 1.0, "put": 2.0, "net": True}})

    assert blob[:len(gh._GRID_MAGIC)] != gh._GRID_MAGIC
    assert gh._decode_grid(blob)[5000.0]["net"] is True


def test_empty_grid_still_none():
    assert gh._encode_grid({}) is None
    assert gh._encode_grid(None) is None


# ---------------------------------------------------------- back-compat ---

def test_decode_still_reads_legacy_formats():
    """Forward-only: rows written before this change must keep decoding."""
    plain = json.dumps({"5000.0": {"net": 1.5}})
    assert gh._decode_grid(plain)[5000.0]["net"] == 1.5
    assert gh._decode_grid(zlib.compress(plain.encode("utf-8")))[5000.0]["net"] == 1.5


# ------------------------------------------------------- documented limit ---

def test_denormal_cells_flush_to_zero():
    """float32's smallest normal is ~1.18e-38. Deep-OTM strikes carry values far
    below that (BS gamma/vanna underflow, e.g. 3.8e-163) — 25.9% of non-zero
    cells on the live DB. They flush to 0.0, which is the physically correct
    value for a sub-1e-38 dollar exposure, and they sit far outside the +/-20
    strike display crop. Documented rather than silently accepted."""
    grid = {5000.0: {"call": 3.77051e-163, "put": -1e-200, "net": 1.0}}
    decoded = gh._decode_grid(gh._encode_grid(grid))

    assert decoded[5000.0]["call"] == 0.0
    assert decoded[5000.0]["put"] == 0.0
    assert decoded[5000.0]["net"] == 1.0


def test_large_gex_magnitudes_survive():
    """GEX runs to ~4.6e13 dollars on the live DB; float32 tops out at 3.4e38."""
    grid = {5000.0: {"call": 4.617e13, "put": -2.5e12, "net": 4.367e13}}
    decoded = gh._decode_grid(gh._encode_grid(grid))

    for field, want in (("call", 4.617e13), ("put", -2.5e12), ("net", 4.367e13)):
        assert abs(decoded[5000.0][field] - want) / abs(want) < 1e-6


def test_value_beyond_float32_range_falls_back_rather_than_becoming_inf():
    """Overflow must DEGRADE to the lossless JSON path, never silently store inf.

    Real GEX tops out ~4.6e13 against float32's 3.4e38, so this should never
    fire — but a silent finite -> inf corruption in a money-adjacent store is
    exactly the failure worth a guard. Underflow is different and deliberate:
    it flushes to 0.0 (see test_denormal_cells_flush_to_zero)."""
    grid = {5000.0: {"call": 1e39, "put": -1.0, "net": 1e39}}
    blob = gh._encode_grid(grid)

    assert blob[:len(gh._GRID_MAGIC)] != gh._GRID_MAGIC
    decoded = gh._decode_grid(blob)
    assert decoded[5000.0]["call"] == 1e39


def test_preexisting_infinity_still_falls_back():
    """An already-infinite input is not float32 overflow, but the same guard
    covers it — and it must round-trip through JSON unchanged, as before."""
    grid = {5000.0: {"call": float("inf"), "put": -1.0, "net": 1.0}}
    decoded = gh._decode_grid(gh._encode_grid(grid))

    assert decoded[5000.0]["call"] == float("inf")
