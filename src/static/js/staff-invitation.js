/**
 * Accepting a staff invitation (Issue 22).
 *
 * The page is opened by someone with no account and no session, so everything it knows comes from
 * the token in the URL and the two public endpoints that read it:
 *   - GET  /api/v1/staff/invitations/preview?token=…  → the clinic, the role and the deadline
 *   - POST /api/v1/staff/invitations/accept           → the account, once
 *
 * The token is never written into the page's own state beyond the request: no localStorage, no
 * history rewrite, and it is not put in the form. A link that is used, revoked, expired or unknown
 * gets one message, because the server gives one answer for all four.
 *
 * External file, no inline handlers: satisfies script-src 'self'.
 */
(function () {
  "use strict";

  var PREVIEW = "/api/v1/staff/invitations/preview";
  var ACCEPT = "/api/v1/staff/invitations/accept";

  var form = document.getElementById("invite-form");
  var loading = document.getElementById("invite-loading");
  var summary = document.getElementById("invite-summary");
  var message = document.getElementById("invite-msg");
  var password = document.getElementById("invite-password");
  var firstName = document.getElementById("invite-first-name");
  var lastName = document.getElementById("invite-last-name");

  if (!form) return;

  var token = new URLSearchParams(window.location.search).get("token") || "";

  function say(text, kind) {
    if (!message) return;
    message.textContent = text;
    message.className = "msg " + (kind || "msg-muted");
    message.hidden = !text;
  }

  /** The one message every unusable link gets, so the page tells no more than the API does. */
  function refuse() {
    if (loading) loading.hidden = true;
    form.hidden = true;
    say(
      "This invitation link is no longer valid. Ask the clinic that invited you to send a new one.",
      "msg-error"
    );
  }

  function show(invitation) {
    if (loading) loading.hidden = true;
    if (summary) {
      summary.textContent =
        "You were invited as a " +
        (invitation.role_label || invitation.role) +
        ", at " +
        invitation.email +
        ".";
      summary.hidden = false;
    }
    form.hidden = false;
  }

  function accept(event) {
    event.preventDefault();
    if (!password || !password.value) return;
    var button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    say("Creating your account…", "msg-muted");

    fetch(ACCEPT, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: token,
        password: password.value,
        first_name: (firstName && firstName.value) || null,
        last_name: (lastName && lastName.value) || null,
      }),
    })
      .then(function (res) {
        return res.json().then(function (body) {
          return { ok: res.ok, body: body };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          if (button) button.disabled = false;
          say(
            (result.body && result.body.detail) ||
              "That did not work. Check your password and try again.",
            "msg-error"
          );
          return;
        }
        form.hidden = true;
        if (summary) summary.hidden = true;
        say(result.body.detail || "Your account is ready. You can sign in now.", "msg-ok");
      })
      .catch(function () {
        if (button) button.disabled = false;
        say("That did not work. Check your connection and try again.", "msg-error");
      });
  }

  form.addEventListener("submit", accept);

  if (!token) {
    refuse();
    return;
  }

  fetch(PREVIEW + "?token=" + encodeURIComponent(token), { credentials: "same-origin" })
    .then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    })
    .then(show)
    .catch(refuse);
})();
