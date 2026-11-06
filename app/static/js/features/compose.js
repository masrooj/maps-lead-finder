/** Feature owner: compose.
 * SOLID: extend this module for compose behavior; peel from app/runtime.js on touch.
 * Cross-panel calls go through app/bridge.js (B.*), not new globals.
 */
import { $ } from '../core/dom.js';
import { api, toast } from '../core/api.js';
import { B } from '../app/bridge.js';

export const FEATURE = 'compose';

/** Cold outreach order: WhatsApp first when available, then email, then Instagram. */
export const COLD_CHANNELS = ['whatsapp', 'email', 'instagram'];

/** Cached IG soft-DM quota from GET /api/sales/settings → ig_outreach_quota */
let igQuotaCache = { cap: 8, used: 0, remaining: 8, timezone: '' };
let igWarmWired = false;

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

/** Already contacted / IG-sent → only Follow is required. */
export function igWarmFollowOnly(b) {
  return !!(b && (b.contacted || b.instagram_sent || b.ig_warm_follow_only));
}

export function igWarmTickCount(b) {
  if (!b) return 0;
  return [b.ig_warm_follow, b.ig_warm_like, b.ig_warm_comment].filter(Boolean).length;
}

/** Cold: Follow + (like or comment). Already contacted / IG-sent: Follow only. */
export function igWarmComplete(b) {
  if (!b) return false;
  if (igWarmFollowOnly(b)) return !!b.ig_warm_follow;
  return !!(b.ig_warm_follow && (b.ig_warm_like || b.ig_warm_comment));
}

export function getIgOutreachQuota() {
  return { ...igQuotaCache };
}

export async function refreshIgOutreachQuota() {
  try {
    const data = await api('/api/sales/settings');
    const q = data && data.ig_outreach_quota;
    if (q && typeof q === 'object') {
      igQuotaCache = {
        cap: Number(q.cap) || 0,
        used: Number(q.used) || 0,
        remaining: Math.max(0, Number(q.remaining) || 0),
        timezone: q.timezone || '',
      };
    } else {
      const raw = (data.settings && data.settings.ig_soft_dm_daily_cap);
      const parsed = parseInt(raw, 10);
      const cap = Number.isFinite(parsed) && parsed >= 0 ? parsed : 8;
      igQuotaCache = { ...igQuotaCache, cap, remaining: Math.max(0, cap - (igQuotaCache.used || 0)) };
    }
  } catch (_) { /* keep last cache */ }
  return getIgOutreachQuota();
}

export function setIgOutreachQuota(q) {
  if (!q || typeof q !== 'object') return getIgOutreachQuota();
  igQuotaCache = {
    cap: Number(q.cap) || 0,
    used: Number(q.used) || 0,
    remaining: Math.max(0, Number(q.remaining) || 0),
    timezone: q.timezone || '',
  };
  return getIgOutreachQuota();
}

/**
 * Client-side IG soft-send gate (server still enforces).
 * @returns {string|null} error message or null if allowed
 */
export function igSoftSendBlockReason(lead, channel) {
  if (String(channel || '').toLowerCase() !== 'instagram') return null;
  if (!igWarmComplete(lead)) {
    if (igWarmFollowOnly(lead)) return 'IG warm incomplete: follow';
    const missing = [];
    if (!lead?.ig_warm_follow) missing.push('follow');
    if (!(lead?.ig_warm_like || lead?.ig_warm_comment)) missing.push('like or comment');
    return `IG warm incomplete: ${missing.join(' + ') || 'follow + like/comment'}`;
  }
  // Already IG-sent re-confirm: do not block on daily cap (server skip_cap).
  if (lead?.instagram_sent) return null;
  const q = getIgOutreachQuota();
  if (q.remaining <= 0) {
    return `IG soft DM daily cap reached (${q.used}/${q.cap}${q.timezone ? ` in ${q.timezone}` : ''})`;
  }
  return null;
}

function paintIgQuotaLine() {
  const el = $('igSoftDmQuota');
  if (!el) return;
  const q = getIgOutreachQuota();
  const tz = q.timezone ? ` · ${q.timezone}` : '';
  el.textContent = `IG soft DMs today: ${q.used}/${q.cap} (remaining ${q.remaining})${tz}`;
  el.classList.toggle('cap-hit', q.remaining <= 0);
}

/**
 * Show/hide IG warm checklist and enable/disable Open + Confirm for IG.
 * @param {object|null} lead
 * @param {string} channel
 */
