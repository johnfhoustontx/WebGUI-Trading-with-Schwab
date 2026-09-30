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

  // ── How the idea did (site_ideas.refresh) ──────────────────────────────────
  // Each idea follows the app's own exit rules (config/trade_mgmt.toml): closed at
  // its profit target or its stop, the first minute the option reaches one, or
  // settled at expiry. The option is MODELLED from the stock price at the implied
  // volatility of its entry price - no option quote is read or published - so the
  // line says "modelled" and an open idea's figure is an estimate. Without a model
  // (no entry stock price yet) the open line falls back to the expiry payoff at
  // today's price, labelled "At the close of today" (operator wording).
  function num(v) {
    return typeof v === "number" && isFinite(v) ? v : null;
  }

  function money(v) {
    var whole = Math.round(Math.abs(v)).toLocaleString("en-US");
    return (v > 0 ? "+" : v < 0 ? "−" : "") + "$" + whole;
  }

  function signedPct(v) {
    return (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "%";
  }

  function price(v) {
    return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function asOf(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var day = new Intl.DateTimeFormat("en-CA", {
      timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit"
    }).format(d);
    var time = new Intl.DateTimeFormat("en-US", {
      timeZone: TZ, hour: "numeric", minute: "2-digit"
    }).format(d);
    return "as of " + (day === todayCT() ? "" : shortDate(day) + ", ") + time + " CT";
  }

  function closedAt(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var day = new Intl.DateTimeFormat("en-CA", {
      timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit"
    }).format(d);
    var time = new Intl.DateTimeFormat("en-US", {
      timeZone: TZ, hour: "numeric", minute: "2-digit"
    }).format(d);
    return shortDate(day) + ", " + time + " CT";
  }

  function resultOf(idea) {
    var r = idea && idea.result;
    if (!r || num(r.pnl) === null || num(r.spot) === null) return null;
    return r;
  }

  function resultBlock(idea) {
    var r = resultOf(idea);
    if (!r) return null;
    var tone = r.pnl > 0 ? " is-up" : r.pnl < 0 ? " is-down" : "";
    var box = el("span", "ns-idea-result" + tone);
    var pct = num(r.pnl_pct) === null ? "" : " (" + signedPct(r.pnl_pct) + " of risk)";
    var move = num(idea.spot) !== null
      ? str(idea.symbol) + " " + price(idea.spot) + " → " + price(r.spot)
        + (num(r.move_pct) === null ? "" : " (" + signedPct(r.move_pct) + ")")
      : str(idea.symbol) + " " + price(r.spot);
    var modelled = r.status === "target" || r.status === "stop" || r.basis === "model";
    if ((r.status === "target" || r.status === "stop") && str(r.closed_at)) {
      var when = closedAt(r.closed_at);
      box.appendChild(el("span", "ns-idea-state", (r.status === "target"
        ? "Closed at its profit target" : "Stopped out") + (when ? " · " + when : "")));
      box.appendChild(el("span", "ns-idea-pnl", "Result " + money(r.pnl) + pct));
    } else if (r.status === "expired" && DAY.test(str(r.settled))) {
      box.appendChild(el("span", "ns-idea-state", "Expired " + shortDate(r.settled)
        + " at " + price(r.spot)));
      box.appendChild(el("span", "ns-idea-pnl", "Result " + money(r.pnl) + pct));
    } else if (r.basis === "model") {
      box.appendChild(el("span", "ns-idea-state", move));
      box.appendChild(el("span", "ns-idea-pnl", "Est. value now " + money(r.pnl) + pct
        + (num(r.target) === null ? "" : " · target " + money(r.target))));
    } else {
      box.appendChild(el("span", "ns-idea-state", move));
      box.appendChild(el("span", "ns-idea-pnl", "At the close of today: " + money(r.pnl) + pct));
    }
    var note = [];
    if (r.status === "open") note.push(asOf(r.as_of));
    if (modelled) note.push("modelled");
    if (idea.approx) note.push("approx. entry");
    note = note.filter(Boolean);
    if (note.length) box.appendChild(el("span", "ns-idea-note", note.join(" · ")));
    return box;
  }

  function tally(ideas) {
    var up = 0, down = 0;
    ideas.forEach(function (i) {
      var r = resultOf(i);
      if (!r) return;
      if (r.pnl > 0) up += 1;
      else if (r.pnl < 0) down += 1;
    });
    return up + down ? " · " + up + " ahead, " + down + " behind" : "";
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
    var result = resultBlock(idea);
    if (result) a.appendChild(result);
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
      heading.textContent = dayTitle(day.date) + " · " + n + (n === 1 ? " idea" : " ideas")
        + tally(day.ideas);
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
