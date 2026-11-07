/** Panel owner: `pipeline` (Pipeline).
 * Stages align with Contact queue early tabs:
 *   Identified (to_contact) → To contact
 *   Ready to Contact → Ready
 *   Contacted → Contacted
 * Then Follow-up +3 / +7 → Engaged → … → Won/Lost.
 */
import { $ } from '../core/dom.js';
import { esc, api, toast } from '../core/api.js';
import { uiForm } from '../core/dialog.js';
import { registerPanel } from './registry.js';
import { safeRefresh } from '../shell/router.js';
import { B } from '../app/bridge.js';

let lastPipelineStages = [];
let pipelineLocations = {};
let pipelineCategories = [];

const CQ_HINT = {
  to_contact: 'Contact queue · To contact',
  ready_to_contact: 'Contact queue · Ready',
  contacted: 'Contact queue · Contacted',
  follow_up_3: 'Due ≤ +3 days',
  follow_up_7: 'Due +7 days or later',
};

function pipelineQuery() {
  const line = ($('pipelineLine') && $('pipelineLine').value) || 'STANDALONE';
  const country = ($('pipelineCountry') && $('pipelineCountry').value) || '';
  const city = ($('pipelineCity') && $('pipelineCity').value) || '';
  const category = ($('pipelineCategory') && $('pipelineCategory').value) || '';
  const q = new URLSearchParams({ product_line: line });
  if (country) q.set('country', country);
  if (city) q.set('city', city);
  if (category) q.set('category', category);
  return q.toString();
}

export function fillPipelineFilters(locations, categories, keep) {
  const countrySel = $('pipelineCountry');
  const citySel = $('pipelineCity');
  const catSel = $('pipelineCategory');
  if (!countrySel || !citySel || !catSel) return;
  pipelineLocations = locations || {};
  pipelineCategories = categories || [];
  const keepCountry = keep && keep.country != null ? keep.country : (countrySel.value || '');
  const keepCity = keep && keep.city != null ? keep.city : (citySel.value || '');
  const keepCat = keep && keep.category != null ? keep.category : (catSel.value || '');

  const countries = Object.keys(pipelineLocations).sort((a, b) => a.localeCompare(b));
  countrySel.innerHTML =
    '<option value="">All countries</option>' +
    countries.map((c) => `<option value="${esc(c)}" ${c === keepCountry ? 'selected' : ''}>${esc(c)}</option>`).join('');
  if (keepCountry && ![...countrySel.options].some((o) => o.value === keepCountry)) countrySel.value = '';

  const activeCountry = countrySel.value;
  const cities = activeCountry
    ? (pipelineLocations[activeCountry] || []).slice().sort((a, b) => a.localeCompare(b))
    : [...new Set(Object.values(pipelineLocations).flat())].sort((a, b) => a.localeCompare(b));
  citySel.innerHTML =
    '<option value="">All cities</option>' +
    cities.map((c) => `<option value="${esc(c)}" ${c === keepCity ? 'selected' : ''}>${esc(c)}</option>`).join('');
  if (keepCity && ![...citySel.options].some((o) => o.value === keepCity)) citySel.value = '';

  catSel.innerHTML =
    '<option value="">All categories</option>' +
    pipelineCategories
      .slice()
      .sort((a, b) => a.localeCompare(b))
      .map((c) => `<option value="${esc(c)}" ${c === keepCat ? 'selected' : ''}>${esc(c)}</option>`)
      .join('');
  if (keepCat && ![...catSel.options].some((o) => o.value === keepCat)) catSel.value = '';
}

function cardChannels(p) {
  const bits = [];
  if (p.outreach_channel) bits.push(`<span class="ch-tag">${esc(p.outreach_channel)}</span>`);
  if (p.email) bits.push('<span class="ch-tag">email</span>');
  if (p.phone || p.international_phone) bits.push('<span class="ch-tag">WA</span>');
  return bits.join('');
}

