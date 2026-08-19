"""No batch file may `call` a sibling script by BARE NAME.

This is a whole-repo guard for a defect that reached production twice on the same
day (2026-08-15). ``tools\\promote.bat`` did:

    cd /d "%~dp0.."      REM  -> the repo root, where stop_all.bat lives
    ...
    call stop_all.bat

which looks obviously fine, and IS fine when promote.bat is double-clicked from
Explorer. It is not fine under a shell with ``NoDefaultCurrentDirectoryInExePath``
set (measured ``=1`` in this environment's automation shells), because cmd then
refuses to search the working directory at all. The call fails with
"'stop_all.bat' is not recognized" — and, critically, **`call` to a missing file
does not abort the script**. promote.bat sailed straight past a stop that never
happened, pulled main, "restarted" via a second call that also never happened,
and then printed *"Prod is now STOPPED"* while prod was in fact still up and
serving OLD code from memory against NEW code on disk.

The failed call also sets errorlevel 1, which the very next ``if errorlevel 1``
read — which is why the false "restart REFUSED" message appeared. Measured: a
full-path call in isolation returns errorlevel 0 and takes the good branch, so
fixing the path fixes the message with it.

``%~dp0`` is the script's own directory and does not depend on cwd, so it is
correct however the script is invoked.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]

# `call foo.bat` / `call foo` — a target with no directory component at all.
# Deliberately does NOT match: a quoted or unquoted path containing \ or /, a
# %~dp0-rooted target, or a :label call (`call :wait_port_free`), which is an
# INTERNAL subroutine and has nothing to do with file resolution.
_BARE_CALL = re.compile(r"^\s*call\s+(?![:\"']?%)(?![\"']?[.a-zA-Z]:)"
                        r"(?![\"']?:)(?P<target>[^\s\"']+)", re.IGNORECASE)


def _batch_files():
    files = sorted(REPO.glob("*.bat")) + sorted((REPO / "tools").glob("*.bat"))
    assert files, "no .bat files found — the guard would pass vacuously"
    return files


def test_no_batch_file_calls_a_sibling_by_bare_name():
    offenders = []
    for path in _batch_files():
        for n, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().lower().startswith("rem"):
                continue
            m = _BARE_CALL.match(line)
            if not m:
                continue
            target = m.group("target")
            if "\\" in target or "/" in target:      # has a directory component
                continue
            offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, (
        "batch files must call siblings by full path (e.g. "
        'call "%~dp0..\\stop_all.bat"), not by bare name — cmd does not search '
        "the working directory when NoDefaultCurrentDirectoryInExePath is set, "
        "and a failed `call` does NOT abort the script:\n  " + "\n  ".join(offenders))


def test_promote_calls_both_launchers_by_full_path():
    """The two specific sites that half-completed two production promotes."""
    text = (REPO / "tools" / "promote.bat").read_text(encoding="utf-8",
                                                      errors="replace")
    assert 'call "%~dp0..\\stop_all.bat"' in text
    assert 'call "%~dp0..\\start_all_wt.bat" nowindow' in text
    # ...and the bare forms are gone.
    assert re.search(r"^\s*call\s+stop_all\.bat", text, re.M | re.I) is None
    assert re.search(r"^\s*call\s+start_all_wt\.bat", text, re.M | re.I) is None


def test_label_calls_are_not_flagged():
    """`call :subroutine` is an internal jump, not a file lookup — promote.bat
    uses it for :wait_port_free and must not be 'fixed'."""
    assert _BARE_CALL.match("call :wait_port_free %P_PROXY% proxy") is None
    assert _BARE_CALL.match('call "%~dp0..\\stop_all.bat"') is None
    assert _BARE_CALL.match("call stop_all.bat") is not None
