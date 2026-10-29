/** Shared app state bag for modules (panels/shell import this; runtime mirrors key fields). */
export const state = {
  rowsData: [],
  appPanel: 'leads',
  drawerLead: null,
  drawerTab: 'overview',
  composeLead: null,
  prospectsCache: [],
  activitiesCache: [],
  tasksCache: [],
  demosCache: [],
  contactQueueLeads: [],
  contactQueueTab: 'all',
  moneyTab: 'ledger',
  commsTab: 'outreach',
  dealsTab: 'proposals',
  settingsTab: 'general',
  ahTab: 'overview',
};
