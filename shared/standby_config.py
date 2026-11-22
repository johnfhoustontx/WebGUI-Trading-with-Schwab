"""``config/standby.toml`` (the warm standby's schedule, boxes and neighbour
apps) and the machine-local box identity file. Stdlib, ``shared.config_toml``
and ``repo_paths`` only. Design: docs/plans/2026-11-22-warm-standby-design.md

The identity file (``repo_paths.BOX_IDENTITY``) is NOT a config file: the
checkout is identical on both boxes, so which one this is cannot live in it.
It is read with ``tomllib`` directly and written atomically with mode 0600.
"""
import json
import os
import pathlib
import tempfile
import tomllib
from dataclasses import dataclass

import repo_paths
from shared.config_toml import toml_loader

DEFAULTS = {
    "sync": {
        "at": "22:15",
        "max_age_h": 80,
        "max_shrink_pct": 50,
        "keep": 3,
        "connect_timeout_sec": 15,
        "generations": "~/standby/generations",
        "shrink_stores": [
            "options-scanner/data/paper_account.db",
            "options-scanner/data/signals.db",
            "options-scanner/gex_history.db",
        ],
    },
    "fence": {
        "lookup_timeout_s": 4,
        "lookup_retry_s": 12,
        "lookup_pause_s": 2,
        "peer_probe_timeout_s": 5,
    },
    "watch": {
        "status_max_age_h": 26,
    },
    "boxes": {
        "vps2": {
            "ssh": ["trading-prod-2", "63.141.255.25"],
            "public_ip": "63.141.255.25",
            "proxy_health": "https://trading-prod-2.tail3050ed.ts.net:8100/health",
        },
        "vps3": {
            "ssh": ["trading-standby", "192.227.249.22"],
            "public_ip": "192.227.249.22",
            "proxy_health": "https://trading-standby.tail3050ed.ts.net:8100/health",
        },
    },
    "neighbours": [
        {"name": "cos-hub", "dir": "cos-hub",
         "exclude": [".venv", "config.local.toml.bak-*", "__pycache__"],
         "units": ["cos-hub.service"], "files": []},
        {"name": "neuralstrike-reports", "dir": "neuralstrike-reports",
         "exclude": [".venv", "logs", "__pycache__"],
         "units": ["neuralstrike-report@.service",
                   "neuralstrike-report-failed@.service",
                   "neuralstrike-report-*.timer",
                   "neuralstrike-report@globex.service.d"],
         "files": [".config/neuralstrike/claude.env"]},
    ],
}

load, reset_cache = toml_loader(repo_paths.STANDBY_TOML, DEFAULTS, label="standby.toml")

ROLES = ("prod", "standby")


def _table(key) -> dict:
    cfg = load()
    raw = cfg.get(key) if isinstance(cfg, dict) else None
    return raw if isinstance(raw, dict) else {}


def _int(raw, default, minimum, maximum):
    if isinstance(raw, bool) or not isinstance(raw, int) or not minimum <= raw <= maximum:
        return default
    return raw


def _strings(raw, default) -> list:
    if isinstance(raw, list) and all(isinstance(v, str) for v in raw):
        return list(raw)
    return list(default)


def sync_settings() -> dict:
    """The ``[sync]`` table with every value checked; a bad one is its default."""
    raw, d = _table("sync"), DEFAULTS["sync"]
    at = raw.get("at", d["at"])
    try:
        hh, mm = str(at).split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError
    except ValueError:
        at = d["at"]
    gen = raw.get("generations")
    if not isinstance(gen, str) or not gen.strip():
        gen = d["generations"]
    return {
        "at": str(at),
        "max_age_h": _int(raw.get("max_age_h"), d["max_age_h"], 1, 24 * 60),
        "max_shrink_pct": _int(raw.get("max_shrink_pct"), d["max_shrink_pct"], 1, 100),
        "keep": _int(raw.get("keep"), d["keep"], 1, 60),
        "connect_timeout_sec": _int(raw.get("connect_timeout_sec"),
                                    d["connect_timeout_sec"], 1, 300),
        "generations": pathlib.Path(os.path.expanduser(gen)),
        "shrink_stores": _strings(raw.get("shrink_stores"), d["shrink_stores"]),
    }


