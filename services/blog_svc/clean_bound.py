"""Run ``clean.clean`` in a worker process, killed at a time limit.

``clean_bounded`` is what the service calls; ``clean.clean`` stays the pure,
in-process function (used by tests and by the worker). The HTML parser's cost
is not linear in the input - one tag with tens of thousands of attributes
parses in minutes at the size limit - and three rounds of review showed a
pre-parse scan cannot predict that cost. So the bound is no longer a prediction:
each document is cleaned in its own process and that process is killed at
``[limits] clean_sec``. An overrun is a refusal (``too_slow``), counted, and the
service thread is free again. This holds for every slow shape, known or not, on
any parser version, and contains a crash or a memory blow-up inside the parser
too. See the design doc, "The cleaner runs in a worker process with a time
limit".

The cheap pre-parse scan still runs first, in-process, as a fast refusal of the
obvious ``crowded_tag`` case - but it is best-effort (``clean._scan``), and the
worker's timer is what actually bounds the cost.

⚠ Platform branches that cannot be exercised on the Windows dev box and are
written to read as plainly correct for the Linux prod box:
  * the worker is started in its OWN session (``start_new_session`` on POSIX),
    so a timeout kills the whole PROCESS GROUP (``os.killpg``), not just the
    leader - a parser that forked would otherwise orphan its children;
  * on Windows there is no session/group, so the timeout kills the one process
    (``Popen.kill``).
Both are listed in the task report for a later Linux run.
"""
import json
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

import repo_paths
from services import _degrade
from services.blog_svc import clean
from shared import blog_inbox

log = logging.getLogger("blog_svc.clean_bound")

_WORKER = (sys.executable, "-m", "services.blog_svc.clean_worker")

# The ONLY environment variables the worker inherits. An allow-list, not a
# deny-list: the service's own environment holds API keys and tokens the worker
# has no use for, and nothing here carries a secret. The two BLOG_CLEAN_* the
# parent adds (below) are limits, not secrets.
_SAFE_ENV = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PYTHONPATH", "PYTHONHOME",
             "VIRTUAL_ENV", "LANG", "LC_ALL", "LC_CTYPE", "TMP", "TEMP", "TMPDIR")
# How much of the worker's stderr is ever logged, ASCII-escaped. Never its
# stdout (that is the document's cleaned form) and never the document.
_STDERR_TAIL = 200
# Beyond the cleaned output's own ceiling (6x the input, pinned by a size test),
# this much slack for the JSON wrapper. More than this from the worker is a bug.
_STDOUT_SLACK = 64 * 1024


@dataclass(frozen=True)
class WorkerRun:
    """What one worker run came to. The injectable ``run`` seam returns this."""
    returncode: int | None
    stdout: bytes
    stderr_tail: str
    timed_out: bool


class _BadResult(Exception):
    """The worker returned something ``clean_bounded`` will not trust."""


def _child_env() -> dict:
    env = {name: os.environ[name] for name in _SAFE_ENV if name in os.environ}
    limits = blog_inbox.limits()
    env["BLOG_CLEAN_MEM_MB"] = str(limits["clean_mem_mb"])
    # A little over the wall limit: the CPU-second rlimit is a backstop for a
    # busy loop the wall-clock kill would also catch.
    env["BLOG_CLEAN_CPU_SEC"] = str(limits["clean_sec"] + 5)
    return env


def _kill(popen, posix) -> None:
    try:
        if posix:
            os.killpg(os.getpgid(popen.pid), signal.SIGKILL)
        else:
            popen.kill()
    except (ProcessLookupError, OSError):       # already gone
        pass


