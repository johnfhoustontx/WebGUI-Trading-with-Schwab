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