export function syncIgWarmComposeUi(lead, channel) {
  const box = $('igWarmChecklist');
  const isIg = String(channel || '').toLowerCase() === 'instagram';
  if (box) box.classList.toggle('hidden', !isIg || !lead);

  const followOnly = igWarmFollowOnly(lead);
  const follow = $('igWarmFollow');
  const like = $('igWarmLike');
  const comment = $('igWarmComment');
  const likeLab = like && like.closest('label');
  const commentLab = comment && comment.closest('label');
  if (follow) follow.checked = !!(lead && lead.ig_warm_follow);
  if (like) {
    like.checked = !!(lead && lead.ig_warm_like);
    like.disabled = followOnly;
  }
  if (comment) {
    comment.checked = !!(lead && lead.ig_warm_comment);
    comment.disabled = followOnly;
  }
  if (likeLab) likeLab.classList.toggle('hidden', followOnly);
  if (commentLab) commentLab.classList.toggle('hidden', followOnly);
  const title = box && box.querySelector('.ig-warm-title');
  if (title) {
    title.textContent = followOnly
      ? 'IG warm-up (Follow only — already contacted)'
      : 'IG warm-up (Follow + like or comment)';
  }

  paintIgQuotaLine();
  syncOptionalCallNow(lead);

  const blocked = isIg ? !!igSoftSendBlockReason(lead, 'instagram') : false;
  const openBtn = $('openComposeBtn');
  const confirmBtn = $('confirmSentBtn');
  const confirmNext = $('confirmNextBtn');
  const chOpens = document.querySelectorAll('#composeChannelControls [data-compose-open="instagram"]');

  if (isIg) {
    if (openBtn) openBtn.disabled = blocked;
    if (confirmBtn) confirmBtn.disabled = blocked;
    if (confirmNext) confirmNext.disabled = blocked;
    chOpens.forEach((btn) => { btn.disabled = blocked; });
    const hint = $('igWarmGateHint');
    if (hint) {
      const reason = igSoftSendBlockReason(lead, 'instagram');
      hint.textContent = reason
        || (followOnly ? 'Follow ticked — soft DM unlocked. Call now is optional.' : 'Warm ready — soft DM unlocked.');
      hint.classList.toggle('ok', !reason);
    }
  } else {
    if (openBtn) openBtn.disabled = false;
    if (confirmBtn) confirmBtn.disabled = false;
    if (confirmNext) confirmNext.disabled = false;
  }
}

/** Optional tel: affordance — never required for Confirm sent. */
export function syncOptionalCallNow(lead) {
  const btn = $('composeCallNowBtn');
  const hint = $('composeCallNowHint');
  if (!btn) return;
  const digits = phoneDigits(lead || {});
  const hasPhone = digits.length >= 8;
  btn.disabled = !hasPhone;
  btn.classList.toggle('hidden', false);
  btn.dataset.tel = hasPhone ? `tel:+${digits}` : '';
  if (hint) {
    hint.textContent = hasPhone
      ? 'Optional same-day call — does not block Confirm sent.'
      : 'No phone on this lead — Call now unavailable.';
  }
}

/** Soft opener templates (purpose=soft_opener) for IG/WA cold ask. */
export function preferredSoftTemplate(templates, channel) {
  const list = Array.isArray(templates) ? templates : [];
  if (channel !== 'instagram' && channel !== 'whatsapp') return null;
  return list.find((t) => (t.purpose || '') === 'soft_opener') || null;
}

async function patchIgWarmTick(field, checked) {
  const lead = typeof B.getComposeLead === 'function' ? B.getComposeLead() : null;
  if (!lead || !lead.id) return;
  const body = { [field]: !!checked };
  try {
    const wasComplete = igWarmComplete(lead);
    const updated = await api(`/api/businesses/${lead.id}`, { method: 'PATCH', body });
    lead.ig_warm_follow = !!updated.ig_warm_follow;
    lead.ig_warm_like = !!updated.ig_warm_like;
    lead.ig_warm_comment = !!updated.ig_warm_comment;
    lead.ig_warm_follow_only = !!updated.ig_warm_follow_only;
    lead.ig_warm_complete = !!updated.ig_warm_complete;
    const qRow = ((typeof B.getContactQueueLeads === 'function' ? B.getContactQueueLeads() : null)
      || []).find((x) => String(x.id) === String(lead.id));
    if (qRow) {
      qRow.ig_warm_follow = lead.ig_warm_follow;
      qRow.ig_warm_like = lead.ig_warm_like;
      qRow.ig_warm_comment = lead.ig_warm_comment;
      qRow.ig_warm_follow_only = lead.ig_warm_follow_only;
      qRow.ig_warm_complete = lead.ig_warm_complete;
    }
    if (igWarmComplete(lead) && !wasComplete) toast('IG warm complete');
    const ch = ($('composeChannel') && $('composeChannel').value) || 'instagram';
    syncIgWarmComposeUi(lead, ch);
  } catch (e) {
    toast(e.message || 'Could not save warm tick', true);
    syncIgWarmComposeUi(lead, ($('composeChannel') && $('composeChannel').value) || 'instagram');
  }
}

/** Wire checklist once; runtime calls syncIgWarmComposeUi after open / channel change. */
export function wireIgWarmCompose() {
  if (igWarmWired) return;
  igWarmWired = true;
  const map = [
    ['igWarmFollow', 'ig_warm_follow'],
    ['igWarmLike', 'ig_warm_like'],
    ['igWarmComment', 'ig_warm_comment'],
  ];
  map.forEach(([id, field]) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('change', () => {
      const lead = typeof B.getComposeLead === 'function' ? B.getComposeLead() : null;
      if (igWarmFollowOnly(lead) && (field === 'ig_warm_like' || field === 'ig_warm_comment')) {
        el.checked = !!(lead && lead[field]);
        toast('Already contacted — only Follow is updated', true);
        return;
      }
      patchIgWarmTick(field, el.checked).catch(() => {});
    });
  });
  const callBtn = $('composeCallNowBtn');
  if (callBtn) {
    callBtn.addEventListener('click', () => {
      const href = callBtn.dataset.tel || '';
      if (!href) {
        toast('No phone on this lead', true);
        return;
      }
      // Optional — never part of the warm/confirm gate.
      window.location.href = href;
    });
  }
}

/** Call before Open app / Confirm when channel is IG. Returns false if blocked. */
export function guardIgSoftSend(lead, channel) {
  const reason = igSoftSendBlockReason(lead, channel);
  if (!reason) return true;
  toast(reason, true);
  syncIgWarmComposeUi(lead, channel);
  return false;
}
