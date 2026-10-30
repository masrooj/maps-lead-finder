/**
 * Email signature cache + compose helpers.
 * Data comes from GET/PUT /api/sales/email-signature (Bearer or cookie auth).
 */
import { api, esc } from '../core/api.js';

let cache = {
  text: 'AppointHive',
  include_logo: true,
  brand_logo_url: '',
  plain: '',
  html: '',
};

export function getSignatureCache() {
  return { ...cache };
}

export function getBrandLogoUrl() {
  return cache.brand_logo_url || '';
}

export function setBrandLogoUrl(url) {
  cache.brand_logo_url = url || '';
}

export function brandLogoAbsoluteUrl() {
  const url = cache.brand_logo_url || '';
  if (!url) return '';
  return /^https?:\/\//i.test(url) ? url : (location.origin + url);
}

export function applySignaturePayload(data) {
  if (!data || typeof data !== 'object') return cache;
  cache = {
    text: data.text != null ? String(data.text) : cache.text,
    include_logo: data.include_logo !== false,
    brand_logo_url: data.brand_logo_url != null ? String(data.brand_logo_url) : cache.brand_logo_url,
    plain: data.plain != null ? String(data.plain) : cache.plain,
    html: data.html != null ? String(data.html) : cache.html,
  };
  return getSignatureCache();
}

export async function loadEmailSignature() {
  const data = await api('/api/sales/email-signature');
  return applySignaturePayload(data);
}

export async function saveEmailSignature({ text, include_logo }) {
  const data = await api('/api/sales/email-signature', {
    method: 'PUT',
    body: { text: text || '', include_logo: !!include_logo },
  });
  return applySignaturePayload(data);
}

export function emailSignaturePlain() {
  const abs = brandLogoAbsoluteUrl();
  const text = (cache.text || 'AppointHive').trim() || 'AppointHive';
  const parts = ['\r\n\r\n--\r\n' + text.replace(/\n/g, '\r\n')];
  if (cache.include_logo && abs) parts.push('\r\nLogo: ' + abs);
  return parts.join('');
}

export function emailSignatureHtml() {
  const abs = brandLogoAbsoluteUrl();
  const text = (cache.text || 'AppointHive').trim() || 'AppointHive';
  const safe = esc(text).replace(/\r\n|\n|\r/g, '<br>');
  let html = '<br><br>--<br>' + safe;
  if (cache.include_logo && abs) {
    html += '<br><img src="' + esc(abs) + '" alt="Logo" height="48" style="max-height:48px;width:auto;display:block;margin:8px 0"><br>';
  }
  return html;
}

export function withEmailSignature(body, { html = false } = {}) {
  const base = String(body || '');
  const abs = brandLogoAbsoluteUrl();
  const text = (cache.text || '').trim();
  if (!text && !(cache.include_logo && abs)) return base;
  // Avoid double-appending when body already includes signature markers
  if (text && base.includes(text) && base.includes('--')) return base;
  if (abs && (base.includes(cache.brand_logo_url) || base.includes(abs))) return base;
  if (html) {
    const withBr = base.replace(/\r\n|\n|\r/g, '<br>');
    return withBr + emailSignatureHtml();
  }
  return base + emailSignaturePlain();
}

export function paintBrandLogoPreview() {
  const img = document.getElementById('brandLogoPreview');
  if (!img) return;
  const url = cache.brand_logo_url || '';
  if (url) {
    img.src = url;
    img.classList.remove('hidden');
  } else {
    img.removeAttribute('src');
    img.classList.add('hidden');
  }
}

export function paintSignatureForm() {
  const ta = document.getElementById('emailSignatureText');
  const chk = document.getElementById('emailSignatureIncludeLogo');
  const preview = document.getElementById('emailSignaturePreview');
  if (ta && document.activeElement !== ta) ta.value = cache.text || '';
  if (chk && document.activeElement !== chk) chk.checked = !!cache.include_logo;
  if (preview) {
    const liveText = ta ? ta.value : (cache.text || '');
    const liveInclude = chk ? chk.checked : !!cache.include_logo;
    const prev = { ...cache };
    cache.text = liveText;
    cache.include_logo = liveInclude;
    const plain = emailSignaturePlain().replace(/^\r\n\r\n/, '');
    cache = prev;
    preview.textContent = plain || '(empty signature)';
  }
  paintBrandLogoPreview();
}

export async function refreshSignatureUi() {
  try {
    await loadEmailSignature();
  } catch (e) {
    /* keep cache */
  }
  paintSignatureForm();
  return getSignatureCache();
}
