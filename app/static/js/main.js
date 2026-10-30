/**
 * Sales OS boot — SOLID entrypoint.
 *
 * Architecture (mandatory — see .cursor/rules/20-solid-frontend.mdc):
 *   core/*     shared kernel (dom, api, dialog, pager, state, notifications)
 *   shell/*    login, sidebar, topbar, router
 *   panels/*   one screen owner each + registry
 *   features/* cross-panel UI (invoices, compose, drawer, scoring, BE)
 *   app/*      bridge + strangler runtime (shrink on touch; do not dump new features here)
 *
 * New feature / bugfix → owning module above. Never grow index.html scripts.
 */
import './core/dom.js'; // installs globalThis.$ early
import './core/auth.js'; // Bearer token session for API calls
import './shell/sidebar.js';
import './shell/topbar.js';
import './shell/login.js';
import './shell/router.js';
import './panels/index.js';
import './features/drawer.js';
import './features/compose.js';
import './features/scoring.js';
import './features/be-bridge.js';
import './features/prospect-picker.js';
import './features/invoices.js';
import './features/email-signature.js';
import './app/runtime.js';