def _run_worker(html_bytes, *, timeout) -> WorkerRun:
    """Start the worker, feed it the document, and return what it produced.
    Kills it (its whole group on POSIX) on overrun or on interrupt."""
    posix = os.name == "posix"
    popen = subprocess.Popen(list(_WORKER), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=str(repo_paths.REPO_ROOT),
                             env=_child_env(), start_new_session=posix)
    try:
        try:
            out, err = popen.communicate(html_bytes, timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill(popen, posix)
            try:
                out, err = popen.communicate(timeout=5)
            except subprocess.TimeoutExpired:   # the drain itself hung: give up on output
                out, err = b"", b""
            timed_out = True
    except (KeyboardInterrupt, SystemExit):
        _kill(popen, posix)
        popen.wait()
        raise
    tail = err[-_STDERR_TAIL:].decode("ascii", "backslashreplace") if err else ""
    return WorkerRun(popen.returncode, out or b"", tail, timed_out)


def _validate(obj, input_bytes) -> clean.Cleaned:
    """Turn the worker's JSON object into a ``Cleaned``, trusting none of it:
    the child is a separate process and its output is data. Raises
    ``_BadResult`` on anything off."""
    if not isinstance(obj, dict):
        raise _BadResult("not an object")
    if set(obj) != {"html", "title", "summary", "removed", "font_links", "reason"}:
        raise _BadResult("fields")
    for key in ("html", "title", "summary", "reason"):
        if not isinstance(obj[key], str):
            raise _BadResult(f"{key} not a string")
    if obj["reason"] and obj["reason"] not in clean.REFUSALS:
        raise _BadResult("reason not a known code")
    removed = obj["removed"]
    if not (isinstance(removed, dict)
            and all(isinstance(name, str) and isinstance(count, int)
                    and not isinstance(count, bool) and count >= 0
                    for name, count in removed.items())):
        raise _BadResult("removed not a str->non-negative-int map")
    links = obj["font_links"]
    if not (isinstance(links, list)
            and all(isinstance(href, str) and clean._FONT_LINK_RE.match(href) for href in links)):
        raise _BadResult("font_links not all typeface links")
    return clean.Cleaned(obj["html"], obj["title"], obj["summary"], removed,
                         tuple(links), obj["reason"])


def _internal(detail) -> clean.Cleaned:
    """A refusal for a worker that could not be run or trusted. The detail is a
    bounded, document-free note (an exit code, an exception type, a stderr
    tail) - never the document and never a full traceback."""
    _degrade.degraded("blog.clean", detail=str(detail)[:_STDERR_TAIL], exc_info=False)
    log.info("refused: internal")
    return clean.refusal("internal")


def clean_bounded(html, *, timeout=None, run=None) -> clean.Cleaned:
    """``clean.clean(html)``, but computed in a worker process killed at
    ``timeout`` seconds (default ``[limits] clean_sec``). NEVER raises, except
    that ``KeyboardInterrupt`` / ``SystemExit`` propagate once the child is
    killed. Same ``Cleaned`` contract as ``clean``.

    ``run`` is an injectable seam (default: the real subprocess). It is called
    ``run(html_bytes, timeout=...)`` and returns a ``WorkerRun``."""
    if not isinstance(html, str):
        return clean.clean(html)                    # not_text, cheap, no process
    text = clean._CONTROL_RE.sub("", html)
    if not text.strip():
        return clean.clean(html)                    # empty, cheap, no process
    if clean._scan(text) is None:                   # the fast, best-effort first refusal
        log.info("refused: crowded_tag")
        return clean.refusal("crowded_tag")
    if timeout is None:
        timeout = blog_inbox.limits()["clean_sec"]
    runner = run or _run_worker
    html_bytes = html.encode("utf-8")
    try:
        ran = runner(html_bytes, timeout=timeout)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        return _internal(f"worker not run: {type(exc).__name__}")
    if ran.timed_out:
        _degrade.degraded("blog.clean.too_slow", exc_info=False)
        log.info("refused: too_slow")
        return clean.refusal("too_slow")
    if ran.returncode != 0:
        return _internal(f"worker exit {ran.returncode}; stderr {ran.stderr_tail!r}")
    if len(ran.stdout) > 6 * len(html_bytes) + _STDOUT_SLACK:
        return _internal("worker stdout over the size bound")
    try:
        return _validate(json.loads(ran.stdout.decode("utf-8")), len(html_bytes))
    except (ValueError, _BadResult) as exc:
        return _internal(f"worker output: {type(exc).__name__}")
