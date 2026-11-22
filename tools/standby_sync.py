#!/usr/bin/env python
"""The standby's nightly sync: pull the newest complete backup generation from
prod, check it, move the checkout to the commit it recorded, restore the data.

Runs on the standby (a systemd timer, ``config/standby.toml [sync] at``), never
on prod. Design: docs/plans/2026-11-22-warm-standby-design.md, section 3.

    .venv/bin/python tools/standby_sync.py

Exit codes (a non-zero exit fires the failure push):
  0  synced, or nothing new and the newest copy is fresh enough, or this box has
     no identity file (no standby feature configured)
  1  refused (this box was promoted) or failed (any check, copy or restore)

What it will not do: restore a path the backup does not itself produce (a
planted script is refused and fails the night), accept a store that shrank
sharply, restore Redis, or touch a promoted box. Whatever happens, it writes
``~/standby/status.json``: a failed night is a record, not silence.
"""
from __future__ import annotations

import datetime as dt
import errno
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Protocol

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402
from shared import standby_config  # noqa: E402
from tools import backup_local as bl  # noqa: E402
from tools import restore_backup as rb  # noqa: E402

GEN_RE = re.compile(r"^([A-Za-z]+)_(\d{4}-\d{2}-\d{2})_(\d{4})$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
PROMOTED_TARGET = ".config/systemd/user/trading-prod.target"
PULL_KEY = "~/.ssh/standby_pull"
PARTIAL = ".partial"
SYNCED = "SYNCED"                # written last: the whole night finished
RSYNC_NO_FILE = 23               # rsync's "file not found"; any other failure is an outage
# Settings the backup must never rewrite on the standby: they are the sync's own guards.
SKIP_RESTORE = ("config/local/standby.toml",)


class SyncError(Exception):
    """A night that must fail, with a sentence for ``status.json``."""


class Remote(Protocol):
    def generations(self) -> list[str]: ...
    def has(self, gen: str, name: str) -> bool: ...
    def read(self, gen: str, name: str) -> str: ...
    def fetch(self, gen: str, dest: pathlib.Path,
              basis: pathlib.Path | None) -> int: ...


class RsyncRemote:
    """The prod box's ``~/backups`` through the read-only ``rrsync`` key.

    The remote root IS the backups folder, so a generation is ``<target>:<gen>/``.
    Targets are tried in order (tailnet name, then public address); the first that
    answers is used for the rest of the run."""

    def __init__(self, targets, home=None, timeout=15, run=subprocess.run):
        self.targets = list(targets)
        self.home = pathlib.Path(home) if home is not None else pathlib.Path.home()
        self.timeout = int(timeout)
        self._run = run
        self._target = None

    def _ssh(self) -> str:
        key = str(self.home / PULL_KEY[2:])
        return (f"ssh -i {shlex.quote(key)} -o IdentitiesOnly=yes -o BatchMode=yes "
                f"-o ConnectTimeout={self.timeout}")

    def _rsync(self, args, timeout=7200):
        return self._run(["rsync", "-e", self._ssh(), *args],
                         capture_output=True, text=True, timeout=timeout)

    def _pick(self) -> str:
        if self._target is not None:
            return self._target
        if not self.targets:
            raise SyncError("no ssh address for the peer box in standby.toml")
        errs = []
        for target in self.targets:
            try:
                p = self._rsync(["--list-only", f"{target}:"], timeout=self.timeout * 4 + 30)
            except (OSError, subprocess.SubprocessError) as exc:
                errs.append(f"{target}: {exc}")
                continue
            if p.returncode == 0:
                self._target = target
                return target
            errs.append(f"{target}: {(p.stderr or '').strip()[:120]}")
        raise SyncError("the peer did not answer: " + "; ".join(errs))

    def generations(self) -> list[str]:
        target = self._pick()
        p = self._rsync(["--list-only", f"{target}:"], timeout=self.timeout * 4 + 30)
        if p.returncode != 0:
            raise SyncError(f"listing {target} failed: {(p.stderr or '').strip()[:200]}")
        names = []
        for line in (p.stdout or "").splitlines():
            if not line.startswith("d"):
                continue
            parts = line.split(None, 4)
            if len(parts) == 5 and GEN_RE.match(parts[4].strip()):
                names.append(parts[4].strip())
        return names

    def has(self, gen: str, name: str) -> bool:
        target = self._pick()
        p = self._rsync(["--list-only", f"{target}:{gen}/{name}"],
                        timeout=self.timeout * 4 + 30)
        if p.returncode == 0:
            return True
        if p.returncode == RSYNC_NO_FILE:
            return False
        raise SyncError(f"could not look for {gen}/{name} ({p.returncode}): "
                        f"{(p.stderr or '').strip()[:200]}")

    def read(self, gen: str, name: str) -> str:
        target = self._pick()
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "f"
            p = self._rsync([f"{target}:{gen}/{name}", str(out)], timeout=300)
            if p.returncode != 0 or not out.is_file():
                raise SyncError(f"could not read {gen}/{name}: "
                                f"{(p.stderr or '').strip()[:200]}")
            return out.read_text(encoding="utf-8")

    def fetch(self, gen: str, dest: pathlib.Path, basis: pathlib.Path | None) -> int:
        target = self._pick()
        args = ["-a", "--no-links", "--stats"]
        if basis is not None:
            args.append(f"--link-dest={pathlib.Path(basis).resolve()}")
        dest = pathlib.Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        p = self._rsync([*args, f"{target}:{gen}/", f"{dest}/"])
        if p.returncode != 0:
            raise SyncError(f"rsync of {gen} failed ({p.returncode}): "
                            f"{(p.stderr or '').strip()[:200]}")
        m = re.search(r"Total transferred file size:\s*([\d,]+)", p.stdout or "")
        return int(m.group(1).replace(",", "")) if m else 0


# --- checks ------------------------------------------------------------------

def _stamp(name: str) -> dt.datetime | None:
    m = GEN_RE.match(name)
    if not m:
        return None
    return dt.datetime.strptime(f"{m.group(2)} {m.group(3)}", "%Y-%m-%d %H%M")


def newest_complete(remote: Remote) -> str | None:
    """The newest generation carrying both ``BACKUP_OK`` and ``GIT_HEAD``."""
    names = [n for n in remote.generations() if _stamp(n) is not None]
    for name in sorted(names, key=lambda n: (_stamp(n), n), reverse=True):
        if remote.has(name, bl.OK_MARKER) and remote.has(name, bl.GIT_HEAD):
            return name
    return None


def generation_age_h(name: str, now: dt.datetime) -> float:
    """Hours since the generation was made. Both are naive Central."""
    stamp = _stamp(name)
    if stamp is None:
        raise ValueError(f"not a generation name: {name!r}")
    return (now - stamp).total_seconds() / 3600.0


def allowed_path(rel: pathlib.PurePosixPath) -> bool:
    """True for a path the backup itself produces: the loose files, the swept
    data trees and any ``*.db`` store, less what the backup leaves out."""
    rel = pathlib.PurePosixPath(rel)
    parts = rel.parts
    if not parts or rel.is_absolute() or ".." in parts or ".venv" in parts or ".git" in parts:
        return False
    for ex in (*bl.SWEEP_EXCLUDE, *bl.DB_EXCLUDE):
        ex_parts = pathlib.PurePosixPath(ex).parts
        if parts[:len(ex_parts)] == ex_parts:
            return False
    if rel.as_posix() in bl.EXTRA_FILES:
        return True
    for tree in bl.DATA_TREES:
        t = pathlib.PurePosixPath(tree).parts
        if len(parts) > len(t) and parts[:len(t)] == t:
            return True
    return rel.suffix == ".db"


def shrink_refusals(new: pathlib.Path, prev: pathlib.Path | None,
                    stores: list[str], max_pct: int) -> list[str]:
    """One sentence per store that vanished or lost more than ``max_pct`` percent
    of its size against the previous generation. No previous generation, no check."""
    if prev is None:
        return []
    out = []
    for store in stores:
        old, cur = pathlib.Path(prev) / store, pathlib.Path(new) / store
        if not old.is_file():
            continue
        if not cur.is_file():
            out.append(f"{store} is missing from the new copy")
            continue
        before, after = old.stat().st_size, cur.stat().st_size
        if before > 0 and (before - after) * 100 > max_pct * before:
            out.append(f"{store} shrank from {before} to {after} bytes "
                       f"(more than {max_pct}%)")
    return out


# --- code and data -------------------------------------------------------------

def _git(run, repo, *args):
    return run(["git", "-C", str(repo), *args], capture_output=True, text=True,
               timeout=600)


def move_code(commit: str, repo: pathlib.Path, run=subprocess.run) -> bool:
    """Check the checkout out at ``commit`` (detached), but only a commit that is
    on ``origin/main``. Returns whether ``requirements.lock`` changed, after
    installing it when it did."""
    if not COMMIT_RE.match(commit):
        raise SyncError(f"GIT_HEAD is not a commit id: {commit[:40]!r}")
    p = _git(run, repo, "fetch", "origin")
    if p.returncode != 0:
        raise SyncError(f"git fetch failed: {(p.stderr or '').strip()[:200]}")
    p = _git(run, repo, "merge-base", "--is-ancestor", commit, "origin/main")
    if p.returncode != 0:
        raise SyncError(f"commit {commit[:12]} is not on origin/main; "
                        "the code is not moved")
    # 1 = the file differs between the checkout and the commit.
    changed = _git(run, repo, "diff", "--quiet", "HEAD", commit, "--",
                   "requirements.lock").returncode != 0
    p = _git(run, repo, "checkout", "--detach", commit)
    if p.returncode != 0:
        raise SyncError(f"git checkout failed: {(p.stderr or '').strip()[:200]}")
    if changed:
        py = pathlib.Path(repo) / ".venv" / "bin" / "python"
        p = run([str(py), "-m", "pip", "install", "-r",
                 str(pathlib.Path(repo) / "requirements.lock")],
                capture_output=True, text=True, timeout=3600)
        if p.returncode != 0:
            raise SyncError(f"pip install failed: {(p.stderr or '').strip()[:200]}")
    return changed


def _inside(base: pathlib.Path, rel: pathlib.PurePath) -> bool:
    return not rel.is_absolute() and ".." not in rel.parts


def _copy_tree(src: pathlib.Path, dst: pathlib.Path, skip_top=()) -> None:
    for f in sorted(src.rglob("*")):
        if f.is_symlink() or not f.is_file():
            continue
        rel = f.relative_to(src)
        if rel.parts[0] in skip_top:
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        os.chmod(out, 0o600)


def _check_spec(spec: dict, home: pathlib.Path, repo: pathlib.Path | None) -> None:
    name, raw = spec["name"], spec["dir"]
    folder = pathlib.PurePosixPath(raw)
    if (len(folder.parts) != 1 or folder.parts[0] in (".", "..") or raw.startswith(".")
            or folder.is_absolute()):
        raise SyncError(f"neighbour {name}: folder {raw!r} must be one plain folder "
                        "name under home")
    if repo is not None:
        try:
            checkout = pathlib.PurePosixPath(
                pathlib.Path(repo).resolve().relative_to(pathlib.Path(home).resolve()).as_posix())
        except ValueError:
            checkout = None
        if checkout is not None and checkout.parts[:1] == folder.parts:
            raise SyncError(f"neighbour {name}: folder {raw!r} is the checkout")


PIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9.+!_-]+")


