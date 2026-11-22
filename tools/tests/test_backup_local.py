"""The backup must SWEEP the data trees, not enumerate files.

WHY THIS TEST EXISTS. The first version backed up ``*.db`` plus a named list of
loose files. That shape silently lost, during the 2026-08-29 migration:

    webgui/data/eod/                       47 dated report archives
    portfolio-analyzer/data/entries.json   86 real trades -- the ledger
    webgui/data/settings.json              alert / voice / autoclose prefs

Everything one directory down fell through, and nothing said so. The gap
surfaced only because someone went looking for a report -- which is the worst
way to discover a backup was incomplete, and only a little better than
discovering it during a restore.

A named list must be remembered every time a feature starts writing somewhere
new. A sweep must not. These tests pin the sweep.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import backup_local as bl  # noqa: E402


def test_the_data_trees_are_swept_not_listed():
    """A directory, not a file, is the unit of coverage."""
    assert bl.DATA_TREES, "no data trees configured"
    for t in bl.DATA_TREES:
        assert not pathlib.Path(t).suffix, f"{t} looks like a file, not a tree"


@pytest.mark.parametrize("tree", [
    "webgui/data",                 # EOD archives + settings.json
    "portfolio-analyzer/data",     # entries.json -- the ledger
    "options-scanner/data",        # reports, instrumentation, the workbook
    "sentiment-dashboard/data",
    "trade-analyzer/data",         # the swing model artifact
    "services/trade_svc/data",
    "services/options_svc/data",  # Model Health reports
    "shared/data",
    "schwab-proxy/data",
    "config/local",                # Settings -> Configuration overrides
])
def test_every_gitignored_data_tree_is_covered(tree):
    """Named individually so a deletion from DATA_TREES fails HERE, with the
    directory's name in the failure, rather than being noticed months later by
    its absence from a restore."""
    assert tree in bl.DATA_TREES


def test_the_three_files_the_first_version_lost_would_now_be_swept():
    """The regression, stated as the paths that were actually missed."""
    lost = ("webgui/data/eod/2026-08-28/summary.html",
            "portfolio-analyzer/data/entries.json",
            "webgui/data/settings.json")
    for rel in lost:
        assert any(rel.startswith(t + "/") for t in bl.DATA_TREES), rel


def test_a_file_nested_deep_in_a_tree_is_picked_up(tmp_path, monkeypatch):
    """The exact failure mode: a *.db-less file several directories down.

    Driven through the real sweep against a fake checkout, so it tests the
    walking code rather than restating the constant."""
    monkeypatch.setattr(bl, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bl, "DATA_TREES", ("webgui/data",))
    monkeypatch.setattr(bl, "SWEEP_EXCLUDE", ())
    monkeypatch.setattr(bl, "EXTRA_FILES", ())
    deep = tmp_path / "webgui" / "data" / "eod" / "2026-08-28" / "summary.html"
    deep.parent.mkdir(parents=True)
    deep.write_text("<html>report</html>", encoding="utf-8")

    dest = tmp_path / "out"
    bl.main(["--dest", str(dest)])

    gen = next(dest.iterdir())
    assert (gen / "webgui/data/eod/2026-08-28/summary.html").read_text(
        encoding="utf-8") == "<html>report</html>"


def test_databases_are_not_swept_as_plain_files(tmp_path, monkeypatch):
    """*.db goes through the ONLINE BACKUP API. Sweeping one as a plain file
    would copy it mid-write -- a torn database that passes a size check and
    fails integrity_check months later."""
    monkeypatch.setattr(bl, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bl, "DATA_TREES", ("webgui/data",))
    monkeypatch.setattr(bl, "SWEEP_EXCLUDE", ())
    monkeypatch.setattr(bl, "EXTRA_FILES", ())
    monkeypatch.setattr(bl, "backup_redis", lambda dst: (True, "stubbed"))

    d = tmp_path / "webgui" / "data"
    d.mkdir(parents=True)
    import sqlite3
    con = sqlite3.connect(d / "store.db")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()

    dest = tmp_path / "out"
    bl.main(["--dest", str(dest)])
    gen = next(dest.iterdir())
    copied = gen / "webgui/data/store.db"
    assert copied.exists()
    # It arrived via the backup API, so it is a valid database, not bytes.
    con = sqlite3.connect(f"file:{copied}?mode=ro", uri=True)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT x FROM t").fetchone()[0] == 1
    con.close()


def test_exclusions_are_few_and_each_is_a_regenerable_cache():
    """An exclusion is a decision to LOSE data on restore, so the bar is that it
    can be rebuilt from something else that IS backed up. The voice clips are
    re-synthesised on demand from their phrase text."""
    assert bl.SWEEP_EXCLUDE == ("webgui/data/voice",)


def test_secrets_are_still_carried_separately():
    """They live outside the data trees, so the sweep does not reach them."""
    for rel in ("shared/tokens.json", "shared/appsettings.json",
                "config/env.local.toml"):
        assert rel in bl.EXTRA_FILES


#############################################
# Pruning the OFFSITE retry caches, not just the generations
#############################################
#
# The encrypted archive is kept when an upload fails, deliberately, so a retry
# does not re-encrypt 1.5 GB. But prune() only ever removed directories, so on a
# run of failed uploads the .tar.age files accumulated at ~1.6 GB PER NIGHT and
# nothing collected them -- on the box that runs the trading stack, where a full
# disk is an outage. Found 2026-08-30 with the timer newly enabled and the Drive
# remote not yet configured, which is precisely the state that triggers it.


def _gen(root, name, *, archive=False):
    """A dated generation directory, optionally with its encrypted archive."""
    d = root / name
    (d / "options-scanner").mkdir(parents=True)
    (d / "options-scanner" / "signals.db").write_bytes(b"x")
    if archive:
        (root / f"{name}.tar.age").write_bytes(b"age-encryption.org/v1\n")
    return d


def test_prune_keeps_the_newest_generations(tmp_path):
    """Non-vacuity: the behaviour that already worked must keep working."""
    for n in ("prod_2026-08-01_2000", "prod_2026-08-02_2000", "prod_2026-08-03_2000",
              "prod_2026-08-04_2000"):
        _gen(tmp_path, n)
    bl.prune(tmp_path, 3)
    left = sorted(d.name for d in tmp_path.iterdir() if d.is_dir())
    assert left == ["prod_2026-08-02_2000", "prod_2026-08-03_2000", "prod_2026-08-04_2000"]


def test_prune_drops_the_archive_of_a_dropped_generation(tmp_path):
    """The .tar.age is a retry cache for ITS generation. Once the generation is
    gone the archive is unreachable by any retry, and is pure disk cost."""
    for n in ("prod_2026-08-01_2000", "prod_2026-08-02_2000", "prod_2026-08-03_2000",
              "prod_2026-08-04_2000"):
        _gen(tmp_path, n, archive=True)
    bl.prune(tmp_path, 3)
    archives = sorted(p.name for p in tmp_path.glob("*.tar.age"))
    assert archives == ["prod_2026-08-02_2000.tar.age",
                        "prod_2026-08-03_2000.tar.age",
                        "prod_2026-08-04_2000.tar.age"], (
        "a dropped generation left its 1.6 GB archive behind")


def test_prune_keeps_the_archive_of_a_kept_generation(tmp_path):
    """The retry cache must survive for generations that still exist, or the
    next run re-encrypts 1.5 GB for nothing."""
    _gen(tmp_path, "prod_2026-08-04_2000", archive=True)
    bl.prune(tmp_path, 3)
    assert (tmp_path / "prod_2026-08-04_2000.tar.age").exists()


def test_prune_reports_what_it_removed(tmp_path):
    """The run prints this line, so it has to name the generations."""
    for n in ("prod_2026-08-01_2000", "prod_2026-08-02_2000"):
        _gen(tmp_path, n, archive=True)
    dropped = bl.prune(tmp_path, 1)
    assert dropped == ["prod_2026-08-01_2000"]


def test_the_env_file_is_carried():
    """`.env` holds MEMURAI_PASSWORD, ALPHAVANTAGE_API_KEY and anything else the
    UNITS read -- and it is loaded as `EnvironmentFile=` with NO leading dash, so
    a missing one does not degrade, it fails the unit.

    It was absent from EXTRA_FILES while config/env.local.toml (its sibling, one
    directory up from the same kind of machine-local config) was present, so a
    restore produced a stack that would not start and nothing to restore it from.
    Found 2026-08-31 while tracing where an API key lives."""
    assert ".env" in bl.EXTRA_FILES


def test_the_public_live_env_file_is_carried_too():
    """`.env.live` is the PUBLIC process's own EnvironmentFile, split off from
    `.env` so the one internet-facing unit does not hold the fleet's strongest
    credentials. It is loaded with no leading dash for the same reason its
    sibling is, so it has the same restore failure: an archive that looks
    complete and a live unit that will not start.

    ⚠ A separate assertion rather than a widened one, because `.env.live` is a
    NEW name -- `.gitignore`'s `.env` line does not match it either, and the two
    omissions have the same root."""
    assert ".env.live" in bl.EXTRA_FILES


def test_every_carried_secret_is_repo_relative():
    """EXTRA_FILES entries are joined onto REPO_ROOT; an absolute path or a `..`
    would silently write outside the generation."""
    for rel in bl.EXTRA_FILES:
        p = pathlib.Path(rel)
        assert not p.is_absolute(), rel
        assert ".." not in p.parts, rel


#############################################
# What the backup omitted, and what keeps it from omitting the next thing
#############################################
#
# Audit AR-04 (2026-10-03). The backup did not carry the LOGIN STORE
# (shared/webgui_auth.json - the password hash, the second-factor secret and the
# session secret: lose it and the owner is locked out of a restored app), the
# Google Calendar service-account key, or the vendor API keys. Each had been
# added to .gitignore, which is exactly the moment a file becomes something only
# a backup can bring back.

@pytest.mark.parametrize("rel", [
    "shared/webgui_auth.json",          # the login: without it a restore locks the owner out
    "shared/google_calendar_sa.json",   # the trade idea's calendar events
    "shared/alphavantage_key.txt",
    "shared/massive_key.txt",
    "shared/proxy_secret.txt",          # the proxy's account routes refuse without it
    "deploy/site/ideas.json",           # the public site's published trade ideas
])
def test_a_file_only_a_backup_can_bring_back_is_carried(rel):
    assert rel in bl.EXTRA_FILES


@pytest.mark.parametrize("tree", ["deploy/site/ideas", "deploy/site/reports"])
def test_the_public_sites_generated_state_is_swept(tree):
    assert tree in bl.DATA_TREES


def _gitignored_secret_files():
    """Every ``shared/<file>`` line in .gitignore: files this repository says
    must never be committed, which makes the backup their only other copy."""
    out = []
    for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("shared/") and not line.endswith("/") and "*" not in line:
            out.append(line)
    return out


def test_every_gitignored_secret_under_shared_is_backed_up_or_named_as_left_out():
    """THE GUARD. A file added to .gitignore and not to the backup is lost on a
    rebuild, and nothing fails when that happens - which is how the login store
    went unbacked. Adding a ``shared/`` line to .gitignore now fails here until
    it is either carried or listed in ``NOT_BACKED_UP`` with its reason."""
    found = _gitignored_secret_files()
    assert len(found) >= 6, "the .gitignore walk found too little to mean anything"
    unaccounted = [f for f in found
                   if f not in bl.EXTRA_FILES and f not in bl.NOT_BACKED_UP]
    assert not unaccounted, unaccounted


def test_the_retired_feed_login_is_left_out_on_purpose():
    """Nothing reads it since cos.'s /feed/ route was removed (2026-11-22); it
    stays gitignored only because leftover copies hold a bcrypt hash."""
    assert "shared/feed_basic_auth.txt" not in bl.EXTRA_FILES
    assert "shared/feed_basic_auth.txt" in bl.NOT_BACKED_UP


def test_every_deliberate_omission_states_its_reason():
    for rel, reason in bl.NOT_BACKED_UP.items():
        assert rel not in bl.EXTRA_FILES, rel
        assert len(reason) > 20, rel


#############################################
# Retention: a weekly generation, and never losing the last good one
#############################################
#
# Three generations on weeknights only meant the oldest copy was three trading
# days old: a problem noticed on a Monday had no copy from before the previous
# Wednesday. And pruning ran BEFORE the run's own failure check, so a night that
# produced a damaged generation could still push the last good one out.

def _names(*days):
    return [f"prod_{d}_2000" for d in days]


def test_select_keep_is_the_newest_dailies_plus_one_per_earlier_week():
    names = _names("2026-09-21", "2026-09-22", "2026-09-25",      # week 39
                   "2026-09-28", "2026-09-29", "2026-09-30",      # week 40
                   "2026-10-01", "2026-10-02")
    kept = bl.select_keep(names, keep=3, keep_weekly=1)
    # the three newest, plus the newest generation of the week before them
    assert kept == set(_names("2026-10-02", "2026-10-01", "2026-09-30", "2026-09-25"))


def test_select_keep_takes_one_generation_per_week_for_several_weeks():
    names = _names("2026-09-11", "2026-09-18", "2026-09-24", "2026-09-25",
                   "2026-10-01", "2026-10-02")
    kept = bl.select_keep(names, keep=2, keep_weekly=3)
    assert kept == set(_names("2026-10-02", "2026-10-01",
                              "2026-09-25", "2026-09-18", "2026-09-11"))


def test_select_keep_with_no_weekly_is_the_old_rule():
    names = _names("2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04")
    assert bl.select_keep(names, keep=3, keep_weekly=0) == set(names[1:])


def test_a_name_with_no_readable_date_is_never_pruned():
    """Something in the backup directory this tool did not name is not this
    tool's to delete, and it must not use up a generation slot either."""
    names = ["prod_scratch", "prod_2026-09-30_2000", "prod_2026-10-01_2000",
             "prod_2026-10-02_2000"]
    kept = bl.select_keep(names, keep=2, keep_weekly=0)
    assert kept == {"prod_scratch", "prod_2026-10-01_2000", "prod_2026-10-02_2000"}


