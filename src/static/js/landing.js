/**
 * BK ClinicQ — landing page behaviour (`/`).
 *
 * Four small things, each of which degrades to a perfectly readable page when this file
 * does not run: the mock-up tabs, the live waiting-room board simulation, the sticky
 * header's hairline, and the reveal-on-scroll wash.
 *
 * External file with no inline handlers, to satisfy `script-src 'self'` — see
 * `src/core/security_headers.py`. The board simulation is illustrative only: numbers are
 * generated here, no request is made and no patient data is involved.
 */
(function () {
  "use strict";

  var reduceMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  document.addEventListener("DOMContentLoaded", function () {
    setupTabs();
    setupStickyHeader();
    setupReveal();
    setupBoard();
  });

  /* ── Mock-up tabs ────────────────────────────────────────────
     Panels are rendered in the HTML and hidden with the `hidden` attribute, so with
     JavaScript off the visitor still gets the first panel (the others stay hidden but are
     in the document for search engines). Arrow keys move between tabs, per the WAI-ARIA
     tabs pattern. */
  function setupTabs() {
    var tabs = [].slice.call(document.querySelectorAll(".lp-tab"));
    if (!tabs.length) return;

    function select(tab, focus) {
      tabs.forEach(function (other) {
        var on = other === tab;
        other.setAttribute("aria-selected", String(on));
        other.setAttribute("tabindex", on ? "0" : "-1");
        var panel = document.getElementById(other.getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { select(tab, false); });
      tab.addEventListener("keydown", function (event) {
        var step =
          event.key === "ArrowRight" ? 1 :
          event.key === "ArrowLeft" ? -1 : 0;
        if (step) {
          event.preventDefault();
          select(tabs[(index + step + tabs.length) % tabs.length], true);
        } else if (event.key === "Home") {
          event.preventDefault();
          select(tabs[0], true);
        } else if (event.key === "End") {
          event.preventDefault();
          select(tabs[tabs.length - 1], true);
        }
      });
    });
  }

  /* ── Sticky header ───────────────────────────────────────────
     The hero starts flush against the top of the window; the header only draws its
     hairline once the page has moved under it. */
  function setupStickyHeader() {
    var header = document.querySelector(".lp-header");
    if (!header) return;
    function sync() { header.classList.toggle("is-stuck", window.scrollY > 8); }
    sync();
    window.addEventListener("scroll", sync, { passive: true });
  }

  /* ── Reveal on scroll ────────────────────────────────────────
     A section fades up the first time it comes into view. Two rules keep the effect from
     ever costing a visitor the content: the `lp-reveal` class is added *here* rather than
     in the HTML — so with JavaScript off, and for any crawler, every section is already at
     full opacity — and anything at or above the fold is revealed in the same pass, so the
     page never opens on a blank screen. A plain scroll listener drives the rest, because
     an IntersectionObserver does not fire in a document that is never rendered (a
     background tab, a prerender, a screenshot bot), which would leave the page invisible
     in exactly the environments that cannot tell us about it. */
  function setupReveal() {
    if (reduceMotion) return;
    var pending = [].slice.call(document.querySelectorAll("[data-reveal]"));
    if (!pending.length) return;

    pending.forEach(function (el) { el.classList.add("lp-reveal"); });

    function flush() {
      var limit = window.innerHeight - Math.min(80, window.innerHeight * 0.08);
      pending = pending.filter(function (el) {
        if (el.getBoundingClientRect().top > limit) return true;
        el.classList.add("is-visible");
        return false;
      });
      return pending.length;
    }

    var queued = false;
    function onScroll() {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(function () {
        queued = false;
        if (!flush()) {
          window.removeEventListener("scroll", onScroll);
          window.removeEventListener("resize", onScroll);
        }
      });
    }

    flush();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
  }

  /* ── Live waiting-room board ─────────────────────────────────
     Calls the next patient every few seconds so the board on the page behaves like the
     board in a clinic: the number changes, the row flashes, the up-next list shifts, and
     the patient's ticket card moves one place closer. Paused while the tab is hidden and
     never started at all under `prefers-reduced-motion`. */
  function setupBoard() {
    var el = {
      clock: document.getElementById("lpClock"),
      row: document.getElementById("lpNowRow"),
      num: document.getElementById("lpNowNum"),
      room: document.getElementById("lpNowRoom"),
      time: document.getElementById("lpNowTime"),
      upNext: document.getElementById("lpUpNext"),
      heroNum: document.getElementById("lpHeroNum"),
      heroRoom: document.getElementById("lpHeroRoom"),
      pos: document.getElementById("lpMyPos"),
      eta: document.getElementById("lpMyEta")
    };
    if (!el.num || !el.upNext) return;

    var ROOMS = ["Room 1", "Room 2", "Room 3", "Pharmacy"];
    var FIRST = 41;          // the board restarts here, so the loop is seamless
    var MY_TICKET = 46;      // the ticket shown on the patient phone
    var current = 42;
    var roomIndex = 1;
    var minutes = 41;

    function ticket(n) { return "#" + String(n).padStart(3, "0"); }
    function twoDigit(n) { return String(n).padStart(2, "0"); }
    function ordinal(n) {
      return n + (n === 1 ? "st" : n === 2 ? "nd" : n === 3 ? "rd" : "th");
    }

    function renderUpNext() {
      el.upNext.textContent = "";
      for (var i = 1; i <= 4; i++) {
        var row = document.createElement("div");
        row.className = "lp-row";
        var no = document.createElement("span");
        no.textContent = ticket(current + i);
        var state = document.createElement("span");
        state.textContent = i === 1 ? "up next" : "waiting";
        row.appendChild(no);
        row.appendChild(state);
        el.upNext.appendChild(row);
      }
    }

    function renderNow() {
      var calledAt = "called 08:" + twoDigit(minutes);
      el.num.textContent = ticket(current);
      el.room.textContent = ROOMS[roomIndex];
      el.time.textContent = calledAt;
      if (el.clock) el.clock.textContent = "08:" + twoDigit(minutes);
      if (el.heroNum) el.heroNum.textContent = ticket(current);
      if (el.heroRoom) el.heroRoom.textContent = ROOMS[roomIndex] + " · " + calledAt;
    }

    /* The patient's card is derived from the board, never set independently: one queue,
       one source of truth — the same rule the real ticket page follows. */
    function renderTicket() {
      if (!el.pos || !el.eta) return;
      var place = Math.max(1, MY_TICKET - current);
      el.pos.textContent = ordinal(place);
      if (place === 1) {
        el.eta.textContent = "you're next";
      } else {
        var low = (place - 1) * 4;
        el.eta.textContent = "~" + low + "–" + (low + 10) + " min";
      }
    }

    function callNext() {
      // The board stops one short of the ticket on the phone, so the card always has a
      // position to show and the loop restarts without the patient ever being called.
      current = current >= MY_TICKET - 1 ? FIRST : current + 1;
      roomIndex = (roomIndex + 1) % ROOMS.length;
      minutes = minutes >= 57 ? 41 : minutes + 2;
      renderNow();
      renderUpNext();
      renderTicket();
      if (!reduceMotion && el.row) {
        el.row.classList.remove("is-flash");
        void el.row.offsetWidth;          // restart the animation
        el.row.classList.add("is-flash");
      }
    }

    renderNow();
    renderUpNext();
    renderTicket();
    if (reduceMotion) return;

    var timer = null;
    function start() { if (timer === null) timer = window.setInterval(callNext, 4200); }
    function stop() { if (timer !== null) { window.clearInterval(timer); timer = null; } }
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stop(); else start();
    });
    if (!document.hidden) start();
  }
})();
