/**
 * Shared full-page composer — the announcement and alert "New …" pages (Issue #132 follow-up).
 *
 * The page shell carries which kind it is (``#compose-page[data-kind]``); this file wires the
 * audience/property-ref toggle, Send (posts immediately) and Save as draft, against whichever API
 * that kind uses. Ids are the generic ``cc-*`` scheme both pages share; ``cc-severity`` is present
 * only on the alert composer (this file no-ops the field-specific bits it can't find). No inline
 * handlers (CSP ``script-src 'self'``).
 */
(function () {
  "use strict";

  var A = window.BKPAdmin;
  if (!A) return;

  var API_BY_KIND = {
    announcement: "/api/v1/messaging",
    alert: "/api/v1/alerts",
  };
  var SEND_PATH_BY_KIND = {
    announcement: "/announcements",
    alert: "",
  };

  document.addEventListener("DOMContentLoaded", function () {
    var marker = document.getElementById("compose-page");
    if (!marker) return;
    var kind = marker.getAttribute("data-kind");
    var api = API_BY_KIND[kind];
    if (!api) return;

    var msg = document.getElementById("cc-msg");
    var audience = document.getElementById("cc-audience");
    var refField = document.getElementById("cc-ref-field");
    var ref = document.getElementById("cc-ref");
    var subject = document.getElementById("cc-subject");
    var body = document.getElementById("cc-body");
    var severity = document.getElementById("cc-severity");
    var sendBtn = document.getElementById("cc-send");
    var saveBtn = document.getElementById("cc-save");

    function syncRefField() {
      if (!refField) return;
      refField.hidden = audience.value !== "property_occupants";
    }
    audience.addEventListener("change", syncRefField);
    syncRefField();

    function sendPayload() {
      var p = {
        audience_type: audience.value,
        subject: subject.value.trim() || null,
        body: body.value.trim(),
      };
      if (audience.value === "property_occupants") p.audience_ref = ref.value.trim() || null;
      if (severity) p.severity = severity.value;
      return p;
    }

    function draftPayload() {
      var p = {
        audience_type: audience.value || null,
        subject: subject.value.trim() || null,
        body: body.value.trim() || null,
      };
      if (audience.value === "property_occupants") p.audience_ref = ref.value.trim() || null;
      if (severity) p.severity = severity.value || null;
      return p;
    }

    sendBtn.addEventListener("click", function () {
      var p = sendPayload();
      if (!p.body) return A.setMsg(msg, "Write a message first.", "error");
      A.setMsg(msg, "Sending…", "muted");
      A.writeJson("POST", api + SEND_PATH_BY_KIND[kind], p).then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not send."), "error");
        A.setMsg(msg, "Sent.", "ok");
        sendBtn.disabled = true; saveBtn.disabled = true;
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    });

    saveBtn.addEventListener("click", function () {
      A.setMsg(msg, "Saving…", "muted");
      A.writeJson("POST", api + "/drafts", draftPayload()).then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not save the draft."), "error");
        A.setMsg(msg, "Saved as draft.", "ok");
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    });
  });
})();
