/**
 * Message settings on the ticket page (Issue 67): how and when this ticket's patient is told, by this link.
 *
 * Loads the patient's preferences from the page's preferences URL when the panel is opened, and saves the
 * whole form in one PUT. The server decides what is allowed and applies it to the next message on every
 * channel; this file only shows the form and the server's answer. External file, no inline handlers.
 */
(function () {
  "use strict";

  var panel = document.getElementById("tk-settings");
  var form = document.getElementById("tk-settings-form");
  if (!panel || !form) return;

  var url = panel.getAttribute("data-url");
  var note = document.getElementById("tk-set-note");
  var loaded = false;

  function $(id) {
    return document.getElementById(id);
  }

  function say(text) {
    note.textContent = text;
    note.hidden = !text;
  }

  function csrfToken() {
    return window.BKP && typeof window.BKP.csrfToken === "function" ? window.BKP.csrfToken() : "";
  }

  function hhmm(value) {
    return value ? String(value).slice(0, 5) : "";
  }

  function show(prefs) {
    $("tk-set-stop").checked = !!prefs.opted_out;
    $("tk-set-channel").value = prefs.preferred_channel || "";
    $("tk-set-language").value = prefs.language || "";
    $("tk-set-quiet-start").value = hhmm(prefs.quiet_hours_start);
    $("tk-set-quiet-end").value = hhmm(prefs.quiet_hours_end);
    var muted = prefs.muted_events || [];
    form.querySelectorAll(".tk-mute input").forEach(function (box) {
      box.checked = muted.indexOf(box.value) !== -1;
    });
  }

  function load() {
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (prefs) {
        loaded = true;
        show(prefs);
      })
      .catch(function () {
        say("Your message settings could not be loaded. Check your connection and open this again.");
      });
  }

  panel.addEventListener("toggle", function () {
    if (panel.open && !loaded) load();
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    say("");
    var start = $("tk-set-quiet-start").value;
    var end = $("tk-set-quiet-end").value;
    if (!!start !== !!end) {
      say("Quiet hours need both a start and an end, or neither.");
      return;
    }
    var body = {
      opted_out: $("tk-set-stop").checked,
      preferred_channel: $("tk-set-channel").value || null,
      language: $("tk-set-language").value || null,
      quiet_hours_start: start || null,
      quiet_hours_end: end || null,
      muted_events: Array.prototype.map.call(form.querySelectorAll(".tk-mute input:checked"), function (box) {
        return box.value;
      })
    };
    $("tk-set-save").disabled = true;
    fetch(url, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrfToken() },
      body: JSON.stringify(body)
    })
      .then(function (response) {
        return response.json().then(function (answer) {
          return { ok: response.ok, answer: answer };
        });
      })
      .then(function (result) {
        $("tk-set-save").disabled = false;
        if (!result.ok) {
          say(typeof result.answer.detail === "string" ? result.answer.detail : "Those settings could not be saved.");
          return;
        }
        show(result.answer);
        say(result.answer.opted_out ? "Saved. You will get no more messages about your tickets." : "Saved. Your next message follows these settings.");
      })
      .catch(function () {
        $("tk-set-save").disabled = false;
        say("No connection: nothing was saved.");
      });
  });
})();