function renderKanbanCard(p, stageId) {
  const badge =
    p.follow_up_badge && p.follow_up_badge.label
      ? `<span class="ch-tag" title="Follow-up timing">${esc(p.follow_up_badge.label)}</span>`
      : p.follow_up_kind === 'call_today'
        ? '<span class="ch-tag">Call today</span>'
        : '';
  const place = [p.city, p.country].filter(Boolean).join(' · ');
  const cat = p.category ? `<span class="sub">${esc(p.category)}</span>` : '';
  const val =
    p.deal_value != null
      ? `${esc(String(p.currency || ''))} ${esc(String(p.deal_value))}`
      : 'no value';
  const fin = (p.financial_status || (p.be_commercial && p.be_commercial.financial_status) || '').trim();
  const del = (p.delivery_status || (p.be_commercial && p.be_commercial.delivery_status) || '').trim();
  const finPill = fin ? `<span class="status-pill" title="Financial status">${esc(fin)}</span>` : '';
  const delPill = del ? `<span class="status-pill" title="Delivery status">${esc(del)}</span>` : '';
  const ch = cardChannels(p);
  return `
    <div class="kanban-card" draggable="true" data-id="${p.id}" data-stage-id="${stageId}">
      <strong>${esc(p.name || 'Unnamed')}</strong>
      <span>${esc(place || '—')} · ${esc(val)}</span>
      ${cat}
      ${finPill || delPill ? `<div class="kanban-card-tags">${finPill}${delPill}</div>` : ''}
      ${badge || ch ? `<div class="kanban-card-tags">${badge}${ch}</div>` : ''}
      <div class="actions" style="margin-top:6px">
        <button type="button" class="stage-move" data-id="${p.id}" data-stage-id="${stageId}">Move</button>
        ${
          typeof B.openProspectById === 'function'
            ? `<button type="button" class="ghost pipeline-open" data-id="${p.id}">Open</button>`
            : ''
        }
      </div>
    </div>`;
}

export async function moveProspectStage(id, stageId, note = '', lostReason = '') {
  try {
    await api('/api/sales/prospects/' + id + '/stage', {
      method: 'POST',
      body: { stage_id: Number(stageId), note, lost_reason: lostReason || '', force: false },
    });
    toast('Stage updated');
    await loadPipeline();
    return true;
  } catch (e) {
    const msg = String(e.message || e);
    if (msg.includes('lost_reason')) {
      const form = await uiForm({
        title: 'Lost reason required',
        message: 'Moving to Lost needs a short reason for reporting.',
        confirmLabel: 'Mark lost',
        danger: true,
        fields: [
          {
            name: 'lost_reason',
            label: 'Lost reason',
            type: 'textarea',
            required: true,
            placeholder: 'e.g. chose competitor, timing, budget',
          },
        ],
      });
      if (!form) return false;
      await api('/api/sales/prospects/' + id + '/stage', {
        method: 'POST',
        body: { stage_id: Number(stageId), note, lost_reason: form.lost_reason, force: false },
      });
      toast('Stage updated');
      await loadPipeline();
      return true;
    }
    toast(msg, true);
    return false;
  }
}

export async function openStageMoveDialog(prospectId, currentStageId) {
  const line = ($('pipelineLine') && $('pipelineLine').value) || 'STANDALONE';
  const stages = lastPipelineStages.length
    ? lastPipelineStages
    : await api('/api/sales/stages?product_line=' + encodeURIComponent(line)).then((d) => {
        lastPipelineStages = (d.stages || []).map((s) => ({
          id: s.id,
          name: s.name,
          slug: s.slug,
          is_lost: !!s.is_lost,
        }));
        return lastPipelineStages;
      });
  const form = await uiForm({
    title: 'Move prospect',
    message: 'Choose the next pipeline stage. Drag-and-drop also works on desktop.',
    confirmLabel: 'Move',
    fields: [
      {
        name: 'stage_id',
        label: 'Stage',
        type: 'select',
        required: true,
        value: String(currentStageId || ''),
        options: stages.map((s) => ({
          value: String(s.id),
          label: s.name + (s.is_lost ? ' (lost)' : ''),
        })),
      },
      { name: 'note', label: 'Note (optional)', type: 'textarea', rows: 2, placeholder: 'Why this move?' },
    ],
  });
  if (!form) return;
  const target = stages.find((s) => String(s.id) === String(form.stage_id));
  let lost = '';
  if (target && target.is_lost) {
    const lostForm = await uiForm({
      title: 'Lost reason',
      message: 'Required when moving to a lost stage.',
      confirmLabel: 'Move to Lost',
      danger: true,
      fields: [{ name: 'lost_reason', label: 'Lost reason', type: 'textarea', required: true }],
    });
    if (!lostForm) return;
    lost = lostForm.lost_reason;
  }
  await moveProspectStage(prospectId, form.stage_id, form.note || '', lost);
}

