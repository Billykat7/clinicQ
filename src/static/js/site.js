/**
 * BK ClinicQ — public site behaviour.
 *
 * Only the 3x3 apps menu and the footer year. Everything else is plain HTML,
 * so the site works with JavaScript disabled (the menu links live on
 * /features, which is reachable from the footer).
 *
 * External file with no inline handlers, to satisfy `script-src 'self'`.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    setupAppsMenu();
    setupStatusModal();
    setCurrentYear();
  });

  function setupAppsMenu() {
    var button = document.getElementById("appsButton");
    var panel = document.getElementById("appsPanel");
    if (!button || !panel) return;

    function setOpen(open) {
      panel.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
    }

    button.addEventListener("click", function () {
      setOpen(button.getAttribute("aria-expanded") !== "true");
    });

    document.addEventListener("click", function (event) {
      if (panel.hidden) return;
      if (panel.contains(event.target) || button.contains(event.target)) return;
      setOpen(false);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || panel.hidden) return;
      setOpen(false);
      button.focus();
    });

    // Keep focus inside the panel while it is open.
    panel.addEventListener("keydown", function (event) {
      if (event.key !== "Tab") return;
      // Tiles are links, except the signed-in "Sign out" tile which is a <button>.
      var items = panel.querySelectorAll("a, button");
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        button.focus();
        setOpen(false);
      }
    });
  }

  function setCurrentYear() {
    var year = document.getElementById("year");
    if (year) year.textContent = String(new Date().getFullYear());
  }

  /**
   * Footer "Status" popup (operators only). Opening it fetches /health/ready and
   * renders the readiness response as formatted, syntax-highlighted JSON instead of
   * navigating away to the raw endpoint. The link keeps its href as a no-JS fallback.
   */
  function setupStatusModal() {
    var link = document.querySelector("[data-open-status]");
    var backdrop = document.getElementById("status-modal-backdrop");
    if (!link || !backdrop) return;

    var closeBtn = document.getElementById("status-modal-close");
    var summary = document.getElementById("status-summary");
    var pill = document.getElementById("status-pill");
    var codeEl = document.getElementById("status-code");
    var loading = document.getElementById("status-loading");
    var errorEl = document.getElementById("status-error");
    var jsonEl = document.getElementById("status-json");
    var url = link.getAttribute("href") || "/health/ready";
    var lastFocus = null;

    link.addEventListener("click", function (event) {
      event.preventDefault();
      open();
    });
    if (closeBtn) closeBtn.addEventListener("click", close);
    backdrop.addEventListener("mousedown", function (event) {
      if (event.target === backdrop) close();
    });
    backdrop.addEventListener("keydown", onKeydown);

    function open() {
      lastFocus = document.activeElement;
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
      if (closeBtn) closeBtn.focus();
      load();
    }

    function close() {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
      if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    }

    function onKeydown(event) {
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key !== "Tab") return;
      // Keep focus inside the dialog.
      var items = backdrop.querySelectorAll('button, [tabindex="0"]');
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function reset() {
      setHidden(loading, false);
      setHidden(summary, true);
      setHidden(errorEl, true);
      setHidden(jsonEl, true);
    }

    function load() {
      reset();
      fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (res) {
          return res.text().then(function (text) {
            var data = null;
            try {
              data = JSON.parse(text);
            } catch (err) {
              data = null;
            }
            render(res.status, data);
          });
        })
        .catch(function () {
          fail("Could not reach the status endpoint.");
        });
    }

    function render(httpStatus, data) {
      setHidden(loading, true);
      if (data === null || typeof data !== "object") {
        fail("Unexpected response (not JSON).");
        return;
      }
      var overall = typeof data.status === "string" ? data.status : "unknown";
      pill.textContent = overall;
      pill.className = "status-pill status-pill--" + overall;
      codeEl.textContent = "HTTP " + httpStatus;
      setHidden(summary, false);
      jsonEl.innerHTML = highlightJson(data);
      setHidden(jsonEl, false);
    }

    function fail(message) {
      setHidden(loading, true);
      setHidden(summary, true);
      setHidden(jsonEl, true);
      errorEl.textContent = message;
      setHidden(errorEl, false);
    }
  }

  /** Pretty-print a value and wrap JSON tokens in <span> for colouring. */
  function highlightJson(value) {
    var json = JSON.stringify(value, null, 2)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    var token =
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false)\b|\bnull\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g;
    return json.replace(token, function (match) {
      var cls = "tok-num";
      if (/^"/.test(match)) {
        cls = /:$/.test(match) ? "tok-key" : "tok-str";
      } else if (/^(true|false)$/.test(match)) {
        cls = "tok-bool";
      } else if (match === "null") {
        cls = "tok-null";
      }
      return '<span class="' + cls + '">' + match + "</span>";
    });
  }

  function setHidden(el, hidden) {
    if (el) el.hidden = !!hidden;
  }
})();
