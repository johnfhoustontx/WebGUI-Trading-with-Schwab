"""The Opening Scalp view contract (cache:options:scalp).

The service validates its payload against ScalpView before publishing, and an
invalid payload is not published. The probabilities it carries are measured win
rates, so a value outside [0, 1], a NaN, an infinity or a bool is refused: a rate
that is not known is None ("not measured"), never a number that reads as a real one.
"""
import pytest
from pydantic import ValidationError

from shared.contracts.options import ScalpView

ROLE = {"structure": "IC", "p": 0.62, "lo": 0.55, "hi": 0.69, "n": 140,
        "p_base": 0.58, "n_base": 900, "edge": 0.04, "strikes": [5400, 5410],
        "entry_value": 1.25, "win_points": 0.6, "loss_points": 1.2}
SYMBOL = {"direction": 1, "bucket": "AM", "alignment": 0.5, "inputs": {},
          "agree": {}, "roles": {"primary": ROLE}, "chain": None,
          "highlight": None, "note": None}
PAYLOAD = {"day": "2026-10-16", "generated_at": "2026-10-16T08:36:00-05:00",
           "state": "call", "symbols": {"$SPX": SYMBOL}, "context": {}, "scorecard": {}}


def _with_role(**fields):
    """A payload whose one role carries ``fields``; a value of None drops the key."""
    role = dict(ROLE, **fields)
    role = {k: v for k, v in role.items() if v is not None or k not in fields}
    return dict(PAYLOAD, symbols={"$SPX": dict(SYMBOL, roles={"primary": role})})


def test_minimal_payload_validates():
    ScalpView()
    ScalpView(**PAYLOAD)
    # A symbol with no roles yet, and a role that names only its structure.
    ScalpView(symbols={"$SPX": {"direction": None}})
    ScalpView(symbols={"SPY": {"roles": {"primary": {"structure": "IC"}}}})
    assert ScalpView().state == "waiting"


def test_probability_outside_unit_interval_refused():
    for field in ("p", "lo", "hi", "p_base"):
        for bad in (1.2, -0.1):
            with pytest.raises(ValidationError):
                ScalpView(**_with_role(**{field: bad}))


def test_probability_on_the_unit_interval_is_valid():
    for field in ("p", "lo", "hi", "p_base"):
        for good in (0.0, 1.0, 0, 1):
            ScalpView(**_with_role(**{field: good}))


def test_nan_refused():
    for field in ("p", "lo", "hi", "p_base"):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValidationError):
                ScalpView(**_with_role(**{field: bad}))
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            ScalpView(**dict(PAYLOAD, symbols={"$SPX": dict(SYMBOL, alignment=bad)}))


def test_bool_is_not_a_probability():
    for field in ("p", "lo", "hi", "p_base"):
        for bad in (True, False):
            with pytest.raises(ValidationError):
                ScalpView(**_with_role(**{field: bad}))
    with pytest.raises(ValidationError):
        ScalpView(**dict(PAYLOAD, symbols={"$SPX": dict(SYMBOL, alignment=True)}))


def test_absent_probability_is_fine():
    ScalpView(**_with_role(p=None, lo=None, hi=None, p_base=None))
    ScalpView(**_with_role(p=None))  # the key present, the value not known
    ScalpView(**_with_role(p=0.5, lo=None, hi=None, p_base=None))


def test_missing_role_field_is_accepted():
    role_without_p = {k: v for k, v in ROLE.items() if k != "p"}
    ScalpView(**dict(PAYLOAD, symbols={"$SPX": dict(SYMBOL, roles={"primary": role_without_p})}))


def test_unknown_state_refused():
    for bad in ("open", "CALL", "", None):
        with pytest.raises(ValidationError):
            ScalpView(state=bad)
    for good in ("call", "waiting", "closed", "error"):
        assert ScalpView(state=good).state == good


def test_extra_keys_are_kept():
    # The service publishes the payload it validated, so a key the model does
    # not name must survive the round trip (the same rule as the money views).
    assert ScalpView(**PAYLOAD, horizon="60m").model_dump()["horizon"] == "60m"
