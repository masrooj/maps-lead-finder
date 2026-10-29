import { $ } from '../core/dom.js';

export function closeSidebar() {
  document.body.classList.remove('sidebar-open');
  const t = $('navToggle');
  if (t) t.setAttribute('aria-expanded', 'false');
  const b = $('sidebarBackdrop');
  if (b) b.setAttribute('aria-hidden', 'true');
}
