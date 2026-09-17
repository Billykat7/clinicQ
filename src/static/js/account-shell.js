/**
 * The signed-in shell's account controls (Issue 9; the modal removed in Issue 231).
 *
 * This is what is left of `login-modal.js` once signing in became a set of pages
 * (`/signin`, `/signup`, `/forgot-password`, `/reset-password`, driven by `auth-pages.js`):
 *
 *   - GET  /api/v1/auth/me      → reveal either the "Sign in" link or the avatar and user menu
 *   - POST /api/v1/auth/logout  → clear the session, then go to the front door
 *   - `window.BKPShell.setAvatar(url)` → reflect an upload or removal without a reload
 *   - `window.BKPAuth.reauthenticate(message)` → the session ended mid-task; go and sign in again
 *
 * Every header control is optional (only the admin shell has them), so each lookup is null-guarded.
 * No inline handlers: `script-src 'self'`.
 *
 * **`reauthenticate` navigates now.** It used to open the sign-in modal over the page and resolve a
 * promise once the person was back in, so the page could carry on. There is no modal to open, so it
 * sends them to `/signin?next=<this page>&expired=1` — the page says why they are there, and brings
 * them back to where they were. Its promise therefore never resolves: the callers
 * (`dashboard-outbox.js`, `dashboard-walkin.js`) treat it as "carry on after the sign-in", and the
 * page is leaving. The outbox keeps its queue in `sessionStorage` and re-sends on return, and the
 * walk-in form saves what was typed before it goes.
 */
(function () {
  "use strict";

  var AUTH = "/api/v1/auth";

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    wireSignout();
    setupHeader();
    window.BKPAuth = { reauthenticate: reauthenticate };
  }

  /**
   * Leave for the sign-in page, remembering this one. Returns a promise that never settles,
   * because the page is navigating away; callers await "signed in again", which now happens on
   * the other side of the round trip.
   */
  function reauthenticate() {
    var here = window.location.pathname + window.location.search;
    window.location.assign(
      "/signin?expired=1&next=" + encodeURIComponent(here)
    );
    return new Promise(function () {});
  }

  /** Bind every [data-signout] control (rail button, user menu) to logout. */
  function wireSignout() {
    document.querySelectorAll("[data-signout]").forEach(function (el) {
      el.addEventListener("click", logout);
    });
  }

  /**
   * Drive the admin-shell header: fetch the current user and reveal either the sign-in link or the
   * avatar + user menu. No-ops on pages without them.
   */
  function setupHeader() {
    var signInLink = document.getElementById("signin-btn");
    var avatarBtn = document.getElementById("avatar-btn");
    var menuBackdrop = document.getElementById("user-menu-backdrop");
    if (!signInLink && !avatarBtn) return;

    fetch(AUTH + "/me", { credentials: "same-origin" })
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .catch(function () {
        return null;
      })
      .then(function (me) {
        if (me) renderSignedIn(me);
        else renderSignedOut();
      });

    function renderSignedOut() {
      toggle(signInLink, false);
      toggle(avatarBtn, true);
    }

    function renderSignedIn(me) {
      toggle(signInLink, true);
      toggle(avatarBtn, false);
      var initial = (me.first_name || me.email || "?").trim().charAt(0).toUpperCase();
      setText("avatar-initial", initial);
      setText("user-menu-initial", initial);
      var displayName =
        [me.first_name, me.last_name].filter(Boolean).join(" ") || me.email;
      setText("user-menu-name", displayName);
      setText("user-menu-email", me.email);
      shellInitial = initial;
      applyShellAvatar(me.avatar_url, initial);
      if (avatarBtn && menuBackdrop) setupMenu(avatarBtn, menuBackdrop);
    }
  }

  function setupMenu(avatarBtn, backdrop) {
    var closeBtn = document.getElementById("user-menu-close");
    function open() {
      backdrop.classList.add("open");
      backdrop.setAttribute("aria-hidden", "false");
      avatarBtn.setAttribute("aria-expanded", "true");
      document.addEventListener("keydown", onKey);
    }
    function close() {
      backdrop.classList.remove("open");
      backdrop.setAttribute("aria-hidden", "true");
      avatarBtn.setAttribute("aria-expanded", "false");
      document.removeEventListener("keydown", onKey);
    }
    function onKey(e) {
      if (e.key === "Escape") close();
    }
    avatarBtn.addEventListener("click", open);
    if (closeBtn) closeBtn.addEventListener("click", close);
    backdrop.addEventListener("mousedown", function (e) {
      if (e.target === backdrop) close();
    });
  }

  function logout() {
    fetch(AUTH + "/logout", {
      method: "POST",
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
    })
      .then(function () {
        window.location.assign("/");
      })
      .catch(function () {
        window.location.assign("/");
      });
  }

  // ── Tiny DOM helpers ─────────────────────────────────────────────────────

  function toggle(el, hidden) {
    if (el) el.hidden = !!hidden;
  }
  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

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
      img.src = url;
      img.alt = "";
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
