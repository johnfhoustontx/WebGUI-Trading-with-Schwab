"""Tests for driver_svc.settings — the autonomous-driver tunables.

``limits()`` is the risk envelope the guardrails enforce; assert its shape and a
few sanity invariants (the daily risk budget must cover at least one max-risk
trade; the caps are positive) so a typo in a default surfaces here, not in a
live cycle.
"""
from services.driver_svc import settings


def test_limits_dict_shape():
    lim = settings.limits()
    assert lim["daily_target"] == 500.0
    assert lim["per_trade_max_risk"] > 0
    assert lim["daily_risk_budget"] >= lim["per_trade_max_risk"]
    assert 20.0 <= lim["vix_max"] <= 40.0          # a sane VIX ceiling (aggression-tunable)
    assert lim["daily_loss_halt"] > 0              # the daily-loss halt is surfaced + positive
    assert lim["max_concurrent"] >= 1
    assert lim["max_trades_per_cycle"] >= 1


def test_target_cap_and_floor_defaults():
    """The cumulative MTD target band: base 500 → floor 250 (ease when ahead) → cap
    1000 (ratchet when behind). Surfaced in limits() for the guardrails/tests."""
    assert settings.TARGET_CAP == 1000.0 and settings.TARGET_FLOOR == 250.0
    lim = settings.limits()
    assert lim["target_cap"] == 1000.0 and lim["target_floor"] == 250.0
    assert lim["daily_target"] == 500.0                 # base unchanged
    assert settings.TARGET_FLOOR < settings.DAILY_TARGET < settings.TARGET_CAP


def test_directional_gate_flag_default_off():
    """The directional gate ships INERT — enabled only after the offline backtest
    (validate_directional_gate.py) proves it blocks the CCS loss bucket."""
    assert settings.DIRECTIONAL_GATE_ENABLED is False


def test_resolve_model_prefers_env(monkeypatch):
    """The DRIVER_MODEL env var wins over the file and the default."""
    monkeypatch.setenv("DRIVER_MODEL", "claude-haiku-4-5-20251001")
    assert settings._resolve_model() == "claude-haiku-4-5-20251001"


def test_resolve_model_falls_back_to_file(monkeypatch, tmp_path):
    """Env unset → read the model from the gitignored shared/driver_model.txt
    (the robust override that dodges Windows setx propagation; stripped)."""
    monkeypatch.delenv("DRIVER_MODEL", raising=False)
    import repo_paths
    monkeypatch.setattr(repo_paths, "SHARED_DIR", tmp_path)
    (tmp_path / "driver_model.txt").write_text("  claude-opus-4-8 \n", encoding="utf-8")
    assert settings._resolve_model() == "claude-opus-4-8"


def test_resolve_model_default_when_unset(monkeypatch, tmp_path):
    """Env unset AND no override file → the committed Sonnet build default.

    Load-bearing, not a restatement of the constant: the ONLY thing that used to
    keep the 30-minute autonomous checkpoints off an Opus model was
    shared/driver_model.txt, which is gitignored — so a fresh clone or a wiped
    shared/ silently ran them on Opus. This pins the no-override path to the
    Sonnet tier the standing cost directive requires."""
    monkeypatch.delenv("DRIVER_MODEL", raising=False)
    import repo_paths
    monkeypatch.setattr(repo_paths, "SHARED_DIR", tmp_path)  # empty dir → no file
    assert settings._resolve_model() == "claude-sonnet-5"
