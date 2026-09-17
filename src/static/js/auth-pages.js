/**
 * The sign-in pages (Issue 231): `/signin`, `/signup`, `/forgot-password`, `/reset-password`.
 *
 * Replaces the behaviour half of `login-modal.js`. The difference is not only that the overlay is
 * gone: **the server decides what the page shows.** Which controls exist comes from the template,
 * rendered from settings, so nothing is fetched from `/auth/config` and nothing appears or vanishes
 * after first paint. This file only submits the forms the page has and says what came back.
 *
 * One exception to "a step is a page": the code step lives on `/signin`, because the code belongs
 * to the email just typed and a fresh GET would have nothing to attach it to. The address stays
 * `/signin`, and a reload honestly starts over.
 *
 * The CSRF token comes from `window.BKP.writeHeaders()` (csrf-htmx.js), which reads the cookie name
 * out of `<meta name="bkp-csrf-cookie">` — never a name written here. Issue 229 removed the last
 * way a stale cookie could refuse a sign-in; a hardcoded name would put one back.
 *
 * External file only, to satisfy `script-src 'self'`.
 */
(function () {
  "use strict";

  var AUTH = "/api/v1/auth";
  var DEFAULT_NEXT = "/dashboard";

  function el(id) {
    return document.getElementById(id);
  }

  /** POST JSON with the CSRF header; resolves {ok, status, data}. */
  function postJson(path, body) {
    return fetch(AUTH + path, {
      method: "POST",
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      var type = res.headers.get("content-type") || "";
      var parse =
        type.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
      return parse
        .catch(function () { return null; })
        .then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
    });
  }

  /** The human sentence in a FastAPI error body, or `fallback`. */
  function errorText(result, fallback) {
    var detail = result && result.data && result.data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length && detail[0] && detail[0].msg) {
      return detail[0].msg;
    }
    return fallback;
  }

  function say(slot, message, ok) {
    if (!slot) return;
    slot.textContent = message;
    slot.classList.toggle("is-ok", Boolean(ok));
  }

  function busy(form, on) {
    if (!form) return;
    Array.prototype.forEach.call(form.elements, function (field) {
      field.disabled = on;
    });
  }

  /**
   * Where to go once signed in: the form's `data-next`, which the route has already checked is a
   * same-origin path. Checked again here so a tampered attribute cannot become an open redirect.
   */
  function nextUrl(form) {
    var next = form ? form.getAttribute("data-next") : "";
    if (next && next.charAt(0) === "/" && next.charAt(1) !== "/") return next;
    return DEFAULT_NEXT;
  }

  /** Submit `form` through `send`, with the message slot and the disabled state handled once. */
  function onSubmit(form, slot, send) {
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      say(slot, "");
      busy(form, true);
      send()
        .catch(function () {
          return { ok: false, data: null, network: true };
        })
        .then(function (result) {
          busy(form, false);
          if (result && result.network) {
            say(slot, "Could not reach the server. Check your connection and try again.");
          }
        });
    });
  }

  // ── /signin ───────────────────────────────────────────────────────────────

  function signInPage() {
    var form = el("signin-form");
    if (!form) return;

    var email = el("signin-email");
    var password = el("signin-password");
    var slot = el("signin-error");
    var codeButton = el("btn-send-code");

    var otpForm = el("otp-form");
    var otpEmail = el("otp-email");
    var otpCode = el("otp-code");
    var otpSlot = el("otp-error");
    var links = el("signin-links");
    var pendingEmail = "";

    function address() {
      return (email.value || "").trim();
    }

    /** Swap the email step for the code step, in place. The URL does not change. */
    function showCodeStep() {
      form.hidden = true;
      if (links) links.hidden = true;
      otpForm.hidden = false;
      if (otpEmail) otpEmail.textContent = pendingEmail;
      if (otpCode) otpCode.focus();
    }

    function showEmailStep() {
      otpForm.hidden = true;
      form.hidden = false;
      if (links) links.hidden = false;
      say(otpSlot, "");
      email.focus();
    }

    function requestCode() {
      if (!address()) {
        say(slot, "Enter your email.");
        return Promise.resolve({ ok: false });
      }
      return postJson("/otp/request", { email: address() }).then(function (result) {
        if (!result.ok) {
          say(slot, errorText(result, "Could not send a code. Try again."));
          return result;
        }
        pendingEmail = address();
        showCodeStep();
        return result;
      });
    }

    function passwordLogin() {
      if (!address() || !password.value) {
        say(slot, "Enter your email and password.");
        return Promise.resolve({ ok: false });
      }
      return postJson("/password/login", {
        email: address(),
        password: password.value,
      }).then(function (result) {
        if (!result.ok) {
          say(slot, errorText(result, "Incorrect email or password."));
          return result;
        }
        window.location.assign(nextUrl(form));
        return result;
      });
    }

    // Submitting the form does whatever the page's primary button does: sign in with a password
    // when this deployment has passwords, otherwise send a code.
    onSubmit(form, slot, password ? passwordLogin : requestCode);

    // "Email me a code instead" is a second button on the same form when both exist.
    if (codeButton && password) {
      codeButton.addEventListener("click", function () {
        say(slot, "");
        busy(form, true);
        requestCode()
          .catch(function () {
            say(slot, "Could not reach the server. Check your connection and try again.");
          })
          .then(function () {
            busy(form, false);
          });
      });
    }

    onSubmit(otpForm, otpSlot, function () {
      var code = (otpCode.value || "").trim();
      if (!code) {
        say(otpSlot, "Enter the code from your email.");
        return Promise.resolve({ ok: false });
      }
      return postJson("/otp/verify", { email: pendingEmail, code: code }).then(
        function (result) {
          if (!result.ok) {
            say(otpSlot, errorText(result, "That code is invalid or has expired."));
            return result;
          }
          window.location.assign(nextUrl(otpForm));
          return result;
        }
      );
    });

    var change = el("change-email-link");
    if (change) {
      change.addEventListener("click", function (event) {
        event.preventDefault();
        showEmailStep();
      });
    }

    var resend = el("resend-code-link");
    if (resend) {
      resend.addEventListener("click", function (event) {
        event.preventDefault();
        if (!pendingEmail) return;
        postJson("/otp/request", { email: pendingEmail }).then(function (result) {
          if (result.ok) say(otpSlot, "A new code is on its way.", true);
          else say(otpSlot, errorText(result, "Could not send another code. Try again."));
        });
      });
    }
  }

  // ── /signup ───────────────────────────────────────────────────────────────

  function signUpPage() {
    var form = el("signup-form");
    if (!form) return;
    var slot = el("signup-error");
    var email = el("signup-email");

    onSubmit(form, slot, function () {
      var address = (email.value || "").trim();
      if (!address) {
        say(slot, "Enter your email.");
        return Promise.resolve({ ok: false });
      }
      return postJson("/signup", { email: address }).then(function (result) {
        if (!result.ok) {
          say(slot, errorText(result, "Could not create the account. Try again."));
          return result;
        }
        // The server writes this sentence, so a channel adapter and this page say the same thing.
        say(
          slot,
          (result.data && result.data.message) ||
            "Check your email to activate your account.",
          true
        );
        form.reset();
        return result;
      });
    });
  }

  // ── /forgot-password ──────────────────────────────────────────────────────

  function forgotPasswordPage() {
    var form = el("forgot-form");
    if (!form) return;
    var slot = el("forgot-error");
    var email = el("forgot-email");

    onSubmit(form, slot, function () {
      var address = (email.value || "").trim();
      if (!address) {
        say(slot, "Enter your email.");
        return Promise.resolve({ ok: false });
      }
      return postJson("/password/forgot", { email: address }).then(function (result) {
        if (!result.ok) {
          say(slot, errorText(result, "Could not start a reset. Try again."));
          return result;
        }
        // Deliberately the same answer whether or not the email has an account.
        say(slot, "If that email has an account, a reset link is on its way.", true);
        return result;
      });
    });
  }

  // ── /reset-password ───────────────────────────────────────────────────────

  function resetPasswordPage() {
    var form = el("reset-form");
    if (!form) return;
    var slot = el("reset-error");
    var token = el("reset-token");
    var chosen = el("reset-password");
    var confirmed = el("reset-password-confirm");

    onSubmit(form, slot, function () {
      if (!chosen.value || chosen.value.length < 8) {
        say(slot, "Choose a password of at least 8 characters.");
        return Promise.resolve({ ok: false });
      }
      if (chosen.value !== confirmed.value) {
        say(slot, "Those two passwords are not the same.");
        return Promise.resolve({ ok: false });
      }
      return postJson("/password/reset", {
        token: token.value,
        new_password: chosen.value,
      }).then(function (result) {
        if (!result.ok) {
          say(
            slot,
            errorText(
              result,
              "Could not set that password. The link may have been used or expired — ask for a new one."
            )
          );
          return result;
        }
        // The password changed and every session was revoked server-side: sign in fresh, on the
        // page whose job that is. `?reset=1` is what makes it say so.
        window.location.assign("/signin?reset=1");
        return result;
      });
    });
  }

  function start() {
    signInPage();
    signUpPage();
    forgotPasswordPage();
    resetPasswordPage();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
