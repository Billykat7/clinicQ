/**
 * Notification delivery viewer: recipient, channel, type and delivery status (Issue 87).
 *
 *   GET /api/v1/notifications?status=&channel=&recipient=&offset=&limit=  → list (logs READ)
 *
 * The list endpoint returns the full delivery projection, so the detail panel is built from the
 * selected row (no per-row refetch). Read-only. The server re-checks the ``logs`` RBAC verb on
 * every call — this is UI only. No inline handlers (CSP ``script-src 'self'``).
 */
(function () {
  "use strict";

  var A = window.BKPAdmin;
  var API = "/api/v1/notifications";

  document.addEventListener("DOMContentLoaded", function () {
    var recipient = document.getElementById("nt-recipient");
    var statusSel = document.getElementById("nt-status");
    var channelSel = document.getElementById("nt-channel");
    var body = document.getElementById("nt-body");
    var msg = document.getElementById("nt-msg");
    var pager = A.createPager({ onChange: function () { load(); } });
    pager.mount(document.getElementById("nt-pager"));

    // A slide-in over the full-width list (Issue #132), not a second column beside it.
    var detail = A.mountDetailSlideover("nt-detail", {
      onClose: function () { A.selectRow(body, null); },
    });
    var f = {
      id: document.getElementById("nd-id"),
      recipient: document.getElementById("nd-recipient"),
      channel: document.getElementById("nd-channel"),
      template: document.getElementById("nd-template"),
      subject: document.getElementById("nd-subject"),
      status: document.getElementById("nd-status"),
      provider: document.getElementById("nd-provider"),
      attempts: document.getElementById("nd-attempts"),
      created: document.getElementById("nd-created"),
      sent: document.getElementById("nd-sent"),
      delivered: document.getElementById("nd-delivered"),
      failed: document.getElementById("nd-failed"),
      next: document.getElementById("nd-next"),
      error: document.getElementById("nd-error"),
    };

    function when(iso) { return iso ? String(iso).replace("T", " ").slice(0, 19) : "—"; }

    function statusBadge(s) {
      var b = document.createElement("span");
      var cls = s === "delivered" ? "badge-ok"
        : (s === "failed" || s === "dead") ? "badge-warn"
        : s === "sent" ? "badge-current" : "badge-muted";
      b.className = "badge " + cls; b.textContent = s;
      return b;
    }

    function load() {
      var params = new URLSearchParams();
      if (recipient.value.trim()) params.set("recipient", recipient.value.trim());
      if (statusSel.value) params.set("status", statusSel.value);
      if (channelSel.value) params.set("channel", channelSel.value);
      pager.applyTo(params);
      A.setMsg(msg, "Loading…", "muted");
      A.getJson(API + "?" + params.toString()).then(function (r) {
        if (!r.ok || !r.data) return A.setMsg(msg, A.errorText(r, "Could not load notifications."), "error");
        render(r.data.items || []);
        pager.update((r.data.items || []).length, r.data.total || 0);
      }).catch(function () { A.setMsg(msg, "Network error.", "error"); });
    }
    // A new filter restarts paging from the first page.
    function reload() { pager.reset(); load(); }

    function render(items) {
      body.innerHTML = "";
      if (!items.length) { A.setMsg(msg, "No notifications match.", "muted"); return; }
      A.setMsg(msg, "");
      items.forEach(function (n) {
        var tr = document.createElement("tr");
        tr.appendChild(A.td(n.recipient));
        tr.appendChild(A.td(n.channel));
        tr.appendChild(A.td(n.template_key));
        var st = document.createElement("td"); st.appendChild(statusBadge(n.status)); tr.appendChild(st);
        tr.addEventListener("click", function () { A.selectRow(body, tr); select(n); });
        body.appendChild(tr);
      });
    }

    function select(n) {
      detail.open();
      f.id.textContent = "ID " + n.id;
      f.recipient.textContent = n.recipient || "—";
      f.channel.textContent = n.channel || "—";
      f.template.textContent = n.template_key || "—";
      f.subject.textContent = n.subject || "—";
      f.status.textContent = n.status || "—";
      f.provider.textContent = n.provider || "—";
      f.attempts.textContent = n.attempts + " / " + n.max_attempts;
      f.created.textContent = when(n.created_at);
      f.sent.textContent = when(n.sent_at);
      f.delivered.textContent = when(n.delivered_at);
      f.failed.textContent = when(n.failed_at);
      f.next.textContent = when(n.next_attempt_at);
      f.error.textContent = n.last_error || "No failures recorded.";
    }

    recipient.addEventListener("input", A.debounce(reload, 250));
    statusSel.addEventListener("change", reload);
    channelSel.addEventListener("change", reload);
    A.mountFilterBar(document.querySelector(".filter-bar"), { onClear: reload });
    load();
  });
})();
