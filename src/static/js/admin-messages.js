/**
 * Staff messaging console: Inbox / Sent / Deleted / Drafts (Issue 87; Issue #132 follow-up split
 * Announcements out to its own console and added the Sent/Deleted/Drafts sub-tabs).
 *
 *   GET    /api/v1/messaging/managed-threads?folder=&offset=&limit=  → thread listing (paginated)
 *   GET    /api/v1/messaging/threads/{id}                            → thread + participants + messages
 *   POST   /api/v1/messaging/threads/{id}/messages                   → reply
 *   POST   /api/v1/messaging/threads/{id}/read                       → mark read
 *   DELETE /api/v1/messaging/threads/{id}                            → soft-delete (Deleted tab)
 *   POST   /api/v1/messaging/threads/{id}/restore                    → undo that
 *   GET/PATCH/DELETE /api/v1/messaging/drafts, POST .../drafts/{id}/send  → the Drafts tab
 *
 * Only one of the two shapes below is ever wired per page load — the sub-tab a request lands on
 * decides which markup shipped (Issue #122 convention: mutually exclusive panels, real navigation
 * between tabs, no client-side panel switch). Message bodies are inserted with textContent, never
 * innerHTML, so a body is shown verbatim and never interpreted as markup. No inline handlers (CSP
 * ``script-src 'self'``).
 */
