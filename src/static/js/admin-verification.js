/* The clinic-verification console (Issue 29).
 *
 * A fully server-rendered list: every row for this tab is already in the DOM, so sorting and the
 * row quick-view work against the rows themselves with no network call, which is the second of the
 * two wiring styles in .cursor/rules/list-view-ui-pattern.mdc. The filter bar submits as a real
 * navigation, which is what keeps the filtered state in the URL.
 *
 * The decision is the one thing that is a request, because it changes something.
 */
(function () {
  const A = window.BKPAdmin || {};
  const table = document.getElementById('vq-table');
  if (!table) return;
  const el = (id) => document.getElementById(id);
  let current = null;

  if (A.mountFilterBar) A.mountFilterBar(document.querySelector('.filter-bar'));
  if (A.mountSortableHeaders) {
    A.mountSortableHeaders(table, {
      value: (row, key) => row.dataset[key] || '',
    });
  }

  function show(row) {
    current = row;
    const d = row.dataset;
    el('vqd-title').textContent = d.name;
    el('vqd-meta').textContent = d.submitted
      ? `Submitted ${new Date(d.submitted).toLocaleDateString('en-ZA', { timeZone: 'Africa/Johannesburg' })}`
      : 'Not submitted through the public form';
    el('vqd-slug').textContent = d.slug;
    el('vqd-where').textContent = [d.suburb, d.city, d.province].filter(Boolean).join(', ');
    el('vqd-contact').textContent =
      [d.contactName, d.contactEmail, d.contactPhone].filter(Boolean).join(' · ') || '—';
    el('vqd-status').textContent = d.status;
    el('vqd-note').textContent = d.note || '—';
    el('vqd-msg').textContent = '';
  }

  /* The panel is the kernel's: ``mountDetailSlideover`` gives it the anchored geometry, the
     head/foot chrome and the outside-click dismissal every other console has, so this file only
     decides *what goes in it* and never how it opens. */
  const detail = A.mountDetailSlideover
    ? A.mountDetailSlideover('vq-detail', { keepWithin: '#vq-split' })
    : null;
  table.querySelectorAll('tbody tr').forEach((row) => {
    row.addEventListener('click', () => {
      show(row);
      if (detail) {
        if (detail.setFullHref) {
          detail.setFullHref(`/dashboard/sites/${row.dataset.id}/settings/display`);
        }
        detail.open();
      }
    });
  });

  async function decide(status) {
    if (!current) return;
    const message = el('vqd-msg');
    message.textContent = 'Saving…';
    const response = await fetch(`/api/v1/sites/${current.dataset.id}/verification`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: window.BKP.writeHeaders(),
      body: JSON.stringify({ status, note: el('vqd-decision-note').value || null }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      message.textContent =
        typeof body.detail === 'string'
          ? body.detail
          : 'That decision could not be saved.';
      message.className = 'msg msg-error';
      return;
    }
    /* The row has left this tab, so reload rather than patching it in place: what an admin wants
       to see next is the rest of the queue. */
    window.location.reload();
  }

  ['vqd-approve', 'vqd-more', 'vqd-suspend'].forEach((id) => {
    const button = el(id);
    if (button) button.addEventListener('click', () => decide(button.dataset.status));
  });
})();
