"""Tests for stop_all's HUD shutdown.

Every other target in stop_all is found by LISTENING PORT, which is
self-limiting: a port identifies exactly one process. The HUD binds no port, so
it has to be found by command line — and that is the dangerous kind of match.

The rule these tests pin was learned expensively. Restarting the HUD with
``taskkill /F /IM pythonw.exe`` took down the entire stack, because the proxy,
all six domain services and the web GUI ALSO run under pythonw.exe. So the
matcher must key on the SCRIPT, never the interpreter — and it must not sweep
up shells or editors that merely mention the script, which is the second trap
the launcher's duplicate guard hit.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import stop_all  # noqa: E402

VENV = r"D:\WebGUI Trading with Schwab\.venv\Scripts"

# One HUD as it really appears: this venv's pythonw is a redirector that
# re-executes the base interpreter, so a single instance is a parent/child pair.
HUD_PARENT = (31432, 38892, "pythonw.exe", f'"{VENV}\\pythonw.exe" tools\\nq_hud.py')
HUD_CHILD = (16188, 31432, "pythonw.exe",
             r'"C:\Users\john_\AppData\Local\Programs\Python\Python311\pythonw.exe" tools\nq_hud.py')

# The eight port-bound processes. All pythonw, none of them the HUD.
SERVICES = [
    (1001, 900, "pythonw.exe", f'"{VENV}\\pythonw.exe" schwab-proxy\\schwab_proxy.py'),
    (1002, 900, "pythonw.exe", f'"{VENV}\\pythonw.exe" services\\options_svc\\app.py'),
    (1003, 900, "pythonw.exe", f'"{VENV}\\pythonw.exe" services\\market_svc\\app.py'),
    (1004, 900, "pythonw.exe", f'"{VENV}\\pythonw.exe" webgui\\main.py'),
]


#############################################
# THE INVARIANT — never touch a service
#############################################

def test_no_service_is_ever_matched():
    """The regression that matters. Killing by interpreter name took the whole
    stack down for ~90 minutes of a live session; the match must key on the
    SCRIPT."""
    assert stop_all.hud_root_pids(SERVICES) == []


def test_the_hud_is_found_among_the_running_services():
    rows = SERVICES + [HUD_PARENT, HUD_CHILD]
    assert stop_all.hud_root_pids(rows) == [HUD_PARENT[0]]


@pytest.mark.parametrize("script", [
    "schwab_proxy.py", "app.py", "main.py", "watchdog.py", "nq_state.py",
    "nq_signal.py", "nq_signal_log.py",
])
def test_no_sibling_script_is_mistaken_for_the_hud(script):
    """nq_signal.py and nq_state.py sit beside nq_hud.py and are imported BY it.
    A loose substring match on "nq_" would take them for the HUD."""
    row = (2001, 900, "pythonw.exe", f'"{VENV}\\pythonw.exe" tools\\{script}')
    assert stop_all.hud_root_pids([row]) == []


#############################################
# NOT-A-PYTHON-PROCESS
#############################################

@pytest.mark.parametrize("name,cmd", [
    ("powershell.exe", "powershell -Command \"Start-Process ... 'tools\\nq_hud.py'\""),
    ("bash.exe", "bash -c 'python tools/nq_hud.py'"),
    ("cmd.exe", r'cmd /c start_all_hidden.bat'),
    ("Code.exe", r'"C:\Code.exe" "D:\repo\tools\nq_hud.py"'),
])
def test_a_shell_or_editor_that_merely_mentions_the_script_is_not_matched(name, cmd):
    """The launcher's duplicate guard hit exactly this: matching on command line
    alone also matched the PowerShell running the check, plus every shell that
    had ever typed the script name. Measured 6 matches where only 2 were real.
    Here the cost would be worse than a no-op — it would kill your terminal.
    """
    assert stop_all.hud_root_pids([(3001, 900, name, cmd)]) == []


@pytest.mark.parametrize("name", ["python.exe", "pythonw.exe", "PYTHONW.EXE",
                                  "python3.11.exe"])
def test_every_python_interpreter_spelling_is_matched(name):
    row = (4001, 900, name, f'"{VENV}\\{name}" tools\\nq_hud.py')
    assert stop_all.hud_root_pids([row]) == [4001]


#############################################
# ROOTS ONLY
#############################################

def test_a_parent_child_pair_collapses_to_one_root():
    """taskkill /T kills the tree, so only roots need killing. Targeting the
    child as well would have the second call report "not found" and read as a
    failure."""
    assert stop_all.hud_root_pids([HUD_PARENT, HUD_CHILD]) == [HUD_PARENT[0]]


def test_the_child_alone_is_still_killed_if_its_parent_is_gone():
    """A HUD whose redirector already exited is still a running HUD."""
    assert stop_all.hud_root_pids([HUD_CHILD]) == [HUD_CHILD[0]]


def test_two_independent_huds_yield_two_roots():
    other = (5001, 900, "pythonw.exe", f'"{VENV}\\pythonw.exe" tools\\nq_hud.py')
    roots = stop_all.hud_root_pids([HUD_PARENT, HUD_CHILD, other])
    assert roots == sorted([HUD_PARENT[0], other[0]])


def test_own_pid_is_never_returned():
    """Cheap insurance. stop_all's own command line does not contain the script
    today, but a wrapper or a rename could change that, and a script that kills
    itself mid-sweep leaves the rest of the stack running."""
    assert stop_all.hud_root_pids([HUD_PARENT], own_pid=HUD_PARENT[0]) == []


#############################################
# DEGRADATION
#############################################

@pytest.mark.parametrize("rows", [[], None])
def test_no_processes_is_not_an_error(rows):
    assert stop_all.hud_root_pids(rows) == []


@pytest.mark.parametrize("row", [
    (None, 900, "pythonw.exe", "x tools/nq_hud.py"),
    (6001, None, "pythonw.exe", "x tools/nq_hud.py"),
    (6002, 900, None, "x tools/nq_hud.py"),
    (6003, 900, "pythonw.exe", None),
    (6004, 900, "pythonw.exe", ""),
])
def test_a_malformed_row_never_raises(row):
    """The rows come from parsing PowerShell CSV output; a process that exits
    mid-query can leave a blank field. This runs inside a shutdown path, so it
    must degrade rather than abort the rest of the sweep."""
    assert isinstance(stop_all.hud_root_pids([row]), list)


def test_forward_and_back_slashes_both_match():
    """The launcher passes tools\\nq_hud.py; a shell might pass tools/nq_hud.py."""
    back = (7001, 900, "pythonw.exe", r"pythonw.exe tools\nq_hud.py")
    fwd = (7002, 900, "pythonw.exe", "pythonw.exe tools/nq_hud.py")
    assert stop_all.hud_root_pids([back]) == [7001]
    assert stop_all.hud_root_pids([fwd]) == [7002]


#############################################
# ORDERING — the HUD goes first
#############################################

def test_the_web_gui_is_still_killed_last():
    """Pre-existing invariant that must survive this change: the GUI's own
    Terminate button spawns this script, so the page that launched it has to die
    after everything it was asked to stop."""
    labels = [label for label, _ in stop_all._targets()]
    assert labels[-1] == "webgui"


def test_the_hud_is_not_a_port_target():
    """It binds no port. Giving it one here would make it look health-checkable
    on the Status page, which it is not."""
    assert not any("hud" in label.lower() for label, _ in stop_all._targets())


#############################################
# THE BORROWED PROXY — dev must not reach it
#############################################

def test_targets_skip_the_proxy_when_not_owned(monkeypatch):
    """Dev's PROXY_PORT is 8100 — PROD'S, borrowed, because there is one
    rotating Schwab OAuth refresh token and so only one proxy. Killing it from
    dev's Terminate button takes the live stack's market data down mid-session.
    """
    monkeypatch.setattr(stop_all, "OWNS_PROXY", False)
    assert "proxy" not in [label for label, _ in stop_all._targets()]


def test_targets_include_the_proxy_first_when_owned(monkeypatch):
    monkeypatch.setattr(stop_all, "OWNS_PROXY", True)
    assert stop_all._targets()[0][0] == "proxy"


@pytest.mark.parametrize("owns", [True, False])
def test_the_web_gui_is_killed_last_whether_or_not_we_own_the_proxy(monkeypatch, owns):
    """Dropping the proxy from the head of the list must not disturb the tail.
    The GUI's Terminate button spawns this script, so the page that launched it
    has to die after everything it was asked to stop."""
    monkeypatch.setattr(stop_all, "OWNS_PROXY", owns)
    assert [label for label, _ in stop_all._targets()][-1] == "webgui"


@pytest.mark.parametrize("owns", [True, False])
def test_every_service_is_still_a_target_either_way(monkeypatch, owns):
    """Non-vacuity partner for the two tests above: skipping the proxy must skip
    ONLY the proxy. Dev owns its own six services and must still stop them."""
    monkeypatch.setattr(stop_all, "OWNS_PROXY", owns)
    labels = [label for label, _ in stop_all._targets()]
    for name in stop_all.SERVICE_PORTS:
        assert f"{name}_svc" in labels


#############################################
# THE HUD, SCOPED TO THIS CHECKOUT
#############################################

MINE = r"D:\WebGUI Trading Dev"
THEIRS = r"D:\WebGUI Trading Prod"


def test_hud_match_is_scoped_to_this_checkout():
    """The HUD binds no port, so it is matched by command line — and unlike a
    port, that match cannot tell two checkouts apart. Prod's HUD has to survive
    dev's stop_all."""
    assert stop_all._is_hud("pythonw.exe", rf"{MINE}\.venv\Scripts\pythonw.exe tools\nq_hud.py",
                            root=MINE)
    assert not stop_all._is_hud("pythonw.exe",
                                rf"{THEIRS}\.venv\Scripts\pythonw.exe tools\nq_hud.py",
                                root=MINE)


