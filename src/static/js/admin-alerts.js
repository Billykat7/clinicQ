/**
 * Alerts console: Inbox / Sent / Drafts / Deleted (Issue #132 follow-up).
 *
 *   GET    /api/v1/alerts?folder=&offset=&limit=  → listing (paginated)
 *   POST   /api/v1/alerts/{id}/read|unread         → toggle read state
 *   DELETE /api/v1/alerts/{id}                     → soft-delete (Deleted tab, author-scoped)
 *   POST   /api/v1/alerts/{id}/restore             → undo that
 *   GET/PATCH/DELETE /api/v1/alerts/drafts, POST .../drafts/{id}/send  → the Drafts tab
 *
 * An alert is a one-way broadcast — the detail view has no reply. Only one of the two shapes below
 * is wired per page load (Issue #122 convention: mutually exclusive panels, real navigation
 * between tabs). No inline handlers (CSP ``script-src 'self'``).
 */
(function () {
  "use strict";

  var A = window.BKPAdmin;
  var API = "/api/v1/alerts";

  document.addEventListener("DOMContentLoaded", function () {
    var marker = document.getElementById("alerts-initial-tab");
    var tab = marker ? marker.getAttribute("data-tab") : "inbox";
    if (tab === "drafts") {
      setupDrafts();
    } else {
      setupList(tab);
    }
  });

  function when(iso) { return iso ? String(iso).replace("T", " ").slice(0, 16) : "—"; }

  function severityBadge(s) {
    var b = document.createElement("span");
    var cls = s === "critical" ? "badge-warn" : s === "warning" ? "badge-warn" : "badge-muted";
    b.className = "badge " + cls; b.textContent = s;
    return b;
  }

  // ── Inbox / Sent / Deleted ──────────────────────────────────────────────────────────────────

  function setupList(folder) {
    var body = document.getElementById("al-body");
    var msg = document.getElementById("al-msg");
    var refresh = document.getElementById("al-refresh");
    var pager = A.createPager({ onChange: function () { load(); } });
    pager.mount(document.getElementById("al-pager"));

    var detail = A.mountDetailSlideover("al-detail", {
      onClose: function () { selectedId = null; A.selectRow(body, null); },
    });
    var aldTitle = document.getElementById("ald-title");
    var aldMeta = document.getElementById("ald-meta");
    var aldBody = document.getElementById("ald-body");
    var aldMark = document.getElementById("ald-mark");
    var aldDelete = document.getElementById("ald-delete");
    var aldRestore = document.getElementById("ald-restore");
    var aldMsg = document.getElementById("ald-msg");

    var selectedId = null;

    function load() {
      var params = new URLSearchParams();
      params.set("folder", folder);
      pager.applyTo(params);
      A.setMsg(msg, "Loading…", "muted");
      A.getJson(API + "?" + params.toString()).then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not load alerts."), "error");
        render(r.data.items || []);
        pager.update((r.data.items || []).length, r.data.total || 0);
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    }

    function render(items) {
      body.innerHTML = "";
      if (!items.length) { A.setMsg(msg, "No alerts.", "muted"); return; }
      A.setMsg(msg, "");
      items.forEach(function (a) {
        var tr = document.createElement("tr");
        tr.appendChild(A.td(a.subject));
        var sv = document.createElement("td"); sv.appendChild(severityBadge(a.severity));
        tr.appendChild(sv);
        tr.appendChild(A.td(when(a.created_at)));
        var rd = document.createElement("td");
        rd.textContent = a.is_read ? "Read" : "Unread";
        if (!a.is_read) { var b = document.createElement("span"); b.className = "badge badge-warn"; b.textContent = "•"; rd.appendChild(b); }
        tr.appendChild(rd);
        tr.addEventListener("click", function () { A.selectRow(body, tr); open(a); });
        body.appendChild(tr);
      });
    }

    function open(a) {
      selectedId = a.id;
      detail.open();
      aldTitle.textContent = a.subject;
      aldMeta.textContent = a.severity + " · " + (a.author_name || "system") + " · " + when(a.created_at);
      aldBody.textContent = a.body; // verbatim, never innerHTML
      A.setMsg(aldMsg, "");
    }

    if (aldMark) aldMark.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(aldMsg, "Marking read…", "muted");
      A.writeJson("POST", API + "/" + encodeURIComponent(selectedId) + "/read", {})
        .then(function (r) {
          if (!r.ok) return A.setMsg(aldMsg, A.errorText(r, "Could not mark read."), "error");
          A.setMsg(aldMsg, "Marked read.", "ok"); load();
        }).catch(function () { A.setMsg(aldMsg, "Network error.", "error"); });
    });

    if (aldDelete) aldDelete.addEventListener("click", function () {
      if (!selectedId) return;
      if (!window.confirm("Delete this alert? It moves to Deleted and can be restored.")) return;
      A.setMsg(aldMsg, "Deleting…", "muted");
      A.writeJson("DELETE", API + "/" + encodeURIComponent(selectedId), undefined)
        .then(function (r) {
          if (!r.ok) return A.setMsg(aldMsg, A.errorText(r, "Could not delete."), "error");
          detail.close(); load();
        }).catch(function () { A.setMsg(aldMsg, "Network error.", "error"); });
    });

    if (aldRestore) aldRestore.addEventListener("click", function () {
      if (!selectedId) return;
      A.setMsg(aldMsg, "Restoring…", "muted");
      A.writeJson("POST", API + "/" + encodeURIComponent(selectedId) + "/restore", {})
        .then(function (r) {
          if (!r.ok) return A.setMsg(aldMsg, A.errorText(r, "Could not restore."), "error");
          detail.close(); load();
        }).catch(function () { A.setMsg(aldMsg, "Network error.", "error"); });
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
    var drdSeverity = document.getElementById("drd-severity");
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
        renderDrafts(r.data || []);
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
      drdTitle.textContent = d.subject || "Draft alert";
      drdAudience.value = d.audience_type || "";
      drdRef.value = d.audience_ref || "";
      drdSeverity.value = d.severity || "";
      drdSubject.value = d.subject || "";
      drdBody.value = d.body || "";
      syncRefField();
      if (drdFullEdit) {
        drdFullEdit.href = "/admin/alerts/drafts/" + encodeURIComponent(d.id);
        drdFullEdit.hidden = false;
      }
      A.setMsg(drdMsg, "");
      detail.open();
    }

    function payload() {
      var p = { subject: drdSubject.value.trim() || null, body: drdBody.value || null };
      p.audience_type = drdAudience.value || null;
      p.audience_ref = drdAudience.value === "property_occupants" ? (drdRef.value.trim() || null) : null;
      p.severity = drdSeverity.value || null;
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
