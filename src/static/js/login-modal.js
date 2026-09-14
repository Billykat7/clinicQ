/**
 * Sign-in / sign-up modal behaviour (Issue 9).
 *
 * Talks to the BK ClinicQ auth API (all under /api/v1/auth):
 *   - GET  /config                 → which methods are enabled (OTP/password/signup)
 *   - POST /otp/request            → email a one-time code
 *   - POST /otp/verify             → verify code, server sets session cookies
 *   - POST /password/login         → password sign-in, server sets cookies
 *   - POST /password/forgot        → email a reset link
 *   - POST /password/reset         → set a new password from the emailed token
 *   - POST /signup                 → email an activation link
 *   - GET  /me                     → current user (drives the header state)
 *   - POST /logout                 → clear the session
 *
 * The modal markup lives in templates/partials/login_modal.html. Header controls
 * (sign-in button, avatar, user menu) are optional — present only in the admin
 * shell — so every lookup is null-guarded. No inline handlers: script-src 'self'.
 */
(function () {
  "use strict";

  var AUTH = "/api/v1/auth";
  var DEFAULT_NEXT = "/dashboard";

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    var backdrop = document.getElementById("login-modal-backdrop");
    var controller = backdrop ? createModal(backdrop) : null;
    wireOpeners(controller);
    wireSignout();
    setupHeader(controller);
    enhancePasswordFields(backdrop);
    if (controller) {
      maybeAutoOpen(controller);
      // A page that must not be left (the dashboard's queued actions, Issue 55) asks for a fresh
      // sign-in in place: the promise resolves once signed in, and the page carries on.
      window.BKPAuth = { reauthenticate: controller.reauthenticate };
    }
  }

  var EYE =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M2 12s3.5-7 10-7c1.7 0 3.2.5 4.5 1.2M22 12s-3.5 7-10 7c-1.7 0-3.2-.5-4.5-1.2"/>' +
    '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="M3 3l18 18"/></svg>';

  /** Add a show/hide toggle to each password field in the auth modal (sign-in + reset). */
  function enhancePasswordFields(root) {
    if (!root) return;
    var inputs = root.querySelectorAll('input[type="password"]');
    Array.prototype.forEach.call(inputs, function (input) {
      if (input.parentNode && input.parentNode.classList.contains("pw-wrap")) return;
      var wrap = document.createElement("span");
      wrap.className = "pw-wrap";
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pw-toggle";
      btn.setAttribute("aria-label", "Show password");
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("tabindex", "-1"); // reachable by mouse; keep Tab flowing through the form
      btn.innerHTML = EYE;
      btn.addEventListener("click", function () {
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.innerHTML = show ? EYE_OFF : EYE;
        btn.setAttribute("aria-pressed", String(show));
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
        input.focus();
      });
      wrap.appendChild(btn);
    });
  }

  /** POST JSON with CSRF header; returns {ok, status, data}. */
  function postJson(path, body) {
    return fetch(AUTH + path, {
      method: "POST",
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    }).then(readResponse);
  }

  function readResponse(res) {
    var ct = res.headers.get("content-type") || "";
    var parse = ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
    return parse.then(function (data) {
      return { ok: res.ok, status: res.status, data: data };
    });
  }

  /** Pull a human message out of a FastAPI error body. */
  function errorText(result, fallback) {
    var d = result && result.data;
    if (d && typeof d.detail === "string") return d.detail;
    if (d && Array.isArray(d.detail) && d.detail.length) {
      var first = d.detail[0];
      if (first && first.msg) return first.msg;
    }
    return fallback;
  }

  function nextUrl() {
    var params = new URLSearchParams(window.location.search);
    var next = params.get("next");
    // Only allow same-origin relative paths, to avoid open redirects.
    if (next && next.charAt(0) === "/" && next.charAt(1) !== "/") return next;
    return DEFAULT_NEXT;
  }

  // ── Modal ────────────────────────────────────────────────────────────────

  function createModal(backdrop) {
    var closeBtn = document.getElementById("login-modal-close");
    var screens = {
      signin: document.getElementById("screen-signin"),
      otp: document.getElementById("screen-otp"),
      signup: document.getElementById("screen-signup"),
      reset: document.getElementById("screen-reset"),
    };

    // Password-reset token from the emailed link (/?resetToken=…). Present only when the
    // user arrived from a "forgot password" email; drives the reset screen below.
    var resetToken = new URLSearchParams(window.location.search).get("resetToken") || "";

    // Sign-in screen
    var signinForm = document.getElementById("signin-form");
    var emailInput = document.getElementById("signin-email");
    var passwordField = document.getElementById("password-field");
    var passwordInput = document.getElementById("signin-password");
    var sendCodeBtn = document.getElementById("btn-send-code");
    var passwordBtn = document.getElementById("btn-password-login");
    var signinError = document.getElementById("signin-error");
    var signinLoading = document.getElementById("signin-loading");
    var forgotLink = document.getElementById("forgot-password-link");
    var forgotRow = document.getElementById("forgot-row");
    var goSignupWrap = document.getElementById("go-signup-wrap");
    var goSignupLink = document.getElementById("go-signup-link");

    // Reset-password screen
    var resetForm = document.getElementById("reset-form");
    var resetPassword = document.getElementById("reset-password");
    var resetPasswordConfirm = document.getElementById("reset-password-confirm");
    var resetError = document.getElementById("reset-error");
    var resetLoading = document.getElementById("reset-loading");
    var resetCancelLink = document.getElementById("reset-cancel-link");

    // OTP screen
    var otpForm = document.getElementById("otp-form");
    var otpEmail = document.getElementById("otp-email");
    var otpCode = document.getElementById("otp-code");
    var otpError = document.getElementById("otp-error");
    var otpLoading = document.getElementById("otp-loading");
    var changeEmailLink = document.getElementById("change-email-link");
    var resendLink = document.getElementById("resend-code-link");

    // Sign-up screen
    var signupForm = document.getElementById("signup-form");
    var signupEmail = document.getElementById("signup-email");
    var signupError = document.getElementById("signup-error");
    var signupLoading = document.getElementById("signup-loading");
    var backToSigninLink = document.getElementById("back-to-signin-link");

    var pendingEmail = "";
    var lastFocus = null;

    var config = { otp_login_enabled: true, password_login_enabled: true, signup_enabled: false };

    fetch(AUTH + "/config", { credentials: "same-origin" })
      .then(readResponse)
      .then(function (r) { if (r.ok && r.data) { config = r.data; applyConfig(); } })
      .catch(function () { applyConfig(); });

    function applyConfig() {
      toggle(sendCodeBtn, !config.otp_login_enabled);
      toggle(passwordBtn, !config.password_login_enabled);
      toggle(passwordField, !config.password_login_enabled);
      toggle(goSignupWrap, !config.signup_enabled);
      // "Forgot password?" only makes sense when password sign-in exists; the
      // /password/forgot endpoint 403s otherwise. Hide the whole row so it never dead-ends.
      toggle(forgotRow, !config.password_login_enabled);
    }

    function show(name) {
      Object.keys(screens).forEach(function (key) {
        var el = screens[key];
        if (!el) return;
        var on = key === name;
        el.classList.toggle("active", on);
        el.setAttribute("aria-hidden", on ? "false" : "true");
      });
      clearMessages();
    }

    function clearMessages() {
      [signinError, otpError, signupError, resetError].forEach(function (el) {
        if (el) { el.textContent = ""; el.classList.remove("success"); }
      });
      [signinLoading, otpLoading, signupLoading, resetLoading].forEach(function (el) {
        if (el) el.classList.remove("visible");
      });
    }

    function open(screenName) {
      lastFocus = document.activeElement;
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
      var target = screens[screenName] ? screenName : "signin";
      show(target);
      var focusEl = target === "reset" ? resetPassword : emailInput;
      if (focusEl) focusEl.focus();
      document.addEventListener("keydown", onKeydown);
    }

    /** Drop ?resetToken= from the address bar so it isn't reused, bookmarked or shown. */
    function stripResetTokenFromUrl() {
      resetToken = "";
      try {
        var url = new URL(window.location.href);
        url.searchParams.delete("resetToken");
        window.history.replaceState({}, document.title, url.pathname + url.search + url.hash);
      } catch (e) { /* history not available — leave the URL as-is */ }
    }

    function close() {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
      document.removeEventListener("keydown", onKeydown);
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    function onKeydown(e) { if (e.key === "Escape") close(); }

    if (closeBtn) closeBtn.addEventListener("click", close);
    backdrop.addEventListener("mousedown", function (e) { if (e.target === backdrop) close(); });

    // Sign-in: default submit = send OTP code.
    if (signinForm) {
      signinForm.addEventListener("submit", function (e) {
        e.preventDefault();
        if (config.otp_login_enabled) requestOtp();
        else if (config.password_login_enabled) passwordLogin();
      });
    }
    if (passwordBtn) passwordBtn.addEventListener("click", passwordLogin);

    function requestOtp() {
      var email = (emailInput.value || "").trim();
      if (!email) return fail(signinError, "Enter your email.");
      busy(signinLoading, true);
      disableForm(signinForm, true);
      postJson("/otp/request", { email: email }).then(function (r) {
        busy(signinLoading, false);
        disableForm(signinForm, false);
        if (!r.ok) return fail(signinError, errorText(r, "Could not send a code. Try again."));
        pendingEmail = email;
        if (otpEmail) otpEmail.textContent = email;
        show("otp");
        if (otpCode) otpCode.focus();
      }).catch(function () {
        busy(signinLoading, false); disableForm(signinForm, false);
        fail(signinError, "Network error. Try again.");
      });
    }

    function passwordLogin() {
      var email = (emailInput.value || "").trim();
      var password = passwordInput ? passwordInput.value : "";
      if (!email || !password) return fail(signinError, "Enter your email and password.");
      busy(signinLoading, true);
      disableForm(signinForm, true);
      postJson("/password/login", { email: email, password: password }).then(function (r) {
        if (!r.ok) {
          busy(signinLoading, false); disableForm(signinForm, false);
          return fail(signinError, errorText(r, "Incorrect email or password."));
        }
        succeed();
      }).catch(function () {
        busy(signinLoading, false); disableForm(signinForm, false);
        fail(signinError, "Network error. Try again.");
      });
    }

    // OTP verify
    if (otpForm) {
      otpForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var code = (otpCode.value || "").trim();
        if (!code) return fail(otpError, "Enter the code from your email.");
        busy(otpLoading, true); disableForm(otpForm, true);
        postJson("/otp/verify", { email: pendingEmail, code: code }).then(function (r) {
          if (!r.ok) {
            busy(otpLoading, false); disableForm(otpForm, false);
            return fail(otpError, errorText(r, "That code is invalid or expired."));
          }
          succeed();
        }).catch(function () {
          busy(otpLoading, false); disableForm(otpForm, false);
          fail(otpError, "Network error. Try again.");
        });
      });
    }
    if (changeEmailLink) changeEmailLink.addEventListener("click", function (e) { e.preventDefault(); show("signin"); if (emailInput) emailInput.focus(); });
    if (resendLink) resendLink.addEventListener("click", function (e) {
      e.preventDefault();
      if (!pendingEmail) return;
      postJson("/otp/request", { email: pendingEmail }).then(function (r) {
        if (r.ok) ok(otpError, "A new code is on its way.");
        else fail(otpError, errorText(r, "Could not resend. Try again."));
      });
    });

    // Forgot password
    if (forgotLink) forgotLink.addEventListener("click", function (e) {
      e.preventDefault();
      var email = (emailInput.value || "").trim();
      if (!email) return fail(signinError, "Enter your email first, then choose Forgot password.");
      postJson("/password/forgot", { email: email }).then(function (r) {
        if (r.ok) ok(signinError, "If that email has an account, a reset link is on its way.");
        else fail(signinError, errorText(r, "Could not start a reset. Try again."));
      });
    });

    // Reset password (from the emailed ?resetToken= link)
    if (resetForm) {
      resetForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var pw = resetPassword ? resetPassword.value : "";
        var confirmPw = resetPasswordConfirm ? resetPasswordConfirm.value : "";
        if (!pw || pw.length < 8) return fail(resetError, "Choose a password of at least 8 characters.");
        if (pw !== confirmPw) return fail(resetError, "Those passwords don't match.");
        if (!resetToken) return fail(resetError, "This reset link is missing its token. Request a new one.");
        busy(resetLoading, true); disableForm(resetForm, true);
        postJson("/password/reset", { token: resetToken, new_password: pw }).then(function (r) {
          busy(resetLoading, false); disableForm(resetForm, false);
          if (!r.ok) return fail(resetError, errorText(r, "Could not reset your password. The link may have expired — request a new one."));
          // Password changed and all sessions revoked server-side: send them to sign in fresh.
          stripResetTokenFromUrl();
          resetPassword.value = ""; resetPasswordConfirm.value = "";
          show("signin");
          ok(signinError, "Password updated. Sign in with your new password.");
          if (emailInput) emailInput.focus();
        }).catch(function () {
          busy(resetLoading, false); disableForm(resetForm, false);
          fail(resetError, "Network error. Try again.");
        });
      });
    }
    if (resetCancelLink) resetCancelLink.addEventListener("click", function (e) {
      e.preventDefault(); stripResetTokenFromUrl(); show("signin"); if (emailInput) emailInput.focus();
    });

    // Navigate to sign-up
    if (goSignupLink) goSignupLink.addEventListener("click", function (e) { e.preventDefault(); show("signup"); if (signupEmail) signupEmail.focus(); });
    if (backToSigninLink) backToSigninLink.addEventListener("click", function (e) { e.preventDefault(); show("signin"); if (emailInput) emailInput.focus(); });

    // Sign-up
    if (signupForm) {
      signupForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var email = (signupEmail.value || "").trim();
        if (!email) return fail(signupError, "Enter your email.");
        busy(signupLoading, true); disableForm(signupForm, true);
        postJson("/signup", { email: email }).then(function (r) {
          busy(signupLoading, false); disableForm(signupForm, false);
          if (!r.ok) return fail(signupError, errorText(r, "Could not sign you up. Try again."));
          ok(signupError, (r.data && r.data.message) || "Check your email to activate your account.");
        }).catch(function () {
          busy(signupLoading, false); disableForm(signupForm, false);
          fail(signupError, "Network error. Try again.");
        });
      });
    }

    // A sign-in asked for by the page itself (reauthenticate below) stays on the page.
    var reauth = null;

    function succeed() {
      if (reauth) {
        var pendingSignIn = reauth;
        reauth = null;
        busy(signinLoading, false); disableForm(signinForm, false);
        busy(otpLoading, false); disableForm(otpForm, false);
        close();
        if (window.BKP && typeof window.BKP.reviveSession === "function") window.BKP.reviveSession();
        document.dispatchEvent(new CustomEvent("session:renewed"));
        pendingSignIn.resolve(true);
        return;
      }
      window.location.assign(nextUrl());
    }

    /**
     * Ask the person to sign in again without leaving the page, saying why. Resolves once they have;
     * a second call while the first is open shares it.
     */
    function reauthenticate(message) {
      if (!reauth) {
        var resolveSignIn;
        var promise = new Promise(function (resolve) { resolveSignIn = resolve; });
        reauth = { promise: promise, resolve: resolveSignIn };
      }
      open("signin");
      if (signinError && message) {
        signinError.textContent = message;
        signinError.classList.add("success");
      }
      return reauth.promise;
    }

    return { open: open, close: close, reauthenticate: reauthenticate };
  }

  // ── Openers & header ─────────────────────────────────────────────────────

  function wireOpeners(controller) {
    if (!controller) return;
    var openers = document.querySelectorAll('[data-open-signin]');
    openers.forEach(function (el) {
      el.addEventListener("click", function (e) { e.preventDefault(); controller.open(); });
    });
  }

  function maybeAutoOpen(controller) {
    var params = new URLSearchParams(window.location.search);
    // A reset link (/?resetToken=…) always lands on the set-a-new-password screen.
    if (params.get("resetToken")) { controller.open("reset"); return; }
    if (params.get("openSignin") === "1") controller.open();
  }

  /** Bind every [data-signout] control (rail button, user menu) to logout. */
  function wireSignout() {
    document.querySelectorAll("[data-signout]").forEach(function (el) {
      el.addEventListener("click", logout);
    });
  }

  /**
   * Drive the admin-shell header: fetch the current user and reveal either the
   * sign-in button or the avatar + user menu. No-ops on pages without them.
   */
  function setupHeader(controller) {
    var signInBtn = document.getElementById("signin-btn");
    var avatarBtn = document.getElementById("avatar-btn");
    var menuBackdrop = document.getElementById("user-menu-backdrop");
    if (!signInBtn && !avatarBtn) return;

    fetch(AUTH + "/me", { credentials: "same-origin" })
      .then(readResponse)
      .then(function (r) {
        if (r.ok && r.data) renderSignedIn(r.data);
        else renderSignedOut();
      })
      .catch(renderSignedOut);

    function renderSignedOut() {
      toggle(signInBtn, false);
      toggle(avatarBtn, true);
      if (signInBtn && controller) {
        signInBtn.addEventListener("click", function () { controller.open(); });
      }
    }

    function renderSignedIn(me) {
      toggle(signInBtn, true);
      toggle(avatarBtn, false);
      var initial = (me.first_name || me.email || "?").trim().charAt(0).toUpperCase();
      setText("avatar-initial", initial);
      setText("user-menu-initial", initial);
      var displayName = [me.first_name, me.last_name].filter(Boolean).join(" ") || me.email;
      setText("user-menu-name", displayName);
      setText("user-menu-email", me.email);
      shellInitial = initial;
      applyShellAvatar(me.avatar_url, initial);
      if (avatarBtn && menuBackdrop) setupMenu(avatarBtn, menuBackdrop);
    }
  }

  function setupMenu(avatarBtn, backdrop) {
    var closeBtn = document.getElementById("user-menu-close");
    function open() { backdrop.classList.add("open"); backdrop.setAttribute("aria-hidden", "false"); avatarBtn.setAttribute("aria-expanded", "true"); document.addEventListener("keydown", onKey); }
    function close() { backdrop.classList.remove("open"); backdrop.setAttribute("aria-hidden", "true"); avatarBtn.setAttribute("aria-expanded", "false"); document.removeEventListener("keydown", onKey); }
    function onKey(e) { if (e.key === "Escape") close(); }
    avatarBtn.addEventListener("click", open);
    if (closeBtn) closeBtn.addEventListener("click", close);
    backdrop.addEventListener("mousedown", function (e) { if (e.target === backdrop) close(); });
  }

  function logout() {
    fetch(AUTH + "/logout", {
      method: "POST",
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
    }).then(function () { window.location.assign("/"); })
      .catch(function () { window.location.assign("/"); });
  }

  // ── Tiny DOM helpers ─────────────────────────────────────────────────────

  function toggle(el, hidden) { if (el) el.hidden = !!hidden; }
  function busy(el, on) { if (el) el.classList.toggle("visible", !!on); }
  function fail(el, msg) { if (el) { el.textContent = msg; el.classList.remove("success"); } }
  function ok(el, msg) { if (el) { el.textContent = msg; el.classList.add("success"); } }
  function disableForm(form, on) {
    if (!form) return;
    Array.prototype.forEach.call(form.querySelectorAll("button, input"), function (el) { el.disabled = !!on; });
  }
  function setText(id, text) { var el = document.getElementById(id); if (el) el.textContent = text; }

  // The signed-in user's initial, remembered from the ``/me`` render: once a picture replaces the
  // initial span there is nothing in the DOM left to read it back from, and removing the picture
  // has to restore it (Issue #130).
  var shellInitial = "";

  /**
   * Render one avatar holder as either the user's picture or their initial.
   *
   * The holder's initial is a ``<span>`` the picture replaces, so reverting has to rebuild that
   * span rather than just clearing the image — otherwise removing a picture would leave an empty
   * circle instead of the fallback (Issue #130).
   */
  function setAvatarImage(id, url, initial, initialId) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = "";
    if (url) {
      var img = document.createElement("img");
      img.src = url; img.alt = "";
      el.appendChild(img);
      return;
    }
    var span = document.createElement("span");
    span.id = initialId;
    span.setAttribute("aria-hidden", "true");
    span.textContent = initial || "";
    el.appendChild(span);
  }

  /** Set (or clear) the picture on both shell avatars, falling back to ``initial``. */
  function applyShellAvatar(url, initial) {
    setAvatarImage("avatar-btn", url, initial, "avatar-initial");
    setAvatarImage("user-menu-avatar", url, initial, "user-menu-initial");
  }

  /**
   * The shell's avatar, exposed so the account page can reflect an upload or removal at once
   * (Issue #130) instead of the user having to reload to see their new picture.
   */
  var shell = (window.BKPShell = window.BKPShell || {});
  shell.setAvatar = function (url) {
    applyShellAvatar(url, shellInitial);
  };
})();
