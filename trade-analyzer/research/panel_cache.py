"""Cache one fetched factor panel so every variant is scored on identical bytes.

Phase 0 moved the composite's OOS IC by 44% with **no methodology change at
all** — only a fresh fetch two months later. That is the measurement noise this
study has to sit above, so a variant comparison that re-fetched between runs
would be worthless: a floor change and a fetch-date change would be
indistinguishable.

The KEY is the load-bearing part. Anything that changes the panel's content —
the universe, the window, the label horizon, or the FACTOR SET — must change the
key, because a stale hit answers a question nobody asked. Ordering must not,
since the universe and the registry are both unordered in practice.

Pickle is deliberate: the panel is a MultiIndexed float frame that round-trips
exactly, the file never leaves this machine, and the alternative (parquet) adds
a dependency to the lock for a research-only artifact.
"""
import hashlib
import pickle


def panel_key(symbols, years, horizon, factors) -> str:
    """A short stable digest of everything that determines the panel's content."""
    parts = [
        ",".join(sorted(str(s) for s in symbols)),
        ",".join(sorted(str(f) for f in factors)),
        f"y={years}", f"h={horizon}",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def save(path, panel, forward, meta=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump({"panel": panel, "forward": forward, "meta": dict(meta or {})}, fh)


def load(path):
    """``(panel, forward, meta)``, or None when there is no usable cache.

    A corrupt or half-written cache reads as a miss rather than an exception:
    the caller's fallback is to re-fetch, which is exactly the right response."""
    try:
        with open(path, "rb") as fh:
            blob = pickle.load(fh)
        return blob["panel"], blob["forward"], blob.get("meta", {})
    except Exception:
        return None
