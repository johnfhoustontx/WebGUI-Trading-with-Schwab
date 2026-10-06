"""clean_bounded: clean.clean run in a worker process with a time limit.

These tests DO start real subprocesses (no network, no store). The ones that
need a particular worker result - a non-zero exit, garbage, the wrong shape, a
flood of output - inject the ``run`` seam instead of coercing a real worker
into misbehaving. The slow-document and child-is-gone tests use the real
worker, because a fake one proves nothing about the kill.
"""
import json
import os
import random
import re
import sys
import threading
import time

import pytest

from services import _degrade
from services.blog_svc import clean, clean_bound
from services.blog_svc.clean_bound import WorkerRun, clean_bounded
from services.blog_svc.tests._audit import FIXTURE, audit
from services.blog_svc.tests.test_clean import HOSTILE, HOSTILE_CSS, MORE_HOSTILE, page


def degrades(area):
    return _degrade.counts().get(area, 0)


def ok_run(payload_obj):
    """A ``run`` seam that returns one worker result without a process."""
    def run(html_bytes, *, timeout):
        return WorkerRun(0, json.dumps(payload_obj).encode("utf-8"), "", False)
    return run


# ── it agrees with in-process clean() ────────────────────────────────────────

def _same(a, b):
    return (a.html == b.html and a.title == b.title and a.summary == b.summary
            and a.removed == b.removed and a.font_links == b.font_links and a.reason == b.reason)


def test_the_fixture_through_the_worker_is_what_clean_produces():
    document = FIXTURE.read_text(encoding="utf-8")
    assert _same(clean_bounded(document), clean.clean(document))


def test_a_sample_of_the_corpus_matches_clean_byte_for_byte():
    corpus = [page(p) for p in HOSTILE + MORE_HOSTILE]
    corpus += [page(f"<style>{css}</style>") for css in HOSTILE_CSS]
    corpus += ["<h1>T</h1><p>one</p><p>two</p>", "<title>A</title><main>body</main>"]
    sample = random.Random(7).sample(corpus, 40)
    for document in sample:
        bounded, in_process = clean_bounded(document), clean.clean(document)
        assert _same(bounded, in_process), document[:120]
        audit(bounded.html)


# ── refusals that need no process ────────────────────────────────────────────

@pytest.mark.parametrize("thing", [None, 5, 1.5, b"<p>x</p>", ["<p>x</p>"]])
def test_a_non_string_is_refused_without_a_process(thing):
    def no_run(*_a, **_k):
        raise AssertionError("must not start a process")
    result = clean_bounded(thing, run=no_run)
    assert result.reason == "not_text" and result.removed == {"unparseable": 1}


@pytest.mark.parametrize("blank", ["", "   ", "\n\t ", "\x00\x01"])
def test_an_empty_document_is_refused_without_a_process(blank):
    def no_run(*_a, **_k):
        raise AssertionError("must not start a process")
    assert clean_bounded(blank, run=no_run).reason == "empty"


def test_a_crowded_tag_is_refused_without_a_process():
    def no_run(*_a, **_k):
        raise AssertionError("must not start a process")
    crowded = "<p " + " ".join(f"a{i}" for i in range(clean.MAX_TAG_ATTRS + 1)) + ">t</p>"
    assert clean_bounded(crowded, run=no_run).reason == "crowded_tag"


# ── a slow document is killed ────────────────────────────────────────────────

_SLEEPER = [sys.executable, "-c", "import sys,time; sys.stdin.buffer.read(); time.sleep(60)"]


