"""One mtime-cached, degrade-to-defaults TOML loader, shared by every config file.

`config/flow_alerts.toml` and `config/sessions.toml` each grew their own copy of
the same ~40 lines. Batch 3 adds four more config files, so the boilerplate lives
here once instead of six times.

The contract every config file in this repo follows:

* **The built-in defaults are the real values.** The TOML only overrides. A
  missing file, an unreadable one, or a syntax error degrades to the defaults and
  **never raises** - a typo in a config file must not take a service down.
* **Deep-merged**, so a file that sets one key inside one table keeps every
  sibling and every untouched section.
* **mtime-cached**, because these are read on hot paths (the 1-min flow-alert
  tick) while the files change about monthly. The documented operator flow is
  *edit the TOML, restart the service*; the mtime check means a live edit is also
  picked up on the next read.

Pure stdlib (``tomllib`` + ``os``) with no repo imports, so it is safe for Tier 1
to read as well - the same shape as ``shared.market_calendar``.

**The operator OVERRIDE layer (2026-09-19).** Every ``config/<name>.toml`` may
have a sibling ``config/local/<name>.toml`` (gitignored) holding only the values
the operator changed in Settings -> Configuration. Load order is
``built-in defaults <- tracked file <- local override``, deep-merged the same
way. The tracked file therefore stays the SHIPPED value: editing it from the app
would dirty the prod checkout, and ``tools/promote.sh`` refuses a dirty tree, so
the first saved setting would have blocked every later promote. "Reset to the
shipped value" is simply deleting the override key.

⚠ Under pytest the override layer is IGNORED (``"pytest" in sys.modules``), so a
tuned prod checkout still runs the suite against the shipped values. The check is
on ``sys.modules`` rather than ``PYTEST_CURRENT_TEST`` because many consumers
resolve module-level constants at IMPORT, which is collection time, when that
variable is not yet set. Tests of the layer itself set
``TRADING_CONFIG_OVERRIDES_IN_TESTS=1``.
"""
import logging
import os
import sys
import tomllib

log = logging.getLogger(__name__)


