"""The monthly refit's ship gate: a fit replaces the live swing model only if it passes.

`trade_svc` re-reads `swing_model.json` on every call, so a fit that writes it IS
the deploy -- there is no restart for a bad model to wait behind. Before the gate
the fit overwrote the live artifact whenever it finished, including when some
symbols' history silently failed to fetch (`fetch_daily` returns None and the
symbol is dropped) and whatever its out-of-sample IC came out as. The live model
this gate was written against scored +0.021, so a refit landing below zero is not
a corner case.

Pinned here:
  * coverage below the floor, or an OOS IC at or below the floor, is refused;
  * a NaN or missing IC is refused, never waved through by a failed comparison;
  * the thresholds come from config/swing_model.toml, not literals;
  * a refused fit leaves the live files byte-identical, writes a `.rejected`
    copy to inspect, and exits 1 so the timer's run shows as failed;
  * `--no-ship` never touches the live files, whatever the verdict.
"""
import json
import math

import pytest

import fit_swing_model as FSM


N = len(FSM.UNIVERSE_SECTOR)


def _artifact(oos_ic=0.03, used=N):
    return {"version": "2026-10-01", "fit_universe_n": used,
            "fit_universe": sorted(FSM.UNIVERSE_SECTOR), "horizon": 20,
            "regimes": {"all": {"oos_ic": oos_ic, "weights": {"mom": 1.0}}}}


CFG = {"refit": {"min_coverage": 0.90, "min_oos_ic": 0.0}}


class TestTheDecision:
    def test_a_full_positive_fit_ships(self):
        assert FSM.ship_decision(_artifact(), CFG) == []

    def test_thin_coverage_is_refused(self):
        reasons = FSM.ship_decision(_artifact(used=int(N * 0.8)), CFG)
        assert reasons and "symbols" in reasons[0]

    def test_coverage_exactly_at_the_floor_ships(self):
        used = math.ceil(N * 0.90)
        assert FSM.ship_decision(_artifact(used=used), CFG) == []

    def test_a_negative_ic_is_refused(self):
        reasons = FSM.ship_decision(_artifact(oos_ic=-0.01), CFG)
        assert reasons and "IC" in reasons[0]

    def test_an_ic_exactly_at_the_floor_is_refused(self):
        """"Above 0" -- a model with no out-of-sample edge is not an upgrade."""
        assert FSM.ship_decision(_artifact(oos_ic=0.0), CFG)

    @pytest.mark.parametrize("bad", [float("nan"), None, "x"])
    def test_an_unreadable_ic_is_refused(self, bad):
        assert FSM.ship_decision(_artifact(oos_ic=bad), CFG)

    def test_both_failures_are_reported(self):
        assert len(FSM.ship_decision(_artifact(oos_ic=-0.2, used=10), CFG)) == 2

    def test_the_thresholds_are_the_configs(self):
        loose = {"refit": {"min_coverage": 0.10, "min_oos_ic": -0.5}}
        assert FSM.ship_decision(_artifact(oos_ic=-0.2, used=10), loose) == []

    def test_the_default_config_is_the_shipped_file(self):
        FSM.reset_refit_config()
        cfg = FSM.load_refit_config()
        assert cfg["refit"]["min_coverage"] == 0.90
        assert cfg["refit"]["min_oos_ic"] == 0.0


@pytest.fixture
def paths(tmp_path, monkeypatch):
    live = tmp_path / "swing_model.json"
    report = tmp_path / "swing_model_report.md"
    live.write_text('{"version": "old"}', encoding="utf-8")
    report.write_text("old report", encoding="utf-8")
    monkeypatch.setattr(FSM, "SWING_MODEL", live)
    monkeypatch.setattr(FSM, "SWING_MODEL_REPORT", report)
    monkeypatch.setattr(FSM, "load_refit_config", lambda: CFG)
    monkeypatch.setattr(FSM, "write_report",
                        lambda *a, path, verdict=None: path.write_text(
                            f"report {verdict or 'shipped'}", encoding="utf-8"))
    return tmp_path


def _fit_returning(artifact):
    return lambda: (artifact, {}, {}, {}, {"oos_ic": 0, "n_folds": 1}, {},
                    artifact["fit_universe_n"])


class TestTheRun:
    def test_a_passing_fit_replaces_the_live_model(self, paths, monkeypatch):
        monkeypatch.setattr(FSM, "fit", _fit_returning(_artifact()))
        FSM.main([])
        assert json.loads((paths / "swing_model.json").read_text())["version"] == "2026-10-01"
        assert not (paths / "swing_model.rejected.json").exists()

    def test_a_passing_fit_clears_a_stale_rejection(self, paths, monkeypatch):
        (paths / "swing_model.rejected.json").write_text("{}", encoding="utf-8")
        (paths / "swing_model_report.rejected.md").write_text("x", encoding="utf-8")
        monkeypatch.setattr(FSM, "fit", _fit_returning(_artifact()))
        FSM.main([])
        assert not (paths / "swing_model.rejected.json").exists()
        assert not (paths / "swing_model_report.rejected.md").exists()

    def test_a_refused_fit_leaves_the_live_model_and_exits_1(self, paths, monkeypatch):
        monkeypatch.setattr(FSM, "fit", _fit_returning(_artifact(oos_ic=-0.05)))
        with pytest.raises(SystemExit) as exc:
            FSM.main([])
        assert exc.value.code == 1
        assert (paths / "swing_model.json").read_text() == '{"version": "old"}'
        assert (paths / "swing_model_report.md").read_text() == "old report"
        rejected = json.loads((paths / "swing_model.rejected.json").read_text())
        assert rejected["version"] == "2026-10-01"
        assert "NOT SHIPPED" in (paths / "swing_model_report.rejected.md").read_text()

    def test_no_ship_never_touches_the_live_model(self, paths, monkeypatch):
        monkeypatch.setattr(FSM, "fit", _fit_returning(_artifact()))
        FSM.main(["--no-ship"])
        assert (paths / "swing_model.json").read_text() == '{"version": "old"}'
        assert (paths / "swing_model.candidate.json").exists()

    def test_no_ship_of_a_failing_fit_still_reports_failure(self, paths, monkeypatch):
        monkeypatch.setattr(FSM, "fit", _fit_returning(_artifact(used=5)))
        with pytest.raises(SystemExit):
            FSM.main(["--no-ship"])
        assert (paths / "swing_model.json").read_text() == '{"version": "old"}'
