"""``tools/promote.sh``, run for real against a sandbox - including when it fails.

Audit AR-01 (2026-10-03). The script used to stop production FIRST and then
fetch, pull and install, with no way back: any of those failing left prod down.
Every other fix ships through this script and there is no staging environment,
so it is tested the way it is used: the REAL script, under bash, in a real git
repository with a real remote. Only what must not happen on a test machine is
stubbed - ``systemctl``, ``ss``, and the checkout's Python (pip, the unit
generator and the HTTP probes), each of which records its calls and can be told
to fail.

What is pinned:

* nothing that can refuse or needs the network runs after the stop;
* a failure after the stop restores the previous commit and restarts on it;
* every process is probed, not only the proxy and the web GUI;
* ``--rollback`` puts back the commit the last promote replaced.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(not (BASH and GIT), reason="needs bash and git")

FAKE_PYTHON = r'''#!/usr/bin/env bash
# The checkout's Python, as far as promote.sh can tell.
echo "python $*" >> "$CALLS"
case "$*" in
  "tools/promote_facts.py")
    echo "ENV_NAME='${FAKE_ENV:-prod}'"
    echo "PROXY_PORT='8100'"
    echo "NICEGUI_PORT='8500'"
    echo "NICEGUI_LIVE_PORT='8501'"
    echo "SERVICE_PORTS='8210 8211 8212 8213 8215 8216'"
    echo "SERVICE_NAMES='sentiment options portfolio trade market news'"
    ;;
  "-m pip install --dry-run"*) [ -z "${FAIL_DRYRUN:-}" ] || exit 1 ;;
  "-m pip install"*)
    # Fails only while the NEW commit is checked out, like a real bad install.
    if [ -n "${FAIL_INSTALL:-}" ] && [ "$(git rev-parse HEAD)" = "${BAD_COMMIT:-x}" ]; then
      exit 1
    fi ;;
  "-m deploy.systemd.generate_units --install") [ -z "${FAIL_UNITS:-}" ] || exit 1 ;;
  "tools/wait_http.py"*)
    # A process that does not answer ON THE NEW CODE, and does on the old.
    if [ -n "${FAIL_PROBE:-}" ] && [ "$(git rev-parse HEAD)" = "${BAD_COMMIT:-x}" ]; then
      case "$*" in *"$FAIL_PROBE"*) exit 1 ;; esac
    fi ;;
esac
exit 0
'''

FAKE_SYSTEMCTL = r'''#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
case "$*" in
  *"is-active"*) exit 3 ;;      # inactive: the stop loop ends at once
  *" start "*) [ -z "${FAIL_START:-}" ] || [ "$(git rev-parse HEAD)" != "${BAD_COMMIT:-x}" ] ;;
esac
'''

FAKE_SS = "#!/usr/bin/env bash\nexit 0\n"       # nothing listening


_PLAIN = ("-c", "core.autocrlf=false", "-c", "core.fileMode=false")


def _git(cwd, *args):
    return subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _write_exec(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))        # LF: a CRLF shebang does not run
    path.chmod(0o755)


class Sandbox:
    def __init__(self, tmp):
        self.tmp = tmp
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"          # where "main" is authored and pushed
        self.prod = tmp / "prod"
        self.calls = tmp / "calls.log"
        self.stubs = tmp / "stubs"
        _git(tmp, "init", "--bare", "-b", "main", str(self.origin))
        # autocrlf and fileMode off AT CLONE TIME: a checkout that rewrites line
        # endings or modes makes the tree read as dirty, which the script refuses.
        _git(tmp, *_PLAIN, "clone", str(self.origin), str(self.work))
        for k, v in (("user.email", "t@example.test"), ("user.name", "t"),
                     ("core.autocrlf", "false"), ("core.fileMode", "false")):
            _git(self.work, "config", k, v)
        (self.work / "tools").mkdir()
        shutil.copy(ROOT / "tools" / "promote.sh", self.work / "tools" / "promote.sh")
        (self.work / "requirements.lock").write_text("pkg==1\n")
        (self.work / ".gitignore").write_text(".venv/\nlogs/\n")
        (self.work / "app.txt").write_text("one\n")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-m", "first")
        _git(self.work, "push", "origin", "HEAD:main")
        _git(tmp, *_PLAIN, "clone", str(self.origin), str(self.prod))
        _git(self.prod, "config", "core.autocrlf", "false")
        _git(self.prod, "config", "core.fileMode", "false")
        _write_exec(self.prod / ".venv" / "bin" / "python", FAKE_PYTHON)
        _write_exec(self.stubs / "systemctl", FAKE_SYSTEMCTL)
        _write_exec(self.stubs / "ss", FAKE_SS)
        self.first = _git(self.prod, "rev-parse", "HEAD")

    def publish(self, *, lock=None, text="two\n"):
        """Author one more commit on main and push it. Returns its sha."""
        (self.work / "app.txt").write_text(text)
        if lock is not None:
            (self.work / "requirements.lock").write_text(lock)
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-m", "next")
        _git(self.work, "push", "origin", "HEAD:main")
        return _git(self.work, "rev-parse", "HEAD")

    def run(self, *args, **env):
        full = dict(os.environ)
        full.update({k: str(v) for k, v in env.items()})
        full["CALLS"] = str(self.calls)
        full["PATH"] = str(self.stubs) + os.pathsep + full["PATH"]
        return subprocess.run([BASH, "tools/promote.sh", *args], cwd=self.prod,
                              env=full, capture_output=True, text=True, timeout=120)

    def head(self):
        return _git(self.prod, "rev-parse", "HEAD")

    def log(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def stops(self):
        return [c for c in self.log() if c.startswith("systemctl") and " stop " in c]

    def starts(self):
        return [c for c in self.log() if c.startswith("systemctl") and " start " in c]

    def probes(self):
        return [c for c in self.log() if c.startswith("python tools/wait_http.py")]


@pytest.fixture
def box(tmp_path):
    return Sandbox(tmp_path)


# ── before the stop ──────────────────────────────────────────────────────────

def test_a_checkout_that_is_not_prod_is_refused_untouched(box):
    box.publish()
    r = box.run(FAKE_ENV="dev")
    assert r.returncode == 1 and "not 'prod'" in r.stdout
    assert box.stops() == [] and box.head() == box.first


def test_a_dirty_tree_is_refused_before_the_stop(box):
    box.publish()
    (box.prod / "app.txt").write_text("edited in place\n")
    r = box.run()
    assert r.returncode == 1 and "dirty" in r.stdout
    assert box.stops() == []


def test_nothing_new_on_main_stops_nothing(box):
    r = box.run()
    assert r.returncode == 0 and "nothing to promote" in r.stdout
    assert box.stops() == [] and box.starts() == []


def test_a_fetch_that_fails_leaves_prod_running(box):
    box.publish()
    _git(box.prod, "remote", "set-url", "origin", str(box.tmp / "gone.git"))
    r = box.run()
    assert r.returncode != 0
    assert box.stops() == [] and box.head() == box.first


def test_a_main_that_is_not_a_fast_forward_is_refused_before_the_stop(box):
    box.publish()
    _git(box.prod, "config", "user.email", "t@example.test")
    _git(box.prod, "config", "user.name", "t")
    (box.prod / "local.txt").write_text("a commit prod has and main does not\n")
    _git(box.prod, "add", "-A")
    _git(box.prod, "commit", "-m", "local only")
    r = box.run()
    assert r.returncode == 1 and "not a fast-forward" in r.stdout
    assert box.stops() == []


def test_a_lock_that_will_not_install_is_refused_before_the_stop(box):
    box.publish(lock="pkg==2\n")
    r = box.run(FAIL_DRYRUN=1)
    assert r.returncode == 1 and "refusing before the stop" in r.stdout
    assert box.stops() == [] and box.head() == box.first


# ── a clean promote ──────────────────────────────────────────────────────────

def test_a_promote_moves_the_code_restarts_and_probes_every_process(box):
    new = box.publish()
    r = box.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.head() == new
    assert len(box.stops()) == 1 and len(box.starts()) == 1
    probed = " ".join(box.probes())
    for name in ("the proxy", "the sentiment service", "the options service",
                 "the portfolio service", "the trade service", "the market service",
                 "the news service", "the web GUI"):
        assert name in probed, name
    for port in (8210, 8211, 8212, 8213, 8215, 8216):
        assert f"http://127.0.0.1:{port}/health" in probed


def test_the_fetch_comes_before_the_stop_and_nothing_fetches_after_it(box):
    box.publish(lock="pkg==2\n")
    box.run()
    log = box.log()
    stop = next(i for i, c in enumerate(log) if c.startswith("systemctl") and " stop " in c)
    dry = next(i for i, c in enumerate(log) if "--dry-run" in c)
    install = next(i for i, c in enumerate(log)
                   if c.startswith("python -m pip install") and "--dry-run" not in c)
    assert dry < stop < install


def test_an_unchanged_lock_installs_nothing(box):
    box.publish()
    box.run()
    assert not [c for c in box.log() if "pip install" in c]


def test_the_previous_commit_is_recorded(box):
    box.publish()
    box.run()
    assert (box.prod / "logs" / "promote_previous_commit").read_text().strip() == box.first
    assert box.first in (box.prod / "logs" / "promote_history.log").read_text()


def test_restart_cycles_the_stack_with_nothing_new(box):
    r = box.run("--restart")
    assert r.returncode == 0
    assert len(box.stops()) == 1 and len(box.starts()) == 1


# ── a promote that fails after the stop ──────────────────────────────────────

def test_an_install_that_fails_rolls_back_and_restarts_on_the_old_commit(box):
    new = box.publish(lock="pkg==2\n")
    r = box.run(FAIL_INSTALL=1, BAD_COMMIT=new)
    assert r.returncode != 0
    assert "ROLLED BACK" in r.stdout
    assert box.head() == box.first
    assert box.starts(), "prod was left stopped"
    assert (box.prod / "requirements.lock").read_text() == "pkg==1\n"


def test_a_service_that_does_not_answer_on_the_new_code_rolls_back(box):
    new = box.publish()
    r = box.run(FAIL_PROBE="the options service", BAD_COMMIT=new)
    assert r.returncode != 0 and "ROLLED BACK" in r.stdout
    assert box.head() == box.first
    assert len(box.starts()) == 2                 # the new code, then the old


def test_a_web_gui_that_does_not_answer_rolls_back(box):
    new = box.publish()
    r = box.run(FAIL_PROBE="the web GUI", BAD_COMMIT=new)
    assert r.returncode != 0 and box.head() == box.first


def test_units_that_will_not_start_roll_back(box):
    new = box.publish()
    r = box.run(FAIL_START=1, BAD_COMMIT=new)
    assert r.returncode != 0 and box.head() == box.first
    assert "ROLLED BACK" in r.stdout


def test_the_public_screens_failing_alone_does_not_roll_back(box):
    new = box.publish()
    r = box.run(FAIL_PROBE="the public screens", BAD_COMMIT=new)
    assert r.returncode == 0, r.stdout
    assert box.head() == new
    assert "public screens are not answering" in r.stdout


def test_timers_that_do_not_arm_are_reported_but_the_promote_stands(box):
    new = box.publish()
    r = box.run(FAIL_UNITS=1)
    assert r.returncode == 1 and "NOT ARMED" in r.stdout
    assert box.head() == new and "ROLLED BACK" not in r.stdout


# ── the explicit rollback ────────────────────────────────────────────────────

def test_rollback_puts_back_the_commit_the_last_promote_replaced(box):
    new = box.publish()
    assert box.run().returncode == 0 and box.head() == new
    r = box.run("--rollback")
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.head() == box.first
    assert len(box.stops()) == 2 and len(box.starts()) == 2


def test_rollback_with_no_recorded_promote_is_refused_untouched(box):
    r = box.run("--rollback")
    assert r.returncode == 1 and "no recorded promote" in r.stdout
    assert box.stops() == []


def test_a_second_rollback_does_not_roll_forward(box):
    box.publish()
    box.run()
    box.run("--rollback")
    r = box.run("--rollback")
    assert r.returncode == 0 and "nothing to roll back" in r.stdout
    assert box.head() == box.first


def test_an_unknown_argument_is_refused(box):
    r = box.run("--force")
    assert r.returncode == 2 and box.stops() == []


# ── the facts helper ─────────────────────────────────────────────────────────

def test_promote_facts_names_every_service_port():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import promote_facts
    import repo_paths
    f = promote_facts.facts()
    assert f["SERVICE_PORTS"].split() == [str(p) for p in sorted(repo_paths.SERVICE_PORTS.values())]
    assert len(f["SERVICE_NAMES"].split()) == len(repo_paths.SERVICE_PORTS)
    assert f["ENV_NAME"] == repo_paths.ENV_NAME


def test_promote_facts_refuses_a_value_a_shell_could_act_on():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import promote_facts
    with pytest.raises(ValueError):
        promote_facts.shell_lines({"ENV_NAME": "prod'; rm -rf /; '"})
    with pytest.raises(ValueError):
        promote_facts.shell_lines({"X": "$(id)"})
