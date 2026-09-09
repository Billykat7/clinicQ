/**
 * Notification preferences page (Issue #72).
 *
 * Loads and saves the signed-in user's preferences through the JSON API:
 *   - GET /api/v1/notifications/preferences  → categories + quiet hours + timezone
 *   - PUT /api/v1/notifications/preferences  → save channel choices / quiet hours / timezone
 *
 * Essential categories render their channel picker without an "Off" option, so the UI itself
 * cannot ask to disable mail the server would refuse to disable anyway.
 */
(function () {
  "use strict";

  var API = "/api/v1/notifications/preferences";

  var CATEGORY_LABELS = {
    account: "Account & security",
    financial: "Financial",
    applications: "Applications",
    maintenance: "Maintenance",
    messages: "Messages",
    marketing: "Marketing",
  };
  var CHANNEL_LABELS = { email: "Email", sms: "SMS", off: "Off" };

  var loading = document.getElementById("prefs-loading");
  var form = document.getElementById("prefs-form");
  var tbody = document.getElementById("prefs-categories");
  var msg = document.getElementById("prefs-msg");
  var qhStart = document.getElementById("qh-start");
  var qhEnd = document.getElementById("qh-end");
  var qhTz = document.getElementById("qh-tz");

  if (!form || !tbody) return;

  function readResponse(res) {
    var ct = res.headers.get("content-type") || "";
    var body = ct.indexOf("application/json") !== -1 ? res.json() : res.text();
    return body.then(function (data) {
      return { ok: res.ok, status: res.status, data: data };
    });
  }

  /** Build the channel <select> for one category (Off omitted for essential categories). */
  function channelSelect(category, channel, essential) {
    var select = document.createElement("select");
    select.className = "channel-select";
    select.setAttribute("data-category", category);
    var options = essential ? ["email", "sms"] : ["email", "sms", "off"];
    options.forEach(function (value) {
      var opt = document.createElement("option");
      opt.value = value;
      opt.textContent = CHANNEL_LABELS[value];
      if (value === channel) opt.selected = true;
      select.appendChild(opt);
    });
    return select;
  }

  function renderCategories(categories) {
    tbody.innerHTML = "";
    categories.forEach(function (cat) {
      var tr = document.createElement("tr");
      var name = document.createElement("td");
      name.textContent = CATEGORY_LABELS[cat.category] || cat.category;
      if (cat.essential) {
        var badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = "Always on";
        badge.style.marginLeft = "8px";
        name.appendChild(badge);
      }
      var picker = document.createElement("td");
      picker.appendChild(channelSelect(cat.category, cat.channel, cat.essential));
      tr.appendChild(name);
      tr.appendChild(picker);
      tbody.appendChild(tr);
    });
  }

  function render(prefs) {
    renderCategories(prefs.categories || []);
    qhStart.value = prefs.quiet_hours_start ? prefs.quiet_hours_start.slice(0, 5) : "";
    qhEnd.value = prefs.quiet_hours_end ? prefs.quiet_hours_end.slice(0, 5) : "";
    qhTz.value = prefs.timezone || "";
    loading.hidden = true;
    form.hidden = false;
  }

  function load() {
    fetch(API, { credentials: "same-origin" })
      .then(readResponse)
      .then(function (r) {
        if (!r.ok) throw new Error("load failed");
        render(r.data);
      })
      .catch(function () {
        loading.textContent = "Could not load your preferences. Reload to try again.";
      });
  }

  function collect() {
    var channels = {};
    var selects = tbody.querySelectorAll("select[data-category]");
    for (var i = 0; i < selects.length; i++) {
      channels[selects[i].getAttribute("data-category")] = selects[i].value;
    }
    var body = { channels: channels, timezone: qhTz.value.trim() || null };
    // Quiet hours are a pair: both set applies them, both empty clears them.
    if (qhStart.value && qhEnd.value) {
      body.quiet_hours_start = qhStart.value;
      body.quiet_hours_end = qhEnd.value;
    } else {
      body.clear_quiet_hours = true;
    }
    return body;
  }

  form.addEventListener("submit", function (evt) {
    evt.preventDefault();
    msg.textContent = "Saving…";
    msg.className = "msg msg-muted";
    fetch(API, {
      method: "PUT",
      credentials: "same-origin",
      headers: window.BKP.writeHeaders(),
      body: JSON.stringify(collect()),
    })
      .then(readResponse)
      .then(function (r) {
        if (!r.ok) {
          var detail = r.data && r.data.detail ? r.data.detail : "Could not save.";
          msg.textContent = typeof detail === "string" ? detail : "Could not save.";
          msg.className = "msg msg-error";
          return;
        }
        render(r.data);
        msg.textContent = "Preferences saved.";
        msg.className = "msg msg-ok";
      })
      .catch(function () {
        msg.textContent = "Could not save. Try again.";
        msg.className = "msg msg-error";
      });
  });

  load();
})();
