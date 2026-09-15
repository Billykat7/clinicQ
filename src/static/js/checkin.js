/* The check-in tablet by the clinic's door (Issue 83).
 *
 * Three ways in, one answer:
 *  - a USB or camera scanner types the QR's text and presses Enter. A hidden field holds the focus all
 *    day, so a scan works whatever the patient last touched;
 *  - the keypad takes a phone number, digits only, with a Delete key;
 *  - where the clinic allows it, a queue button starts a walk-in.
 *
 * Whatever is shown is cleared after data-clear-seconds and the screen goes back to idle, so nothing
 * personal is left for the next patient. The page asks data-state-url every data-ping-seconds: when
 * that fails, or the browser says it is offline, the screen says "please see reception" and takes
 * nothing, rather than seeming to work.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var root = document.querySelector('.checkin[data-arrivals-url]');
  if (!root) return;

  var ARRIVALS = root.getAttribute('data-arrivals-url');
  var WALK_INS = root.getAttribute('data-walk-ins-url');
  var STATE = root.getAttribute('data-state-url');
  var CLEAR_MS = (parseFloat(root.getAttribute('data-clear-seconds')) || 20) * 1000;
  var PING_MS = (parseFloat(root.getAttribute('data-ping-seconds')) || 15) * 1000;
  var OFFLINE = root.getAttribute('data-offline-sentence') || 'Please see reception.';
  var MAX_DIGITS = 12;

  var idle = document.getElementById('checkin-idle');
  var keypad = document.getElementById('checkin-keypad');
  var answer = document.getElementById('checkin-answer');
  var problem = document.getElementById('checkin-problem');
  var offline = document.getElementById('checkin-offline');
  var scan = document.getElementById('checkin-scan');
  var entry = document.getElementById('checkin-entry');
  var digits = '';
  var clearTimer = null;
  var busy = false;

  function show(panel) {
    [idle, keypad, answer, problem].forEach(function (section) {
      section.hidden = section !== panel;
    });
    if (panel === idle) holdFocus();
  }

  function holdFocus() {
    try {
      scan.focus({ preventScroll: true });
    } catch (error) {
      scan.focus();
    }
  }

  function toIdle() {
    window.clearTimeout(clearTimer);
    clearTimer = null;
    digits = '';
    entry.textContent = ' ';
    scan.value = '';
    show(idle);
  }

  function clearSoon() {
    window.clearTimeout(clearTimer);
    clearTimer = window.setTimeout(toIdle, CLEAR_MS);
  }

  function say(found) {
    document.getElementById('checkin-number').textContent = found.number;
    document.getElementById('checkin-where').textContent =
      found.queue_name + (found.room_label ? ' · ' + found.room_label : '');
    document.getElementById('checkin-ahead').textContent =
      found.waiting_ahead > 0
        ? found.waiting_ahead + (found.waiting_ahead === 1 ? ' person is' : ' people are') + ' ahead of you'
        : 'You are next in line';
    show(answer);
    clearSoon();
  }

  function complain(sentence) {
    document.getElementById('checkin-problem-text').textContent = sentence;
    show(problem);
    clearSoon();
  }

  function send(url, body) {
    if (busy) return;
    busy = true;
    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body)
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        busy = false;
        if (result.ok) return say(result.data);
        complain((result.data && result.data.detail) || OFFLINE);
      })
      .catch(function () {
        busy = false;
        goneOffline();
      });
  }

  function goneOffline() {
    offline.hidden = false;
    toIdle();
  }

  function backOnline() {
    offline.hidden = true;
  }

  // The scanner types the code and presses Enter; nothing else uses this field.
  scan.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    var code = scan.value.trim();
    scan.value = '';
    if (code) send(ARRIVALS, { code: code });
  });
  scan.addEventListener('blur', function () {
    if (idle.hidden) return;
    window.setTimeout(holdFocus, 50);
  });

  document.getElementById('checkin-start-phone').addEventListener('click', function () {
    digits = '';
    entry.textContent = ' ';
    show(keypad);
    clearSoon();
  });

  Array.prototype.forEach.call(root.querySelectorAll('[data-digit]'), function (key) {
    key.addEventListener('click', function () {
      if (digits.length >= MAX_DIGITS) return;
      digits += key.getAttribute('data-digit');
      entry.textContent = digits;
      clearSoon();
    });
  });

  document.getElementById('checkin-back').addEventListener('click', function () {
    digits = digits.slice(0, -1);
    entry.textContent = digits || ' ';
    clearSoon();
  });

  document.getElementById('checkin-go').addEventListener('click', function () {
    if (digits.length < 9) return complain('Please type your whole phone number.');
    send(ARRIVALS, { code: digits });
  });

  document.getElementById('checkin-cancel').addEventListener('click', toIdle);
  document.getElementById('checkin-problem-close').addEventListener('click', toIdle);

  Array.prototype.forEach.call(root.querySelectorAll('.checkin-queue'), function (button) {
    button.addEventListener('click', function () {
      send(WALK_INS, { queue_id: button.getAttribute('data-queue') });
    });
  });

  function ping() {
    if (!navigator.onLine) return goneOffline();
    fetch(STATE, { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (response.ok) return backOnline();
        goneOffline();
      })
      .catch(goneOffline);
  }

  window.addEventListener('online', ping);
  window.addEventListener('offline', goneOffline);
  window.setInterval(ping, PING_MS);
  ping();
  toIdle();
})();
