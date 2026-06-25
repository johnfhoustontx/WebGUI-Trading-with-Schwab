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


# ---------------------------------------------------------------------------
# Task 3.2 — build_messages + system prompt
# ---------------------------------------------------------------------------
def test_build_messages_includes_menu_and_target():
    packet = {"target": 500, "gap_to_target": 380, "menu": [{"id": "m0", "symbol": "QQQ"}],
              "vix": 14.2, "open_positions": [], "limits": {"per_trade_max_risk": 300}}
    msgs = decider.build_messages(packet)
    blob = str(msgs)
    assert "m0" in blob and "500" in blob and "380" in blob


def test_build_messages_shape_is_single_user_turn():
    """A list with exactly one user message embedding the packet JSON."""
    msgs = decider.build_messages({"menu": [{"id": "m0"}], "target": 500})
    assert isinstance(msgs, list) and len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "m0" in msgs[0]["content"] and "500" in msgs[0]["content"]


def test_build_messages_is_json_safe_with_odd_types():
    """Non-JSON-native packet values (e.g. a set) must not blow up serialization."""
    packet = {"menu": [{"id": "m0", "weird": {1, 2, 3}}], "target": 500}
    msgs = decider.build_messages(packet)   # must not raise
    assert msgs[0]["role"] == "user" and "m0" in msgs[0]["content"]


def test_system_prompt_states_the_mandate():
    sp = decider.system_prompt().lower()
    assert "menu" in sp                                  # pick only from the menu
    assert "stand" in sp and "down" in sp                # standing down is valid
    assert "ceiling" in sp or "clamp" in sp              # quantities are ceilings
    assert "target" in sp                                # the daily target
    assert "submit_decision" in decider.system_prompt()  # call the tool (exact name)


# ---------------------------------------------------------------------------
# Task 3.3 — decide (the Anthropic call, client injected/mocked)
# ---------------------------------------------------------------------------
def _block(btype="tool_use", name="submit_decision", inp=None):
    """A minimal anthropic-style content block (duck-typed: .type/.name/.input)."""
    return type("B", (), {"type": btype, "name": name, "input": inp})()


class _FakeClient:
    """Mimics ``anthropic.Anthropic``: ``.messages.create(**kw)`` → an object whose
    ``.content`` is a list of content blocks (one ``tool_use`` carrying the input)."""

    def __init__(self, tool_input):
        self._ti = tool_input
        self.last_kwargs = None

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.last_kwargs = kw
        return type("R", (), {"content": [_block(inp=self._ti)]})()


class _RaisingClient:
    """A client whose ``messages.create`` raises — simulates an API/network error."""

    @property
    def messages(self):
        return self

    def create(self, **kw):
        raise RuntimeError("boom from the API")


def test_decide_extracts_tool_input():
    client = _FakeClient({"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    d = decider.decide({"menu": [{"id": "m0"}]}, client=client)
    assert d["stand_down"] is False and d["trades"][0]["id"] == "m0"


def test_decide_no_client_stands_down():
    # no api key / no client → stand-down, never raises
    d = decider.decide({"menu": []}, client=None, _force_no_key=True)
    assert d["stand_down"] is True and d["trades"] == []


def test_decide_passes_model_tool_and_choice():
    """The call uses settings.MODEL, forces submit_decision, and sends the system prompt."""
    client = _FakeClient({"stand_down": True, "trades": []})
    decider.decide({"menu": [{"id": "m0"}], "target": 500}, client=client)
    kw = client.last_kwargs
    assert kw["model"] == decider.settings.MODEL
    assert kw["max_tokens"] == decider.settings.MAX_TOKENS
    assert kw["tool_choice"] == {"type": "tool", "name": "submit_decision"}
    assert kw["tools"] == [decider.DECISION_TOOL]
    assert kw["system"] == decider.system_prompt()
    assert kw["messages"][0]["role"] == "user"


def test_decide_api_error_stands_down():
    """ANY exception from the client → stand-down (decide never raises)."""
    d = decider.decide({"menu": [{"id": "m0"}]}, client=_RaisingClient())
    assert d["stand_down"] is True and d["trades"] == []


def test_decide_malformed_tool_input_stands_down():
    """A tool_use block whose input isn't a usable dict → stand-down."""
    d = decider.decide({"menu": []}, client=_FakeClient("not-a-dict"))
    assert d["stand_down"] is True and d["trades"] == []


def test_decide_no_tool_use_block_stands_down():
    """A response with only a text block (no tool_use) → stand-down."""

    class _TextOnly:
        @property
        def messages(self):
            return self

        def create(self, **kw):
            return type("R", (), {"content": [_block(btype="text", name="", inp=None)]})()

    d = decider.decide({"menu": []}, client=_TextOnly())
    assert d["stand_down"] is True and d["trades"] == []


def test_decide_ignores_wrong_named_tool_block():
    """A tool_use block with the wrong name is not treated as the decision."""

    class _WrongTool:
        @property
        def messages(self):
            return self

        def create(self, **kw):
            blk = _block(name="something_else",
                         inp={"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
            return type("R", (), {"content": [blk]})()

    d = decider.decide({"menu": []}, client=_WrongTool())
    assert d["stand_down"] is True and d["trades"] == []


def test_decide_empty_or_none_content_stands_down():
    """A response with no/None content → stand-down (no crash on the loop)."""

    class _Empty:
        @property
        def messages(self):
            return self

        def create(self, **kw):
            return type("R", (), {"content": None})()

    assert decider.decide({"menu": []}, client=_Empty())["stand_down"] is True
