/**
 * Signing a patient in with a phone number (Issue 200) or an email address (Issue 219) and a code:
 * patient/_sign_in.html.
 *
 * Used by the join page and the installed app's start page. Two steps over the patients API:
 *   - POST /api/v1/patients/otp/request  → 202 a code is on its way; 429 wait (Retry-After); 503 that way is off
 *   - POST /api/v1/patients/otp/verify   → 200 signed in (session and CSRF cookies set); 400 wrong code
 * Every refusal shown is the API's own `detail` sentence. The resend button waits out the cooldown the API
 * gives, counting down. External file, no inline handlers, no eval: satisfies script-src 'self'.
 *
 * The two contacts are peers here as they are in the API: one `contact` of a `kind`, sent as `{phone: …}`
 * or `{email: …}`, and everything after the first step — the code, the cooldown, the retries, the errors —
 * is written once and does not know which it is. The second tab exists only when the server rendered it
 * (PATIENT_EMAIL_SIGN_IN_ENABLED); with one way in, this behaves exactly as it did before Issue 219.
 *
 *   window.BKPSignIn.start(function onSignedIn(patient) { ... });
 */
(function () {
  "use strict";

  var OFFLINE = "We could not reach ClinicQ. Check your connection and try again.";
  var UNREADABLE = "That did not work. Check what you typed and try again.";

  function $(id) {
    return document.getElementById(id);
  }

  var steps = {
    phone: document.querySelector('[data-sign-in-step="phone"]'),
    code: document.querySelector('[data-sign-in-step="code"]'),
  };
  var error = $("sign-in-error");
  if (!steps.phone || !steps.code || !error) return;

  var codeInput = $("sign-in-code");
  var verify = $("sign-in-verify");
  var resend = $("sign-in-resend");
  var change = $("sign-in-change");
  var wait = $("sign-in-wait");
  var sent = $("sign-in-sent");
  var codeLength = parseInt(codeInput.getAttribute("data-length"), 10) || 6;

  /**
   * One way in. `field` and `send` are null for a way the server did not render, which is how the email
   * half of this file costs nothing when the flag is off. `off` is set when the server says that way is
   * switched off for the deployment, so asking again cannot work — and it is per way, so SMS being off
   * never disables an email sign-in that works.
   */
  var ways = {
    phone: {
      field: $("sign-in-phone"),
      send: $("sign-in-send"),
      form: $("sign-in-phone-form"),
      tab: $("sign-in-tab-phone"),
      panel: document.querySelector('[data-sign-in-panel="phone"]'),
      another: "Use a different number",
      typeTheCode: "Type the " + codeLength + "-digit code from the SMS.",
      off: false,
    },
    email: {
      field: $("sign-in-email"),
      send: $("sign-in-send-email"),
      form: $("sign-in-email-form"),
      tab: $("sign-in-tab-email"),
      panel: document.querySelector('[data-sign-in-panel="email"]'),
      another: "Use a different email address",
      typeTheCode: "Type the " + codeLength + "-digit code from the email.",
      off: false,
    },
  };
  if (!ways.phone.field || !ways.phone.send || !ways.phone.form) return;

  /** Which way the patient is using. The code step belongs to whichever asked for it. */
  var kind = "phone";
  var contact = "";
  var timer = null;
  var onSignedIn = function () {};

  function way() {
    return ways[kind];
  }

  function invalidMessage(which) {
    return ways[which].form.getAttribute("data-invalid") || UNREADABLE;
  }

  function csrfToken() {
    return window.BKP && typeof window.BKP.csrfToken === "function" ? window.BKP.csrfToken() : "";
  }

  function say(text) {
    error.textContent = text || "";
    error.hidden = !text;
  }

  /** The API's answer as {ok, status, body, retryAfter}; a network failure is status 0. */
  function call(method, url, body) {
    return fetch(url, {
      method: method,
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrfToken() },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(
      function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            return {
              ok: response.ok,
              status: response.status,
              body: data,
              retryAfter: parseInt(response.headers.get("Retry-After"), 10) || 0,
            };
          });
      },
      function () {
        return { ok: false, status: 0, body: {}, retryAfter: 0 };
      }
    );
  }

  /** The sentence to show for a refused answer: the API's own words when it sent some. */
  function sentence(answer) {
    if (answer.status === 0) return OFFLINE;
    return typeof answer.body.detail === "string" ? answer.body.detail : UNREADABLE;
  }

  function show(name) {
    steps.phone.hidden = name !== "phone";
    steps.code.hidden = name !== "code";
    var heading = steps[name].querySelector("h2");
    if (heading) heading.focus();
  }

  /**
   * Open one way's panel and close the other's. The closed field is **disabled** as well as hidden, so the
   * form carries one contact whatever a browser does with `hidden`, and a password manager cannot fill the
   * one nobody is looking at.
   */
  function openWay(which) {
    kind = which;
    contact = "";
    say("");
    Object.keys(ways).forEach(function (name) {
      var it = ways[name];
      if (!it.panel) return;
      var open = name === which;
      it.panel.hidden = !open;
      if (it.field) it.field.disabled = !open;
      if (it.tab) it.tab.setAttribute("aria-selected", open ? "true" : "false");
    });
    change.textContent = ways[which].another;
    if (ways[which].field) ways[which].field.focus();
  }

  function busy(button, on, label) {
    if (on) button.setAttribute("data-label", button.textContent);
    button.disabled = on;
    button.textContent = on ? label : button.getAttribute("data-label") || button.textContent;
  }

  /**
   * Hold "Send a new code" for `seconds`, saying how long is left, then let it go. The cooldown belongs to
   * the contact, so "Send me a code" is held too only when the server refused it (`alsoSend`): a patient who
   * mistyped their number can send to the right one straight away.
   */
  function holdSending(seconds, alsoSend) {
    clearInterval(timer);
    var current = way();
    var left = seconds;
    function tick() {
      if (left <= 0) {
        clearInterval(timer);
        wait.hidden = true;
        current.send.disabled = current.off;
        resend.disabled = current.off;
        return;
      }
      if (alsoSend) current.send.disabled = true;
      resend.disabled = true;
      wait.textContent = "You can ask for a new code in " + left + " s.";
      wait.hidden = false;
      left -= 1;
    }
    tick();
    timer = setInterval(tick, 1000);
  }

  function requestCode(fromCodeStep) {
    say("");
    var current = way();
    var button = fromCodeStep ? resend : current.send;
    var payload = {};
    payload[kind] = contact;
    busy(button, true, "Sending…");
    return call("POST", "/api/v1/patients/otp/request", payload).then(function (answer) {
      busy(button, false);
      if (answer.ok) {
        sent.textContent =
          "We sent a " + codeLength + "-digit code to " + contact + ". It expires in " +
          Math.round((answer.body.expires_in_seconds || 600) / 60) + " minutes.";
        codeInput.value = "";
        show("code");
        codeInput.focus();
        holdSending(answer.body.resend_after_seconds || 60, false);
        return;
      }
      if (fromCodeStep && answer.status === 422) show("phone");
      // A contact too short or too long is refused by request validation, which sends no sentence.
      say(answer.status === 422 && typeof answer.body.detail !== "string" ? invalidMessage(kind) : sentence(answer));
      if (answer.status === 429) holdSending(answer.retryAfter || 60, !fromCodeStep);
      if (answer.status === 503) {
        // This way in is switched off for the deployment: asking again cannot work. The other way,
        // if there is one, is untouched — SMS being off is not email being off.
        current.off = true;
        current.send.disabled = true;
        resend.disabled = true;
      }
    });
  }

  function submitContact(which) {
    return function (event) {
      event.preventDefault();
      kind = which;
      contact = ways[which].field.value.trim();
      if (!contact) {
        say(invalidMessage(which));
        ways[which].field.focus();
        return;
      }
      requestCode(false);
    };
  }

  Object.keys(ways).forEach(function (name) {
    var it = ways[name];
    if (it.form) it.form.addEventListener("submit", submitContact(name));
    if (it.tab) {
      it.tab.addEventListener("click", function () {
        openWay(name);
      });
    }
  });

  resend.addEventListener("click", function () {
    requestCode(true);
  });

  change.addEventListener("click", function () {
    // Another contact starts afresh; asking again for the same one is refused by the server, with its wait.
    clearInterval(timer);
    wait.hidden = true;
    way().send.disabled = way().off;
    resend.disabled = way().off;
    say("");
    show("phone");
    if (way().field) way().field.focus();
  });

  $("sign-in-code-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var code = codeInput.value.replace(/\D/g, "");
    if (code.length !== codeLength) {
      say(way().typeTheCode);
      codeInput.focus();
      return;
    }
    say("");
    var payload = { code: code };
    payload[kind] = contact;
    busy(verify, true, "Checking…");
    call("POST", "/api/v1/patients/otp/verify", payload).then(function (answer) {
      busy(verify, false);
      if (answer.ok) {
        clearInterval(timer);
        wait.hidden = true;
        steps.code.hidden = true;
        onSignedIn(answer.body);
        return;
      }
      var text = sentence(answer);
      if (answer.body.attempts_left > 0) {
        text += " " + answer.body.attempts_left + (answer.body.attempts_left === 1 ? " try left." : " tries left.");
      }
      say(text);
      codeInput.select();
    });
  });

  window.BKPSignIn = {
    /** Call `callback` with the patient once the code is accepted. */
    start: function (callback) {
      onSignedIn = callback;
    },
    /** Show the first step again, with a sentence saying why. */
    restart: function (text) {
      show("phone");
      say(text);
    },
    /** One API call as {ok, status, body, retryAfter}, with the CSRF header: for the page's next steps. */
    call: call,
    sentence: sentence,
  };
})();
