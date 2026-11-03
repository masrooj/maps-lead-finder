/** Feature owner: compose.
 * SOLID: extend this module for compose behavior; peel from app/runtime.js on touch.
 * Cross-panel calls go through app/bridge.js (B.*), not new globals.
 */
export const FEATURE = 'compose';

/** Cold outreach order: WhatsApp first when available, then email, then Instagram. */
export const COLD_CHANNELS = ['whatsapp', 'email', 'instagram'];

export function channelLabel(ch) {
  return ch === 'email' ? 'Email'
    : ch === 'whatsapp' ? 'WhatsApp'
    : ch === 'instagram' ? 'Instagram'
    : ch === 'invoice' ? 'Invoice'
    : ch;
}

const IG_BLOCKED_PATHS = new Set([
  'p', 'reel', 'reels', 'stories', 'direct', 'explore', 'accounts', 'account',
  'about', 'legal', 'developer', 'directory', 'nametag', 'login', 'signup',
]);

/**
 * Coerce stored IG values into something URL()/handle parsing can read.
 * Handles: @user, user, instagram.com/user, www.instagram.com/user, //instagram.com/user
 */
function normalizeInstagramInput(url) {
  let raw = String(url || '').trim();
  if (!raw) return '';
  if (raw.startsWith('//')) raw = `https:${raw}`;
  // Scheme-less host paths — new URL('instagram.com/x') throws and used to yield ''
  if (/^(www\.)?instagram\.com([\/?#]|$)/i.test(raw)) {
    raw = `https://${raw}`;
  }
  return raw;
}

/** True Instagram username chars only (no @, no path). */
function sanitizeIgUsername(part) {
  const h = String(part || '').replace(/^@/, '').trim().split(/[/?#]/)[0].trim();
  if (!h) return '';
  if (!/^[A-Za-z0-9._]{1,30}$/.test(h)) return '';
  if (IG_BLOCKED_PATHS.has(h.toLowerCase())) return '';
  return h;
}

/** Parse @username from an Instagram profile URL (or bare handle). Never returns login paths. */
export function instagramHandle(url) {
  const raw0 = String(url || '').trim();
  if (!raw0) return '';
  const raw = normalizeInstagramInput(raw0);
  if (!/^https?:\/\//i.test(raw)) {
    // Bare @handle / handle (never treat as a relative page href)
    return sanitizeIgUsername(raw);
  }
  try {
    const u = new URL(raw);
    const host = (u.hostname || '').toLowerCase();
    if (host && !host.includes('instagram.com')) return '';
    const path = decodeURIComponent(u.pathname || '').replace(/^\/+|\/+$/g, '');
    const part = (path.split('/').filter(Boolean)[0] || '').replace(/^@/, '');
    return sanitizeIgUsername(part);
  } catch (e) {
    return '';
  }
}

/** Absolute https://www.instagram.com/{handle}/ — never bare @slug (browsers resolve that relative to localhost). */
export function instagramProfileHref(handleOrUrl) {
  const h = sanitizeIgUsername(handleOrUrl) || instagramHandle(handleOrUrl);
  if (!h) return '';
  return `https://www.instagram.com/${encodeURIComponent(h)}/`;
}

/** Slug a business display name into a possible IG username (display/guess only — not used for open). */
export function instagramHandleFromBusinessName(name) {
  const raw = String(name || '').trim().toLowerCase();
  if (!raw) return '';
  return raw
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9._]+/g, '')
    .replace(/^[._]+|[._]+$/g, '')
    .slice(0, 30);
}

/**
 * Browser profile URL — always https://www.instagram.com/{handle}/ (never accounts/login, never bare @slug).
 * Does not guess from business name (that opens the wrong user → "User not found").
 * @param {string} handle
 * @param {string} [profileUrl]
 * @param {string} [_businessName] kept for call-site compat; ignored for open URL
 */
export function instagramProfileWebUrl(handle, profileUrl, _businessName) {
  // If "handle" is actually a stored URL / @slug, parse it — never prefix @ onto the href
  const fromHandle = instagramHandle(handle) || sanitizeIgUsername(handle);
  const fromUrl = instagramHandle(profileUrl);
  const h = fromHandle || fromUrl;
  return h ? instagramProfileHref(h) : '';
}

/**
 * Open the lead's Instagram profile: native app on mobile, logged-in web profile on desktop.
 * Avoids ig.me/m, accounts/login walls, and relative @{slug} navigations.
 * @returns {{ app: string, web: string, handle: string }}
 */
export function instagramOpenUrls(profileUrl, handle, { mobile = false, businessName: _businessName = '' } = {}) {
  const h = (
    sanitizeIgUsername(handle)
    || instagramHandle(handle)
    || instagramHandle(profileUrl)
    || ''
  );
  const web = instagramProfileHref(h);
  if (h && mobile) {
    return {
      app: `instagram://user?username=${encodeURIComponent(h)}`,
      web,
      handle: h,
    };
  }
  return { app: '', web, handle: h };
}

export function firstEmail(b) {
  return String(b?.email || '').split(',').map(e => e.trim()).filter(Boolean)[0] || '';
}

export function phoneDigits(b) {
  return String(b?.international_phone || b?.phone || '').replace(/\D/g, '');
}

export function contactChannels(b) {
  const s = (b && b.social_links) || {};
  const ch = (b && b.channels) || {};
  return {
    email: !!(ch.email || firstEmail(b)),
    whatsapp: !!(ch.whatsapp || phoneDigits(b).length >= 8),
    instagram: !!(ch.instagram || s.instagram),
  };
}

export function availableComposeChannels(b) {
  const c = contactChannels(b);
  return COLD_CHANNELS.filter(ch => c[ch]);
}

/** Prefer channel for cold outreach (WA → email → IG). */
export function preferredColdChannel(b, hint) {
  const channels = availableComposeChannels(b);
  if (hint && channels.includes(hint)) return hint;
  return channels[0] || 'email';
}

/**
 * Visible channel switcher + per-channel Open buttons (Email / WhatsApp / Instagram).
 * @param {string[]} channels
 * @param {string} active
 * @param {{mobile?: boolean}} opts
 */
export function composeChannelControlsHtml(channels, active, opts = {}) {
  const list = channels || [];
  if (!list.length) return '';
  const mobile = !!opts.mobile;
  const pills = list.map(ch => {
    const on = ch === active ? ' active' : '';
    return `<button type="button" class="compose-ch-pill${on}" data-compose-ch="${ch}" aria-pressed="${ch === active ? 'true' : 'false'}">${channelLabel(ch)}</button>`;
  }).join('');
  const opens = list.map(ch => {
    let label = 'Open ' + channelLabel(ch);
    if (ch === 'whatsapp') label = mobile ? 'Open WhatsApp app' : 'Open WhatsApp Web';
    else if (ch === 'email') label = mobile ? 'Open Mail app' : 'Open Zoho Mail';
    else if (ch === 'instagram') label = mobile ? 'Open Instagram app' : 'Open Instagram';
    return `<button type="button" class="compose-ch-open" data-compose-open="${ch}">${label}</button>`;
  }).join('');
  return `
    <div class="compose-ch-pills" role="group" aria-label="Message channel">${pills}</div>
    <div class="compose-ch-opens" role="group" aria-label="Open channel apps">${opens}</div>
  `;
}
