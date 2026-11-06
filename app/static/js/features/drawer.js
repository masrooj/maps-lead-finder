/** Feature owner: drawer.
 * SOLID: extend this module for drawer behavior; peel from app/runtime.js on touch.
 * Cross-panel calls go through app/bridge.js (B.*), not new globals.
 */
import { $ } from '../core/dom.js';
import { drawerSkeletonHtml } from '../core/skeleton.js';

export const FEATURE = 'drawer';

/** CRM-only tabs (Sales / Proposal / Delivery). */
export const DRAWER_CRM_TABS = ['sales', 'proposal', 'appointhive'];

/** Discover: triage only. */
export const DRAWER_DISCOVER_TABS = ['overview', 'contacts'];

/** Contact queue: overview + contacts + activity (no pipeline edits). */
export const DRAWER_QUEUE_TABS = ['overview', 'contacts', 'activity'];

export function isOutreachDrawerPanel(panel) {
  return panel === 'leads' || panel === 'contactQueue';
}

export function resolveDrawerMode(panel) {
  if (panel === 'leads') return 'discover';
  if (panel === 'contactQueue') return 'queue';
  return 'crm';
}

export function clampDrawerTab(tab, mode) {
  const t = tab || 'overview';
  if (mode === 'discover' && !DRAWER_DISCOVER_TABS.includes(t)) return 'overview';
  if (mode === 'queue' && !DRAWER_QUEUE_TABS.includes(t)) return 'overview';
  if (mode === 'outreach' && DRAWER_CRM_TABS.includes(t)) return 'overview';
  return t;
}

/** Show drawer shell with skeleton while prospect payload is fetching. */
export function showDrawerLoading({ title = 'Loading…', sub = 'Fetching prospect' } = {}) {
  const drawer = $('drawer');
  if (!drawer) return;
  if ($('drawerTitle')) $('drawerTitle').textContent = title;
  if ($('drawerSub')) $('drawerSub').textContent = sub;
  if ($('drawerBody')) $('drawerBody').innerHTML = drawerSkeletonHtml();
  drawer.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}
