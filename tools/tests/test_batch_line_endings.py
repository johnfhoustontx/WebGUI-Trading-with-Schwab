"""No batch file may combine LF-only line endings with a non-ASCII byte.

``tools\\restart_one.bat`` -- the script behind every Restart button on the
System Status page -- became unrunnable from a shell on 2026-08-15:

    > cmd /c "tools\\restart_one.bat 9500 0 webgui webgui\\main.py"
    < was unexpected at this time.

with ``@echo on`` showing cmd had begun the second line two bytes late::

    M restart_one.bat <kill_port|0> <wait_port|0> <name> <script_relpath...>

``REM`` had lost its ``RE``, so the line was no longer a comment and the ``<``
was read as a redirection.

**Three conditions had to coincide**, measured as a matrix over the real file:

    =========================  ========  ==================
    file                       CP 437    CP 65001 (UTF-8)
    =========================  ========  ==================
    LF   + non-ASCII (as was)  ok        **PARSE ERROR**
    CRLF + non-ASCII           ok        ok
    LF   + ASCII only          ok        ok
    CRLF + ASCII only          ok        ok
    =========================  ========  ==================

Removing any one of the three fixes it. That is why the bug looked
invocation-specific: PowerShell's console runs at codepage 65001 and failed,
Git Bash runs at 437 and did not, and the shipping path -- ``subprocess.Popen``
with ``CREATE_NO_WINDOW`` in ``webgui/pages/status.py`` -- gets **no console at
all** and so falls back to the OEM codepage, which is why the Status page's
buttons kept working the whole time (verified live: dev ``trade_svc`` rebound
:9213 on a new PID through the unfixed file).

It was invisible to git as well. The committed blob was always fine; an editor
had rewritten the dev checkout's copy in place with LF, and because
``core.autocrlf`` normalizes both directions on comparison, ``git status``
stayed clean and ``git diff`` showed nothing.

This guard therefore checks the working-tree bytes, not the blob. It asserts
the *combination*, not CRLF everywhere -- four legacy launchers
(``options-scanner/scan_once.bat``, ``options-scanner/start_scanner.bat``,
``schwab-proxy/Launch_Proxy.bat``,
``sentiment-dashboard/Launch_Sector_Rotation.bat``) are LF-only but pure ASCII
and so genuinely work; demanding CRLF of them would fail in any checkout that
predates ``.gitattributes`` without any of them being broken.
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]

_SKIP_DIRS = {".git", ".venv", ".claude", "node_modules", "__pycache__"}


def _batch_files():
    files = [p for p in sorted(REPO.rglob("*.bat")) + sorted(REPO.rglob("*.cmd"))
             if not _SKIP_DIRS & set(p.relative_to(REPO).parts)]
    assert files, "no .bat files found -- the guard would pass vacuously"
    return files


def _verdict(raw: bytes):
    """``None`` if safe, else why this file's bytes can trip cmd's parser."""
    lf_only = b"\r" not in raw and b"\n" in raw
    try:
        raw.decode("ascii")
    except UnicodeDecodeError:
        non_ascii = True
    else:
        non_ascii = False
    if lf_only and non_ascii:
        return "LF-only line endings AND a non-ASCII byte"
    return None


def test_no_batch_file_combines_lf_endings_with_non_ascii():
    offenders = []
    for path in _batch_files():
        why = _verdict(path.read_bytes())
        if why:
            offenders.append(f"{path.relative_to(REPO)}: {why}")
    assert not offenders, (
        "cmd.exe resumes parsing mid-line in a batch file with LF-only line "
        "endings that also contains a non-ASCII byte, when the console "
        'codepage is UTF-8 -- it dies with "< was unexpected at this time." '
        "Give the file CRLF line endings (see .gitattributes) or keep it "
        "ASCII-only:\n  " + "\n  ".join(offenders))


def test_restart_one_is_crlf_and_ascii():
    """The file the Status page's Restart buttons run carries BOTH belts.

    Either one alone is sufficient, so this is deliberately redundant: the
    script must survive an editor that strips its CRLF *and* a comment that
    reintroduces a typographic dash.
    """
    raw = (REPO / "tools" / "restart_one.bat").read_bytes()
    assert b"\r\n" in raw, "restart_one.bat must have CRLF line endings"
    assert b"\n" not in raw.replace(b"\r\n", b""), "restart_one.bat has bare LF lines"
    raw.decode("ascii")  # raises UnicodeDecodeError if a non-ASCII byte crept back


def test_gitattributes_pins_crlf_for_batch_files():
    """.gitattributes is what stops a fresh clone reintroducing LF."""
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in text


def test_verdict_flags_the_broken_combination_only():
    """Non-vacuity: the detector must actually fire on the shape that broke."""
    em_dash = "—".encode("utf-8")
    assert _verdict(b"@echo off\nREM a " + em_dash + b"\n") is not None
    assert _verdict(b"@echo off\r\nREM a " + em_dash + b"\r\n") is None
    assert _verdict(b"@echo off\nREM plain ascii\n") is None
