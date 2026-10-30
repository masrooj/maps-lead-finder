/** Feature owner: drawer.
 * SOLID: extend this module for drawer behavior; peel from app/runtime.js on touch.
 * Cross-panel calls go through app/bridge.js (B.*), not new globals.
 */
import { $ } from '../core/dom.js';
import { drawerSkeletonHtml } from '../core/skeleton.js';

export const FEATURE = 'drawer';

/** Discover + Contact queue: outreach only (no Sales / Proposal / Delivery). */
export const DRAWER_CRM_TABS = ['sales', 'proposal', 'appointhive'];

export function isOutreachDrawerPanel(panel) {
  return panel === 'leads' || panel === 'contactQueue';
}

export function resolveDrawerMode(panel) {
  return isOutreachDrawerPanel(panel) ? 'outreach' : 'crm';
}

export function clampDrawerTab(tab, mode) {
  const t = tab || 'overview';
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
