/** Panel owner: `templates` (Templates).
 * SOLID: template list/editor + default selection live HERE.
 */
import { $ } from '../core/dom.js';
import { esc, api, toast } from '../core/api.js';
import { uiConfirm } from '../core/dialog.js';
import { registerPanel } from './registry.js';
import { B } from '../app/bridge.js';
import { channelLabel } from '../features/compose.js';

export const panelId = 'templates';

export const CHANNELS = ['email', 'whatsapp', 'instagram', 'invoice'];

let activeTplChannel = 'email';
let selectedTplId = null;
let templatesStore = null;

/** Channel templates from the last /api/templates fetch. */
export function templatesFor(channel) {
  return (templatesStore && templatesStore[channel]) || [];
}

export function getTemplatesStore() {
  return templatesStore;
}

/**
 * Resolve which template compose / the editor should treat as default.
 * Marked isDefault always wins (Set default). Soft openers are fallback for IG/WA only.
 */
export function pickDefaultTemplate(list, channel) {
  const items = Array.isArray(list) ? list : [];
  const marked = items.find((t) => t && t.isDefault);
  if (marked) return marked;
  if (channel === 'instagram' || channel === 'whatsapp') {
    const soft = items.find((t) => (t.purpose || '') === 'soft_opener');
    if (soft) return soft;
  }
  return items[0] || null;
}

export function defaultTemplate(channel) {
  return pickDefaultTemplate(templatesFor(channel), channel);
}

export function getTemplate(channel, id) {
  return templatesFor(channel).find((t) => String(t.id) === String(id)) || defaultTemplate(channel);
}

export function templateOptionLabel(t) {
  const bits = [];
  if (t && t.isDefault) bits.push('default');
  if (t && (t.purpose || '') === 'soft_opener') bits.push('soft');
  return bits.length ? ` (${bits.join(' · ')})` : '';
}

async function migrateLocalTemplates() {
  try {
    if (localStorage.getItem('mlf_templates_migrated')) return;
    const raw = localStorage.getItem('mlf_channel_templates');
    if (!raw) {
      localStorage.setItem('mlf_templates_migrated', '1');
      return;
    }
    const parsed = JSON.parse(raw);
    let migrated = 0;
    for (const ch of CHANNELS) {
      const list = Array.isArray(parsed?.[ch]) ? parsed[ch] : [];
      for (const t of list) {
        if (!t || !(t.body || t.subject)) continue;
        if (t.id && String(t.id).endsWith('-default') && list.length === 1) continue;
        await api('/api/templates', {
          method: 'POST',
          body: {
            channel: ch,
            name: t.name || `Imported ${ch}`,
            subject: t.subject || '',
            body: t.body || '',
            is_default: !!t.isDefault,
          },
        });
        migrated++;
      }
    }
    localStorage.setItem('mlf_templates_migrated', '1');
    if (migrated) toast(`Migrated ${migrated} local templates to server.`);
  } catch (e) {
    localStorage.setItem('mlf_templates_migrated', '1');
  }
}

export async function loadTemplates() {
  await migrateLocalTemplates();
  templatesStore = await api('/api/templates');
  renderTemplateList();
  return templatesStore;
}

export function renderTemplateList() {
  const list = $('templateList');
  if (!list) return;
  const items = templatesFor(activeTplChannel);
  if (!selectedTplId || !items.some((t) => String(t.id) === String(selectedTplId))) {
    selectedTplId = (defaultTemplate(activeTplChannel) || items[0] || {}).id;
  }
  list.innerHTML = items.map((t) => (
    `<button type="button" class="${String(t.id) === String(selectedTplId) ? 'active' : ''}" data-tpl-id="${esc(t.id)}">`
    + `<strong>${esc(t.name)}</strong>`
    + `<span class="meta">${t.isDefault ? 'Default · ' : ''}${channelLabel(activeTplChannel)}</span>`
    + `</button>`
  )).join('') || '<span class="sub">No templates</span>';
  const subjectField = $('tplSubjectField');
  if (subjectField) subjectField.classList.toggle('hidden', activeTplChannel !== 'email');
  const t = getTemplate(activeTplChannel, selectedTplId);
  if (!t) {
    if ($('tplName')) $('tplName').value = '';
    if ($('tplSubject')) $('tplSubject').value = '';
    if ($('tplBody')) $('tplBody').value = '';
    return;
  }
  if ($('tplName')) $('tplName').value = t.name;
  if ($('tplSubject')) $('tplSubject').value = t.subject || '';
  if ($('tplBody')) $('tplBody').value = t.body || '';
}

