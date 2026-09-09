/**
 * Theme toggle (Issue 97).
 *
 * Light/dark follow the OS by default; this lets the user override and remembers the choice.
 * The override is applied as ``data-theme`` on <html> as soon as this file runs — it is loaded
 * in <head> without ``defer``, so it executes before the body paints and there is no flash of
 * the wrong theme. The visible toggle is wired on DOMContentLoaded.
 *
 * External file only — CSP is ``script-src 'self'`` (no inline scripts).
 */
(function () {
  "use strict";

  var KEY = "bkp-theme";
  var root = document.documentElement;

  function stored() {
    try {
      var v = localStorage.getItem(KEY);
      return v === "dark" || v === "light" ? v : null;
    } catch (e) { return null; }
  }

  // Apply the remembered choice before first paint.
  var saved = stored();
  if (saved) root.setAttribute("data-theme", saved);

  function prefersDark() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }
  function effectiveTheme() {
    var forced = root.getAttribute("data-theme");
    if (forced === "dark" || forced === "light") return forced;
    return prefersDark() ? "dark" : "light";
  }

  function syncButton(btn) {
    var isDark = effectiveTheme() === "dark";
    var label = isDark ? "Switch to light theme" : "Switch to dark theme";
    btn.setAttribute("aria-label", label);
    btn.setAttribute("title", label);
    btn.setAttribute("aria-pressed", String(isDark));
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    syncButton(btn);
    btn.addEventListener("click", function () {
      var next = effectiveTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* storage blocked */ }
      syncButton(btn);
    });
    // With no explicit override, keep the toggle label in step with OS changes.
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
        if (!stored()) syncButton(btn);
      });
    }
  });
})();
