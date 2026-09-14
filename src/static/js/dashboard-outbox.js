/* Actions that never silently vanish: held while offline, sent when the clinic is back (Issue 55).
 *
 * A receptionist who presses Call next while the network is down has asked for something. The page
 * must either do it when it can or say plainly that it did not; it must never drop it. So a press the
 * page cannot send (the connection is offline, the answer never came, or the session has ended) is put
 * here, with the Idempotency-Key it was pressed with, and shown in a list above the cards:
 *
 *   Waiting for the connection   held; sent in the order pressed as soon as the page is live again
 *   Sending                      on its way
 *   Done                         the clinic did it, with what it did ("Called T005"); fades after 15 s
 *   Not done                     the clinic refused it, in the clinic's words; stays until dismissed
 *   Not sent                     it waited longer than the clinic allows (2 minutes by default), so it
 *                                was not sent at all: a Call next from ten minutes ago is not what the
 *                                person wants now. Stays until dismissed.
 *   Sign in to finish            the session ended: the person signs in again in place and it is sent
 *
 * Sending an action twice is safe: the key makes the server answer a repeat with the first result, so
 * a request that reached the clinic just before the network went is not done again on replay. Pressing
 * the same button again while its action is held adds nothing.
 *
 * Held actions are kept in this tab's session storage, so a reload while offline does not lose them,
 * and closing the tab while one still waits asks first.
 * The connection state comes from dashboard-live.js (`board:connection`, `window.ClinicQLive`), the
 * in-place sign-in from login-modal.js (`window.BKPAuth`). Each outcome is also announced to the card it
 * belongs to (`outbox:settled`), whose status line says it again.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var root = document.getElementById('action-outbox');
  if (!root || !window.fetch) return;

  var EXPIRY_MS = Number(root.getAttribute('data-expiry-seconds') || '120') * 1000;
  var DONE_VISIBLE_MS = 15000;
  var STORE_KEY = 'clinicq.outbox.' + window.location.pathname;
  var list = root.querySelector('ol');
  var items = restore();
  var flushing = false;
  var signingIn = false;

  // ── Keeping them ─────────────────────────────────────────────────────────────────────────────────

  function restore() {
    try {
      var saved = JSON.parse(window.sessionStorage.getItem(STORE_KEY) || '[]');
      return saved.map(function (item) {
        if (item.state === 'sending') item.state = 'queued'; // the tab closed mid-send: ask again
        return item;
      });
    } catch (error) {
      return [];
    }
  }

  function persist() {
    try {
      var open = items.filter(function (item) { return item.state === 'queued' || item.state === 'sign-in'; });
      window.sessionStorage.setItem(STORE_KEY, JSON.stringify(open));
    } catch (error) { /* no storage: held for as long as the page is open */ }
  }

  function clock(ms) {
    var date = new Date(ms);
    return ('0' + date.getHours()).slice(-2) + ':' + ('0' + date.getMinutes()).slice(-2);
  }

  // ── Showing them ─────────────────────────────────────────────────────────────────────────────────

  var WORDS = {
    queued: 'Waiting for the connection',
    sending: 'Sending',
    done: 'Done',
    failed: 'Not done',
    expired: 'Not sent',
    'sign-in': 'Sign in to finish',
  };

  function render() {
    list.textContent = '';
    items.forEach(function (item) {
      var row = document.createElement('li');
      row.className = 'outbox-item is-' + item.state;
      row.setAttribute('data-outbox-id', item.id);
      var state = document.createElement('strong');
      state.textContent = WORDS[item.state] + ': ';
      row.appendChild(state);
      var text = document.createElement('span');
      text.textContent = item.message || item.label + ' (pressed at ' + clock(item.queuedAt) + ')';
      row.appendChild(text);
      if (item.state === 'sign-in') {
        row.appendChild(button('Sign in', 'sign-in'));
      } else if (item.state === 'failed' || item.state === 'expired' || item.state === 'done') {
        row.appendChild(button('Dismiss', 'dismiss'));
      }
      list.appendChild(row);
    });
    root.hidden = items.length === 0;
  }

  function button(label, action) {
    var control = document.createElement('button');
    control.type = 'button';
    control.className = 'btn btn-quiet btn-sm';
    control.setAttribute('data-outbox-action', action);
    control.textContent = label;
    return control;
  }

  function settle(item, state, message, kind) {
    item.state = state;
    item.message = message;
    persist();
    render();
    document.dispatchEvent(new CustomEvent('outbox:settled', {
      detail: { queueId: item.queueId, text: message, kind: kind },
    }));
    if (state === 'done') {
      window.setTimeout(function () { remove(item.id); }, DONE_VISIBLE_MS);
    }
  }

  function remove(id) {
    items = items.filter(function (item) { return item.id !== id; });
    persist();
    render();
  }

  root.addEventListener('click', function (event) {
    var control = event.target.closest('[data-outbox-action]');
    if (!control) return;
    var id = control.closest('[data-outbox-id]').getAttribute('data-outbox-id');
    if (control.getAttribute('data-outbox-action') === 'dismiss') remove(id);
    else askToSignIn();
  });

  // ── Sending them ─────────────────────────────────────────────────────────────────────────────────

  function reachable() {
    return window.ClinicQLive ? window.ClinicQLive.reachable() : navigator.onLine !== false;
  }

  function explain(status, data) {
    var detail = data && data.detail;
    if (typeof detail === 'string') return detail;
    if (status === 404) return 'It is no longer yours to act on (a room change, or the patient moved on).';
    if (status === 403) return 'Your role at this clinic cannot do this.';
    return 'The clinic could not do it.';
  }

  function outcome(item, data) {
    var number = data && (data.number || (data.ticket && data.ticket.number));
    var named = number && item.label.indexOf(number) === -1 ? ' (' + number + ')' : '';
    var when = item.via === 'sign-in' ? 'sent after you signed in again' : 'sent when the connection came back';
    return item.label + named + ', ' + when + '.';
  }

  /** Send the oldest held action, then the next, until one cannot be sent. */
  function flush() {
    if (flushing || signingIn || !reachable()) return;
    var next = items.filter(function (item) { return item.state === 'queued'; })[0];
    if (!next) return;
    if (Date.now() - next.queuedAt > EXPIRY_MS) {
      settle(next, 'expired',
        next.label + ' was pressed at ' + clock(next.queuedAt) + ' and waited too long for the connection, ' +
        'so it was not sent and nothing was changed. Press it again if it is still needed.', 'error');
      flush();
      return;
    }
    flushing = true;
    next.state = 'sending';
    render();
    var headers = window.BKP && window.BKP.writeHeaders ? window.BKP.writeHeaders() : { 'Content-Type': 'application/json' };
    headers['Idempotency-Key'] = next.key;
    fetch(next.url, { method: 'POST', credentials: 'same-origin', headers: headers, body: next.body || undefined })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (response.status === 401) {
            next.state = 'sign-in';
            next.via = 'sign-in';
            persist();
            render();
            askToSignIn();
            return false;
          }
          if (response.ok) settle(next, 'done', outcome(next, data), 'ok');
          else settle(next, 'failed', next.label + ': ' + explain(response.status, data) + ' Nothing was changed.', 'error');
          document.dispatchEvent(new CustomEvent('board:refresh'));
          return true;
        });
      })
      .catch(function () {
        next.state = 'queued'; // still unreachable: wait for the connection to come back
        persist();
        render();
        return false;
      })
      .then(function (carryOn) {
        flushing = false;
        if (carryOn) flush();
      });
  }

  function askToSignIn() {
    if (signingIn) return;
    var auth = window.BKPAuth;
    if (!auth) {
      window.location.reload(); // no in-place sign-in on this page: the held actions survive the reload
      return;
    }
    signingIn = true;
    var waiting = items.filter(function (item) { return item.state === 'sign-in'; });
    var message = waiting.length
      ? 'Your session ended. Sign in again to finish: ' + waiting.map(function (item) { return item.label; }).join('; ') + '.'
      : 'Your session ended. Sign in again to keep this screen up to date.';
    auth.reauthenticate(message).then(renewed);
  }

  function renewed() {
    signingIn = false;
    items.forEach(function (item) { if (item.state === 'sign-in') item.state = 'queued'; });
    persist();
    render();
    flush();
  }

  document.addEventListener('board:connection', function (event) {
    var state = event.detail && event.detail.state;
    if (state === 'live' || state === 'polling') flush();
  });
  document.addEventListener('session:expired', askToSignIn);
  document.addEventListener('session:renewed', function () { if (signingIn) return; renewed(); });

  // A held action lives in this tab: closing it while something waits asks first. (A reload keeps them.)
  window.addEventListener('beforeunload', function (event) {
    var waiting = items.some(function (item) {
      return item.state === 'queued' || item.state === 'sending' || item.state === 'sign-in';
    });
    if (!waiting) return;
    event.preventDefault();
    event.returnValue = '';
  });

  // ── What the patient buttons call ────────────────────────────────────────────────────────────────

  function add(action, state) {
    var already = items.filter(function (item) {
      return item.signature === action.signature && (item.state === 'queued' || item.state === 'sending' || item.state === 'sign-in');
    })[0];
    if (already) return false;
    items.push({
      id: action.key,
      signature: action.signature,
      url: action.url,
      body: action.body || null,
      key: action.key,
      label: action.label,
      queueId: action.queueId,
      queuedAt: Date.now(),
      state: state,
      via: state,
      message: '',
    });
    persist();
    render();
    return true;
  }

  window.ClinicQOutbox = {
    /** Hold `action` until the page can send it. False when the same action is already held. */
    hold: function (action) {
      var added = add(action, 'queued');
      flush();
      return added;
    },
    /** The session ended while sending `action`: hold it and ask the person to sign in. */
    needsSignIn: function (action) {
      var added = add(action, 'sign-in');
      askToSignIn();
      return added;
    },
  };

  render();
})();
