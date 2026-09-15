/* The patient message templates console (Issue 66).
 *
 * A fully server-rendered list: every row for this channel is already in the page, so sorting and the row
 * quick view work on the rows themselves with no network call (the second wiring style in
 * docs/IDE/RULES/list-view-ui-pattern.mdc). The filter bar submits as a real navigation, which keeps the
 * filter in the URL. The slideover's full view is the editor, where the words are changed.
 */
(function () {
  const A = window.BKPAdmin || {};
  const table = document.getElementById('nt-table');
  if (!table) return;
  const el = (id) => document.getElementById(id);

  if (A.mountFilterBar) A.mountFilterBar(document.querySelector('.filter-bar'));
  if (A.mountSortableHeaders) {
    A.mountSortableHeaders(table, {
      value: (row, key) => (key === 'version' ? Number(row.dataset.version) : row.dataset[key] || ''),
    });
  }

  function show(row) {
    const d = row.dataset;
    el('ntd-title').textContent = d.template;
    el('ntd-meta').textContent = `${d.language}, version ${d.version}, ${d.source === 'edited' ? 'edited' : 'from the locale file'}`;
    el('ntd-subject').textContent = d.subject || '—';
    el('ntd-body').textContent = d.body;
    el('ntd-reviewed').textContent = d.reviewed || '—';
  }

  const detail = A.mountDetailSlideover ? A.mountDetailSlideover('nt-detail', { keepWithin: '#nt-split' }) : null;
  table.querySelectorAll('tbody tr').forEach((row) => {
    const open = () => {
      show(row);
      if (detail) {
        if (detail.setFullHref) detail.setFullHref(row.dataset.edit);
        detail.open();
      }
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') open();
    });
  });
})();
