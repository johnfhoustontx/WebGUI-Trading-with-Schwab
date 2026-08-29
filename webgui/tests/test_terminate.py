"""Tests for the Terminate page pure builder (webgui/pages/terminate.py)."""
import inspect

from pages import terminate


def test_stop_command_stops_this_environments_target():
    from repo_paths import ENV_NAME
    assert terminate.stop_command() == [
        "systemctl", "--user", "--no-block", "stop", f"trading-{ENV_NAME}.target"]


def test_stop_command_is_no_block_and_that_is_load_bearing():
    """`--no-block` registers the stop job with the systemd MANAGER and returns.

    This page kills the web app that issued the command. With --no-block the job
    is owned by systemd, so this process dying partway cannot orphan the
    shutdown -- which is strictly safer than the old `cmd /c start` detachment
    trick it replaces, where an independent console was the only thing keeping
    the batch alive."""
    assert "--no-block" in terminate.stop_command()


def test_stop_command_is_user_scoped():
    """A system-scoped stop would need root. The whole supervision design rests
    on `systemctl --user`."""
    assert terminate.stop_command()[:2] == ["systemctl", "--user"]


def test_no_windows_machinery_survives_anywhere_in_the_module():
    src = inspect.getsource(terminate)
    for banned in ("stop_all.bat", "STOP_BAT", "cmd /c", "taskkill",
                   "start_all_wt.bat", "start_all.bat"):
        assert banned not in src, banned


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
    # The bus's "left running" note is unrelated to ownership and must survive.
    # It is REDIS on Linux, not Memurai -- the Windows port is gone, and the
    # reason it survives a stop is now structural: it is a SYSTEM unit, so a
    # `systemctl --user` target stop cannot reach it even in principle.
    assert "Redis" in src
    assert "Memurai" not in src