def _check_freeze(name: str, freeze: pathlib.Path) -> None:
    """Every line of a neighbour's freeze must be a bare ``name==version`` pin: no
    option, include, URL, path or marker, because the file comes from prod and is
    installed on the standby every night it changes."""
    if not freeze.is_file():
        return
    for line in freeze.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not PIN_RE.fullmatch(line):
            raise SyncError(f"neighbour {name}: venv-freeze.txt line is not a plain "
                            f"pin: {line[:80]!r}")


def restore_neighbours(gen: pathlib.Path, home: pathlib.Path, specs: list[dict],
                       run=subprocess.run, repo: pathlib.Path | None = None) -> None:
    """Put the generation's ``neighbours/<name>/`` back under ``home``.

    ``dir/`` over ``~/<dir>`` (its ``.venv`` untouched), ``files/`` at ``~/<rel>``
    mode 0600, ``units/`` into ``~/standby/units/<name>/`` (NOT the systemd user
    folder: the standby runs nothing). The venv is rebuilt only when the
    generation's freeze differs from the one kept from the last good night."""
    gen, home = pathlib.Path(gen), pathlib.Path(home)
    # Check everything before writing anything: a bad night changes nothing here.
    for spec in specs:
        src = gen / bl.NEIGHBOURS_DIR / spec["name"]
        if not src.is_dir():
            continue
        _check_spec(spec, home, repo)
        _check_freeze(spec["name"], src / "venv-freeze.txt")
        listed = set(spec.get("files", []))
        if (src / "files").is_dir():
            for f in sorted((src / "files").rglob("*")):
                if f.is_file() or f.is_symlink():
                    rel = f.relative_to(src / "files").as_posix()
                    if rel not in listed:
                        raise SyncError(f"neighbour {spec['name']}: {rel} is not one "
                                        "of its listed files")
    for spec in specs:
        name = spec["name"]
        src = gen / bl.NEIGHBOURS_DIR / name
        if not src.is_dir():
            continue
        folder = pathlib.PurePosixPath(spec["dir"])
        if (src / "dir").is_dir():
            _copy_tree(src / "dir", home / folder, skip_top=(".venv",))
        if (src / "files").is_dir():
            for f in sorted((src / "files").rglob("*")):
                if f.is_symlink() or not f.is_file():
                    continue
                rel = f.relative_to(src / "files")
                if not _inside(home, rel):
                    raise SyncError(f"neighbour {name}: file {rel} leaves home")
                out = home / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)
                os.chmod(out, 0o600)
        kept = home / "standby" / "units" / name
        if (src / "units").is_dir():
            _copy_tree(src / "units", kept)
        freeze = src / "venv-freeze.txt"
        if not freeze.is_file():
            continue
        old = kept / "venv-freeze.txt"
        text = freeze.read_text(encoding="utf-8")
        if old.is_file() and old.read_text(encoding="utf-8") == text:
            continue
        venv = home / folder / ".venv"
        uv = str(home / ".local" / "bin" / "uv")
        p = run([uv, "venv", "--clear", str(venv)], capture_output=True, text=True,
                timeout=600)
        if p.returncode == 0:
            p = run([uv, "pip", "install", "--only-binary", ":all:",
                     "--python", str(venv / "bin" / "python"),
                     "-r", str(freeze)], capture_output=True, text=True, timeout=3600)
        if p.returncode != 0:
            raise SyncError(f"neighbour {name}: venv rebuild failed: "
                            f"{(p.stderr or '').strip()[:200]}")
        kept.mkdir(parents=True, exist_ok=True)
        shutil.copy2(freeze, old)
        os.chmod(old, 0o600)


