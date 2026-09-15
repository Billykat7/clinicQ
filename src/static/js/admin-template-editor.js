/* Editing a patient message template (Issue 66).
 *
 * As the words change (debounced), asks the server for exactly what a patient would receive: the same
 * renderer the notification service uses, on a sample ticket. For an SMS it shows the character count and
 * segments, and the count with the longest names the database allows, which must stay within one segment.
 * The server decides whether the words may be published; this page only shows its answer. External file,
 * no inline handlers: satisfies script-src 'self'.
 */
(function () {
  const root = document.getElementById('te-split');
  const form = document.getElementById('te-form');
  if (!root || !form) return;
  const el = (id) => document.getElementById(id);
  const body = el('te-body');
  const subject = el('te-subject');
  const reviewed = el('te-reviewed');
  const publish = el('te-publish');
  const error = el('te-error');
  const saved = el('te-saved');
  const counter = el('te-count');

  const csrf = () => (window.BKP && typeof window.BKP.csrfToken === 'function' ? window.BKP.csrfToken() : '');
  const headers = () => ({ 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRF-Token': csrf() });

  function draft() {
    return { body: body.value, subject: subject ? subject.value : null, reviewed_by: reviewed.value || null };
  }

  function sayError(text) {
    error.textContent = text || '';
    error.hidden = !text;
  }

  let timer = null;
  let sequence = 0;
  function preview() {
    const mine = ++sequence;
    const payload = Object.assign(draft(), {
      template: root.dataset.template,
      channel: root.dataset.channel,
      language: root.dataset.language,
    });
    fetch(root.dataset.previewUrl, { method: 'POST', credentials: 'same-origin', headers: headers(), body: JSON.stringify(payload) })
      .then((response) => response.json())
      .then((answer) => {
        if (mine !== sequence) return; // a newer preview is on its way
        el('te-preview-text').textContent = answer.valid ? answer.text : '';
        const title = el('te-preview-subject');
        title.textContent = answer.subject || '';
        title.hidden = !answer.subject;
        sayError(answer.valid ? '' : answer.error);
        if (publish) publish.disabled = !answer.valid;
        if (answer.valid && answer.segments != null) {
          counter.textContent =
            `${answer.characters} characters, ${answer.segments} segment${answer.segments === 1 ? '' : 's'} ` +
            `(${answer.encoding}); with the longest names: ${answer.worst_characters} characters, ` +
            `${answer.worst_segments} segment${answer.worst_segments === 1 ? '' : 's'}.`;
        } else {
          counter.textContent = answer.valid ? `${answer.text.length} characters.` : '';
        }
      })
      .catch(() => sayError('The preview could not be loaded. Check your connection.'));
  }

  function schedule() {
    saved.hidden = true;
    window.clearTimeout(timer);
    timer = window.setTimeout(preview, 250);
  }

  [body, subject, reviewed].forEach((input) => input && input.addEventListener('input', schedule));

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!publish) return; // this operator may read the templates but not publish them
    publish.disabled = true;
    fetch(root.dataset.publishUrl, { method: 'POST', credentials: 'same-origin', headers: headers(), body: JSON.stringify(draft()) })
      .then((response) => response.json().then((answer) => ({ ok: response.ok, answer })))
      .then(({ ok, answer }) => {
        if (!ok) {
          sayError(typeof answer.detail === 'string' ? answer.detail : 'The words could not be published.');
          publish.disabled = false;
          return;
        }
        saved.textContent = `Published as version ${answer.version}. Patients receive it from the next message.`;
        saved.hidden = false;
        window.setTimeout(() => window.location.reload(), 1200);
      })
      .catch(() => {
        sayError('No connection: nothing was published.');
        publish.disabled = false;
      });
  });

  preview();
})();
