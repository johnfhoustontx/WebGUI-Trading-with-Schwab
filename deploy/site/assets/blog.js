/* The Blog: blog.html's list of entries, drawn from blog.json.
 *
 * blog.json and everything under blog/ are GENERATED on the serving box by
 * services/blog_svc/sitewriter.py when the operator publishes an entry, and are
 * gitignored (a tracked file would dirty prod's tree). So on a fresh deploy, or
 * before the first entry, there is nothing to draw: the list stays empty and the
 * page says no entry has been published yet.
 *
 * Four rules:
 *  - The manifest is DATA, never markup: every node is built with createElement
 *    and textContent, and no string from it is ever parsed as a document. A
 *    title is whatever was typed into the private Blog page.
 *  - A row is drawn only when its slug is one the service could have written
 *    (SLUG below is shared/blog_inbox.py's SLUG_RE; deploy/tests/test_site.py
 *    compares the two) and its title is a non-empty string. The slug becomes
 *    part of an address, so anything else is dropped, never repaired.
 *  - Always revalidated (cache: "no-cache"). A stale manifest would hide a new
 *    entry and keep listing one that was unpublished.
 *  - One request, to the manifest beside this page. Nothing is stored, nothing
 *    is scheduled and nothing is fetched from anywhere else.
 *
 * An entry is dated by the day it was published in Central time, the clock the
 * rest of the site keeps. A date that does not parse is left out.
 */
(function () {
  "use strict";

  var TZ = "America/Chicago";
  var MAX_TAGS = 6;
  var SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  /* A date, or a date and a time with its offset. The fraction of a second is
   * matched and then left out: the service writes six digits, and a browser is
   * only obliged to read three. */
  var STAMP = /^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:\d{2}))?$/;

  function str(v) {
    return typeof v === "string" ? v : "";
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function when(iso) {
    var m = STAMP.exec(str(iso));
    if (!m) return null;
    var d = new Date(m[2]
      ? m[1] + "T" + m[2] + ":" + (m[3] || "00") + m[4]
      : m[1] + "T12:00:00Z");              /* noon UTC: the same date everywhere */
    return isNaN(d.getTime()) ? null : d;
  }

  function longDate(d) {
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: TZ, year: "numeric", month: "long", day: "numeric"
      }).format(d);
    } catch (e) {
      return "";                           /* no such zone here: no date, not a wrong one */
    }
  }

  function tagsOf(raw) {
    if (!Array.isArray(raw)) return [];
    return raw
      .map(function (t) { return str(t).trim(); })
      .filter(Boolean)
      .slice(0, MAX_TAGS);
  }

  function clean(manifest) {
    var rows = manifest && Array.isArray(manifest.entries) ? manifest.entries : [];
    var out = [];
    rows.forEach(function (r) {
      if (!r || typeof r !== "object") return;
      var slug = str(r.slug);
      var title = str(r.title).trim();
      if (!SLUG.test(slug) || !title) return;
      out.push({
        slug: slug,
        title: title,
        summary: str(r.summary).trim(),
        published: when(r.published),
        tags: tagsOf(r.tags)
      });
    });
    return out;
  }

  function card(row) {
    var a = el("a", "ns-blog-card");
    a.href = "blog/" + row.slug + "/";
    a.appendChild(el("h2", "ns-blog-card-title", row.title));

    var day = row.published ? longDate(row.published) : "";
    if (day) {
      var time = el("time", "ns-blog-card-date", day);
      time.dateTime = row.published.toISOString();
      a.appendChild(time);
    }

    if (row.summary) a.appendChild(el("p", "ns-blog-card-summary", row.summary));

    if (row.tags.length) {
      var tags = el("span", "ns-blog-card-tags");
      row.tags.forEach(function (t) {
        tags.appendChild(el("span", "ns-blog-card-tag", t));
      });
      a.appendChild(tags);
    }
    return a;
  }

  function start(manifest) {
    var page = document.querySelector("[data-blog-page]");
    if (!page) return;
    var list = page.querySelector("[data-blog-list]");
    var empty = page.querySelector("[data-blog-empty]");
    if (!list || !empty) return;

    var rows = clean(manifest);
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!rows.length) {
      empty.hidden = false;
      return;
    }
    rows.forEach(function (row) { list.appendChild(card(row)); });
  }

  fetch("blog.json", { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(start, function () { start(null); });
})();
