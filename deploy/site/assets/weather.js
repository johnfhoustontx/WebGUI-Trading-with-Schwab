/* NeuralStrike market weather card -- the homepage's quick read of today.
 *
 * Reads weather.json (same origin; written by services/market_svc/site_weather.py
 * after each Market read slot) and draws it into <aside data-weather>, which
 * ships hidden. Every word on the card comes from that file; this script only
 * places it, and decides one thing the file cannot know: whether the sky is
 * still current. For that it uses the shipped market clock
 * (assets/market-clock.js, which must load first):
 *
 *   live  - today's reading, younger than two slots
 *   fog   - the session is open but the reading is late: never an old sky
 *   moon  - the session is over and this is its final reading
 *   hide  - anything else; the hero is exactly as it was without this script
 *
 * Strings from the file only ever go in through textContent. The icons are
 * constants below, and setting one is the only innerHTML here. No third-party
 * origin, and nothing announced on an update (a card that speaks every 15
 * minutes is noise).
 * Design: docs/plans/2026-11-19-market-weather-card-design.md.
 */
(function () {
  "use strict";

  var DESK = "https://live.neuralstrike.co/desk";
  var REDRAW_MS = 60000;
  var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  var MINUS = "−";

  /* The symbol set from the design canvas: skies on a 48 grid, readings on 24. */
  var SKY = 'viewBox="0 0 48 48" class="ns-wx-svg"';
  var CLOUD = '<path class="ns-wx-line ns-wx-grey" d="M15 36H35a7 7 0 0 0 0-14a10 10 0 0 0-19-2a8 8 0 0 0-1 16z"/>';
  var ROW = 'viewBox="0 0 24 24" class="ns-wx-svg"';
  var ICONS = {
    clear: '<svg ' + SKY + '><g class="ns-wx-line ns-wx-amber"><circle cx="24" cy="24" r="8.5"/>' +
      '<path d="M24 4v5M24 39v5M4 24h5M39 24h5M9.9 9.9l3.5 3.5M34.6 34.6l3.5 3.5M38.1 9.9l-3.5 3.5M13.4 34.6l-3.5 3.5"/></g></svg>',
    fair: '<svg ' + SKY + '><g class="ns-wx-line ns-wx-amber"><circle cx="18" cy="18" r="7"/>' +
      '<path d="M18 4v4M4 18h4M8.1 8.1l2.8 2.8M27.9 8.1l-2.8 2.8M8.1 27.9l2.8-2.8"/></g>' +
      '<g transform="translate(5,7)"><path class="ns-wx-line ns-wx-grey ns-wx-occlude" d="M15 36H35a7 7 0 0 0 0-14a10 10 0 0 0-19-2a8 8 0 0 0-1 16z"/></g></svg>',
    overcast: '<svg ' + SKY + '><path class="ns-wx-line ns-wx-dim" d="M11 22a9 9 0 0 1 17-5"/>' +
      '<g transform="translate(1,3)">' + CLOUD + '</g></svg>',
    showers: '<svg ' + SKY + '><g transform="translate(0,-6)">' + CLOUD + '</g>' +
      '<path class="ns-wx-line ns-wx-blue" d="M17 36l-2 5M25 36l-2 5M33 36l-2 5M21 43l-1 2.5M29 43l-1 2.5"/></svg>',
    storm: '<svg ' + SKY + '><g transform="translate(0,-7)">' + CLOUD + '</g>' +
      '<polygon class="ns-wx-bolt" points="26,29 19,39 24.5,39 21,47 31,35.5 25.5,35.5 28.5,29"/>' +
      '<path class="ns-wx-line ns-wx-blue" d="M14 34l-2 5M37 34l-2 5"/></svg>',
    fog: '<svg ' + SKY + '><g transform="translate(0,-6)">' + CLOUD + '</g>' +
      '<path class="ns-wx-line ns-wx-dim" d="M9 37h30M14 43h25"/></svg>',
    moon: '<svg ' + SKY + '><g class="ns-wx-line ns-wx-moon"><path d="M33 31.5A13.5 13.5 0 0 1 18 11.5a14 14 0 1 0 15 20z"/>' +
      '<path d="M37 9v5M34.5 11.5h5M40 22v3M38.5 23.5h3"/></g></svg>',
    temperature: '<svg ' + ROW + '><path class="ns-wx-thin" d="M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0zM12 16.5v-6"/></svg>',
    coverage: '<svg ' + ROW + '><path class="ns-wx-thin" d="M12 3.5s-6 7-6 11a6 6 0 0 0 12 0c0-4-6-11-6-11z"/></svg>',
    wind: '<svg ' + ROW + '><path class="ns-wx-thin" d="M3 9h11a3 3 0 1 0-3-3M3 15h15a3 3 0 1 1-3 3M3 12h7"/></svg>',
    pressure: '<svg ' + ROW + '><path class="ns-wx-thin" d="M4.5 17a8.5 8.5 0 1 1 15 0M12 13.5l-4-4"/>' +
      '<circle class="ns-wx-dot" cx="12" cy="13.5" r="1.4"/></svg>',
    umbrellas: '<svg ' + ROW + '><path class="ns-wx-thin" d="M3 12a9 9 0 0 1 18 0zM12 12v7a2 2 0 0 1-4 0M12 3V2"/></svg>',
    shelter: '<svg ' + ROW + '><path class="ns-wx-thin" d="M4 11l8-7 8 7M6 9.5V20h12V9.5M10 20v-5h4v5"/></svg>',
    chevron: '<svg ' + ROW + '><path class="ns-wx-thin" d="M6 9l6 6 6-6"/></svg>'
  };

  /* ── the clock ─────────────────────────────────────────────────────────── */
  function minutesOfDay(hhmm) {
    var bits = String(hhmm).split(":");
    var h = parseInt(bits[0], 10);
    var m = parseInt(bits[1], 10);
    return isFinite(h) && isFinite(m) ? h * 60 + m : null;
  }

  /* The exchange's wall clock -- the same reading market-glow.js takes. */
  function nowInZone(timeZone) {
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timeZone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false
    }).formatToParts(new Date());
    var got = {};
    for (var i = 0; i < parts.length; i++) { got[parts[i].type] = parts[i].value; }
    var date = got.year + "-" + got.month + "-" + got.day;
    return {
      date: date,
      weekday: weekdayOf(date),
      minutes: (parseInt(got.hour, 10) % 24) * 60 + parseInt(got.minute, 10),
      epoch: Math.floor(Date.now() / 1000)
    };
  }

  function utc(date) {
    var b = String(date).split("-");
    return Date.UTC(parseInt(b[0], 10), parseInt(b[1], 10) - 1, parseInt(b[2], 10));
  }

  function weekdayOf(date) { return new Date(utc(date)).getUTCDay(); }

  function addDays(date, n) {
    return new Date(utc(date) + n * 86400000).toISOString().slice(0, 10);
  }

  function isTradingDay(date, clock) {
    var wd = weekdayOf(date);
    if (wd === 0 || wd === 6) { return false; }
    var holidays = clock.holidays || [];
    for (var i = 0; i < holidays.length; i++) {
      if (holidays[i] === date) { return false; }
    }
    return true;
  }

  function stepTradingDay(date, clock, step) {
    var d = date;
    for (var i = 0; i < 14; i++) {
      d = addDays(d, step);
      if (isTradingDay(d, clock)) { return d; }
    }
    return null;
  }

  /* An Eastern minute-of-day as a Central clock: the two zones are always an hour apart. */
  function centralClock(minutesET) {
    var m = minutesET - 60;
    var h = Math.floor(m / 60);
    var mm = m % 60;
    return ((h % 12) || 12) + ":" + (mm < 10 ? "0" : "") + mm + " " + (h < 12 ? "AM" : "PM") + " CT";
  }

  /* ── the decision ──────────────────────────────────────────────────────── */
  function decide(file, now, clock) {
    if (!file || file.hidden || file.v !== 1 || !clock || !now) { return { mode: "hide" }; }
    var open = minutesOfDay(clock.openET);
    var close = minutesOfDay(clock.closeET);
    var interval = file.interval_min > 0 ? file.interval_min : 15;
    if (open === null || close === null) { return { mode: "hide" }; }
    var trading = isTradingDay(now.date, clock);
    var grace = open + 2 * interval;   /* the first slot is due at open + interval */

    function moon() {
      var today = trading && now.minutes < grace;
      var day = today ? now.date : stepTradingDay(now.date, clock, 1);
      return { mode: "moon",
               next: centralClock(open + interval) + " " + (today ? "today" : DAYS[weekdayOf(day)]) };
    }

    if (file.date === now.date && !file.final && now.epoch - file.ts <= 2 * interval * 60) {
      return { mode: "live" };
    }
    /* A final reading from today is the close. (The service marks only the
       15:00 CT slot final: half days are not modelled anywhere yet.) */
    if (file.date === now.date && file.final) { return moon(); }
    if (trading && now.minutes >= open && now.minutes < close) {
      if (now.minutes < grace && file.final && file.date === stepTradingDay(now.date, clock, -1)) {
        return moon();
      }
      return { mode: "fog" };
    }
    var recent = trading && now.minutes >= open ? now.date : stepTradingDay(now.date, clock, -1);
    if (file.final && file.date === recent) { return moon(); }
    return { mode: "hide" };
  }

  window.NS_WEATHER = { decide: decide };

  /* ── drawing ───────────────────────────────────────────────────────────── */
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }

  function icon(key, cls, label) {
    var s = el("span", cls);
    s.innerHTML = ICONS[key];
    if (label) {
      s.setAttribute("role", "img");
      s.setAttribute("aria-label", label);
    } else {
      s.setAttribute("aria-hidden", "true");
    }
    return s;
  }

  function tone(t) { return t === "warm" || t === "cold" ? " is-" + t : ""; }

  function degrees(x) {
    if (typeof x !== "number" || !isFinite(x)) { return null; }
    var text = Math.abs(x).toFixed(1);
    if (parseFloat(text) === 0) { return text + "°"; }
    return (x > 0 ? "+" : MINUS) + text + "°";
  }

  function shortDay(date) { return DAYS[weekdayOf(date)].slice(0, 3); }

  function draw(aside, file, d, open, onToggle) {
    var mode = d.mode;
    var card = el("div", "ns-wx-card");

    var head = el("div", "ns-wx-head");
    var eyebrow = el("span", "ns-wx-eyebrow");
    eyebrow.appendChild(el("span", "ns-wx-live" + (mode === "live" ? " is-on" : "")));
    eyebrow.appendChild(document.createTextNode("Market weather"));
    head.appendChild(eyebrow);
    var stamp = mode === "live" ? "Live · " + file.slot_label
      : mode === "moon" ? "Final · " + shortDay(file.date) + " " + file.slot_label
      : "Delayed";
    head.appendChild(el("span", "ns-wx-stamp", stamp));
    card.appendChild(head);

    var sky = el("div", "ns-wx-sky ns-wx-sky-" + (mode === "live" ? file.sky : mode));
    var skyKey = mode === "live" ? file.sky : mode;
    var label = mode === "live" ? file.label : mode === "moon" ? "After the close" : "Fog";
    sky.appendChild(icon(ICONS[skyKey] ? skyKey : "overcast", "ns-wx-icon", label));
    var words = el("div", "ns-wx-words");
    words.appendChild(el("span", "ns-wx-label", label));
    var temp = mode === "fog" ? null : degrees(file.temp && file.temp.spx_pct);
    if (temp) {
      words.appendChild(el("span", "ns-wx-temp" + tone(file.temp.tone), temp));
      words.appendChild(el("span", "ns-wx-sub",
        mode === "moon" ? "Last sky: " + file.label : "S&P 500 today"));
    }
    sky.appendChild(words);
    card.appendChild(sky);

    var headline = mode === "live" ? file.headline
      : mode === "moon" ? "Next forecast at " + d.next + "."
      : "Readings are delayed, so there is no sky to report right now.";
    if (headline) { card.appendChild(el("p", "ns-wx-headline", headline)); }

    if (mode !== "fog") {
      var tally = el("div", "ns-wx-tally");
      var bars = el("div", "ns-wx-bars");
      bars.setAttribute("aria-hidden", "true");
      for (var i = 0; i < (file.tally || []).length; i++) {
        bars.appendChild(el("span", "ns-wx-bar is-" + file.tally[i]));
      }
      tally.appendChild(bars);
      var line = el("div", "ns-wx-tally-line");
      line.appendChild(el("span", "ns-wx-tally-text", file.tally_text));
      if (file.regime) { line.appendChild(el("span", "ns-wx-chip", file.regime)); }
      tally.appendChild(line);
      card.appendChild(tally);

      var more = el("div", "ns-wx-more");
      more.id = "ns-weather-rows";
      more.hidden = !open;
      var list = el("ul", "ns-wx-rows");
      for (var j = 0; j < (file.readings || []).length; j++) {
        var r = file.readings[j];
        var li = el("li", "ns-wx-row");
        li.appendChild(icon(ICONS[r.key] ? r.key : "chevron", "ns-wx-row-icon"));
        var what = el("span", "ns-wx-row-what");
        what.appendChild(el("span", "ns-wx-row-name", r.name));
        what.appendChild(el("span", "ns-wx-row-sub", r.sub));
        li.appendChild(what);
        li.appendChild(el("span", "ns-wx-row-value" + tone(r.tone), r.value));
        list.appendChild(li);
      }
      more.appendChild(list);
      more.appendChild(el("p", "ns-wx-note",
        "From the Market read at " + file.slot_label + ". Measured readings, not a forecast."));
      card.appendChild(more);
    }

    var foot = el("div", "ns-wx-foot");
    if (mode !== "fog") {
      var button = el("button", "ns-wx-toggle" + (open ? " is-open" : ""),
                      open ? "Hide the readings" : "Show the readings");
      button.type = "button";
      button.setAttribute("aria-expanded", open ? "true" : "false");
      button.setAttribute("aria-controls", "ns-weather-rows");
      button.appendChild(icon("chevron", "ns-wx-chevron"));
      button.addEventListener("click", onToggle);
      foot.appendChild(button);
    }
    var desk = el("a", "ns-wx-desk", "Live Desk →");
    desk.href = DESK;
    foot.appendChild(desk);
    card.appendChild(foot);

    aside.textContent = "";
    aside.appendChild(card);
  }

  /* ── wiring ────────────────────────────────────────────────────────────── */
  try {
    var clock = window.NS_MARKET_CLOCK;
    var aside = document.querySelector("[data-weather]");
    if (!clock || !aside) { return; }          /* no data or no slot: the hero as it was */
    var hero = aside.parentNode;
    var mark = document.querySelector(".ns-hero-mark");
    var file = null;
    var open = false;
    var shown = "";

    var show = function (on) {
      aside.hidden = !on;
      if (mark) { mark.hidden = on; }
      if (hero && hero.classList) { hero.classList.toggle("ns-hero--weather", on); }
      if (!on) { aside.textContent = ""; }
    };

    var redraw = function (force) {
      try {
        var d = decide(file, nowInZone(clock.timeZone), clock);
        var key = d.mode + "|" + (file && file.ts) + "|" + (d.next || "") + "|" + open;
        if (key === shown && !force) { return; }
        shown = key;
        if (d.mode === "hide") { show(false); return; }
        draw(aside, file, d, open, function () { open = !open; redraw(true);
          var again = aside.querySelector(".ns-wx-toggle");   /* the redraw replaced it */
          if (again) { again.focus(); }
        });
        show(true);
      } catch (err) {
        show(false);                            /* any failure: no card, never a broken one */
      }
    };

    var load = function () {
      if (document.visibilityState === "hidden" || typeof fetch !== "function") { return; }
      fetch("weather.json", { cache: "no-cache" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { file = j; redraw(false); })
        .catch(function () { /* keep what is shown; the next pass tries again */ });
    };

    var schedule = function () {
      var secs = file && file.refetch_sec >= 60 && file.refetch_sec <= 3600 ? file.refetch_sec : 300;
      setTimeout(function () { load(); schedule(); }, secs * 1000);
    };

    load();
    schedule();
    setInterval(function () { redraw(false); }, REDRAW_MS);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") { load(); }
    });
  } catch (err) {
    /* Leave the hero exactly as it shipped. */
  }
})();
