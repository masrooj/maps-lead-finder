import { getPanel } from '../panels/registry.js';
import { state } from '../core/state.js';

/** Call after DOM panel switch — runs registered refresh if present. */
export async function refreshRegisteredPanel(id) {
  const p = getPanel(id || state.appPanel);
  if (p && typeof p.refresh === 'function') {
    await p.refresh();
  }
}

/**
 * Safe refresh wrapper used when registering panels from runtime.
 * @param {(() => any) | null | undefined} fn
 */
export function safeRefresh(fn) {
  return async () => {
    if (typeof fn === 'function') await fn();
  };
}
