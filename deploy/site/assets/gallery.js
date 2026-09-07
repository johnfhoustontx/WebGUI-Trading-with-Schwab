/* NeuralStrike gallery — panel switching, and nothing else.
 *
 * The CONTENT lives in gallery.html: all 16 screens are real elements, already
 * in the document. This file only decides which one is visible. With scripting
 * off, `.js` is never added to <html>, so the rules that hide a panel never
 * match and they stack into a long scrolling gallery that works fine.
 *
 * ⚠ Visibility is the `is-active` CLASS and never the `hidden` ATTRIBUTE.
 * `hidden` is honoured by the browser's own stylesheet, so using it here would
 * hide the panels for a scripting-off visitor too -- collapsing the fallback
 * this whole arrangement exists to provide. See the note in site.css.
 *
 * Structure it relies on, all of it emitted by gallery.html:
 *   .ns-rail-row[aria-controls="screen-N"]   one per screen, in order
 *   .ns-screen#screen-N                      the panel
 *     .ns-tab                                one per shot, when there are several
 *     .ns-shot                               the figures, same order as the tabs
 *   [data-nav="prev"|"next"]                 the pager
 *
 * No dependencies, no build step, no third-party origin.
 */
(function () {
  "use strict";

  var rows = Array.prototype.slice.call(document.querySelectorAll(".ns-rail-row"));
  var panels = Array.prototype.slice.call(document.querySelectorAll(".ns-screen"));
  if (!rows.length || rows.length !== panels.length) return;

  var current = 0;

  function shotsOf(panel) {
    return Array.prototype.slice.call(panel.querySelectorAll(".ns-shot"));
  }
  function tabsOf(panel) {
    return Array.prototype.slice.call(panel.querySelectorAll(".ns-tab"));
  }

  /* Show shot `j` of one panel. Kept separate from selectScreen so switching a
   * sub-tab never touches the rail or scrolls the page. */
  function selectShot(panel, j) {
    tabsOf(panel).forEach(function (t, n) {
      t.setAttribute("aria-selected", n === j ? "true" : "false");
    });
    shotsOf(panel).forEach(function (f, n) {
      f.classList.toggle("is-active", n === j);
    });
    var kicker = panel.querySelector(".ns-kicker");
    var tab = tabsOf(panel)[j];
    if (kicker && tab) kicker.textContent = tab.textContent;
  }

  function selectScreen(i, opts) {
    i = (i + panels.length) % panels.length;
    current = i;
    rows.forEach(function (r, n) {
      r.setAttribute("aria-current", n === i ? "true" : "false");
    });
    panels.forEach(function (p, n) {
      p.classList.toggle("is-active", n === i);
    });
    /* Every screen opens on its first section, as the design's pick(i, 0) did. */
    selectShot(panels[i], 0);

    if (opts && opts.focus) rows[i].focus();
    /* Keep the chosen screen linkable, and keep the rail row in view when the
     * pager walks past the edge of a scrolled list. */
    if (opts && opts.push && window.history && history.replaceState) {
      history.replaceState(null, "", "#" + panels[i].id);
    }
    if (opts && opts.scrollRail && rows[i].scrollIntoView) {
      rows[i].scrollIntoView({ block: "nearest" });
    }
  }

  rows.forEach(function (row, i) {
    row.addEventListener("click", function () {
      selectScreen(i, { push: true });
    });
  });

  /* Arrow keys move through the rail, which is what a role="tablist" promises. */
  document.querySelectorAll(".ns-rail-list").forEach(function (list) {
    list.addEventListener("keydown", function (e) {
      var step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      selectScreen(current + step, { push: true, focus: true, scrollRail: true });
    });
  });

  panels.forEach(function (panel) {
    tabsOf(panel).forEach(function (tab, j) {
      tab.addEventListener("click", function () {
        selectShot(panel, j);
      });
    });
  });

  document.querySelectorAll("[data-nav]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var step = btn.getAttribute("data-nav") === "next" ? 1 : -1;
      selectScreen(current + step, { push: true, scrollRail: true });
      /* The pager sits below a tall screenshot, so without this the next screen
       * opens with its heading already scrolled off the top. */
      var top = document.querySelector(".ns-gallery");
      if (top && top.scrollIntoView) top.scrollIntoView({ block: "start" });
    });
  });

  /* Deep links. #screen-N is a real element id, so this URL also works with
   * scripting off -- the browser just jumps to it in the stacked layout. */
  function fromHash() {
    var i = panels.findIndex(function (p) {
      return "#" + p.id === window.location.hash;
    });
    return i < 0 ? null : i;
  }

  window.addEventListener("hashchange", function () {
    var i = fromHash();
    if (i !== null) selectScreen(i, { scrollRail: true });
  });

  selectScreen(fromHash() === null ? 0 : fromHash(), { scrollRail: true });
})();
