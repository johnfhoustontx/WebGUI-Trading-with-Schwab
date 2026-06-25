"""Tests for driver_svc.decider — the LLM decision layer (Claude tool-use).

The decider is deliberately UNTRUSTED: it proposes trades for the guardrails to
then validate/clamp, and its entire failure surface collapses to "stand down" so
a broken or missing model can never cause a bad trade — it just causes no trade.
These tests pin that contract at three levels:

* ``parse_decision`` — robustly normalizes whatever the model returns, and every
  malformed shape (non-dict, trades-not-a-list, id-less / non-dict trade items,
  non-int quantity) degrades to a clean stand-down rather than raising.
* ``build_messages`` / ``system_prompt`` — the packet is embedded for the model
  and the mandate is stated.
* ``decide`` — the Anthropic tool-use call, driven by an INJECTED fake client
  (no network); ANY failure (no key, no client, API error, malformed output)
  returns a stand-down. There are no real API calls in this module.

Grows per task (3.1 → 3.3).
"""
from services.driver_svc import decider


# ---------------------------------------------------------------------------
# Task 3.1 — parse_decision (robust, malformed → stand-down)
# ---------------------------------------------------------------------------
def test_parse_valid():
    raw = {"stand_down": False, "day_thesis": "bull", "confidence": 0.7,
           "trades": [{"id": "m0", "quantity": 2, "rationale": "high pop"}]}
    d = decider.parse_decision(raw)
    assert d["stand_down"] is False and d["trades"][0]["id"] == "m0"
    assert d["trades"][0]["quantity"] == 2 and d["trades"][0]["rationale"] == "high pop"
    assert d["day_thesis"] == "bull" and d["confidence"] == 0.7


def test_parse_malformed_falls_back_to_stand_down():
    assert decider.parse_decision(None)["stand_down"] is True
    assert decider.parse_decision({"trades": "nope"})["stand_down"] is True
    assert decider.parse_decision({"trades": [{"quantity": 1}]})["trades"] == []  # drop id-less


def test_parse_non_dict_inputs_stand_down():
    """A list / string / int / empty — anything not a dict — stands down cleanly."""
    for bad in ([], "submit", 42, 0.0, ("a",), set()):
        d = decider.parse_decision(bad)
        assert d == {"stand_down": True, "day_thesis": "", "confidence": 0.0, "trades": []}


def test_parse_trades_not_a_list_yields_empty_trades():
    """trades present but not a list → no trades, and (no clean trades) → stand down."""
    for bad_trades in ("x", 5, {"id": "m0"}, None):
        d = decider.parse_decision({"stand_down": False, "trades": bad_trades})
        assert d["trades"] == [] and d["stand_down"] is True


def test_parse_drops_non_dict_and_idless_trade_items():
    """Non-dict items and dicts lacking an id are dropped; valid ones survive."""
    raw = {"stand_down": False, "trades": [
        "not-a-dict", 7, None,                      # non-dict items → dropped
        {"quantity": 3},                            # no id → dropped
        {"id": "", "quantity": 1},                  # falsy id → dropped
        {"id": "m1", "quantity": 2},                # keeps
    ]}
    d = decider.parse_decision(raw)
    assert [t["id"] for t in d["trades"]] == ["m1"]
    assert d["trades"][0]["quantity"] == 2


def test_parse_coerces_types():
    """id→str, quantity→int (defaulting/falling back to 1), rationale→str; never raises."""
    raw = {"trades": [
        {"id": 99, "quantity": "4", "rationale": None},   # numeric-string qty, int id
        {"id": "m2"},                                      # missing qty → 1
        {"id": "m3", "quantity": 0},                       # falsy qty → 1
        {"id": "m4", "quantity": "bad"},                   # unparseable qty → 1
        {"id": "m5", "quantity": 2.9},                     # float qty → int (2)
    ]}
    d = decider.parse_decision(raw)
    got = [(t["id"], t["quantity"], t["rationale"]) for t in d["trades"]]
    assert got == [("99", 4, ""), ("m2", 1, ""), ("m3", 1, ""), ("m4", 1, ""), ("m5", 2, "")]
    assert all(isinstance(t["id"], str) for t in d["trades"])


def test_parse_stand_down_defaults_true_when_no_trades():
    """With no usable trades and no explicit stand_down, default to standing down."""
    assert decider.parse_decision({})["stand_down"] is True
    assert decider.parse_decision({"trades": []})["stand_down"] is True


def test_parse_stand_down_explicit_true_even_with_trades():
    """An explicit stand_down=True is honored even if the model also listed trades."""
    raw = {"stand_down": True, "trades": [{"id": "m0", "quantity": 1}]}
    d = decider.parse_decision(raw)
    assert d["stand_down"] is True and d["trades"][0]["id"] == "m0"


def test_parse_bad_scalars_never_raise():
    """Non-numeric confidence / non-string thesis degrade rather than crash."""
    d = decider.parse_decision({"stand_down": False, "confidence": "nan-ish",
                                "day_thesis": 123, "trades": [{"id": "m0"}]})
    assert d["confidence"] == 0.0          # unparseable → 0.0
    assert d["day_thesis"] == "123"        # coerced to str
    assert d["trades"][0]["id"] == "m0"
