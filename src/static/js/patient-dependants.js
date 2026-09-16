/**
 * Who this visit is for: the patient, or somebody they act for (Issue 84).
 *
 * One phone often serves a household. This control sits above the join and booking forms and answers one
 * question — *who is this for?* — plus the flow that adds somebody:
 *   - GET    /api/v1/patients/me/dependants        → the people this phone may act for
 *   - POST   /api/v1/patients/me/dependants/code   → a code to their number (the verification step)
 *   - POST   /api/v1/patients/me/dependants        → make the link (with the code, or for somebody with
 *                                                    no phone of their own, such as a small child)
 *   - DELETE /api/v1/patients/me/dependants/{id}   → stop acting for them
 *
 * The page asks `window.BKPWho.selected()` for the id to send; null means the patient themselves.
 *
 * External file, no inline handlers: script-src 'self'.
 *
 *   window.BKPWho.mount(signIn);   // after sign-in
 */
(function () {
  "use strict";

  var BASE = "/api/v1/patients/me/dependants";

  function $(id) {
    return document.getElementById(id);
  }

  var box = $("who-box");
  var picker = $("who-for");
  var adding = $("who-add");
  var message = $("who-message");
  var signIn = null;
  var phoneStep = null;

  function say(text) {
    if (!message) return;
    message.textContent = text || "";
    message.hidden = !text;
  }

  function option(value, label) {
    var node = document.createElement("option");
    node.value = value;
    node.textContent = label;
    return node;
  }

  function load() {
    if (!signIn || !picker) return Promise.resolve();
    return signIn.call("GET", BASE).then(function (answer) {
      if (!answer.ok) return;
      var chosen = picker.value;
      picker.textContent = "";
      picker.appendChild(option("", "Me"));
      (answer.body.items || []).forEach(function (item) {
        picker.appendChild(option(item.patient_id, item.name + " (" + item.relationship + ")"));
      });
      picker.value = chosen && picker.querySelector('option[value="' + chosen + '"]') ? chosen : "";
      if (box) box.hidden = false;
    });
  }

  function openAdd(open) {
    if (!adding) return;
    adding.hidden = !open;
    if (open) $("who-name").focus();
    if (!open) resetAdd();
  }

  function resetAdd() {
    ["who-name", "who-phone", "who-code"].forEach(function (id) {
      var field = $(id);
      if (field) field.value = "";
    });
    if (phoneStep) phoneStep.hidden = true;
    say("");
  }

  function sendCode() {
    var phone = $("who-phone").value.trim();
    if (!phone) return say("Type their phone number, or leave it empty for someone with no phone.");
    say("");
    signIn.call("POST", BASE + "/code", { phone: phone }).then(function (answer) {
      if (!answer.ok) return say(signIn.sentence(answer));
      phoneStep.hidden = false;
      say("We sent a code to that phone. Ask them to read it to you.");
      $("who-code").focus();
    });
  }

  function save() {
    var name = $("who-name").value.trim();
    var phone = $("who-phone").value.trim();
    var code = $("who-code").value.trim();
    var body = { relationship: $("who-relationship").value };
    if (phone) {
      if (!code) return say("Type the code we sent to their phone.");
      body.phone = phone;
      body.code = code;
      if (name) body.name = name;
    } else {
      if (!name) return say("Type their name.");
      body.name = name;
    }
    say("");
    signIn.call("POST", BASE, body).then(function (answer) {
      if (!answer.ok) return say(signIn.sentence(answer));
      var items = answer.body.items || [];
      openAdd(false);
      load().then(function () {
        if (items.length && picker) picker.value = items[items.length - 1].patient_id;
        say("Added. This visit is for them unless you change it above.");
      });
    });
  }

  function remove() {
    if (!picker || !picker.value) return say("Choose the person above first.");
    var chosen = picker.querySelector('option[value="' + picker.value + '"]');
    signIn.call("GET", BASE).then(function (answer) {
      if (!answer.ok) return say(signIn.sentence(answer));
      var match = (answer.body.items || []).filter(function (item) {
        return item.patient_id === picker.value;
      })[0];
      if (!match) return;
      signIn.call("DELETE", BASE + "/" + encodeURIComponent(match.link_id)).then(function (ended) {
        if (!ended.ok) return say(signIn.sentence(ended));
        picker.value = "";
        load();
        say("You no longer act for " + (chosen ? chosen.textContent : "them") + ".");
      });
    });
  }

  window.BKPWho = {
    mount: function (api) {
      signIn = api;
      if (!box || !picker) return;
      phoneStep = $("who-code-step");
      $("who-open").addEventListener("click", function () {
        openAdd(adding.hidden);
      });
      $("who-send-code").addEventListener("click", sendCode);
      $("who-save").addEventListener("click", save);
      $("who-remove").addEventListener("click", remove);
      load();
    },
    selected: function () {
      return picker && picker.value ? picker.value : null;
    },
    refresh: load,
  };
})();
