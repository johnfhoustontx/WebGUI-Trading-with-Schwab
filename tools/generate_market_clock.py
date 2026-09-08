"""Emit the NYSE calendar the static site needs, as a JavaScript data file.

``neuralstrike.co`` is a static tree served by Caddy's ``file_server``. Nothing
on the server side runs, so the site cannot be told whether the market is open
-- the "Live screens" link's open/closed glow has to be decided in the visitor's
browser, and that browser needs a holiday calendar and the session bounds.

**Why generated rather than typed.** ``CLAUDE.md`` states the rule outright:
*"Do not add a new holiday literal or window constant anywhere"*. Ten duplicated
holiday sets were once consolidated onto ``shared/market_calendar.py``, which
derives NYSE holidays algorithmically and so needs no yearly edit. A hand-typed
list in JavaScript would be an eleventh copy, and it would rot in the quietest
possible way: nothing turns red when a marketing page glows on Thanksgiving.

**The output is COMMITTED**, unlike the rest of this repo's generated state
(``deploy/site/live/``, ``webgui/data/``). Three reasons, and they only hold
together: it changes about once a year, a stale one degrades to a wrong glow
rather than a broken page, and committing it means a fresh clone serves a
working site with no build step. Regenerating is therefore a deliberate act --
there is no timer -- and ``tools/tests/test_generate_market_clock.py`` fails when
the committed bytes drift from what this emits.

``render()`` is pure and returns the file as a string; ``main()`` is the only
thing that touches disk. That is what lets the tests assert on the real output
without writing anything.

Scope: this emits DATA. Deciding the market state and toggling a class is the
consumer's job, in the site's own script -- a DOM reference in here would mean
the two halves had started to merge, and a test pins that they have not.
"""
import datetime
import json
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402
from shared import market_calendar as mc  # noqa: E402

# The years the file covers. Two, not one: a file generated in December that
# carried only that year would be blind on 2 January -- the first trading day of
# the next one -- and a blind clock reads as "closed", i.e. the glow simply never
# comes on and nobody notices for a fortnight.
CURRENT_YEAR = datetime.date.today().year
YEARS_EMITTED = 2

OUTPUT_PATH = pathlib.Path(repo_paths.SITE_ROOT) / "assets" / "market-clock.js"

# The browser reasons in Eastern -- that is how the market's own hours are quoted
# and what ``Intl`` gives a visitor in any zone. ``config/sessions.toml`` states
# them in CENTRAL, so the conversion happens here rather than being re-typed as
# 09:30/16:00 on the far side, which is the second copy that config file exists
# to prevent.
EASTERN = "America/New_York"


def _holidays() -> list:
    """Every NYSE full-day closure in the covered years, as ISO strings.

    Straight out of ``market_calendar.nyse_holidays`` -- this function invents
    nothing and must not start to. Note the module's documented year-boundary
    spill: when 1 Jan falls on a Saturday the observed closure is 31 Dec of the
    PREVIOUS year, so a year's set can contain a date outside it. Taking the
    union and sorting handles that without a special case.
    """
    days = set()
    for offset in range(YEARS_EMITTED):
        days |= mc.nyse_holidays(CURRENT_YEAR + offset)
    return sorted(d.isoformat() for d in days)


def _eastern(t: datetime.time) -> str:
    """A Central wall-clock time as its Eastern ``"HH:MM"`` equivalent.

    A plain +1 hour, and this converts rather than adding one so that the fact is
    derived rather than asserted. It is safe on every date because the two zones
    share DST rules and shift together -- ``config/sessions.toml`` says so in its
    own header ("ET and CT observe DST together, so these CT values are stable
    year-round"), which is exactly why the file can state CT times with ET
    annotations and need no seasonal edit. The reference day is therefore
    arbitrary; today's is used so that nothing has to be chosen. Neither bound
    lands near 02:00, so no DST gap or fold is in play.
    """
    ct = datetime.datetime.combine(datetime.date.today(), t, tzinfo=mc.CT)
    return ct.astimezone(ZoneInfo(EASTERN)).strftime("%H:%M")


def _session_bounds_et():
    """``(open, close)`` for the regular session, in Eastern ``"HH:MM"``.

    Reads ``market_calendar._session_bounds`` -- the same accessor ``session_at``
    and ``mins_to_close`` use -- rather than parsing the TOML here. It is private,
    and that is the point: going through it means this file and the app's own
    "is the market open" predicate cannot answer differently, and it inherits the
    loader's never-raises fallback to the built-in defaults.
    """
    start, end = mc._session_bounds("regular")
    return _eastern(start), _eastern(end)


_HEADER = """\
/* NeuralStrike market clock -- GENERATED DATA. Do not hand-edit.
 *
 * Written by tools/generate_market_clock.py from shared/market_calendar.py (the
 * NYSE holidays, derived algorithmically -- there is no list to maintain) and
 * config/sessions.toml (the regular session, stated there in Central and
 * converted to Eastern here). Regenerate with:
 *
 *     python -m tools.generate_market_clock
 *
 * This file is COMMITTED so a fresh clone serves a working site, and it covers
 * {years} years so a visitor in early January is not looking at a blind clock.
 * Regenerate it when the later year is the current one.
 *
 * KNOWN GAP -- EARLY CLOSES ARE NOT HANDLED (decided 2026-09-08). The NYSE shuts
 * at 13:00 ET on roughly three afternoons a year (the eves of Independence Day
 * and Christmas, and the Friday after Thanksgiving). Nothing in this repo models
 * a half day -- there is no is_half_day anywhere -- so on those afternoons this
 * data says the session runs to {close}. It is decoration with no downstream
 * consumer, so the gap was accepted rather than papered over with another
 * hand-maintained date list. Written down here so the next reader finds it
 * before Black Friday does.
 *
 * No dependencies, no build step, no third-party origin.
 */
"""


def render() -> str:
    """The file's exact bytes, as a string. Pure -- no disk, no network."""
    open_et, close_et = _session_bounds_et()
    header = _HEADER.format(years=YEARS_EMITTED, close=close_et)
    # json.dumps rather than string formatting: a malformed literal here would
    # make the browser refuse the whole file SILENTLY, leaving a dead glow and
    # nothing to see. One date per line so a yearly regeneration is a readable
    # diff instead of one 300-character line.
    dates = ",\n".join(f"    {json.dumps(d)}" for d in _holidays())
    return (
        f"{header}"
        f"window.NS_MARKET_CLOCK = (function () {{\n"
        f'  "use strict";\n'
        f"\n"
        f"  /* Full-day NYSE closures, ISO YYYY-MM-DD, sorted. */\n"
        f"  var HOLIDAYS = [\n{dates}\n  ];\n"
        f"\n"
        f"  /* The regular session, Eastern wall clock. See the early-close gap above. */\n"
        f'  var OPEN_ET = "{open_et}";\n'
        f'  var CLOSE_ET = "{close_et}";\n'
        f'  var TIME_ZONE = "{EASTERN}";\n'
        f"\n"
        f"  return {{\n"
        f"    holidays: HOLIDAYS,\n"
        f"    openET: OPEN_ET,\n"
        f"    closeET: CLOSE_ET,\n"
        f"    timeZone: TIME_ZONE\n"
        f"  }};\n"
        f"}})();\n"
    )


def main() -> int:
    r"""Write the file. The only thing here that touches disk.

    ``newline="\n"`` because this box's git runs ``core.autocrlf=true``: without
    it the generator would emit CRLF on Windows and LF on the Linux prod box, and
    the committed artifact would flip back and forth for no reason anyone reading
    the diff could see.
    """
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
