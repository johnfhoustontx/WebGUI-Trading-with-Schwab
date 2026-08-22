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
    the extraction must not have moved a single one."""
    assert sc.min_iv_rank() == {"0-DTE": 35, "SWING": 30}
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
