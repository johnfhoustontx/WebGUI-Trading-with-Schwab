"""Tests for the "is a stack already running?" pre-flight the launchers call.

Two things are worth pinning here, and they are not the same thing.

**The probe.** It has to answer correctly, and it has to answer defensively: it
sits between the operator and a start, so a check that cannot run must let the
start proceed rather than refuse on evidence it does not have. The port is bound
for real in these tests rather than mocked — a socket is the entire subject, and
a mocked one would pass whether or not ``connect_ex`` were ever called.

**Where the ports come from.** This is the load-bearing one. The tool must read
``stop_all._targets()`` and never carry a list of its own, because that function
is what knows the proxy drops out when ``OWNS_PROXY`` is false. A launcher that
believed it owned :8100 in a dev checkout is precisely the drift that took prod
down: the desktop shortcut pointed at the dev folder, the prod launcher ran there
and bound prod's proxy port while prod never started.

``tools/`` has no ``__init__.py``; the sys.path insert mirrors
tools/tests/test_stop_all.py. Note that importing the tool this way gives a
SECOND ``stop_all`` module object (the tool adds ``tools/`` to sys.path and
imports it flat), so these tests monkeypatch ``check_stack_down._targets``
directly rather than reaching through ``tools.stop_all``.
"""
import inspect
import pathlib
import re
import socket
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import check_stack_down as csd  # noqa: E402
from tools import stop_all  # noqa: E402


@pytest.fixture
def bound_port():
    """A real listening socket on an OS-assigned port.

    Deliberately NOT one of the stack's real ports: probing 8100 or 8500 from a
    test would make the result depend on whether prod happened to be running,
    and would make a green suite mean "prod is down".
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


@pytest.fixture
def free_port():
    """A port that was bound and then released, so nothing is listening on it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


#############################################
# THE PROBE
#############################################

def test_a_listening_port_is_detected(bound_port):
    assert csd.is_listening(bound_port) is True


def test_a_free_port_is_not(free_port):
    assert csd.is_listening(free_port) is False


@pytest.mark.parametrize("port", [None, "", "not-a-port", -1, 999999, object()])
def test_a_nonsense_port_reads_as_free_rather_than_raising(port):
    """Degradation, in the direction chosen in the module docstring: this check
    may cost a duplicate process, it may never cost a start."""
    assert csd.is_listening(port) is False


#############################################
# WHICH TARGETS ARE BUSY
#############################################

def test_busy_targets_reports_the_occupied_one(bound_port, free_port):
    targets = [("webgui", bound_port), ("options_svc", free_port)]
    assert csd.busy_targets(targets) == [("webgui", bound_port)]


def test_busy_targets_is_empty_when_everything_is_free(free_port):
    assert csd.busy_targets([("webgui", free_port)]) == []


def test_no_targets_is_not_an_error():
    assert csd.busy_targets([]) == []


def test_omitting_targets_falls_back_to_stop_alls_list(monkeypatch, free_port):
    """``None`` means "ask stop_all", which is how the launchers call it. Pinned
    with a monkeypatch rather than by letting it run for real: the real list is
    the LIVE stack's ports, and a test that probed those would pass or fail
    depending on whether prod happened to be up."""
    asked = []
    monkeypatch.setattr(csd, "_targets",
                        lambda: asked.append(1) or [("webgui", free_port)])
    assert csd.busy_targets() == []
    assert asked, "busy_targets() ignored stop_all's target list"


@pytest.mark.parametrize("entry", [None, ("only-one",), ("a", "b", "c"), 7])
def test_a_malformed_target_row_never_raises(entry):
    assert isinstance(csd.busy_targets([entry]), list)


#############################################
# THE EXIT CODES — what the launchers branch on
#############################################

def test_main_exits_1_and_names_the_busy_port(monkeypatch, capsys, bound_port):
    monkeypatch.setattr(csd, "_targets", lambda: [("webgui", bound_port)])
    assert csd.main() == 1
    out = capsys.readouterr().out
    assert "webgui" in out
    assert str(bound_port) in out


def test_main_exits_0_when_every_port_is_free(monkeypatch, capsys, free_port):
    """Non-vacuity partner: the refusal must not fire unconditionally, or the
    guard would simply prevent every start."""
    monkeypatch.setattr(csd, "_targets", lambda: [("webgui", free_port)])
    assert csd.main() == 0
    assert "refusing" not in capsys.readouterr().out


def test_main_lets_the_start_proceed_if_the_check_itself_fails(monkeypatch, capsys):
    """A check that cannot run must not stand between the operator and a start.
    The pre-guard behavior was "launch anyway", whose cost is a duplicate
    process; refusing on an answer we never got is the more expensive mistake."""
    def boom():
        raise RuntimeError("no repo_paths for you")
    monkeypatch.setattr(csd, "_targets", boom)
    assert csd.main() == 0
    assert "could not check" in capsys.readouterr().out


#############################################
# THE MESSAGE — which environment is already up
#############################################

def test_the_refusal_names_the_environment_and_the_checkout():
    """With two stacks on one machine "already running" is ambiguous, and the
    answer differs: the dev stack is yours to stop, the prod one is serving live
    market data and stopping it is a decision."""
    text = csd.refusal_text([("webgui", 9500)], env_name="dev", root=r"D:\Dev")
    assert "DEV" in text
    assert r"D:\Dev" in text
    assert "webgui" in text and "9500" in text
    assert "stop_all.bat" in text


def test_the_refusal_says_prod_in_the_prod_checkout():
    assert "PROD" in csd.refusal_text([("proxy", 8100)], env_name="prod")


#############################################
# THE PORT SET COMES FROM stop_all
#############################################

def test_targets_is_stop_alls_own_function():
    """Not a source-text match: compare the CODE OBJECT's file, which is true
    only if the tool really imported stop_all's function rather than defining a
    lookalike. (Identity would fail — the tool imports stop_all flat, so this
    process holds two module objects for it.)"""
    assert pathlib.Path(csd._targets.__code__.co_filename).name == "stop_all.py"
    assert csd._targets.__code__.co_name == stop_all._targets.__code__.co_name


def test_the_tool_carries_no_port_list_of_its_own():
    """The drift this guard exists to prevent, pinned at the source: a port
    literal here would let the starter and the stopper disagree about what this
    environment owns. PROBE_TIMEOUT_SEC and the like are not port-shaped."""
    src = inspect.getsource(csd)
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    # Strip the docstrings; they legitimately discuss :8100.
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    assert not re.findall(r"(?<!\d)(\d{4})(?!\d)", code), (
        "check_stack_down.py names a port number - it must read every port from "
        "stop_all._targets() instead")


def test_it_inherits_the_borrowed_proxy_rule(monkeypatch):
    """The property that makes reusing _targets worth it: in a dev checkout the
    proxy is PROD'S, borrowed, so a listener on :8100 is not a reason to refuse
    a dev start. This follows from the import for free - which is the point."""
    monkeypatch.setattr(stop_all, "OWNS_PROXY", False)
    assert "proxy" not in [label for label, _ in stop_all._targets()]


def test_the_web_gui_is_probed_last_like_it_is_stopped_last():
    """Ordering comes along with the shared list. It costs nothing here, but it
    means a future reader comparing the two never finds them out of step."""
    assert csd.busy_targets is not None
    assert [label for label, _ in stop_all._targets()][-1] == "webgui"