# (lowest, highest) accepted for each [fence] value. The highest are chosen so the
# worst case (a retried lookup, then the peer probe) stays well under the fence
# unit's TimeoutStartSec=30: a fence that times out FAILS and holds the stack.
FENCE_BOUNDS = {
    "lookup_timeout_s": (1, 10),
    "lookup_retry_s": (0, 15),
    "lookup_pause_s": (1, 5),
    "peer_probe_timeout_s": (1, 8),
}


def fence_settings() -> dict:
    """The ``[fence]`` table, each value within ``FENCE_BOUNDS`` or its default."""
    raw, d = _table("fence"), DEFAULTS["fence"]
    return {k: _int(raw.get(k), d[k], lo, hi) for k, (lo, hi) in FENCE_BOUNDS.items()}


def watch_settings() -> dict:
    """The ``[watch]`` table (prod's morning check), each value checked."""
    raw, d = _table("watch"), DEFAULTS["watch"]
    return {"status_max_age_h": _int(raw.get("status_max_age_h"),
                                     d["status_max_age_h"], 1, 24 * 60)}


def boxes() -> dict:
    """``{name: {ssh, public_ip, proxy_health}}`` in the file's order."""
    out = {}
    for name, entry in _table("boxes").items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        out[name] = {
            "ssh": _strings(entry.get("ssh"), []),
            "public_ip": str(entry.get("public_ip", "")),
            "proxy_health": str(entry.get("proxy_health", "")),
        }
    return out or {k: dict(v) for k, v in DEFAULTS["boxes"].items()}


def neighbours() -> list:
    """The file's ``[[neighbours]]`` list when it has one, else the defaults'.

    A list in a config file means "use exactly these" (``deep_merge`` replaces
    lists), so a file naming one neighbour runs one."""
    cfg = load()
    raw = cfg.get("neighbours") if isinstance(cfg, dict) else None
    if not isinstance(raw, list):
        raw = DEFAULTS["neighbours"]
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name, folder = entry.get("name"), entry.get("dir")
        if not isinstance(name, str) or not isinstance(folder, str) or not name or not folder:
            continue
        out.append({"name": name, "dir": folder,
                    "exclude": _strings(entry.get("exclude"), []),
                    "units": _strings(entry.get("units"), []),
                    "files": _strings(entry.get("files"), [])})
    return out


@dataclass(frozen=True)
class Identity:
    self_name: str
    role: str
    taking_over: bool


def read_identity(path: pathlib.Path | None = None) -> Identity | None:
    """This box's identity, or None when the file is absent, unreadable or has
    a role that is not ``prod`` / ``standby``."""
    path = pathlib.Path(path) if path is not None else repo_paths.BOX_IDENTITY
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    name, role, taking = raw.get("self"), raw.get("role"), raw.get("taking_over", False)
    if not isinstance(name, str) or not name or role not in ROLES:
        return None
    if not isinstance(taking, bool):
        return None
    return Identity(name, role, taking)


def write_identity(identity: Identity, path: pathlib.Path | None = None) -> None:
    """Write the identity file atomically with mode 0600."""
    path = pathlib.Path(path) if path is not None else repo_paths.BOX_IDENTITY
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (f"self = {json.dumps(identity.self_name)}\n"
            f"role = {json.dumps(identity.role)}\n"
            f"taking_over = {'true' if identity.taking_over else 'false'}\n")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def peer(identity: Identity) -> tuple[str, dict]:
    """The other box's name and entry. Raises ``ValueError`` unless the config
    holds exactly two boxes and ``identity`` is one of them."""
    table = boxes()
    if len(table) != 2 or identity.self_name not in table:
        raise ValueError(
            f"standby.toml must hold exactly two boxes including {identity.self_name!r}; "
            f"it holds {sorted(table)}")
    (name,) = [n for n in table if n != identity.self_name]
    return name, table[name]


__all__ = ["DEFAULTS", "load", "reset_cache", "sync_settings", "fence_settings",
           "watch_settings", "FENCE_BOUNDS", "boxes", "neighbours",
           "Identity", "read_identity", "write_identity", "peer", "ROLES"]
