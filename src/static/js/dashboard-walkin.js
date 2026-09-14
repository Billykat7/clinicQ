/* Walk-in intake at the front desk (Issue 51).
 *
 * A walk-in is a name and the Enter key: the name field has focus, the queue this desk used last is
 * already chosen (remembered per clinic in this browser), and Enter issues the ticket through the queue
 * API. Tab reaches the rest in order; nothing needs a mouse. After a ticket is issued its number fills
 * the panel beside the form in large type, the form clears, and focus is back in the name field for
 * the next patient.
 *
 *   Enter    issue the ticket (also from the queue choice or the consent box)
 *   Alt+P    open the stub of the ticket just issued, to print or to show the patient
 *   Alt+U    undo the last walk-in, while the list offers it
 *
 * One press issues one ticket: the form ignores Enter while a request is in flight, and every request
 * carries an Idempotency-Key. When an answer is lost, the key is kept for that same form, so pressing
 * Enter again asks about the same walk-in rather than issuing a second one.
 *
 * The consent box is switched on only once a phone number is typed, and its answer is sent only then;
 * the API refuses it without a number either way. What can be undone, and until when, is the server's
 * answer in the recent list, which is fetched again after every issue and undo.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var form = document.getElementById('walkin-form');
  if (!form || !window.fetch) return;

  var RETRY_KEY_MS = 120000;
  var UNDO_TICK_MS = 1000;
  var STORE_KEY = 'clinicq.walkin.queue.' + form.getAttribute('data-site-id');

  var name = form.querySelector('#walkin-name');
  var phone = form.querySelector('#walkin-phone');
  var consent = form.querySelector('#walkin-consent');
  var reason = form.querySelector('#walkin-reason');
  var submit = form.querySelector('button[type="submit"]');
  var message = form.querySelector('[data-walkin-msg]');
  var result = document.getElementById('walkin-result');
  var busy = false;
  var retry = null; // { signature, key, until }
  var clock = { asOf: null, offset: null };
  var lastIssued = null; // the ticket id shown in the result panel

  // ── Small helpers ────────────────────────────────────────────────────────────────────────────────

  function freshKey() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }

  function say(text, kind) {
    message.textContent = text;
    message.className = 'msg' + (kind ? ' msg-' + kind : '');
  }

  function explain(status, data) {
    var detail = data && data.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map(function (item) { return item.msg; }).join(' ');
    }
    if (status === 403) return 'Your role at this clinic cannot issue tickets.';
    if (status === 404) return 'That queue is no longer here. Reload the page.';
    return 'The ticket was not issued. Try again.';
  }

  function headers(key) {
    var out = window.BKP && window.BKP.writeHeaders ? window.BKP.writeHeaders() : { 'Content-Type': 'application/json' };
    if (key) out['Idempotency-Key'] = key;
    return out;
  }

  function chosenQueue() {
    return form.querySelector('input[name="queue_id"]:checked');
  }

  // ── The queue this desk used last, and the consent box ───────────────────────────────────────────

  try {
    var saved = window.localStorage.getItem(STORE_KEY);
    if (saved) {
      Array.prototype.forEach.call(form.querySelectorAll('input[name="queue_id"]'), function (radio) {
        if (radio.value === saved) radio.checked = true;
      });
    }
  } catch (error) { /* no storage: the first queue stays chosen */ }

  function syncConsent() {
    var hasNumber = /\d/.test(phone.value);
    consent.disabled = !hasNumber;
    if (!hasNumber) consent.checked = false;
  }
  phone.addEventListener('input', syncConsent);
  syncConsent();

  // Enter on the queue choice or the consent box issues too: one key from anywhere in the form.
  form.addEventListener('keydown', function (event) {
    var target = event.target;
    if (event.key === 'Enter' && (target.type === 'radio' || target.type === 'checkbox')) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  // ── Issuing ──────────────────────────────────────────────────────────────────────────────────────

  function payload() {
    return {
      name: name.value.trim() || null,
      phone: phone.value.trim() || null,
      reason_text: reason.value.trim() || null,
      notifications_consent: !consent.disabled && consent.checked,
    };
  }

  function showResult(data, queueLabel) {
    var ticket = data.ticket;
    lastIssued = ticket.id;
    result.querySelector('[data-result-number]').textContent = ticket.number;
    result.querySelector('[data-result-queue]').textContent = queueLabel;
    var ahead = data.waiting_ahead === 0 ? 'Next in line' : data.waiting_ahead + ' ahead';
    result.querySelector('[data-result-standing]').textContent = ahead + ' · ' + data.wait.label;
    result.querySelector('[data-result-ref]').textContent = ticket.reference_code;
    result.querySelector('[data-result-print]').href = form.getAttribute('data-stub-template').replace('{ticket}', encodeURIComponent(ticket.id)) + '?print=1';
    result.hidden = false;
  }

  function clearForm() {
    name.value = '';
    phone.value = '';
    reason.value = '';
    consent.checked = false;
    syncConsent();
    name.focus();
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (busy) return; // a second Enter while the first is on its way
    if (!name.value.trim()) {
      say('Type a name or initials first.', 'error');
      name.focus();
      return;
    }
    var queue = chosenQueue();
    if (!queue) {
      say('Choose a queue.', 'error');
      return;
    }
    var body = payload();
    var signature = queue.value + '|' + JSON.stringify(body);
    var key = retry && retry.signature === signature && retry.until > Date.now() ? retry.key : freshKey();
    var queueLabel = queue.closest('label').querySelector('.walkin-queue-name').textContent.trim();

    busy = true;
    submit.disabled = true;
    form.setAttribute('aria-busy', 'true');
    say('Issuing…', 'muted');

    fetch(form.getAttribute('data-api-template').replace('{queue}', encodeURIComponent(queue.value)), {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers(key),
      body: JSON.stringify(body),
    })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          retry = null;
          if (response.status === 401) {
            window.location.reload();
            return;
          }
          if (!response.ok) {
            say(explain(response.status, data), 'error');
            return;
          }
          try { window.localStorage.setItem(STORE_KEY, queue.value); } catch (error) { /* not remembered */ }
          showResult(data, queueLabel);
          say(data.created ? 'Issued ' + data.ticket.number + '.' : data.message, 'ok');
          clearForm();
          refreshRecent();
        });
      })
      .catch(function () {
        retry = { signature: signature, key: key, until: Date.now() + RETRY_KEY_MS };
        say('The clinic could not be reached, so the ticket may not have been issued. Press Enter again: it will not be issued twice.', 'error');
      })
      .then(function () {
        busy = false;
        submit.disabled = false;
        form.removeAttribute('aria-busy');
      });
  });

  // ── The recent list and undo ─────────────────────────────────────────────────────────────────────

  function refreshRecent() {
    return fetch(form.getAttribute('data-recent-url'), { credentials: 'same-origin', headers: { Accept: 'text/html' } })
      .then(function (response) {
        if (!response.ok) return null;
        return response.text();
      })
      .then(function (html) {
        if (!html) return;
        var holder = document.createElement('template');
        holder.innerHTML = html.trim();
        var fresh = holder.content.querySelector('#walkin-recent');
        var current = document.getElementById('walkin-recent');
        if (fresh && current) current.replaceWith(fresh);
        tickUndo();
      })
      .catch(function () { /* the list stays as it was; the next issue fetches it again */ });
  }

  function undo(button) {
    if (button.disabled) return;
    button.disabled = true;
    var item = button.closest('[data-ticket-id]');
    var number = item ? item.getAttribute('data-number') : 'The ticket';
    fetch(button.getAttribute('data-undo-url'), { method: 'POST', credentials: 'same-origin', headers: headers(null) })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (response.ok) {
            say(number + ' was undone and taken out of the line.', 'ok');
            if (item && item.getAttribute('data-ticket-id') === lastIssued) result.hidden = true;
          } else {
            say(explain(response.status, data), 'error');
          }
          return refreshRecent();
        });
      })
      .catch(function () {
        button.disabled = false;
        say('The clinic could not be reached, so nothing was undone. Try again.', 'error');
      })
      .then(function () { name.focus(); });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('button.walkin-undo');
    if (button) {
      event.preventDefault();
      undo(button);
    }
  });

  document.addEventListener('keydown', function (event) {
    if (!event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.code === 'KeyP' && !result.hidden) {
      event.preventDefault();
      result.querySelector('[data-result-print]').click();
    } else if (event.code === 'KeyU') {
      var button = document.querySelector('button.walkin-undo:not([hidden]):not(:disabled)');
      if (button) {
        event.preventDefault();
        undo(button);
      }
    }
  });

  /** The server's clock, corrected as dashboard-actions.js does: the largest gap seen is closest. */
  function serverNow() {
    var list = document.getElementById('walkin-recent');
    var asOf = list ? Date.parse(list.getAttribute('data-as-of')) : NaN;
    if (!isNaN(asOf) && asOf !== clock.asOf) {
      clock.asOf = asOf;
      var gap = asOf - Date.now();
      clock.offset = clock.offset === null ? gap : Math.max(clock.offset, gap);
    }
    return Date.now() + (clock.offset || 0);
  }

  function tickUndo() {
    var now = serverNow();
    document.querySelectorAll('button[data-undo-until]').forEach(function (button) {
      var until = Date.parse(button.getAttribute('data-undo-until'));
      if (isNaN(until)) return;
      var left = Math.ceil((until - now) / 1000);
      if (left <= 0) {
        button.hidden = true; // the server would refuse now, and the next refresh agrees
        return;
      }
      var minutes = Math.floor(left / 60);
      var seconds = ('0' + (left % 60)).slice(-2);
      button.firstChild.textContent = 'Undo ' + minutes + ':' + seconds + ' ';
    });
  }

  window.setInterval(tickUndo, UNDO_TICK_MS);
  tickUndo();
})();