def deep_merge(base, over):
    """``over`` layered onto ``base``; both left untouched. Tables merge, scalars
    and lists replace (a list in a config file means "use exactly these", never
    "append to the defaults")."""
    out = {k: (deep_merge(v, {}) if isinstance(v, dict)
               else list(v) if isinstance(v, list) else v)
           for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif isinstance(v, dict):
            out[k] = deep_merge(v, {})
        else:
            out[k] = list(v) if isinstance(v, list) else v
    return out


LOCAL_DIRNAME = "local"


def overlay_path(path):
    """``config/local/<name>.toml`` for ``config/<name>.toml``."""
    path = os.fspath(path)
    return os.path.join(os.path.dirname(path), LOCAL_DIRNAME, os.path.basename(path))


def overrides_enabled():
    """False inside a pytest process unless a test opts in (see module doc)."""
    if "pytest" in sys.modules:
        return os.environ.get("TRADING_CONFIG_OVERRIDES_IN_TESTS") == "1"
    return True


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except Exception:
        return None


def layered_mtime(path):
    """Cache key covering BOTH layers, so saving an override is noticed."""
    over = _mtime(overlay_path(path)) if overrides_enabled() else None
    return (_mtime(path), over)


def read_overrides(path):
    """The local override table for ``path`` (``{}`` when absent or disabled).

    A malformed override file is logged and IGNORED rather than raised: the
    shipped file underneath is still good, and a bad override must never cost
    the operator the rest of their configuration."""
    if not overrides_enabled():
        return {}
    op = overlay_path(path)
    try:
        with open(op, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        log.warning("%s could not be parsed - ignoring the local overrides", op,
                    exc_info=True)
        return {}


def read_layered(path):
    """The tracked file with its local overrides merged on top.

    Raises exactly what reading the TRACKED file raises (``FileNotFoundError``,
    a parse error), so every existing loader keeps its own degrade policy for the
    base file. Only the override layer is forgiving."""
    with open(path, "rb") as fh:
        base = tomllib.load(fh)
    return deep_merge(base, read_overrides(path))


def toml_loader(path, defaults, *, label=None):
    """Return ``(load, reset)`` for one config file.

    ``load()`` gives the file deep-merged over ``defaults`` - built fresh on every
    cache miss and never the ``defaults`` object itself, so a caller that mutates
    its result cannot poison the module-level defaults. It DOES hand back the
    cached mapping on a hit (copying on every hot-path read would defeat the
    cache), so **treat a config dict as read-only** - the same convention the
    flow_alerts and sessions loaders already rely on. ``reset()`` drops the cache
    (test hook, and the way to force a re-read inside one mtime tick).
    """
    name = label or os.path.basename(str(path))
    cache: dict[str, object] = {"mtime": None, "cfg": None}

    def reset():
        cache.update(mtime=None, cfg=None)

    def load():
        mtime = layered_mtime(path)
        if cache["cfg"] is not None and cache["mtime"] == mtime:
            return cache["cfg"]
        try:
            cfg = deep_merge(defaults, read_layered(path))
        except FileNotFoundError:
            cfg = deep_merge(defaults, read_overrides(path))
        except Exception:
            # A malformed file is worth a line - silently running on defaults is
            # how a config edit "does nothing" for a week.
            log.warning("%s could not be parsed - using built-in defaults", name,
                        exc_info=True)
            cfg = deep_merge(defaults, {})
        cache.update(mtime=mtime, cfg=cfg)
        return cfg

    return load, reset


# ── Writing the override layer (Settings -> Configuration) ──────────────────
# A deliberately small TOML writer: the override files are machine-written and
# hold only scalars, flat lists, nested tables and (for symbols.toml) one array
# of tables. Every write is parsed back with tomllib before it replaces the file,
# so the writer can never leave behind a file the loaders cannot read.

import json as _json
import re as _re

_BARE_KEY = _re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k):
    k = str(k)
    return k if _BARE_KEY.match(k) else _json.dumps(k, ensure_ascii=False)


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("a config value must be a finite number")
        return repr(v)
    if isinstance(v, str):
        return _json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"unsupported config value {v!r}")


def _is_table_array(v):
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def dumps(data):
    """TOML text for ``data`` (a dict of scalars, lists and nested tables)."""
    out = []

    def emit(table, prefix):
        scalars = [(k, v) for k, v in table.items()
                   if not isinstance(v, dict) and not _is_table_array(v)]
        if prefix and (scalars or not any(isinstance(v, dict) or _is_table_array(v)
                                          for v in table.values())):
            out.append(f"[{'.'.join(_key(p) for p in prefix)}]")
        for k, v in scalars:
            out.append(f"{_key(k)} = {_value(v)}")
        if scalars:
            out.append("")
        for k, v in table.items():
            if isinstance(v, dict):
                emit(v, prefix + [k])
            elif _is_table_array(v):
                for item in v:
                    out.append(f"[[{'.'.join(_key(p) for p in prefix + [k])}]]")
                    for ik, iv in item.items():
                        out.append(f"{_key(ik)} = {_value(iv)}")
                    out.append("")

    emit(data, [])
    return "\n".join(out).rstrip() + "\n"


def write_overrides(path, overrides, *, header=None):
    """Replace ``config/local/<name>.toml`` with ``overrides``; an empty mapping
    deletes the file (nothing overridden = the shipped values). Atomic: written
    to a temp file, parsed back, then ``os.replace``d over the old one."""
    op = overlay_path(path)
    if not overrides:
        try:
            os.remove(op)
        except FileNotFoundError:
            pass
        return op
    text = dumps(overrides)
    if header:
        text = "".join(f"# {line}\n" for line in header.splitlines()) + "\n" + text
    if tomllib.loads(text) != _json.loads(_json.dumps(overrides)):
        raise ValueError("override did not survive a TOML round trip")
    os.makedirs(os.path.dirname(op), exist_ok=True)
    tmp = op + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, op)
    return op
