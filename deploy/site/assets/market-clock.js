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
 * 2 years so a visitor in early January is not looking at a blind clock.
 * Regenerate it when the later year is the current one.
 *
 * HALF DAYS (since 2026-11-19) come from config/sessions.toml [early_close], a
 * list the operator keeps (the NYSE's July rule is not derived). EARLY_CLOSES
 * maps each one in the covered years to its Eastern close; on any other trading
 * day the session runs to 16:00. Regenerate after changing that list.
 *
 * No dependencies, no build step, no third-party origin.
 */
window.NS_MARKET_CLOCK = (function () {
  "use strict";

  /* Full-day NYSE closures, ISO YYYY-MM-DD, sorted. */
  var HOLIDAYS = [
    "2026-01-01",
    "2026-01-19",
    "2026-02-16",
    "2026-04-03",
    "2026-05-25",
    "2026-06-19",
    "2026-07-03",
    "2026-09-07",
    "2026-11-26",
    "2026-12-25",
    "2027-01-01",
    "2027-01-18",
    "2027-02-15",
    "2027-03-26",
    "2027-05-31",
    "2027-06-18",
    "2027-07-05",
    "2027-09-06",
    "2027-11-25",
    "2027-12-24"
  ];

  /* The regular session, Eastern wall clock. */
  var OPEN_ET = "09:30";
  var CLOSE_ET = "16:00";
  var TIME_ZONE = "America/New_York";

  /* Half days: ISO date -> Eastern close, replacing CLOSE_ET on that date. */
  var EARLY_CLOSES = {"2026-11-27": "13:00", "2026-12-24": "13:00", "2027-11-26": "13:00"};

  return {
    holidays: HOLIDAYS,
    openET: OPEN_ET,
    closeET: CLOSE_ET,
    earlyCloses: EARLY_CLOSES,
    timeZone: TIME_ZONE
  };
})();
