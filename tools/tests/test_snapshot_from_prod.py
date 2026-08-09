"""Tests for the prod -> dev snapshot tool.

This is the highest-blast-radius tool in the repo, so the tests are weighted
towards the three ways it could do real damage rather than towards coverage:

1. running in the WRONG DIRECTION and overwriting prod's live data;
2. ``FLUSHDB`` against the wrong Redis DB (both environments share one Memurai
   server; only the logical index separates them, so a wrong index destroys
   prod's entire published cache);
3. copying a TORN database — ``gex_history.db`` is ~1.4 GB and prod writes to it
   every minute during market hours.

Note on the direction guard: under pytest ``repo_paths`` pins ``ENV_NAME`` to
``"prod"`` regardless of the marker file, so the guard always refuses during a
test run. That is exactly right — the tool can never fire for real inside a test
— but it means the "allowed" case has to be exercised by monkeypatching
``ENV_NAME`` **on the tool module**, never on ``repo_paths``.

``tools/`` has no ``__init__.py``; the sys.path insert below mirrors
tools/tests/test_stop_all.py.
"""
import json
import inspect
import pathlib
import shutil
import socket
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import snapshot_from_prod as snap  # noqa: E402


#############################################
# The planner — pure, so WHAT moves is testable without a 1.4 GB database
#############################################

def test_plan_maps_every_store_across_roots(tmp_path):
    src, dst = tmp_path / "prod", tmp_path / "dev"
    plan = snap.snapshot_plan(src, dst)
    assert plan, "the plan must not be empty"
    for item in plan:
        assert item.src.is_relative_to(src)
        assert item.dst.is_relative_to(dst)
        assert item.src.relative_to(src) == item.dst.relative_to(dst)


def test_plan_includes_every_documented_store(tmp_path):
    rels = {i.src.relative_to(tmp_path / "prod").as_posix()
            for i in snap.snapshot_plan(tmp_path / "prod", tmp_path / "dev")}
    # gex_history.db lives at the APP ROOT, not under data/ — there is a stale
    # 0-byte options-scanner/data/gex_history.db on disk that is NOT the store.
    assert "options-scanner/gex_history.db" in rels
    assert "options-scanner/data/paper_account.db" in rels
    assert "options-scanner/data/paper_account_driver.db" in rels
    assert "sentiment-dashboard/data/momentum.db" in rels
    assert "services/trade_svc/data/iv_history.db" in rels
    # Without the watchlist the scanner degrades to base symbols and the
    # SPDR-sector group on the Net Prem view empties.
    assert "options-scanner/data/Top 20.xlsx" in rels
    assert "shared/sentiment_bridge.json" in rels


def test_skip_gex_drops_only_the_big_store(tmp_path):
    full = snap.snapshot_plan(tmp_path / "p", tmp_path / "d")
    lean = snap.snapshot_plan(tmp_path / "p", tmp_path / "d", skip_gex=True)
    dropped = {i.src.name for i in full} - {i.src.name for i in lean}
    assert dropped == {"gex_history.db"}


def test_plan_touches_no_filesystem(tmp_path):
    """PURE: planning against roots that do not exist must not create them."""
    src, dst = tmp_path / "nope-src", tmp_path / "nope-dst"
    snap.snapshot_plan(src, dst)
    assert not src.exists() and not dst.exists()
    assert list(tmp_path.iterdir()) == []


def test_plan_kinds_are_only_sqlite_and_file(tmp_path):
    kinds = {i.kind for i in snap.snapshot_plan(tmp_path / "p", tmp_path / "d")}
    assert kinds == {"sqlite", "file"}


#############################################
# SQLite — the online-backup API, consistent under a live writer
#############################################

