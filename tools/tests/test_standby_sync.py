"""The standby's nightly sync, driven by a fake remote and a fake ``run``."""
import datetime as dt
import errno
import json
import pathlib
import sqlite3
import subprocess
import tempfile
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.standby_config import Identity  # noqa: E402
from tools import standby_sync as ss  # noqa: E402

GEN = "prod_2026-11-20_2000"
COMMIT = "a" * 40
STANDBY = Identity("vps3", "standby", False)
PAPER = "options-scanner/data/paper_account.db"


def _settings(tmp_path, **kw):
    s = {"at": "22:15", "max_age_h": 80, "max_shrink_pct": 50, "keep": 3,
         "connect_timeout_sec": 15, "generations": tmp_path / "gens",
         "shrink_stores": [PAPER]}
    s.update(kw)
    return s


def _db(size):
    """A real SQLite file of about ``size`` bytes (restore integrity-checks it)."""
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "s.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE t (x BLOB)")
        con.execute("INSERT INTO t VALUES (?)", (b"x" * size,))
        con.commit()
        con.close()
        return path.read_bytes()


def _files(paper=100_000, extra=None):
    f = {PAPER: _db(paper), "shared/tokens.json": b"{}",
         "config/local/a.toml": b"a = 1\n"}
    f.update(extra or {})
    return f


class FakeRemote:
    def __init__(self, gens=None):
        self.gens = gens if gens is not None else {GEN: self.make()}
        self.fetched = []
        self.fetch_error = None

    @staticmethod
    def make(files=None, marker=True, commit=COMMIT):
        return {"files": files if files is not None else _files(),
                "marker": marker, "head": commit}

    def generations(self):
        return list(self.gens)

    def has(self, gen, name):
        g = self.gens[gen]
        return g["marker"] if name == "BACKUP_OK" else name == "GIT_HEAD"

    def read(self, gen, name):
        return self.gens[gen]["head"] + "\n"

    def fetch(self, gen, dest, basis):
        self.fetched.append(gen)
        if self.fetch_error:
            (dest / "half").parent.mkdir(parents=True, exist_ok=True)
            (dest / "half").write_bytes(b"x")
            raise self.fetch_error
        total = 0
        for rel, data in self.gens[gen]["files"].items():
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            total += len(data)
        (dest / "BACKUP_OK").write_text("ok")
        (dest / "GIT_HEAD").write_text(self.gens[gen]["head"] + "\n")
        return total


class FakeRun:
    """Records argv; answers ``git`` calls, with chosen return codes."""

    def __init__(self, rc=None):
        self.calls = []
        self.rc = rc or {}

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        rc = 0
        for key, value in self.rc.items():
            if key in argv:
                rc = value
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="boom" if rc else "")


