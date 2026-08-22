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
"""
import logging
import os
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
        try:
            mtime = os.stat(path).st_mtime
        except Exception:
            mtime = None
        if cache["cfg"] is not None and cache["mtime"] == mtime:
            return cache["cfg"]
        try:
            with open(path, "rb") as fh:
                cfg = deep_merge(defaults, tomllib.load(fh))
        except FileNotFoundError:
            cfg = deep_merge(defaults, {})
        except Exception:
            # A malformed file is worth a line - silently running on defaults is
            # how a config edit "does nothing" for a week.
            log.warning("%s could not be parsed - using built-in defaults", name,
                        exc_info=True)
            cfg = deep_merge(defaults, {})
        cache.update(mtime=mtime, cfg=cfg)
        return cfg

    return load, reset
