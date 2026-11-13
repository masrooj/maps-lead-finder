/** In-memory list cache gated by cheap meta fingerprints (count + max_updated_at + mode). */
import { api } from './api.js';

const DEFAULT_TTL_MS = 90_000;
const store = new Map();

export function fingerprintEqual(a, b) {
  if (!a || !b) return false;
  return (
    String(a.count ?? '') === String(b.count ?? '') &&
    String(a.max_updated_at || '') === String(b.max_updated_at || '') &&
    String(a.mode || '') === String(b.mode || '')
  );
}

export function fingerprintFromMeta(meta) {
  if (!meta || typeof meta !== 'object') return null;
  return {
    // CQ meta uses `count`; Discover /api/stats uses `total`
    count: meta.count ?? meta.total ?? 0,
    max_updated_at: meta.max_updated_at || '',
    mode: meta.mode || '',
  };
}

/** @param {string|'all'|string[]} keys */
export function invalidateListCache(keys = 'all') {
  if (keys === 'all' || keys == null) {
    store.clear();
    return;
  }
  const list = Array.isArray(keys) ? keys : [keys];
  for (const k of list) store.delete(k);
}

/**
 * @param {{
 *   key: string,
 *   metaUrl: string,
 *   metaParams?: Record<string,string>,
 *   fullFetch: () => Promise<any>,
 *   force?: boolean,
 *   ttlMs?: number,
 * }} opts
 */
export async function refreshIfChanged(opts) {
  const {
    key,
    metaUrl,
    metaParams = {},
    fullFetch,
    force = false,
    ttlMs = DEFAULT_TTL_MS,
  } = opts;
  const entry = store.get(key);
  const now = Date.now();

  let fp = null;
  try {
    const qs = new URLSearchParams();
    Object.entries(metaParams || {}).forEach(([k, v]) => {
      if (v != null && String(v) !== '') qs.set(k, String(v));
    });
    const q = qs.toString();
    const meta = await api(metaUrl + (q ? `?${q}` : ''));
    fp = fingerprintFromMeta(meta);
  } catch (_) {
    fp = null;
  }

  const withinTtl = entry && now - entry.fetchedAt < ttlMs;
  if (!force && entry && fp && fingerprintEqual(fp, entry.fingerprint) && withinTtl) {
    return { hit: true, payload: entry.payload, fingerprint: entry.fingerprint };
  }

  const gen = (entry && entry.gen ? entry.gen : 0) + 1;
  if (entry) entry.gen = gen;
  else store.set(key, { fingerprint: null, payload: null, fetchedAt: 0, gen });

  const payload = await fullFetch();
  const cur = store.get(key);
  if (cur && cur.gen > gen) {
    return { hit: false, stale: true, payload: cur.payload, fingerprint: cur.fingerprint };
  }

  const storedFp =
    fp ||
    fingerprintFromMeta({
      count: payload?.total_candidates ?? payload?.count ?? payload?.total ?? 0,
      max_updated_at: payload?.max_updated_at || '',
      mode: payload?.mode || '',
    });

  store.set(key, {
    fingerprint: storedFp,
    payload,
    fetchedAt: Date.now(),
    gen,
  });
  return { hit: false, payload, fingerprint: storedFp };
}
