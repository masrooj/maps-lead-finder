/** Panel owner: `leads` (Discover).
 * Discover = fresh pool only: not To contact, not Contacted, not parks.
 * Triage: ★ mark · Has booking · Can't contact · edit contacts.
 * Messaging / warm / Already connected live on Contact queue.
 */
import { api, toast } from '../core/api.js';
import { $ } from '../core/dom.js';
import { uiConfirm, uiForm } from '../core/dialog.js';
import { isFreshDiscoverLead as sharedFresh } from '../features/lead-workflow.js';
import {
  cannotViaLabel,
  markCannotContact,
} from '../features/cannot-contact.js';
import { markForContact } from '../features/already-connected.js';
import { registerPanel } from './registry.js';
import { B } from '../app/bridge.js';

let scanBookingRunning = false;
let scanBookingCaps = { limit: 60, chunk: 8 };

export function refreshScanBookingMeta(settings) {
  const s = settings || {};
  const lim = parseInt(s.scan_booking_limit, 10);
  const chunk = parseInt(s.scan_booking_chunk, 10);
  if (Number.isFinite(lim) && lim >= 1 && lim <= 200) scanBookingCaps.limit = lim;
  if (Number.isFinite(chunk) && chunk >= 1 && chunk <= 50) scanBookingCaps.chunk = chunk;
  if (scanBookingCaps.chunk > scanBookingCaps.limit) scanBookingCaps.chunk = scanBookingCaps.limit;
  const meta = $('scanBookingMeta');
  if (meta) {
    meta.textContent = `up to ${scanBookingCaps.limit}`;
    meta.title = `Max ${scanBookingCaps.limit} leads · batch ${scanBookingCaps.chunk} · change in Settings`;
  }
}

export async function loadScanBookingCaps() {
  try {
    const data = await api('/api/sales/settings');
    refreshScanBookingMeta((data && data.settings) || {});
  } catch (_) {
    refreshScanBookingMeta({});
  }
  return { ...scanBookingCaps };
}

export const panelId = 'leads';

/** API status for the Discover city list. */
export const DISCOVER_STATUS = 'fresh';

/** True when a lead belongs on Discover (fresh discovered list). */
export function isFreshDiscoverLead(b){
  return sharedFresh(b);
}

/** City Discover default when status is empty; keep explicit choices (incl. all). */
export function resolveDiscoverStatus(status){
  const s=String(status||'').trim();
  if(!s) return DISCOVER_STATUS;
  return s;
}

/** Toggle ★ mark for contact — Discover owner.
 * Mark: drops from Discover list; stays on Discover (no panel/tab jump).
 * Unmark: returns to Discover when still fresh.
 * Uses POST /api/sales/prospects/{id}/mark-for-contact (mobile + web).
 */
export async function toggleDiscoverMark(id, row, { patchLead } = {}) {
  if (!row) throw new Error('row required');
  const nextMarked = !row.marked_for_contact;
  let updated = null;
  try {
    const res = await markForContact(id, nextMarked);
    updated = (res && res.prospect) || res;
  } catch (e) {
    // Fallback to legacy PATCH if sales route unavailable
    if (typeof patchLead !== 'function') throw e;
    updated = await patchLead(id, { marked_for_contact: nextMarked });
  }
  if (row) {
    if (updated && typeof updated === 'object') Object.assign(row, updated);
    row.marked_for_contact = nextMarked;
    if (nextMarked) row.ready_for_contact = false;
  }
  // Keep Discover list prune when using sales endpoint (patchLead already prunes).
  if (nextMarked && typeof patchLead === 'function' && updated && updated.marked_for_contact) {
    /* caller (toggleMark) also filters rowsData */
  }
  toast(nextMarked ? 'Marked for To contact' : 'Unmarked — back in Discover.');
  return nextMarked;
}

/** Has booking / clear from Discover — uses CQ markBookingIds via bridge. */
export async function setDiscoverBooking(id, on, row) {
  if (typeof B.markBookingIds === 'function') {
    await B.markBookingIds([id], on);
  } else {
    await api('/api/sales/prospects/' + id, {
      method: 'PATCH',
      body: { current_booking_software: on ? 'Has booking' : '' },
    });
  }
  if (row) {
    row.current_booking_software = on ? 'Has booking' : '';
    row.has_booking = !!on;
    if (on) {
      row.marked_for_contact = false;
      row.ready_for_contact = false;
    }
  }
  return row;
}

