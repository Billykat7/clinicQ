/**
 * Booking an appointment from the web (Issue 81): discover/book.html, after patient-sign-in.js.
 *
 *   - GET/PUT /api/v1/patients/me/consents[/notifications]          the messages question, once, first
 *   - GET  /api/v1/clinics/{site}/appointments/availability?day=   the day's bookable times
 *   - POST /api/v1/clinics/{site}/appointments                      book one
 *   - GET  /api/v1/patients/me/appointments                         the patient's own bookings
 *   - POST /api/v1/patients/me/appointments/{id}/reschedule         move one to the time chosen
 *   - POST /api/v1/patients/me/appointments/{id}/cancel             cancel one
 *
 * The server decides everything and its sentences are shown as they come. External file, no inline handlers;
 * markup is built with DOM methods, never strings.
 */
(function () {
  "use strict";

  var root = document.getElementById("book");
  var signIn = window.BKPSignIn;
  if (!root || !signIn) return;

  var site = root.getAttribute("data-site");
  var daySelect = document.getElementById("book-day");
  var times = document.getElementById("book-times");
  var none = document.getElementById("book-none");
  var error = document.getElementById("book-error");
  var list = document.getElementById("book-list");
  var listEmpty = document.getElementById("book-list-empty");
  var moving = null;
  var timeFormat = new Intl.DateTimeFormat("en-ZA", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Africa/Johannesburg" });
  var dayFormat = new Intl.DateTimeFormat("en-ZA", { weekday: "short", day: "numeric", month: "short", timeZone: "Africa/Johannesburg" });

  function say(text) {
    error.textContent = text || "";
    error.hidden = !text;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  var consent = document.getElementById("book-consent");
  var consentForm = document.getElementById("book-consent-form");

  /** Ask the messages question unless the patient already said yes; then show the times. */
  function afterSignIn() {
    signIn.call("GET", "/api/v1/patients/me/consents").then(function (answer) {
      var current = ((answer.ok && answer.body.answers) || []).filter(function (item) {
        return item.purpose === "notifications";
      })[0];
      if (current && current.granted) return showSignedIn();
      consent.hidden = false;
      document.getElementById("book-consent-heading").focus();
    });
  }

  consentForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var box = consentForm.querySelector('input[name="notifications"]:checked');
    if (!box) return say("Choose yes or no.");
    say("");
    signIn.call("PUT", "/api/v1/patients/me/consents/notifications", { granted: box.value === "yes" }).then(function (answer) {
      if (!answer.ok) return say(signIn.sentence(answer));
      consent.hidden = true;
      showSignedIn();
    });
  });

  function showSignedIn() {
    document.getElementById("book-choose").hidden = false;
    document.getElementById("book-mine").hidden = false;
    loadTimes();
    loadMine();
  }

  function loadTimes() {
    times.textContent = "";
    none.hidden = true;
    signIn.call("GET", "/api/v1/clinics/" + encodeURIComponent(site) + "/appointments/availability?day=" + daySelect.value).then(function (answer) {
      if (!answer.ok) return say(signIn.sentence(answer));
      var count = 0;
      answer.body.queues.forEach(function (queue) {
        queue.slots.forEach(function (slot) {
          count += 1;
          var button = el("button", "btn btn-block book-time", timeFormat.format(new Date(slot.starts_at)) + " · " + queue.queue_name);
          button.type = "button";
          button.setAttribute("data-slot", slot.id);
          button.addEventListener("click", function () { choose(slot.id, button); });
          times.appendChild(button);
        });
      });
      none.hidden = count > 0;
    });
  }

  function choose(slotId, button) {
    say("");
    button.disabled = true;
    var request = moving
      ? signIn.call("POST", "/api/v1/patients/me/appointments/" + encodeURIComponent(moving) + "/reschedule", { slot_id: slotId })
      : signIn.call("POST", "/api/v1/clinics/" + encodeURIComponent(site) + "/appointments", { slot_id: slotId });
    request.then(function (answer) {
      button.disabled = false;
      if (answer.status === 401) return signIn.restart("Your sign-in has ended. Sign in again to book.");
      if (!answer.ok) return say(signIn.sentence(answer));
      stopMoving();
      document.getElementById("book-confirmed").hidden = false;
      document.getElementById("book-confirmed-message").textContent = answer.body.message;
      document.getElementById("book-confirmed-reference").textContent = answer.body.reference;
      loadTimes();
      loadMine();
    });
  }

  function stopMoving() {
    moving = null;
    document.getElementById("book-moving").hidden = true;
  }

  function loadMine() {
    list.textContent = "";
    signIn.call("GET", "/api/v1/patients/me/appointments").then(function (answer) {
      if (!answer.ok) return;
      var items = answer.body.items.filter(function (item) { return item.status === "booked" || item.status === "converted"; });
      listEmpty.hidden = items.length > 0;
      items.forEach(function (item) {
        var at = new Date(item.starts_at);
        var row = el("li", "book-item");
        row.appendChild(el("p", "book-item-when", dayFormat.format(at) + " " + timeFormat.format(at) + " · " + item.queue_name));
        row.appendChild(el("p", "clinic-hint", item.clinic + " · " + item.message));
        if (item.ticket_page_url) {
          var link = el("a", "btn btn-quiet btn-block", "Open my place in the queue");
          link.href = item.ticket_page_url;
          row.appendChild(link);
        }
        if (item.changeable) {
          var move = el("button", "btn btn-quiet btn-block", "Move to another time");
          move.type = "button";
          move.addEventListener("click", function () {
            moving = item.id;
            document.getElementById("book-moving").hidden = false;
            document.getElementById("book-choose-heading").focus();
          });
          var cancel = el("button", "btn btn-quiet btn-block", "Cancel this booking");
          cancel.type = "button";
          cancel.addEventListener("click", function () {
            cancel.disabled = true;
            signIn.call("POST", "/api/v1/patients/me/appointments/" + encodeURIComponent(item.id) + "/cancel").then(function (result) {
              cancel.disabled = false;
              if (!result.ok) return say(signIn.sentence(result));
              loadTimes();
              loadMine();
            });
          });
          row.appendChild(move);
          row.appendChild(cancel);
        }
        list.appendChild(row);
      });
    });
  }

  daySelect.addEventListener("change", loadTimes);
  document.getElementById("book-moving-stop").addEventListener("click", stopMoving);
  signIn.start(afterSignIn);
  if (root.getAttribute("data-signed-in") === "true") afterSignIn();
})();
