/* The waiting-room screen's settings (Issue 27, non-negotiable 4).
 *
 * This page renders no rule of its own. The modes, their plain-language descriptions, the
 * language list, the retention bounds and the two warnings all come from
 * /api/v1/sites/display-options, and the confirmations are fields the server refuses the change
 * without. So a change made here and a change made by any other client are governed by the same
 * text and the same rules: the screen cannot reassure somebody about a setting the server treats
 * differently, which is the failure this shape exists to prevent.
 */
(function () {
  const root = document.getElementById('display-settings');
  if (!root) return;
  const siteId = root.dataset.siteId;
  const el = (id) => document.getElementById(id);
  const form = el('ds-form');
  let options = null;

  async function getJson(url) {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  function renderModes(current) {
    el('ds-modes').innerHTML = options.modes
      .map(
        (mode) => `
        <label class="radio-card">
          <input type="radio" name="ds-mode" value="${mode.value}"${mode.value === current ? ' checked' : ''}>
          <span class="radio-card-body">
            <span class="radio-card-title">${labelFor(mode.value)}</span>
            <span class="radio-card-sub">${mode.warning}</span>
          </span>
        </label>`
      )
      .join('');
    el('ds-modes')
      .querySelectorAll('input[name="ds-mode"]')
      .forEach((input) => input.addEventListener('change', refreshWarning));
  }

  function labelFor(value) {
    return {
      number_only: 'Ticket numbers only',
      name_lite: 'Number and a short name',
      full: 'Number and full name',
    }[value] || value;
  }

  function selectedMode() {
    const checked = el('ds-modes').querySelector('input[name="ds-mode"]:checked');
    return checked ? checked.value : 'number_only';
  }

  /* Which confirmations this choice needs, decided from the same data the server uses. */
  function refreshWarning() {
    const mode = options.modes.find((m) => m.value === selectedMode());
    const comment = el('ds-show-comment').checked;
    const lines = [mode.warning];
    if (comment) lines.push(options.comment_warning);
    const commentOnFullName = comment && mode.value === 'full';
    if (commentOnFullName) lines.push(options.comment_with_full_name_warning);

    const warning = el('ds-warning');
    warning.textContent = lines.join(' ');
    warning.hidden = !(mode.requires_confirmation || comment);
    el('ds-confirm-wrap').hidden = !(mode.requires_confirmation || comment);
    el('ds-confirm-comment-wrap').hidden = !commentOnFullName;
    el('ds-confirm-comment-text').textContent = options.comment_with_full_name_warning;
  }

  async function load() {
    options = await getJson(`/api/v1/sites/${siteId}/settings/display-options`);
    const current = await getJson(`/api/v1/sites/${siteId}/settings/display`);

    renderModes(current.display_mode);
    el('ds-show-comment').checked = current.display_show_comment;
    el('ds-announce-audio').checked = current.announce_audio;
    el('ds-retention').value = current.reason_retention_days;
    el('ds-retention').max = options.retention_ceiling_days;
    el('ds-retention').min = options.retention_floor_days;
    el('ds-retention-hint').textContent =
      `Between ${options.retention_floor_days} and ${options.retention_ceiling_days} days. ` +
      'After that the reason is deleted automatically.';
    el('ds-comment-hint').textContent = options.comment_warning;
    el('ds-language').innerHTML = options.languages
      .map((code) => `<option value="${code}"${code === current.board_language ? ' selected' : ''}>${code}</option>`)
      .join('');

    el('ds-show-comment').addEventListener('change', refreshWarning);
    refreshWarning();
    el('ds-loading').hidden = true;
    form.hidden = false;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = el('ds-msg');
    message.textContent = 'Saving…';
    const response = await fetch(`/api/v1/sites/${siteId}/settings/display`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: window.BKP.writeHeaders(),
      body: JSON.stringify({
        display_mode: selectedMode(),
        display_show_comment: el('ds-show-comment').checked,
        board_language: el('ds-language').value,
        announce_audio: el('ds-announce-audio').checked,
        reason_retention_days: Number(el('ds-retention').value),
        confirm_public_display: el('ds-confirm').checked,
        confirm_comment_with_full_name: el('ds-confirm-comment').checked,
      }),
    });
    const body = await response.json().catch(() => ({}));
    message.textContent = response.ok
      ? 'Saved.'
      : body.detail || 'That change could not be saved.';
    message.className = response.ok ? 'msg msg-ok' : 'msg msg-error';
  });

  load().catch(() => {
    el('ds-loading').textContent = 'The settings could not be loaded.';
  });
})();
