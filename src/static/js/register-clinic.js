/* The public clinic-registration form (Issue 29).
 *
 * Posts to /api/v1/sites/register and shows what the server says. Two things it deliberately does
 * not do: it does not decide whether a coordinate is acceptable (the bounding-box rule is the
 * server's, and a form that guessed differently would refuse a real address or accept a wrong one),
 * and it does not compose its own success message (the server sends one, so a channel adapter and
 * this page tell a clinic the same thing).
 * The CSRF token comes from window.BKP.writeHeaders() (csrf-htmx.js), which reads the cookie
 * name out of <meta name="bkp-csrf-cookie">. An earlier version of this file matched a
 * hardcoded cookie name and every submission from a browser that had one was refused with
 * "Invalid or missing CSRF token" — found by filling the form in, not by a test.
 */
(function () {
  const form = document.getElementById('rc-form');
  if (!form) return;
  const el = (id) => document.getElementById(id);

  /* The slug preview: what a patient will see in the address bar, as it is typed. */
  el('rc-slug').addEventListener('input', (event) => {
    el('rc-slug-preview').textContent = event.target.value || 'your-clinic';
  });

  /* A convenience, not a rule: derive a slug from the name until somebody edits the slug. */
  let slugTouched = false;
  el('rc-slug').addEventListener('change', () => { slugTouched = true; });
  el('rc-name').addEventListener('input', (event) => {
    if (slugTouched) return;
    const slug = event.target.value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
    el('rc-slug').value = slug;
    el('rc-slug-preview').textContent = slug || 'your-clinic';
  });

  function value(id) {
    const raw = el(id).value.trim();
    return raw === '' ? null : raw;
  }

  function showError(text) {
    const box = el('rc-error');
    box.textContent = text;
    box.hidden = false;
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  /* FastAPI's validation errors are a list; a refusal we raise is a string. Show whichever came. */
  function readDetail(body) {
    const detail = body && body.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map((item) => item.msg).join(' ');
    }
    return 'Those details could not be sent. Please check them and try again.';
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    el('rc-error').hidden = true;
    const button = el('rc-submit');
    button.disabled = true;
    button.textContent = 'Sending…';

    const payload = {
      name: value('rc-name'),
      slug: value('rc-slug'),
      sector: el('rc-sector').value,
      location: {
        latitude: Number(el('rc-latitude').value),
        longitude: Number(el('rc-longitude').value),
      },
      address_line: value('rc-address'),
      suburb: value('rc-suburb'),
      city: value('rc-city'),
      province: el('rc-province').value,
      phone_e164: value('rc-phone'),
      contact_name: value('rc-contact-name'),
      contact_email: value('rc-contact-email'),
      contact_phone: value('rc-contact-phone'),
    };

    try {
      const response = await fetch('/api/v1/sites/register', {
        method: 'POST',
        credentials: 'same-origin',
        headers: window.BKP.writeHeaders(),
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        showError(readDetail(body));
        return;
      }
      form.hidden = true;
      el('rc-done-message').textContent = body.message;
      el('rc-done-link').textContent = `Your clinic's web address will be: ${body.slug}`;
      el('rc-done').hidden = false;
      el('rc-done').scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (error) {
      showError('We could not reach the server. Please try again in a moment.');
    } finally {
      button.disabled = false;
      button.textContent = 'Send for checking';
    }
  });
})();
