/* Trade ideas: the home page's strip and ideas.html, drawn from ideas.json.
 *
 * ideas.json and the cards under ideas/<day>/ are GENERATED on the serving box by
 * services/options_svc/site_ideas.py each time the hourly trade idea posts, and are
 * gitignored (a tracked file would dirty prod's tree). So on a fresh deploy, or
 * before the first post, there is nothing to draw: the strip ships `hidden` and
 * stays hidden, and the page says no idea has been posted yet.
 *
 * Three rules:
 *  - The manifest is DATA, never markup: every node is built with createElement
 *    and textContent, and a card's image paths must match the shape site_ideas
 *    writes, or the card is dropped.
 *  - Always revalidated (cache: "no-cache"). The Caddy rule that says the same is
 *    installed by hand, and a stale manifest would show yesterday's trades as
 *    today's.
 *  - "Today" means today in Central time, the clock the posts run on. Otherwise a
 *    day is named for itself, so an old card is never called today's.
 */
(function () {
  "use strict";

  var TZ = "America/Chicago";
  var STRIP_COUNT = 3;
  var DAY = /^\d{4}-\d{2}-\d{2}$/;
  var REF = /^ideas\/\d{4}-\d{2}-\d{2}\/[A-Za-z0-9-]+\.(png|webp)$/;

  function todayCT() {
    // en-CA formats as YYYY-MM-DD.
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit"
    }).format(new Date());
  }

  function asDate(day) {
    return new Date(day + "T12:00:00Z");   // noon UTC: the same date everywhere
  }

  function weekday(day) {
    return new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: "UTC" })
      .format(asDate(day));
  }

  function shortDate(day) {
    return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" })
      .format(asDate(day));
  }

  function dayTitle(day) {
    return (day === todayCT() ? "Today" : weekday(day)) + ", " + shortDate(day);
  }

  function stripHeading(day) {
    return (day === todayCT() ? "Today’s" : weekday(day) + "’s") + " trade ideas";
  }

  function str(v) {
    return typeof v === "string" ? v : "";
  }

  function clean(manifest) {
    var days = manifest && Array.isArray(manifest.days) ? manifest.days : [];
    var out = [];
    days.forEach(function (d) {
      if (!d || !DAY.test(str(d.date)) || !Array.isArray(d.ideas)) return;
      var ideas = d.ideas.filter(function (i) {
        return i && REF.test(str(i.img)) && REF.test(str(i.full));
      });
      if (ideas.length) out.push({ date: d.date, ideas: ideas });
    });
    return out;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function card(idea, eager) {
    var a = el("a", "ns-idea-card");
    a.href = idea.full;
    a.target = "_blank";
    a.rel = "noopener";

    var img = el("img");
    img.src = idea.img;
    img.alt = str(idea.alt) || str(idea.symbol) + " trade idea";
    img.width = 1200;
    img.height = 675;
    img.loading = eager ? "eager" : "lazy";
    img.decoding = "async";

    var meta = el("span", "ns-idea-meta");
    meta.appendChild(el("span", "ns-idea-time", str(idea.time) + " CT"));
    var bits = [str(idea.symbol), str(idea.label)];
    if (str(idea.grade)) bits.push(str(idea.grade));
    meta.appendChild(el("span", "ns-idea-what", bits.filter(Boolean).join(" · ")));

    a.appendChild(img);
    a.appendChild(meta);
    return a;
  }

  function fill(grid, ideas, eagerCount) {
    while (grid.firstChild) grid.removeChild(grid.firstChild);
    ideas.forEach(function (idea, n) {
      grid.appendChild(card(idea, n < eagerCount));
    });
  }

  function renderStrip(root, days) {
    if (!days.length) return;                       // stays hidden
    var day = days[0];
    var heading = root.querySelector("[data-ideas-heading]");
    var more = root.querySelector("[data-ideas-more]");
    var grid = root.querySelector("[data-ideas-grid]");
    if (!heading || !more || !grid) return;
    heading.textContent = stripHeading(day.date);
    more.href = "ideas.html?day=" + day.date;
    more.textContent = day.ideas.length > STRIP_COUNT
      ? "See all " + day.ideas.length + " →"
      : "Every trade idea →";
    fill(grid, day.ideas.slice(0, STRIP_COUNT), 0);
    root.hidden = false;
  }

  function renderPage(root, days) {
    var picker = root.querySelector("[data-ideas-days]");
    var heading = root.querySelector("[data-ideas-heading]");
    var grid = root.querySelector("[data-ideas-grid]");
    var empty = root.querySelector("[data-ideas-empty]");
    if (!picker || !heading || !grid || !empty) return;
    if (!days.length) {
      empty.hidden = false;
      return;
    }

    var buttons = [];
    function show(date) {
      var day = days.filter(function (d) { return d.date === date; })[0] || days[0];
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", b.dataset.day === day.date ? "true" : "false");
      });
      var n = day.ideas.length;
      heading.textContent = dayTitle(day.date) + " · " + n + (n === 1 ? " idea" : " ideas");
      fill(grid, day.ideas, 2);
      return day.date;
    }

    days.forEach(function (d) {
      var b = el("button", "ns-day", dayTitle(d.date));
      b.type = "button";
      b.dataset.day = d.date;
      b.addEventListener("click", function () {
        var shown = show(d.date);
        try { history.replaceState(null, "", "?day=" + shown); } catch (e) { /* file:// */ }
      });
      buttons.push(b);
      picker.appendChild(b);
    });
    picker.hidden = false;

    var want = null;
    try { want = new URLSearchParams(location.search).get("day"); } catch (e) { /* old browser */ }
    show(want);
  }

  function start(manifest) {
    var days = clean(manifest);
    var strip = document.querySelector("[data-ideas-strip]");
    var page = document.querySelector("[data-ideas-page]");
    if (strip) renderStrip(strip, days);
    if (page) renderPage(page, days);
  }

  fetch("ideas.json", { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(start, function () { start(null); });
})();
