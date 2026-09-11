/**
 * Board clock (Issue 5): keeps the waiting-room screen's clock right without a reload.
 *
 * Every element marked ``data-board-clock`` shows the time in Africa/Johannesburg, whatever zone
 * the kiosk's own clock is set to, so a board shows the clinic's time even on a TV that shipped
 * set to UTC. Updates on the minute; external file only (CSP ``script-src 'self'``).
 */
(function () {
  "use strict";

  var format = new Intl.DateTimeFormat("en-ZA", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Africa/Johannesburg",
  });

  function tick() {
    var now = new Date();
    document.querySelectorAll("[data-board-clock]").forEach(function (el) {
      el.textContent = format.format(now);
      el.setAttribute("datetime", now.toISOString());
    });
    // Next update just after the next minute starts.
    setTimeout(tick, 60000 - (now.getSeconds() * 1000 + now.getMilliseconds()) + 50);
  }

  tick();
})();