def test_the_real_worker_is_killed_on_overrun_and_leaves_no_child(monkeypatch):
    """The kill itself, through a real subprocess. The worker is replaced with a
    process that reads stdin then sleeps - a worker that does not finish - so
    the test does not depend on finding a document slow to PARSE (the scan now
    refuses the obvious slow shapes before the worker ever runs). Popen is
    wrapped only for the worker start; ``_gone`` on Windows shells out to
    tasklist, which would otherwise go through a still-installed spy."""
    monkeypatch.setattr(clean_bound, "_WORKER", tuple(_SLEEPER))
    pids = []
    real_popen = clean_bound.subprocess.Popen

    def spy(*a, **k):
        proc = real_popen(*a, **k)
        pids.append(proc.pid)
        return proc

    started = time.perf_counter()
    clean_bound.subprocess.Popen = spy
    try:
        ran = clean_bound._run_worker(b"<p>never finishes</p>", timeout=1)
    finally:
        clean_bound.subprocess.Popen = real_popen
    elapsed = time.perf_counter() - started

    assert ran.timed_out and elapsed < 10, elapsed
    assert pids, "no child was started"
    deadline = time.perf_counter() + 5
    for pid in pids:
        while not _gone(pid) and time.perf_counter() < deadline:
            time.sleep(0.05)
        assert _gone(pid), f"child {pid} still alive after the kill"


def test_a_timed_out_run_is_refused_and_counted():
    """clean_bounded's handling of a worker that overran: too_slow, counted,
    no raise. The run seam reports the timeout so the logic is tested without
    depending on how slow any particular document is."""
    before = degrades("blog.clean.too_slow")
    run = lambda html_bytes, *, timeout: WorkerRun(None, b"", "", True)
    result = clean_bounded("<p>x</p>", run=run)
    assert result.reason == "too_slow" and result.removed == {"unparseable": 1}
    assert degrades("blog.clean.too_slow") == before + 1


def _gone(pid):
    """Whether process ``pid`` is no longer running."""
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False        # exists, not ours
        return False
    # Windows: tasklist is always present; a gone pid is not listed.
    import subprocess
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True).stdout
    return str(pid) not in out


# ── a worker that misbehaves is an internal refusal ──────────────────────────

def test_a_nonzero_exit_is_internal(monkeypatch):
    before = degrades("blog.clean")
    run = lambda html_bytes, *, timeout: WorkerRun(3, b"", "boom", False)
    result = clean_bounded("<p>x</p>", run=run)
    assert result.reason == "internal" and degrades("blog.clean") == before + 1


def test_garbage_on_stdout_is_internal(monkeypatch):
    before = degrades("blog.clean")
    run = lambda html_bytes, *, timeout: WorkerRun(0, b"not json at all {", "", False)
    assert clean_bounded("<p>x</p>", run=run).reason == "internal"
    assert degrades("blog.clean") == before + 1


@pytest.mark.parametrize("obj", [
    {"html": "<x>"},                                             # missing fields
    {"html": "<x>", "title": "", "summary": "", "removed": {}, "font_links": [],
     "reason": "", "extra": 1},                                 # an extra field
    {"html": 5, "title": "", "summary": "", "removed": {}, "font_links": [], "reason": ""},
    {"html": "<x>", "title": "", "summary": "", "removed": {"s": -1}, "font_links": [],
     "reason": ""},                                             # negative count
    {"html": "<x>", "title": "", "summary": "", "removed": {"s": True}, "font_links": [],
     "reason": ""},                                             # bool is not a count
    {"html": "<x>", "title": "", "summary": "", "removed": {}, "font_links": ["not-a-font"],
     "reason": ""},                                             # a bad font link
    {"html": "<x>", "title": "", "summary": "", "removed": {}, "font_links": [],
     "reason": "made_up"},                                      # an unknown reason code
])
def test_a_wrong_shaped_object_is_internal(obj):
    before = degrades("blog.clean")
    assert clean_bounded("<p>x</p>", run=ok_run(obj)).reason == "internal"
    assert degrades("blog.clean") == before + 1


def test_a_flood_of_stdout_is_internal():
    before = degrades("blog.clean")
    flood = b'{"html":"' + b"a" * (8 * 1024 * 1024) + b'"}'
    run = lambda html_bytes, *, timeout: WorkerRun(0, flood, "", False)
    assert clean_bounded("<p>x</p>", run=run).reason == "internal"
    assert degrades("blog.clean") == before + 1