export function setTplChannel(channel) {
  activeTplChannel = channel;
  selectedTplId = null;
  document.querySelectorAll('[data-tpl-channel]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tplChannel === channel);
  });
  renderTemplateList();
}

async function saveCurrentTemplate() {
  const t = getTemplate(activeTplChannel, selectedTplId);
  const body = {
    channel: activeTplChannel,
    name: ($('tplName') && $('tplName').value.trim()) || 'Template',
    subject: ($('tplSubject') && $('tplSubject').value) || '',
    body: ($('tplBody') && $('tplBody').value) || '',
    is_default: !!(t && t.isDefault),
  };
  if (t && t.id) {
    await api('/api/templates/' + t.id, { method: 'PATCH', body });
    toast('Template saved.');
  } else {
    const created = await api('/api/templates', { method: 'POST', body });
    selectedTplId = created.id;
    toast('Template created.');
  }
  await loadTemplates();
}

async function newTemplate() {
  const created = await api('/api/templates', {
    method: 'POST',
    body: {
      channel: activeTplChannel,
      name: `New ${channelLabel(activeTplChannel)}`,
      subject: activeTplChannel === 'email' ? 'Quick idea for {{name}}' : '',
      body: 'Hi {{name}},\n\n',
      is_default: false,
    },
  });
  selectedTplId = created.id;
  await loadTemplates();
  if ($('tplName')) $('tplName').focus();
  toast('New template created.');
}

async function setDefaultTemplate() {
  if (!selectedTplId) {
    toast('Select a template first.', true);
    return;
  }
  await api('/api/templates/' + selectedTplId + '/default', { method: 'POST' });
  await loadTemplates();
  // Re-fill open compose so message pane picks the new default immediately.
  if (typeof B.refreshComposeTemplates === 'function') B.refreshComposeTemplates();
  toast('Default updated.');
}

async function deleteTemplate() {
  if (!selectedTplId) {
    toast('Select a template first.', true);
    return;
  }
  const tpl = getTemplate(activeTplChannel, selectedTplId);
  const ok = await uiConfirm({
    title: 'Delete template?',
    message: `“${(tpl && tpl.name) || 'This template'}” will be permanently removed.`,
    confirmLabel: 'Delete template',
    danger: true,
  });
  if (!ok) return;
  try {
    await api('/api/templates/' + selectedTplId, { method: 'DELETE' });
    selectedTplId = null;
    await loadTemplates();
    toast('Template deleted.');
  } catch (e) {
    toast(e.message, true);
  }
}

function wireTemplatePanel() {
  document.querySelectorAll('[data-tpl-channel]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      setTplChannel(btn.dataset.tplChannel);
    });
  });
  const list = $('templateList');
  if (list) {
    list.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-tpl-id]');
      if (!btn) return;
      selectedTplId = btn.dataset.tplId;
      renderTemplateList();
    });
  }
  $('saveTplBtn') && $('saveTplBtn').addEventListener('click', () => saveCurrentTemplate().catch((e) => toast(e.message, true)));
  $('newTplBtn') && $('newTplBtn').addEventListener('click', () => newTemplate().catch((e) => toast(e.message, true)));
  $('setDefaultTplBtn') && $('setDefaultTplBtn').addEventListener('click', () => setDefaultTemplate().catch((e) => toast(e.message, true)));
  $('deleteTplBtn') && $('deleteTplBtn').addEventListener('click', () => deleteTemplate());
}

wireTemplatePanel();

B.loadTemplates = loadTemplates;
B.defaultTemplate = defaultTemplate;
B.getTemplate = getTemplate;
B.templatesFor = templatesFor;
B.setTplChannel = setTplChannel;
B.pickDefaultTemplate = pickDefaultTemplate;

registerPanel({ id: 'templates', refresh: loadTemplates });