/** Can't contact from Discover — parks to Contact queue Can't contact tab. */
export async function markDiscoverCannotContact(id, row) {
  const form = await uiForm({
    title: "Can't contact",
    message: 'Park this lead when WhatsApp, Instagram, or both are unreachable.',
    confirmLabel: 'Save',
    fields: [
      {
        name: 'via',
        label: 'Channel',
        type: 'select',
        required: true,
        value: 'both',
        options: [
          { value: 'whatsapp', label: 'WhatsApp only' },
          { value: 'instagram', label: 'Instagram only' },
          { value: 'both', label: 'WhatsApp + Instagram' },
        ],
      },
      {
        name: 'notes',
        label: 'Note (optional)',
        type: 'textarea',
        rows: 3,
        placeholder: 'e.g. WA number invalid · IG DMs closed',
      },
    ],
  });
  if (!form) return null;
  const updated = await markCannotContact(id, { via: form.via, notes: form.notes || '' });
  if (row) {
    Object.assign(row, updated || {});
    row.cannot_contact = true;
    row.cannot_contact_via = form.via;
    row.marked_for_contact = true;
    row.contacted = false;
  }
  toast(`Can't contact · ${cannotViaLabel(form.via)}`);
  return { updated, via: form.via };
}

/** Alias used by older call sites. */
export const setDiscoverCannotContact = markDiscoverCannotContact;

/**
 * Bulk Scan booking for fresh Discover leads.
 * @param {{ country?: string, city?: string, ids?: number[], onProgress?: (msg: string) => void }} opts
 */
export async function scanBookingFreshLeads(opts = {}) {
  const country = String(opts.country || '').trim();
  const city = String(opts.city || '').trim();
  let ids = [...new Set((opts.ids || []).map(Number).filter(Boolean))];
  const scopeLabel = country || city
    ? [country, city].filter(Boolean).join(' · ')
    : 'all locations';

  if (scanBookingRunning) {
    toast('Scan booking already running…');
    return null;
  }

  await loadScanBookingCaps();
  const maxLeads = scanBookingCaps.limit;
  const chunk = scanBookingCaps.chunk;
  if (ids.length > maxLeads) ids = ids.slice(0, maxLeads);

  const ok = await uiConfirm({
    title: 'Scan booking',
    message: ids.length
      ? `Scan ${ids.length} selected fresh lead${ids.length === 1 ? '' : 's'} in ${scopeLabel}?\nBooking → Has booking · else → To contact\n(Limit ${maxLeads} · batch ${chunk} — Settings)`
      : `Scan up to ${maxLeads} fresh leads in ${scopeLabel}?\nBooking → Has booking · else → To contact\n(Batch ${chunk} — change Limits in Settings)`,
    confirmLabel: 'Scan booking',
  });
  if (!ok) return null;

  scanBookingRunning = true;
  const btn = $('scanBookingBtn');
  if (btn) btn.disabled = true;

  let scanned = 0;
  let hasBooking = 0;
  let toContact = 0;
  let skipped = 0;
  let failed = 0;

  try {
    if (ids.length) {
      for (let i = 0; i < ids.length; i += chunk) {
        const slice = ids.slice(i, i + chunk);
        const label = `Scanning ${Math.min(i + slice.length, ids.length)}/${ids.length}…`;
        if (btn) btn.textContent = label;
        if (typeof opts.onProgress === 'function') opts.onProgress(label);
        const data = await api('/api/businesses/scan-booking', {
          method: 'POST',
          body: {
            ids: slice,
            country,
            city,
            limit: slice.length,
          },
        });
        scanned += data.scanned || 0;
        hasBooking += data.has_booking || 0;
        toContact += data.to_contact || 0;
        skipped += data.skipped || 0;
        failed += data.failed || 0;
      }
    } else {
      let remaining = maxLeads;
      let guard = 0;
      const maxBatches = Math.max(1, Math.ceil(maxLeads / chunk) + 2);
      while (remaining > 0 && guard < maxBatches) {
        guard += 1;
        const batch = Math.min(chunk, remaining);
        const label = `Scanning… ${scanned}/${maxLeads}`;
        if (btn) btn.textContent = label;
        if (typeof opts.onProgress === 'function') opts.onProgress(label);
        const data = await api('/api/businesses/scan-booking', {
          method: 'POST',
          body: { country, city, limit: batch },
        });
        const got = data.scanned || 0;
        scanned += got;
        hasBooking += data.has_booking || 0;
        toContact += data.to_contact || 0;
        skipped += data.skipped || 0;
        failed += data.failed || 0;
        remaining -= got;
        if (!got) break;
        if (got < batch) break;
      }
    }

    const summary = `Scan done: ${hasBooking} Has booking · ${toContact} To contact`
      + (skipped ? ` · ${skipped} skipped` : '')
      + (failed ? ` · ${failed} failed` : '');
    toast(summary);
    if (typeof B.reloadDiscover === 'function') {
      await B.reloadDiscover({ force: true });
    }
    return { scanned, has_booking: hasBooking, to_contact: toContact, skipped, failed };
  } catch (e) {
    toast(e.message || String(e), true);
    return null;
  } finally {
    scanBookingRunning = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Scan booking';
    }
  }
}

