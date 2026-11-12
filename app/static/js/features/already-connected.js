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

/** POST /api/sales/prospects/{id}/mark-engaged */
export function markEngaged(id, note = '') {
  const body = {};
  if (note && String(note).trim()) body.note = String(note).trim();
  return api('/api/sales/prospects/' + id + '/mark-engaged', { method: 'POST', body });
}
