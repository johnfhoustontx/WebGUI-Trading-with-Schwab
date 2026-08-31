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
    "shared/data",
    "schwab-proxy/data",
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


def test_every_carried_secret_is_repo_relative():
    """EXTRA_FILES entries are joined onto REPO_ROOT; an absolute path or a `..`
    would silently write outside the generation."""
    for rel in bl.EXTRA_FILES:
        p = pathlib.Path(rel)
        assert not p.is_absolute(), rel
        assert ".." not in p.parts, rel
