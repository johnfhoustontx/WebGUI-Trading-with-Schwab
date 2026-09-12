"""Pure helpers for per-page UI-state persistence across navigation (Tier-1).

The Calculator + Simulator keep a single-user module-level snapshot of their inputs
and restore it on render (see the page modules). These helpers are the pure,
unit-tested core: whitelist a snapshot and overlay it on defaults. The symbol,
strategy and legs the two pages share live in ``shared_position``."""


def snapshot(values: dict, keys) -> dict:
    """Pick exactly ``keys`` from ``values`` into a fresh dict (the persisted state).

    Missing keys are omitted (not an error); junk / widget refs in ``values`` are
    dropped by virtue of the whitelist."""
    return {k: values[k] for k in keys if k in values}


def merge_restore(snap: dict | None, defaults: dict) -> dict:
    """Overlay a (possibly partial / stale) ``snap`` on ``defaults``.

    Every default key is present in the result; only keys that exist in ``defaults``
    are taken from ``snap`` (a stale snapshot from an older build can't leak a removed
    field). Returns a fresh dict (never the ``defaults`` object)."""
    out = dict(defaults)
    if snap:
        out.update({k: v for k, v in snap.items() if k in defaults})
    return out