def test_hud_match_unscoped_keeps_legacy_behavior():
    """root=None is the pre-environment behavior, so every existing two-arg
    caller — and every test above — is unaffected."""
    assert stop_all._is_hud("pythonw.exe", r"C:\anywhere\tools\nq_hud.py", root=None)
    assert stop_all._is_hud("pythonw.exe", rf"{THEIRS}\tools\nq_hud.py")


def test_hud_match_still_rejects_non_python_images_when_scoped():
    """Condition 2 is untouched by the new scope. A PowerShell in OUR checkout
    that merely mentions the script is still not the HUD — killing it would kill
    your terminal."""
    assert not stop_all._is_hud("powershell.exe", rf"{MINE}\tools\nq_hud.py", root=MINE)
    assert not stop_all._is_hud("Code.exe", rf'"{MINE}\tools\nq_hud.py"', root=MINE)


def test_hud_match_still_rejects_sibling_scripts_when_scoped():
    """Condition 1 is untouched by the new scope. nq_signal.py sits beside the
    HUD in OUR tools/ and is imported by it."""
    assert not stop_all._is_hud("pythonw.exe", rf"{MINE}\tools\nq_signal.py", root=MINE)


def test_a_checkout_whose_path_merely_starts_with_ours_is_not_matched():
    """The one that makes a naive substring test wrong. "D:\\Trading" is a
    PREFIX of "D:\\Trading Prod", so an unanchored `in` has dev matching prod and
    the whole scope achieves nothing. The needle carries a trailing separator."""
    assert not stop_all._is_hud("pythonw.exe",
                                r"D:\Trading Prod\.venv\Scripts\pythonw.exe tools\nq_hud.py",
                                root=r"D:\Trading")
    assert stop_all._is_hud("pythonw.exe",
                            r"D:\Trading\.venv\Scripts\pythonw.exe tools\nq_hud.py",
                            root=r"D:\Trading")


