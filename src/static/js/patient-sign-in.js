/**
 * Signing a patient in with their phone number and a code (Issue 200): patient/_sign_in.html.
 *
 * Used by the join page and the installed app's start page. Two steps over the patients API:
 *   - POST /api/v1/patients/otp/request  → 202 a code is on its way; 429 wait (Retry-After); 503 SMS is off
 *   - POST /api/v1/patients/otp/verify   → 200 signed in (session and CSRF cookies set); 400 wrong code
 * Every refusal shown is the API's own `detail` sentence. The resend button waits out the cooldown the API
 * gives, counting down. External file, no inline handlers, no eval: satisfies script-src 'self'.
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

  var phoneInput = $("sign-in-phone");
  var codeInput = $("sign-in-code");
  var send = $("sign-in-send");
  var verify = $("sign-in-verify");
  var resend = $("sign-in-resend");
  var wait = $("sign-in-wait");
  var sent = $("sign-in-sent");
  var codeLength = parseInt(codeInput.getAttribute("data-length"), 10) || 6;
  var invalidPhone = $("sign-in-phone-form").getAttribute("data-invalid") || UNREADABLE;

  var phone = "";
  var timer = null;
  var smsOff = false;
  var onSignedIn = function () {};

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

  function busy(button, on, label) {
    if (on) button.setAttribute("data-label", button.textContent);
    button.disabled = on;
    button.textContent = on ? label : button.getAttribute("data-label") || button.textContent;
  }

  /**
   * Hold "Send a new code" for `seconds`, saying how long is left, then let it go. The cooldown belongs to
   * the number, so "Send me a code" is held too only when the server refused it (`alsoSend`): a patient who
   * mistyped their number can send to the right one straight away.
   */
  function holdSending(seconds, alsoSend) {
    clearInterval(timer);
    var left = seconds;
    function tick() {
      if (left <= 0) {
        clearInterval(timer);
        wait.hidden = true;
        send.disabled = smsOff;
        resend.disabled = smsOff;
        return;
      }
      if (alsoSend) send.disabled = true;
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
    var button = fromCodeStep ? resend : send;
    busy(button, true, "Sending…");
    return call("POST", "/api/v1/patients/otp/request", { phone: phone }).then(function (answer) {
      busy(button, false);
      if (answer.ok) {
        sent.textContent =
          "We sent a " + codeLength + "-digit code to " + phone + ". It expires in " +
          Math.round((answer.body.expires_in_seconds || 600) / 60) + " minutes.";
        codeInput.value = "";
        show("code");
        codeInput.focus();
        holdSending(answer.body.resend_after_seconds || 60, false);
        return;
      }
      if (fromCodeStep && answer.status === 422) show("phone");
      // A number too short or too long is refused by request validation, which sends no sentence.
      say(answer.status === 422 && typeof answer.body.detail !== "string" ? invalidPhone : sentence(answer));
      if (answer.status === 429) holdSending(answer.retryAfter || 60, !fromCodeStep);
      if (answer.status === 503) {
        // SMS is switched off for this deployment: asking again cannot work.
        smsOff = true;
        send.disabled = true;
        resend.disabled = true;
      }
    });
  }

  $("sign-in-phone-form").addEventListener("submit", function (event) {
    event.preventDefault();
    phone = phoneInput.value.trim();
    if (!phone) {
      say(invalidPhone);
      phoneInput.focus();
      return;
    }
    requestCode(false);
  });

  resend.addEventListener("click", function () {
    requestCode(true);
  });

  $("sign-in-change").addEventListener("click", function () {
    // Another number starts afresh; asking again for the same one is refused by the server, with its wait.
    clearInterval(timer);
    wait.hidden = true;
    send.disabled = smsOff;
    resend.disabled = smsOff;
    say("");
    show("phone");
    phoneInput.focus();
  });

  $("sign-in-code-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var code = codeInput.value.replace(/\D/g, "");
    if (code.length !== codeLength) {
      say("Type the " + codeLength + "-digit code from the SMS.");
      codeInput.focus();
      return;
    }
    say("");
    busy(verify, true, "Checking…");
    call("POST", "/api/v1/patients/otp/verify", { phone: phone, code: code }).then(function (answer) {
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
    /** Show the phone step again, with a sentence saying why. */
    restart: function (text) {
      show("phone");
      say(text);
    },
    /** One API call as {ok, status, body, retryAfter}, with the CSRF header: for the page's next steps. */
    call: call,
    sentence: sentence,
  };
})();
