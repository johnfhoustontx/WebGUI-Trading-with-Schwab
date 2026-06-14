"""Bridge writer for downstream consumers (Options Scanner regime_filter,
Blueprint Analyzer, etc.).

This module is intentionally UI-free: it accepts a fully-built payload
dict and writes it to disk. The UI shell in ``sentiment_dashboard.py``
constructs the payload and calls :func:`write_bridge`.

v4.0 — The bridge file canonical location is now
``repo_paths.BRIDGE_PATH`` (``shared/sentiment_bridge.json``). We also
continue to write a copy at the
legacy in-package path with ``deprecated_path: true`` merged in so any
external consumer that hasn't been migrated yet keeps working.
"""
import json
import logging
import sys
import pathlib
from pathlib import Path
from typing import Optional, Union

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import BRIDGE_PATH  # noqa: E402

BRIDGE_SCHEMA_VERSION = "4.2"

# Canonical, project-wide bridge location read by Options Scanner and
# Blueprint Analyzer. Lives outside the SentimentDashboard package so
# downstream tools don't have to import this repo to know where to look.
CANONICAL_PATH = BRIDGE_PATH

# Legacy location kept for backward compatibility while consumers migrate.
LEGACY_PATH = Path(__file__).parent / "sentiment_bridge.json"

logger = logging.getLogger(__name__)


def _write_one(payload: dict, path: Path) -> Path:
    """Internal: write a payload dict to ``path`` as indented JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def write_bridge(
    payload: dict,
    path: Optional[Union[str, Path]] = None,
) -> Path:
    """Write the bridge payload as JSON.

    Default behavior (no ``path`` arg) writes both the canonical and
    legacy locations. The legacy copy gets an extra ``deprecated_path:
    true`` flag merged in to nudge consumers to migrate.

    Tests can pass an explicit ``path`` to redirect writes to a tmp
    directory; in that case only that single path is written.

    Returns the canonical :class:`Path` written (or the override path
    when one was supplied).
    """
    if "schema_version" not in payload:
        payload = {**payload, "schema_version": BRIDGE_SCHEMA_VERSION}

    if path is not None:
        out = Path(path)
        _write_one(payload, out)
        logger.info(
            "Bridge written: score=%s regime=%s -> %s",
            payload.get("composite_score"),
            payload.get("regime"),
            out,
        )
        return out

    # Default: dual-write to canonical + legacy.
    _write_one(payload, CANONICAL_PATH)
    legacy_payload = {**payload, "deprecated_path": True}
    try:
        _write_one(legacy_payload, LEGACY_PATH)
    except OSError as e:
        # Legacy write is best-effort; don't fail the recalc if the
        # legacy directory is read-only or otherwise unwritable.
        logger.warning("Legacy bridge write failed: %s", e)
    logger.info(
        "Bridge written: score=%s regime=%s -> %s (+ legacy %s)",
        payload.get("composite_score"),
        payload.get("regime"),
        CANONICAL_PATH,
        LEGACY_PATH.name,
    )
    return CANONICAL_PATH
