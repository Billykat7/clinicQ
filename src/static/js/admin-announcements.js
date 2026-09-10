/**
 * Announcements console: Inbox / Sent / Drafts / Deleted (Issue #132 follow-up — promoted off the
 * old /admin/messages collapsible section into its own console).
 *
 *   GET    /api/v1/messaging/announcement-threads?folder=&offset=&limit=  → listing (paginated)
 *   GET    /api/v1/messaging/threads/{id}                                 → thread + messages
 *   POST   /api/v1/messaging/threads/{id}/read                            → mark read
 *   DELETE /api/v1/messaging/threads/{id}                                 → soft-delete (Deleted tab)
 *   POST   /api/v1/messaging/threads/{id}/restore                         → undo that
 *   GET/PATCH/DELETE /api/v1/messaging/drafts, POST .../drafts/{id}/send  → the Drafts tab
 *
 * An announcement is a one-way broadcast — the detail view has no reply box, unlike Messages.
 * Only one of the two shapes below is wired per page load (Issue #122 convention: mutually
 * exclusive panels, real navigation between tabs). No inline handlers (CSP ``script-src 'self'``).
 */
(function () {
  "use strict";

  var A = window.BKPAdmin;
  var API = "/api/v1/messaging";

  document.addEventListener("DOMContentLoaded", function () {
    var marker = document.getElementById("announcements-initial-tab");
    var tab = marker ? marker.getAttribute("data-tab") : "inbox";
    if (tab === "drafts") {
      setupDrafts();
    } else {
      setupThreadList(tab);
    }
  });

  function when(iso) { return iso ? String(iso).replace("T", " ").slice(0, 16) : "—"; }

  // ── Inbox / Sent / Deleted ──────────────────────────────────────────────────────────────────

  function setupThreadList(folder) {
    var body = document.getElementById("an-body");
    var msg = document.getElementById("an-msg");
    var refresh = document.getElementById("an-refresh");
    var pager = A.createPager({ onChange: function () { load(); } });
    pager.mount(document.getElementById("an-pager"));

    var detail = A.mountDetailSlideover("an-detail", {
      onClose: function () { selectedId = null; A.selectRow(body, null); },
    });
    var andTitle = document.getElementById("and-title");
    var andParticipants = document.getElementById("and-participants");
    var andMessages = document.getElementById("and-messages");
    var andMark = document.getElementById("and-mark");
    var andDelete = document.getElementById("and-delete");
    var andRestore = document.getElementById("and-restore");
    var andMsg = document.getElementById("and-msg");

    var selectedId = null;

    function load() {
      var params = new URLSearchParams();
      params.set("folder", folder);
      pager.applyTo(params);
      A.setMsg(msg, "Loading…", "muted");
      A.getJson(API + "/announcement-threads?" + params.toString()).then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not load announcements."), "error");
        render(r.data.items || []);
        pager.update((r.data.items || []).length, r.data.total || 0);
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    }

    function render(items) {
      body.innerHTML = "";
      if (!items.length) { A.setMsg(msg, "No announcements.", "muted"); return; }
      A.setMsg(msg, "");
      items.forEach(function (t) {
        var tr = document.createElement("tr");
        tr.appendChild(A.td(t.subject || "(no subject)"));
        tr.appendChild(A.td(when(t.last_message_at || t.created_at)));
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
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not open the announcement."), "error");
        renderDetail(r.data);
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    }

    function renderDetail(t) {
      detail.open();
      andTitle.textContent = t.subject || "(no subject)";
      var names = (t.participants || []).map(function (p) {
        return (p.name || p.user_id) + " (" + p.role + ")";
      });
      andParticipants.textContent = names.length ? "Reached: " + names.join(", ") : "";
      andMessages.innerHTML = "";
      (t.messages || []).forEach(function (m) {
        var wrap = document.createElement("div");
        wrap.className = "thread-msg";
        var bodyEl = document.createElement("div");
        bodyEl.className = "thread-msg-body";
        bodyEl.textContent = m.body; // verbatim, never innerHTML
        wrap.appendChild(bodyEl);
        andMessages.appendChild(wrap);
      });
      A.setMsg(andMsg, "");
    }

    if (andMark) andMark.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(andMsg, "Marking read…", "muted");
      A.writeJson("POST", API + "/threads/" + encodeURIComponent(selectedId) + "/read", {})
        .then(function (r) {
          if (!r.ok) return A.setMsg(andMsg, A.errorText(r, "Could not mark read."), "error");
          A.setMsg(andMsg, "Marked read.", "ok"); load();
        }).catch(function () { A.setMsg(andMsg, "Network error.", "error"); });
    });

    if (andDelete) andDelete.addEventListener("click", function () {
      if (!selectedId) return;
      if (!window.confirm("Delete this announcement? It moves to Deleted and can be restored.")) return;
      A.setMsg(andMsg, "Deleting…", "muted");
      A.writeJson("DELETE", API + "/threads/" + encodeURIComponent(selectedId), undefined)
        .then(function (r) {
          if (!r.ok) return A.setMsg(andMsg, A.errorText(r, "Could not delete."), "error");
          detail.close(); load();
        }).catch(function () { A.setMsg(andMsg, "Network error.", "error"); });
    });

    if (andRestore) andRestore.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(andMsg, "Restoring…", "muted");
      A.writeJson("POST", API + "/threads/" + encodeURIComponent(selectedId) + "/restore", {})
        .then(function (r) {
          if (!r.ok) return A.setMsg(andMsg, A.errorText(r, "Could not restore."), "error");
          detail.close(); load();
        }).catch(function () { A.setMsg(andMsg, "Network error.", "error"); });
    });

    if (refresh) refresh.addEventListener("click", function () { pager.reset(); load(); });
    load();
  }

  // ── Drafts ──────────────────────────────────────────────────────────────────────────────────

  function setupDrafts() {
    var drBody = document.getElementById("dr-body");
    var drMsg = document.getElementById("dr-msg");

    var detail = A.mountDetailSlideover("dr-detail", {
      onClose: function () { selectedId = null; A.selectRow(drBody, null); },
    });
    var drdTitle = document.getElementById("drd-title");
    var drdAudience = document.getElementById("drd-audience");
    var drdRefField = document.getElementById("drd-ref-field");
    var drdRef = document.getElementById("drd-ref");
    var drdSubject = document.getElementById("drd-subject");
    var drdBody = document.getElementById("drd-body");
    var drdSave = document.getElementById("drd-save");
    var drdSend = document.getElementById("drd-send");
    var drdDelete = document.getElementById("drd-delete");
    var drdFullEdit = document.getElementById("drd-full-edit");
    var drdMsg = document.getElementById("drd-msg");

    var selectedId = null;

    function syncRefField() { drdRefField.hidden = drdAudience.value !== "property_occupants"; }
    if (drdAudience) drdAudience.addEventListener("change", syncRefField);

    function audienceLabel(d) {
      var t = String(d.audience_type || "");
      return t ? t.replace(/_/g, " ") : "Not set";
    }

    function loadDrafts() {
      A.getJson(API + "/drafts").then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(drMsg, A.errorText(r, "Could not load drafts."), "error");
        var items = (r.data || []).filter(function (d) { return d.audience_type !== null; });
        renderDrafts(items);
      }).catch(function () { A.setMsg(drMsg, "Network error.", "error"); });
    }

    function renderDrafts(items) {
      drBody.innerHTML = "";
      if (!items.length) { A.setMsg(drMsg, "No drafts.", "muted"); return; }
      A.setMsg(drMsg, "");
      items.forEach(function (d) {
        var tr = document.createElement("tr");
        tr.appendChild(A.td(d.subject || "(no subject)"));
        tr.appendChild(A.td(audienceLabel(d)));
        tr.appendChild(A.td(when(d.modified_at)));
        tr.addEventListener("click", function () { A.selectRow(drBody, tr); openDraft(d); });
        drBody.appendChild(tr);
      });
    }

    function openDraft(d) {
      selectedId = d.id;
      drdTitle.textContent = d.subject || "Draft announcement";
      drdAudience.value = d.audience_type || "";
      drdRef.value = d.audience_ref || "";
      drdSubject.value = d.subject || "";
      drdBody.value = d.body || "";
      syncRefField();
      if (drdFullEdit) {
        drdFullEdit.href = "/admin/announcements/drafts/" + encodeURIComponent(d.id);
        drdFullEdit.hidden = false;
      }
      A.setMsg(drdMsg, "");
      detail.open();
    }

    function payload() {
      var p = { subject: drdSubject.value.trim() || null, body: drdBody.value || null };
      p.audience_type = drdAudience.value || null;
      p.audience_ref = drdAudience.value === "property_occupants" ? (drdRef.value.trim() || null) : null;
      return p;
    }

    if (drdSave) drdSave.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(drdMsg, "Saving…", "muted");
      A.writeJson("PATCH", API + "/drafts/" + encodeURIComponent(selectedId), payload())
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
