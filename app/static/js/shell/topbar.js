import { $ } from '../core/dom.js';

/** Panel title/subtitle shown in the top bar — keep in sync with data-app-panel ids. */
export const PANEL_META = {
  guide: { title: 'How it works', sub: 'Discovery → Money playbook' },
  dashboard: { title: 'Dashboard', sub: 'Queues, pipeline value, and cash collected' },
  leads: { title: 'Discover', sub: 'Find and contact prospects' },
  contactQueue: { title: 'Contact queue', sub: 'Ranked who to call next' },
  pipeline: { title: 'Pipeline', sub: 'Stages (auto-advance + manual override)' },
  prospects: { title: 'Prospects', sub: 'Search your sales book' },
  tasks: { title: 'Tasks', sub: 'Follow-ups due and overdue' },
  activities: { title: 'Communication', sub: 'Outreach log — not deal closing' },
  demos: { title: 'Demos', sub: 'Demo outcomes and next steps' },
  deals: { title: 'Deals', sub: 'Proposals, pay links, confirm close' },
  money: { title: 'Money', sub: 'Cash provenance, providers, ledger' },
  paySetup: { title: 'Pay setup', sub: 'Configure Stripe, PayPal, Payoneer, Skrill + validate' },
  appoinHive: { title: 'AppointHive', sub: 'Workspace bridge · Connect · Catalogs (drawer has Delivery / pay)' },
  reports: { title: 'Reports', sub: 'Funnel, scores, win rate, sources' },
  settings: { title: 'Settings', sub: 'General · Team · Ranking · Jobs · Views' },
  templates: { title: 'Templates', sub: 'Email, WhatsApp, and Instagram messages' },
};

export const APP_PANELS = Object.keys(PANEL_META);

export function closeTopMenus() {
  ['notifMenu', 'profileMenu'].forEach((id) => {
    const el = $(id);
    if (el) el.classList.remove('open');
  });
  ['notifBtn', 'profileBtn'].forEach((id) => {
    const el = $(id);
    if (el) el.setAttribute('aria-expanded', 'false');
  });
}

/** @param {string} panel panel id (data-app-panel) */
export function updateTopbarTitle(panel) {
  const meta = PANEL_META[panel] || PANEL_META.leads;
  if ($('topbarPageTitle')) $('topbarPageTitle').textContent = meta.title;
  if ($('topbarPageSub')) $('topbarPageSub').textContent = meta.sub;
}
