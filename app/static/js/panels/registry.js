/** Panel registry — Open/Closed for new screens. */
export const panels = new Map();

export function registerPanel(def) {
  if (!def || !def.id) throw new Error('registerPanel requires { id, refresh }');
  panels.set(def.id, def);
}

export function getPanel(id) {
  return panels.get(id);
}
