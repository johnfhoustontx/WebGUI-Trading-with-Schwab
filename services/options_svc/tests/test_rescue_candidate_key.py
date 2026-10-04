"""What a rescue candidate IS, apart from its prices (audit AR-08).

``rescue_apply`` used to apply whatever candidate the page echoed back. The
service now applies its OWN candidate from the menu it published, found by this
identity: the action and the contracts it trades.
"""
import pytest

from services.options_svc import rescue


def _cand(**over):
    base = {"action": "roll_down", "new_expiry": "2026-10-09", "net_cash": -131.3,
            "est_fill_legs": [
                {"side": "BUY", "right": "PUT", "strike": 495.0,
                 "expiry": "2026-10-09", "qty": 1, "price": 2.4},
                {"side": "SELL", "right": "PUT", "strike": 490.0,
                 "expiry": "2026-10-09", "qty": 1, "price": 1.1}]}
    base.update(over)
    return base


def _legs(**change):
    return [dict(leg, **change) for leg in _cand()["est_fill_legs"]]


def test_a_candidates_identity_ignores_its_prices_and_the_order_of_its_legs():
    a = _cand()
    b = _cand(net_cash=5.0, commission=9.0, new_max_loss=1.0,
              est_fill_legs=[dict(a["est_fill_legs"][1], price=0.5),
                             dict(a["est_fill_legs"][0], price=9.9)])
    assert rescue.candidate_key(a) == rescue.candidate_key(b)


@pytest.mark.parametrize("change", [
    {"action": "roll_out"},
    {"est_fill_legs": _legs()[:1]},
    {"est_fill_legs": [_legs()[0], dict(_legs()[1], strike=485.0)]},
    {"est_fill_legs": _legs(qty=2)},
    {"est_fill_legs": _legs(expiry="2026-10-16")},
    {"est_fill_legs": _legs(right="CALL")},
    {"est_fill_legs": [dict(_legs()[0], side="SELL"), dict(_legs()[1], side="BUY")]},
])
def test_a_different_action_or_contract_is_a_different_candidate(change):
    assert rescue.candidate_key(_cand()) != rescue.candidate_key(_cand(**change))


def test_a_strike_sent_as_an_int_is_the_same_strike():
    legs = [dict(leg, strike=int(leg["strike"])) for leg in _cand()["est_fill_legs"]]
    assert rescue.candidate_key(_cand()) == rescue.candidate_key(
        _cand(est_fill_legs=legs))


@pytest.mark.parametrize("junk", [
    None, "close", 7, [], {}, {"action": None}, {"action": ""},
    {"action": "close", "est_fill_legs": "x"},
    {"action": "close", "est_fill_legs": [5]},
    {"action": "close", "est_fill_legs": [{"side": "BUY", "strike": float("nan")}]},
])
def test_something_that_is_not_a_candidate_has_no_identity(junk):
    assert rescue.candidate_key(junk) is None


def test_a_candidate_with_no_legs_is_known_by_its_action():
    assert rescue.candidate_key({"action": "close"}) == rescue.candidate_key(
        {"action": "close", "est_fill_legs": [], "net_cash": 12.0})
    assert rescue.candidate_key({"action": "close"}) != rescue.candidate_key(
        {"action": "partial_close"})


def test_find_candidate_returns_the_menus_own_dict():
    ours = _cand()
    menu = {"candidates": [{"action": "close"}, ours]}
    assert rescue.find_candidate(menu, _cand(net_cash=1e9)) is ours
    assert rescue.find_candidate(menu, _cand(action="roll_out")) is None
    assert rescue.find_candidate(None, ours) is None
    assert rescue.find_candidate({"candidates": "x"}, ours) is None
    assert rescue.find_candidate(menu, None) is None


def test_two_menu_rows_with_one_identity_are_not_guessed_between():
    # It should never happen; if it does, neither is applied.
    menu = {"candidates": [_cand(net_cash=1.0), _cand(net_cash=2.0)]}
    assert rescue.find_candidate(menu, _cand()) is None
