/**
 * Bearer token session for Sales OS API clients.
 * Flow: login → store tokens → api() sends Authorization: Bearer →
 * backend validates JWT → router → service → response.
 * Cookie session remains a same-origin fallback.
 */
const ACCESS_KEY = 'mlf_access_token';
const REFRESH_KEY = 'mlf_refresh_token';
const EXPIRES_KEY = 'mlf_access_expires_at';

function storage() {
  try {
    return window.sessionStorage;
  } catch (e) {
    return null;
  }
}

export function getAccessToken() {
  const s = storage();
  return s ? (s.getItem(ACCESS_KEY) || '') : '';
}

export function getRefreshToken() {
  const s = storage();
  return s ? (s.getItem(REFRESH_KEY) || '') : '';
}

export function clearAuthTokens() {
  const s = storage();
  if (!s) return;
  s.removeItem(ACCESS_KEY);
  s.removeItem(REFRESH_KEY);
  s.removeItem(EXPIRES_KEY);
}

/** Persist token pair from POST /api/login or /api/auth/refresh. */
export function setAuthTokens(data) {
  const s = storage();
  if (!s || !data) return;
  const access = data.access_token || '';
  const refresh = data.refresh_token || '';
  if (access) s.setItem(ACCESS_KEY, access);
  else s.removeItem(ACCESS_KEY);
  if (refresh) s.setItem(REFRESH_KEY, refresh);
  else s.removeItem(REFRESH_KEY);
  const expiresIn = Number(data.expires_in || 0);
  if (expiresIn > 0) {
    s.setItem(EXPIRES_KEY, String(Date.now() + expiresIn * 1000));
  } else {
    s.removeItem(EXPIRES_KEY);
  }
}

export function authHeaders(extra = {}) {
  const headers = { ...(extra || {}) };
  const token = getAccessToken();
  if (token && !headers.Authorization && !headers.authorization) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

let refreshInFlight = null;

/** Rotate refresh token; updates stored pair. Returns new access token or ''. */
export async function refreshAccessToken() {
  if (refreshInFlight) return refreshInFlight;
  const refresh = getRefreshToken();
  if (!refresh) return '';

  refreshInFlight = (async () => {
    try {
      const r = await fetch('/api/auth/refresh', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        clearAuthTokens();
        return '';
      }
      setAuthTokens(data);
      return data.access_token || '';
    } catch (e) {
      return '';
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export async function logoutAuth() {
  const refresh = getRefreshToken();
  try {
    await fetch('/api/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ refresh_token: refresh || '' }),
    });
  } catch (e) {
    /* ignore */
  }
  clearAuthTokens();
}
