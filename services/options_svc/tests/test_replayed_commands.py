"""A replayed command must not re-mutate the book or re-bill a Claude call.

Consumer groups are created at id ``0``, so the FIRST consume against a stream
holding a backlog delivers the whole history - the documented incident where a
first launch "burned a day's API budget in one go". The service already refuses a
stale ``driver_paper_create``/``paper_create`` via ``_is_stale_open``, but two
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
