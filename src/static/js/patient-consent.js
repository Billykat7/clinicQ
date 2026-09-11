/**
 * The patient's consent page (Issue 21).
 *
 * Reads and writes through the JSON API, so the wording, the current answers and the version the
 * patient was shown all come from one place on the server:
 *   - GET /api/v1/patients/me/consents           → the questions and the current answers
 *   - PUT /api/v1/patients/me/consents/{purpose}  → one answer, taking effect immediately
 *
 * Every question defaults to "no" on the server, so a failed load shows nothing as agreed rather
 * than guessing. External file, no inline handlers: satisfies script-src 'self'.
 */
(function () {
  "use strict";

  var API = "/api/v1/patients/me/consents";
  var list = document.getElementById("consent-list");
  var form = document.getElementById("consent-form");
  var loading = document.getElementById("consent-loading");
  var message = document.getElementById("consent-msg");
  var version = document.getElementById("consent-version");
  var intro = document.getElementById("consent-intro");

  if (!list || !form) return;

  /** The CSRF token the double-submit check expects on a write (csrf-htmx.js reads the cookie). */
  function csrfToken() {
    return window.BKP && typeof window.BKP.csrfToken === "function" ? window.BKP.csrfToken() : "";
  }

  function say(text, kind) {
    if (!message) return;
    message.textContent = text;
    message.className = "msg " + (kind || "msg-muted");
    message.hidden = !text;
  }

  /** One question: the words, and a switch showing the current answer. */
  function row(answer) {
    var item = document.createElement("li");
    item.className = "consent-item";

    var label = document.createElement("label");
    label.className = "consent-label";
    label.setAttribute("for", "consent-" + answer.purpose);

    var box = document.createElement("input");
    box.type = "checkbox";
    box.id = "consent-" + answer.purpose;
    box.checked = answer.granted === true;
    box.setAttribute("data-purpose", answer.purpose);

    var text = document.createElement("span");
    text.className = "consent-question";
    text.textContent = answer.question;

    label.appendChild(box);
    label.appendChild(text);
    item.appendChild(label);
    return item;
  }

  function render(state) {
    list.textContent = "";
    (state.answers || []).forEach(function (answer) {
      list.appendChild(row(answer));
    });
    if (intro && state.intro) intro.textContent = state.intro;
    if (version && state.wording_version) {
      version.textContent = "Wording version " + state.wording_version;
    }
    if (loading) loading.hidden = true;
    form.hidden = false;
  }

  /** Send one answer; on failure put the switch back, so the page never claims more than it did. */
  function save(box) {
    var purpose = box.getAttribute("data-purpose");
    var granted = box.checked;
    box.disabled = true;
    say("Saving…", "msg-muted");
    fetch(API + "/" + encodeURIComponent(purpose), {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
      body: JSON.stringify({ granted: granted }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then(function (state) {
        render(state);
        say(state.notice || "Saved. This takes effect straight away.", "msg-ok");
      })
      .catch(function () {
        box.checked = !granted;
        say("That did not save. Check your connection and try again.", "msg-error");
      })
      .then(function () {
        box.disabled = false;
      });
  }

  list.addEventListener("change", function (event) {
    var target = event.target;
    if (target && target.matches('input[type="checkbox"][data-purpose]')) save(target);
  });

  fetch(API, { credentials: "same-origin" })
    .then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    })
    .then(render)
    .catch(function () {
      if (loading) loading.hidden = true;
      say("We could not load your choices. Open the page again from your phone.", "msg-error");
    });
})();