def test_the_scope_is_case_insensitive_and_separator_blind():
    """Windows paths compare case-insensitively, and the same checkout can be
    spelled with either slash depending on who launched it."""
    assert stop_all._is_hud("pythonw.exe", rf"{MINE.upper()}\TOOLS\NQ_HUD.PY", root=MINE)
    assert stop_all._is_hud("pythonw.exe", f"{MINE}/tools/nq_hud.py".replace("\\", "/"),
                            root=MINE)
    assert stop_all._is_hud("pythonw.exe", rf"{MINE}\tools\nq_hud.py",
                            root=MINE.replace("\\", "/"))


def test_a_hud_naming_no_checkout_at_all_is_left_running_when_scoped():
    """THE JUDGEMENT CALL, pinned. `pythonw tools\\nq_hud.py` names no checkout,
    so it cannot be attributed — and an unattributable HUD is treated as NOT
    ours. Leaving a stale HUD window open is an annoyance; killing the live
    stack's HUD is the thing this scope exists to prevent, and "kill anything
    ambiguous" would hand that back.

    In practice the launcher does not produce this: start_all_hidden.bat runs
    %~dp0.venv\\Scripts\\pythonw.exe, an ABSOLUTE path that names the checkout,
    and taskkill /T sweeps the redirector's child with it.
    """
    assert not stop_all._is_hud("pythonw.exe", r"pythonw.exe tools\nq_hud.py", root=MINE)
    # ...and is still killed unscoped, which is what the legacy callers see.
    assert stop_all._is_hud("pythonw.exe", r"pythonw.exe tools\nq_hud.py")


def test_hud_root_pids_threads_the_scope_through():
    """The scope is useless if the pure entry point drops it."""
    ours = (8001, 900, "pythonw.exe", rf"{MINE}\.venv\Scripts\pythonw.exe tools\nq_hud.py")
    theirs = (8002, 900, "pythonw.exe", rf"{THEIRS}\.venv\Scripts\pythonw.exe tools\nq_hud.py")
    assert stop_all.hud_root_pids([ours, theirs], root=MINE) == [8001]
    assert stop_all.hud_root_pids([ours, theirs]) == [8001, 8002]


def test_the_real_call_site_scopes_to_this_checkout(monkeypatch):
    """stop_hud() is where the scope has to actually be applied — passing
    root=REPO_ROOT there is the whole point of threading it."""
    root = str(stop_all.REPO_ROOT)
    ours = (8101, 900, "pythonw.exe", rf"{root}\.venv\Scripts\pythonw.exe tools\nq_hud.py")
    theirs = (8102, 900, "pythonw.exe", rf"{THEIRS}\.venv\Scripts\pythonw.exe tools\nq_hud.py")
    killed = []
    monkeypatch.setattr(stop_all, "_process_rows", lambda: [ours, theirs])
    monkeypatch.setattr(stop_all, "_kill", lambda pid: killed.append(pid) or True)
    stop_all.stop_hud()
    assert killed == ["8101"]
