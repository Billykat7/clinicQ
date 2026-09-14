/* The patient buttons: Call next, Start, Done, Recall, No-show and Undo call (Issue 50).
 *
 * Call next is pressed hundreds of times a day by someone talking to a patient at the same time. So a
 * press answers at once, never acts twice, and never leaves the screen and the room disagreeing:
 *
 *   - At once: the card shows the change before the server answers (the waiting count drops, "Calling
 *     T005" appears, a badge reads "Being seen"), and the buttons in play are switched off.
 *   - Never twice: a card with a request in flight ignores further presses, and every press carries an
 *     Idempotency-Key. A request whose answer was lost keeps its key, so pressing again retries the
 *     same action and the server answers with the first result instead of calling a second patient.
 *   - Never disagreeing: when the server refuses, the card is put back exactly as it was and its status
 *     line says why, in the server's words when it gave some. A success asks the live board for the
 *     server's own cards, so what stays on screen is the server's state.
 *
 * When the clinic cannot be reached (offline, an answer that never came, a session that ended), the
 * card is put back and the press goes to dashboard-outbox.js with its key, which sends it when the page
 * is live again or says why it did not: a press never silently vanishes (Issue 55).
 *
 * Which buttons exist, whether they are enabled, and until when a call can be undone all come from the
 * server's markup (dashboard/_queue_card.html). This file holds no rule: it counts the minutes and the
 * undo seconds down for display, and the API decides.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  if (!window.fetch) return;

  var MESSAGE_MS = 12000; // how long a card's status line keeps its message across refreshes
  var RETRY_KEY_MS = 120000; // how long a lost request's key is reused by the same button
  var ELAPSED_TICK_MS = 15000;
  var UNDO_TICK_MS = 1000;

  var dialog = document.getElementById('action-confirm');
  var messages = {}; // queue id -> { text, kind, until }
  var retryKeys = {}; // "url|body" -> { key, until }
  var clock = { asOf: null, offset: null };

  // ── Keys ────────────────────────────────────────────────────────────────────────────────────────

  function freshKey() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }

  /** The key for this press: the lost request's key when retrying it, otherwise a new one. */
  function keyFor(signature) {
    var saved = retryKeys[signature];
    if (saved && saved.until > Date.now()) return saved.key;
    delete retryKeys[signature];
    return freshKey();
  }

  // ── Messages that survive a live refresh ─────────────────────────────────────────────────────────

  function cardFor(queueId) {
    return document.querySelector('[data-queue-id="' + queueId + '"]');
  }

  function showMessage(queueId) {
    var card = cardFor(queueId);
    var slot = card && card.querySelector('[data-action-msg]');
    var message = messages[queueId];
    if (!slot) return;
    if (!message || message.until < Date.now()) {
      delete messages[queueId];
      slot.textContent = '';
      slot.className = 'queue-action-msg';
      return;
    }
    slot.textContent = message.text;
    slot.className = 'queue-action-msg is-' + message.kind;
  }

  function say(queueId, text, kind) {
    messages[queueId] = { text: text, kind: kind, until: Date.now() + MESSAGE_MS };
    showMessage(queueId);
  }

  document.addEventListener('board:card-replaced', function (event) {
    var queueId = event.detail && event.detail.queueId;
    if (queueId) showMessage(queueId);
    tick();
  });

  // ── The refusal, in words ────────────────────────────────────────────────────────────────────────

  function explain(status, data) {
    var detail = data && data.detail;
    if (status === 409 && typeof detail === 'string') return detail + ' Nothing was changed.';
    if (status === 404) {
      return 'This queue or patient is no longer yours to act on (a room change, or they moved on), so nothing was changed.';
    }
    if (status === 403) return 'Your role at this clinic cannot do this, so nothing was changed.';
    if (status === 422) return 'The request was not accepted, so nothing was changed. Reload the page.';
    if (typeof detail === 'string') return detail;
    return 'The clinic could not do this just now, so nothing was changed. Try again.';
  }

  // ── What the card shows while the server answers ─────────────────────────────────────────────────

  function bump(card, name, by) {
    var node = card.querySelector('[data-count="' + name + '"]');
    if (!node) return;
    var value = parseInt(node.textContent, 10);
    if (!isNaN(value)) node.textContent = String(Math.max(0, value + by));
  }

  function optimistic(button, card) {
    var kind = button.getAttribute('data-action');
    if (kind === 'call-next') {
      var number = button.getAttribute('data-next-number');
      bump(card, 'waiting', -1);
      bump(card, 'with-staff', 1);
      button.disabled = true;
      var label = button.querySelector('.queue-call-next-label');
      if (label) label.textContent = 'Calling…';
      var list = card.querySelector('[data-with-staff]');
      if (list) {
        var pending = document.createElement(list.tagName === 'UL' ? 'li' : 'div');
        pending.className = 'with-staff-ticket is-pending';
        pending.setAttribute('data-pending-call', '');
        pending.textContent = number ? 'Calling ' + number + '…' : 'Calling the next patient…';
        list.insertBefore(pending, list.firstChild);
      }
      return;
    }
    var ticket = button.closest('[data-ticket-id]');
    if (!ticket) return;
    ticket.classList.add('is-pending');
    var badge = ticket.querySelector('[data-ticket-badge]');
    if (badge) badge.textContent = button.getAttribute('data-pending') || badge.textContent;
    ticket.querySelectorAll('.ticket-actions button').forEach(function (other) { other.disabled = true; });
    if (kind === 'undo') bump(card, 'waiting', 1);
  }

  function signatureOf(button) {
    return button.getAttribute('data-action-url') + '|' + (button.getAttribute('data-action-body') || '');
  }

  /** The button in `card` that sends what `button` sent, or null. */
  function sameButton(card, button) {
    var marker = signatureOf(button);
    return Array.prototype.find.call(card.querySelectorAll('button[data-action]'), function (candidate) {
      return signatureOf(candidate) === marker;
    }) || null;
  }

  /** Put the card back as it was before the press, keeping focus on the same button if it had it. */
  function rollback(card, snapshot, button) {
    var hadFocus = document.activeElement === button || card.contains(document.activeElement);
    snapshot.removeAttribute('data-action-busy');
    card.replaceWith(snapshot);
    if (!hadFocus) return;
    var again = sameButton(snapshot, button);
    if (again) again.focus({ preventScroll: true });
  }

  function successText(button, data) {
    var kind = button.getAttribute('data-action');
    var number = data && data.number;
    if (kind === 'call-next') return number ? 'Called ' + number + '.' : 'Called.';
    if (kind === 'undo') return number ? number + ' is back in the line, in the same place.' : 'The call was undone.';
    var ticket = button.closest('[data-ticket-id]');
    var name = number || (ticket && ticket.getAttribute('data-number')) || 'The ticket';
    return name + ': ' + (button.getAttribute('data-pending') || 'saved') + '.';
  }

  function refreshCards() {
    document.dispatchEvent(new CustomEvent('board:refresh'));
  }

  // ── Sending ──────────────────────────────────────────────────────────────────────────────────────

  /** What the action is, in words, for the outbox and its messages: "Call next in Triage". */
  function labelOf(button, card) {
    var queue = card.querySelector('.queue-card-name, .room-head h2');
    var queueName = queue ? queue.textContent.trim() : 'this queue';
    var kind = button.getAttribute('data-action');
    if (kind === 'call-next') return 'Call next in ' + queueName;
    var ticket = button.closest('[data-ticket-id]');
    var number = ticket ? ticket.getAttribute('data-number') : '';
    if (kind === 'undo') return 'Undo the call of ' + number;
    return button.textContent.trim() + ' for ' + number;
  }

  /** The card cannot send now: put it back as it was and hand the action to the outbox. */
  function holdForLater(button, card, snapshot, action, reason) {
    rollback(card, snapshot, button);
    var outbox = window.ClinicQOutbox;
    if (!outbox) {
      // A page without the outbox: keep the key, so pressing again asks about the same action.
      retryKeys[action.signature] = { key: action.key, until: Date.now() + RETRY_KEY_MS };
      say(action.queueId, 'The clinic could not be reached, so this may not have gone through. Press again to retry: it will not be done twice.', 'error');
      return;
    }
    if (reason === 'sign-in') {
      outbox.needsSignIn(action);
      say(action.queueId, 'Your session ended. Sign in again, and ' + action.label + ' will be sent.', 'muted');
    } else if (outbox.hold(action)) {
      say(action.queueId, action.label + ' is waiting for the connection and will be sent when the clinic is back.', 'muted');
    } else {
      say(action.queueId, action.label + ' is already waiting for the connection.', 'muted');
    }
  }

  function send(button, card) {
    var queueId = card.getAttribute('data-queue-id');
    var url = button.getAttribute('data-action-url');
    var body = button.getAttribute('data-action-body');
    var signature = signatureOf(button);
    var key = keyFor(signature);
    var snapshot = card.cloneNode(true);
    var action = { signature: signature, key: key, url: url, body: body, queueId: queueId, label: labelOf(button, card) };
    snapshot.removeAttribute('data-action-busy');

    // Offline: nothing can reach the clinic, so do not pretend. Hold it, and say so.
    if (window.ClinicQLive && !window.ClinicQLive.reachable()) {
      card.removeAttribute('data-action-busy');
      holdForLater(button, card, snapshot, action, 'offline');
      return;
    }

    optimistic(button, card);
    say(queueId, 'Sending…', 'muted');

    var headers = window.BKP && window.BKP.writeHeaders ? window.BKP.writeHeaders() : { 'Content-Type': 'application/json' };
    headers['Idempotency-Key'] = key;
    var init = { method: 'POST', credentials: 'same-origin', headers: headers };
    if (body) init.body = body;

    fetch(url, init)
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          delete retryKeys[signature];
          if (response.status === 401) {
            // The session ended (session-refresh.js could not renew it): sign in again in place.
            holdForLater(button, card, snapshot, action, 'sign-in');
            return;
          }
          if (response.ok) {
            card.removeAttribute('data-action-busy');
            say(queueId, successText(button, data), 'ok');
            refreshCards();
            return;
          }
          rollback(card, snapshot, button);
          say(queueId, explain(response.status, data), 'error');
          refreshCards();
        });
      })
      .catch(function () {
        // The answer never came: the action may or may not have happened. The outbox sends it again
        // with the same key when the clinic is reachable, and the server does not do it twice.
        holdForLater(button, card, snapshot, action, 'unreachable');
        refreshCards();
      });
  }

  document.addEventListener('outbox:settled', function (event) {
    var detail = event.detail || {};
    if (detail.queueId) say(detail.queueId, detail.text, detail.kind);
  });

  function confirmFirst(text) {
    if (!text) return Promise.resolve(true);
    if (!dialog || typeof dialog.showModal !== 'function') return Promise.resolve(window.confirm(text));
    document.getElementById('action-confirm-text').textContent = text;
    dialog.returnValue = '';
    dialog.showModal();
    return new Promise(function (resolve) {
      dialog.addEventListener('close', function done() {
        dialog.removeEventListener('close', done);
        resolve(dialog.returnValue === 'confirm');
      });
    });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('button[data-action]');
    if (!button || button.disabled) return;
    var card = button.closest('[data-queue-id]');
    if (!card) return;
    event.preventDefault();
    // One request per card at a time: a second tap, however fast, lands here and is ignored.
    if (card.hasAttribute('data-action-busy')) return;
    card.setAttribute('data-action-busy', '');
    var question = button.getAttribute('data-confirm');
    confirmFirst(question).then(function (yes) {
      if (!yes) {
        card.removeAttribute('data-action-busy');
        button.focus({ preventScroll: true });
        return;
      }
      // The card may have been replaced while the question was open: act on the one in the page.
      var current = cardFor(card.getAttribute('data-queue-id')) || card;
      var target = button.isConnected ? button : sameButton(current, button);
      if (!target) {
        current.removeAttribute('data-action-busy');
        say(current.getAttribute('data-queue-id'), 'That patient has moved on since you pressed, so nothing was changed.', 'error');
        return;
      }
      current.setAttribute('data-action-busy', '');
      send(target, current);
    });
  });

  // ── Counting for display: minutes at a step, seconds left to undo ────────────────────────────────

  /** The server's clock: this device's, corrected by how far apart the two were when cards arrived.
   *
   * Each new set of cards says when the server read them; the page sees that moment a little later
   * (the time the answer took), so `asOf - now` understates the gap by that delay. The largest gap seen
   * is the closest to the truth, and a tablet whose clock is minutes out still counts correctly. */
  function serverNow() {
    var root = document.getElementById('board-cards');
    var asOf = root ? Date.parse(root.getAttribute('data-as-of')) : NaN;
    if (!isNaN(asOf) && asOf !== clock.asOf) {
      clock.asOf = asOf;
      var gap = asOf - Date.now();
      clock.offset = clock.offset === null ? gap : Math.max(clock.offset, gap);
    }
    return Date.now() + (clock.offset || 0);
  }

  function tickElapsed() {
    var now = serverNow();
    document.querySelectorAll('[data-since]').forEach(function (node) {
      var since = Date.parse(node.getAttribute('data-since'));
      if (isNaN(since)) return;
      node.textContent = Math.max(0, Math.floor((now - since) / 60000)) + ' min';
    });
  }

  function tickUndo() {
    var now = serverNow();
    document.querySelectorAll('button[data-undo-until]').forEach(function (button) {
      var until = Date.parse(button.getAttribute('data-undo-until'));
      if (isNaN(until)) return;
      var left = Math.ceil((until - now) / 1000);
      if (left <= 0) {
        // The server would refuse now; the button goes, and the next refresh agrees.
        button.hidden = true;
        return;
      }
      button.textContent = 'Undo call (' + left + ' s)';
    });
  }

  function tick() {
    tickElapsed();
    tickUndo();
  }

  window.setInterval(tickElapsed, ELAPSED_TICK_MS);
  window.setInterval(tickUndo, UNDO_TICK_MS);
  tick();
})();
