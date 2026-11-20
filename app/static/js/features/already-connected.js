/**
 * Already-connected park API helpers (Ready → shared IG/WA/email other location).
 */
import { api } from '../core/api.js';

export function isAlreadyConnected(b) {
  return !!(b && b.already_connected);
}

export function alreadyConnectedPrimaryId(b) {
  if (!b || !b.already_connected) return null;
  const id = b.already_connected_primary_id;
  return id != null && id !== '' ? Number(id) : null;
}

/** GET /api/sales/prospects/{id}/same-contact-siblings */
export function fetchSameContactSiblings(id, limit = 25) {
  const q = limit ? `?limit=${encodeURIComponent(limit)}` : '';
  return api('/api/sales/prospects/' + id + '/same-contact-siblings' + q);
}

/** POST /api/sales/prospects/{id}/already-connected */
export function markAlreadyConnected(id, { primary_id, notes = '' } = {}) {
  const body = { primary_id: Number(primary_id) };
  if (notes && String(notes).trim()) body.notes = String(notes).trim();
  return api('/api/sales/prospects/' + id + '/already-connected', { method: 'POST', body });
}

/** POST /api/sales/prospects/{id}/clear-already-connected */
export function clearAlreadyConnected(id, rematch = true) {
  const q = rematch ? '?rematch=true' : '?rematch=false';
  return api('/api/sales/prospects/' + id + '/clear-already-connected' + q, { method: 'POST', body: {} });
}

/** POST /api/sales/prospects/{id}/ig-awaiting-follow-back */
export function setIgAwaitingFollowBack(id, awaiting = true) {
  return api('/api/sales/prospects/' + id + '/ig-awaiting-follow-back', {
    method: 'POST',
    body: { awaiting: !!awaiting },
  });
}

/** POST /api/sales/prospects/{id}/mark-engaged — note and/or customer_response. */
export function markEngaged(id, noteOrOpts = '', customerResponse = '') {
  const body = {};
  if (noteOrOpts && typeof noteOrOpts === 'object') {
    if (noteOrOpts.note && String(noteOrOpts.note).trim()) body.note = String(noteOrOpts.note).trim();
    if (noteOrOpts.customer_response && String(noteOrOpts.customer_response).trim()) {
      body.customer_response = String(noteOrOpts.customer_response).trim();
    }
  } else {
    if (noteOrOpts && String(noteOrOpts).trim()) body.note = String(noteOrOpts).trim();
    if (customerResponse && String(customerResponse).trim()) {
      body.customer_response = String(customerResponse).trim();
    }
  }
  return api('/api/sales/prospects/' + id + '/mark-engaged', { method: 'POST', body });
}

/** POST /api/sales/prospects/{id}/mark-for-contact — Discover / All → To contact. */
export function markForContact(id, marked = true) {
  return api('/api/sales/prospects/' + id + '/mark-for-contact', {
    method: 'POST',
    body: { marked: !!marked },
  });
}

/** POST /api/sales/prospects/bulk-mark-for-contact */
export function bulkMarkForContact(ids, marked = true) {
  return api('/api/sales/prospects/bulk-mark-for-contact', {
    method: 'POST',
    body: { ids: (ids || []).map(Number).filter(Boolean), marked: !!marked },
  });
}

export function isEngaged(b) {
  if (!b) return false;
  if (b.engaged) return true;
  return !!(b.stage && b.stage.slug === 'engaged');
}
