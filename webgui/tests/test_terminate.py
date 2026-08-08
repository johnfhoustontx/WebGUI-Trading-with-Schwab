"""Tests for the Terminate page pure builder (webgui/pages/terminate.py)."""
import inspect

from pages import terminate


def test_stop_command_is_detached_start_of_the_bat():
    cmd = terminate.stop_command()
    # cmd /c start <title> <bat>  → fully detached, own console.
    assert cmd[:3] == ["cmd", "/c", "start"]
    assert cmd[-1].endswith("stop_all.bat")


def test_stop_command_targets_the_repo_root_bat():
    assert terminate.STOP_BAT.name == "stop_all.bat"
    assert str(terminate.STOP_BAT) == terminate.stop_command()[-1]


# --- copy honesty -------------------------------------------------------------
# The page's copy lives inline in render(), so these read the source — the same
# idiom test_shell.py and test_status.py use for wiring that can't be called
# under pytest. The BEHAVIOUR is already right: tools/stop_all.py drops the proxy
# unless OWNS_PROXY. Only the promise was wrong.
def test_copy_does_not_promise_to_stop_a_proxy_this_checkout_may_not_own():
    """Dev borrows prod's proxy on :8100 and stop_all leaves it alone. Copy that
    flatly promises to kill it is wrong there — and wrong copy about a
    destructive action either scares an operator off a button they're entitled
    to press, or makes them mistrust the result when the proxy survives."""
    src = inspect.getsource(terminate)
    assert "Stops the schwab-proxy" not in src
    assert "The proxy, all services" not in src
    assert "proxy + the six domain services" not in src   # the module docstring


def test_copy_states_that_stopping_the_proxy_is_ownership_conditional():
    """One wording that is true in BOTH environments, not dev-specific prose."""
    src = inspect.getsource(terminate)
    assert src.count("only in the environment that owns it") >= 2
    # Memurai's "left running" note is unrelated to ownership and must survive.
    assert "Memurai" in src
