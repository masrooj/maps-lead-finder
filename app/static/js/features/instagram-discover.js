/**
 * Discover Instagram from business website via Sales OS API
 * (server fetches HTML — no browser CORS). Enables IG buttons when the site links IG.
 */
import { api } from '../core/api.js';

const inflight = new Map();
const done = new Map();

export function igFromLead(b) {
  const s = (b && b.social_links) || {};
  return String(s.instagram || b.instagram || '').trim();
}

export function leadNeedsIgDiscover(b) {
  if (!b) return false;
  if (igFromLead(b)) return false;
  return !!(b.website || '').trim();
}

/**
 * @returns {Promise<{found:boolean, instagram:string, business?:object}>}
 */
export async function discoverInstagram(businessId, { force = false } = {}) {
  const id = Number(businessId);
  if (!Number.isFinite(id)) return { found: false, instagram: '' };
  const key = String(id);
  if (!force && done.has(key)) return done.get(key);
  if (inflight.has(key)) return inflight.get(key);
  const p = api('/api/businesses/' + id + '/discover-instagram' + (force ? '?force=true' : ''), {
    method: 'POST',
    body: {},
  })
    .then((res) => {
      const out = {
        found: !!res.found,
        instagram: String(res.instagram || '').trim(),
        persisted: !!res.persisted,
        business: res.business || null,
      };
      done.set(key, out);
      return out;
    })
    .catch((e) => {
      const out = { found: false, instagram: '', error: e };
      done.set(key, out);
      return out;
    })
    .finally(() => {
      inflight.delete(key);
    });
  inflight.set(key, p);
  return p;
}

/** Patch local lead cache after discovery. */
export function applyDiscoveredIg(lead, instagram) {
  if (!lead || !instagram) return lead;
  const social = { ...(lead.social_links || {}), instagram };
  lead.social_links = social;
  lead.channels = { ...(lead.channels || {}), instagram: true };
  if ('instagram' in lead) lead.instagram = instagram;
  return lead;
}

/**
 * Ensure lead has Instagram when website may link it.
 * Mutates `lead` in place when found. Safe no-op if already has IG / no website / no id.
 */
export async function ensureLeadInstagram(lead, { force = false } = {}) {
  if (!lead) return { found: false, instagram: '' };
  const existing = igFromLead(lead);
  if (existing && !force) return { found: true, instagram: existing, persisted: false };
  if (!leadNeedsIgDiscover(lead) && !force) return { found: false, instagram: '' };
  const id = Number(lead.id ?? lead.business_id);
  if (!Number.isFinite(id)) return { found: false, instagram: '' };
  const res = await discoverInstagram(id, { force });
  if (res.found && res.instagram) applyDiscoveredIg(lead, res.instagram);
  return res;
}

export function clearIgDiscoverCache(businessId) {
  if (businessId == null) {
    done.clear();
    return;
  }
  done.delete(String(businessId));
}
