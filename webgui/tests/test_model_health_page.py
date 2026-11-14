"""The Model Health page's pure builders (pages/model_health.py)."""
from pages import model_health as page
from pages.options.theme import MUTED, TXT_NEG, TXT_POS


def model(**kw):
    base = {"key": "scalp_morning", "label": "08:36 scalp chance",
            "verdict": "beats", "days": 30, "min_days": 20, "rows": 400,
            "brier": 0.2414, "brier_usual": 0.2556,
            "diff": {"mean": -0.0142, "lo": -0.021, "hi": -0.004, "days": 30},
            "reliability": [[0.3, 0.4, 0.36, 0.31, 212], [0.4, 0.5, None, None, 0]],
            "drift_status": "steady", "drift": [], "note": None, "history": None}
    base.update(kw)
    return base


def test_verdict_words_and_fixed_classes():
    r = page.rows({"models": [model()]})[0]
    assert r["verdict"] == "Beats the usual rate" and r["_verdict_class"] == TXT_POS
    r = page.rows({"models": [model(verdict="does_not_beat")]})[0]
    assert r["verdict"] == "Does not beat the usual rate" and r["_verdict_class"] == TXT_NEG
    assert set(page.VERDICT_CLASSES) == set(page.VERDICT_WORDS)


def test_too_few_days_counts_toward_the_floor():
    m = model(verdict="too_few_days", days=19)
    assert page.verdict_text(m) == "Too few days (19 of 20)"
    assert page.rows({"models": [m]})[0]["_verdict_class"] == MUTED


def test_brier_prints_three_decimals():
    r = page.rows({"models": [model()]})[0]
    assert r["brier"] == "0.241" and r["usual"] == "0.256"
    assert r["diff"] == "-0.014 (-0.021 to -0.004)"
    assert page.brier(None) == page._fmt.NO_READING
    assert page.diff_text(None) == page._fmt.NO_READING


def test_history_row_is_labelled_as_the_refits():
    h = {"verdict": "does_not_beat", "brier": 0.277, "brier_usual": 0.243,
         "weeks": 32, "days": 186, "beat_trades": 0, "trades": 4}
    out = page.rows({"models": [model(history=h)]})
    assert [r["model"] for r in out] == ["08:36 scalp chance", "Weekly refit history"]
    assert out[1]["verdict"] == "Does not beat the usual rate (0 of 4 trades)"
    assert out[1]["days"] == "186 (32 weeks)"
    assert out[1]["diff"] == page._fmt.NO_READING


def test_cold_view_and_empty_run_say_different_things():
    assert page.status_text(None) == page.NOT_YET
    assert page.status_text({"data_date": "2026-11-13", "models": []}) == page.NO_MODELS
    assert page.status_text({"data_date": "2026-11-13", "models": [model(), model()]}) \
        == "Results to 2026-11-13 · 2 models"
    assert page.rows(None) == []


def test_drift_summary_and_lines():
    flagged = [{"input": "sentiment", "psi": 1.4, "p": 0.005, "band": "shifted",
                "missing_before": 0.04, "missing_after": 1.0},
               {"input": "slope_5", "psi": 0.14, "p": 0.03, "band": "moved",
                "missing_before": 0.0, "missing_after": 0.0}]
    m = model(drift_status="shifted", drift=flagged)
    assert page.drift_text(m) == "1 shifted, 1 moved"
    assert page.rows({"models": [m]})[0]["_drift_class"] == page.DRIFT_CLASSES["shifted"]
    lines = page.drift_lines(m)
    assert lines[0] == ("sentiment: shifted, stability index 1.40, chance alone 0.5%; "
                        "no reading 4% before, 100% now")
    assert lines[1] == "slope_5: moved, stability index 0.14, chance alone 3.0%"
    assert page.drift_text(model()) == "Steady"
    assert page.drift_text(model(drift_status="too_little_history")) == "Too little history"


def test_reliability_lists_only_filled_slices():
    assert page.reliability_lines(model()) == [
        "30% to 40%: said 36%, happened 31% (212 rows)"]
