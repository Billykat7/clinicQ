/**
 * Messages "New message" full-page composer (Issue #132 follow-up).
 *
 * Unlike announcements/alerts (one payload, one endpoint), starting a message thread is two
 * steps against the existing messaging API: open (get-or-create) the anchor's thread, then post
 * the first message to it. No inline handlers (CSP ``script-src 'self'``).
 */
(function () {
  "use strict";

  var A = window.BKPAdmin;
  if (!A) return;

  var API = "/api/v1/messaging";

  document.addEventListener("DOMContentLoaded", function () {
    var msg = document.getElementById("mc-msg");
    var anchorType = document.getElementById("mc-anchor-type");
    var anchorId = document.getElementById("mc-anchor-id");
    var subject = document.getElementById("mc-subject");
    var body = document.getElementById("mc-body");
    var sendBtn = document.getElementById("mc-send");
    if (!sendBtn) return;

    sendBtn.addEventListener("click", function () {
      var id = anchorId.value.trim();
      var text = body.value.trim();
      if (!id) return A.setMsg(msg, "Enter the record's id first.", "error");
      if (!text) return A.setMsg(msg, "Write a message first.", "error");

      A.setMsg(msg, "Starting conversation…", "muted");
      A.writeJson("POST", API + "/threads", {
        anchor_type: anchorType.value,
        anchor_id: id,
        subject: subject.value.trim() || null,
      }).then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not open that conversation."), "error");
        var threadId = r.data.id;
        A.writeJson("POST", API + "/threads/" + encodeURIComponent(threadId) + "/messages", { body: text })
          .then(function (r2) {
            if (!r2.ok || !r2.data) return A.setMsg(msg, A.errorText(r2, "Could not send the message."), "error");
            A.setMsg(msg, "Sent.", "ok");
            sendBtn.disabled = true;
          }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    });
  });
})();
