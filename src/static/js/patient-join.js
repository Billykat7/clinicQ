/**
 * Joining a clinic's queue from the web (Issue 200): discover/join.html, after patient-sign-in.js.
 *
 * Once the patient is signed in:
 *   - GET  /api/v1/patients/me/consents                 → their current notifications answer, preselected
 *   - PUT  /api/v1/patients/me/consents/notifications   → the answer given here
 *   - POST /api/v1/clinics/{site}/queues/{queue}/tickets → 201 a new ticket, 200 the one already held;
 *                                                         either way the page opens its page_url
 * A patient already signed in when the page loaded starts at the queue. A refusal shows the API's own
 * sentence; a session that has ended goes back to the phone step. External file, no inline handlers.
 */
(function () {
  "use strict";

  var root = document.getElementById("join");
  var signIn = window.BKPSignIn;
  if (!root || !signIn) return;

  var site = root.getAttribute("data-site");
  var error = document.getElementById("join-error");
  var consentForm = document.getElementById("join-consent-form");
  var queueForm = document.getElementById("join-queue-form");
  var consentSave = document.getElementById("join-consent-save");
  var submit = document.getElementById("join-submit");
  var messages = document.getElementById("join-messages");
  var steps = {
    consent: root.querySelector('[data-join-step="consent"]'),
    queue: root.querySelector('[data-join-step="queue"]'),
  };
  var TICKET_PAGE = /^\/t\/[A-Za-z0-9_-]{43}$/;

  function say(text) {
    error.textContent = text || "";
    error.hidden = !text;
  }

  function show(name) {
    Object.keys(steps).forEach(function (key) {
      steps[key].hidden = key !== name;
    });
    if (!name) return;
    var heading = steps[name].querySelector("h2");
    if (heading) heading.focus();
  }

  function busy(button, on, label) {
    if (on) button.setAttribute("data-label", button.textContent);
    button.disabled = on;
    button.textContent = on ? label : button.getAttribute("data-label") || button.textContent;
  }

  /** The session ended between steps (signed out elsewhere, or expired): sign in again. */
  function signInAgain() {
    show(null);
    signIn.restart("Your sign-in has ended. Sign in again to join.");
  }

  function choose(name, value) {
    var box = root.querySelector('input[name="' + name + '"][value="' + value + '"]');
    if (box) box.checked = true;
  }

  function checked(form, name) {
    var box = form.querySelector('input[name="' + name + '"]:checked');
    return box ? box.value : "";
  }

  signIn.start(function () {
    say("");
    signIn.call("GET", "/api/v1/patients/me/consents").then(function (answer) {
      var current = ((answer.ok && answer.body.answers) || []).filter(function (item) {
        return item.purpose === "notifications";
      })[0];
      choose("notifications", current && current.granted ? "yes" : "no");
      show("consent");
    });
  });

  consentForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var value = checked(consentForm, "notifications");
    if (!value) {
      say("Choose yes or no.");
      return;
    }
    say("");
    busy(consentSave, true, "Saving…");
    signIn
      .call("PUT", "/api/v1/patients/me/consents/notifications", { granted: value === "yes" })
      .then(function (answer) {
        busy(consentSave, false);
        if (answer.status === 401) return signInAgain();
        if (!answer.ok) return say(signIn.sentence(answer));
        document.getElementById("join-messages-state").textContent =
          value === "yes" ? "We will message you about your turn." : "We will not message you about your turn.";
        messages.hidden = false;
        show("queue");
      });
  });

  queueForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var queue = checked(queueForm, "queue");
    if (!queue) {
      say("Choose a queue.");
      return;
    }
    var reason = document.getElementById("join-reason").value.trim();
    say("");
    busy(submit, true, "Joining…");
    signIn
      .call("POST", "/api/v1/clinics/" + encodeURIComponent(site) + "/queues/" + encodeURIComponent(queue) + "/tickets", {
        reason_text: reason || null,
      })
      .then(function (answer) {
        if (answer.ok && TICKET_PAGE.test(answer.body.page_url || "")) {
          window.location.assign(answer.body.page_url);
          return;
        }
        busy(submit, false);
        if (answer.status === 401) return signInAgain();
        say(signIn.sentence(answer));
      });
  });
})();
