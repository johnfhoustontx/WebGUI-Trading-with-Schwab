/* NeuralStrike Live Desk -- the wall: the twelve tiles on a turning 3D ring.
 *
 * live.html's one script of its own. The page ships as a plain grid of links
 * in three group sections; this adds .is-on to .ns-wall, which (site.css)
 * flattens the sections away and stands every tile on the ring. Order around
 * the ring is markup order, and the kicker, name and description in the
 * caption are read from each tile, so nothing here repeats the page's copy.
 *
 * Behaviour: the ring drifts while nobody is touching it (not on hover, focus,
 * a hidden tab, off-screen, or under prefers-reduced-motion). Drag turns it and
 * snaps to the nearest tile on release. A click on a side tile brings it to the
 * front; a click on the front tile follows its link; a drag cancels the click.
 * The arrow buttons and the arrow keys step one tile; Enter on the stage opens
 * the front screen. A group chip turns to that group's first tile.
 *
 * FAILURE IS SILENCE, as with the market glow: if anything here is missing the
 * script returns before adding .is-on, and the visitor has the grid.
 *
 * No dependencies, no build step, no third-party origin.
 */
(function () {
  "use strict";

  // Tunables.
  var AUTO_SPEED = 0.06;   // degrees per frame while idle
  var DRAG_GAIN = 0.25;    // degrees per pixel dragged
  var EASE = 0.09;         // share of the remaining turn taken each frame
  var TILT_X = -7;         // the camera looks slightly down onto the ring
  var CLICK_SLOP = 6;      // pixels a press may move and still be a click
  var CARD_MIN = 200, CARD_MAX = 300, CARD_SHARE = 0.26;

  var wall = document.querySelector(".ns-wall");
  if (!wall || !window.requestAnimationFrame || !Element.prototype.closest) return;
  var stage = wall.querySelector(".ns-wall-stage");
  var ring = wall.querySelector(".ns-wall-ring");
  var cards = ring ? Array.prototype.slice.call(ring.querySelectorAll(".ns-live-tile")) : [];
  var N = cards.length;
  if (!stage || N < 3) return;
  var STEP = 360 / N;

  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function text(root, sel) {
    var el = root && root.querySelector(sel);
    return el ? el.textContent.trim() : "";
  }

  var screens = cards.map(function (a) {
    var group = a.closest(".ns-desk-group");
    return {
      href: a.href,
      name: text(a, ".ns-live-cap"),
      desc: text(a, ".ns-desk-screen-desc"),
      kicker: text(a, ".ns-desk-tile-kick"),
      group: group ? group.id : ""
    };
  });

  // Group chips: each turns to the first tile of its section.
  var chips = Array.prototype.slice.call(document.querySelectorAll('.ns-desk-pill[href^="#"]'))
    .map(function (chip) {
      var gid = chip.getAttribute("href").slice(1);
      var first = -1;
      for (var i = 0; i < N; i++) if (screens[i].group === gid) { first = i; break; }
      return { el: chip, gid: gid, first: first };
    })
    .filter(function (c) { return c.first >= 0; });

  var capKick = wall.querySelector(".ns-wall-cap-kick");
  var capName = wall.querySelector(".ns-wall-cap-name");
  var capDesc = wall.querySelector(".ns-wall-cap-desc");
  var capLink = wall.querySelector(".ns-wall-open");

  var rot = 0, target = null, drag = null, moved = 0, hover = false;
  var front = -1, radius = 600, visible = true, raf = 0;

  function layout() {
    var w = stage.clientWidth;
    var cw = Math.max(CARD_MIN, Math.min(CARD_MAX, Math.round(w * CARD_SHARE)));
    wall.style.setProperty("--ns-card-w", cw + "px");
    radius = Math.round((cw / 2) / Math.tan(Math.PI / N) + 20);
    cards.forEach(function (c, i) {
      c.style.transform = "rotateY(" + (i * STEP) + "deg) translateZ(" + radius + "px)";
    });
    paint();
  }

  function pick(i) {
    var k = Math.round((rot + i * STEP) / 360);
    target = -i * STEP + 360 * k;
    kick();
  }

  function step(d) {
    target = (Math.round((target == null ? rot : target) / STEP) - d) * STEP;
    kick();
  }

  function setFront(f) {
    front = f;
    var s = screens[f];
    cards.forEach(function (c, i) {
      c.classList.toggle("is-front", i === f);
      c.tabIndex = i === f ? 0 : -1;
    });
    chips.forEach(function (c) {
      if (c.gid === s.group) c.el.setAttribute("aria-current", "true");
      else c.el.removeAttribute("aria-current");
    });
    var n = String(f + 1);
    if (capKick) capKick.textContent = s.kicker + " · " + (n.length < 2 ? "0" + n : n) + " / " + N;
    if (capName) capName.textContent = s.name;
    if (capDesc) capDesc.textContent = s.desc;
    if (capLink) capLink.href = s.href;
  }

  function paint() {
    ring.style.transform = "translateZ(" + (-radius) + "px) rotateX(" + TILT_X +
      "deg) rotateY(" + rot + "deg)";
    var f = ((Math.round(-rot / STEP) % N) + N) % N;
    if (f !== front) setFront(f);
  }

  function drifting() { return !drag && !hover && !reduce && target == null; }

  // One frame. It schedules the next only while something is moving, so an
  // idle wall under reduced motion -- or one scrolled away -- costs nothing.
  function tick() {
    raf = 0;
    if (target != null) {
      rot += (target - rot) * (reduce ? 1 : EASE);
      if (Math.abs(target - rot) < 0.05) { rot = target; target = null; }
    } else if (drifting()) {
      rot -= AUTO_SPEED;
    }
    paint();
    if (target != null || drifting()) kick();
  }

  function kick() {
    if (!raf && visible && !document.hidden) raf = window.requestAnimationFrame(tick);
  }

  cards.forEach(function (a, i) {
    a.draggable = false;
    var img = a.querySelector("img");
    if (img) img.draggable = false;
    a.addEventListener("click", function (e) {
      if (moved > CLICK_SLOP) { e.preventDefault(); return; }
      // A modified click (new tab, new window) is the visitor's own business.
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      if (i !== front) { e.preventDefault(); pick(i); }
    });
  });

  chips.forEach(function (c) {
    c.el.addEventListener("click", function (e) {
      e.preventDefault();
      pick(c.first);
    });
  });

  Array.prototype.forEach.call(wall.querySelectorAll(".ns-wall-round"), function (b) {
    b.addEventListener("click", function () { step(Number(b.getAttribute("data-step")) || 0); });
  });

  stage.addEventListener("pointerdown", function (e) {
    if (e.button !== 0) return;
    drag = { x: e.clientX };
    moved = 0;
    target = null;
    kick();
  });
  window.addEventListener("pointermove", function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.x;
    drag.x = e.clientX;
    moved += Math.abs(dx);
    rot += dx * DRAG_GAIN;
    kick();
  });
  function release() {
    if (!drag) return;
    drag = null;
    if (moved > CLICK_SLOP) target = Math.round(rot / STEP) * STEP;
    kick();
    // The click that ends a drag fires after this; clear the slop after it.
    window.setTimeout(function () { moved = 0; }, 0);
  }
  window.addEventListener("pointerup", release);
  window.addEventListener("pointercancel", release);

  function pause() { hover = true; }
  function resume() { hover = false; kick(); }
  stage.addEventListener("mouseenter", pause);
  stage.addEventListener("mouseleave", resume);
  stage.addEventListener("focusin", pause);
  stage.addEventListener("focusout", resume);

  stage.addEventListener("keydown", function (e) {
    if (e.key === "ArrowLeft") { step(-1); e.preventDefault(); }
    else if (e.key === "ArrowRight") { step(1); e.preventDefault(); }
    else if (e.key === "Enter" && e.target === stage) window.location.href = screens[front].href;
  });

  document.addEventListener("visibilitychange", kick);
  if (window.IntersectionObserver) {
    new IntersectionObserver(function (entries) {
      visible = entries[entries.length - 1].isIntersecting;
      kick();
    }).observe(stage);
  }

  stage.tabIndex = 0;
  stage.setAttribute("role", "region");
  stage.setAttribute("aria-roledescription", "carousel");
  stage.setAttribute("aria-label", "Live screens: drag, or use the arrow keys");
  wall.classList.add("is-on");

  var resizeRaf = 0;
  window.addEventListener("resize", function () {
    if (resizeRaf) return;
    resizeRaf = window.requestAnimationFrame(function () { resizeRaf = 0; layout(); });
  });
  layout();
  kick();
})();
