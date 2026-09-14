/* The operator's waiting-room screen console (Issue 61).
 *
 * A fully server-rendered list: every row for this tab is already in the DOM, so sorting and the row
 * quick view work on the rows themselves with no network call (the second wiring style in
 * docs/IDE/RULES/list-view-ui-pattern.mdc). The filter bar submits as a real navigation, which keeps the
 * filter in the URL. Nothing here changes a screen.
 */
(function () {
  const A = window.BKPAdmin || {};
  const table = document.getElementById('dd-table');
  if (!table) return;
  const el = (id) => document.getElementById(id);
  const when = (iso) =>
    iso
      ? new Date(iso).toLocaleString('en-ZA', { timeZone: 'Africa/Johannesburg', dateStyle: 'medium', timeStyle: 'short' })
      : '—';
  const words = { online: 'Showing the board', silent: 'Not heard from', revoked: 'Removed' };

  if (A.mountFilterBar) A.mountFilterBar(document.querySelector('.filter-bar'));
  if (A.mountSortableHeaders) {
    A.mountSortableHeaders(table, { value: (row, key) => row.dataset[key] || '' });
  }

  function show(row) {
    const d = row.dataset;
    el('ddd-title').textContent = d.label;
    el('ddd-clinic').textContent = d.clinic;
    el('ddd-status').textContent = words[d.status] || d.status;
    el('ddd-seen').textContent = when(d.seen);
    el('ddd-paired').textContent = when(d.paired);
    el('ddd-version').textContent = d.version || '—';
    el('ddd-agent').textContent = d.agent || '—';
  }

  const detail = A.mountDetailSlideover ? A.mountDetailSlideover('dd-detail', { keepWithin: '#dd-split' }) : null;
  table.querySelectorAll('tbody tr').forEach((row) => {
    const open = () => {
      show(row);
      if (detail) {
        if (detail.setFullHref) detail.setFullHref(`/dashboard/sites/${row.dataset.siteId}/settings/devices`);
        detail.open();
      }
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') open();
    });
  });
})();
