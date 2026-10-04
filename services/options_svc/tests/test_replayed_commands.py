"""A replayed command must not re-mutate the book or re-bill a Claude call.

Consumer groups are created at id ``0``, so the FIRST consume against a stream
holding a backlog delivers the whole history - the documented incident where a
first launch "burned a day's API budget in one go". The service already refuses a
stale ``paper_create`` via ``_is_stale_open``, but two
side-effectful commands were ungated:

* ``rescue_apply``  -> mutates the paper book. Its only guards are "is the
  position still OPEN" and a 15% price-drift check, and a fast replay passes
  BOTH, re-applying a partial_close or paying a second roll's commission.
* ``gamma_analyze`` -> a PAID Claude call.

⚠ This is an age gate, not true idempotency: two genuinely FRESH duplicate
commands still both run. It closes the replay case using machinery the service
already trusts; a dedup store keyed on the stream id would be the stronger fix.
"""
import datetime as dt

import pytest

from services.options_svc import handlers
from shared.bus import Bus
from shared.contracts.envelope import Command


def _aged(cmd_type, seconds, **args):
    """A command whose enqueue ts is ``seconds`` in the past."""
    ts = (dt.datetime.now(dt.timezone.utc)
          - dt.timedelta(seconds=seconds)).isoformat()
    return Command(type=cmd_type, args=args, ts=ts)


@pytest.fixture
def bus():
    return Bus(fake=True)


@pytest.mark.parametrize("cmd_type,args", [
    ("rescue_apply", {"position_id": 1, "candidate": {"kind": "close"}}),
    ("gamma_analyze", {}),
])
def test_a_stale_side_effect_command_is_refused(bus, monkeypatch, cmd_type, args):
    fired = []
    monkeypatch.setattr(handlers, "run_rescue_apply",
                        lambda *a, **k: fired.append("rescue"))
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda *a, **k: fired.append("analyze") or {})

    handlers.handle_command(
        bus, _aged(cmd_type, handlers.STALE_OPEN_MAX_AGE_SEC + 60, **args))
    assert fired == [], f"{cmd_type} re-executed on a stale (replayed) command"


@pytest.mark.parametrize("cmd_type,args", [
    ("rescue_apply", {"position_id": 1, "candidate": {"kind": "close"}}),
    ("gamma_analyze", {}),
])
def test_a_fresh_command_still_runs(bus, monkeypatch, cmd_type, args):
    """Power check: the gate must not break the normal path."""
    fired = []
    monkeypatch.setattr(handlers, "run_rescue_apply",
                        lambda *a, **k: fired.append("rescue"))
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda *a, **k: fired.append("analyze") or {})
    monkeypatch.setattr(handlers, "_record_gamma_analysis", lambda *a, **k: None,
                        raising=False)

    handlers.handle_command(bus, _aged(cmd_type, 1, **args))
    assert fired, f"{cmd_type} did not run on a fresh command"


def test_a_command_with_no_ts_still_runs(bus, monkeypatch):
    """A legacy command serialized before ``ts`` existed must never be rejected -
    the same back-compat rule ``_is_stale_open`` already documents."""
    fired = []
    monkeypatch.setattr(handlers, "run_rescue_apply",
                        lambda *a, **k: fired.append("rescue"))
    cmd = Command(type="rescue_apply",
                  args={"position_id": 1, "candidate": {"kind": "close"}})
    object.__setattr__(cmd, "ts", None) if hasattr(cmd, "__dataclass_fields__") \
        else setattr(cmd, "ts", None)
    handlers.handle_command(bus, cmd)
    assert fired == ["rescue"]


# --- dossier: a replayed lookup must not re-spend 4-5 Schwab calls -----------

def test_a_stale_dossier_command_does_not_fetch(bus, monkeypatch):
    """Membership in ``_REPLAY_GUARDED`` does nothing by itself - the branch
    has to call ``_is_stale_side_effect``. This fails if that call goes."""
    fired = []
    monkeypatch.setattr(handlers.dossier, "build_dossier",
                        lambda symbol: fired.append(symbol) or {"symbol": symbol})
    handlers.handle_command(
        bus, _aged("dossier", handlers.STALE_OPEN_MAX_AGE_SEC + 60, symbol="MU"))
    assert fired == [], "a replayed dossier command re-spent Schwab calls"
    assert bus.cache_get(handlers.dossier_key("MU")) is None


def test_a_fresh_dossier_command_does_fetch(bus, monkeypatch):
    """Power check for the one above: the branch must actually run."""
    fired = []
    monkeypatch.setattr(handlers.dossier, "build_dossier",
                        lambda symbol: fired.append(symbol) or {"symbol": symbol})
    handlers.handle_command(bus, _aged("dossier", 1, symbol="MU"))
    assert fired == ["MU"]


# --- the guard is applied by the dispatcher, for every listed command ---------
# Until 2026-10-04 each branch of handle_command had to remember to call
# _is_stale_side_effect, and one did not: ``calc_rate`` sat in _REPLAY_GUARDED,
# was documented as replay-guarded, and ran on a replay all the same.

def test_a_stale_calc_rate_command_does_not_run(bus, monkeypatch):
    fired = []
    monkeypatch.setattr(handlers.rate_trade, "rate",
                        lambda *a, **k: fired.append("rate") or {"row": None, "error": "x"})
    handlers.handle_command(bus, _aged(
        "calc_rate", handlers.STALE_OPEN_MAX_AGE_SEC + 60,
        request_id="r1", symbol="SPY", structure="PCS", legs=[]))
    assert fired == [], "a replayed calc_rate re-spent its Schwab calls"
    assert bus.cache_get("cache:options:calc_rating") is None


def test_every_replay_guarded_command_is_refused_before_its_handler(bus, monkeypatch):
    """Structural: membership in _REPLAY_GUARDED is what refuses a stale
    command, whatever its handler does or forgets to do."""
    ran = []
    for name in handlers._REPLAY_GUARDED:
        monkeypatch.setitem(handlers._COMMANDS, name,
                            lambda bus, command, _n=name: ran.append(_n))
    monkeypatch.setattr(handlers.x_post, "record_refusal", lambda *a, **k: None)
    for name in handlers._REPLAY_GUARDED:
        handlers.handle_command(
            bus, _aged(name, handlers.STALE_OPEN_MAX_AGE_SEC + 60))
    assert ran == []
    for name in handlers._REPLAY_GUARDED:
        handlers.handle_command(bus, _aged(name, 1))
    assert ran == list(handlers._REPLAY_GUARDED)


def test_every_replay_guarded_name_is_a_real_command():
    assert set(handlers._REPLAY_GUARDED) <= set(handlers._COMMANDS)


def test_an_unknown_command_is_a_no_op(bus):
    handlers.handle_command(bus, Command(type="no_such_command", args={}))


def test_the_dispatcher_is_a_lookup_not_a_chain():
    """CQ-03: handle_command was a 370-line if/elif chain. A branch added back
    into it would bypass the table the drift tests and the replay guard read."""
    import ast
    import inspect
    fn = ast.parse(inspect.getsource(handlers.handle_command).lstrip()).body[0]
    compares = [n for n in ast.walk(fn) if isinstance(n, ast.Compare)
                and isinstance(n.left, ast.Attribute) and n.left.attr == "type"]
    assert compares == [], "handle_command compares command.type again"
