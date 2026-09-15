/**
 * Delivery by transport on the notifications page (Issue 71): sends, failures and cost per transport.
 *
 *   GET /api/v1/notifications/delivery-stats?hours=1|24|168  (logs READ)
 *
 * One row per transport. A transport whose failure rate crosses the alert threshold is marked "Over the
 * alert rate", the same rule the watch that alerts the team uses. Reloads when the window changes and every
 * minute while the page is open. Read-only; the server re-checks the grant. No inline handlers.
 */
(function () {
  "use strict";

  var panel = document.getElementById("nt-health");
  if (!panel) return;

  var url = panel.getAttribute("data-url");
  var body = document.getElementById("nt-health-body");
  var hours = document.getElementById("nt-health-hours");
  var msg = document.getElementById("nt-health-msg");
  var NAMES = { email: "Email", sms: "SMS", web_push: "Web push", whatsapp: "WhatsApp" };

  function cell(tag, text, className) {
    var node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function percent(rate) {
    return rate === null || rate === undefined ? "–" : Math.round(rate * 1000) / 10 + "%";
  }

  function render(stats) {
    body.textContent = "";
    stats.channels.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.setAttribute("data-channel", row.channel);
      tr.setAttribute("data-alerting", row.alerting ? "true" : "false");
      var name = cell("td", NAMES[row.channel] || row.channel, "nt-health-name");
      if (row.alerting) {
        var badge = cell("span", "Over the alert rate", "badge badge-warn");
        name.appendChild(document.createTextNode(" "));
        name.appendChild(badge);
      }
      tr.appendChild(name);
      [row.sent, row.delivered, row.failed, row.suppressed, row.waiting].forEach(function (value) {
        tr.appendChild(cell("td", String(value), "num"));
      });
      tr.appendChild(cell("td", percent(row.failure_rate), "num"));
      tr.appendChild(cell("td", stats.currency + " " + Number(row.cost).toFixed(2), "num"));
      body.appendChild(tr);
    });
    msg.textContent =
      "Alerts the team at " + percent(stats.alert_rate) + " failed, once a transport has " +
      stats.alert_min_attempts + " attempted messages in the alert window.";
  }

  function load() {
    msg.textContent = "Loading…";
    fetch(url + "?hours=" + encodeURIComponent(hours.value), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      cache: "no-store"
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(render)
      .catch(function () {
        msg.textContent = "Delivery figures could not be loaded. They will be tried again in a minute.";
      });
  }

  hours.addEventListener("change", load);
  load();
  window.setInterval(load, 60000);
})();
