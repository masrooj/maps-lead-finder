/**
 * Panel ownership contract (SOLID).
 * Each panels/<name>.js owns one data-app-panel id.
 * On first real change to that screen: peel loaders from app/runtime.js into the panel
 * file, registerPanel({ id, refresh }), and keep index.html free of logic.
 */
export const PANEL_OWNERSHIP = {
  guide: 'guide',
  dashboard: 'dashboard',
  leads: 'leads',
  contactQueue: 'contactQueue',
  pipeline: 'pipeline',
  prospects: 'prospects',
  tasks: 'tasks',
  activities: 'activities',
  demos: 'demos',
  templates: 'templates',
  deals: 'deals',
  money: 'money',
  reports: 'reports',
  appoinHive: 'appoinHive',
  paySetup: 'paySetup',
  settings: 'settings',
};