def test_sqlite_copy_reproduces_rows_while_source_stays_open(tmp_path):
    """The online-backup API is the whole point: prod keeps running."""
    src, dst = tmp_path / "a.db", tmp_path / "b.db"
    con = sqlite3.connect(src)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    con.commit()
    snap.copy_sqlite(src, dst)          # source connection deliberately left open
    assert sqlite3.connect(dst).execute("SELECT count(*) FROM t").fetchone()[0] == 3
    con.close()


def test_sqlite_copy_creates_missing_destination_directories(tmp_path):
    src = tmp_path / "a.db"
    sqlite3.connect(src).execute("CREATE TABLE t (x)")
    dst = tmp_path / "deep" / "nested" / "b.db"
    snap.copy_sqlite(src, dst)
    assert dst.exists()


def test_sqlite_copy_does_not_checkpoint_the_source_wal(tmp_path):
    """Opening the source READ-ONLY is load-bearing, not a nicety.

    Measured: a read-WRITE open of a WAL database whose writer is gone
    CHECKPOINTS it and DELETES the -wal sidecar on close — i.e. it mutates prod's
    WAL state. ``mode=ro`` leaves it alone (it may create a -shm, but never
    touches the data), and still reads the uncheckpointed frames, which is why
    the row count below includes the post-checkpoint insert.

    The fixture deliberately builds a "crashed writer" directory — database plus
    a non-empty -wal, no -shm, no live connection. An earlier version of this
    test held the writing connection open and was VACUOUS: with another
    connection attached, neither open mode can checkpoint, so it passed against a
    read-write copy_sqlite too. Caught by mutation testing.
    """
    live, crashed = tmp_path / "live", tmp_path / "crashed"
    live.mkdir()
    crashed.mkdir()
    con = sqlite3.connect(live / "a.db")
    con.execute("PRAGMA journal_mode=wal")
    con.execute("CREATE TABLE t (x)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(200)])
    con.commit()
    con.execute("INSERT INTO t VALUES (999)")
    con.commit()
    # Snapshot the on-disk state of a writer that never got to close.
    shutil.copy2(live / "a.db", crashed / "a.db")
    shutil.copy2(live / "a.db-wal", crashed / "a.db-wal")
    con.close()

    src, dst = crashed / "a.db", tmp_path / "b.db"
    wal = crashed / "a.db-wal"
    before = wal.stat().st_size
    assert before > 0, "fixture must leave a non-empty -wal"

    snap.copy_sqlite(src, dst)

    assert wal.exists() and wal.stat().st_size == before, \
        "the source WAL was checkpointed — copy_sqlite opened it read-write"
    assert sqlite3.connect(dst).execute("SELECT count(*) FROM t").fetchone()[0] == 201


def test_sqlite_copy_stays_read_only_when_the_path_contains_a_hash(tmp_path):
    """SQLite reads '#' in a URI filename as a fragment delimiter.

    Built by concatenation and unquoted, a source under ``prod #2`` drops the
    ``?mode=ro`` entirely and opens READ-WRITE — defeating the one guard
    copy_sqlite's docstring calls load-bearing, in the one situation nobody is
    watching for it. Demonstrated before the fix: CREATE TABLE succeeded on the
    "read-only" handle.
    """
    peer = tmp_path / "prod #2"
    peer.mkdir()
    src, dst = peer / "a.db", tmp_path / "b.db"
    con = sqlite3.connect(src)
    con.execute("PRAGMA journal_mode=wal")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()

    snap.copy_sqlite(src, dst)
    assert sqlite3.connect(dst).execute("SELECT count(*) FROM t").fetchone()[0] == 1

    # The connection copy_sqlite builds must reject writes. Rebuild it the same
    # way rather than trusting the copy: this is about the OPEN MODE.
    from urllib.parse import quote
    con = sqlite3.connect(f"file:{quote(src.as_posix(), safe='/')}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("CREATE TABLE injected (x)")
    con.close()


def test_sqlite_copy_handles_spaces_and_unicode_in_the_path(tmp_path):
    """Percent-quoting must not break ordinary paths — 'WebGUI Trading Prod'
    has a space in it, and the drive colon must survive too."""
    peer = tmp_path / "WebGUI Trading Prod ünïcode"
    peer.mkdir()
    src, dst = peer / "a.db", tmp_path / "b.db"
    con = sqlite3.connect(src)
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    snap.copy_sqlite(src, dst)
    assert sqlite3.connect(dst).execute("SELECT count(*) FROM t").fetchone()[0] == 1


def test_file_copy_creates_missing_destination_directories(tmp_path):
    src = tmp_path / "x.json"
    src.write_text('{"a": 1}')
    dst = tmp_path / "deep" / "x.json"
    snap.copy_file(src, dst)
    assert json.loads(dst.read_text()) == {"a": 1}


#############################################
# Redis
#############################################

_FAKE_PAIR = []


def _fake_pair():
    """One reused fakeredis pair, flushed per test.

    A FRESH FakeStrictRedis pays ~2.5 s on its first flushdb (measured), which
    would have added ~20 s to a tools/tests suite that runs in under a second.
    """
    if not _FAKE_PAIR:
        import fakeredis
        _FAKE_PAIR.extend((fakeredis.FakeStrictRedis(), fakeredis.FakeStrictRedis()))
    src, dst = _FAKE_PAIR
    src.flushdb()
    dst.flushdb()
    return src, dst


def test_redis_copy_carries_published_views_and_their_version_counters():
    src, dst = _fake_pair()
    src.set("cache:options:scan", b"payload")
    src.set("cache:options:scan:ver", b"7")
    snap.copy_redis(src, dst)
    assert dst.get("cache:options:scan") == b"payload"
    assert dst.get("cache:options:scan:ver") == b"7"


def test_redis_copy_preserves_ttls():
    src, dst = _fake_pair()
    src.set("cache:options:calc_chain", b"x", px=90_000)
    src.set("cache:options:scan", b"y")            # no expiry
    snap.copy_redis(src, dst)
    assert 0 < dst.pttl("cache:options:calc_chain") <= 90_000
    assert dst.pttl("cache:options:scan") == -1, "a no-expiry key must not gain one"


def test_redis_copy_preserves_non_string_types():
    """DUMP/RESTORE, not GET/SET — a stray hash or list must survive intact."""
    src, dst = _fake_pair()
    src.hset("cache:options:some_hash", mapping={"a": "1", "b": "2"})
    src.rpush("cache:options:some_list", "a", "b")
    snap.copy_redis(src, dst)
    assert dst.type("cache:options:some_hash") == b"hash"
    assert dst.hget("cache:options:some_hash", "b") == b"2"
    assert dst.lrange("cache:options:some_list", 0, -1) == [b"a", b"b"]


def test_redis_copy_excludes_command_streams_and_dead_letter_lists():
    """Dev must not inherit a queued command backlog and execute prod's commands
    on startup, nor prod's dead-letter queue (an ops artefact, not a view)."""
    src, dst = _fake_pair()
    src.xadd("cmd:options", {"a": "1"})
    src.rpush("cmd:options:dead", b"poison")
    src.set("cache:options:scan", b"payload")
    snap.copy_redis(src, dst)
    assert not dst.exists("cmd:options"), "a queued command backlog must not follow"
    assert not dst.exists("cmd:options:dead"), "prod's dead letters must not follow"
    assert dst.exists("cache:options:scan"), "views must still copy"


def test_redis_copy_flushes_the_destination_first():
    src, dst = _fake_pair()
    src.set("cache:options:scan", b"payload")
    dst.set("stale:key", b"x")
    snap.copy_redis(src, dst)
    assert not dst.exists("stale:key"), "the destination DB is flushed first"


def test_redis_copy_disarms_the_driver_control_key():
    """A snapshot taken while the autonomous driver was ARMED must not arm dev.

    The live value is a CacheEnvelope ({version, ts, payload}), not the bare
    control dict — writing the bare shape would break DriverControl's reader.
    """
    src, dst = _fake_pair()
    armed = {"version": 32, "ts": "2026-08-03T01:28:27+00:00",
             "payload": {"enabled": True, "halted": False, "reason": None,
                         "halted_date": None, "timestamp": None}}
    src.set("cache:driver:control", json.dumps(armed).encode())
    snap.copy_redis(src, dst)

    got = json.loads(dst.get("cache:driver:control"))
    assert got["payload"]["enabled"] is False
    # The envelope must stay parseable by the real contract, or read_control
    # raises inside driver_svc instead of reading "off".
    from shared.contracts.envelope import CacheEnvelope
    env = CacheEnvelope.from_json(dst.get("cache:driver:control"))
    assert env.payload["enabled"] is False
    assert env.version == 32, "the version must stay in lockstep with :ver"


def test_redis_copy_writes_a_disabled_control_key_even_when_prod_has_none():
    src, dst = _fake_pair()
    src.set("cache:options:scan", b"payload")
    snap.copy_redis(src, dst)
    from shared.contracts.envelope import CacheEnvelope
    env = CacheEnvelope.from_json(dst.get("cache:driver:control"))
    assert env.payload["enabled"] is False


def test_redis_copy_disarms_even_an_unparseable_control_value():
    src, dst = _fake_pair()
    src.set("cache:driver:control", b"not json at all")
    snap.copy_redis(src, dst)
    from shared.contracts.envelope import CacheEnvelope
    env = CacheEnvelope.from_json(dst.get("cache:driver:control"))
    assert env.payload["enabled"] is False


@pytest.mark.parametrize("broken", [
    {"version": None, "ts": "2026-08-03T01:28:27+00:00"},
    {"version": 32, "ts": None},
    {"version": None, "ts": None},
    {"version": True, "ts": "2026-08-03T01:28:27+00:00"},   # bool is an int subclass
    {"ts": "2026-08-03T01:28:27+00:00"},                     # version absent
    {"version": 32},                                          # ts absent
])
def test_redis_copy_repairs_a_malformed_control_envelope(broken):
    """A null version/ts must be REPLACED, not passed through.

    ``setdefault`` does not fire on a key present with value ``None``, so the
    envelope would reach CacheEnvelope with a null required field and be
    rejected — leaving driver_svc.read_control RAISING rather than reading
    "off". This is the one function whose whole job is a disarmed dev.
    """
    src, dst = _fake_pair()
    env = dict(broken)
    env["payload"] = {"enabled": True, "halted": False}
    src.set("cache:driver:control", json.dumps(env).encode())

    snap.copy_redis(src, dst)

    written = dst.get("cache:driver:control")
    # Assert on the BYTES this function writes, not only on what survives the
    # contract: pydantic coerces a JSON `true` version to 1, so a downstream-only
    # assertion cannot tell a repaired envelope from an unrepaired one. Caught by
    # mutation testing (removing the bool carve-out changed nothing observable).
    fields = json.loads(written)
    assert type(fields["version"]) is int, "version must be written as a real int"
    assert type(fields["ts"]) is str, "ts must be written as a real string"

    from shared.contracts.envelope import CacheEnvelope
    got = CacheEnvelope.from_json(written)
    assert got.payload["enabled"] is False


#############################################
# The guards — the reason this tool is safe to keep in the repo
#############################################

def test_refuses_to_run_outside_a_dev_checkout(monkeypatch):
    """The one guard that makes this tool safe to keep in the repo."""
    monkeypatch.setattr(snap, "ENV_NAME", "prod")
    with pytest.raises(SystemExit):
        snap.assert_destination_is_dev()


def test_allows_a_dev_checkout(monkeypatch):
    """Non-vacuity partner: the guard must not refuse EVERYTHING."""
    monkeypatch.setattr(snap, "ENV_NAME", "dev")
    snap.assert_destination_is_dev()


def test_refuses_the_same_redis_db_for_source_and_destination():
    """FLUSHDB on the destination would wipe the source. Never allow it."""
    with pytest.raises(SystemExit):
        snap.assert_distinct_redis_dbs(0, 0)


def test_allows_distinct_redis_dbs():
    snap.assert_distinct_redis_dbs(0, 1)


def test_refuses_a_peer_that_is_this_checkout(tmp_path, monkeypatch):
    """src == dst would back a database up onto itself."""
    monkeypatch.setattr(snap, "REPO_ROOT", tmp_path)
    (tmp_path / "repo_paths.py").write_text("")
    with pytest.raises(SystemExit):
        snap.assert_peer_is_usable(tmp_path)


def test_refuses_a_peer_that_is_not_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "REPO_ROOT", tmp_path / "dev")
    peer = tmp_path / "not-a-checkout"
    peer.mkdir()
    with pytest.raises(SystemExit):
        snap.assert_peer_is_usable(peer)


def test_accepts_a_distinct_peer_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "REPO_ROOT", tmp_path / "dev")
    peer = tmp_path / "prod"
    peer.mkdir()
    (peer / "repo_paths.py").write_text("")
    snap.assert_peer_is_usable(peer)


