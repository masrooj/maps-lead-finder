/**
 * Shared lead-workflow predicates (Discover / Contact queue / compose).
 * Single owner for freshness + park + messaging gates — panels import from here.
 */
import { isCannotContact } from './cannot-contact.js';
import { isAlreadyConnected } from './already-connected.js';

export function leadHasBooking(b) {
  return !!(b && (b.has_booking || (b.current_booking_software || '').trim()));
}

export function isFreshDiscoverLead(b) {
  if (!b) return false;
  if (b.archived) return false;
  if (b.marked_for_contact) return false;
  if (b.contacted) return false;
  if (b.lost) return false;
  if (leadHasBooking(b)) return false;
  if (isAlreadyConnected(b)) return false;
  if (isCannotContact(b)) return false;
  return true;
}

export function canMessageLead(b) {
  if (!b) return { ok: false, reason: 'No lead' };
  if (isAlreadyConnected(b)) return { ok: false, reason: 'Already connected to another location' };
  if (isCannotContact(b)) return { ok: false, reason: "Can't contact" };
  if (leadHasBooking(b)) return { ok: false, reason: 'Has booking' };
  if (b.ig_awaiting_follow_back) return { ok: false, reason: 'Awaiting IG follow-back' };
  if (b.can_message === false) {
    return { ok: false, reason: b.can_message_reason || 'Cannot message yet' };
  }
  const wait = b.ig_ready_wait;
  if (wait && wait.applies && wait.unlocked === false) {
    return { ok: false, reason: wait.reason || 'IG Ready wait not elapsed' };
  }
  if (!b.ready_for_contact && !(b.contacted || b.email_sent || b.whatsapp_sent || b.instagram_sent)) {
    return { ok: false, reason: 'Move to Ready to contact first' };
  }
  return { ok: true, reason: '' };
}
