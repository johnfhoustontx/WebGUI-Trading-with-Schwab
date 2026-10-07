"""Scanner selection floors are config, and the shapes the engine expects survive."""
import pytest

from shared import scanner_config as sc


@pytest.fixture(autouse=True)
def _fresh():
    sc.reset_cache()
    yield
    sc.reset_cache()


def test_shipped_toml_matches_the_pre_extraction_values():
    """These were literals in scanner_engine.py carrying dated retune comments;
    the extraction must not have moved a single one.

    ⚠ ``min_iv_rank`` is asserted KEY BY KEY rather than as a whole dict. The
    docstring's claim is about the two extracted VALUES, and the whole-dict form
    additionally froze the key SET — which is a different and unwanted promise: it
    made adding a floor for a surface that never had one (``INCOME``, gap
    assessment B2) fail a test whose subject is drift in 35 and 30. The two
    original values are still pinned exactly; `test_vol_gate.py` owns the key set.
    """
    assert sc.min_iv_rank()["0-DTE"] == 35
    assert sc.min_iv_rank()["SWING"] == 30
    assert sc.min_credit_pct() == {
        "0-DTE": {"LOW": 0.08, "NORMAL": 0.12, "ELEVATED": 0.15, "HIGH": 0.20},
        "SWING": 0.12,
    }
    assert sc.directional_delta_range() == {"PCS": (-0.55, -0.30), "CCS": (0.30, 0.55)}
    d = sc.directional()
    assert (d["min_credit_pct"], d["max_risk_pct"], d["max_per_symbol_bucket"]) == \
        (0.20, 0.02, 2)
    sl = sc.single_leg()
    assert (sl["max_per_symbol"], sl["min_score"], sl["excluded_grades"]) == \
        (8, 50.0, ["Weak"])
    s = sc.scores()
    assert (s["capture_min"], s["neg_gex_min"], s["gex_strong_neg"], s["swing_min"]) == \
        (58, 62, -0.30, 50.0)


def test_delta_ranges_are_TUPLES_not_lists():
    """TOML gives arrays; the engine compares and unpacks these as tuples."""
    for v in sc.directional_delta_range().values():
        assert isinstance(v, tuple) and len(v) == 2


def test_credit_shape_is_the_one_the_engine_indexes():
    """MIN_CREDIT_PCT["0-DTE"][regime] and MIN_CREDIT_PCT["SWING"] are both live
    call shapes - the TOML nests them differently, so the flattening matters."""
    cp = sc.min_credit_pct()
    assert isinstance(cp["0-DTE"], dict) and isinstance(cp["SWING"], float)
    for regime in ("LOW", "NORMAL", "ELEVATED", "HIGH"):
        assert isinstance(cp["0-DTE"][regime], float)


def test_a_partial_credit_table_keeps_its_siblings(monkeypatch):
    monkeypatch.setattr(sc, "load",
                        lambda: {"credit": {"zero_dte": {"HIGH": 0.99}}})
    cp = sc.min_credit_pct()
    assert cp["0-DTE"]["HIGH"] == 0.99
    assert cp["0-DTE"]["LOW"] == 0.08, "an unset regime must keep its default"
    assert cp["SWING"] == 0.12


def test_a_junk_delta_band_falls_back(monkeypatch):
    monkeypatch.setattr(sc, "load",
                        lambda: {"directional": {"pcs_delta": "nonsense",
                                                 "ccs_delta": [0.1]}})
    assert sc.directional_delta_range() == {"PCS": (-0.55, -0.30), "CCS": (0.30, 0.55)}


def test_a_non_table_section_falls_back(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"scores": 42})
    assert sc.scores() == sc.DEFAULTS["scores"]


# --- the consumers actually read it -----------------------------------------
# scanner_engine + signal_recorder live in options-scanner and are covered by
# options-scanner/tests/; only the service side is importable from here.

def test_options_svc_swing_cut_reads_the_config():
    from services.options_svc import compute

    assert compute.SWING_MIN_SCORE == sc.scores()["swing_min"]


def test_options_svc_actually_READS_it(monkeypatch):
    """Equality alone proves nothing - the literal was 50.0 and so is the config."""
    import importlib

    from services.options_svc import compute

    monkeypatch.setattr(sc, "scores",
                        lambda: {**sc.DEFAULTS["scores"], "swing_min": 77.0})
    try:
        importlib.reload(compute)
        assert compute.SWING_MIN_SCORE == 77.0, \
            "options_svc/compute.py is not reading config/scanner.toml"
    finally:
        monkeypatch.undo()
        importlib.reload(compute)


