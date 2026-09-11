/**
 * Shared UI feedback primitives — toasts and skeleton loading (Issue #111).
 *
 * The console pages already share empty (``.empty-state``) and inline message (``.msg``) states;
 * this adds the two that were missing a single implementation, so every console and portal gives
 * the same immediate, accessible feedback instead of each rolling its own:
 *
 *   window.BKP.toast(message, { kind, timeout, action })  → a dismissible toast in a live region
 *   window.BKP.skeletonRows(tbody, { rows, cols })        → placeholder rows while a table loads
 *   window.BKP.clearSkeleton(el)                          → remove placeholders before real rows
 *
 * ``kind`` is ``'ok' | 'error' | 'info'`` (default info). An ``action`` ({ label, onClick }) renders
 * an inline button — the seam an *undo* affordance for reversible writes plugs into. Toasts live in
 * one ``aria-live`` region so a screen reader announces them (errors assertively); every toast is
 * keyboard-dismissible and auto-expires. Styling lives in ``components.css`` on the shared tokens.
 *
 * The server can raise a toast without writing any script (Issue 5), three ways:
 *
 *   <template data-toast data-toast-kind="ok">Saved</template>   (the toast() macro) is shown on
 *       load and after every htmx swap, then removed;
 *   an htmx response carrying ``HX-Trigger: {"bkp:toast": {"message", "kind"}}``
 *       (``src.web.components.toast_trigger``) shows one when it lands;
 *   a button with ``data-toast-message`` (and optional ``data-toast-kind``) shows one on click.
 *
 * No inline handlers or styles — external file only (CSP ``script-src 'self'``).
 */
(function () {
  "use strict";

  var ns = (window.BKP = window.BKP || {});

  var CLOSE =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

  function stack() {
    var el = document.querySelector(".toast-stack");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast-stack";
      // One polite live region for the app; an error toast escalates to role=alert below.
      el.setAttribute("aria-live", "polite");
      el.setAttribute("aria-atomic", "false");
      document.body.appendChild(el);
    }
    return el;
  }

  /**
   * Show a toast. Returns a handle with ``dismiss()``.
   * @param {string} message
   * @param {{kind?: string, timeout?: number, action?: {label: string, onClick: Function}}} [opts]
   */
  ns.toast = function toast(message, opts) {
    opts = opts || {};
    var kind = opts.kind || "info";
    var el = document.createElement("div");
    el.className = "toast toast-" + kind + " toast-enter";
    if (kind === "error") el.setAttribute("role", "alert");

    var text = document.createElement("span");
    text.className = "toast-text";
    text.textContent = message;
    el.appendChild(text);

    var timer = null;
    function dismiss() {
      if (timer) { clearTimeout(timer); timer = null; }
      el.classList.add("toast-leave");
      var done = function () { if (el.parentNode) el.parentNode.removeChild(el); };
      // Remove after the leave transition, with a fallback for reduced-motion (no transitionend).
      el.addEventListener("transitionend", done, { once: true });
      setTimeout(done, 220);
    }

    if (opts.action && opts.action.label && typeof opts.action.onClick === "function") {
      var act = document.createElement("button");
      act.type = "button";
      act.className = "toast-action";
      act.textContent = opts.action.label;
      act.addEventListener("click", function () {
        try { opts.action.onClick(); } finally { dismiss(); }
      });
      el.appendChild(act);
    }

    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast-x";
    close.setAttribute("aria-label", "Dismiss");
    close.innerHTML = CLOSE;
    close.addEventListener("click", dismiss);
    el.appendChild(close);

    stack().appendChild(el);
    // Next frame: drop the enter class so the toast transitions in.
    requestAnimationFrame(function () { el.classList.remove("toast-enter"); });

    // An action toast (e.g. undo) lingers longer so it can actually be used.
    var timeout = opts.timeout != null ? opts.timeout : (opts.action ? 8000 : 4000);
    if (timeout > 0) timer = setTimeout(dismiss, timeout);
    return { dismiss: dismiss };
  };

  /** Fill a table body with ``rows`` × ``cols`` shimmering placeholders while data loads. */
  ns.skeletonRows = function skeletonRows(tbody, opts) {
    if (!tbody) return;
    opts = opts || {};
    var rows = opts.rows || 5;
    var cols = opts.cols || (tbody.closest("table")
      ? tbody.closest("table").querySelectorAll("thead th").length || 3
      : 3);
    tbody.innerHTML = "";
    tbody.setAttribute("aria-busy", "true");
    for (var r = 0; r < rows; r += 1) {
      var tr = document.createElement("tr");
      tr.className = "skeleton-row";
      tr.setAttribute("aria-hidden", "true");
      for (var c = 0; c < cols; c += 1) {
        var td = document.createElement("td");
        var line = document.createElement("span");
        line.className = "skeleton-line";
        td.appendChild(line);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
  };

  var KINDS = { info: true, ok: true, error: true };

  /** The toast kind named by ``value``, or ``info`` for anything unknown. */
  function kindOf(value) {
    return KINDS[value] ? value : "info";
  }

  /** Show, then remove, every server-rendered ``<template data-toast>`` under ``root``. */
  function showTemplateToasts(root) {
    var found = (root || document).querySelectorAll("template[data-toast]");
    Array.prototype.forEach.call(found, function (tpl) {
      var message = (tpl.content.textContent || "").trim();
      if (message) ns.toast(message, { kind: kindOf(tpl.getAttribute("data-toast-kind")) });
      tpl.parentNode.removeChild(tpl);
    });
  }

  // The page as rendered (this file is deferred, so the document is already parsed).
  showTemplateToasts(document);
  // Content an htmx swap brought in.
  document.addEventListener("htmx:afterSettle", function (evt) {
    showTemplateToasts(evt.target);
  });
  // A toast the server asked for through the HX-Trigger response header.
  document.addEventListener("bkp:toast", function (evt) {
    var detail = evt.detail || {};
    if (detail.message) ns.toast(String(detail.message), { kind: kindOf(detail.kind) });
  });
  // A button that announces something on click, with no page script of its own.
  document.addEventListener("click", function (evt) {
    var trigger = evt.target.closest ? evt.target.closest("[data-toast-message]") : null;
    if (!trigger) return;
    ns.toast(trigger.getAttribute("data-toast-message"), {
      kind: kindOf(trigger.getAttribute("data-toast-kind")),
    });
  });

  /** Remove skeleton placeholders (and clear ``aria-busy``) before rendering real content. */
  ns.clearSkeleton = function clearSkeleton(el) {
    if (!el) return;
    el.removeAttribute("aria-busy");
    Array.prototype.forEach.call(el.querySelectorAll(".skeleton-row"), function (r) {
      if (r.parentNode) r.parentNode.removeChild(r);
    });
  };
})();
