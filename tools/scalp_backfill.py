"""Backfill the Opening Scalp's price bars once, by hand.

Walks back from today through Schwab price history (1- and 5-minute, every
``[bars] symbols`` entry in config/scalp.toml) until Schwab has nothing older,
writing ``services/options_svc/data/scalp.db``. Run on the box where the proxy is
up; the nightly ``[slots.scalp_bars]`` top-up keeps it current afterwards:

    .venv/bin/python tools/scalp_backfill.py

Prints the rows written per ``SYMBOL/interval``. Safe to re-run: a stored bar is
replaced by the same bar.
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.options_svc import scalp_fetch  # noqa: E402


def main() -> int:
    written = scalp_fetch.backfill()
    for key, count in written.items():
        print(f"{key:<12} {count:>8} rows")
    print(f"{'total':<12} {sum(written.values()):>8} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
