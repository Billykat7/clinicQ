/**
 * Account settings behaviour: profile edit + security (Issues 9, 59).
 *
 * One file serves both /account/profile and /account/security; each page only
 * has the elements it needs, so every lookup is null-guarded. Talks to:
 *   - GET   /api/v1/auth/me                       → profile + account metadata
 *   - PATCH /api/v1/auth/me                       → save name / date of birth
 *   - POST  /api/v1/auth/me/avatar                → upload/replace the profile picture
 *   - DELETE /api/v1/auth/me/avatar               → remove the profile picture
 *   - POST  /api/v1/auth/me/email                 → start a re-verified email change
 *   - POST  /api/v1/auth/me/password              → change password (current password required)
 *   - POST  /api/v1/auth/me/resend-verification   → resend the activation email
 *   - POST  /api/v1/auth/password/forgot          → email a password reset/set link
 *   - GET   /api/v1/auth/me/sessions              → list active sessions
 *   - DELETE /api/v1/auth/me/sessions             → revoke all other sessions
 *   - DELETE /api/v1/auth/me/sessions/{id}        → revoke one session
 *
 * Sensitive actions (email change, password change) require the current password.
 * Business timestamps render in Africa/Johannesburg (project timezone rule).
 */
(function () {
  "use strict";

  var AUTH = "/api/v1/auth";
  var TZ = "Africa/Johannesburg";

  document.addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("profile-form")) initProfile();
    if (document.getElementById("send-reset-btn") || document.getElementById("sessions-body")) initSecurity();
  });

  // ── Shared helpers ─────────────────────────────────────────────────────────

  function readResponse(res) {
    var ct = res.headers.get("content-type") || "";
    var parse = ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
    return parse.then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
  }

  function getMe() {
    return fetch(AUTH + "/me", { credentials: "same-origin" }).then(readResponse);
  }

  function errorText(result, fallback) {
    var d = result && result.data;
    if (d && typeof d.detail === "string") return d.detail;
    if (d && Array.isArray(d.detail) && d.detail.length && d.detail[0].msg) return d.detail[0].msg;
    return fallback;
  }

  function setMsg(el, text, kind) {
    if (!el) return;
    el.textContent = text || "";
    el.className = "msg" + (kind ? " msg-" + kind : "");
  }

  function fmtDateTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    try {
      return d.toLocaleString("en-ZA", {
        timeZone: TZ, year: "numeric", month: "short", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      });
    } catch (e) { return d.toISOString(); }
  }

  // ── Profile picture (Issue 130) ────────────────────────────────────────────

  /**
   * Wire the profile-picture card: preview before saving, upload, replace and remove.
   *
   * Its own card and its own request, deliberately separate from the JSON profile save: an image
   * upload is multipart, and a picture the user chose should not be lost because a name field
   * failed validation (or the other way round).
   *
   * The preview is a local ``blob:`` URL of the chosen file, so the user sees what they picked
   * before anything is sent. The server is still the authority on what is acceptable — it
   * re-checks the type by content sniff, the size, and that the bytes decode — so the client-side
   * checks here exist only to fail fast with a clearer message.
   *
   * Returns a small handle with ``render(url)`` so the page can seed the card from ``/me``.
   */
  function initAvatar() {
    var card = document.getElementById("avatar-card");
    if (!card) return { render: function () {}, setInitials: function () {} };

    var preview = document.getElementById("pf-avatar-preview");
    var initials = document.getElementById("pf-avatar-initials");
    var input = document.getElementById("pf-avatar-file");
    var uploadBtn = document.getElementById("pf-avatar-upload");
    var cancelBtn = document.getElementById("pf-avatar-cancel");
    var removeBtn = document.getElementById("pf-avatar-remove");
    var msg = document.getElementById("pf-avatar-msg");

    var ACCEPTED = ["image/jpeg", "image/png", "image/webp"];
    // Mirrors the server's default cap; the server re-checks and is the real limit.
    var MAX_BYTES = 5 * 1024 * 1024;

    var savedUrl = null;   // what the server currently has
    var objectUrl = null;  // the local preview of an unsaved choice

    /** Release the previous blob: URL — an object URL held forever is a leak. */
    function releaseObjectUrl() {
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    }

    /** Point the preview at ``url``, or fall back to the initials circle when it is empty. */
    function show(url) {
      if (url) {
        preview.src = url;
        preview.hidden = false;
        initials.hidden = true;
      } else {
        preview.hidden = true;
        preview.removeAttribute("src");
        initials.hidden = false;
      }
    }

    /**
     * Fill the placeholder from the account's name — "JD" for John Doe, one letter when only one
     * name is known, and the email's first letter when neither is set (the same fallback the
     * account menu's avatar uses, so the two never disagree).
     */
    function setInitials(me) {
      var parts = [me.first_name, me.last_name]
        .map(function (n) { return (n || "").trim(); })
        .filter(Boolean)
        .map(function (n) { return n.charAt(0); });
      var text = parts.join("") || (me.email || "").charAt(0);
      initials.textContent = text.toUpperCase();
    }

    /** Reflect the saved state: no pending choice, remove offered only when there is one. */
    function render(url) {
      releaseObjectUrl();
      savedUrl = url || null;
      input.value = "";
      show(savedUrl);
      uploadBtn.disabled = true;
      cancelBtn.hidden = true;
      removeBtn.hidden = !savedUrl;
    }

    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      releaseObjectUrl();
      if (!file) { render(savedUrl); return; }
      if (ACCEPTED.indexOf(file.type) < 0) {
        input.value = "";
        return setMsg(msg, "Choose a JPEG, PNG or WebP image.", "error");
      }
      if (file.size > MAX_BYTES) {
        input.value = "";
        return setMsg(msg, "That image is too large (5 MB maximum).", "error");
      }
      objectUrl = URL.createObjectURL(file);
      show(objectUrl);
      uploadBtn.disabled = false;
      cancelBtn.hidden = false;
      setMsg(msg, "Preview — save to keep it.", "muted");
    });

    cancelBtn.addEventListener("click", function () {
      render(savedUrl);
      setMsg(msg, "");
    });

    /** Apply a successful response: re-render from it and update the shell at once. */
    function applyResult(data, text) {
      render(data && data.avatar_url);
      if (window.BKPShell) window.BKPShell.setAvatar(savedUrl);
      setMsg(msg, text, "ok");
    }

    uploadBtn.addEventListener("click", function () {
      var file = input.files && input.files[0];
      if (!file) return;
      var body = new FormData();
      body.append("file", file, file.name);
      // Only the CSRF token: no Content-Type, so the browser sets the multipart boundary itself
      // (the same pattern the inspection-photo upload uses).
      var headers = {};
      var token = window.BKP && window.BKP.csrfToken && window.BKP.csrfToken();
      if (token) headers["X-CSRF-Token"] = token;
      uploadBtn.disabled = true;
      setMsg(msg, "Uploading…", "muted");
      fetch(AUTH + "/me/avatar", {
        method: "POST",
        headers: headers,
        credentials: "same-origin",
        body: body,
      }).then(readResponse).then(function (r) {
        if (!r.ok) {
          uploadBtn.disabled = false;
          // 415 (not an image), 413 (too large) and 422 (undecodable) all carry a usable message.
          return setMsg(msg, errorText(r, "Could not save your picture."), "error");
        }
        applyResult(r.data, "Picture saved.");
      }).catch(function () {
        uploadBtn.disabled = false;
        setMsg(msg, "Network error. Try again.", "error");
      });
    });

    removeBtn.addEventListener("click", function () {
      if (!window.confirm("Remove your profile picture? Your initial will be shown instead.")) return;
      setMsg(msg, "Removing…", "muted");
      fetch(AUTH + "/me/avatar", {
        method: "DELETE",
        headers: window.BKP.writeHeaders(),
        credentials: "same-origin",
      }).then(readResponse).then(function (r) {
        if (!r.ok) return setMsg(msg, errorText(r, "Could not remove your picture."), "error");
        applyResult(r.data, "Picture removed.");
      }).catch(function () { setMsg(msg, "Network error. Try again.", "error"); });
    });

    return { render: render, setInitials: setInitials };
  }

  // ── Profile page ───────────────────────────────────────────────────────────

  function initProfile() {
    var loading = document.getElementById("profile-loading");
    var form = document.getElementById("profile-form");
    var email = document.getElementById("pf-email");
    var firstName = document.getElementById("pf-first-name");
    var lastName = document.getElementById("pf-last-name");
    var dob = document.getElementById("pf-dob");
    var verifyHint = document.getElementById("pf-verify-hint");
    var resendBtn = document.getElementById("pf-resend-verify");
    var msg = document.getElementById("profile-msg");
    var meta = document.getElementById("account-meta");

    showEmailChangedBanner();

    getMe().then(function (r) {
      if (loading) loading.hidden = true;
      if (!r.ok || !r.data) {
        if (loading) { loading.hidden = false; setMsg(loading, "Could not load your profile.", "error"); }
        return;
      }
      var me = r.data;
      form.hidden = false;
      if (email) email.value = me.email || "";
      if (firstName) firstName.value = me.first_name || "";
      if (lastName) lastName.value = me.last_name || "";
      if (dob) dob.value = me.date_of_birth || "";
      // Initials first: `render` decides between the picture and the placeholder, so the
      // placeholder has to know what to say before it can be shown.
      avatarCard.setInitials(me);
      avatarCard.render(me.avatar_url);
      renderVerification(me);
      renderMeta(me);
      initEmailChange(me);
    });

    var avatarCard = initAvatar();

    function renderVerification(me) {
      if (me.is_verified) {
        if (verifyHint) verifyHint.textContent = "Your email is verified.";
        if (resendBtn) resendBtn.hidden = true;
      } else {
        if (verifyHint) verifyHint.textContent = "Your email is not verified yet.";
        if (resendBtn) resendBtn.hidden = false;
      }
    }

    function renderMeta(me) {
      if (!meta) return;
      var rows = [
        ["Role", me.role || "user"],
        ["Sign-in method", me.auth_provider || "Email"],
        ["Last sign-in", fmtDateTime(me.last_login)],
        ["Member since", fmtDateTime(me.created_at)],
      ];
      meta.innerHTML = "";
      rows.forEach(function (row) {
        var d = document.createElement("div");
        d.className = "stat";
        var num = document.createElement("div");
        num.className = "num";
        num.style.fontSize = "1.05rem";
        num.textContent = row[1];
        var label = document.createElement("div");
        label.className = "label";
        label.textContent = row[0];
        d.appendChild(num);
        d.appendChild(label);
        meta.appendChild(d);
      });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      setMsg(msg, "Saving…", "muted");
      var payload = {
        first_name: firstName ? firstName.value.trim() || null : null,
        last_name: lastName ? lastName.value.trim() || null : null,
        date_of_birth: dob && dob.value ? dob.value : null,
      };
      fetch(AUTH + "/me", {
        method: "PATCH",
        headers: window.BKP.writeHeaders(),
        credentials: "same-origin",
        body: JSON.stringify(payload),
      }).then(readResponse).then(function (r) {
        if (!r.ok) return setMsg(msg, errorText(r, "Could not save changes."), "error");
        setMsg(msg, "Saved.", "ok");
      }).catch(function () { setMsg(msg, "Network error. Try again.", "error"); });
    });

    if (resendBtn) {
      resendBtn.addEventListener("click", function () {
        resendBtn.disabled = true;
        setMsg(msg, "Sending…", "muted");
        fetch(AUTH + "/me/resend-verification", {
          method: "POST",
          headers: window.BKP.writeHeaders(),
          credentials: "same-origin",
        }).then(readResponse).then(function (r) {
          resendBtn.disabled = false;
          if (!r.ok) return setMsg(msg, errorText(r, "Could not send the email."), "error");
          setMsg(msg, (r.data && r.data.message) || "Verification email sent.", "ok");
        }).catch(function () { resendBtn.disabled = false; setMsg(msg, "Network error. Try again.", "error"); });
      });
    }

    // Email change: gated on the account having a password (sensitive-action rule).
    function initEmailChange(me) {
      var emailForm = document.getElementById("email-form");
      var emailCard = document.getElementById("email-card");
      var noPassword = document.getElementById("email-no-password");
      var toggle = document.getElementById("ec-toggle");
      var cancel = document.getElementById("ec-cancel");
      if (!emailForm) return;
      if (!me.has_password) {
        // No password, no email change — hide the way in, and open the card just far enough to say
        // why rather than leaving a bar that does nothing.
        emailForm.hidden = true;
        if (toggle) toggle.hidden = true;
        if (emailCard) emailCard.hidden = false;
        if (noPassword) noPassword.hidden = false;
        return;
      }
      var newEmail = document.getElementById("ec-new-email");
      var currentPw = document.getElementById("ec-current-password");
      var emailMsg = document.getElementById("email-msg");

      if (toggle) {
        var setEmailOpen = function (open) {
          emailForm.hidden = !open;
          if (emailCard) emailCard.hidden = !open;
          toggle.setAttribute("aria-expanded", open ? "true" : "false");
          toggle.classList.toggle("is-open", open);
          if (open) newEmail.focus();
        };
        toggle.addEventListener("click", function () { setEmailOpen(true); });
        if (cancel) cancel.addEventListener("click", function () {
          emailForm.reset();
          setMsg(emailMsg, "");
          setEmailOpen(false);
        });
      }

      emailForm.addEventListener("submit", function (e) {
        e.preventDefault();
        setMsg(emailMsg, "Sending…", "muted");
        var submitBtn = emailForm.querySelector("button[type=submit]");
        if (submitBtn) submitBtn.disabled = true;
        fetch(AUTH + "/me/email", {
          method: "POST",
          headers: window.BKP.writeHeaders(),
          credentials: "same-origin",
          body: JSON.stringify({
            new_email: newEmail.value.trim(),
            current_password: currentPw.value,
          }),
        }).then(readResponse).then(function (r) {
          if (submitBtn) submitBtn.disabled = false;
          if (!r.ok) return setMsg(emailMsg, errorText(r, "Could not start the email change."), "error");
          currentPw.value = "";
          setMsg(emailMsg, (r.data && r.data.message) || "Check your new inbox to confirm.", "ok");
        }).catch(function () {
          if (submitBtn) submitBtn.disabled = false;
          setMsg(emailMsg, "Network error. Try again.", "error");
        });
      });
    }

    // Surface the ?emailChanged= result the confirm redirect lands back on.
    function showEmailChangedBanner() {
      var params = new URLSearchParams(window.location.search);
      var state = params.get("emailChanged");
      if (!state) return;
      var text = state === "success"
        ? "Your email address has been updated."
        : "That email confirmation link is invalid or has expired.";
      setMsg(msg, text, state === "success" ? "ok" : "error");
    }
  }

  // ── Security page ──────────────────────────────────────────────────────────

  function initSecurity() {
    var pwLoadingCard = document.getElementById("password-loading-card");
    var pwCard = document.getElementById("password-card");
    var pwForm = document.getElementById("password-form");
    var pwToggle = document.getElementById("pw-toggle");
    var pwCancel = document.getElementById("pw-cancel");
    var pwSet = document.getElementById("password-set");
    var resetBtn = document.getElementById("send-reset-btn");
    var passwordMsg = document.getElementById("password-msg");
    var passwordSetMsg = document.getElementById("password-set-msg");
    var loading = document.getElementById("sessions-loading");
    var wrap = document.getElementById("sessions-wrap");
    var body = document.getElementById("sessions-body");
    var revokeOthersBtn = document.getElementById("revoke-others-btn");
    var sessionsMsg = document.getElementById("sessions-msg");
    var currentEmail = "";

    // One of two flows, depending on whether a password is set. The change flow reveals its *bar*,
    // not its fields — the card behind it stays folded away until the bar asks for it. The setup
    // flow has nothing to fold, so it is a plain card.
    if (pwToggle || pwSet) {
      getMe().then(function (r) {
        if (pwLoadingCard) pwLoadingCard.hidden = true;
        if (r.ok && r.data) currentEmail = r.data.email || "";
        if (r.ok && r.data && r.data.has_password) {
          if (pwToggle) pwToggle.hidden = false;
        } else if (pwSet) {
          pwSet.hidden = false;
        }
      });
    }

    if (pwToggle && pwCard) {
      var setPwOpen = function (open) {
        pwCard.hidden = !open;
        pwToggle.setAttribute("aria-expanded", open ? "true" : "false");
        pwToggle.classList.toggle("is-open", open);
        if (open) document.getElementById("pw-current").focus();
      };
      pwToggle.addEventListener("click", function () { setPwOpen(true); });
      if (pwCancel) pwCancel.addEventListener("click", function () {
        pwForm.reset();
        setMsg(passwordMsg, "");
        setPwOpen(false);
      });
    }

    if (pwForm) initChangePassword();

    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        if (!currentEmail) return setMsg(passwordSetMsg, "Could not read your account. Reload and try again.", "error");
        resetBtn.disabled = true;
        setMsg(passwordSetMsg, "Sending…", "muted");
        fetch(AUTH + "/password/forgot", {
          method: "POST",
          headers: window.BKP.writeHeaders(),
          credentials: "same-origin",
          body: JSON.stringify({ email: currentEmail }),
        }).then(readResponse).then(function (r) {
          resetBtn.disabled = false;
          if (!r.ok) return setMsg(passwordSetMsg, errorText(r, "Could not send the link."), "error");
          setMsg(passwordSetMsg, "Check your inbox for the link.", "ok");
        }).catch(function () { resetBtn.disabled = false; setMsg(passwordSetMsg, "Network error. Try again.", "error"); });
      });
    }

    // In-place password change: current password re-proven, new password confirmed client-side.
    function initChangePassword() {
      var current = document.getElementById("pw-current");
      var next = document.getElementById("pw-new");
      var confirm = document.getElementById("pw-confirm");
      pwForm.addEventListener("submit", function (e) {
        e.preventDefault();
        if (next.value !== confirm.value) {
          return setMsg(passwordMsg, "New passwords do not match.", "error");
        }
        setMsg(passwordMsg, "Saving…", "muted");
        var submitBtn = pwForm.querySelector("button[type=submit]");
        if (submitBtn) submitBtn.disabled = true;
        fetch(AUTH + "/me/password", {
          method: "POST",
          headers: window.BKP.writeHeaders(),
          credentials: "same-origin",
          body: JSON.stringify({
            current_password: current.value,
            new_password: next.value,
          }),
        }).then(readResponse).then(function (r) {
          if (submitBtn) submitBtn.disabled = false;
          if (!r.ok) return setMsg(passwordMsg, errorText(r, "Could not change your password."), "error");
          pwForm.reset();
          setMsg(passwordMsg, (r.data && r.data.message) || "Password changed.", "ok");
          if (body) loadSessions();
        }).catch(function () {
          if (submitBtn) submitBtn.disabled = false;
          setMsg(passwordMsg, "Network error. Try again.", "error");
        });
      });
    }

    if (body) loadSessions();

    function loadSessions() {
      fetch(AUTH + "/me/sessions", { credentials: "same-origin" }).then(readResponse).then(function (r) {
        if (loading) loading.hidden = true;
        if (!r.ok || !r.data) {
          if (loading) { loading.hidden = false; setMsg(loading, "Could not load sessions.", "error"); }
          return;
        }
        renderSessions(r.data.sessions || []);
      }).catch(function () { if (loading) setMsg(loading, "Network error loading sessions.", "error"); });
    }

    function renderSessions(sessions) {
      if (wrap) wrap.hidden = sessions.length === 0;
      body.innerHTML = "";
      var others = 0;
      sessions.forEach(function (s) {
        if (!s.is_current) others += 1;
        var tr = document.createElement("tr");

        var device = document.createElement("td");
        device.className = "col-device";
        // A raw user-agent string runs hundreds of characters and would drag a horizontal scrollbar
        // across the table, so the cell truncates and carries the full string as its tooltip. The
        // inner row is what lets it truncate *around* the badge: the text is the part that gives up
        // width, and the badge keeps its own.
        var cellRow = document.createElement("span");
        cellRow.className = "device-cell";
        var agent = s.user_agent || "Unknown device";
        var agentText = document.createElement("span");
        agentText.className = "device-agent";
        agentText.textContent = agent;
        agentText.title = agent;
        cellRow.appendChild(agentText);
        if (s.is_current) {
          var badge = document.createElement("span");
          badge.className = "badge badge-current";
          badge.textContent = "This device";
          cellRow.appendChild(badge);
        }
        device.appendChild(cellRow);
        tr.appendChild(device);

        tr.appendChild(cell(s.sign_in_ip || "—"));
        tr.appendChild(cell(fmtDateTime(s.last_seen_at)));
        tr.appendChild(cell(fmtDateTime(s.expires_at)));

        var action = document.createElement("td");
        if (!s.is_current) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn btn-quiet btn-sm";
          btn.textContent = "Revoke";
          btn.addEventListener("click", function () { revokeOne(s.id, btn); });
          action.appendChild(btn);
        }
        tr.appendChild(action);
        body.appendChild(tr);
      });
      if (revokeOthersBtn) revokeOthersBtn.hidden = others === 0;
    }

    function cell(text) { var td = document.createElement("td"); td.textContent = text; return td; }

    function revokeOne(id, btn) {
      btn.disabled = true;
      fetch(AUTH + "/me/sessions/" + encodeURIComponent(id), {
        method: "DELETE",
        headers: window.BKP.writeHeaders(),
        credentials: "same-origin",
      }).then(function (res) {
        if (!res.ok) { btn.disabled = false; return setMsg(sessionsMsg, "Could not revoke that session.", "error"); }
        setMsg(sessionsMsg, "Session revoked.", "ok");
        loadSessions();
      }).catch(function () { btn.disabled = false; setMsg(sessionsMsg, "Network error. Try again.", "error"); });
    }

    if (revokeOthersBtn) {
      revokeOthersBtn.addEventListener("click", function () {
        revokeOthersBtn.disabled = true;
        fetch(AUTH + "/me/sessions", {
          method: "DELETE",
          headers: window.BKP.writeHeaders(),
          credentials: "same-origin",
        }).then(function (res) {
          revokeOthersBtn.disabled = false;
          if (!res.ok) return setMsg(sessionsMsg, "Could not revoke sessions.", "error");
          setMsg(sessionsMsg, "Signed out all other sessions.", "ok");
          loadSessions();
        }).catch(function () { revokeOthersBtn.disabled = false; setMsg(sessionsMsg, "Network error. Try again.", "error"); });
      });
    }
  }
})();
