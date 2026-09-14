/* The clinic dashboard frame's keyboard layer (Issue 48).
 *
 * A receptionist's hands are on the keyboard and their eyes are on a patient, so every screen the
 * frame links to opens without the mouse: press g, then the key shown beside the screen's tab
 * (g b for the front desk, g r for a room, g s for the settings, g c for the clinic switcher), and ?
 * lists them. The keys are not written here: each comes from the rendered link's data-shortcut,
 * which the server fills from the nav registry, so a screen the caller cannot open has no key.
 *
 * Shortcuts never fire while focus is in a field, a select or anything editable, or with a modifier
 * held, so typing a patient's name that contains a g and a b does not leave the page.
 *
 * Pages add their own single-key actions with data-kbd="<key>" on a button (Issue 50's Call next);
 * the same rules apply, and a disabled button is left alone.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var LEADER = 'g';
  // How long after g the second key still counts: long enough for a hurried hand, short enough that
  // a stray g typed outside a field does not arm a jump minutes later.
  var LEADER_WINDOW_MS = 1500;
  var leaderAt = 0;

  var dialog = document.getElementById('shortcuts-dialog');

  /** True when the key press belongs to something the person is typing into. */
  function isTyping(target) {
    if (!target || target === document.body) return false;
    if (target.isContentEditable) return true;
    var tag = target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  }

  function openShortcuts() {
    if (!dialog || dialog.open) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  /** Follow the frame's element for a shortcut key: a link navigates, the switcher opens. */
  function jump(key) {
    var target = document.querySelector('[data-shortcut="' + key + '"]');
    if (!target) return false;
    if (target.tagName === 'SUMMARY') {
      var details = target.parentElement;
      details.open = true;
      var first = details.querySelector('a');
      if (first) first.focus();
      return true;
    }
    target.click();
    return true;
  }

  /** Press a page's own action button for a single key, when it is present and enabled. */
  function act(key) {
    var button = document.querySelector('[data-kbd="' + key + '"]');
    if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
    button.click();
    return true;
  }

  document.addEventListener('keydown', function (event) {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (isTyping(event.target)) return;
    if (dialog && dialog.open) return; // the dialog handles its own Escape
    var key = event.key;

    if (key === '?') {
      event.preventDefault();
      openShortcuts();
      return;
    }
    if (leaderAt && Date.now() - leaderAt <= LEADER_WINDOW_MS) {
      leaderAt = 0;
      if (jump(key.toLowerCase())) event.preventDefault();
      return;
    }
    leaderAt = 0;
    if (key === LEADER) {
      leaderAt = Date.now();
      return;
    }
    if (act(key.toLowerCase())) event.preventDefault();
  });

  document.querySelectorAll('[data-shortcuts-open]').forEach(function (button) {
    button.addEventListener('click', openShortcuts);
  });

  // The switcher closes on Escape and on a click elsewhere, the way a menu is expected to.
  document.querySelectorAll('[data-site-switcher]').forEach(function (details) {
    details.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape' || !details.open) return;
      details.open = false;
      var summary = details.querySelector('summary');
      if (summary) summary.focus();
    });
    document.addEventListener('click', function (event) {
      if (details.open && !details.contains(event.target)) details.open = false;
    });
  });
})();