def test_prune_keeps_a_weekly_generation(tmp_path):
    for n in _names("2026-09-25", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"):
        _gen(tmp_path, n)
    dropped = bl.prune(tmp_path, 3, keep_weekly=1)
    assert dropped == ["prod_2026-09-29_2000"]
    assert (tmp_path / "prod_2026-09-25_2000").is_dir()


def test_prune_never_drops_the_newest_generation_that_finished_clean(tmp_path):
    """Four bad nights in a row must not push out the last good copy."""
    good = _gen(tmp_path, "prod_2026-09-01_2000")
    (good / bl.OK_MARKER).write_text("ok", encoding="utf-8")
    for n in _names("2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"):
        _gen(tmp_path, n)                       # no marker: each had a failure
    bl.prune(tmp_path, 3, keep_weekly=0)
    assert good.is_dir()
    assert not (tmp_path / "prod_2026-09-02_2000").exists()


def test_a_clean_run_marks_its_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "REPO_ROOT", tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(bl, "DATA_TREES", ())
    monkeypatch.setattr(bl, "EXTRA_FILES", ())
    monkeypatch.setattr(bl, "backup_redis", lambda dst: (True, "stubbed"))
    assert bl.main(["--dest", str(tmp_path / "out"), "--no-offsite"]) == 0
    gen = next((tmp_path / "out").iterdir())
    assert (gen / bl.OK_MARKER).is_file()


def test_a_run_with_a_failure_does_not_mark_its_generation_and_prunes_nothing_good(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "REPO_ROOT", tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(bl, "DATA_TREES", ())
    monkeypatch.setattr(bl, "EXTRA_FILES", ())
    monkeypatch.setattr(bl, "backup_redis", lambda dst: (False, "redis is down"))
    out = tmp_path / "out"
    good = _gen(out, "prod_2026-09-01_2000")
    (good / bl.OK_MARKER).write_text("ok", encoding="utf-8")
    for n in _names("2026-09-02", "2026-09-03", "2026-09-04"):
        _gen(out, n)
    assert bl.main(["--dest", str(out), "--no-offsite", "--keep", "3"]) == 1
    newest = sorted(d for d in out.iterdir() if d.is_dir())[-1]
    assert not (newest / bl.OK_MARKER).exists()
    assert good.is_dir()


def test_the_offsite_keeps_a_weekly_archive_too():
    names = [n + ".tar.age" for n in _names("2026-09-25", "2026-09-29", "2026-09-30",
                                            "2026-10-01", "2026-10-02")]
    drop = bl.remote_to_delete(names, keep=3, keep_weekly=1)
    assert drop == ["prod_2026-09-29_2000.tar.age"]


#############################################
# The offsite archive is COMPRESSED, and old ones leave Drive for good
#############################################
#
# 2026-11-11: the upload failed with storageQuotaExceeded. Drive held four live
# archives (13.8 GB) and 53.4 GiB of TRASH: rclone's deletefile moves a Drive
# file to the trash, which still counts against the quota, so every pruned
# archive kept its space. Same night: zstd -3 measured 0.72 / 0.66 on the two
# gamma stores at ~180 MB/s, ~30% off the whole generation.


def test_the_archive_is_tar_then_zstd_then_age():
    cmd = bl.archive_command(pathlib.PurePosixPath("/b/prod_2026-11-11_2000"),
                             pathlib.PurePosixPath("/b/prod_2026-11-11_2000.tar.zst.age"),
                             "age1xyz", level=3, threads=2)
    stages = [s.strip().split()[0] for s in cmd.split(">")[0].split("|")]
    assert stages == ["tar", "zstd", "age"], cmd
    assert " -3 " in cmd and " -T2 " in cmd, cmd


def test_a_new_archive_is_named_for_its_compression():
    assert bl.ARCHIVE_SUFFIX == ".tar.zst.age"


def test_an_archive_name_gives_back_its_generation_either_way():
    assert bl.archive_stem("prod_2026-11-11_2000.tar.zst.age") == "prod_2026-11-11_2000"
    assert bl.archive_stem("prod_2026-11-10_2005.tar.age") == "prod_2026-11-10_2005"
    assert bl.archive_stem("prod_2026-11-10_2005.txt") is None


def test_the_offsite_prune_counts_old_and_new_archives_as_one_rotation():
    """The first compressed nights sit beside the uncompressed ones: both kinds
    must age out by date, or the old .tar.age files stay on Drive forever."""
    names = ["prod_2026-11-06_2004.tar.age", "prod_2026-11-09_2002.tar.age",
             "prod_2026-11-10_2005.tar.age", "prod_2026-11-11_2000.tar.zst.age",
             "prod_2026-11-12_2000.tar.zst.age", "prod_2026-10-30_2003.tar.age"]
    drop = bl.remote_to_delete(names, keep=3, keep_weekly=1)
    assert drop == ["prod_2026-10-30_2003.tar.age", "prod_2026-11-09_2002.tar.age"]


def test_prune_drops_a_compressed_archive_with_its_generation(tmp_path):
    for n in ("prod_2026-08-01_2000", "prod_2026-08-02_2000"):
        _gen(tmp_path, n)
        (tmp_path / f"{n}.tar.zst.age").write_bytes(b"age-encryption.org/v1\n")
    (tmp_path / "prod_2026-08-01_2000.tar.age").write_bytes(b"age-encryption.org/v1\n")
    bl.prune(tmp_path, 1)
    left = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert left == ["prod_2026-08-02_2000.tar.zst.age"]


def test_an_offsite_delete_skips_the_drive_trash(tmp_path, monkeypatch):
    calls = []

    class _Done:
        def __init__(self, stdout=""):
            self.returncode, self.stdout, self.stderr = 0, stdout, ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["rclone", "lsf"]:
            return _Done("\n".join(n + ".tar.age" for n in _names(
                "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09")))
        return _Done()

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    f = tmp_path / "prod_2026-11-10_2000.tar.zst.age"
    f.write_bytes(b"x")
    ok, _ = bl.upload(f, keep=3)
    deletes = [c for c in calls if c[:2] == ["rclone", "deletefile"]]
    assert ok and deletes, calls
    assert all("--drive-use-trash=false" in c for c in deletes), deletes


@pytest.mark.parametrize("raw, want", [(None, 3), (5, 5), (0, 3), (20, 3), (True, 3), ("3", 3)])
def test_the_zstd_level_is_read_from_config_and_bounded(monkeypatch, raw, want):
    cfg = {"offsite": {} if raw is None else {"zstd_level": raw}}
    monkeypatch.setattr(bl, "_load_config", lambda: cfg)
    assert bl.zstd_level() == want


@pytest.mark.parametrize("raw, want", [(None, 2), (4, 4), (0, 2), (-1, 2), (2.5, 2)])
def test_the_zstd_threads_are_read_from_config_and_bounded(monkeypatch, raw, want):
    cfg = {"offsite": {} if raw is None else {"zstd_threads": raw}}
    monkeypatch.setattr(bl, "_load_config", lambda: cfg)
    assert bl.zstd_threads() == want


def test_the_raw_feed_store_is_never_backed_up(tmp_path):
    """~1 GB a session of raw chains, re-collected rather than restored
    (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md §4). The sweep takes
    every *.db, so the exclusion has to be explicit."""
    raw = tmp_path / "services" / "feed_svc" / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "2026-10-14.db").write_bytes(b"x" * 10)
    keep = tmp_path / "options-scanner" / "data"
    keep.mkdir(parents=True)
    (keep / "signals.db").write_bytes(b"x" * 10)
    assert [p.name for p in bl.db_paths(tmp_path)] == ["signals.db"]


# ── the commit and the neighbours (warm standby) ────────────────────────────

import os  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402


class _Done:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _spec(**kw):
    base = {"name": "cos-hub", "dir": "cos-hub", "exclude": [".venv", "logs"],
            "units": [], "files": []}
    base.update(kw)
    return base


def test_generation_carries_git_head(tmp_path):
    out = tmp_path / "gen"
    out.mkdir()
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return _Done(0, "abc123def\n")

    assert bl.write_git_head(out, tmp_path, run=run) == "abc123def"
    assert (out / bl.GIT_HEAD).read_text().strip() == "abc123def"
    assert calls[0][:3] == ["git", "-C", str(tmp_path)] and "rev-parse" in calls[0]


def test_git_head_failure_is_a_failure_and_blocks_ok_marker(tmp_path, monkeypatch):
    out = tmp_path / "gen"
    out.mkdir()
    assert bl.write_git_head(out, tmp_path, run=lambda c, **k: _Done(128, "", "no")) is None
    assert not (out / bl.GIT_HEAD).exists()
    monkeypatch.setattr(bl, "REPO_ROOT", tmp_path / "repo")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    monkeypatch.setattr(bl, "DATA_TREES", ())
    monkeypatch.setattr(bl, "EXTRA_FILES", ())
    monkeypatch.setattr(bl, "backup_redis", lambda dst: (True, "stubbed"))
    monkeypatch.setattr(bl, "write_git_head", lambda o, r, run=None: None)
    monkeypatch.setattr(bl, "backup_neighbour", lambda *a, **k: [])
    assert bl.main(["--dest", str(tmp_path / "o"), "--no-offsite"]) == 1
    gen = next((tmp_path / "o").iterdir())
    assert not (gen / bl.OK_MARKER).exists()


def test_neighbour_sqlite_goes_through_online_backup(tmp_path):
    home, out = tmp_path / "home", tmp_path / "gen"
    data = home / "cos-hub" / "data"
    data.mkdir(parents=True)
    con = sqlite3.connect(data / "hub.sqlite3")
    con.execute("create table t(x)")
    con.execute("insert into t values (7)")
    con.commit()
    con.close()
    (data / "hub.sqlite3-wal").write_bytes(b"wal")
    (data / "hub.sqlite3-shm").write_bytes(b"shm")
    assert bl.backup_neighbour(_spec(), home, out, run=lambda c, **k: _Done()) == []
    dst = out / bl.NEIGHBOURS_DIR / "cos-hub" / "dir" / "data" / "hub.sqlite3"
    c2 = sqlite3.connect(dst)
    assert c2.execute("select x from t").fetchone() == (7,)
    c2.close()
    assert not list((out / bl.NEIGHBOURS_DIR).rglob("*-wal"))
    assert not list((out / bl.NEIGHBOURS_DIR).rglob("*-shm"))


def test_neighbour_excludes(tmp_path):
    home, out = tmp_path / "home", tmp_path / "gen"
    for rel in (".venv/x", "logs/a.jsonl", "prompts/p.md"):
        f = home / "cos-hub" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("1")
    bl.backup_neighbour(_spec(), home, out, run=lambda c, **k: _Done())
    base = out / bl.NEIGHBOURS_DIR / "cos-hub" / "dir"
    assert (base / "prompts" / "p.md").is_file()
    assert not (base / ".venv").exists() and not (base / "logs").exists()


def test_neighbour_units_and_files_copied(tmp_path):
    home, out = tmp_path / "home", tmp_path / "gen"
    (home / "cos-hub").mkdir(parents=True)
    ud = home / ".config" / "systemd" / "user"
    (ud / "neuralstrike-report@globex.service.d").mkdir(parents=True)
    (ud / "neuralstrike-report@globex.service.d" / "o.conf").write_text("[Service]")
    for n in ("neuralstrike-report-am.timer", "neuralstrike-report-pm.timer", "other.timer"):
        (ud / n).write_text("t")
    env = home / ".config" / "neuralstrike" / "claude.env"
    env.parent.mkdir(parents=True)
    env.write_text("K=1")
    spec = _spec(units=["neuralstrike-report-*.timer", "neuralstrike-report@globex.service.d"],
                 files=[".config/neuralstrike/claude.env"])
    assert bl.backup_neighbour(spec, home, out, run=lambda c, **k: _Done()) == []
    units = out / bl.NEIGHBOURS_DIR / "cos-hub" / "units"
    assert sorted(p.name for p in units.iterdir()) == [
        "neuralstrike-report-am.timer", "neuralstrike-report-pm.timer",
        "neuralstrike-report@globex.service.d"]
    assert (units / "neuralstrike-report@globex.service.d" / "o.conf").is_file()
    f = out / bl.NEIGHBOURS_DIR / "cos-hub" / "files" / ".config" / "neuralstrike" / "claude.env"
    assert f.read_text() == "K=1"
    if os.name != "nt":
        assert f.stat().st_mode & 0o777 == 0o600


def test_absent_neighbour_is_not_a_failure(tmp_path, capsys):
    out = tmp_path / "gen"
    assert bl.backup_neighbour(_spec(), tmp_path / "home", out, run=lambda c, **k: _Done()) == []
    assert "cos-hub (absent)" in capsys.readouterr().out


def test_venv_freeze_written_and_its_failure_counts(tmp_path):
    home, out = tmp_path / "home", tmp_path / "gen"
    py = home / "cos-hub" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    seen = []

    def ok(cmd, **kw):
        seen.append(cmd)
        return _Done(0, "pkg==1\n")

    assert bl.backup_neighbour(_spec(), home, out, run=ok) == []
    assert (out / bl.NEIGHBOURS_DIR / "cos-hub" / "venv-freeze.txt").read_text() == "pkg==1\n"
    assert seen[0] == [str(home / ".local" / "bin" / "uv"), "pip", "freeze",
                       "--python", str(py)]
    bad = bl.backup_neighbour(_spec(), home, tmp_path / "gen2",
                              run=lambda c, **k: _Done(1, "", "boom"))
    assert len(bad) == 1 and "cos-hub" in bad[0]


def _venv(home, cfg):
    py = home / "cos-hub" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    if cfg is not None:
        (home / "cos-hub" / ".venv" / "pyvenv.cfg").write_text(cfg)


@pytest.mark.parametrize("cfg,want", [
    ("home = /x/bin\nimplementation = CPython\nversion_info = 3.12.3\n", "3.12"),
    ("home = /usr/bin\nversion = 3.11.9\n", "3.11"),
])
def test_neighbour_venv_python_version_is_recorded(tmp_path, cfg, want):
    home, out = tmp_path / "home", tmp_path / "gen"
    _venv(home, cfg)
    assert bl.backup_neighbour(_spec(), home, out, run=lambda c, **k: _Done(0, "a==1\n")) == []
    f = out / bl.NEIGHBOURS_DIR / "cos-hub" / "python-version"
    assert f.read_text() == want + "\n"


@pytest.mark.parametrize("cfg", [None, "home = /x\n", "version_info = banana\n"])
def test_unreadable_python_version_is_said_not_failed(tmp_path, capsys, cfg):
    home, out = tmp_path / "home", tmp_path / "gen"
    _venv(home, cfg)
    assert bl.backup_neighbour(_spec(), home, out, run=lambda c, **k: _Done(0, "a==1\n")) == []
    assert not (out / bl.NEIGHBOURS_DIR / "cos-hub" / "python-version").exists()
    assert "cos-hub: venv python version not recorded" in capsys.readouterr().out


def test_unmatched_unit_glob_is_said_not_failed(tmp_path, capsys):
    home, out = tmp_path / "home", tmp_path / "gen"
    (home / "cos-hub").mkdir(parents=True)
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    spec = _spec(units=["cos-hub.service", "nothing-*.timer"])
    assert bl.backup_neighbour(spec, home, out, run=lambda c, **k: _Done()) == []
    text = capsys.readouterr().out
    assert "cos-hub: unit cos-hub.service matched nothing" in text
    assert "cos-hub: unit nothing-*.timer matched nothing" in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_copied_neighbour_files_keep_only_the_owner_exec_bit(tmp_path):
    home, out = tmp_path / "home", tmp_path / "gen"
    d = home / "cos-hub"
    d.mkdir(parents=True)
    (d / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(d / "run.sh", 0o755)
    (d / "app.py").write_text("x")
    os.chmod(d / "app.py", 0o664)
    assert bl.backup_neighbour(_spec(), home, out, run=lambda c, **k: _Done()) == []
    base = out / bl.NEIGHBOURS_DIR / "cos-hub" / "dir"
    assert (base / "run.sh").stat().st_mode & 0o777 == 0o700
    assert (base / "app.py").stat().st_mode & 0o777 == 0o600