@pytest.fixture
def env(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    return repo, home


def _go(tmp_path, env, remote=None, runner=None, identity=STANDBY, now=None, **kw):
    repo, home = env
    remote = remote or FakeRemote()
    runner = runner or FakeRun()
    now = now or dt.datetime(2026, 11, 21, 22, 15)
    status = ss.run(now, identity=identity, remote=remote, repo=repo, home=home,
                    settings=_settings(tmp_path, **kw), run=runner)
    return status, remote, runner


def _saved(home):
    return json.loads((home / "standby" / "status.json").read_text())


def test_synced_restores_and_records(tmp_path, env):
    status, remote, runner = _go(tmp_path, env)
    assert status["result"] == "synced", status["detail"]
    assert status["commit"] == COMMIT and status["bytes"] > 0
    assert (env[0] / "shared" / "tokens.json").is_file()
    assert (tmp_path / "gens" / GEN).is_dir()
    assert not list((tmp_path / "gens").glob("*.partial"))
    assert ["git", "-C", str(env[0]), "checkout", "--detach", COMMIT] in runner.calls
    assert _saved(env[1])["result"] == "synced"


def test_refuses_on_promoted_box(tmp_path, env):
    unit = env[1] / ".config" / "systemd" / "user"
    unit.mkdir(parents=True)
    (unit / "trading-prod.target").write_text("[Unit]\n")
    status, remote, runner = _go(tmp_path, env)
    assert status["result"] == "refused"
    assert remote.fetched == [] and runner.calls == []
    assert _saved(env[1])["result"] == "refused"


def test_refuses_when_identity_says_prod(tmp_path, env):
    status, remote, _ = _go(tmp_path, env, identity=Identity("vps3", "prod", False))
    assert status["result"] == "refused" and remote.fetched == []


def test_notifier_template_alone_does_not_refuse(tmp_path, env):
    unit = env[1] / ".config" / "systemd" / "user"
    unit.mkdir(parents=True)
    (unit / "trading-prod-notify-failure@.service").write_text("[Unit]\n")
    status, _, _ = _go(tmp_path, env)
    assert status["result"] == "synced"


def test_newest_complete_skips_unmarked():
    newer = "prod_2026-11-21_2000"
    remote = FakeRemote({newer: FakeRemote.make(marker=False), GEN: FakeRemote.make()})
    assert ss.newest_complete(remote) == GEN
    assert ss.newest_complete(FakeRemote({})) is None


def test_unchanged_is_quiet_and_recorded(tmp_path, env):
    _go(tmp_path, env)
    status, remote, runner = _go(tmp_path, env, now=dt.datetime(2026, 11, 22, 22, 15))
    assert status["result"] == "unchanged"
    assert _saved(env[1])["result"] == "unchanged"
    assert _saved(env[1])["checked_at"].startswith("2026-11-22")


def test_unchanged_does_not_fetch_again(tmp_path, env):
    _go(tmp_path, env)
    remote = FakeRemote()
    status, _, runner = _go(tmp_path, env, remote=remote,
                            now=dt.datetime(2026, 11, 22, 22, 15))
    assert status["result"] == "unchanged"
    assert remote.fetched == [] and runner.calls == []


def test_unchanged_but_too_old_fails(tmp_path, env):
    _go(tmp_path, env)
    now = dt.datetime(2026, 11, 20, 20, 0) + dt.timedelta(hours=81)
    status, _, _ = _go(tmp_path, env, now=now)
    assert status["result"] == "failed" and "stopped making backups" in status["detail"]


def test_failed_restore_is_retried_next_night(tmp_path, env, monkeypatch):
    def boom(*a, **k):
        return {"restored": [], "kept": [], "bad": ["x.db: corrupt"], "refused": [],
                "marked_ok": True}
    monkeypatch.setattr(ss.rb, "restore", boom)
    status, _, _ = _go(tmp_path, env)
    assert status["result"] == "failed"
    monkeypatch.undo()
    status, remote, _ = _go(tmp_path, env, now=dt.datetime(2026, 11, 22, 22, 15))
    assert status["result"] == "synced" and remote.fetched == []


def test_shrink_refuses_and_keeps_previous(tmp_path, env, monkeypatch):
    prev = "prod_2026-11-19_2000"
    remote = FakeRemote({prev: FakeRemote.make()})
    status, _, _ = _go(tmp_path, env, remote=remote, now=dt.datetime(2026, 11, 20, 1, 0))
    assert status["result"] == "synced"
    remote.gens[GEN] = FakeRemote.make(_files(paper=10_000))
    called = []
    monkeypatch.setattr(ss.rb, "restore", lambda *a, **k: called.append(1))
    status, _, _ = _go(tmp_path, env, remote=remote)
    assert status["result"] == "failed" and "shrank" in status["detail"]
    gens = tmp_path / "gens"
    assert (gens / prev).is_dir() and not (gens / GEN).exists()
    assert not list(gens.glob("*.partial")) and called == []


def test_shrink_refusals_unit(tmp_path):
    new, old = tmp_path / "n", tmp_path / "o"
    for root, size in ((old, 10_000), (new, 4_000)):
        (root / PAPER).parent.mkdir(parents=True)
        (root / PAPER).write_bytes(b"x" * size)
    assert ss.shrink_refusals(new, old, [PAPER], 50)
    assert ss.shrink_refusals(new, None, [PAPER], 50) == []
    assert ss.shrink_refusals(new, old, [PAPER], 70) == []
    (new / PAPER).unlink()
    assert "missing" in ss.shrink_refusals(new, old, [PAPER], 50)[0]


def test_commit_not_on_main_fails(tmp_path, env):
    runner = FakeRun({"--is-ancestor": 1})
    status, _, _ = _go(tmp_path, env, runner=runner)
    assert status["result"] == "failed" and "not on origin/main" in status["detail"]
    assert not any("checkout" in c for c in runner.calls)
    assert not (env[0] / "shared" / "tokens.json").exists()


def test_lock_change_reinstalls(tmp_path, env):
    runner = FakeRun({"diff": 1})
    status, _, _ = _go(tmp_path, env, runner=runner)
    assert status["result"] == "synced"
    pip = [c for c in runner.calls if "pip" in c]
    assert pip and pip[0][-3:-1] == ["install", "-r"] and pip[0][-1].endswith("requirements.lock")


def test_unchanged_lock_does_not_reinstall(tmp_path, env):
    _, _, runner = _go(tmp_path, env)
    assert not [c for c in runner.calls if "pip" in c]


def test_planted_file_fails_the_night(tmp_path, env):
    remote = FakeRemote({GEN: FakeRemote.make(_files(extra={"tools/evil.py": b"boom"}))})
    status, _, _ = _go(tmp_path, env, remote=remote)
    assert status["result"] == "failed" and "evil.py" in status["detail"]
    assert not (env[0] / "tools" / "evil.py").exists()
    assert not (tmp_path / "gens" / GEN).exists()


def test_disk_full_mid_copy_leaves_previous_untouched(tmp_path, env):
    prev = "prod_2026-11-19_2000"
    remote = FakeRemote({prev: FakeRemote.make()})
    _go(tmp_path, env, remote=remote, now=dt.datetime(2026, 11, 20, 1, 0))
    remote.gens[GEN] = FakeRemote.make()
    remote.fetch_error = OSError(errno.ENOSPC, "No space left on device")
    status, _, _ = _go(tmp_path, env, remote=remote)
    gens = tmp_path / "gens"
    assert status["result"] == "failed" and "disk full" in status["detail"]
    assert (gens / prev / PAPER).is_file() and not (gens / GEN).exists()
    assert not list(gens.glob("*.partial"))


def test_prune_keeps_three(tmp_path):
    names = [f"prod_2026-11-{d:02d}_2000" for d in (10, 11, 12, 13, 14)]
    for n in names:
        (tmp_path / n).mkdir()
    (tmp_path / "other").mkdir()
    gone = ss.prune(tmp_path, 3)
    assert gone == names[:2]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other", *names[2:]]


def test_status_written_on_failure(tmp_path, env):
    remote = FakeRemote({})
    status, _, _ = _go(tmp_path, env, remote=remote)
    saved = _saved(env[1])
    assert status["result"] == saved["result"] == "failed"
    assert set(saved) == {"checked_at", "result", "generation", "commit", "bytes",
                          "started", "finished", "detail"}


def test_allowed_path():
    ok = ss.allowed_path
    P = pathlib.PurePosixPath
    assert ok(P("shared/tokens.json")) and ok(P("config/local/x.toml"))
    assert ok(P("services/blog_svc/data/a.txt")) and ok(P("some/where/x.db"))
    assert not ok(P("tools/evil.py")) and not ok(P("webgui/data/voice/a.mp3"))
    assert not ok(P("webgui/main.py")) and not ok(P("../x.db"))
    assert not ok(P(".venv/lib/x.db"))


def test_age_is_hours_of_naive_central():
    assert ss.generation_age_h(GEN, dt.datetime(2026, 11, 21, 8, 0)) == 12.0


def _neighbour_gen(tmp_path, freeze="a==1\n"):
    gen = tmp_path / "gen"
    base = gen / "neighbours" / "cos-hub"
    (base / "dir").mkdir(parents=True, exist_ok=True)
    (base / "dir" / "app.py").write_text("print(1)\n")
    (base / "units").mkdir(exist_ok=True)
    (base / "units" / "cos-hub.service").write_text("[Unit]\n")
    (base / "files" / ".config" / "x").mkdir(parents=True, exist_ok=True)
    (base / "files" / ".config" / "x" / "k.env").write_text("K=1\n")
    (base / "venv-freeze.txt").write_text(freeze)
    return gen


SPEC = [{"name": "cos-hub", "dir": "cos-hub", "exclude": [], "units": [], "files": [".config/x/k.env"]}]


def test_neighbour_venv_rebuilt_only_when_freeze_changes(tmp_path):
    home = tmp_path / "home"
    (home / "cos-hub" / ".venv").mkdir(parents=True)
    (home / "cos-hub" / ".venv" / "keep").write_text("k")
    runner = FakeRun()
    ss.restore_neighbours(_neighbour_gen(tmp_path), home, SPEC, run=runner)
    assert len(runner.calls) == 2 and runner.calls[0][1] == "venv"
    assert runner.calls[1][1:3] == ["pip", "install"]
    assert (home / "cos-hub" / "app.py").is_file()
    assert (home / "cos-hub" / ".venv" / "keep").read_text() == "k"
    assert (home / ".config" / "x" / "k.env").read_text() == "K=1\n"
    assert (home / "standby" / "units" / "cos-hub" / "cos-hub.service").is_file()
    assert not (home / ".config" / "systemd").exists()
    again = FakeRun()
    ss.restore_neighbours(_neighbour_gen(tmp_path), home, SPEC, run=again)
    assert again.calls == []
    changed = FakeRun()
    ss.restore_neighbours(_neighbour_gen(tmp_path, "a==2\n"), home, SPEC, run=changed)
    assert len(changed.calls) == 2


def test_neighbour_rebuild_failure_fails(tmp_path):
    with pytest.raises(ss.SyncError):
        ss.restore_neighbours(_neighbour_gen(tmp_path), tmp_path / "home", SPEC,
                              run=FakeRun({"venv": 1}))


def test_rsync_remote_falls_through_to_second_target(tmp_path):
    listing = ("drwxr-xr-x          4,096 2026/11/20 20:00:00 prod_2026-11-20_2000\n"
               "-rw-r--r--              5 2026/11/20 20:00:00 notes.txt\n")

    def fake(argv, **kw):
        fake.calls.append(argv)
        if any(a.startswith("down:") for a in argv):
            return subprocess.CompletedProcess(argv, 255, "", "timed out")
        return subprocess.CompletedProcess(argv, 0, listing, "")
    fake.calls = []
    r = ss.RsyncRemote(["down", "up"], home=tmp_path, timeout=7, run=fake)
    assert r.generations() == [GEN]
    assert r.has(GEN, "BACKUP_OK")
    sshs = [a for c in fake.calls for a in c if a.startswith("ssh ")]
    assert all("BatchMode=yes" in s and "IdentitiesOnly=yes" in s
               and "ConnectTimeout=7" in s and "standby_pull" in s for s in sshs)
    assert [c for c in fake.calls if any(a == "up:prod_2026-11-20_2000/BACKUP_OK" for a in c)]
    assert sum(any(a.startswith("down:") for a in c) for c in fake.calls) == 1


def test_rsync_remote_no_targets_is_a_clear_failure(tmp_path):
    with pytest.raises(ss.SyncError, match="no ssh address"):
        ss.RsyncRemote([], home=tmp_path, run=FakeRun()).generations()


def test_rsync_fetch_flags(tmp_path):
    def fake(argv, **kw):
        fake.argv = argv
        return subprocess.CompletedProcess(
            argv, 0, "Total transferred file size: 1,234 bytes\n", "")
    r = ss.RsyncRemote(["up"], home=tmp_path, run=fake)
    r._target = "up"
    n = r.fetch(GEN, tmp_path / "d", tmp_path / "b")
    assert n == 1234 and "--no-links" in fake.argv
    assert any(a.startswith("--link-dest=") for a in fake.argv)


def _planted(tmp_path, rel):
    gen = _neighbour_gen(tmp_path)
    f = gen / "neighbours" / "cos-hub" / "files" / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("evil")
    return gen


@pytest.mark.parametrize("rel", [".ssh/authorized_keys", "dev/x.py",
                                 ".config/systemd/user/x.service"])
def test_unlisted_neighbour_file_fails_and_writes_nothing(tmp_path, rel):
    home = tmp_path / "home"
    with pytest.raises(ss.SyncError, match="not one of its listed files"):
        ss.restore_neighbours(_planted(tmp_path, rel), home, SPEC, run=FakeRun())
    assert not (home / rel).exists() and not (home / "cos-hub").exists()


@pytest.mark.parametrize("folder", ["dev", ".ssh", ".", "", "a/b", ".config"])
def test_bad_neighbour_folder_fails(tmp_path, folder):
    home = tmp_path / "home"
    (home / "dev").mkdir(parents=True)
    spec = [dict(SPEC[0], dir=folder)]
    with pytest.raises(ss.SyncError):
        ss.restore_neighbours(_neighbour_gen(tmp_path), home, spec, run=FakeRun(),
                              repo=home / "dev")
    assert not (home / "dev" / "app.py").exists()


def test_local_standby_toml_is_skipped_not_refused(tmp_path, env):
    repo, home = env
    local = repo / "config" / "local"
    local.mkdir(parents=True)
    (local / "standby.toml").write_text("mine = 1")
    remote = FakeRemote({GEN: FakeRemote.make(_files(
        extra={"config/local/standby.toml": b"[sync] max_shrink_pct = 100"}))})
    status, _, _ = _go(tmp_path, env, remote=remote)
    assert status["result"] == "synced", status["detail"]
    assert (local / "standby.toml").read_text() == "mine = 1"
    assert "config/local/standby.toml" in status["detail"]
    assert (repo / "config" / "local" / "a.toml").is_file()


def test_local_standby_toml_not_created_when_absent(tmp_path, env):
    remote = FakeRemote({GEN: FakeRemote.make(_files(
        extra={"config/local/standby.toml": b"x"}))})
    _go(tmp_path, env, remote=remote)
    assert not (env[0] / "config" / "local" / "standby.toml").exists()


def test_failed_restore_then_outage_then_not_unchanged(tmp_path, env, monkeypatch):
    real = ss.rb.restore
    monkeypatch.setattr(ss.rb, "restore", lambda *a, **k: {
        "restored": [], "kept": [], "bad": ["x.db: corrupt"], "refused": [],
        "marked_ok": True})
    assert _go(tmp_path, env)[0]["result"] == "failed"
    assert not (tmp_path / "gens" / GEN / "SYNCED").exists()
    monkeypatch.setattr(ss.rb, "restore", real)

    class Down(FakeRemote):
        def generations(self):
            raise ss.SyncError("the peer did not answer")
    status, _, _ = _go(tmp_path, env, remote=Down())
    assert status["generation"] is None and status["result"] == "failed"
    remote = FakeRemote()
    status, _, _ = _go(tmp_path, env, remote=remote, now=dt.datetime(2026, 11, 22, 22, 15))
    assert status["result"] == "synced" and remote.fetched == []
    assert (tmp_path / "gens" / GEN / "SYNCED").is_file()


def test_has_outage_is_failure_not_absence(tmp_path):
    def fake(argv, **kw):
        return subprocess.CompletedProcess(argv, 255, "", "timed out")
    r = ss.RsyncRemote(["up"], home=tmp_path, run=fake)
    r._target = "up"
    with pytest.raises(ss.SyncError):
        r.has(GEN, "BACKUP_OK")
    r._run = lambda argv, **kw: subprocess.CompletedProcess(argv, 23, "", "")
    assert r.has(GEN, "BACKUP_OK") is False


def test_outage_in_has_fails_night_without_fetch(tmp_path, env):
    class Flaky(FakeRemote):
        def has(self, gen, name):
            raise ss.SyncError("outage")
    remote = Flaky()
    status, _, _ = _go(tmp_path, env, remote=remote)
    assert status["result"] == "failed" and remote.fetched == []


def test_never_goes_backwards(tmp_path, env, monkeypatch):
    newer = "prod_2026-11-21_2000"
    remote = FakeRemote({newer: FakeRemote.make()})
    assert _go(tmp_path, env, remote=remote, now=dt.datetime(2026, 11, 22, 1, 0))[0][
        "result"] == "synced"
    old = FakeRemote({GEN: FakeRemote.make()})
    called = []
    monkeypatch.setattr(ss.rb, "restore", lambda *a, **k: called.append(1))
    status, _, runner = _go(tmp_path, env, remote=old, now=dt.datetime(2026, 11, 22, 2, 0))
    assert status["result"] == "failed" and "backwards" in status["detail"]
    assert called == [] and old.fetched == []
