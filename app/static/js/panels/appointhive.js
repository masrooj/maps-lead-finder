/** Panel owner: `appoinHive` (AppointHive). */
import { registerPanel } from './registry.js';
import { safeRefresh } from '../shell/router.js';
import { B } from '../app/bridge.js';

export const panelId = 'appoinHive';

async function refreshAppointHive() {
  if (typeof B.loadAppointHive === 'function') {
    await B.loadAppointHive();
    return;
  }
  // Fallback: force overview reload via tab switch side-effects
  if (typeof B.setAhTab === 'function') B.setAhTab('overview');
}

registerPanel({ id: 'appoinHive', refresh: safeRefresh(refreshAppointHive) });