# ---- the strike-selection thresholds are settings (audit CQ-10) --------------
# Eleven literals in scanner_engine.py, several retuned by hand with dated
# comments, decided which strikes the scanner may sell. They are config now.

SELECTION = {
    "max_entry_short_delta": 0.27, "momentum_veto": 0.6, "edge_margin": 0.02,
    "delta_sanity_max": 0.40, "min_abs_credit": 0.25, "min_abs_spread": 0.02,
    "max_width_dollars": 200, "zero_dte_min_mult": 0.618, "zero_dte_max_mult": 3.0,
    "directional_min_mult": 0.0, "directional_max_mult": 0.618,
}


def test_the_shipped_selection_thresholds_are_the_literals_they_replaced():
    assert sc.selection() == SELECTION


def test_one_selection_override_keeps_its_siblings(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"selection": {"edge_margin": 0.05}})
    got = sc.selection()
    assert got["edge_margin"] == 0.05
    assert {k: v for k, v in got.items() if k != "edge_margin"} == \
        {k: v for k, v in SELECTION.items() if k != "edge_margin"}


@pytest.mark.parametrize("bad", ["0.3", None, True, float("nan"), float("inf"), -0.1, [0.3]])
def test_an_unusable_selection_value_is_the_shipped_one(monkeypatch, bad):
    monkeypatch.setattr(sc, "load", lambda: {"selection": {"max_entry_short_delta": bad}})
    assert sc.selection()["max_entry_short_delta"] == 0.27


def test_a_selection_table_that_is_not_a_table_is_the_shipped_one(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"selection": 5})
    assert sc.selection() == SELECTION


def test_a_key_the_code_does_not_know_is_dropped(monkeypatch):
    # A typo must be a no-op, never a phantom threshold nothing reads.
    monkeypatch.setattr(sc, "load", lambda: {"selection": {"edge_margn": 0.5}})
    assert sc.selection() == SELECTION


# ── [structures]: the Market Scanner's non-credit pass (2026-10-06) ──────────

STRUCTURES = {"enabled": True,
              "families": ["VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR"],
              "min_score": 50.0, "excluded_grades": ["Weak"],
              "max_per_family": 2, "short_delta_min": 0.15,
              "earnings_long_premium": "flag"}


def test_the_shipped_structures_table_is_the_designed_one():
    assert sc.structures() == STRUCTURES


def test_a_missing_structures_table_is_the_shipped_one(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {})
    assert sc.structures() == STRUCTURES
    monkeypatch.setattr(sc, "load", lambda: {"structures": 5})
    assert sc.structures() == STRUCTURES


def test_structures_overrides_are_read(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"structures": {
        "enabled": False, "families": ["straddle", " vertical "],
        "min_score": 62, "excluded_grades": ["weak", "marginal"],
        "max_per_family": 1, "short_delta_min": 0.2,
        "earnings_long_premium": "drop"}})
    assert sc.structures() == {
        "enabled": False, "families": ["STRADDLE", "VERTICAL"],
        "min_score": 62.0, "excluded_grades": ["Weak", "Marginal"],
        "max_per_family": 1, "short_delta_min": 0.2,
        "earnings_long_premium": "drop"}


def test_structures_refuses_what_it_cannot_use(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"structures": {
        "enabled": "yes", "families": ["VERTICAL", "TYPO"],
        "min_score": "high", "excluded_grades": "Weak",
        "max_per_family": -1, "short_delta_min": float("nan"),
        "earnings_long_premium": "maybe", "min_scor": 99}})
    s = sc.structures()
    assert s["enabled"] is True                   # not a bool: the shipped one
    assert s["families"] == ["VERTICAL"]          # an unknown family is dropped
    assert s["min_score"] == 50.0
    assert s["excluded_grades"] == ["Weak"]
    assert s["max_per_family"] == 2
    assert s["short_delta_min"] == 0.15           # a NaN is never a threshold
    assert s["earnings_long_premium"] == "flag"
    assert "min_scor" not in s                    # a typo is a no-op


def test_a_bool_is_not_a_number_for_structures(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"structures": {
        "min_score": True, "max_per_family": True}})
    s = sc.structures()
    assert s["min_score"] == 50.0 and s["max_per_family"] == 2


def test_the_structures_dict_is_a_copy(monkeypatch):
    # The loader hands out its CACHED mapping; a caller mutating the answer
    # must not change the next caller's.
    sc.structures()["families"].append("JUNK")
    assert sc.structures()["families"] == STRUCTURES["families"]
