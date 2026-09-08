/* NeuralStrike market glow -- the Live screens button lights up while the US
 * stock market is open. Hand-written; the DATA it reads is generated.
 *
 * Two files on purpose. `market-clock.js` is written by
 * tools/generate_market_clock.py and carries nothing but the holiday list and
 * the session bounds; this file carries the logic and is never regenerated. A
 * generator that owns hand-written code is a generator people stop running.
 *
 * WHY Intl AND NOT AN OFFSET. The question is "what time is it in New York",
 * not "what time is it here". A visitor is in any timezone, and the naive fix
 * -- take UTC and subtract five, or four in summer -- is wrong twice a year in
 * the gap between the US and everyone else's DST switch, and wrong all year for
 * anyone whose own clock is off. Intl.DateTimeFormat with an explicit timeZone
 * asks the browser's own tz database for the exchange's wall clock, which is
 * the only thing the session bounds are stated in.
 *
 * WHY THE WEEKDAY IS NOT AN Intl `weekday` PART. That part is a LOCALE STRING
 * -- "Sat" here, "sam." in French, and a non-Latin script elsewhere -- so
 * matching it means matching text that changes under the visitor. Instead the
 * formatted Y/M/D (numbers, not words) is fed to Date.UTC and read back with
 * getUTCDay(): an integer, identical for every visitor, derived from the
 * exchange's own calendar date.
 *
 * KNOWN GAP -- EARLY CLOSES (decided 2026-09-08, same decision as the clock's).
 * The NYSE shuts at 13:00 ET on roughly three afternoons a year -- the eves of
 * Independence Day and Christmas, and the Friday after Thanksgiving. Nothing in
 * this repo models a half day, so on those afternoons the button stays lit
 * until 16:00. It is decoration with no downstream consumer and no second
 * hand-maintained date list was worth it. Written down so the next reader finds
 * it before Black Friday does.
 *
 * FAILURE IS SILENCE. Every path degrades to an unlit button: no clock data (a
 * fresh clone before the generator ran), no such link in the nav, an engine
 * with no timeZone support. Decoration must never break a page.
 *
 * No dependencies, no build step, no third-party origin.
 */
(function () {
  "use strict";

  var CLASS = "ns-market-open";
  var REFRESH_MS = 60000;   /* a minute is ample for a boundary measured in them */

  /* "09:30" -> 570. Minutes since midnight, so the comparison is one integer
     against another and no Date arithmetic crosses a timezone. */
  function minutesOfDay(hhmm) {
    var bits = String(hhmm).split(":");
    var h = parseInt(bits[0], 10);
    var m = parseInt(bits[1], 10);
    if (!isFinite(h) || !isFinite(m)) { return null; }
    return h * 60 + m;
  }

  /* The exchange's wall clock, as {date: "YYYY-MM-DD", minutes: <int>}. */
  function nowInZone(timeZone) {
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timeZone,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false
    }).formatToParts(new Date());

    var got = {};
    for (var i = 0; i < parts.length; i++) { got[parts[i].type] = parts[i].value; }
    if (!got.year || !got.month || !got.day || !got.hour || !got.minute) { return null; }

    var y = parseInt(got.year, 10);
    var mo = parseInt(got.month, 10);
    var d = parseInt(got.day, 10);
    /* Defensive, not observed here: hour12:false emits "24" for midnight in
       SOME engines (a long-standing Intl quirk), where Chrome as measured on
       2026-09-08 emits "00". Unwrapped, that hour would read as 1440 minutes
       and sit past every bound rather than before them. Cheap to wrap. */
    var h = parseInt(got.hour, 10) % 24;
    var mi = parseInt(got.minute, 10);
    if (!isFinite(y) || !isFinite(mo) || !isFinite(d) || !isFinite(h) || !isFinite(mi)) {
      return null;
    }

    return {
      date: got.year + "-" + got.month + "-" + got.day,
      /* Midnight UTC on those calendar numbers. Nothing local enters, so
         getUTCDay() reads the weekday of the EXCHANGE's date: 0 = Sunday. */
      weekday: new Date(Date.UTC(y, mo - 1, d)).getUTCDay(),
      minutes: h * 60 + mi
    };
  }

  function isOpen(clock) {
    if (!clock || !clock.timeZone) { return false; }

    var open = minutesOfDay(clock.openET);
    var close = minutesOfDay(clock.closeET);
    if (open === null || close === null) { return false; }

    var now = nowInZone(clock.timeZone);
    if (!now) { return false; }

    if (now.weekday === 0 || now.weekday === 6) { return false; }

    var holidays = clock.holidays || [];
    for (var i = 0; i < holidays.length; i++) {
      if (holidays[i] === now.date) { return false; }
    }

    /* Half-open: at 16:00 exactly the session is over. */
    return now.minutes >= open && now.minutes < close;
  }

  function apply(link, clock) {
    /* classList.toggle's second argument is the state, so this both lights the
       button at the open and puts it out at the close on a page left running. */
    link.classList.toggle(CLASS, isOpen(clock));
  }

  try {
    var clock = window.NS_MARKET_CLOCK;
    if (!clock) { return; }                      /* no data, no glow, no noise */

    /* Selected on the href rather than a class: the treatment on this button
       has already changed once, and where it points has not. */
    var link = document.querySelector('nav a[href="live.html"]');
    if (!link) { return; }

    apply(link, clock);
    setInterval(function () {
      try { apply(link, clock); } catch (err) { /* leave it as it stands */ }
    }, REFRESH_MS);
  } catch (err) {
    /* Any failure at all leaves the button in its ordinary, un-glowed state. */
  }
})();