/** Bind Discover toolbar Scan booking controls (country/city from DOM). */
export function bindScanBookingButton(getRowsIds) {
  const btn = $('scanBookingBtn');
  if (btn && btn.dataset.bound !== '1') {
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
      const country = ($('country') && $('country').value || '').trim();
      const city = ($('city') && $('city').value || '').trim();
      let ids = [];
      if (typeof getRowsIds === 'function') {
        ids = getRowsIds() || [];
      }
      await scanBookingFreshLeads({ country, city, ids });
    });
  }
  const limitsBtn = $('scanBookingSettingsBtn');
  if (limitsBtn && limitsBtn.dataset.bound !== '1') {
    limitsBtn.dataset.bound = '1';
    limitsBtn.addEventListener('click', () => {
      if (typeof B.setAppPanel === 'function') B.setAppPanel('settings');
      if (typeof B.setSettingsTab === 'function') B.setSettingsTab('general');
      else {
        const tab = document.querySelector('#settingsTabs [data-settings-tab="general"]');
        if (tab) tab.click();
      }
      setTimeout(() => {
        const card = $('settingsScanBookingCard');
        const el = $('scanBookingLimit');
        if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        if (el && el.focus) el.focus();
      }, 160);
    });
  }
  loadScanBookingCaps().catch(() => {});
  B.refreshScanBookingMeta = refreshScanBookingMeta;
}

async function tickJobUntilDone(jobId) {
  let tick = await api('/api/jobs/' + jobId + '/tick', { method: 'POST', body: {} });
  let guard = 0;
  while (!tick.done && guard < 100) {
    guard += 1;
    tick = await api('/api/jobs/' + jobId + '/tick', { method: 'POST', body: {} });
  }
  return tick;
}

/** v2: start maps_booking_verify job for current Discover scope / selected ids. */
export async function runMapsBookingVerifyJob(opts = {}) {
  const country = String(opts.country || '').trim();
  const city = String(opts.city || '').trim();
  const ids = [...new Set((opts.ids || []).map(Number).filter(Boolean))];
  const limit = Math.max(1, Math.min(50, Number(opts.limit) || 25));
  const scopeLabel = country || city
    ? [country, city].filter(Boolean).join(' · ')
    : 'selected / scoped leads';
  const ok = await uiConfirm({
    title: 'Verify Maps booking',
    message: `Run Maps Reserve/Book verify for ${scopeLabel}? Confirmed → Has booking · no booking → To contact · uncertain left for manual.`,
    confirmLabel: 'Verify',
  });
  if (!ok) return null;
  const btn = $('mapsVerifyBtn');
  if (btn) btn.disabled = true;
  try {
    const payload = {
      country,
      city,
      limit,
      park_only_on_confirmed: true,
    };
    if (ids.length) payload.business_ids = ids.slice(0, limit);
    const job = await api('/api/sales/jobs', {
      method: 'POST',
      body: { job_type: 'maps_booking_verify', payload },
    });
    const tick = await tickJobUntilDone(job.id);
    const stats = tick.stats || {};
    toast(
      `Maps verify: ${stats.parked || 0} Has booking · ${stats.to_contact || 0} To contact · ${stats.skipped || 0} skipped`,
    );
    if (typeof B.reloadDiscover === 'function') await B.reloadDiscover({ force: true });
    return tick;
  } catch (e) {
    toast(e.message || String(e), true);
    return null;
  } finally {
    if (btn) btn.disabled = false;
  }
}

export function bindMapsVerifyButton(getRowsIds) {
  const btn = $('mapsVerifyBtn');
  if (!btn || btn.dataset.bound === '1') return;
  btn.dataset.bound = '1';
  // Hide on Vercel live (no Playwright). Scan booking remains the live path.
  api('/api/health')
    .then((h) => {
      const ok = !!(h && h.capabilities && h.capabilities.maps_verify);
      btn.hidden = !ok;
      btn.title = ok
        ? 'Maps Reserve/Book click-verify'
        : 'Maps verify needs local Chromium — use Scan booking on Vercel';
    })
    .catch(() => {
      btn.hidden = true;
    });
  btn.addEventListener('click', async () => {
    const country = ($('country') && $('country').value || '').trim();
    const city = ($('city') && $('city').value || '').trim();
    let ids = [];
    if (typeof getRowsIds === 'function') ids = getRowsIds() || [];
    await runMapsBookingVerifyJob({ country, city, ids, limit: 25 });
  });
}

registerPanel({
  id: panelId,
  refresh() {
    if (typeof B.reloadDiscover === 'function') return B.reloadDiscover({ force: true });
  },
});
