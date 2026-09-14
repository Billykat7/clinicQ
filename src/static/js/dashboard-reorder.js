/* Moving a waiting patient forward from the front desk: drag, or tap "Move forward" (Issue 52).
 *
 * Two gestures, one request. Dragging a ticket above another and tapping its "Move forward" button
 * both call openMove(ticket, callBefore), which opens the same reason prompt; saving it sends the same
 * body to the same endpoint:
 *
 *   POST /api/v1/sites/{site}/tickets/{ticket}/priority
 *   { "ahead_of_ticket_id": "...", "reason": "...", "note": "..." }
 *
 * So a move made either way lands identically, because it is the same move.
 *
 * No rule of an override lives here. Whether a reason is required, whether the patient may be placed
 * before that ticket, and whether this person may move anyone at all are the server's decisions
 * (Issue 46). A move the server refuses changes nothing on the page: its answer is shown in the prompt
 * and the line stays as it was. A move it accepts reloads the board, so the line, the badge and the
 * trail on screen are the server's, not a guess.
 *
 * A line rendered for someone without the permission carries data-reorder-disabled: its handles and
 * buttons are disabled in the markup, and nothing here attaches to it.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var dialog = document.getElementById('move-dialog');
  if (!dialog) return;
  // The lines are replaced whenever the live board refreshes (Issue 49), so every listener is on the
  // document and finds its line when the event happens, never on a line that may be gone.
  var ENABLED = '[data-reorder-list]:not([data-reorder-disabled])';

  var OPEN_KEY = 'clinicq.board.openLine';
  var form = document.getElementById('move-form');
  var select = document.getElementById('move-ahead');
  var note = document.getElementById('move-note');
  var message = document.getElementById('move-msg');
  var saveButton = document.getElementById('move-save');
  var numberSlot = dialog.querySelector('[data-move-number]');
  var siteId = dialog.getAttribute('data-site-id');
  var moving = null;

  function ticketsBefore(item) {
    var before = [];
    for (var node = item.parentElement.firstElementChild; node && node !== item; node = node.nextElementSibling) {
      before.push(node);
    }
    return before;
  }

  /** Open the reason prompt for moving `item` so it is called before `callBefore`. */
  function openMove(item, callBefore) {
    var candidates = ticketsBefore(item);
    if (!candidates.length || !callBefore) return;
    moving = item;
    numberSlot.textContent = item.getAttribute('data-number');
    select.innerHTML = '';
    candidates.forEach(function (candidate) {
      var option = document.createElement('option');
      option.value = candidate.getAttribute('data-ticket-id');
      option.textContent = candidate.getAttribute('data-place') + '. ' + candidate.getAttribute('data-number');
      option.selected = candidate === callBefore;
      select.appendChild(option);
    });
    form.querySelectorAll('input[name="reason"]').forEach(function (radio) { radio.checked = false; });
    note.value = '';
    message.textContent = '';
    message.className = 'msg';
    saveButton.disabled = false;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    var firstReason = form.querySelector('input[name="reason"]');
    if (firstReason) firstReason.focus();
  }

  function closeMove() {
    moving = null;
    if (dialog.open) dialog.close();
  }

  /** The words for a refused move: the server's own sentence, or the missing reason by name. */
  function refusalText(status, body) {
    var detail = body && body.detail;
    if (Array.isArray(detail) && detail.some(function (item) {
      return Array.isArray(item.loc) && item.loc.indexOf('reason') >= 0;
    })) {
      return 'Choose a reason. A move without one is not saved.';
    }
    if (typeof detail === 'string') return detail;
    if (status === 403) return 'You do not have permission to move patients at this clinic.';
    return 'The move was not saved. Try again.';
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (!moving) return;
    var chosen = form.querySelector('input[name="reason"]:checked');
    var body = { ahead_of_ticket_id: select.value };
    // An unchosen reason is sent as absent, exactly as the prompt left it: the server refuses it.
    if (chosen) body.reason = chosen.value;
    if (note.value.trim()) body.note = note.value.trim();
    var ticketId = moving.getAttribute('data-ticket-id');
    var queueId = moving.parentElement.getAttribute('data-queue-id');
    saveButton.disabled = true;
    message.textContent = 'Saving…';
    message.className = 'msg msg-muted';
    fetch('/api/v1/sites/' + encodeURIComponent(siteId) + '/tickets/' + encodeURIComponent(ticketId) + '/priority', {
      method: 'POST',
      credentials: 'same-origin',
      headers: window.BKP.writeHeaders(),
      body: JSON.stringify(body),
    })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
      })
      .then(function (result) {
        if (result.ok) {
          if (document.getElementById('board-connection')) {
            // The live board (Issue 49) reads the cards again, keeping this line open.
            closeMove();
            document.dispatchEvent(new CustomEvent('board:refresh'));
            return;
          }
          try { window.sessionStorage.setItem(OPEN_KEY, queueId); } catch (e) { /* private mode */ }
          window.location.reload();
          return;
        }
        saveButton.disabled = false;
        message.textContent = refusalText(result.status, result.data);
        message.className = 'msg msg-error';
      })
      .catch(function () {
        saveButton.disabled = false;
        message.textContent = 'The clinic could not be reached, so the move was not saved.';
        message.className = 'msg msg-error';
      });
  });

  dialog.querySelectorAll('[data-move-cancel]').forEach(function (button) {
    button.addEventListener('click', closeMove);
  });
  dialog.addEventListener('close', function () { moving = null; });

  var dragged = null;

  function clearMarks() {
    document.querySelectorAll('.is-drop-target, .is-dragging').forEach(function (node) {
      node.classList.remove('is-drop-target', 'is-dragging');
    });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest(ENABLED + ' [data-move-ticket]');
    if (!button || button.disabled) return;
    var item = button.closest('.line-ticket');
    // Tapping moves one place forward by default; the prompt lets the person pick any earlier ticket.
    openMove(item, item.previousElementSibling);
  });

  document.addEventListener('dragstart', function (event) {
    var item = event.target.closest && event.target.closest(ENABLED + ' .line-ticket');
    if (!item) return;
    dragged = item;
    dragged.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', dragged.getAttribute('data-ticket-id'));
  });

  document.addEventListener('dragover', function (event) {
    var over = event.target.closest && event.target.closest(ENABLED + ' .line-ticket');
    // A drop target is a ticket above the dragged one in the same line: this control moves forward.
    if (!dragged || !over || over === dragged || ticketsBefore(dragged).indexOf(over) < 0) return;
    event.preventDefault();
    document.querySelectorAll('.is-drop-target').forEach(function (node) { node.classList.remove('is-drop-target'); });
    over.classList.add('is-drop-target');
  });

  document.addEventListener('drop', function (event) {
    var over = event.target.closest && event.target.closest(ENABLED + ' .line-ticket');
    var item = dragged;
    if (!item) return;
    event.preventDefault();
    clearMarks();
    dragged = null;
    if (over && ticketsBefore(item).indexOf(over) >= 0) openMove(item, over);
  });

  document.addEventListener('dragend', function () {
    clearMarks();
    dragged = null;
  });

  // After a saved move the board reloads; reopen the line the person was working in.
  try {
    var reopen = window.sessionStorage.getItem(OPEN_KEY);
    if (reopen) {
      window.sessionStorage.removeItem(OPEN_KEY);
      var line = document.querySelector('[data-queue-line="' + reopen + '"]');
      if (line) line.open = true;
    }
  } catch (e) { /* private mode */ }
})();
