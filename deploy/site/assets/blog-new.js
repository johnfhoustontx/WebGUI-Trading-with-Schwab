/* NeuralStrike "new" on Blog -- for a while after an entry is first published,
 * the menu's Blog link carries a small glowing "new" tag.
 *
 * The window is NOT decided here. blog.json carries `new_until`, written by
 * the blog writer (services/blog_svc/sitewriter.new_until: the newest first
 * publication plus [site] new_badge_hours from config/blog.toml), and this
 * file only compares the visitor's clock with it. The live screens' menu
 * makes the same comparison on the server (shared/site_nav.blog_is_new), so
 * the two can never disagree about the window.
 *
 * The tag is the app's Option Signals "new" badge. Its look is .ns-new in
 * site.css; this file adds two classes and one span, nothing else.
 *
 * FAILURE IS SILENCE, as with the market glow: no blog.json (a fresh deploy,
 * nothing published yet), a list that does not parse, no `new_until`, a time
 * that does not read, or no Blog link in the menu all leave the menu exactly
 * as it was. The manifest is data: nothing from it is ever put in the page.
 *
 * One request, to the manifest on this site (always revalidated, like the
 * Blog list's own), nothing stored and nothing fetched from anywhere else. A
 * page left open past `new_until` takes the tag off at that moment.
 */
(function () {
  "use strict";

  var LINK_CLASS = "ns-has-new";
  var TAG_CLASS = "ns-new";
  var TEXT = "new";
  var LABEL = "Blog, new entry";
  /* setTimeout's largest delay; a window longer than this is never taken off
     on an open page, which is fine (720 hours is the configured ceiling). */
  var MAX_DELAY = 2147483647;

  function links() {
    return document.querySelectorAll('nav a[href="blog.html"]');
  }

  function mark(link) {
    if (link.classList.contains(LINK_CLASS)) { return; }
    var tag = document.createElement("span");
    tag.className = TAG_CLASS;
    tag.setAttribute("aria-hidden", "true");
    tag.textContent = TEXT;
    link.classList.add(LINK_CLASS);
    link.setAttribute("aria-label", LABEL);
    link.appendChild(tag);
  }

  function unmark(link) {
    var tags = link.querySelectorAll("." + TAG_CLASS);
    for (var i = 0; i < tags.length; i++) { tags[i].parentNode.removeChild(tags[i]); }
    link.classList.remove(LINK_CLASS);
    link.removeAttribute("aria-label");
  }

  function each(fn) {
    var found = links();
    for (var i = 0; i < found.length; i++) { fn(found[i]); }
  }

  try {
    if (!window.fetch || !links().length) { return; }
    fetch("blog.json", { cache: "no-cache", credentials: "omit" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (list) {
        var until = list && typeof list.new_until === "string" ? Date.parse(list.new_until) : NaN;
        var left = until - Date.now();
        if (!isFinite(left) || left <= 0) { return; }
        each(mark);
        if (left < MAX_DELAY) {
          setTimeout(function () {
            try { each(unmark); } catch (err) { /* leave it as it stands */ }
          }, left);
        }
      })
      .catch(function () { /* no list, no tag */ });
  } catch (err) {
    /* Any failure at all leaves the menu as it was. */
  }
})();
