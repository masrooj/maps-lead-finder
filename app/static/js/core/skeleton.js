/** Shared loading skeletons — use while panel / drawer data fetches. */

export function skLine(width = '100%', height = 12) {
  return `<span class="sk sk-line" style="width:${width};height:${height}px" aria-hidden="true"></span>`;
}

export function listSkeletonHtml({ rows = 5, label = 'Loading…' } = {}) {
  const items = Array.from({ length: rows }, () => `
    <div class="sk-card" aria-hidden="true">
      <div class="sk-card-main">
        ${skLine('42%', 14)}
        ${skLine('68%', 10)}
        ${skLine('55%', 10)}
      </div>
      <div class="sk-card-actions">
        ${skLine('72px', 32)}
        ${skLine('64px', 32)}
      </div>
    </div>`).join('');
  return `<div class="sk-stack" role="status" aria-live="polite" aria-busy="true"><span class="sk-sr">${label}</span>${items}</div>`;
}

export function tableSkeletonHtml({ rows = 6, cols = 7, label = 'Loading…' } = {}) {
  const cells = Array.from({ length: cols }, (_, i) =>
    `<td data-label="">${skLine(i === 0 ? '70%' : '50%', 12)}</td>`
  ).join('');
  const body = Array.from({ length: rows }, () => `<tr class="sk-tr" aria-hidden="true">${cells}</tr>`).join('');
  return body + `<tr class="sk-sr-row"><td colspan="${cols}"><span class="sk-sr">${label}</span></td></tr>`;
}

export function drawerSkeletonHtml({ label = 'Loading prospect…' } = {}) {
  return `<div class="sk-drawer" role="status" aria-live="polite" aria-busy="true">
    <span class="sk-sr">${label}</span>
    <div class="sk-tabs" aria-hidden="true">${[1,2,3,4,5].map(() => skLine('72px', 28)).join('')}</div>
    <div class="sk-pane" aria-hidden="true">
      ${skLine('28%', 11)}
      ${skLine('48%', 18)}
      ${skLine('90%', 10)}
      ${skLine('75%', 10)}
      ${skLine('62%', 10)}
      <div class="sk-card-actions" style="margin-top:14px">${skLine('120px', 36)}${skLine('110px', 36)}</div>
    </div>
  </div>`;
}
