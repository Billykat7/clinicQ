/**
 * Notification centre: the shell bell, its unread badge, and the preview dropdown (Issue 113).
 *
 *   GET  /api/v1/auth/me                                  → is there a signed-in session?
 *   GET  /api/v1/notifications/center                     → { unread_total, items[] } (dropdown)
 *   GET  /api/v1/notifications/center/unread-count        → { unread_total } (badge poll)
 *   POST /api/v1/notifications/center/{id}/read|unread    → toggle one item's read-state
 *   POST /api/v1/notifications/center/read-all            → mark every notification read
 *
 * The unread total includes unread messages (Issue 112) and unread domain-event notifications.
 * The bell is present only in the signed-in shell, so every lookup is null-guarded and the whole
 * centre no-ops when there is no session. The dropdown is adaptive (a full-width sheet on small
 * screens, an anchored panel on desktop — CSS only), keyboard-openable and focus-managed like the
 * profile menu. No inline handlers (CSP script-src 'self'); previews are filled with textContent,
 * never innerHTML, so a body is shown verbatim and never interpreted as markup.
 */
(function () {
  "use strict";

  var AUTH = "/api/v1/auth";
  var API = "/api/v1/notifications/center";
  var POLL_MS = 60000; // refresh the badge about once a minute — a lightweight count-only call

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    var bell = document.getElementById("notif-bell");
    var backdrop = document.getElementById("notif-backdrop");
    if (!bell || !backdrop) return; // page without the shell bell

    // Only wire the centre for a signed-in session (same signal the header avatar uses).
    fetch(AUTH + "/me", { credentials: "same-origin" })
      .then(readResponse)
      .then(function (r) { if (r.ok && r.data) setup(bell, backdrop); })
      .catch(function () { /* signed out or offline: leave the bell hidden */ });
  }

  function setup(bell, backdrop) {
    var badge = document.getElementById("notif-badge");
    var list = document.getElementById("notif-list");
    var empty = document.getElementById("notif-empty");
    var readAll = document.getElementById("notif-readall");
    var lastFocus = null;

    bell.hidden = false;

    function open() {
      lastFocus = document.activeElement;
      backdrop.setAttribute("aria-hidden", "false");
      backdrop.classList.add("open");
      bell.setAttribute("aria-expanded", "true");
      document.addEventListener("keydown", onKey);
      loadItems();
    }
    function close() {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
      bell.setAttribute("aria-expanded", "false");
      document.removeEventListener("keydown", onKey);
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    function onKey(e) { if (e.key === "Escape") close(); }

    bell.addEventListener("click", function () {
      if (backdrop.classList.contains("open")) close(); else open();
    });
    backdrop.addEventListener("mousedown", function (e) { if (e.target === backdrop) close(); });
    if (readAll) readAll.addEventListener("click", function () { markAllRead(); });

    // ── Badge ────────────────────────────────────────────────────────────────
    function setBadge(total) {
      var n = Number(total) || 0;
      if (n > 0) {
        badge.hidden = false;
        badge.textContent = n > 99 ? "99+" : String(n);
        bell.setAttribute("aria-label", n + " unread notifications");
      } else {
        badge.hidden = true;
        badge.textContent = "";
        bell.setAttribute("aria-label", "Notifications");
      }
    }
    function refreshBadge() {
      fetch(API + "/unread-count", { credentials: "same-origin" })
        .then(readResponse)
        .then(function (r) { if (r.ok && r.data) setBadge(r.data.unread_total); })
        .catch(function () { /* transient: keep the last known badge */ });
    }

    // ── Dropdown list ──────────────────────────────────────────────────────────
    function loadItems() {
      fetch(API, { credentials: "same-origin" })
        .then(readResponse)
        .then(function (r) {
          if (!r.ok || !r.data) return;
          setBadge(r.data.unread_total);
          render(r.data.items || []);
        })
        .catch(function () { /* leave whatever is already shown */ });
    }

    function render(items) {
      list.innerHTML = "";
      if (empty) empty.hidden = items.length > 0;
      items.forEach(function (item) { list.appendChild(itemEl(item)); });
    }

    function itemEl(item) {
      var li = document.createElement("li");
      li.className = "notif-item" + (item.read ? "" : " notif-item-unread");

      var main = document.createElement(item.link ? "a" : "div");
      main.className = "notif-item-main";
      if (item.link) {
        main.href = item.link;
        // Opening the item marks a notification read first, then navigates.
        main.addEventListener("click", function (e) {
          if (item.source === "notification" && !item.read) {
            e.preventDefault();
            markRead(item.id, function () { window.location.assign(item.link); });
          }
        });
      }
      var title = document.createElement("span");
      title.className = "notif-item-title";
      title.textContent = item.title || "Notification";
      var snip = document.createElement("span");
      snip.className = "notif-item-snippet";
      snip.textContent = item.snippet || "";
      var meta = document.createElement("span");
      meta.className = "notif-item-meta";
      meta.textContent = when(item.created_at);
      main.appendChild(title); main.appendChild(snip); main.appendChild(meta);
      li.appendChild(main);

      // Inline read/unread toggle — messages are read by opening their thread, so only in-app
      // notifications get the toggle here.
      if (item.source === "notification") {
        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "notif-item-toggle";
        toggle.textContent = item.read ? "Mark unread" : "Mark read";
        toggle.setAttribute("aria-label", (item.read ? "Mark unread: " : "Mark read: ") + (item.title || ""));
        toggle.addEventListener("click", function () {
          if (item.read) markUnread(item.id); else markRead(item.id);
        });
        li.appendChild(toggle);
      }
      return li;
    }

    // ── Read-state mutations ────────────────────────────────────────────────────
    function post(path, onOk) {
      fetch(API + path, { method: "POST", headers: window.BKP.writeHeaders(), credentials: "same-origin" })
        .then(readResponse)
        .then(function (r) {
          if (r.ok && r.data) setBadge(r.data.unread_total);
          if (onOk) onOk();
        })
        .catch(function () { /* transient: the next poll reconciles */ });
    }
    function markRead(id, after) { post("/" + encodeURIComponent(id) + "/read", after || loadItems); }
    function markUnread(id) { post("/" + encodeURIComponent(id) + "/unread", loadItems); }
    function markAllRead() { post("/read-all", loadItems); }

    // ── Kick-off ────────────────────────────────────────────────────────────────
    refreshBadge();
    window.setInterval(refreshBadge, POLL_MS);
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function readResponse(res) {
    var ct = res.headers.get("content-type") || "";
    var parse = ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
    return parse.then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
  }

  /** Short, locale-independent timestamp ("YYYY-MM-DD HH:MM"), or "" when absent. */
  function when(iso) { return iso ? String(iso).replace("T", " ").slice(0, 16) : ""; }
})();
