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

/** Parse @username from an Instagram profile URL (or bare handle). */
export function instagramHandle(url) {
  const raw = String(url || '').trim();
  if (!raw) return '';
  if (!/^https?:\/\//i.test(raw) && !raw.includes('/')) {
    return raw.replace(/^@/, '').split('?')[0].trim();
  }
  try {
    const path = decodeURIComponent(new URL(raw).pathname).replace(/^\/+|\/+$/g, '');
    const part = (path.split('/').filter(Boolean)[0] || '').replace(/^@/, '');
    if (!part || ['p', 'reel', 'reels', 'stories', 'direct', 'explore', 'accounts'].includes(part.toLowerCase())) {
      return '';
    }
    return part;
  } catch (e) {
    return '';
  }
}

/** Browser profile URL — uses the signed-in Instagram web session (not ig.me login walls). */
export function instagramProfileWebUrl(handle, profileUrl) {
  const h = String(handle || '').replace(/^@/, '').trim();
  if (h) return `https://www.instagram.com/${encodeURIComponent(h)}/`;
  const fallback = String(profileUrl || '').trim();
  return fallback || 'https://www.instagram.com/';
}

/**
 * Open the lead's Instagram profile: native app on mobile, logged-in web profile on desktop.
 * Avoids ig.me/m blind DM links that force a login interstitial.
 * @returns {{ app: string, web: string, handle: string }}
 */
export function instagramOpenUrls(profileUrl, handle, { mobile = false } = {}) {
  const h = String(handle || instagramHandle(profileUrl) || '').replace(/^@/, '').trim();
  const web = instagramProfileWebUrl(h, profileUrl);
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
