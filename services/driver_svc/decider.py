"""The decision layer: build the prompt, call Claude (tool-use), parse the result.

This is the LLM half of the autonomous driver. ``guardrails.py`` is the
code-authoritative safety core; THIS module is the (deliberately untrusted)
decision engine that *proposes* trades for the guardrails to then validate and
clamp. Because the model is never trusted, the entire failure surface of this
module collapses to a single safe outcome — **stand down** — so a missing key, a
network error, a malformed tool payload, or a hallucinated trade can never cause
a bad trade; it just causes no trade.

Three pieces:

* ``parse_decision`` — robustly normalize whatever the model returned into the
  canonical ``{stand_down, day_thesis, confidence, trades:[{id, quantity,
  rationale}]}`` shape. Tolerant of every malformed input (non-dict, trades not a
  list, id-less or non-dict trade items, non-numeric quantity/confidence): a bad
  shape degrades to a stand-down, it never raises.
* ``build_messages`` / ``system_prompt`` — embed the decision packet and state the
  mandate (strategy-agnostic; pick ONLY from the menu; quantities are ceilings the
  code re-clamps; standing down is a valid, encouraged decision; prefer high
  composite score / PoP; call the tool exactly once).
* ``decide`` — the single-shot Anthropic tool-use call. The client is injected (a
  fake in tests, the real ``anthropic.Anthropic`` in production), and ANY failure
  returns ``parse_decision(None)`` (a stand-down) — ``decide`` never raises.

The ``anthropic`` import is intentionally LAZY (inside ``_make_client``) so the
test suite can import this module without the SDK installed and drive ``decide``
with an injected fake client. See @claude-api for the exact ``messages.create`` /
tool-use surface this mirrors.
"""
import json

from services.driver_svc import api_keys, settings

# The tool the model must call exactly once. ``tool_choice`` (in ``decide``) forces
# this tool, so the model's whole reply is a single ``submit_decision`` tool_use
# block whose ``input`` ``parse_decision`` then normalizes. The schema documents
# the shape for the model; CODE (not the schema) is the real safety boundary.
DECISION_TOOL = {
    "name": "submit_decision",
    "description": "Submit the trade decision for this checkpoint.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stand_down": {"type": "boolean"},
            "day_thesis": {"type": "string"},
            "confidence": {"type": "number"},
            "trades": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "menu id from the packet"},
                        "quantity": {"type": "integer", "minimum": 1},
                        "rationale": {"type": "string"},
                    },
                    "required": ["id", "quantity"],
                },
            },
        },
        "required": ["stand_down", "trades"],
    },
}


def _coerce_qty(value) -> int:
    """A proposed quantity → a positive int, defaulting to 1; never raises.

    The model's quantity is only a CEILING (the guardrails re-clamp it), so a
    missing / falsy / unparseable / float value defaults to ``1`` rather than
    rejecting the trade here. ``"4"`` → 4, ``2.9`` → 2, ``0`` / ``None`` /
    ``"bad"`` → 1. Anything ``< 1`` floors to 1.
    """
    try:
        q = int(float(value))
    except (TypeError, ValueError):
        return 1
    return q if q >= 1 else 1


