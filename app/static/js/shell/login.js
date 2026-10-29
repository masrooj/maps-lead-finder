/** Login DOM helpers; full auth flow remains in runtime for feature parity. */
export function showLoginError(text) {
  const el = document.getElementById('loginError');
  if (!el) return;
  if (!text) {
    el.className = 'login-error';
    el.textContent = '';
    return;
  }
  el.textContent = text;
  el.className = 'login-error show';
}
