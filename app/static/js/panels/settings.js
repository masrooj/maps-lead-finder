/**
 * Panel owner: `settings` (Settings).
 * Signature + brand logo wiring lives here; other loaders may still be in runtime
 * until peeled — refresh goes through bridge when available.
 */
import { $ } from '../core/dom.js';
import { api, toast } from '../core/api.js';
import { uiConfirm } from '../core/dialog.js';
import { registerPanel } from './registry.js';
import { B } from '../app/bridge.js';
import {
  saveEmailSignature,
  refreshSignatureUi,
  setBrandLogoUrl,
  paintSignatureForm,
  paintBrandLogoPreview,
} from '../features/email-signature.js';
import { refreshIgOutreachQuota, setIgOutreachQuota } from '../features/compose.js';

let wired = false;

export async function loadIgSoftDmCapSetting() {
  try {
    const data = await api('/api/sales/settings');
    if (data.ig_outreach_quota) setIgOutreachQuota(data.ig_outreach_quota);
    const raw = data.settings && data.settings.ig_soft_dm_daily_cap;
    const parsed = parseInt(raw, 10);
    if ($('igSoftDmDailyCap')) {
      $('igSoftDmDailyCap').value = String(Number.isFinite(parsed) && parsed >= 0 ? parsed : 8);
    }
    const waitRaw = data.settings && data.settings.ig_ready_wait_days;
    const waitParsed = parseInt(waitRaw, 10);
    if ($('igReadyWaitDays')) {
      $('igReadyWaitDays').value = String(
        Number.isFinite(waitParsed) && waitParsed >= 0 && waitParsed <= 14 ? waitParsed : 1
      );
    }
  } catch (_) { /* settings may be owner-gated elsewhere */ }
}

export async function loadSignatureSettings() {
  await refreshSignatureUi();
}

async function saveSignatureFromForm() {
  const ta = $('emailSignatureText');
  const chk = $('emailSignatureIncludeLogo');
  const data = await saveEmailSignature({
    text: ta ? ta.value : '',
    include_logo: chk ? chk.checked : true,
  });
  paintSignatureForm();
  toast('Email signature saved');
  return data;
}

function wireSettingsOnce() {
  if (wired) return;
  wired = true;

  const saveBtn = $('emailSignatureSave');
  saveBtn && saveBtn.addEventListener('click', () => {
    saveSignatureFromForm().catch((e) => toast(e.message || 'Save failed', true));
  });

  const ta = $('emailSignatureText');
  ta && ta.addEventListener('input', () => paintSignatureForm());
  const chk = $('emailSignatureIncludeLogo');
  chk && chk.addEventListener('change', () => paintSignatureForm());

  $('brandLogoFile') && $('brandLogoFile').addEventListener('change', async (ev) => {
    const f = ev.target.files && ev.target.files[0];
    if (!f) return;
    if ($('brandLogoFileName')) $('brandLogoFileName').textContent = f.name || 'No file chosen';
    try {
      const fd = new FormData();
      fd.append('file', f);
      const data = await api('/api/sales/brand-logo', { method: 'POST', body: fd });
      setBrandLogoUrl(data.url || '');
      paintBrandLogoPreview();
      await refreshSignatureUi();
      toast('Brand logo saved for signatures');
    } catch (e) {
      toast(e.message, true);
      if ($('brandLogoFileName')) $('brandLogoFileName').textContent = 'No file chosen';
    }
    ev.target.value = '';
  });

  $('saveIgSoftDmCap') && $('saveIgSoftDmCap').addEventListener('click', async () => {
    try {
      const raw = $('igSoftDmDailyCap') ? $('igSoftDmDailyCap').value : '8';
      const n = Math.max(0, Math.min(100, parseInt(raw, 10)));
      if (Number.isNaN(n)) {
        toast('Enter a daily cap 0–100', true);
        return;
      }
      const waitRaw = $('igReadyWaitDays') ? $('igReadyWaitDays').value : '1';
      const waitN = Math.max(0, Math.min(14, parseInt(waitRaw, 10)));
      if (Number.isNaN(waitN)) {
        toast('Enter Ready wait days 0–14', true);
        return;
      }
      await api(`/api/sales/settings/ig_soft_dm_daily_cap?value=${encodeURIComponent(String(n))}`, { method: 'PUT' });
      await api(`/api/sales/settings/ig_ready_wait_days?value=${encodeURIComponent(String(waitN))}`, { method: 'PUT' });
      if ($('igSoftDmDailyCap')) $('igSoftDmDailyCap').value = String(n);
      if ($('igReadyWaitDays')) $('igReadyWaitDays').value = String(waitN);
      await refreshIgOutreachQuota().catch(() => {});
      toast(`IG soft DM saved (cap ${n}, Ready wait ${waitN}d)`);
    } catch (e) {
      toast(e.message, true);
    }
  });

  $('brandLogoClear') && $('brandLogoClear').addEventListener('click', async () => {
    const ok = await uiConfirm({
      title: 'Remove brand logo?',
      message: 'Email signatures will no longer include the logo image (text signature stays).',
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    try {
      await api('/api/sales/brand-logo', { method: 'DELETE' });
      setBrandLogoUrl('');
      paintBrandLogoPreview();
      if ($('brandLogoFileName')) $('brandLogoFileName').textContent = 'No file chosen';
      await refreshSignatureUi();
      toast('Brand logo removed');
    } catch (e) {
      toast(e.message, true);
    }
  });
}

export async function refreshSettingsPanel() {
  wireSettingsOnce();
  if (typeof B.loadSettings === 'function') {
    await B.loadSettings();
  } else {
    await loadSignatureSettings();
    await loadIgSoftDmCapSetting();
  }
}

wireSettingsOnce();

registerPanel({
  id: 'settings',
  refresh: async () => {
    await refreshSettingsPanel();
  },
});
