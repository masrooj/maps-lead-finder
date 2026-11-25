/**
 * Markets glance — country × city contact counts.
 * Click a cell → Discover (discovered) or Contact queue tab with that geo.
 */
import { $ } from '../core/dom.js';
import { api, esc, toast } from '../core/api.js';
import { listSkeletonHtml } from '../core/skeleton.js';
import { registerPanel } from './registry.js';
import { B } from '../app/bridge.js';
import { safeRefresh } from '../shell/router.js';

export const panelId = 'geoGlance';

const BUCKETS = [
  { key: 'discovered', label: 'Discovered', goto: 'leads', status: 'fresh' },
  { key: 'selected', label: 'To contact', goto: 'contactQueue:selected' },
  { key: 'lined_up', label: 'Lined up', goto: 'contactQueue:lined_up' },
  { key: 'ready', label: 'Ready', goto: 'contactQueue:ready' },
  { key: 'contacted', label: 'Contacted', goto: 'contactQueue:contacted' },
  { key: 'plus3', label: 'Follow-up +3', goto: 'contactQueue:plus3' },
  { key: 'plus7', label: 'Follow-up +7', goto: 'contactQueue:plus7' },
  { key: 'engaged', label: 'Engaged', goto: 'contactQueue:engaged' },
  { key: 'booking', label: 'Has booking', goto: 'contactQueue:booking' },
  { key: 'cannot', label: "Can't contact", goto: 'contactQueue:cannot' },
  { key: 'connected', label: 'Connected', goto: 'contactQueue:connected' },
];

function blankPlace(v) {
  const s = String(v || '').trim();
  return !s || s === '—' ? '' : s;
}

function openBucket(bucketKey, country, city) {
  const opts = {
    country: blankPlace(country),
    city: blankPlace(city),
  };
  if (bucketKey === 'all') {
    if (typeof B.setAppPanel === 'function') B.setAppPanel('contactQueue:all', opts);
    return;
  }
  const meta = BUCKETS.find((b) => b.key === bucketKey);
  if (!meta) return;
  if (meta.status) opts.status = meta.status;
  if (typeof B.setAppPanel === 'function') B.setAppPanel(meta.goto, opts);
}

function cellBtn(count, bucketKey, country, city) {
  const n = Number(count || 0);
  const disabled = n <= 0;
  return `<button type="button" class="geo-cell${disabled ? ' is-zero' : ''}" data-geo-bucket="${esc(bucketKey)}" data-geo-country="${esc(country || '')}" data-geo-city="${esc(city || '')}" ${disabled ? 'disabled' : ''} title="${esc(BUCKETS.find((b) => b.key === bucketKey)?.label || bucketKey)}">${n}</button>`;
}

function renderTable(payload) {
  const host = $('geoGlanceTable');
  if (!host) return;
  const rows = payload.rows || [];
  const totals = payload.totals || {};
  if (!rows.length) {
    host.innerHTML = '<div class="empty-card">No market counts yet — search Discover or open Contact queue after leads exist.</div>';
    return;
  }
  const head = BUCKETS.map((b) => `<th scope="col" title="${esc(b.label)}">${esc(b.label)}</th>`).join('');
  const body = rows.map((r) => {
    const country = r.country || '—';
    const city = r.city || '—';
    const cells = BUCKETS.map((b) => `<td>${cellBtn(r[b.key], b.key, country, city)}</td>`).join('');
    return `<tr>
      <th scope="row" class="geo-place">
        <button type="button" class="geo-place-btn" data-geo-bucket="all" data-geo-country="${esc(country)}" data-geo-city="${esc(city)}" title="Open Contact queue All for this market">
          <strong>${esc(country)}</strong><span>${esc(city)}</span>
        </button>
      </th>
      ${cells}
    </tr>`;
  }).join('');
  const foot = BUCKETS.map((b) => `<td>${cellBtn(totals[b.key], b.key, '', '')}</td>`).join('');
  host.innerHTML = `<div class="geo-table-wrap" role="region" aria-label="Market contact counts">
    <table class="geo-table">
      <thead><tr><th scope="col">Market</th>${head}</tr></thead>
      <tbody>${body}</tbody>
      <tfoot><tr><th scope="row">All markets</th>${foot}</tr></tfoot>
    </table>
  </div>
  <p class="hint" style="margin-top:10px">Counts only. Click a number to open that Contact queue section (or Discover for Discovered) scoped to the country/city. Totals row opens without a geo filter.</p>`;

  host.querySelectorAll('[data-geo-bucket]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      openBucket(btn.dataset.geoBucket, btn.dataset.geoCountry, btn.dataset.geoCity);
    });
  });
}

export async function loadGeoGlance() {
  const host = $('geoGlanceTable');
  const note = $('geoGlanceNote');
  if (host) host.innerHTML = listSkeletonHtml({ rows: 4, label: 'Loading market counts…' });
  try {
    const data = await api('/api/sales/contact-queue/geo-counts?product_line=STANDALONE&limit=5000');
    renderTable(data || {});
    if (note) {
      const n = (data.rows || []).length;
      const sample = data.sample_size || 0;
      note.textContent = `${n} market${n === 1 ? '' : 's'} · ${sample} open leads in ranking scope`;
    }
  } catch (e) {
    if (host) host.innerHTML = `<div class="empty-card">${esc(e.message || 'Failed to load counts')}</div>`;
    toast(e.message || 'Failed to load market counts', true);
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('geoGlanceRefresh');
    if (btn && !btn.dataset.bound) {
      btn.dataset.bound = '1';
      btn.addEventListener('click', () => loadGeoGlance().catch(() => {}));
    }
  });
}

registerPanel({ id: 'geoGlance', refresh: safeRefresh(loadGeoGlance) });
