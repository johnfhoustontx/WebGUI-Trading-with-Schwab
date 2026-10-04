#!/usr/bin/env python
"""The facts ``tools/promote.sh`` needs about this checkout, as shell lines.

One Python start instead of one per value, and one place a test can call::

    ENV_NAME=prod
    PROXY_PORT=8100
    NICEGUI_PORT=8500
    NICEGUI_LIVE_PORT=8501
    SERVICE_PORTS=8210 8211 8212 8213 8215 8216
    SERVICE_NAMES=sentiment options portfolio trade market news

``promote.sh`` reads them with ``eval``, so every value is restricted to
letters, digits, spaces, dots, dashes and underscores; anything else is an
error rather than something to quote around.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SAFE = re.compile(r"^[A-Za-z0-9 ._-]*$")


def facts() -> dict:
    """The checkout's identity and ports, from ``repo_paths``."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import repo_paths

    names = sorted(repo_paths.SERVICE_PORTS, key=lambda n: repo_paths.SERVICE_PORTS[n])
    return {
        "ENV_NAME": repo_paths.ENV_NAME,
        "PROXY_PORT": repo_paths.PROXY_PORT,
        "NICEGUI_PORT": repo_paths.NICEGUI_PORT,
        "NICEGUI_LIVE_PORT": repo_paths.NICEGUI_LIVE_PORT,
        "SERVICE_PORTS": " ".join(str(repo_paths.SERVICE_PORTS[n]) for n in names),
        "SERVICE_NAMES": " ".join(names),
    }


def shell_lines(values: dict) -> list:
    """``KEY='value'`` lines, refusing any value that is not plainly safe."""
    out = []
    for key, value in values.items():
        text = str(value)
        if not _SAFE.match(text):
            raise ValueError(f"{key} is not safe to hand to a shell: {text!r}")
        out.append(f"{key}='{text}'")
    return out


def main() -> int:
    print("\n".join(shell_lines(facts())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
