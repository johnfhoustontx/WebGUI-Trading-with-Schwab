"""Write the day's EOD report archive, unattended.

This is the **/eod Generate button, run for you** at ``[slots.eod_report]``
(15:15 CT). It snapshots the already-published ``options:*`` caches and writes
``webgui/data/eod/<date>/summary.html`` + ``detail.html`` — the same two
documents, from the same builders, that the button produces. Nothing is
duplicated here: the report *is* ``webgui/pages/eod.py``.

**Why a tool and a systemd timer rather than a service scheduler slot.** The
builders are Tier-1 webgui code, and Tier 2 may not import them; copying them
into ``options_svc`` would give the app two EOD reports free to drift. The
webgui process has no app-wide scheduler either — its only timers are per-client
``ui.timer``s, so "generate at 15:15" would silently mean "generate at 15:15 if a
browser tab happens to be open". A timer-owned oneshot is the pattern the
gallery capture and the flow-delta instrumentation already use for exactly this
shape of work.

**It makes NO Schwab call and NO Claude call.** It reads Redis and writes two
local files, in well under a second. That is why sharing 15:15 with the
autoscan's last slot and the scheduled Claude close briefing costs nothing.

**An all-empty snapshot writes NOTHING and exits non-zero.** Every builder in
``pages/eod.py`` is deliberately defensive, so a stopped stack — or a bus that
answered but held nothing — renders a complete report of "No data" notes that is
indistinguishable from a real one at a glance. The archive is keyed by DATE and
overwrites in place, so committing that would replace a real report (a hand-run
one, or an earlier firing) with an empty page. Refusing surfaces in
``systemctl --user --failed``; the button, where a person is watching and can
see what they got, keeps its existing unconditional behaviour.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.market_calendar import is_trading_day  # noqa: E402

CT = ZoneInfo("America/Chicago")


def _eod():
    """The webgui EOD module, imported lazily.

    Deferred so ``parse_args`` and the trading-day gate stay importable (and
    testable) without pulling in NiceGUI, and so a holiday firing does not pay
    the ~2 s import to print one line and exit.
    """
    from pages import eod
    return eod


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Write the day's EOD report archive (the /eod Generate "
                    "button, unattended).")
    p.add_argument("--force", action="store_true",
                   help="generate even on a non-trading day")
    p.add_argument("--allow-empty", action="store_true",
                   help="write the report even when every cache read was empty "
                        "(overwrites the date's existing report with a page of "
                        "'No data' notes -- for debugging only)")
    return p.parse_args(argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    args = parse_args(argv)

    today = datetime.now(CT).date()
    if not is_trading_day(today) and not args.force:
        why = "weekend" if today.weekday() >= 5 else "market holiday"
        print(f"Skipped: {today:%Y-%m-%d} is not a trading day ({why}). "
              f"No report written. Use --force to generate anyway.")
        return 0        # a correct skip is not a failure

    eod = _eod()
    snap = eod.read_snapshot()
    if not eod.has_data(snap) and not args.allow_empty:
        print(f"REFUSED: every options cache read for {snap['date']} was empty. "
              f"Nothing written -- an all-'No data' report would overwrite "
              f"{eod.ARCHIVE_ROOT / snap['date']}. Is the stack up, and does "
              f"this process have the bus credentials (MEMURAI_PASSWORD)?",
              file=sys.stderr)
        return 1

    out = eod.generate(snap)
    day = eod.ARCHIVE_ROOT / out["date"]
    print(f"EOD report written for {out['date']}: "
          f"{day / 'summary.html'}, {day / 'detail.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
