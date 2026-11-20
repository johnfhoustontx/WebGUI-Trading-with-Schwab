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

  /* The 3D set (2026-11-20): rounded clay shapes lit from the upper left, after a
     reference sheet the operator chose (drawn here, not copied). Every paint is a
     gradient defined inside the SAME icon under an id of its own ("wx-<icon>-<part>"):
     a url() reference that resolves nowhere paints nothing and the shape silently vanishes.
     The soft shadow under each is a CSS drop-shadow (site.css). Skies on a 48 grid,
     readings on 24. */
  var SKY = 'viewBox="0 0 48 48" class="ns-wx-svg"';
  var ROW = 'viewBox="0 0 24 24" class="ns-wx-svg"';
  /* A puffy cloud spanning x 10-41.5, y 11-35; the group around it gives the fill.
     Its gradients are in user space, so the four pieces shade as one body. */
  var PUFF = '<circle cx="17" cy="27" r="7"/><circle cx="25.5" cy="21" r="10"/>' +
    '<circle cx="34" cy="26" r="7.5"/><rect x="10" y="25" width="31.5" height="10" rx="5"/>';
  var PUFF_SHINE = '<ellipse cx="21.5" cy="15.4" rx="4.4" ry="2.1" transform="rotate(-18 21.5 15.4)" fill="#fff"';
  var CLOUD_SPAN = 'gradientUnits="userSpaceOnUse" x1="0" y1="11" x2="0" y2="35"';
  /* Eight rounded rays round (24, 24), and a raindrop centred on (0, 0). */
  var RAYS = (function () {
    var s = "";
    for (var a = 0; a < 360; a += 45) {
      s += '<rect x="21.7" y="1.8" width="4.6" height="9.6" rx="2.3" transform="rotate(' + a + ' 24 24)"/>';
    }
    return s;
  })();
  var DROP = 'd="M0-5.2C1.7-2.6 3.4-.8 3.4 1.4A3.4 3.4 0 0 1-3.4 1.4C-3.4-.8-1.7-2.6 0-5.2z"/>' +
    '<ellipse cx="-1.2" cy="1.5" rx=".8" ry="1.5" fill="#fff" opacity=".75"/></g>';
  var SPARKLE = 'd="M0-3.4Q.7-.7 3.4 0Q.7 .7 0 3.4Q-.7 .7-3.4 0Q-.7-.7 0-3.4z"/>';
  /* The clays: white, night, grey-blue, gold, sun rays and water. */
  var WHITE = '<stop offset="0" stop-color="#ffffff"/><stop offset=".55" stop-color="#eef1f8"/>' +
    '<stop offset="1" stop-color="#b4bdd7"/>';
  var NAVY = '<stop offset="0" stop-color="#959ed8"/><stop offset=".55" stop-color="#5a63a8"/>' +
    '<stop offset="1" stop-color="#2c3270"/>';
  var MIST = '<stop offset="0" stop-color="#d5daef"/><stop offset="1" stop-color="#7c86b2"/>';
  var GOLD = '<stop offset="0" stop-color="#fff6c4"/><stop offset=".3" stop-color="#ffd84a"/>' +
    '<stop offset=".78" stop-color="#f6a91a"/><stop offset="1" stop-color="#d47c08"/>';
  var RAY = '<stop offset=".45" stop-color="#ffe07a"/><stop offset="1" stop-color="#f0930e"/>';
  var WATER = '<stop offset="0" stop-color="#d6efff"/><stop offset=".4" stop-color="#6ab6ff"/>' +
    '<stop offset="1" stop-color="#1d61cc"/>';
  var SUN_AT = 'gradientUnits="userSpaceOnUse" cx="24" cy="24" r="22"';
  var CORE_AT = 'gradientUnits="userSpaceOnUse" cx="20" cy="19" r="17"';
  var CORE_SHINE = '<ellipse cx="19.6" cy="18.6" rx="4.2" ry="2.5" transform="rotate(-35 19.6 18.6)" ' +
    'fill="#fff" opacity=".55"/>';
  var ICONS = {
    clear: '<svg ' + SKY + '><defs>' +
      '<radialGradient id="wx-clear-ray" ' + SUN_AT + '>' + RAY + '</radialGradient>' +
      '<radialGradient id="wx-clear-core" ' + CORE_AT + '>' + GOLD + '</radialGradient></defs>' +
      '<g fill="url(#wx-clear-ray)">' + RAYS + '</g>' +
      '<circle cx="24" cy="24" r="11.6" fill="url(#wx-clear-core)"/>' + CORE_SHINE + '</svg>',
    fair: '<svg ' + SKY + '><defs>' +
      '<radialGradient id="wx-fair-ray" ' + SUN_AT + '>' + RAY + '</radialGradient>' +
      '<radialGradient id="wx-fair-core" ' + CORE_AT + '>' + GOLD + '</radialGradient>' +
      '<linearGradient id="wx-fair-cloud" ' + CLOUD_SPAN + '>' + WHITE + '</linearGradient></defs>' +
      '<g transform="translate(1 1) scale(.7)"><g fill="url(#wx-fair-ray)">' + RAYS + '</g>' +
      '<circle cx="24" cy="24" r="11.6" fill="url(#wx-fair-core)"/>' + CORE_SHINE + '</g>' +
      '<g transform="translate(4 6)"><g fill="url(#wx-fair-cloud)">' + PUFF + '</g>' +
      PUFF_SHINE + ' opacity=".85"/></g></svg>',
    overcast: '<svg ' + SKY + '><defs>' +
      '<linearGradient id="wx-overcast-back" ' + CLOUD_SPAN + '>' + MIST + '</linearGradient>' +
      '<linearGradient id="wx-overcast-cloud" ' + CLOUD_SPAN + '>' + WHITE + '</linearGradient></defs>' +
      '<g transform="translate(-4 -5) scale(.82)"><g fill="url(#wx-overcast-back)">' + PUFF + '</g></g>' +
      '<g transform="translate(3 4)"><g fill="url(#wx-overcast-cloud)">' + PUFF + '</g>' +
      PUFF_SHINE + ' opacity=".85"/></g></svg>',
    showers: '<svg ' + SKY + '><defs>' +
      '<linearGradient id="wx-showers-cloud" ' + CLOUD_SPAN + '>' + WHITE + '</linearGradient>' +
      '<linearGradient id="wx-showers-drop" x1=".25" y1="0" x2=".75" y2="1">' + WATER + '</linearGradient></defs>' +
      '<g transform="translate(0 -7)"><g fill="url(#wx-showers-cloud)">' + PUFF + '</g>' +
      PUFF_SHINE + ' opacity=".85"/></g>' +
      '<g transform="translate(15 37) rotate(16)"><path fill="url(#wx-showers-drop)" ' + DROP +
      '<g transform="translate(24.5 40.5) rotate(16)"><path fill="url(#wx-showers-drop)" ' + DROP +
      '<g transform="translate(34 37) rotate(16)"><path fill="url(#wx-showers-drop)" ' + DROP + '</svg>',
    storm: '<svg ' + SKY + '><defs>' +
      '<linearGradient id="wx-storm-cloud" ' + CLOUD_SPAN + '>' + NAVY + '</linearGradient>' +
      '<linearGradient id="wx-storm-bolt" gradientUnits="userSpaceOnUse" x1="20" y1="24" x2="30" y2="44">' +
      GOLD + '</linearGradient>' +
      '<linearGradient id="wx-storm-drop" x1=".25" y1="0" x2=".75" y2="1">' + WATER + '</linearGradient></defs>' +
      '<g transform="translate(0 -8)"><g fill="url(#wx-storm-cloud)">' + PUFF + '</g>' +
      PUFF_SHINE + ' opacity=".35"/></g>' +
      '<g transform="translate(12.5 34) rotate(16) scale(.8)"><path fill="url(#wx-storm-drop)" ' + DROP +
      '<g transform="translate(37 33) rotate(16) scale(.8)"><path fill="url(#wx-storm-drop)" ' + DROP +
      '<polygon fill="url(#wx-storm-bolt)" stroke="#c97200" stroke-width=".7" stroke-linejoin="round" ' +
      'points="26,23 18.5,34.5 24.3,34.5 20.6,44 31.5,30.5 25.8,30.5 29,23"/></svg>',
    fog: '<svg ' + SKY + '><defs>' +
      '<linearGradient id="wx-fog-cloud" ' + CLOUD_SPAN + '>' + WHITE + '</linearGradient>' +
      '<linearGradient id="wx-fog-bar" gradientUnits="userSpaceOnUse" x1="0" y1="31" x2="0" y2="47">' +
      WHITE + '</linearGradient></defs>' +
      '<g transform="translate(0 -8)"><g fill="url(#wx-fog-cloud)">' + PUFF + '</g>' +
      PUFF_SHINE + ' opacity=".85"/></g>' +
      '<g fill="url(#wx-fog-bar)"><rect x="7" y="31" width="27" height="3.6" rx="1.8"/>' +
      '<rect x="14" y="37" width="28" height="3.6" rx="1.8"/>' +
      '<rect x="9" y="43" width="20" height="3.6" rx="1.8"/></g></svg>',
    moon: '<svg ' + SKY + '><defs>' +
      '<radialGradient id="wx-moon-body" gradientUnits="userSpaceOnUse" cx="15" cy="15" r="26">' +
      GOLD + '</radialGradient>' +
      '<linearGradient id="wx-moon-star" x1="0" y1="0" x2="1" y2="1">' + GOLD + '</linearGradient></defs>' +
      '<path fill="url(#wx-moon-body)" d="M33 31.5A13.5 13.5 0 0 1 18 11.5a14 14 0 1 0 15 20z"/>' +
      '<g fill="url(#wx-moon-star)"><path transform="translate(37 10)" ' + SPARKLE +
      '<path transform="translate(41.5 22.5) scale(.65)" ' + SPARKLE +
      '<path transform="translate(30 4.5) scale(.5)" ' + SPARKLE + '</g></svg>',
    temperature: '<svg ' + ROW + '><defs>' +
      '<linearGradient id="wx-temperature-glass" gradientUnits="userSpaceOnUse" x1="7.4" y1="0" x2="16.6" y2="0">' +
      '<stop offset="0" stop-color="#dfe5f3"/><stop offset=".4" stop-color="#ffffff"/>' +
      '<stop offset="1" stop-color="#a7b1ce"/></linearGradient></defs>' +
      '<g fill="url(#wx-temperature-glass)"><rect x="9" y="1.5" width="6" height="15" rx="3"/>' +
      '<circle cx="12" cy="18" r="4.6"/></g>' +
      '<g class="ns-wx-liquid"><rect x="10.9" y="7" width="2.2" height="11" rx="1.1"/>' +
      '<circle cx="12" cy="18" r="3.1"/></g>' +
      '<ellipse cx="10.9" cy="16.9" rx=".85" ry="1.2" fill="#fff" opacity=".7"/>' +
      '<path d="M10.4 3.6v9.4" stroke="#fff" stroke-width=".9" stroke-linecap="round" opacity=".85"/></svg>',
    coverage: '<svg ' + ROW + '><defs>' +
      '<linearGradient id="wx-coverage-drop" x1=".25" y1="0" x2=".75" y2="1">' + WATER + '</linearGradient></defs>' +
      '<path fill="url(#wx-coverage-drop)" d="M12 2.2C14.6 6 18.2 9.9 18.2 14.4A6.2 6.2 0 0 1 5.8 14.4C5.8 9.9 9.4 6 12 2.2z"/>' +
      '<ellipse cx="9.3" cy="14.6" rx="1.2" ry="2.3" transform="rotate(12 9.3 14.6)" fill="#fff" opacity=".75"/></svg>',
    wind: '<svg ' + ROW + '><defs>' +
      '<linearGradient id="wx-wind-air" gradientUnits="userSpaceOnUse" x1="2" y1="0" x2="21" y2="0">' +
      '<stop offset="0" stop-color="#d2ebff"/><stop offset=".5" stop-color="#5aa4f2"/>' +
      '<stop offset="1" stop-color="#2a6fd6"/></linearGradient></defs>' +
      '<path transform="translate(0 .9)" fill="none" stroke="#1c4a94" stroke-width="2.6" stroke-linecap="round" ' +
      'opacity=".55" d="M3 9h11a3 3 0 1 0-3-3M3 15h15a3 3 0 1 1-3 3M3 12h7"/>' +
      '<path fill="none" stroke="url(#wx-wind-air)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" ' +
      'd="M3 9h11a3 3 0 1 0-3-3M3 15h15a3 3 0 1 1-3 3M3 12h7"/></svg>',
    pressure: '<svg ' + ROW + '><defs>' +
      '<radialGradient id="wx-pressure-rim" gradientUnits="userSpaceOnUse" cx="9" cy="8" r="15">' + NAVY +
      '</radialGradient>' +
      '<radialGradient id="wx-pressure-face" gradientUnits="userSpaceOnUse" cx="10" cy="9" r="11">' + WHITE +
      '</radialGradient>' +
      '<radialGradient id="wx-pressure-hub" gradientUnits="userSpaceOnUse" cx="11.4" cy="11.9" r="2.4">' + GOLD +
      '</radialGradient></defs>' +
      '<circle cx="12" cy="12.5" r="9.6" fill="url(#wx-pressure-rim)"/>' +
      '<circle cx="12" cy="12.5" r="7.5" fill="url(#wx-pressure-face)"/>' +
      '<path d="M7.2 16.2l.9-.5M12 6.6v1.1M16.8 16.2l-.9-.5M7.9 8.6l.8.8M16.1 8.6l-.8.8" stroke="#7d86ad" ' +
      'stroke-width="1" stroke-linecap="round"/>' +
      '<path d="M12 12.5L8.6 9" stroke="#e5484d" stroke-width="1.7" stroke-linecap="round"/>' +
      '<circle cx="12" cy="12.5" r="1.7" fill="url(#wx-pressure-hub)"/></svg>',
    umbrellas: '<svg ' + ROW + '><defs>' +
      '<linearGradient id="wx-umbrellas-canopy" gradientUnits="userSpaceOnUse" x1="5" y1="3" x2="17" y2="14">' +
      '<stop offset="0" stop-color="#acd5ff"/><stop offset=".5" stop-color="#4f97f0"/>' +
      '<stop offset="1" stop-color="#2357c2"/></linearGradient></defs>' +
      '<path d="M12 12v6.6a2 2 0 0 1-4 0M12 3.2V1.8" fill="none" stroke="#cfd6ea" stroke-width="1.8" ' +
      'stroke-linecap="round"/>' +
      '<path fill="url(#wx-umbrellas-canopy)" d="M2.5 12.6a9.5 9.5 0 0 1 19 0c-1.6-1.3-3.15-1.3-4.75 0' +
      'c-1.6-1.3-3.15-1.3-4.75 0c-1.6-1.3-3.15-1.3-4.75 0c-1.6-1.3-3.15-1.3-4.75 0z"/>' +
      '<path d="M6.2 9.4a6.6 6.6 0 0 1 4.4-4.3" fill="none" stroke="#fff" stroke-width="1.1" ' +
      'stroke-linecap="round" opacity=".6"/></svg>',
    shelter: '<svg ' + ROW + '><defs>' +
      '<linearGradient id="wx-shelter-wall" gradientUnits="userSpaceOnUse" x1="0" y1="10" x2="0" y2="21">' + WHITE +
      '</linearGradient>' +
      '<linearGradient id="wx-shelter-roof" gradientUnits="userSpaceOnUse" x1="0" y1="3" x2="0" y2="13">' + NAVY +
      '</linearGradient>' +
      '<linearGradient id="wx-shelter-door" x1="0" y1="0" x2="0" y2="1">' + GOLD + '</linearGradient></defs>' +
      '<rect x="5" y="10.5" width="14" height="10" rx="1.4" fill="url(#wx-shelter-wall)"/>' +
      '<rect x="10" y="14.2" width="4" height="6.3" rx="1" fill="url(#wx-shelter-door)"/>' +
      '<path fill="url(#wx-shelter-roof)" stroke="url(#wx-shelter-roof)" stroke-width="1.4" stroke-linejoin="round" ' +
      'd="M12 3.4L21 11.6H3z"/></svg>',
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
    var early = clock.earlyCloses && clock.earlyCloses[now.date];   /* a half day */
    var close = minutesOfDay(early || clock.closeET);
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
    /* A final reading from today is the close: 15:00 CT, or a half day's early
       close (config/sessions.toml [early_close], which the clock also carries). */
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
        var li = el("li", "ns-wx-row" + tone(r.tone));   /* the thermometer's liquid */
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
    var open = true;                            /* the readings show until folded away */
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