(function () {
  "use strict";

  var A = window.BKPAdmin;
  var API = "/api/v1/messaging";

  document.addEventListener("DOMContentLoaded", function () {
    var marker = document.getElementById("messages-initial-tab");
    var tab = marker ? marker.getAttribute("data-tab") : "inbox";
    if (tab === "drafts") {
      setupDrafts();
    } else {
      setupThreadList(tab);
    }
  });

  function conversationLabel(t) {
    var anchor = String(t.anchor_type || "").replace("_", " ");
    return t.subject || (anchor.charAt(0).toUpperCase() + anchor.slice(1) + " conversation");
  }
  function when(iso) { return iso ? String(iso).replace("T", " ").slice(0, 16) : "—"; }

  // ── Inbox / Sent / Deleted — one thread-list + reply-slideover shape, folder-scoped ───────────

  function setupThreadList(folder) {
    var body = document.getElementById("mg-body");
    var msg = document.getElementById("mg-msg");
    var refresh = document.getElementById("mg-refresh");
    var unreadTotal = document.getElementById("mg-unread-total");
    var pager = A.createPager({ onChange: function () { load(); } });
    pager.mount(document.getElementById("mg-pager"));

    function loadUnreadTotal() {
      if (!unreadTotal) return;
      A.getJson(API + "/managed-unread-count").then(function (r) {
        if (!r.ok || !r.data) return;
        var n = r.data.unread_total || 0;
        unreadTotal.textContent = n === 1 ? "1 unread" : n + " unread";
        unreadTotal.hidden = false;
      });
    }

    var detail = A.mountDetailSlideover("mg-detail", {
      onClose: function () { selectedId = null; A.selectRow(body, null); },
    });
    var mdTitle = document.getElementById("md-title");
    var mdSub = document.getElementById("md-sub");
    var mdParticipants = document.getElementById("md-participants");
    var mdMessages = document.getElementById("md-messages");
    var mdMessagesMsg = document.getElementById("md-messages-msg");
    var mdReply = document.getElementById("md-reply");
    var mdSend = document.getElementById("md-send");
    var mdMark = document.getElementById("md-mark");
    var mdDelete = document.getElementById("md-delete");
    var mdRestore = document.getElementById("md-restore");
    var mdMsg = document.getElementById("md-msg");

    var selectedId = null;

    function load() {
      var params = new URLSearchParams();
      params.set("folder", folder);
      pager.applyTo(params);
      A.setMsg(msg, "Loading…", "muted");
      A.getJson(API + "/managed-threads?" + params.toString()).then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not load conversations."), "error");
        render(r.data.items || []);
        pager.update((r.data.items || []).length, r.data.total || 0);
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    }

    function render(items) {
      body.innerHTML = "";
      if (!items.length) { A.setMsg(msg, "No conversations.", "muted"); return; }
      A.setMsg(msg, "");
      items.forEach(function (t) {
        var tr = document.createElement("tr");
        tr.appendChild(A.td(conversationLabel(t)));
        tr.appendChild(A.td(when(t.last_message_at)));
        var u = document.createElement("td");
        if (t.unread_count) {
          var b = document.createElement("span");
          b.className = "badge badge-warn"; b.textContent = String(t.unread_count);
          u.appendChild(b);
        } else { u.textContent = "—"; }
        tr.appendChild(u);
        tr.addEventListener("click", function () { A.selectRow(body, tr); open(t.id); });
        body.appendChild(tr);
      });
    }

    function open(id) {
      selectedId = id;
      A.getJson(API + "/threads/" + encodeURIComponent(id)).then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not open the conversation."), "error");
        renderDetail(r.data);
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    }

    function renderDetail(t) {
      detail.open();
      mdTitle.textContent = conversationLabel(t);
      mdSub.textContent = String(t.anchor_type).replace("_", " ") + " · " + t.anchor_id;
      var names = (t.participants || []).map(function (p) {
        return (p.name || p.user_id) + " (" + p.role + ")";
      });
      mdParticipants.textContent = names.length ? "Participants: " + names.join(", ") : "";

      mdMessages.innerHTML = "";
      var messages = t.messages || [];
      if (!messages.length) {
        A.setMsg(mdMessagesMsg, "No messages yet.", "muted");
      } else {
        A.setMsg(mdMessagesMsg, "");
        messages.forEach(function (m) { mdMessages.appendChild(messageEl(m)); });
      }
      if (mdReply) mdReply.value = "";
      A.setMsg(mdMsg, "");
    }

    function messageEl(m) {
      var wrap = document.createElement("div");
      wrap.className = "thread-msg";
      var head = document.createElement("div");
      head.className = "thread-msg-head";
      var who = document.createElement("span");
      who.className = "thread-msg-who";
      who.textContent = m.author_name || "Unknown";
      var at = document.createElement("span");
      at.className = "thread-msg-at";
      at.textContent = when(m.created_at);
      head.appendChild(who); head.appendChild(at);
      var bodyEl = document.createElement("div");
      bodyEl.className = "thread-msg-body";
      bodyEl.textContent = m.body; // verbatim, never innerHTML
      wrap.appendChild(head); wrap.appendChild(bodyEl);
      return wrap;
    }

    if (mdSend) mdSend.addEventListener("click", function () {
      if (!selectedId) return;
      var text = mdReply.value.trim();
      if (!text) return A.setMsg(mdMsg, "Write a reply first.", "error");
      A.setMsg(mdMsg, "Sending…", "muted");
      A.writeJson("POST", API + "/threads/" + encodeURIComponent(selectedId) + "/messages", { body: text })
        .then(function (r) {
          if (!r.ok || !r.data) return A.setMsg(mdMsg, A.errorText(r, "Could not send the reply."), "error");
          A.setMsg(mdMsg, "Sent.", "ok"); open(selectedId); load(); loadUnreadTotal();
        }).catch(function () { A.setMsg(mdMsg, "Network error.", "error"); });
    });

    if (mdMark) mdMark.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(mdMsg, "Marking read…", "muted");
      A.writeJson("POST", API + "/threads/" + encodeURIComponent(selectedId) + "/read", {})
        .then(function (r) {
          if (!r.ok) return A.setMsg(mdMsg, A.errorText(r, "Could not mark read."), "error");
          A.setMsg(mdMsg, "Marked read.", "ok"); load(); loadUnreadTotal();
        }).catch(function () { A.setMsg(mdMsg, "Network error.", "error"); });
    });

    if (mdDelete) mdDelete.addEventListener("click", function () {
      if (!selectedId) return;
      if (!window.confirm("Delete this conversation? It moves to Deleted and can be restored.")) return;
      A.setMsg(mdMsg, "Deleting…", "muted");
      A.writeJson("DELETE", API + "/threads/" + encodeURIComponent(selectedId), undefined)
        .then(function (r) {
          if (!r.ok) return A.setMsg(mdMsg, A.errorText(r, "Could not delete."), "error");
          detail.close(); load(); loadUnreadTotal();
        }).catch(function () { A.setMsg(mdMsg, "Network error.", "error"); });
    });

    if (mdRestore) mdRestore.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(mdMsg, "Restoring…", "muted");
      A.writeJson("POST", API + "/threads/" + encodeURIComponent(selectedId) + "/restore", {})
        .then(function (r) {
          if (!r.ok) return A.setMsg(mdMsg, A.errorText(r, "Could not restore."), "error");
          detail.close(); load();
        }).catch(function () { A.setMsg(mdMsg, "Network error.", "error"); });
    });

    if (refresh) refresh.addEventListener("click", function () { pager.reset(); load(); loadUnreadTotal(); });
    load();
    loadUnreadTotal();
  }

  // ── Drafts — a private compose list; quick-edit slider + a "Full edit" new-tab link ───────────

  function setupDrafts() {
    var drBody = document.getElementById("dr-body");
    var drMsg = document.getElementById("dr-msg");

    var detail = A.mountDetailSlideover("dr-detail", {
      onClose: function () { selectedId = null; A.selectRow(drBody, null); },
    });
    var drdTitle = document.getElementById("drd-title");
    var drdBody = document.getElementById("drd-body");
    var drdSave = document.getElementById("drd-save");
    var drdSend = document.getElementById("drd-send");
    var drdDelete = document.getElementById("drd-delete");
    var drdFullEdit = document.getElementById("drd-full-edit");
    var drdMsg = document.getElementById("drd-msg");

    var selectedId = null;

    function loadDrafts() {
      A.getJson(API + "/drafts").then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(drMsg, A.errorText(r, "Could not load drafts."), "error");
        // This page only lists reply-in-progress drafts; announcement drafts live on /admin/announcements.
        var items = (r.data || []).filter(function (d) { return d.thread_id !== null; });
        renderDrafts(items);
      }).catch(function () { A.setMsg(drMsg, "Network error.", "error"); });
    }

    function renderDrafts(items) {
      drBody.innerHTML = "";
      if (!items.length) { A.setMsg(drMsg, "No drafts.", "muted"); return; }
      A.setMsg(drMsg, "");
      items.forEach(function (d) {
        var tr = document.createElement("tr");
        tr.appendChild(A.td(d.subject || "Draft reply"));
        tr.appendChild(A.td(when(d.modified_at)));
        tr.appendChild(A.td(""));
        tr.addEventListener("click", function () { A.selectRow(drBody, tr); openDraft(d); });
        drBody.appendChild(tr);
      });
    }

    function openDraft(d) {
      selectedId = d.id;
      drdTitle.textContent = d.subject || "Draft reply";
      drdBody.value = d.body || "";
      if (drdFullEdit) {
        drdFullEdit.href = "/admin/messages/drafts/" + encodeURIComponent(d.id);
        drdFullEdit.hidden = false;
      }
      A.setMsg(drdMsg, "");
      detail.open();
    }

    if (drdSave) drdSave.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(drdMsg, "Saving…", "muted");
      A.writeJson("PATCH", API + "/drafts/" + encodeURIComponent(selectedId), { body: drdBody.value })
        .then(function (r) {
          if (!r.ok) return A.setMsg(drdMsg, A.errorText(r, "Could not save."), "error");
          A.setMsg(drdMsg, "Saved.", "ok"); loadDrafts();
        }).catch(function () { A.setMsg(drdMsg, "Network error.", "error"); });
    });

    if (drdSend) drdSend.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(drdMsg, "Sending…", "muted");
      A.writeJson("POST", API + "/drafts/" + encodeURIComponent(selectedId) + "/send", {})
        .then(function (r) {
          if (!r.ok) return A.setMsg(drdMsg, A.errorText(r, "Could not send."), "error");
          detail.close(); loadDrafts();
        }).catch(function () { A.setMsg(drdMsg, "Network error.", "error"); });
    });

    if (drdDelete) drdDelete.addEventListener("click", function () {
      if (!selectedId) return;
      if (!window.confirm("Discard this draft?")) return;
      A.writeJson("DELETE", API + "/drafts/" + encodeURIComponent(selectedId), undefined)
        .then(function (r) {
          if (!r.ok) return A.setMsg(drdMsg, A.errorText(r, "Could not delete."), "error");
          detail.close(); loadDrafts();
        }).catch(function () { A.setMsg(drdMsg, "Network error.", "error"); });
    });

    loadDrafts();
  }
})();
