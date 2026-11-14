/**
 * Searchable combobox for long option lists (Discover country/city).
 * Keeps a normal input so existing `$('id').value` call sites still work.
 */
import { esc } from '../core/api.js';

const INSTANCES = new WeakMap();
const MAX_SHOWN = 80;
/** Below this size, open with a browsable list; larger lists require typing. */
const BROWSE_WITHOUT_QUERY = 200;

function norm(s) {
  return String(s || '').trim().toLowerCase();
}

function matchRank(opt, q) {
  const n = norm(opt);
  if (!q) return 1;
  if (n === q) return 0;
  if (n.startsWith(q)) return 1;
  if (n.includes(q)) return 2;
  return -1;
}

function filterOptions(options, query) {
  const q = norm(query);
  const ranked = [];
  for (const opt of options) {
    const r = matchRank(opt, q);
    if (r < 0) continue;
    ranked.push({ opt, r });
  }
  ranked.sort((a, b) => a.r - b.r || a.opt.localeCompare(b.opt));
  return ranked.map((x) => x.opt);
}

/**
 * @param {HTMLInputElement} input
 * @param {{ options?: string[], placeholder?: string, emptyHint?: string, disabled?: boolean, onChange?: (value: string) => void }} [opts]
 */
export function mountSearchableSelect(input, opts = {}) {
  if (!input) return null;
  const existing = INSTANCES.get(input);
  if (existing) {
    if (opts.options) existing.setOptions(opts.options);
    if (opts.placeholder != null) existing.setPlaceholder(opts.placeholder);
    if (opts.disabled != null) existing.setDisabled(!!opts.disabled);
    if (opts.onChange) existing.onChange = opts.onChange;
    return existing;
  }

  let wrap = input.closest('.searchable-select');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'searchable-select';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
  }

  let list = wrap.querySelector('.searchable-select-list');
  if (!list) {
    list = document.createElement('div');
    list.className = 'searchable-select-list';
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    wrap.appendChild(list);
  }

  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');
  input.setAttribute('autocomplete', 'off');
  input.setAttribute('spellcheck', 'false');

  const state = {
    options: Array.isArray(opts.options) ? opts.options.slice() : [],
    onChange: opts.onChange || null,
    activeIndex: -1,
    open: false,
    suppressBlur: false,
    skipNextFocusOpen: false,
  };

  if (opts.placeholder != null) input.placeholder = opts.placeholder;
  if (opts.disabled != null) input.disabled = !!opts.disabled;

  function setExpanded(on) {
    state.open = on;
    input.setAttribute('aria-expanded', on ? 'true' : 'false');
    list.hidden = !on;
    list.classList.toggle('open', on);
  }

  function close() {
    setExpanded(false);
    state.activeIndex = -1;
  }

  function exactOption(raw) {
    const q = norm(raw);
    if (!q) return '';
    return state.options.find((o) => norm(o) === q) || '';
  }

  function fireChange(prev, next) {
    if (next === prev) return;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    if (typeof state.onChange === 'function') state.onChange(next);
  }

  function commit(value, { silent = false } = {}) {
    const next = String(value || '').trim();
    const prev = String(input.value || '').trim();
    input.value = next;
    close();
    if (!silent) fireChange(prev, next);
  }

  function renderList() {
    const q = input.value;
    const qn = norm(q);
    if (!state.options.length) {
      list.innerHTML = `<div class="searchable-select-empty">${esc(opts.emptyHint || 'No options')}</div>`;
      setExpanded(true);
      return;
    }
    if (!qn && state.options.length > BROWSE_WITHOUT_QUERY) {
      list.innerHTML =
        '<div class="searchable-select-empty">Type to search — list is long</div>';
      setExpanded(true);
      return;
    }
    const matches = filterOptions(state.options, q);
    if (!matches.length) {
      list.innerHTML = '<div class="searchable-select-empty">No matches</div>';
      setExpanded(true);
      state.activeIndex = -1;
      return;
    }
    const shown = matches.slice(0, MAX_SHOWN);
    const more = matches.length - shown.length;
    list.innerHTML =
      shown
        .map(
          (opt, i) =>
            `<button type="button" role="option" class="searchable-select-option${i === state.activeIndex ? ' active' : ''}" data-value="${esc(opt)}" data-idx="${i}">${esc(opt)}</button>`
        )
        .join('') +
      (more > 0
        ? `<div class="searchable-select-empty">+${more} more — keep typing</div>`
        : '');
    setExpanded(true);
  }

  function moveActive(delta) {
    const buttons = [...list.querySelectorAll('.searchable-select-option')];
    if (!buttons.length) return;
    if (state.activeIndex < 0) state.activeIndex = delta > 0 ? 0 : buttons.length - 1;
    else state.activeIndex = Math.max(0, Math.min(buttons.length - 1, state.activeIndex + delta));
    buttons.forEach((btn, i) => btn.classList.toggle('active', i === state.activeIndex));
    const el = buttons[state.activeIndex];
    if (el) el.scrollIntoView({ block: 'nearest' });
  }

  function pickActive() {
    const buttons = [...list.querySelectorAll('.searchable-select-option')];
    if (state.activeIndex >= 0 && buttons[state.activeIndex]) {
      commit(buttons[state.activeIndex].dataset.value || '');
      return true;
    }
    const hit = exactOption(input.value);
    if (hit) {
      commit(hit);
      return true;
    }
    return false;
  }

  input.addEventListener('focus', () => {
    if (state.skipNextFocusOpen) {
      state.skipNextFocusOpen = false;
      return;
    }
    state.activeIndex = -1;
    renderList();
  });

  input.addEventListener('input', () => {
    state.activeIndex = -1;
    renderList();
  });

  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      if (!state.open) renderList();
      else moveActive(1);
    } else if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      if (!state.open) renderList();
      else moveActive(-1);
    } else if (ev.key === 'Enter') {
      if (state.open) {
        ev.preventDefault();
        pickActive();
      }
    } else if (ev.key === 'Escape') {
      if (state.open) {
        ev.preventDefault();
        close();
      }
    }
  });

  input.addEventListener('blur', () => {
    if (state.suppressBlur) return;
    setTimeout(() => {
      if (state.suppressBlur) return;
      const hit = exactOption(input.value);
      if (hit) commit(hit);
      else if (input.value.trim()) commit('');
      else close();
    }, 120);
  });

  list.addEventListener('mousedown', (ev) => {
    const btn = ev.target.closest('.searchable-select-option');
    if (!btn) return;
    ev.preventDefault();
    state.suppressBlur = true;
    state.skipNextFocusOpen = true;
    commit(btn.dataset.value || '');
    state.suppressBlur = false;
    input.focus();
    close();
  });

  const api_ = {
    setOptions(options) {
      state.options = Array.isArray(options) ? options.slice() : [];
      if (state.open) renderList();
    },
    getOptions() {
      return state.options.slice();
    },
    setValue(value, { silent = true } = {}) {
      const next = String(value || '').trim();
      const resolved = next ? exactOption(next) : '';
      commit(resolved, { silent });
    },
    getValue() {
      return String(input.value || '').trim();
    },
    setDisabled(disabled) {
      input.disabled = !!disabled;
      if (disabled) close();
    },
    setPlaceholder(text) {
      input.placeholder = text || '';
    },
    set onChange(fn) {
      state.onChange = fn || null;
    },
    get onChange() {
      return state.onChange;
    },
    close,
  };

  INSTANCES.set(input, api_);
  return api_;
}

export function getSearchableSelect(input) {
  return input ? INSTANCES.get(input) || null : null;
}