def _coerce_float(value, default=0.0) -> float:
    """Best-effort float; ``default`` (0.0) on anything unparseable. Never raises."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_decision(raw) -> dict:
    """Normalize the model's tool output to the canonical decision dict.

    Returns ``{stand_down: bool, day_thesis: str, confidence: float,
    trades: [{id: str, quantity: int, rationale: str}]}``. Every malformed shape
    degrades safely (never raises):

    * ``raw`` not a dict (``None`` / list / string / int) → a stand-down.
    * ``trades`` not a list → no trades.
    * trade items that aren't dicts, or lack a truthy ``id`` → dropped.
    * ``quantity`` / ``confidence`` non-numeric → coerced (see ``_coerce_*``).

    ``stand_down`` defaults to ``True`` when no usable trades survived — so a
    "nothing actionable" reply (or any malformed one) results in no trade.
    """
    if not isinstance(raw, dict):
        return {"stand_down": True, "day_thesis": "", "confidence": 0.0, "trades": []}
    trades = raw.get("trades")
    clean = []
    if isinstance(trades, list):
        for t in trades:
            if isinstance(t, dict) and t.get("id"):
                clean.append({
                    "id": str(t["id"]),
                    "quantity": _coerce_qty(t.get("quantity", 1)),
                    "rationale": str(t.get("rationale", "") or ""),
                })
    # Stand down if the model said so OR nothing actionable survived parsing — an
    # explicit ``stand_down=False`` with zero clean trades is a contradictory /
    # malformed decision, so we take the safe outcome (no trade) rather than
    # honoring "don't stand down" when there is nothing to execute.
    stand_down = bool(raw.get("stand_down", False)) or not clean
    return {
        "stand_down": stand_down,
        "day_thesis": str(raw.get("day_thesis", "") or ""),
        "confidence": _coerce_float(raw.get("confidence", 0.0)),
        "trades": clean,
    }


# The mandate. Strategy-agnostic: the model never invents strikes or structures —
# it can only pick menu ids the scanner already produced and scored. The
# quantities it gives are CEILINGS the code re-clamps to the risk budget, and
# standing down on a poor-edge checkpoint is an explicitly correct, encouraged
# decision (the daily target is a target, NOT a quota to force-fill).
_SYSTEM = (
    "You are the decision engine for an autonomous PAPER options trader. Your job: "
    "choose 0+ defined-risk credit spreads FROM THE PROVIDED MENU to move toward the "
    "daily net target, or stand down. You may ONLY pick menu ids; never invent trades. "
    "Quantities you give are ceilings — code re-clamps to the risk budget. The target is "
    "a target, NOT a quota: standing down on a poor-edge checkpoint is a correct, "
    "encouraged decision. Prefer high composite_score and PoP; avoid over-concentration. "
    "Call submit_decision exactly once."
)


def build_messages(packet) -> list:
    """The ``messages`` payload: one user turn embedding the decision packet JSON.

    ``default=str`` keeps serialization total — any non-JSON-native value in the
    packet (e.g. a stray set/datetime) is stringified rather than raising, so the
    call can never fail to build. The packet is the model's entire view of the
    world (target, gap, menu, VIX, open positions, limits).
    """
    return [{"role": "user",
             "content": "Decision packet (JSON):\n" + json.dumps(packet, default=str)}]


def system_prompt() -> str:
    """The system prompt stating the decision mandate (see ``_SYSTEM``)."""
    return _SYSTEM


def _make_client():
    """Build a real ``anthropic.Anthropic`` client, or ``None`` if no key.

    The ``anthropic`` import is LAZY (here, not at module top) so the test suite
    can import this module and drive ``decide`` with an injected fake client
    without the SDK installed. Returns ``None`` when the API key is unset — the
    caller then stands down rather than calling a non-existent client.
    """
    key = api_keys.anthropic_api_key()
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key)


def decide(packet, client=None, _force_no_key=False) -> dict:
    """Ask Claude for a decision via a single forced tool-use call.

    A one-shot ``messages.create`` that forces the ``submit_decision`` tool
    (``tool_choice``), then parses the tool_use block's ``input`` through
    ``parse_decision``. The whole body is wrapped so that ANY failure degrades to
    a stand-down and ``decide`` NEVER raises:

    * ``_force_no_key`` (test hook) or no resolvable API key / no client → stand down,
    * an API / network error from ``messages.create`` → stand down,
    * a response with no ``submit_decision`` tool_use block (text-only, wrong tool
      name, empty/``None`` content) → stand down,
    * a malformed tool ``input`` → ``parse_decision`` normalizes it (→ stand down).

    ``client`` is injected (a fake in tests, the real SDK client in production); when
    omitted it is built via ``_make_client``. See @claude-api for the SDK surface.
    """
    try:
        if _force_no_key:
            return parse_decision(None)
        client = client or _make_client()
        if client is None:
            return parse_decision(None)
        resp = client.messages.create(
            model=settings.MODEL,
            max_tokens=settings.MAX_TOKENS,
            system=system_prompt(),
            tools=[DECISION_TOOL],
            tool_choice={"type": "tool", "name": "submit_decision"},
            messages=build_messages(packet),
        )
        for block in getattr(resp, "content", None) or []:
            if (getattr(block, "type", None) == "tool_use"
                    and getattr(block, "name", "") == "submit_decision"):
                return parse_decision(getattr(block, "input", None))
        return parse_decision(None)
    except Exception:  # noqa: BLE001 — the decider is untrusted; degrade to stand down.
        return parse_decision(None)