def test_refuses_while_dev_services_are_listening(monkeypatch):
    """Dev's services hold DB handles and would write over the restore."""
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        monkeypatch.setattr(snap, "SERVICE_PORTS", {"options": port})
        monkeypatch.setattr(snap, "NICEGUI_PORT", 0)
        with pytest.raises(SystemExit) as exc:
            snap.assert_dev_stack_is_down()
    assert str(port) in str(exc.value), "the message must name the busy port"


def test_allows_when_no_dev_service_is_listening(monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    monkeypatch.setattr(snap, "SERVICE_PORTS", {"options": free})
    monkeypatch.setattr(snap, "NICEGUI_PORT", 0)
    snap.assert_dev_stack_is_down()


#############################################
# Reading the PEER's Redis DB index — never assume prod is 0
#############################################

def _write_peer(root, marker, profiles, *, bom=False):
    (root / "config").mkdir(parents=True, exist_ok=True)
    enc = "utf-8-sig" if bom else "utf-8"
    if marker is not None:
        (root / "config" / "env.local.toml").write_text(marker, encoding=enc)
    (root / "config" / "environments.toml").write_text(profiles, encoding=enc)


def test_peer_redis_db_reads_the_peer_profile(tmp_path):
    _write_peer(tmp_path, 'name = "prod"\n', "[prod]\nredis_db = 0\n[dev]\nredis_db = 1\n")
    assert snap.peer_redis_db(tmp_path) == 0


def test_peer_redis_db_follows_a_non_default_peer_marker(tmp_path):
    _write_peer(tmp_path, 'name = "dev"\n', "[prod]\nredis_db = 0\n[dev]\nredis_db = 1\n")
    assert snap.peer_redis_db(tmp_path) == 1


def test_peer_redis_db_tolerates_a_bom(tmp_path):
    """PowerShell's `>` writes UTF-8 WITH BOM; plain utf-8 decoding chokes."""
    _write_peer(tmp_path, 'name = "dev"\n', "[prod]\nredis_db = 0\n[dev]\nredis_db = 1\n",
                bom=True)
    assert snap.peer_redis_db(tmp_path) == 1


def test_peer_redis_db_defaults_to_zero_without_a_marker(tmp_path):
    _write_peer(tmp_path, None, "[prod]\nredis_db = 0\n")
    assert snap.peer_redis_db(tmp_path) == 0


def test_peer_redis_db_falls_back_to_prod_for_an_unknown_name(tmp_path):
    _write_peer(tmp_path, 'name = "staging"\n', "[prod]\nredis_db = 0\n[dev]\nredis_db = 1\n")
    assert snap.peer_redis_db(tmp_path) == 0


def test_peer_redis_db_defaults_to_zero_when_nothing_is_readable(tmp_path):
    assert snap.peer_redis_db(tmp_path) == 0


#############################################
# main() — the dry run must be inert
#############################################

def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    peer, dev = tmp_path / "prod", tmp_path / "dev"
    (peer / "options-scanner" / "data").mkdir(parents=True)
    (peer / "repo_paths.py").write_text("")
    _write_peer(peer, 'name = "prod"\n', "[prod]\nredis_db = 0\n")
    sqlite3.connect(peer / "options-scanner" / "gex_history.db").execute("CREATE TABLE t (x)")
    dev.mkdir()

    monkeypatch.setattr(snap, "ENV_NAME", "dev")
    monkeypatch.setattr(snap, "REPO_ROOT", dev)
    monkeypatch.setattr(snap, "REDIS_DB", 1)

    snap.main(["--dry-run", "--peer", str(peer)])

    out = capsys.readouterr().out
    assert "would copy" in out and "gex_history.db" in out
    assert list(dev.iterdir()) == [], "a dry run must create nothing"


def test_dry_run_refuses_when_the_two_redis_dbs_match(tmp_path, monkeypatch):
    peer, dev = tmp_path / "prod", tmp_path / "dev"
    peer.mkdir()
    (peer / "repo_paths.py").write_text("")
    _write_peer(peer, 'name = "prod"\n', "[prod]\nredis_db = 0\n")
    dev.mkdir()
    monkeypatch.setattr(snap, "ENV_NAME", "dev")
    monkeypatch.setattr(snap, "REPO_ROOT", dev)
    monkeypatch.setattr(snap, "REDIS_DB", 0)          # misconfigured: same as prod
    with pytest.raises(SystemExit):
        snap.main(["--dry-run", "--peer", str(peer)])


def test_main_refuses_outside_dev_whatever_the_arguments(tmp_path, monkeypatch):
    """No argument combination may let this run in prod.

    The peer here is a VALID, distinct checkout, so the environment guard is the
    only thing that can refuse — and the message is asserted. An earlier version
    pointed --peer at a bare tmp_path and passed even with the guard deleted,
    because ``assert_peer_is_usable`` refused instead. Caught by the
    stayed-green sweep.
    """
    peer, dev = tmp_path / "prod", tmp_path / "dev"
    peer.mkdir()
    (peer / "repo_paths.py").write_text("")
    _write_peer(peer, 'name = "prod"\n', "[prod]\nredis_db = 0\n")
    dev.mkdir()
    monkeypatch.setattr(snap, "ENV_NAME", "prod")
    monkeypatch.setattr(snap, "REPO_ROOT", dev)
    monkeypatch.setattr(snap, "REDIS_DB", 1)
    with pytest.raises(SystemExit) as exc:
        snap.main(["--dry-run", "--redis-only", "--skip-gex", "--peer", str(peer)])
    assert "'dev'" in str(exc.value), "it must refuse for the ENVIRONMENT reason"


def test_redis_import_precedes_any_copying():
    """The redis dependency must be checked BEFORE the SQLite copy loop.

    It is only *used* at the end of the run, so the natural place to import it
    is where it is used — and that is wrong. A missing dependency (in practice:
    running under the system interpreter rather than .venv) would then fail
    after ~1.4 GB of stores had already landed, leaving dev with fresh SQLite
    and stale Redis. That half-applied state is what every other guard in
    ``main`` exists to prevent.

    Source inspection rather than behaviour, because ``redis`` IS installed in
    this venv, so the failure cannot be reproduced without faking the import
    machinery — and the property worth pinning is the ORDERING, not the message.
    """
    src = inspect.getsource(snap.main)
    import_at = src.index("import redis")
    copy_at = src.index("snapshot_plan(")
    assert import_at < copy_at, (
        "import redis moved after the copy loop — a missing dependency would "
        "now fail with a partially written snapshot"
    )
