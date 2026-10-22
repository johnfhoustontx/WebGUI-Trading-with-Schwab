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


# ── the intraday section (every 15 minutes, cache:options:scalp) ─────────────

INTRADAY_ENTRY = {"direction": 1, "bucket": "AM", "roles": {"primary": ROLE},
                  "confirmation": {"score": 0.5, "verdict": "confirms"}}
INTRADAY = {"slot": "09:06", "latest": {"SPY": INTRADAY_ENTRY},
            "timeline": [{"t": "09:06", "SPY": {"direction": 1, "p": 0.6, "p_base": 0.58}}]}


def _with_intraday(intraday):
    return dict(PAYLOAD, intraday=intraday)


def _with_latest_role(**fields):
    """An intraday payload whose one latest role carries ``fields``; None drops the key."""
    role = dict(ROLE, **fields)
    role = {k: v for k, v in role.items() if v is not None or k not in fields}
    entry = dict(INTRADAY_ENTRY, roles={"primary": role})
    return _with_intraday(dict(INTRADAY, latest={"SPY": entry}))


def test_intraday_defaults_to_empty_and_old_payload_validates():
    # A payload written before the section existed still validates, and reads as "none".
    assert ScalpView(**PAYLOAD).intraday == {}
    assert ScalpView().intraday == {}


def test_intraday_section_validates():
    assert ScalpView(**_with_intraday(INTRADAY)).intraday["slot"] == "09:06"
    ScalpView(**_with_intraday({"slot": "09:06", "latest": {}, "timeline": []}))
    ScalpView(**_with_intraday({"slot": "09:06", "latest": None, "timeline": None}))


def test_intraday_role_rate_outside_unit_interval_refused():
    for field in ("p", "lo", "hi", "p_base"):
        for bad in (1.2, -0.1):
            with pytest.raises(ValidationError):
                ScalpView(**_with_latest_role(**{field: bad}))


def test_intraday_role_rate_nan_and_bool_refused():
    for field in ("p", "lo", "hi", "p_base"):
        for bad in (float("nan"), float("inf"), True, False):
            with pytest.raises(ValidationError):
                ScalpView(**_with_latest_role(**{field: bad}))


def test_intraday_role_rate_on_unit_interval_or_absent_is_fine():
    for field in ("p", "lo", "hi", "p_base"):
        ScalpView(**_with_latest_role(**{field: 0.0}))
        ScalpView(**_with_latest_role(**{field: 1}))
        ScalpView(**_with_latest_role(**{field: None}))


def test_intraday_timeline_rate_outside_unit_interval_refused():
    for field in ("p", "p_base"):
        for bad in (1.2, -0.1, float("nan"), True):
            row = {"t": "09:06", "SPY": {field: bad}}
            with pytest.raises(ValidationError):
                ScalpView(**_with_intraday(dict(INTRADAY, timeline=[row])))


def test_intraday_timeline_nested_symbols_rates_are_checked():
    # The shape scalp_intraday_call publishes: the cells under "symbols", the
    # highlighted role's rate as edge_p / edge_p_base.
    good = {"slot": "09:06", "symbols": {"SPY": {"direction": 1, "edge_p": 0.6,
                                                 "edge_p_base": 0.5, "word": None},
                                         "QQQ": {"direction": None, "edge_p": None}}}
    ScalpView(**_with_intraday(dict(INTRADAY, timeline=[good])))
    for field in ("p", "p_base", "edge_p", "edge_p_base"):
        for bad in (1.2, -0.1, float("nan"), True):
            row = {"slot": "09:06", "symbols": {"SPY": {field: bad}}}
            with pytest.raises(ValidationError):
                ScalpView(**_with_intraday(dict(INTRADAY, timeline=[row])))
    with pytest.raises(ValidationError):
        ScalpView(**_with_intraday(dict(INTRADAY, timeline=[{"slot": "09:06",
                                                             "symbols": ["SPY"]}])))


def test_intraday_confirmation_score_range():
    for good in (1, -1, 0, 0.0, 1.0, -1.0, None):
        entry = dict(INTRADAY_ENTRY, confirmation={"score": good})
        ScalpView(**_with_intraday(dict(INTRADAY, latest={"SPY": entry})))
    for bad in (1.5, -1.5, float("nan"), float("inf"), True):
        entry = dict(INTRADAY_ENTRY, confirmation={"score": bad})
        with pytest.raises(ValidationError):
            ScalpView(**_with_intraday(dict(INTRADAY, latest={"SPY": entry})))


