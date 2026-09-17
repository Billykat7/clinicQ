/* The platform admin's clinics console (Issue 222).
 *
 * A fully server-rendered list: every row for this tab is already in the DOM, so sorting and the row
 * quick-view work against the rows themselves with no network call, which is the second of the two
 * wiring styles in docs/IDE/RULES/list-view-ui-pattern.mdc. The filter bar submits as a real
 * navigation, which is what keeps the filtered state in the URL.
 *
 * The writes are real requests, because they change something:
 *   - POST   /api/v1/sites            create, always a draft whatever is asked for
 *   - PUT    /api/v1/sites/{id}/directory   correct the entry (the operator's, business tier)
 *   - DELETE /api/v1/sites/{id}       archive (a soft delete)
 *   - POST   /api/v1/sites/geocode    address → coordinate, for a new clinic
 *   - POST   /api/v1/sites/{id}/geocode                     … for one that exists
 * Every refusal shown is the API's own sentence, so this page cannot disagree with the API about
 * why something was not allowed — and the API re-checks the grant on all of them, whatever the
 * buttons here say.
 */
(function () {
  const A = window.BKPAdmin || {};
  const table = document.getElementById('cl-table');
  if (!table) return;
  const el = (id) => document.getElementById(id);
  const form = el('cl-form');
  const msg = el('cld-msg');
  const panel = el('cl-detail');
  const can = A.verdicts ? A.verdicts(panel) : { can: () => false };

  /* The clinic on screen: a row's dataset when one was clicked, null while creating a new one. */
  let current = null;

  const FIELDS = [
    ['cld-name', 'name'],
    ['cld-slug', 'slug'],
    ['cld-sector', 'sector'],
    ['cld-address', 'address'],
    ['cld-suburb', 'suburb'],
    ['cld-city', 'city'],
    ['cld-province', 'province'],
    ['cld-postal', 'postal'],
    ['cld-phone', 'phone'],
    ['cld-notes', 'notes'],
    ['cld-lat', 'latitude'],
    ['cld-lng', 'longitude'],
  ];

  if (A.mountFilterBar) A.mountFilterBar(document.querySelector('.filter-bar'));
  if (A.mountSortableHeaders) {
    A.mountSortableHeaders(table, { value: (row, key) => row.dataset[key] || '' });
  }

  const detail = A.mountDetailSlideover
    ? A.mountDetailSlideover('cl-detail', { keepWithin: '#cl-split' })
    : null;

  /** Read-only until Edit, so a stray keystroke cannot change a live clinic's address. */
  function setEditing(on) {
    FIELDS.forEach(([id]) => {
      const field = el(id);
      if (field) field.disabled = !on;
    });
    el('cl-geocode').disabled = !on;
    el('cl-save').hidden = !on;
    el('cl-cancel').hidden = !on;
    el('cl-edit').hidden = on;
    const archive = el('cl-archive');
    if (archive) archive.hidden = on || !current;
    el('cl-links').hidden = on || !current;
  }

  function fill(data) {
    FIELDS.forEach(([id, key]) => {
      const field = el(id);
      if (!field) return;
      /* A <select> has no empty option — every clinic is public or private and is in one of the
         nine provinces — so a missing value means "the first one", not a blank the API refuses. */
      if (field.tagName === 'SELECT' && !data[key]) field.selectedIndex = 0;
      else field.value = data[key] || '';
    });
  }

  function show(row) {
    current = row;
    const d = row.dataset;
    el('cld-eyebrow').textContent = 'Clinic';
    el('cld-title').textContent = d.name;
    el('cld-meta').textContent = `${d.status} · /discover/clinics/${d.slug}`;
    fill(d);
    el('cld-link-public').href = `/discover/clinics/${d.slug}`;
    el('cld-link-dashboard').href = `/dashboard/sites/${d.id}/settings/profile`;
    A.setMsg(msg, '');
    setEditing(false);
  }

  function showNew() {
    current = null;
    el('cld-eyebrow').textContent = 'New clinic';
    el('cld-title').textContent = 'Add a clinic';
    el('cld-meta').textContent =
      'It starts as a draft: invisible to patients until it is checked.';
    fill({});
    A.setMsg(msg, '');
    setEditing(true);
    if (detail) detail.open();
    el('cld-name').focus();
  }

  table.querySelectorAll('tbody tr').forEach((row) => {
    row.addEventListener('click', () => {
      show(row);
      if (detail) detail.open();
    });
  });

  const newButton = el('cl-new');
  if (newButton) newButton.addEventListener('click', showNew);
  el('cl-edit').addEventListener('click', () => setEditing(true));
  el('cl-cancel').addEventListener('click', () => {
    if (current) show(current);
    else if (detail) detail.close();
  });

  /** What the API takes, from the fields. A blank optional field is null, never "". */
  function payload() {
    const value = (id) => el(id).value.trim();
    const orNull = (id) => value(id) || null;
    return {
      name: value('cld-name'),
      slug: value('cld-slug'),
      sector: value('cld-sector'),
      address_line: value('cld-address'),
      suburb: orNull('cld-suburb'),
      city: value('cld-city'),
      province: value('cld-province'),
      postal_code: orNull('cld-postal'),
      phone_e164: orNull('cld-phone'),
      notes: orNull('cld-notes'),
      location: {
        latitude: parseFloat(value('cld-lat')),
        longitude: parseFloat(value('cld-lng')),
      },
    };
  }

  /* Look the address up. The operator is the fallback, not an error state: when the service cannot
     answer, the coordinate fields are simply typed into, which is why they are always editable. */
  el('cl-geocode').addEventListener('click', async () => {
    const address = [
      el('cld-address').value,
      el('cld-suburb').value,
      el('cld-city').value,
      el('cld-province').value,
    ]
      .map((part) => part.trim())
      .filter(Boolean)
      .join(', ');
    if (address.length < 3) {
      A.setMsg(el('cld-geo-msg'), 'Type the address first.', 'error');
      return;
    }
    const list = el('cl-candidates');
    list.hidden = true;
    list.innerHTML = '';
    A.setMsg(el('cld-geo-msg'), 'Looking…');
    const url = current
      ? `/api/v1/sites/${current.dataset.id}/geocode`
      : '/api/v1/sites/geocode';
    const answer = await A.writeJson('POST', url, { address });
    if (!answer.ok) {
      A.setMsg(
        el('cld-geo-msg'),
        A.errorText(answer, 'The address could not be looked up. Type the point in instead.'),
        'error'
      );
      return;
    }
    const candidates = (answer.data && answer.data.candidates) || [];
    if (!candidates.length) {
      A.setMsg(el('cld-geo-msg'), 'Nothing matched that address. Type the point in instead.');
      return;
    }
    A.setMsg(el('cld-geo-msg'), 'Pick the right one:');
    candidates.forEach((candidate) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-quiet btn-sm';
      button.textContent = candidate.label;
      button.addEventListener('click', () => {
        el('cld-lat').value = candidate.location.latitude;
        el('cld-lng').value = candidate.location.longitude;
        A.setMsg(el('cld-geo-msg'), `Using ${candidate.label}.`, 'ok');
        list.hidden = true;
      });
      item.appendChild(button);
      list.appendChild(item);
    });
    list.hidden = false;
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const body = payload();
    if (Number.isNaN(body.location.latitude) || Number.isNaN(body.location.longitude)) {
      A.setMsg(msg, 'Find the clinic on the map, or type its latitude and longitude.', 'error');
      return;
    }
    A.setMsg(msg, 'Saving…');
    /* The operator's edit is the directory route, not the guarded one: a platform admin is
       assigned to no clinic (Issue 19), so `PUT /sites/{id}` answers 404 for them. */
    const answer = current
      ? await A.writeJson('PUT', `/api/v1/sites/${current.dataset.id}/directory`, body)
      : await A.writeJson('POST', '/api/v1/sites', body);
    if (!answer.ok) {
      A.setMsg(msg, A.errorText(answer, 'That clinic could not be saved.'), 'error');
      return;
    }
    /* The row's name, tab or filter may all have changed, so reload rather than patching it in
       place: what the operator wants to see next is the list as it now is. */
    window.location.reload();
  });

  const archive = el('cl-archive');
  if (archive) {
    archive.addEventListener('click', async () => {
      if (!current || !can.can('archive')) return;
      const name = current.dataset.name;
      const sure = window.confirm(
        `Archive ${name}?\n\nIt leaves the directory and no patient can find it or join a queue ` +
          `there. Its tickets and audit trail are kept. This is not undone from here.`
      );
      if (!sure) return;
      A.setMsg(msg, 'Archiving…');
      const answer = await A.writeJson('DELETE', `/api/v1/sites/${current.dataset.id}`);
      if (!answer.ok) {
        A.setMsg(msg, A.errorText(answer, 'That clinic could not be archived.'), 'error');
        return;
      }
      window.location.reload();
    });
  }

  setEditing(false);
})();
