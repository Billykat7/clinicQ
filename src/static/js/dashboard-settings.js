/* The clinic settings screens' one piece of plumbing (Issue 54).
 *
 * Every change on these screens is a request to an API route that already exists and already
 * decides: the grant, the validation and the audit row are the server's. So this file has no rule
 * in it. It turns declared markup into requests and shows the server's answers:
 *
 *   <form data-api="/api/v1/sites/{site}/queues" data-method="POST">   send the form as JSON
 *   <button data-api="…" data-method="DELETE" data-confirm="…">       send a request, no body
 *   data-confirm="Deactivate Triage? …"                                 ask first, in these words
 *   data-api-template="/api/v1/sites/S/queues/{id}"                     filled from the open record
 *
 * Field names map to JSON keys (dots nest: location.latitude). A field says how to read it:
 * data-type="number" (empty is null), data-type="list" (checked values of a group), a checkbox is
 * true or false, and data-empty="null" sends an empty text as null. Anything else is its string.
 *
 * A success reloads the page, so what is on screen afterwards is the server's state and not a
 * guess at it. A refusal is shown beside the control that asked, in the server's words.
 *
 * Lists on these screens are server-rendered (docs/IDE/RULES/list-view-ui-pattern.mdc): the filter
 * bar is a GET form, headers marked data-sort-key sort the rows in place, and a row click opens the
 * record in the slideover beside the list, its form filled from the row's data-record.
 *
 * External file, no inline handlers or styles: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var A = window.BKPAdmin || {};
  var dialog = document.getElementById('confirm-dialog');

  /** Ask the person to confirm `text`; resolves true only for an explicit yes. */
  function confirmFirst(text) {
    if (!text) return Promise.resolve(true);
    if (!dialog || typeof dialog.showModal !== 'function') return Promise.resolve(window.confirm(text));
    document.getElementById('confirm-text').textContent = text;
    dialog.returnValue = '';
    dialog.showModal();
    return new Promise(function (resolve) {
      dialog.addEventListener('close', function done() {
        dialog.removeEventListener('close', done);
        resolve(dialog.returnValue === 'confirm');
      });
    });
  }

  function setPath(target, path, value) {
    var keys = path.split('.');
    var node = target;
    for (var i = 0; i < keys.length - 1; i += 1) {
      node[keys[i]] = node[keys[i]] || {};
      node = node[keys[i]];
    }
    node[keys[keys.length - 1]] = value;
  }

  function getPath(source, path) {
    return path.split('.').reduce(function (node, key) {
      return node === null || node === undefined ? undefined : node[key];
    }, source);
  }

  /** The form as the JSON body the API expects. */
  function serialize(form) {
    var body = {};
    var lists = {};
    Array.prototype.forEach.call(form.elements, function (field) {
      if (!field.name || field.disabled || field.type === 'submit' || field.type === 'button') return;
      var type = field.getAttribute('data-type');
      if (type === 'list') {
        lists[field.name] = lists[field.name] || [];
        if (field.checked) lists[field.name].push(field.value);
        return;
      }
      if (field.type === 'radio') {
        if (field.checked) setPath(body, field.name, field.value);
        return;
      }
      if (field.type === 'checkbox') {
        setPath(body, field.name, field.checked);
        return;
      }
      var value = field.value;
      if (type === 'number') {
        setPath(body, field.name, value.trim() === '' ? null : Number(value));
      } else if (value.trim() === '' && field.getAttribute('data-empty') === 'null') {
        setPath(body, field.name, null);
      } else {
        setPath(body, field.name, value);
      }
    });
    Object.keys(lists).forEach(function (name) { setPath(body, name, lists[name]); });
    return body;
  }

  /** The weekly hours form: each weekday's filled spans, in the shape PUT /hours takes. */
  function serializeHours(form) {
    var days = [];
    form.querySelectorAll('[data-weekday]').forEach(function (row) {
      var closed = row.querySelector('[data-day-closed]');
      var spans = [];
      if (!closed || !closed.checked) {
        row.querySelectorAll('[data-span]').forEach(function (span) {
          var opens = span.querySelector('[data-opens]').value;
          var closes = span.querySelector('[data-closes]').value;
          if (opens && closes) spans.push({ opens_at: opens, closes_at: closes });
        });
      }
      days.push({ weekday: Number(row.getAttribute('data-weekday')), spans: spans });
    });
    return { days: days };
  }

  /** The server's refusal in words: its sentence, or the fields its validation named. */
  function refusal(status, data) {
    var detail = data && data.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map(function (item) {
        var loc = Array.isArray(item.loc) ? item.loc.filter(function (part) { return part !== 'body'; }).join('.') : '';
        return (loc ? loc + ': ' : '') + item.msg;
      }).join(' ');
    }
    if (status === 403) return 'You do not have permission to change this at this clinic.';
    if (status === 404) return 'That is no longer here. Reload the page.';
    return 'The change was not saved. Try again.';
  }

  function messageFor(control) {
    var scope = control.closest('form, [data-api-scope], .slideover-panel') || control.parentElement;
    return scope.querySelector('[data-api-msg]');
  }

  function say(slot, text, kind) {
    if (!slot) return;
    slot.textContent = text;
    slot.className = 'msg' + (kind ? ' msg-' + kind : '');
  }

  function send(control, method, url, body) {
    var slot = messageFor(control);
    return confirmFirst(control.getAttribute('data-confirm')).then(function (yes) {
      if (!yes) return;
      say(slot, 'Saving…', 'muted');
      var init = { method: method, credentials: 'same-origin', headers: window.BKP.writeHeaders() };
      if (body !== undefined) init.body = JSON.stringify(body);
      return fetch(url, init)
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (data) {
            if (response.ok) {
              say(slot, 'Saved.', 'ok');
              window.location.reload();
              return;
            }
            say(slot, refusal(response.status, data), 'error');
          });
        })
        .catch(function () {
          say(slot, 'The clinic could not be reached, so nothing was saved.', 'error');
        });
    });
  }

  function urlOf(control) {
    return control.getAttribute('data-api') || control.getAttribute('data-api-url') || '';
  }

  document.addEventListener('submit', function (event) {
    var form = event.target.closest('form[data-api], form[data-api-url]');
    if (!form) return;
    event.preventDefault();
    var body = form.getAttribute('data-serializer') === 'hours' ? serializeHours(form) : serialize(form);
    send(form, (form.getAttribute('data-method') || 'POST').toUpperCase(), urlOf(form), body);
  });

  document.addEventListener('click', function (event) {
    var button = event.target.closest('button[data-api], button[data-api-url]');
    if (!button || button.form && button.type === 'submit') return;
    event.preventDefault();
    var raw = button.getAttribute('data-body');
    send(button, (button.getAttribute('data-method') || 'POST').toUpperCase(), urlOf(button), raw ? JSON.parse(raw) : undefined);
  });

  // ── Server-rendered lists: sort in place, open a row in the slideover ───────────────────────

  document.querySelectorAll('table[data-settings-list]').forEach(function (table) {
    var body = table.querySelector('tbody');
    var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
    if (A.mountSortableHeaders) {
      A.mountSortableHeaders(table, {
        onSort: function (key, order) {
          var sorted = rows.slice();
          if (key) {
            var direction = order === 'desc' ? -1 : 1;
            sorted.sort(function (a, b) {
              var left = a.getAttribute('data-sort-' + key) || '';
              var right = b.getAttribute('data-sort-' + key) || '';
              var numeric = !isNaN(Number(left)) && !isNaN(Number(right)) && left !== '' && right !== '';
              if (numeric) { left = Number(left); right = Number(right); }
              else { left = left.toLowerCase(); right = right.toLowerCase(); }
              return left < right ? -direction : left > right ? direction : 0;
            });
          }
          sorted.forEach(function (row) { body.appendChild(row); });
        },
      });
    }

    var panelId = table.getAttribute('data-settings-list');
    var panel = document.getElementById(panelId);
    if (!panel) return;
    var detail = A.mountDetailSlideover
      ? A.mountDetailSlideover(panelId, { keepWithin: table.getAttribute('data-keep-within') || null })
      : null;

    /** Fill the slideover for `record` (null for a new one): fields, urls, and what shows when. */
    function fill(record) {
      var isNew = !record;
      panel.querySelectorAll('[data-show-when]').forEach(function (node) {
        var when = node.getAttribute('data-show-when');
        var roles = record && Array.isArray(record.roles) ? record.roles : [];
        node.hidden = !(
          (when === 'new' && isNew) ||
          (when === 'existing' && !isNew) ||
          (when === 'active' && record && record.is_active) ||
          (when === 'inactive' && record && !record.is_active) ||
          (when.indexOf('has:') === 0 && roles.indexOf(when.slice(4)) >= 0) ||
          (when.indexOf('lacks:') === 0 && record && roles.indexOf(when.slice(6)) < 0)
        );
      });
      panel.querySelectorAll('[data-fill-text]').forEach(function (node) {
        var value = record ? getPath(record, node.getAttribute('data-fill-text')) : '';
        node.textContent = value === null || value === undefined || value === '' ? (node.getAttribute('data-fill-empty') || '') : value;
      });
      panel.querySelectorAll('[data-api-template]').forEach(function (control) {
        var template = control.getAttribute(isNew && control.hasAttribute('data-api-new') ? 'data-api-new' : 'data-api-template');
        control.setAttribute('data-api-url', record ? template.replace(/\{(\w+)\}/g, function (_, key) { return encodeURIComponent(record[key]); }) : template);
        if (control.hasAttribute('data-method-new')) {
          control.setAttribute('data-method', isNew ? control.getAttribute('data-method-new') : control.getAttribute('data-method-existing'));
        }
        if (control.hasAttribute('data-confirm-template') && record) {
          control.setAttribute('data-confirm', control.getAttribute('data-confirm-template').replace(/\{(\w+)\}/g, function (_, key) { return record[key]; }));
        }
      });
      panel.querySelectorAll('form').forEach(function (form) {
        if (isNew) form.reset();
        Array.prototype.forEach.call(form.elements, function (field) {
          if (!field.name || isNew) return;
          var value = getPath(record, field.name);
          if (field.getAttribute('data-type') === 'list') {
            field.checked = Array.isArray(value) && value.indexOf(field.value) >= 0;
          } else if (field.type === 'checkbox') {
            field.checked = Boolean(value);
          } else if (field.type === 'radio') {
            field.checked = String(value) === field.value;
          } else {
            field.value = value === null || value === undefined ? '' : String(value);
          }
        });
      });
      panel.querySelectorAll('[data-api-msg]').forEach(function (slot) { say(slot, '', ''); });
    }

    function open(row) {
      rows.forEach(function (other) { other.classList.toggle('selected', other === row); });
      fill(row ? JSON.parse(row.getAttribute('data-record')) : null);
      if (detail) {
        if (detail.setFullHref) detail.setFullHref(null);
        detail.open();
      }
    }

    rows.forEach(function (row) {
      row.addEventListener('click', function (event) {
        if (event.target.closest('button, a, input, select')) return;
        open(row);
      });
      row.addEventListener('keydown', function (event) {
        if (event.target !== row || (event.key !== 'Enter' && event.key !== ' ')) return;
        event.preventDefault();
        open(row);
      });
    });
    document.querySelectorAll('[data-new-record="' + panelId + '"]').forEach(function (button) {
      button.addEventListener('click', function () { open(null); });
    });
  });

  // ── Queue order: move a row, then save the order the rows now show ─────────────────────────────

  document.querySelectorAll('[data-order-list]').forEach(function (table) {
    var body = table.querySelector('tbody');
    var saveButton = document.getElementById(table.getAttribute('data-order-save'));
    table.addEventListener('click', function (event) {
      var button = event.target.closest('[data-order-move]');
      if (!button) return;
      var row = button.closest('tr');
      if (button.getAttribute('data-order-move') === 'up' && row.previousElementSibling) {
        body.insertBefore(row, row.previousElementSibling);
      } else if (button.getAttribute('data-order-move') === 'down' && row.nextElementSibling) {
        body.insertBefore(row.nextElementSibling, row);
      }
      if (saveButton) {
        saveButton.hidden = false;
        var ids = Array.prototype.map.call(body.querySelectorAll('tr'), function (tr) {
          return JSON.parse(tr.getAttribute('data-record')).id;
        });
        saveButton.setAttribute('data-body', JSON.stringify({ queue_ids: ids }));
      }
      button.focus();
    });
  });

  if (A.mountFilterBar) {
    document.querySelectorAll('.filter-bar').forEach(function (bar) { A.mountFilterBar(bar); });
  }
})();
