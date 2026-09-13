/* A private clinic's payment methods and medical aids (Issue 37).
 *
 * Renders no rule of its own: whether the clinic may hold a profile (`applicable`), the controlled
 * scheme list, the notice patients see and whether the profile is stale all come from
 * /api/v1/sites/{id}/payment-profile. Saving is confirming; "Still correct" re-confirms without a
 * change. A public clinic gets an explanation and no form, and the server would refuse its save
 * anyway.
 */
(function () {
  const root = document.getElementById('payment-profile');
  if (!root) return;
  const url = `/api/v1/sites/${root.dataset.siteId}/payment-profile`;
  const el = (id) => document.getElementById(id);

  function say(text, ok) {
    const message = el('pp-msg');
    message.textContent = text;
    message.className = ok ? 'msg msg-ok' : 'msg msg-error';
  }

  function render(profile) {
    el('pp-notice').textContent = `Patients see this with: "${profile.notice}"`;
    el('pp-loading').hidden = true;
    if (!profile.applicable) {
      el('pp-public').hidden = false;
      el('pp-form').hidden = true;
      return;
    }
    const chosen = new Set(profile.schemes.map((item) => item.scheme));
    const box = el('pp-schemes');
    box.textContent = '';
    profile.scheme_options.forEach((option) => {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = option.value;
      input.checked = chosen.has(option.value);
      input.addEventListener('change', syncOther);
      label.append(input, ` ${option.label}`);
      box.append(label);
    });
    const other = profile.schemes.find((item) => item.scheme === 'other');
    el('pp-other').value = other ? other.label : '';
    el('pp-cash').checked = Boolean(profile.accepts_cash);
    el('pp-card').checked = Boolean(profile.accepts_card);
    el('pp-copay').value = profile.copay_notice || '';
    el('pp-confirmed').textContent = profile.last_confirmed_at
      ? `Last confirmed ${new Date(profile.last_confirmed_at).toLocaleDateString('en-ZA', { timeZone: 'Africa/Johannesburg', dateStyle: 'long' })}.` +
        (profile.stale ? ' Patients see this as not confirmed in six months.' : '')
      : 'Not listed yet. Patients see "not listed" until you save.';
    el('pp-confirm').hidden = !profile.last_confirmed_at;
    syncOther();
    el('pp-form').hidden = false;
  }

  function syncOther() {
    const otherBox = el('pp-schemes').querySelector('input[value="other"]');
    el('pp-other-wrap').hidden = !(otherBox && otherBox.checked);
  }

  async function send(method, path, body) {
    const response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: window.BKP.writeHeaders(),
      body: body ? JSON.stringify(body) : undefined,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(payload.detail) ? payload.detail.map((d) => d.msg).join(' ') : payload.detail;
      say(detail || 'That could not be saved.', false);
      return;
    }
    render(payload);
    say(method === 'PUT' ? 'Saved and confirmed.' : 'Confirmed.', true);
  }

  el('pp-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const schemes = [...el('pp-schemes').querySelectorAll('input:checked')].map((input) => input.value);
    send('PUT', url, {
      accepts_cash: el('pp-cash').checked,
      accepts_card: el('pp-card').checked,
      schemes,
      other_scheme_name: schemes.includes('other') ? el('pp-other').value : null,
      copay_notice: el('pp-copay').value || null,
    });
  });
  el('pp-confirm').addEventListener('click', () => send('POST', `${url}/confirm`));

  fetch(url, { credentials: 'same-origin' })
    .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
    .then(render)
    .catch(() => {
      el('pp-loading').textContent = 'The payment profile could not be loaded.';
    });
})();
