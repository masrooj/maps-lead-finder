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

/** Reminder toggles default off when unset. */
function settingTruthy(raw) {
  const v = String(raw ?? '').trim().toLowerCase();
  return v === '1' || v === 'true' || v === 'yes' || v === 'on';
}

export function paintScanBookingLimitFields(settings) {
  const s = settings || {};
  const lim = parseInt(s.scan_booking_limit, 10);
  const chunk = parseInt(s.scan_booking_chunk, 10);
  if ($('scanBookingLimit')) {
    $('scanBookingLimit').value = String(
      Number.isFinite(lim) && lim >= 1 && lim <= 200 ? lim : 60
    );
  }
  if ($('scanBookingChunk')) {
    $('scanBookingChunk').value = String(
      Number.isFinite(chunk) && chunk >= 1 && chunk <= 50 ? chunk : 8
    );
  }
}

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
    const s = data.settings || {};
    paintScanBookingLimitFields(s);
    if ($('followUpEmailReminders')) {
      $('followUpEmailReminders').checked = settingTruthy(s['follow_up_reminders.email_enabled']);
    }
    if ($('followUpPushReminders')) {
      $('followUpPushReminders').checked = settingTruthy(s['follow_up_reminders.push_enabled']);
    }
    if (typeof B.refreshScanBookingMeta === 'function') {
      B.refreshScanBookingMeta(s);
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

  $('settingsGotoDiscoverScan') && $('settingsGotoDiscoverScan').addEventListener('click', () => {
    if (typeof B.setAppPanel === 'function') B.setAppPanel('leads');
  });

  $('saveScanBookingLimits') && $('saveScanBookingLimits').addEventListener('click', async () => {
    try {
      let lim = parseInt($('scanBookingLimit') ? $('scanBookingLimit').value : '60', 10);
      let chunk = parseInt($('scanBookingChunk') ? $('scanBookingChunk').value : '8', 10);
      if (Number.isNaN(lim) || lim < 1 || lim > 200) {
        toast('Max leads per scan must be 1–200', true);
        return;
      }
      if (Number.isNaN(chunk) || chunk < 1 || chunk > 50) {
        toast('Batch size must be 1–50', true);
        return;
      }
      if (chunk > lim) chunk = lim;
      await api(`/api/sales/settings/scan_booking_limit?value=${encodeURIComponent(String(lim))}`, { method: 'PUT' });
      await api(`/api/sales/settings/scan_booking_chunk?value=${encodeURIComponent(String(chunk))}`, { method: 'PUT' });
      if ($('scanBookingLimit')) $('scanBookingLimit').value = String(lim);
      if ($('scanBookingChunk')) $('scanBookingChunk').value = String(chunk);
      if (typeof B.refreshScanBookingMeta === 'function') {
        B.refreshScanBookingMeta({ scan_booking_limit: String(lim), scan_booking_chunk: String(chunk) });
      }
      toast(`Scan limits saved · max ${lim} · batch ${chunk}`);
    } catch (e) {
      toast(e.message, true);
    }
  });

  $('saveFollowUpReminders') && $('saveFollowUpReminders').addEventListener('click', async () => {
    try {
      const emailOn = !!($('followUpEmailReminders') && $('followUpEmailReminders').checked);
      const pushOn = !!($('followUpPushReminders') && $('followUpPushReminders').checked);
      await api(
        `/api/sales/settings/follow_up_reminders.email_enabled?value=${encodeURIComponent(emailOn ? 'true' : 'false')}`,
        { method: 'PUT' },
      );
      await api(
        `/api/sales/settings/follow_up_reminders.push_enabled?value=${encodeURIComponent(pushOn ? 'true' : 'false')}`,
        { method: 'PUT' },
      );
      toast(`Reminders saved · email ${emailOn ? 'on' : 'off'} · push ${pushOn ? 'on' : 'off'}`);
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
