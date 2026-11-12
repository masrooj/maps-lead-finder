/** Panel owner: `leads` (Discover).
 * Discover = fresh pool only: not To contact, not Contacted, not parks.
 * Triage: ★ mark · Has booking · Can't contact · edit contacts.
 * Messaging / warm / Already connected live on Contact queue.
 */
import { api, toast } from '../core/api.js';
import { uiForm } from '../core/dialog.js';
import { isFreshDiscoverLead as sharedFresh } from '../features/lead-workflow.js';
import {
  cannotViaLabel,
  markCannotContact,
} from '../features/cannot-contact.js';
import { registerPanel } from './registry.js';
import { B } from '../app/bridge.js';

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
 * Mark: leaves Discover → Contact queue To contact (runtime navigates).
 * Unmark: returns to Discover when still fresh.
 */
export async function toggleDiscoverMark(id, row, { patchLead } = {}) {
  if (!row || typeof patchLead !== 'function') throw new Error('patchLead required');
  const nextMarked = !row.marked_for_contact;
  const updated = await patchLead(id, { marked_for_contact: nextMarked });
  if (row) {
    if (updated && typeof updated === 'object') Object.assign(row, updated);
    row.marked_for_contact = nextMarked;
    if (nextMarked) row.ready_for_contact = false;
  }
  toast(nextMarked ? 'Moved to To contact' : 'Unmarked — back in Discover.');
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

registerPanel({
  id: panelId,
  refresh() {
    if (typeof B.reloadDiscover === 'function') return B.reloadDiscover();
  },
});
