/* The clinic manager's overrides list (Issue 52).
 *
 * A fully server-rendered list: every override for the day is already a row in the page, so the
 * sort and the row's quick view work on those rows with no request (the second wiring style in
 * docs/IDE/RULES/list-view-ui-pattern.mdc). The filters are a plain GET form, so they stay in the URL.
 *
 * Nothing here changes anything: this page only reads.
 */
(function () {
  'use strict';

  var A = window.BKPAdmin || {};
  var table = document.getElementById('ov-table');
  if (!table) return;
  var body = table.querySelector('tbody');
  var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
  var el = function (id) { return document.getElementById(id); };

  if (A.mountFilterBar) A.mountFilterBar(document.querySelector('.filter-bar'));

  /** Sort keys whose values are numbers; every other key sorts as text. */
  var NUMERIC = { before: true };

  function valueOf(row, key) {
    var raw = row.dataset[key] || '';
    return NUMERIC[key] ? Number(raw) : raw.toLowerCase();
  }

  if (A.mountSortableHeaders) {
    A.mountSortableHeaders(table, {
      onSort: function (key, order) {
        var sorted = rows.slice();
        if (key) {
          var direction = order === 'desc' ? -1 : 1;
          sorted.sort(function (a, b) {
            var left = valueOf(a, key);
            var right = valueOf(b, key);
            return left < right ? -direction : left > right ? direction : 0;
          });
        }
        // No key: the server's order (newest first), which is how `rows` was captured.
        sorted.forEach(function (row) { body.appendChild(row); });
      },
    });
  }

  function show(row) {
    var d = row.dataset;
    el('ovd-title').textContent = d.ticket;
    el('ovd-meta').textContent = 'At ' + d.time;
    el('ovd-queue').textContent = d.queue || '—';
    el('ovd-reason').textContent = d.reason;
    el('ovd-note').textContent = d.note || '—';
    el('ovd-places').textContent = 'From ' + d.before + ' to ' + d.after;
    el('ovd-staff').textContent = d.staff;
    rows.forEach(function (other) { other.classList.toggle('selected', other === row); });
  }

  var detail = A.mountDetailSlideover ? A.mountDetailSlideover('ov-detail', { keepWithin: '#ov-split' }) : null;
  rows.forEach(function (row) {
    function open() {
      show(row);
      if (detail) {
        if (detail.setFullHref) detail.setFullHref(null);
        detail.open();
      }
    }
    row.addEventListener('click', open);
    row.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
      }
    });
  });
})();
