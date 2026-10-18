"""Backfill the Opening Scalp's price bars once, by hand.

Walks back from today through Schwab price history (1- and 5-minute, every
``[bars] symbols`` entry in config/scalp.toml) until Schwab has nothing older,
writing ``services/options_svc/data/scalp.db``. Run on the box where the proxy is
up; the nightly ``[slots.scalp_bars]`` top-up keeps it current afterwards:

    .venv/bin/python tools/scalp_backfill.py

Prints the rows written per ``SYMBOL/interval``, and names any key whose walk hit
a FAILED fetch (not merely the end of history); the exit status is 1 then. Safe
to re-run: a stored bar is replaced by the same bar.
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.options_svc import scalp_fetch  # noqa: E402


def main() -> int:
    written = scalp_fetch.backfill()
    failed = []
    for key, count in written.items():
        if count == scalp_fetch.FAILED:
            failed.append(key)
            print(f"{key:<12} FAILED (a fetch failed; history stops short, re-run)")
        else:
            print(f"{key:<12} {count:>8} rows")
    print(f"{'total':<12} {sum(n for n in written.values() if n > 0):>8} rows")
    if failed:
        print(f"{len(failed)} key(s) FAILED: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
