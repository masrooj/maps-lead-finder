/** DOM helpers — also mirror on globalThis so any stale inline/cached caller still works. */
export const $ = (id) => document.getElementById(id);
try { globalThis.$ = $; } catch (_) {}