export async function loadPipeline() {
  const keep = {
    country: ($('pipelineCountry') && $('pipelineCountry').value) || '',
    city: ($('pipelineCity') && $('pipelineCity').value) || '',
    category: ($('pipelineCategory') && $('pipelineCategory').value) || '',
  };
  const data = await api('/api/sales/pipeline?' + pipelineQuery());
  fillPipelineFilters(data.locations || {}, data.categories || [], keep);
  const cols = data.columns || [];
  lastPipelineStages = cols
    .map((c) => ({
      id: c.stage && c.stage.id,
      name: (c.stage && c.stage.name) || '',
      slug: (c.stage && c.stage.slug) || '',
      is_lost: !!(c.stage && c.stage.is_lost),
    }))
    .filter((s) => s.id != null);

  const board = $('kanban');
  const empty = $('pipelineEmpty');
  if (!board) return;
  if (!cols.length) {
    board.innerHTML = '';
    if (empty) empty.classList.remove('hidden');
    return;
  }
  if (empty) empty.classList.add('hidden');

  const totalCards = cols.reduce((n, c) => n + (c.count || 0), 0);
  if ($('pipelineMeta')) {
    const bits = [`${totalCards} prospects on board`];
    if (keep.country) bits.push(keep.country);
    if (keep.city) bits.push(keep.city);
    if (keep.category) bits.push(keep.category);
    $('pipelineMeta').textContent = bits.join(' · ');
  }

  board.innerHTML = cols
    .map((col) => {
      const s = col.stage || {};
      const val = col.value_by_currency || {};
      const valStr = Object.keys(val).length
        ? Object.entries(val)
            .map(([c, v]) => `${c} ${Number(v).toFixed(0)}`)
            .join(' · ')
        : '—';
      const hint = CQ_HINT[s.slug] || '';
      const cards =
        (col.prospects || []).map((p) => renderKanbanCard(p, s.id)).join('') ||
        '<div class="hint">Empty — drag a card here</div>';
      return `<div class="kanban-col" data-stage-id="${s.id}" data-slug="${esc(s.slug || '')}">
        <h3>${esc(s.name || '')}</h3>
        <div class="kanban-meta">${col.count || 0} · ${esc(valStr)} · ${Math.round((s.probability || 0) * 100)}%</div>
        ${hint ? `<div class="kanban-cq-hint">${esc(hint)}</div>` : ''}
        ${cards}
      </div>`;
    })
    .join('');

  board.querySelectorAll('.stage-move').forEach((btn) =>
    btn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try {
        await openStageMoveDialog(btn.dataset.id, btn.dataset.stageId);
      } catch (e) {
        toast(e.message || String(e), true);
      }
    }),
  );
  board.querySelectorAll('.pipeline-open').forEach((btn) =>
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (typeof B.openProspectById === 'function') B.openProspectById(Number(btn.dataset.id));
    }),
  );

  let dragId = null;
  board.querySelectorAll('.kanban-card').forEach((card) => {
    card.addEventListener('dragstart', (ev) => {
      dragId = card.dataset.id;
      card.classList.add('dragging');
      ev.dataTransfer.setData('text/plain', String(dragId));
      ev.dataTransfer.effectAllowed = 'move';
    });
    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      dragId = null;
      board.querySelectorAll('.kanban-col').forEach((c) => c.classList.remove('drag-over'));
    });
  });
  board.querySelectorAll('.kanban-col').forEach((col) => {
    col.addEventListener('dragover', (ev) => {
      ev.preventDefault();
      col.classList.add('drag-over');
    });
    col.addEventListener('dragleave', () => col.classList.remove('drag-over'));
    col.addEventListener('drop', async (ev) => {
      ev.preventDefault();
      col.classList.remove('drag-over');
      const id = ev.dataTransfer.getData('text/plain') || dragId;
      const stageId = col.dataset.stageId;
      if (!id || !stageId) return;
      const from = document.querySelector(`.kanban-card[data-id="${id}"]`);
      if (from && String(from.dataset.stageId) === String(stageId)) return;
      try {
        await moveProspectStage(id, stageId, 'Drag-drop move');
      } catch (e) {
        toast(e.message || String(e), true);
      }
    });
  });
}

function wirePipelineFilters() {
  const reload = () => loadPipeline().catch((e) => toast(e.message, true));
  $('pipelineRefresh') && $('pipelineRefresh').addEventListener('click', reload);
  $('pipelineLine') && $('pipelineLine').addEventListener('change', reload);
  $('pipelineCountry') &&
    $('pipelineCountry').addEventListener('change', () => {
      if ($('pipelineCity')) $('pipelineCity').value = '';
      reload();
    });
  $('pipelineCity') && $('pipelineCity').addEventListener('change', reload);
  $('pipelineCategory') && $('pipelineCategory').addEventListener('change', reload);
}

wirePipelineFilters();
registerPanel({ id: 'pipeline', refresh: safeRefresh(loadPipeline) });
B.loadPipeline = loadPipeline;
B.moveProspectStage = moveProspectStage;
