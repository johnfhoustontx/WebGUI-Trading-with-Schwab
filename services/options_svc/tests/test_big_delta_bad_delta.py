"""One NaN delta must not silence a symbol's big-delta alerts (audit AC-52).

``gross += nan`` made the total NaN, and every ``>= rel * gross`` comparison is
False against a NaN, so the detector returned nothing for the whole symbol."""
from services.options_svc import flow_alerts

CFG = {"big_delta": {"rel_threshold": 0.20, "min_contract_notional": 1_000,
                     "delta_lo": 0.05, "delta_hi": 0.85, "delta_max": 1.0,
                     "top_n": 3}}


def _chain(extra):
    calls = {"100.0": [{"delta": 0.50, "totalVolume": 5000, "mark": 2.0}]}
    calls.update(extra)
    return {"underlyingPrice": 100.0,
            "callExpDateMap": {"2026-10-16:12": calls}, "putExpDateMap": {}}


def test_the_alert_fires_on_a_clean_chain():
    out = flow_alerts.detect_big_delta("XYZ", _chain({}), CFG)
    assert [a["strike"] for a in out] == [100.0]


def test_a_nan_delta_on_another_contract_does_not_silence_it():
    bad = {"105.0": [{"delta": float("nan"), "totalVolume": 10, "mark": 1.0}]}
    out = flow_alerts.detect_big_delta("XYZ", _chain(bad), CFG)
    assert [a["strike"] for a in out] == [100.0]


def test_a_placeholder_or_text_delta_is_skipped_too():
    bad = {"105.0": [{"delta": -999.0, "totalVolume": 10, "mark": 1.0}],
           "110.0": [{"delta": "n/a", "totalVolume": 10, "mark": 1.0}]}
    out = flow_alerts.detect_big_delta("XYZ", _chain(bad), CFG)
    assert [a["strike"] for a in out] == [100.0]
