"""Reading and writing the operator's configuration overrides.

The tracked ``config/<name>.toml`` is the SHIPPED value and is never written
here: an edit to a tracked file dirties the prod checkout, and
``tools/promote.sh`` refuses a dirty tree. What the operator changes goes to
``config/local/<name>.toml`` (gitignored), which every loader in the stack layers
on top through ``shared.config_toml``. See that module for the load order.

Pure transforms (``flatten`` / ``build_overrides``) are unit-tested; the I/O is
three thin functions. Tier 1 allow-list: ``shared.config_toml`` (stdlib-only,
the same footing as ``shared.market_calendar``) and ``repo_paths``.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tomllib
from pathlib import Path

from repo_paths import REPO_ROOT
from shared import config_toml

CONFIG_DIR = Path(REPO_ROOT) / "config"
CHANGE_LOG = CONFIG_DIR / config_toml.LOCAL_DIRNAME / "changes.jsonl"
_HEADER = ("Written by Settings -> Configuration. Only values that differ from\n"
           "the shipped config/{name} are kept here; delete a line (or use Reset\n"
           "in the app) to go back to the shipped value.")


def config_path(name):
    return CONFIG_DIR / name


# ── pure ─────────────────────────────────────────────────────────────────────
def _is_table_array(v):
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def flatten(data, prefix=()):
    """``{("a", "b"): leaf}`` for every leaf. An array of tables is addressed by
    each item's ``key`` field (``netprem_groups`` -> ``("netprem_groups",
    "indices", "symbols")``), since that is how the operator names a group."""
    out = {}
    for k, v in (data or {}).items():
        path = prefix + (str(k),)
        if isinstance(v, dict):
            out.update(flatten(v, path))
        elif _is_table_array(v):
            for i, item in enumerate(v):
                ident = str(item.get("key", i))
                for ik, iv in item.items():
                    if ik != "key":
                        out[path + (ident, str(ik))] = iv
        else:
            out[path] = v
    return out


def table_array_labels(data, name):
    """``{key: label}`` for an array of tables (the Net Prem groups)."""
    return {str(it.get("key")): str(it.get("label") or it.get("key"))
            for it in (data or {}).get(name, []) if isinstance(it, dict)}


def build_overrides(shipped, values):
    """The override table for ``values`` (a flat ``{path: value}``): every leaf
    whose value differs from ``shipped`` and nothing else.

    A ``None`` value means "not set" and is dropped. An array of tables cannot be
    partially overridden (a TOML list replaces the whole list), so when any leaf
    inside one changes, the WHOLE array is written, rebuilt from the shipped
    array with the edits applied in its original order."""
    base = flatten(shipped)
    over: dict = {}
    arrays: set = set()
    for path, val in values.items():
        if val is None or base.get(path) == val:
            continue
        top = shipped.get(path[0]) if shipped else None
        if _is_table_array(top):
            arrays.add(path[0])
            continue
        node = over
        for p in path[:-1]:
            node = node.setdefault(p, {})
        node[path[-1]] = val
    for name in sorted(arrays):
        rebuilt = []
        for i, item in enumerate(shipped[name]):
            ident = str(item.get("key", i))
            new = dict(item)
            for (top, gid, fld), val in ((p, v) for p, v in values.items()
                                         if len(p) == 3 and p[0] == name):
                if gid == ident and val is not None:
                    new[fld] = val
            rebuilt.append(new)
        over[name] = rebuilt
    return over


def effective(shipped, overrides):
    return config_toml.deep_merge(shipped or {}, overrides or {})


# ── I/O ──────────────────────────────────────────────────────────────────────
def load(name):
    """``(shipped, overrides)`` for ``config/<name>``. Never raises: an
    unreadable tracked file is ``{}`` and the page says so."""
    path = config_path(name)
    try:
        with open(path, "rb") as fh:
            shipped = tomllib.load(fh)
    except Exception:  # noqa: BLE001 - the page renders "could not be read"
        shipped = {}
    return shipped, config_toml.read_overrides(path)


def save(name, overrides, *, changes=()):
    """Write the override file and append each change to the change log."""
    config_toml.write_overrides(config_path(name), overrides,
                                header=_HEADER.format(name=name))
    if changes:
        CHANGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().isoformat(timespec="seconds")
        with open(CHANGE_LOG, "a", encoding="utf-8") as fh:
            for key, old, new in changes:
                fh.write(json.dumps({"at": stamp, "file": name, "key": key,
                                     "from": old, "to": new}) + "\n")


def recent_changes(limit=25):
    """The newest ``limit`` change-log entries, newest first."""
    try:
        with open(CHANGE_LOG, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit * 4:]):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def overridden_count(name):
    """How many values in ``config/<name>`` are currently overridden."""
    shipped, over = load(name)
    base = flatten(shipped)
    return sum(1 for p, v in flatten(over).items() if base.get(p) != v)


def overrides_active():
    return config_toml.overrides_enabled() and os.path.isdir(
        CONFIG_DIR / config_toml.LOCAL_DIRNAME)