def _finished(gen_root: pathlib.Path) -> list[pathlib.Path]:
    if not gen_root.is_dir():
        return []
    found = [p for p in gen_root.iterdir() if p.is_dir() and GEN_RE.match(p.name)]
    return sorted(found, key=lambda p: (_stamp(p.name), p.name))


def prune(gen_root: pathlib.Path, keep: int) -> list[str]:
    """Delete the oldest finished generations beyond ``keep``; returns their names."""
    old = _finished(pathlib.Path(gen_root))
    doomed = old[:max(0, len(old) - max(1, keep))]
    for p in doomed:
        shutil.rmtree(p)
    return [p.name for p in doomed]


# --- the run -------------------------------------------------------------------

def _write_status(home: pathlib.Path, status: dict) -> None:
    folder = pathlib.Path(home) / "standby"
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="status.json.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(status, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, folder / "status.json")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_status(home: pathlib.Path) -> dict:
    try:
        raw = json.loads((pathlib.Path(home) / "standby" / "status.json")
                         .read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _promoted(identity, home: pathlib.Path) -> bool:
    return identity.role == "prod" or (pathlib.Path(home) / PROMOTED_TARGET).exists()


def run(now: dt.datetime, *, identity, remote: Remote, repo: pathlib.Path,
        home: pathlib.Path, settings: dict, run=subprocess.run) -> dict:
    """One night. Always writes ``~/standby/status.json`` and returns the dict."""
    home, repo = pathlib.Path(home), pathlib.Path(repo)
    started = dt.datetime.now().isoformat(timespec="seconds")
    status = {"checked_at": now.isoformat(timespec="seconds"), "result": "failed",
              "generation": None, "commit": None, "bytes": 0, "started": started,
              "finished": None, "detail": ""}
    previous = _read_status(home)
    try:
        _sync(now, status, previous, identity, remote, repo, home, settings, run)
    except SyncError as exc:
        status["result"], status["detail"] = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 - the status file is this tool's trace
        status["result"] = "failed"
        status["detail"] = f"{type(exc).__name__}: {exc}"
    status["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    _write_status(home, status)
    return status


def _sync(now, status, previous, identity, remote, repo, home, settings, run_cmd):
    if _promoted(identity, home):
        status["result"] = "refused"
        status["detail"] = "this box has been promoted; the sync does not touch it"
        return
    gen_root = pathlib.Path(settings["generations"])
    name = newest_complete(remote)
    if name is None:
        raise SyncError("no complete generation (BACKUP_OK and GIT_HEAD) on the peer")
    status["generation"] = name
    age = generation_age_h(name, now)
    stale = age > settings["max_age_h"]
    final = gen_root / name
    newest_done = [p for p in _finished(gen_root) if (p / SYNCED).is_file()]
    if newest_done and _stamp(name) < _stamp(newest_done[-1].name):
        raise SyncError(f"the newest complete generation on the peer ({name}) is older "
                        f"than {newest_done[-1].name}, already synced here; "
                        "the standby never goes backwards")
    if (final / SYNCED).is_file():
        head = final / bl.GIT_HEAD
        status["commit"] = head.read_text(encoding="utf-8").strip() if head.is_file() else None
        if stale:
            raise SyncError(f"the newest generation {name} is {age:.0f} h old "
                            f"(limit {settings['max_age_h']} h): the peer has stopped "
                            "making backups")
        status["result"] = "unchanged"
        return
    commit = remote.read(name, bl.GIT_HEAD).strip()
    if not COMMIT_RE.match(commit):
        raise SyncError(f"GIT_HEAD of {name} is not a commit id: {commit[:40]!r}")
    status["commit"] = commit
    if not final.is_dir():
        _fetch(remote, name, final, gen_root, settings, status)
    move_code(commit, repo, run_cmd)
    out = []
    summary = rb.restore(
        final, repo, force=True, out=out.append,
        allow=lambda rel: allowed_path(rel) and rel.as_posix() not in SKIP_RESTORE)
    norm = {r: r.replace("\\", "/") for r in summary["refused"]}
    skipped = [n for n in norm.values() if n in SKIP_RESTORE]
    refused = [r for r, n in norm.items() if n not in SKIP_RESTORE]
    if summary["bad"] or refused:
        raise SyncError("restore failed: " + "; ".join((summary["bad"] + refused)[:5]))
    restore_neighbours(final, home, standby_config.neighbours(), run_cmd, repo=repo)
    (final / SYNCED).write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
    status["result"] = "synced"
    notes = [f"not restored (this box's own setting): {', '.join(skipped)}"] if skipped else []
    if stale:
        status["result"] = "failed"
        notes.append(f"synced, but {name} is {age:.0f} h old "
                     f"(limit {settings['max_age_h']} h)")
    status["detail"] = "; ".join(notes)


def _fetch(remote, name, final, gen_root, settings, status) -> None:
    gen_root.mkdir(parents=True, exist_ok=True)
    for stale_partial in gen_root.glob("*" + PARTIAL):
        shutil.rmtree(stale_partial, ignore_errors=True)
    partial = gen_root / (name + PARTIAL)
    kept = [p for p in _finished(gen_root) if p.name != name]
    basis = kept[-1] if kept else None
    try:
        status["bytes"] = int(remote.fetch(name, partial, basis))
        bad = [str(r) for r in rb.files_in(partial)
               if not allowed_path(pathlib.PurePosixPath(*r.parts))]
        if bad:
            raise SyncError("the copy holds paths the backup never writes: "
                            + ", ".join(bad[:5]))
        short = shrink_refusals(partial, basis, settings["shrink_stores"],
                                settings["max_shrink_pct"])
        if short:
            raise SyncError("refused, the copy shrank: " + "; ".join(short))
        partial.rename(final)
    except BaseException as exc:
        shutil.rmtree(partial, ignore_errors=True)
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            raise SyncError("disk full while copying; the previous generation is untouched")
        raise
    prune(gen_root, settings["keep"])


def main() -> int:
    identity = standby_config.read_identity()
    if identity is None:
        print("no box identity; nothing to do")
        return 0
    home, settings = pathlib.Path.home(), standby_config.sync_settings()
    try:
        _name, entry = standby_config.peer(identity)
    except ValueError as exc:
        status = {"checked_at": dt.datetime.now().isoformat(timespec="seconds"),
                  "result": "failed", "generation": None, "commit": None, "bytes": 0,
                  "started": None, "finished": None, "detail": str(exc)}
        _write_status(home, status)
        print(f"standby sync: failed: {exc}")
        return 1
    remote = RsyncRemote(entry["ssh"], home, settings["connect_timeout_sec"])
    status = run(dt.datetime.now(), identity=identity, remote=remote,
                 repo=repo_paths.REPO_ROOT, home=home, settings=settings)
    print(f"standby sync: {status['result']} {status['generation'] or '-'} "
          f"{(status['commit'] or '-')[:12]} {status['bytes']} bytes {status['detail']}".rstrip())
    return 0 if status["result"] in ("synced", "unchanged") else 1


if __name__ == "__main__":
    sys.exit(main())