def test_a_run_that_raises_is_internal():
    def boom(html_bytes, *, timeout):
        raise OSError("no subprocess today")
    before = degrades("blog.clean")
    assert clean_bounded("<p>x</p>", run=boom).reason == "internal"
    assert degrades("blog.clean") == before + 1


# ── the environment carries no secret ────────────────────────────────────────

def test_the_child_environment_carries_no_secret(monkeypatch):
    for name in ("OPENAI_API_KEY", "SCHWAB_TOKEN", "DB_PASSWORD", "MY_SECRET_THING",
                 "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "s3cr3t")
    env = clean_bound._child_env()
    for name in env:
        upper = name.upper()
        assert not any(bad in upper for bad in ("KEY", "TOKEN", "SECRET", "PASSWORD")), name
    assert "s3cr3t" not in env.values()
    assert env["BLOG_CLEAN_MEM_MB"] == str(clean.blog_inbox.limits()["clean_mem_mb"])


# ── interrupts kill the child and propagate ──────────────────────────────────

def test_a_keyboard_interrupt_in_the_runner_kills_the_child_and_propagates(monkeypatch):
    killed = []

    class FakePopen:
        def __init__(self, *a, **k):
            self.pid = 4242
            self.returncode = None

        def communicate(self, *a, **k):
            raise KeyboardInterrupt

        def kill(self):
            killed.append(self.pid)

        def wait(self, *a, **k):
            return 0

    monkeypatch.setattr(clean_bound.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(clean_bound.os, "name", "nt")      # exercise the non-POSIX kill
    with pytest.raises(KeyboardInterrupt):
        clean_bound._run_worker(b"<p>x</p>", timeout=5)
    assert killed == [4242]


def test_clean_bounded_lets_a_keyboard_interrupt_through():
    def interrupt(html_bytes, *, timeout):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        clean_bounded("<p>x</p>", run=interrupt)


# ── it holds up under concurrency ────────────────────────────────────────────

def test_eight_threads_each_get_a_right_answer():
    document = FIXTURE.read_text(encoding="utf-8")
    want = clean.clean(document)
    results, errors = [], []

    def worker():
        try:
            results.append(clean_bounded(document))
        except BaseException as exc:            # noqa: BLE001 - a test recording failures
            errors.append(exc)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors and len(results) == 8
    assert all(_same(result, want) for result in results)


# ── the worker writes nothing but the JSON object ────────────────────────────

def test_the_worker_prints_only_json_even_when_clean_logs():
    """A document that makes clean() log a refusal (crowded tag reaches the
    worker only if the scan missed it - force it by calling the worker directly
    on a document that refuses). stdout must still be exactly one JSON object."""
    import subprocess
    document = "<a href='javascript:alert(1)'>x</a><!-- never closed"
    done = subprocess.run(list(clean_bound._WORKER), input=document.encode("utf-8"),
                          cwd=str(clean_bound.repo_paths.REPO_ROOT),
                          env=clean_bound._child_env(), capture_output=True)
    assert done.returncode == 0
    obj = json.loads(done.stdout)               # the WHOLE of stdout is one object
    assert set(obj) == {"html", "title", "summary", "removed", "font_links", "reason"}
    assert obj["reason"] == "cut_off"


def test_the_worker_decodes_invalid_utf8_without_dying():
    import subprocess
    done = subprocess.run(list(clean_bound._WORKER), input=b"<p>\xff\xfe bad bytes</p>",
                          cwd=str(clean_bound.repo_paths.REPO_ROOT),
                          env=clean_bound._child_env(), capture_output=True)
    assert done.returncode == 0 and json.loads(done.stdout)["reason"] == ""


def test_the_font_link_pattern_is_what_validate_accepts():
    good = "https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap"
    assert re.match(clean._FONT_LINK_RE, good)
    obj = {"html": clean.clean("<p>x</p>").html, "title": "", "summary": "",
           "removed": {}, "font_links": [good], "reason": ""}
    assert clean_bounded("<p>x</p>", run=ok_run(obj)).font_links == (good,)
