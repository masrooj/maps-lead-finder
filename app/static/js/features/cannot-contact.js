/**
 * Cannot-contact API + helpers (Contact queue).
 * Proper columns: cannot_contact + cannot_contact_via (whatsapp|instagram|both).
 * Legacy: outcome cannot_contact_* still recognized via API payload.
 */
import { api } from '../core/api.js';

export const CANNOT_VIA = ['whatsapp', 'instagram', 'both'];

export function normalizeCannotVia(raw) {
  const v = String(raw || '').trim().toLowerCase();
  if (v === 'wa' || v === 'whatsapp' || v === 'phone') return 'whatsapp';
  if (v === 'ig' || v === 'instagram' || v === 'insta') return 'instagram';
  if (v === 'both' || v === 'all' || v === 'wa_ig') return 'both';
  return '';
}

export function viaFromOutcome(outcome) {
  const o = String(outcome || '').trim().toLowerCase();
  if (o === 'cannot_contact_whatsapp') return 'whatsapp';
  if (o === 'cannot_contact_instagram') return 'instagram';
  if (o === 'cannot_contact_both' || o === 'cannot_contact') return 'both';
  if (o.startsWith('cannot_contact_')) {
    return normalizeCannotVia(o.slice('cannot_contact_'.length)) || 'both';
  }
  if (o.startsWith('cannot_contact')) return 'both';
  return '';
}

export function isCannotContact(b) {
  if (!b) return false;
  if (b.cannot_contact) return true;
  return !!viaFromOutcome(b.outcome);
}

export function cannotContactVia(b) {
  if (!b) return '';
  const via = normalizeCannotVia(b.cannot_contact_via);
  if (via) return via;
  return viaFromOutcome(b.outcome);
}

export function cannotViaLabel(via) {
  const v = normalizeCannotVia(via) || via;
  if (v === 'whatsapp') return 'WhatsApp';
  if (v === 'instagram') return 'Instagram';
  if (v === 'both') return 'WhatsApp + Instagram';
  return v || '—';
}

/** POST /api/businesses/{id}/cannot-contact */
export function markCannotContact(id, { via = 'both', notes = '' } = {}) {
  const body = { via: normalizeCannotVia(via) || 'both' };
  if (notes && String(notes).trim()) body.notes = String(notes).trim();
  return api('/api/businesses/' + id + '/cannot-contact', { method: 'POST', body });
}

/** POST /api/businesses/{id}/clear-cannot-contact?rematch=true */
export function clearCannotContact(id, rematch = true) {
  const q = rematch ? '?rematch=true' : '?rematch=false';
  return api('/api/businesses/' + id + '/clear-cannot-contact' + q, { method: 'POST', body: {} });
}
