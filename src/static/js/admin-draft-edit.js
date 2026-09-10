/**
 * Full-page draft editor — shared by the message/announcement/alert "Full edit" pages (Issue #132
 * follow-up). Each Drafts quick-edit slider's "Full edit" link opens one of these in a new tab.
 *
 * The page shell carries which kind it is (``#draft-edit-page[data-kind]``) and the draft id; this
 * file fetches the draft, fills in whichever fields exist for that kind (a message draft is body
 * only; announcement/alert drafts add subject/audience/severity), and wires Save (PATCH) / Send
 * (POST .../send) / Delete. No inline handlers (CSP ``script-src 'self'``).
 */
(function () {
  "use strict";

  var A = window.BKPAdmin;
  if (!A) return;

  var API_BY_KIND = {
    message: "/api/v1/messaging",
    announcement: "/api/v1/messaging",
    alert: "/api/v1/alerts",
  };

  document.addEventListener("DOMContentLoaded", function () {
    var marker = document.getElementById("draft-edit-page");
    if (!marker) return;
    var kind = marker.getAttribute("data-kind");
    var draftId = marker.getAttribute("data-draft-id");
    var api = API_BY_KIND[kind];
    if (!api) return;

    var msg = document.getElementById("de-msg");
    var bodyEl = document.getElementById("de-body");
    var subjectEl = document.getElementById("de-subject");
    var audienceEl = document.getElementById("de-audience");
    var refField = document.getElementById("de-ref-field");
    var refEl = document.getElementById("de-ref");
    var severityEl = document.getElementById("de-severity");
    var saveBtn = document.getElementById("de-save");
    var sendBtn = document.getElementById("de-send");
    var deleteBtn = document.getElementById("de-delete");

    function syncRefField() {
      if (!refField || !audienceEl) return;
      refField.hidden = audienceEl.value !== "property_occupants";
    }
    if (audienceEl) audienceEl.addEventListener("change", syncRefField);

    A.getJson(api + "/drafts/" + encodeURIComponent(draftId)).then(function (r) {
      if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not load the draft."), "error");
      var d = r.data;
      if (bodyEl) bodyEl.value = d.body || "";
      if (subjectEl) subjectEl.value = d.subject || "";
      if (audienceEl) audienceEl.value = d.audience_type || "";
      if (refEl) refEl.value = d.audience_ref || "";
      if (severityEl) severityEl.value = d.severity || "";
      syncRefField();
    }).catch(function () { A.setMsg(msg, "Network error.", "error"); });

    function payload() {
      var p = { body: bodyEl ? bodyEl.value : undefined };
      if (subjectEl) p.subject = subjectEl.value.trim() || null;
      if (audienceEl) p.audience_type = audienceEl.value || null;
      if (refEl) p.audience_ref = (audienceEl && audienceEl.value === "property_occupants") ? (refEl.value.trim() || null) : null;
      if (severityEl) p.severity = severityEl.value || null;
      return p;
    }

    if (saveBtn) saveBtn.addEventListener("click", function () {
      A.setMsg(msg, "Saving…", "muted");
      A.writeJson("PATCH", api + "/drafts/" + encodeURIComponent(draftId), payload()).then(function (r) {
        if (!r.ok) return A.setMsg(msg, A.errorText(r, "Could not save."), "error");
        A.setMsg(msg, "Saved.", "ok");
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    });

    if (sendBtn) sendBtn.addEventListener("click", function () {
      A.setMsg(msg, "Saving before send…", "muted");
      A.writeJson("PATCH", api + "/drafts/" + encodeURIComponent(draftId), payload()).then(function (r) {
        if (!r.ok) return A.setMsg(msg, A.errorText(r, "Could not save."), "error");
        A.setMsg(msg, "Sending…", "muted");
        return A.writeJson("POST", api + "/drafts/" + encodeURIComponent(draftId) + "/send", {});
      }).then(function (r) {
        if (!r) return;
        if (!r.ok) return A.setMsg(msg, A.errorText(r, "Could not send."), "error");
        A.setMsg(msg, "Sent.", "ok");
        if (saveBtn) saveBtn.disabled = true;
        if (sendBtn) sendBtn.disabled = true;
        if (deleteBtn) deleteBtn.disabled = true;
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    });

    if (deleteBtn) deleteBtn.addEventListener("click", function () {
      if (!window.confirm("Discard this draft?")) return;
      A.writeJson("DELETE", api + "/drafts/" + encodeURIComponent(draftId), undefined).then(function (r) {
        if (!r.ok) return A.setMsg(msg, A.errorText(r, "Could not delete."), "error");
        A.setMsg(msg, "Discarded.", "ok");
        if (saveBtn) saveBtn.disabled = true;
        if (sendBtn) sendBtn.disabled = true;
        deleteBtn.disabled = true;
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    });
  });
})();
