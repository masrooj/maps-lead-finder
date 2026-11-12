/** Panel owner: `leads` (Discover).
 * Discover = fresh pool only: not To contact, not Contacted, not Has booking, not Already connected.
 * Unmarking from To contact returns a lead here when those flags stay clear.
 * Mark / Has booking actions owned here; list load/paint still thin via bridge + runtime until full peel.
 */
import { api, toast } from '../core/api.js';
import { isFreshDiscoverLead as sharedFresh } from '../features/lead-workflow.js';
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

/** Toggle ★ mark for contact — Discover owner. */
export async function toggleDiscoverMark(id, row, { patchLead } = {}) {
  if (!row || typeof patchLead !== 'function') throw new Error('patchLead required');
  const nextMarked = !row.marked_for_contact;
  await patchLead(id, { marked_for_contact: nextMarked });
  toast(nextMarked ? 'Marked for contact — left Discover.' : 'Unmarked — back in Discover.');
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

registerPanel({
  id: panelId,
  refresh() {
    if (typeof B.reloadDiscover === 'function') return B.reloadDiscover();
  },
});