def test_intraday_timeline_not_a_list_refused():
    # A wrong container type is a ValidationError, never an AttributeError.
    for bad in ({"t": "09:06"}, "09:06", 5):
        with pytest.raises(ValidationError):
            ScalpView(**_with_intraday(dict(INTRADAY, timeline=bad)))


def test_intraday_timeline_row_not_a_mapping_refused():
    with pytest.raises(ValidationError):
        ScalpView(**_with_intraday(dict(INTRADAY, timeline=["09:06"])))


def test_intraday_latest_not_a_mapping_refused():
    for bad in ([INTRADAY_ENTRY], "SPY", 5):
        with pytest.raises(ValidationError):
            ScalpView(**_with_intraday(dict(INTRADAY, latest=bad)))


def test_intraday_latest_entry_not_a_mapping_refused():
    with pytest.raises(ValidationError):
        ScalpView(**_with_intraday(dict(INTRADAY, latest={"SPY": 5})))


def test_intraday_confirmation_not_a_mapping_refused():
    entry = dict(INTRADAY_ENTRY, confirmation=0.5)
    with pytest.raises(ValidationError):
        ScalpView(**_with_intraday(dict(INTRADAY, latest={"SPY": entry})))


# ── the fitted chance of target ──────────────────────────────────────────────
CHANCE = {"version": 3, "fitted_at": "2026-10-21T20:00:00-05:00",
          "by_role": {"long_with": 0.41, "long_against": 0.22, "credit_with": None,
                      "credit_against": 0.0},
          "beats_usual": {}, "pushes": []}


def _chance_payload(chance, role_chance=0.41):
    role = dict(ROLE, chance=role_chance)
    return dict(PAYLOAD, symbols={"$SPX": dict(SYMBOL, roles={"primary": role},
                                               chance=chance)})


def test_scalpview_accepts_a_chance_or_none_or_no_key():
    ScalpView(**_chance_payload(CHANCE))
    ScalpView(**_chance_payload(None, role_chance=None))
    ScalpView(**_chance_payload({"version": None, "fitted_at": None, "by_role": {}}))
    ScalpView(**PAYLOAD)                      # absent: switched off


def test_scalpview_rejects_a_chance_above_one():
    for bad in (1.2, -0.1, float("nan"), True):
        with pytest.raises(ValidationError):
            ScalpView(**_chance_payload(CHANCE, role_chance=bad))
        with pytest.raises(ValidationError):
            ScalpView(**_chance_payload(dict(CHANCE, by_role={"long_with": bad})))


def test_scalpview_rejects_a_malformed_chance():
    for bad in (5, "x", [0.5]):
        with pytest.raises(ValidationError):
            ScalpView(**_chance_payload(bad, role_chance=None))
    with pytest.raises(ValidationError):
        ScalpView(**_chance_payload(dict(CHANCE, by_role=[0.5]), role_chance=None))


def test_intraday_chance_is_checked_on_the_entry_and_its_roles():
    def view(chance, role_chance=0.3):
        role = dict(ROLE, chance=role_chance)
        entry = dict(INTRADAY_ENTRY, roles={"primary": role}, chance=chance)
        return _with_intraday(dict(INTRADAY, latest={"SPY": entry}))
    ScalpView(**view(CHANCE))
    ScalpView(**view(None, role_chance=None))
    for bad in (1.5, -0.2, float("inf"), True):
        with pytest.raises(ValidationError):
            ScalpView(**view(CHANCE, role_chance=bad))
        with pytest.raises(ValidationError):
            ScalpView(**view(dict(CHANCE, by_role={"long_with": bad})))
    with pytest.raises(ValidationError):
        ScalpView(**view(7))


def test_intraday_timeline_chance_is_a_unit_number():
    ok = {"slot": "09:06", "symbols": {"SPY": {"direction": 1, "chance": 0.4},
                                       "QQQ": {"direction": None, "chance": None}}}
    ScalpView(**_with_intraday(dict(INTRADAY, timeline=[ok])))
    for bad in (1.2, -0.1, float("nan"), True):
        row = {"slot": "09:06", "symbols": {"SPY": {"chance": bad}}}
        with pytest.raises(ValidationError):
            ScalpView(**_with_intraday(dict(INTRADAY, timeline=[row])))
