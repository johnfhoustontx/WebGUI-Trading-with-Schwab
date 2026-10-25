/* NeuralStrike Live Desk -- the spotlight that follows the cursor on a card.
 *
 * live.html's one script of its own. Each .ns-desk-spot card carries a
 * .ns-desk-glow layer whose radial gradient is centred on --mx / --my; this
 * sets them to the pointer's position inside the card. Everything else on the
 * page's hover (the lift, the outline, the zoom, the dimmed siblings) is CSS.
 *
 * FAILURE IS SILENCE, as with the market glow: with no script the gradient
 * sits at its CSS default (top centre) and the page works the same.
 *
 * No dependencies, no build step, no third-party origin.
 */
(function () {
  "use strict";

  var cards = document.querySelectorAll(".ns-desk-spot");
  for (var i = 0; i < cards.length; i++) {
    cards[i].addEventListener("mousemove", function (e) {
      var r = this.getBoundingClientRect();
      this.style.setProperty("--mx", (e.clientX - r.left) + "px");
      this.style.setProperty("--my", (e.clientY - r.top) + "px");
    });
  }
})();
