/**
 * Sales OS application runtime (extracted from index.html).
 * Helpers live in core/*; this module owns panel/feature behavior + boot wiring.
 */
import { $ } from '../core/dom.js';
import { esc, api, toast, showMessage, fmtMoney, fmtDateTime, dateOnly, plusDays } from '../core/api.js';
import { setAuthTokens, clearAuthTokens, logoutAuth } from '../core/auth.js';
import { pageSlice, renderPager, ensurePagerHost, PAGE_SIZE_DEFAULT, pagerState } from '../core/pager.js';
import { closeUiDialog, openUiDialog, uiConfirm, uiForm } from '../core/dialog.js';
import {
  notifDismissStore, saveNotifDismissStore, isNotifDismissed, dismissNotification,
  clearAllNotifications, buildNotificationItems, refreshNotifications, getNotifLastItems
} from '../core/notifications.js';
import { registerPanel, getPanel } from '../panels/registry.js';
import { state as sharedState } from '../core/state.js';

import { closeSidebar } from '../shell/sidebar.js';
import { showLoginError } from '../shell/login.js';
import { closeTopMenus, updateTopbarTitle, PANEL_META, APP_PANELS } from '../shell/topbar.js';
import { refreshRegisteredPanel, safeRefresh } from '../shell/router.js';
import { B } from './bridge.js';
import {
  groupByLeadName, invoicePayload, invoicePreviewHtml, invoiceCardHtml,
  bindInvoiceCardActions, renderGroupedInvoiceCards, renderMoneyInvoices,
  renderCommsInvoices, loadCommsInvoices, currencySelectHtml, CURRENCY_OPTIONS
} from '../features/invoices.js';
import { wireProspectPicker, pickProspectField } from '../features/prospect-picker.js';
import { scoreOpts, readScoreCriteria, refreshScorePreview } from '../features/scoring.js';
import {
  contactQueueSelectedLeads, contactQueueReadyLeads, fillContactQueueLocationFilters, contactQueueMatchQuery,
  contactQueueBookingLeads, contactQueueChannelMatch, contactQueueIsParked,
  contactQueueVisibleLeads, fillContactQueueModes, renderContactQueueExplain,
  syncContactQueueSearchUi, syncContactQueueTabs, mapsUrlFor, channelTagsHtml,
  renderContactQueueList, markContactQueueIds, markBookingIds, loadContactQueue,
  saveContactQueueMode, getContactQueuePick, clearContactQueuePick, setContactQueueTab,
  setContactQueueQuery, setContactQueueChannel, getContactQueueLocations
} from '../panels/contact-queue.js';
import {
  brandLogoAbsoluteUrl, emailSignaturePlain, withEmailSignature,
  refreshSignatureUi,
} from '../features/email-signature.js';
import {
  refreshBeConnBanners, renderBeConnDetails, beCatalogIsPlan, beCatalogRows,
  formatBePrice, renderBeCatalogBody, loadBeCatalog, isOwnerRole, applyBeConnectOwnerGate,
  setBeRole, beConnCache, setBeConnCache, loadBeMonitor, loadBeEvents, renderCommercialPanel,
  bindCommercialPanelActions
} from '../features/be-bridge.js';

import { bindDashKpis, loadDashboard } from '../panels/dashboard.js';
import { loadReports, renderBars } from '../panels/reports.js';
import { loadTasks, renderTasksList, createTaskFlow } from '../panels/tasks.js';
import { DISCOVER_STATUS, isFreshDiscoverLead, resolveDiscoverStatus } from '../panels/discover.js';
import { loadDemos, renderDemosTable, loadDemoAssets, addDemoAssetFlow, uploadDemoAssetFile, createDemoFlow } from '../panels/demos.js';
import { loadProspects, renderProspectsTable, fillProspectsLocationFilters, prospectsScopeLabel, mergeProspectsLocCache } from '../panels/prospects.js';
import { showDrawerLoading, resolveDrawerMode, clampDrawerTab } from '../features/drawer.js';
import { listSkeletonHtml } from '../core/skeleton.js';
import {
  channelLabel as composeChannelLabel,
  firstEmail as composeFirstEmail,
  phoneDigits as composePhoneDigits,
  contactChannels as composeContactChannels,
  availableComposeChannels as composeAvailableChannels,
  preferredColdChannel,
  composeChannelControlsHtml,
  instagramHandle as composeInstagramHandle,
  instagramOpenUrls,
  instagramProfileWebUrl,
  wireIgWarmCompose,
  syncIgWarmComposeUi,
  refreshIgOutreachQuota,
  setIgOutreachQuota,
  guardIgSoftSend,
} from '../features/compose.js';
import {
  applyDiscoveredIg,
  ensureLeadInstagram,
  leadNeedsIgDiscover,
} from '../features/instagram-discover.js';
import {
  loadTemplates,
  setTplChannel,
  templatesFor,
  defaultTemplate,
  getTemplate,
  templateOptionLabel,
  getTemplatesStore,
} from '../panels/templates.js';

let rowsData=[];
let locations={};
let selectedIds=new Set();
let composeLead=null;
let composePreferred='email';
/** True when compose opened via invoice/pay (API confirm may skip Ready gate). */
let composeAllowUnready=false;
let drawerLead=null;
let drawerTab='overview';
let drawerMode='crm'; // outreach (Discover/CQ) | crm (Prospects + money flows)
let viewMode='city'; // city | queue | due
let openedChannel=null;
let composeAwaitingReturn=false;
let composeReturnArmedAt=0;
let enriching=false;
let lastUndo=null; // {type:'unarchive'|'restore', id, snapshot?}
let loadSeq=0; // ignore stale responses when views/filters change quickly
let appPanel='leads'; // dashboard | leads | pipeline | tasks | money | templates
/* lastPipelineStages / loadPipeline peeled → panels/pipeline.js */

let moneyTab='ledger'; // ledger | invoices | settings
let commsTab='outreach'; // outreach | invoices
let dealsTab='proposals'; // proposals | paylinks
let settingsTab='general'; // general | team | ranking | jobs | views
let ahTab='overview'; // overview | connect | events | catalogs
let currentRole='owner';



function setAppPanel(panel, opts){
  const allowed=APP_PANELS;
  const o=opts||{};
  // Allow "money:invoices" / "activities:invoices" / "appoinHive:connect" shorthand from KPI data-goto
  let target=panel;
  let tabHint='';
  if(typeof panel==='string' && panel.includes(':')){
    const parts=panel.split(':');
    target=parts[0];
    tabHint=parts[1]||'';
  }
  // Alias common misspelling / shorthand panel ids from KPIs & guide
  const aliases={
    appointHive:'appoinHive', appointHIve:'appoinHive',
    discover:'leads', discovery:'leads',
    queue:'contactQueue', contact:'contactQueue',
    comms:'activities', communication:'activities',
  };
  if(aliases[target]) target=aliases[target];
  if(!allowed.includes(target)){
    // Keep current panel if goto is unknown — never silent-dump every KPI onto Discover
    if(allowed.includes(appPanel)) target=appPanel;
    else target='leads';
  }
  appPanel=target;
  B.appPanel=appPanel;
  sharedState.appPanel=appPanel;
  if(tabHint){
    if(appPanel==='money') moneyTab=['ledger','invoices','settings'].includes(tabHint)?tabHint:moneyTab;
    if(appPanel==='activities') commsTab=tabHint==='invoices'?'invoices':'outreach';
    if(appPanel==='deals') dealsTab=tabHint==='paylinks'?'paylinks':'proposals';
    if(appPanel==='appoinHive') ahTab=['overview','connect','events','catalogs'].includes(tabHint)?tabHint:ahTab;
    if(appPanel==='settings') settingsTab=['general','team','ranking','jobs','views'].includes(tabHint)?tabHint:settingsTab;
  }
  if(o.moneyTab) moneyTab=o.moneyTab;
  if(o.commsTab) commsTab=o.commsTab;
  if(o.dealsTab) dealsTab=o.dealsTab;
  if(o.ahTab) ahTab=o.ahTab;
  allowed.forEach(p=>{
    const el=$(p+'Panel');
    if(el) el.classList.toggle('hidden',appPanel!==p);
  });
  document.documentElement.classList.toggle('templates-mode',appPanel==='templates');
  document.body.classList.toggle('templates-mode',appPanel==='templates');
  document.querySelectorAll('#appNav [data-app-panel]').forEach(btn=>btn.classList.toggle('active',btn.dataset.appPanel===appPanel));
  updateTopbarTitle(appPanel);
  closeTopMenus();
  closeSidebar();
  if(appPanel==='money') setMoneyTab(moneyTab);
  if(appPanel==='activities') setCommsTab(commsTab);
  if(appPanel==='deals'){
    document.querySelectorAll('#dealsTabs [data-deals-tab]').forEach(b=>b.classList.toggle('active',b.dataset.dealsTab===dealsTab));
    if($('dealsProposalsPane')) $('dealsProposalsPane').classList.toggle('hidden', dealsTab!=='proposals');
    if($('dealsPaylinksPane')) $('dealsPaylinksPane').classList.toggle('hidden', dealsTab!=='paylinks');
  }
  if(appPanel==='templates'){
    loadTemplates().catch(()=>{});
    requestAnimationFrame(()=>{$('tplBody')&&$('tplBody').focus({preventScroll:true})});
  }
  if(appPanel==='pipeline' || appPanel==='deals' || appPanel==='money'){
    refreshBeConnBanners().catch(()=>{});
  }
  if(appPanel==='appoinHive') setAhTab(ahTab);
  if(appPanel==='settings') setSettingsTab(settingsTab);
  // Panel data refresh via registry (same loaders as pre-peel if-chain in HEAD).
  // Special cases keep HEAD behavior: Comms invoices tab, Deals paylinks host.
  if(appPanel==='activities' && commsTab==='invoices'){
    loadCommsInvoices().catch(e=>toast(e.message,true));
  } else if(appPanel==='deals' && dealsTab==='paylinks'){
    const p = getPanel(appPanel);
    Promise.resolve(p && p.refresh ? p.refresh() : loadDeals())
      .then(()=>renderPaylinksHost())
      .catch(e=>toast(e.message,true));
  } else if(appPanel==='leads'){
    // HEAD did not reload Discover on nav; keep that — boot/filters own load().
  } else {
    const p = getPanel(appPanel);
    if(p && typeof p.refresh === 'function'){
      Promise.resolve(p.refresh()).catch(e=>toast(e && e.message ? e.message : e, true));
    }
  }
}

function setAhTab(tab){
  const allowed=['overview','connect','events','catalogs'];
  ahTab=allowed.includes(tab)?tab:'overview';
  document.querySelectorAll('#ahTabs [data-ah-tab]').forEach(b=>{
    const on=b.dataset.ahTab===ahTab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on?'true':'false');
  });
  document.querySelectorAll('#appoinHivePanel [data-ah-pane]').forEach(pane=>{
    pane.classList.toggle('hidden', pane.dataset.ahPane!==ahTab);
  });
  if(ahTab==='catalogs') loadBeCatalog().catch(()=>{});
  if(ahTab==='connect') applyBeConnectOwnerGate();
  if(ahTab==='events') loadBeEvents().catch(()=>{});
  if(ahTab==='overview') loadBeMonitor().catch(()=>{});
}





function setSettingsTab(tab){
  const allowed=['general','team','ranking','jobs','views'];
  settingsTab=allowed.includes(tab)?tab:'general';
  document.querySelectorAll('#settingsTabs [data-settings-tab]').forEach(b=>{
    const on=b.dataset.settingsTab===settingsTab;
    b.classList.toggle('active',on);
    b.setAttribute('aria-selected', on?'true':'false');
  });
  document.querySelectorAll('#settingsPanel [data-settings-pane]').forEach(pane=>{
    pane.classList.toggle('hidden', pane.dataset.settingsPane!==settingsTab);
  });
}























async function loadProfileBar(username){
  let role='owner', email=username||'', name=username||'Account';
  try{
    const me=await api('/api/sales/me');
    email=(me.user&&me.user.email)||me.username||username||'';
    name=(me.user&&me.user.name)||email.split('@')[0]||'Account';
    role=(me.user&&me.user.role)||'owner';
  }catch(e){ /* keep defaults */ }
  currentRole=role||'owner'; setBeRole(currentRole);
  const initials=(name||email||'A').trim().charAt(0).toUpperCase()||'A';
  if($('profileAvatar')) $('profileAvatar').textContent=initials;
  if($('profileName')) $('profileName').textContent=name;
  if($('profileRole')) $('profileRole').textContent=role;
  if($('profileMenuEmail')) $('profileMenuEmail').textContent=email;
  if($('profileMenuRole')) $('profileMenuRole').textContent=role;
  applyBeConnectOwnerGate();
}




async function openProspectById(id){
  try{ await openDrawer(id); }
  catch(e){ toast(e.message,true); }
}



let activitiesCache=[];

























function renderActivitiesFeed(){
  const list=activitiesCache||[];
  const feed=$('activitiesFeed');
  const pagerHost=ensurePagerHost(feed,'activitiesPager');
  if(!list.length){
    feed.innerHTML='<div class="empty-card">No activities yet. Log a call or message outcome.</div>';
    if(pagerHost) pagerHost.innerHTML='';
    return;
  }
  const groups=groupByLeadName(list);
  const slice=pageSlice(groups,'activities');
  feed.innerHTML=slice.items.map(g=>`
    <section class="lead-group">
      <div class="lead-group-head">
        <strong>${esc(g.name)}</strong>
        <div class="action-stack">
          ${g.business_id?`<button type="button" data-open-prospect="${g.business_id}">Open lead</button>`:''}
          <span class="sub">${g.items.length}</span>
        </div>
      </div>
      <div class="lead-group-body">
        ${g.items.map(a=>{
          const msg=a.message||a.next_action||'';
          return `
          <article class="task-row">
            <div class="task-main">
              <div class="biz">${esc(a.summary||a.activity_type)}${a.channel?' · '+esc(a.channel):''}</div>
              <div class="sub">${a.outcome?esc(a.outcome)+' · ':''}${esc(fmtDateTime(a.created_at||''))}</div>
              ${msg?`<pre class="sent-message">${esc(msg)}</pre>`:''}
            </div>
            <div class="action-stack">
              <button type="button" class="danger" data-del-activity="${a.id}">Delete</button>
            </div>
          </article>`;
        }).join('')}
      </div>
    </section>`).join('');
  renderPager(pagerHost, slice, renderActivitiesFeed);
  feed.querySelectorAll('[data-open-prospect]').forEach(btn=>btn.addEventListener('click',()=>openProspectById(btn.dataset.openProspect)));
  feed.querySelectorAll('[data-del-activity]').forEach(btn=>btn.addEventListener('click',async()=>{
    const ok=await uiConfirm({title:'Delete activity?', message:'This removes the log entry only.', confirmLabel:'Delete', danger:true});
    if(!ok) return;
    await api('/api/sales/activities/'+btn.dataset.delActivity,{method:'DELETE'});
    toast('Activity deleted'); await loadActivitiesFeed();
  }));
}
async function loadActivitiesFeed(){
  const data=await api('/api/sales/activities?limit=200');
  activitiesCache=data.activities||[];
  if(pagerState.activities) pagerState.activities.page=1;
  renderActivitiesFeed();
}






function setCommsTab(tab){
  commsTab=tab==='invoices'?'invoices':'outreach';
  B.commsTab=commsTab; sharedState.commsTab=commsTab;
  document.querySelectorAll('#commsTabs [data-comms-tab]').forEach(b=>{
    const on=b.dataset.commsTab===commsTab;
    b.classList.toggle('active',on);
    b.setAttribute('aria-selected', on?'true':'false');
  });
  if($('commsOutreachPane')) $('commsOutreachPane').classList.toggle('hidden', commsTab!=='outreach');
  if($('commsInvoicesPane')) $('commsInvoicesPane').classList.toggle('hidden', commsTab!=='invoices');
  if(commsTab==='invoices') loadCommsInvoices().catch(e=>toast(e.message,true));
}
function setMoneyTab(tab){
  moneyTab=['ledger','invoices','settings'].includes(tab)?tab:'ledger';
  B.moneyTab=moneyTab; sharedState.moneyTab=moneyTab;
  document.querySelectorAll('#moneyTabs [data-money-tab]').forEach(b=>{
    const on=b.dataset.moneyTab===moneyTab;
    b.classList.toggle('active',on);
    b.setAttribute('aria-selected', on?'true':'false');
  });
  if($('moneyLedgerPane')) $('moneyLedgerPane').classList.toggle('hidden', moneyTab!=='ledger');
  if($('moneyInvoicesPane')) $('moneyInvoicesPane').classList.toggle('hidden', moneyTab!=='invoices');
  if($('moneySettingsPane')) $('moneySettingsPane').classList.toggle('hidden', moneyTab!=='settings');
}















async function loadRankingModesCrud(){
  const panel=$('rankingModesPanel');
  if(!panel) return;
  try{
    const data=await api('/api/sales/ranking-modes?include_inactive=true');
    const rows=data.modes||[];
    panel.innerHTML=rows.length?`<div class="sales-table-wrap"><table class="sales-table"><thead><tr><th>Name</th><th>Slug</th><th>Filter</th><th>Boost</th><th>Active</th><th></th></tr></thead><tbody>
      ${rows.map(m=>`<tr>
        <td><strong>${esc(m.name)}</strong><div class="sub">${esc(m.description||'')}</div></td>
        <td><code>${esc(m.slug)}</code></td>
        <td><span class="sub">${esc(Object.entries(m.filter||{}).map(([k,v])=>k.replace(/_/g,' ')+': '+(v?'yes':'no')).join(' · ')||'any')}</span></td>
        <td><span class="sub">+${esc(String((m.boost&&m.boost.base_boost)||0))}${m.boost&&m.boost.tag?' · '+esc(m.boost.tag):''}</span></td>
        <td>${m.active?'yes':'no'}</td>
        <td><div class="action-stack">
          <button type="button" data-edit-rank="${m.id}">Edit</button>
          ${m.slug==='default'?'':`<button type="button" class="danger" data-del-rank="${m.id}">Delete</button>`}
        </div></td>
      </tr>`).join('')}
    </tbody></table></div>`:'<div class="empty-card">No modes — seed on next restart</div>';
    const byId=Object.fromEntries(rows.map(m=>[String(m.id),m]));
    panel.querySelectorAll('[data-edit-rank]').forEach(btn=>btn.addEventListener('click',()=>editRankingModeFlow(byId[btn.dataset.editRank])));
    panel.querySelectorAll('[data-del-rank]').forEach(btn=>btn.addEventListener('click',async()=>{
      const ok=await uiConfirm({title:'Delete ranking mode?', confirmLabel:'Delete', danger:true});
      if(!ok) return;
      await api('/api/sales/ranking-modes/'+btn.dataset.delRank,{method:'DELETE'});
      toast('Mode deleted'); await loadRankingModesCrud();
    }));
  }catch(e){ panel.textContent=e.message||'Owner only'; }
}

const RANK_FILTER_KEYS=[
  ['has_website','Has website'],
  ['has_email','Has email'],
  ['has_phone','Has phone'],
  ['has_instagram','Has Instagram'],
  ['email_sent','Email sent'],
  ['whatsapp_sent','WhatsApp sent'],
  ['instagram_sent','Instagram sent'],
  ['marked_for_contact','Starred / marked'],
  ['contacted','Contacted'],
];
function rankFilterSelectValue(filt, key){
  if(!filt||!(key in filt)||filt[key]==null) return 'any';
  return filt[key]?'yes':'no';
}
function buildFilterFromForm(form){
  const filter={};
  RANK_FILTER_KEYS.forEach(([k])=>{
    const v=form['f_'+k];
    if(v==='yes') filter[k]=true;
    else if(v==='no') filter[k]=false;
  });
  return filter;
}
async function editRankingModeFlow(existing){
  const f=existing&&existing.filter||{};
  const b=existing&&existing.boost||{};
  const tri=[{value:'any',label:'Any'},{value:'yes',label:'Yes'},{value:'no',label:'No'}];
  const filterFields=RANK_FILTER_KEYS.map(([k,label])=>({
    name:'f_'+k, label:label, type:'select', value:rankFilterSelectValue(f,k), options:tri
  }));
  const form=await uiForm({
    title: existing?'Edit ranking mode':'Add ranking mode',
    message:'Set filters with Yes/No/Any (no JSON required). Boost points raise queue priority.',
    confirmLabel:'Save',
    fields:[
      {name:'name', label:'Name', required:true, value:existing&&existing.name||''},
      {name:'slug', label:'Slug', value:existing&&existing.slug||'', placeholder:'auto from name if blank'},
      {name:'description', label:'Description', type:'textarea', rows:2, value:existing&&existing.description||''},
      ...filterFields,
      {name:'base_boost', label:'Boost points', type:'number', value:String(b.base_boost!=null?b.base_boost:5)},
      {name:'boost_tag', label:'Boost tag (shown in queue)', value:b.tag||''},
      {name:'sort_order', label:'Sort order', type:'number', value:String((existing&&existing.sort_order)||0)},
      {name:'active', label:'Active', type:'select', value:String(existing?!!existing.active:true), options:[{value:'true',label:'Active'},{value:'false',label:'Inactive'}]}
    ]
  });
  if(!form) return;
  const filter=buildFilterFromForm(form);
  const boost={base_boost:Number(form.base_boost||0), tag:(form.boost_tag||'').trim()};
  const body={
    name:form.name, slug:form.slug||'', description:form.description||'',
    filter, boost, sort_order:Number(form.sort_order||0), active:form.active==='true'
  };
  if(existing&&existing.id) await api('/api/sales/ranking-modes/'+existing.id,{method:'PUT',body});
  else await api('/api/sales/ranking-modes',{method:'POST',body});
  toast('Ranking mode saved'); await loadRankingModesCrud();
}

const PAY_SETUP_FIELDS={
  stripe:[
    {key:'pay.stripe_secret_key', label:'Secret key', secret:true, placeholder:'sk_test_… or sk_live_…'},
    {key:'pay.stripe_publishable_key', label:'Publishable key', secret:false, placeholder:'pk_test_…'},
    {key:'pay.stripe_webhook_secret', label:'Webhook secret', secret:true, placeholder:'whsec_…'},
    {key:'pay.stripe_mode', label:'Mode', secret:false, placeholder:'test or live', type:'select', options:[{value:'test',label:'test'},{value:'live',label:'live'}]},
    {key:'pay.public_base_url', label:'Public app URL', secret:false, placeholder:'https://your-app.example'},
  ],
  paypal:[
    {key:'pay.paypal_client_id', label:'Client ID', secret:false, placeholder:'PayPal client id'},
    {key:'pay.paypal_client_secret', label:'Client secret', secret:true, placeholder:'••••••••'},
    {key:'pay.paypal_webhook_id', label:'Webhook ID', secret:false, placeholder:'Webhook id'},
    {key:'pay.paypal_mode', label:'Mode', secret:false, type:'select', options:[{value:'sandbox',label:'sandbox'},{value:'live',label:'live'}]},
  ],
  payoneer:[
    {key:'pay.payoneer_payee_email', label:'Payee email', secret:false, placeholder:'you@payoneer.example'},
    {key:'pay.payoneer_api_key', label:'API key (optional)', secret:true, placeholder:'••••••••'},
    {key:'pay.payoneer_webhook_secret', label:'Webhook secret', secret:true, placeholder:'Shared secret for X-Webhook-Secret'},
  ],
  skrill:[
    {key:'pay.skrill_pay_to_email', label:'Pay-to email', secret:false, placeholder:'you@skrill.example'},
    {key:'pay.skrill_merchant_id', label:'Merchant ID', secret:false, placeholder:'optional'},
    {key:'pay.skrill_secret', label:'Secret word', secret:true, placeholder:'••••••••'},
    {key:'pay.skrill_webhook_secret', label:'Webhook secret', secret:true, placeholder:'Shared secret for X-Webhook-Secret'},
  ],
  mail:[
    {key:'mail.smtp_host', label:'SMTP host', secret:false, placeholder:'smtp.example.com', mail:true},
    {key:'mail.smtp_port', label:'SMTP port', secret:false, placeholder:'587', mail:true},
    {key:'mail.smtp_user', label:'SMTP user', secret:false, placeholder:'optional', mail:true},
    {key:'mail.smtp_password', label:'SMTP password', secret:true, placeholder:'••••••••', mail:true},
    {key:'mail.from_email', label:'From email', secret:false, placeholder:'sales@example.com', mail:true},
    {key:'mail.smtp_tls', label:'TLS', secret:false, type:'select', options:[{value:'true',label:'true (STARTTLS)'},{value:'false',label:'false'}], mail:true},
  ],
};
let paySetupTab='stripe';
let paySetupCache={settings:[], mail:[], providers:{}};

function paySetupSetStatus(msg, kind){
  const el=$('paySetupStatus');
  if(!el) return;
  el.className='pay-status '+(kind||'info');
  el.textContent=msg||'';
}
function paySetupFieldValue(key, secret){
  const rows=[...(paySetupCache.settings||[]),...(paySetupCache.mail||[])];
  const row=rows.find(s=>s.key===key);
  if(!row||!row.configured) return '';
  if(secret) return ''; // never prefill secrets
  return row.masked||row.hint||'';
}
function renderPaySetupForm(){
  const host=$('paySetupFormHost');
  if(!host) return;
  const fields=PAY_SETUP_FIELDS[paySetupTab]||[];
  host.innerHTML=`<form class="pay-form" id="paySetupForm" onsubmit="return false">
    ${fields.map(f=>{
      const id='payf_'+f.key.replace(/\./g,'_');
      const cur=paySetupFieldValue(f.key, !!f.secret);
      if(f.type==='select'){
        const opts=(f.options||[]).map(o=>`<option value="${esc(o.value)}" ${String(o.value)===String(cur||f.options[0].value)?'selected':''}>${esc(o.label)}</option>`).join('');
        return `<div class="field"><label for="${id}">${esc(f.label)}</label><select id="${id}" name="${esc(f.key)}">${opts}</select></div>`;
      }
      return `<div class="field"><label for="${id}">${esc(f.label)}${f.secret?' <span class="hint">(leave blank to keep existing)</span>':''}</label>
        <input id="${id}" name="${esc(f.key)}" type="${f.secret?'password':'text'}" value="${esc(cur)}" placeholder="${esc(f.placeholder||'')}" autocomplete="off"></div>`;
    }).join('')}
  </form>`;
  document.querySelectorAll('#paySetupTabs [data-pay-tab]').forEach(btn=>btn.classList.toggle('active', btn.dataset.payTab===paySetupTab));
}
async function loadPaySetup(){
  // Always paint the form first so the screen is never blank while APIs load.
  renderPaySetupForm();
  try{
    const [pay, mail]=await Promise.all([
      api('/api/sales/payment-settings'),
      api('/api/sales/mail-settings').catch(()=>({settings:[], storage:{}})),
    ]);
    paySetupCache={settings:pay.settings||[], mail:(mail.settings||[]).filter(s=>s.key&&s.key!=='_status'), providers:pay.providers||{}};
    renderPaySetupForm();
    const el=$('paySetupProviders');
    if(el){
      const p=paySetupCache.providers||{};
      el.innerHTML=['stripe','paypal','payoneer','skrill','manual'].map(k=>{
        const row=p[k]||{};
        return `<div class="prov-card"><strong>${esc(k)} · ${row.configured?'ready':'not configured'}</strong>
          <div class="sub">Auto link: ${row.auto_link?'yes':'no'} · Webhook: ${row.auto_paid_webhook?'yes':'no'}${row.mode?' · '+esc(row.mode):''}</div></div>`;
      }).join('');
    }
    const ready=Object.entries(paySetupCache.providers||{}).filter(([k,v])=>k!=='test_mode_banner'&&v&&v.configured).map(([k])=>k);
    paySetupSetStatus(ready.length?('Configured: '+ready.join(', ')+'. Open a tab to edit keys, then Validate.'):'No providers configured yet. Start with Stripe or PayPal — fill fields, Save, Validate.', 'info');
  }catch(e){
    paySetupSetStatus((e.message||'Could not load settings')+' — you can still fill and Save if you are Owner.', 'bad');
    const el=$('paySetupProviders');
    if(el) el.innerHTML='<div class="hint">Status unavailable. Form below still works after Save.</div>';
  }
}
async function savePaySetup(){
  const fields=PAY_SETUP_FIELDS[paySetupTab]||[];
  const paySettings={};
  const mailSettings={};
  fields.forEach(f=>{
    const el=document.getElementById('payf_'+f.key.replace(/\./g,'_'));
    if(!el) return;
    const v=el.value;
    if(f.mail) mailSettings[f.key]=v;
    else paySettings[f.key]=v;
  });
  if(Object.keys(paySettings).length){
    await api('/api/sales/payment-settings/bulk',{method:'PUT',body:{settings:paySettings}});
  }
  for(const [key,value] of Object.entries(mailSettings)){
    if(key.endsWith('password') && !String(value||'').trim()) continue;
    await api('/api/sales/mail-settings',{method:'PUT',body:{key,value:value||''}});
  }
  toast('Saved '+paySetupTab+' settings');
  await loadPaySetup();
}
async function validatePaySetup(){
  paySetupSetStatus('Validating '+paySetupTab+'…', 'info');
  try{
    const r=await api('/api/sales/payment-settings/validate',{method:'POST',body:{provider:paySetupTab}});
    paySetupSetStatus(r.message||(r.valid?'OK':'Failed'), r.valid?'ok':'bad');
    toast(r.valid?'Validation passed':'Validation failed', !r.valid);
  }catch(e){
    paySetupSetStatus(e.message||'Validate failed', 'bad');
    toast(e.message,true);
  }
}
async function applyLocalPayDefaults(){
  const ok=await uiConfirm({
    title:'Apply local defaults?',
    message:'Sets test/sandbox modes, http://localhost:8000, Payoneer/Skrill payee + webhook secrets. Does not create Stripe or PayPal API keys — paste those from the provider dashboard.',
    confirmLabel:'Apply',
  });
  if(!ok) return;
  paySetupSetStatus('Applying local defaults…', 'info');
  const r=await api('/api/sales/payment-settings/fill-local-defaults',{method:'POST',body:{}});
  paySetupSetStatus(r.note||'Local defaults saved', 'ok');
  toast('Local payment defaults applied');
  await loadPaySetup();
}

let beRevisionsCache=[];














async function loadAppointHive(){
  const banner=$('beConnBanner');
  const revHost=$('beRevisionsHost');
  try{
    const conn=await api('/api/sales/be/connection');
    setBeConnCache(conn);
    const st=conn.connection_status||'disconnected';
    const cls=conn.connected?'ok':(st==='error'?'err':'info');
    if(banner){
      banner.className='pay-status '+cls;
      banner.innerHTML=conn.connected
        ?`<strong>Connected</strong> to AppointHive at ${esc(conn.base_url||'—')}. Live prices + pay links enabled. Local STANDALONE PSP blocked. <button type="button" class="chip ok" data-goto="appoinHive:connect">Connection details</button>`
        :`<strong>${esc(st)}</strong> — Connect AppointHive first. Catalog / send / upgrade stay disabled until Test connection succeeds. <button type="button" class="chip ok" data-goto="appoinHive:connect">Open Connect</button>`;
    }
    renderBeConnDetails(conn);
    if($('beBaseUrl') && conn.base_url && !$('beBaseUrl').value) $('beBaseUrl').value=conn.base_url;
    if($('bePairBox')){
      $('bePairBox').innerHTML=conn.pairing_code_active
        ?`Pairing code active until ${esc(conn.pairing_expires_at)}. On AppointHive Super Admin → AppointHive Sales OS, paste this Sales OS URL and the code.`
        :(conn.secret_configured?`Secret configured (value hidden) · webhook ${esc(conn.webhook_path||'')}`:'No secret yet — generate a pairing code or paste one.');
    }
    if(revHost){
      if(!conn.connected){
        revHost.innerHTML=`Connect first to load packages. <button type="button" class="chip ok" data-goto="appoinHive:connect">Open Connect</button>`;
      }else{
        try{
          const data=await api('/api/sales/be/revisions');
          const rows=data.revisions||[];
          beRevisionsCache=rows;
          revHost.innerHTML=rows.length
            ?`<div class="sales-table-wrap"><table class="sales-table"><thead><tr><th>Package</th><th>Edition</th><th>Price</th><th>Revision</th></tr></thead><tbody>
              ${rows.map(r=>`<tr>
                <td><strong>${esc(r.label||r.productName||r.productId||'—')}</strong></td>
                <td>${esc(r.edition||'')}</td>
                <td>${esc(r.currency||'USD')} ${(Number(r.priceCents||0)/100).toFixed(2)}</td>
                <td><code>${esc(r.productRevisionId||r.id||'')}</code></td>
              </tr>`).join('')}
            </tbody></table></div>`
            :'<div class="empty-card">No FINALIZED revisions on AppointHive yet.</div>';
        }catch(e){
          revHost.innerHTML=`<div class="pay-status err">${esc(e.message||'Failed to load revisions')}</div>`;
        }
      }
    }
    applyBeConnectOwnerGate();
    await loadBeMonitor().catch(()=>{});
    if(ahTab==='catalogs') await loadBeCatalog().catch(()=>{});
    if(ahTab==='events') await loadBeEvents().catch(()=>{});
  }catch(e){
    if(banner){ banner.className='pay-status err'; banner.textContent=e.message||'Failed to load connection'; }
    if($('beConnDetails')) $('beConnDetails').textContent=e.message||'Failed to load connection';
    applyBeConnectOwnerGate();
  }
}

async function loadPaymentSettings(){
  const panel=$('paymentSettingsPanel');
  if(!panel) return;
  try{
    const data=await api('/api/sales/payment-settings');
    const rows=data.settings||[];
    panel.innerHTML=`<div class="sales-table-wrap"><table class="sales-table"><thead><tr><th>Setting</th><th>Status</th><th>Update</th></tr></thead><tbody>
      ${rows.map(s=>`<tr>
        <td><strong>${esc(s.label)}</strong><div class="sub">${esc(s.key)} · env ${esc(s.env)}</div></td>
        <td>${s.configured?esc(s.hint||s.masked):'not set'}${s.secret?' <span class="hint">secret</span>':''}</td>
        <td><button type="button" data-pay-set="${esc(s.key)}" data-secret="${s.secret?'1':'0'}">Set</button></td>
      </tr>`).join('')}
    </tbody></table></div>`;
    panel.querySelectorAll('[data-pay-set]').forEach(btn=>btn.addEventListener('click',async()=>{
      const form=await uiForm({
        title:'Set '+btn.dataset.paySet,
        message:btn.dataset.secret==='1'?'Secret values are encrypted at rest. Leave blank to clear DB override (falls back to .env).':'',
        confirmLabel:'Save',
        fields:[{name:'value', label:'Value', type:btn.dataset.secret==='1'?'password':'text', placeholder:btn.dataset.secret==='1'?'••••••••':''}]
      });
      if(!form) return;
      await api('/api/sales/payment-settings',{method:'PUT',body:{key:btn.dataset.paySet, value:form.value||''}});
      toast('Payment setting saved'); await loadPaymentSettings(); await loadMoney();
    }));
  }catch(e){
    panel.innerHTML=`<div class="hint">${esc(e.message||'Owner only')}</div>`;
  }
  await loadMailSettings().catch(()=>{});
}
async function loadMailSettings(){
  const panel=$('mailSettingsPanel');
  if(!panel) return;
  try{
    const data=await api('/api/sales/mail-settings');
    const rows=(data.settings||[]).filter(s=>s.key&&s.key!=='_status');
    const st=data.storage||{};
    panel.innerHTML=`<p class="hint" style="margin:0 0 8px">SMTP: ${data.smtp_ready?'ready':'not ready'} · Storage: <strong>${esc(st.backend||'local')}</strong>${st.s3_bucket?' · bucket '+esc(st.s3_bucket):''}${st.note?' · '+esc(st.note):''}</p>
      <div class="sales-table-wrap"><table class="sales-table"><thead><tr><th>Setting</th><th>Status</th><th></th></tr></thead><tbody>
      ${rows.map(s=>`<tr>
        <td><strong>${esc(s.label)}</strong><div class="sub">${esc(s.key)}</div></td>
        <td>${s.configured?esc(s.hint||s.masked):'not set'}</td>
        <td><button type="button" data-mail-set="${esc(s.key)}" data-secret="${s.secret?'1':'0'}">Set</button></td>
      </tr>`).join('')}
      </tbody></table></div>`;
    panel.querySelectorAll('[data-mail-set]').forEach(btn=>btn.addEventListener('click',async()=>{
      const form=await uiForm({
        title:'Set '+btn.dataset.mailSet,
        confirmLabel:'Save',
        fields:[{name:'value', label:'Value', type:btn.dataset.secret==='1'?'password':'text'}]
      });
      if(!form) return;
      await api('/api/sales/mail-settings',{method:'PUT',body:{key:btn.dataset.mailSet, value:form.value||''}});
      toast('Mail setting saved'); await loadMailSettings();
    }));
  }catch(e){
    panel.innerHTML=`<div class="hint">${esc(e.message||'Owner only')}</div>`;
  }
}







async function loadSettings(){
  const me=await api('/api/sales/me');
  await refreshSignatureUi().catch(()=>{});
  $('saasEnabledToggle').checked=!!me.saas_enabled;
  try{
    const settingsPayload=await api('/api/sales/settings');
    if(settingsPayload.ig_outreach_quota) setIgOutreachQuota(settingsPayload.ig_outreach_quota);
    const capRaw=(settingsPayload.settings&&settingsPayload.settings.ig_soft_dm_daily_cap);
    const capParsed=parseInt(capRaw,10);
    if($('igSoftDmDailyCap')) $('igSoftDmDailyCap').value=String(Number.isFinite(capParsed)&&capParsed>=0?capParsed:8);
    const waitRaw=(settingsPayload.settings&&settingsPayload.settings.ig_ready_wait_days);
    const waitParsed=parseInt(waitRaw,10);
    if($('igReadyWaitDays')) $('igReadyWaitDays').value=String(Number.isFinite(waitParsed)&&waitParsed>=0&&waitParsed<=14?waitParsed:1);
  }catch(_){}
  try{
    const wp=await api('/api/sales/be/won-policy');
    if($('wonAutoOnPaid')) $('wonAutoOnPaid').checked=!!wp.auto_won_on_be_paid;
    if($('wonRequireFullyPaid')) $('wonRequireFullyPaid').checked=wp.require_fully_paid!==false;
    if($('wonRequireAgreement')) $('wonRequireAgreement').checked=!!wp.require_agreement_accepted;
    if($('wonRequireMilestone')) $('wonRequireMilestone').checked=!!wp.require_initial_milestone_paid;
  }catch(_){}
  $('settingsMeta').innerHTML=`
    <div class="queue-card"><span>Signed in</span><strong style="font-size:14px">${esc((me.user&&me.user.email)||me.username||'')}</strong></div>
    <div class="queue-card"><span>Role</span><strong style="font-size:14px">${esc((me.user&&me.user.role)||'owner')}</strong></div>
    <div class="queue-card"><span>won_on</span><strong style="font-size:14px">${esc(me.won_on||'first_payment')}</strong></div>
    <div class="queue-card"><span>SaaS</span><strong style="font-size:14px">${me.saas_enabled?'enabled':'disabled'}</strong></div>`;
  const saasOpt=$('pipelineLine')&&$('pipelineLine').querySelector('option[value="SAAS"]');
  if(saasOpt) saasOpt.disabled=!me.saas_enabled;
  try{
    const audit=await api('/api/sales/audit?limit=50');
    $('auditTable').querySelector('tbody').innerHTML=(audit.events||[]).map(e=>`<tr>
      <td>${esc(fmtDateTime(e.created_at))}</td><td>${esc(e.action)}</td><td>${esc(e.entity_type)} #${esc(e.entity_id||'')}</td>
    </tr>`).join('')||'<tr><td colspan="3">No audit events yet</td></tr>';
  }catch(e){
    $('auditTable').querySelector('tbody').innerHTML=`<tr><td colspan="3">${esc(e.message||'Owner only')}</td></tr>`;
  }
  try{
    const [users, terr]=await Promise.all([api('/api/sales/users'), api('/api/sales/territories')]);
    const urows=users.users||[];
    $('usersTerritories').innerHTML=`
      <div class="sales-table-wrap" style="margin-bottom:10px"><table class="sales-table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Password</th><th></th></tr></thead><tbody>
        ${urows.length?urows.map(u=>`<tr>
          <td>${esc(u.name||'—')}</td><td>${esc(u.email)}</td><td>${esc(u.role)}</td>
          <td>${u.has_password?'set':'missing'}</td>
          <td><button type="button" data-set-pw="${u.id}" data-email="${esc(u.email)}">Set password</button></td>
        </tr>`).join(''):'<tr><td colspan="5">No users yet</td></tr>'}
      </tbody></table></div>`;
    $('usersTerritories').querySelectorAll('[data-set-pw]').forEach(btn=>btn.addEventListener('click',async()=>{
      const form=await uiForm({title:'Set password for '+btn.dataset.email, confirmLabel:'Save', fields:[
        {name:'password', label:'New password', type:'password', required:true, placeholder:'min 8 characters'}
      ]});
      if(!form) return;
      await api('/api/sales/users/'+btn.dataset.setPw+'/password',{method:'POST',body:{password:form.password}});
      toast('Password updated'); await loadSettings();
    }));
    const trows=terr.territories||[];
    if($('territoriesPanel')){
      $('territoriesPanel').innerHTML=trows.length?`<div class="sales-table-wrap"><table class="sales-table"><thead><tr><th>Name</th><th>Country</th><th>Owner</th><th></th></tr></thead><tbody>
        ${trows.map(t=>`<tr><td>${esc(t.name)}</td><td>${esc(t.country)}</td><td>#${esc(t.owner_id||'—')}</td>
        <td><button type="button" data-assign-terr="${t.id}">Auto-assign unowned</button></td></tr>`).join('')}
      </tbody></table></div>`:'<div class="empty-card">No territories yet</div>';
      $('territoriesPanel').querySelectorAll('[data-assign-terr]').forEach(btn=>btn.addEventListener('click',async()=>{
        const res=await api('/api/sales/territories/'+btn.dataset.assignTerr+'/assign-prospects',{method:'POST',body:{}});
        toast('Assigned '+res.assigned+' prospects'); await loadSettings();
      }));
    }
  }catch(e){$('usersTerritories').textContent=e.message||String(e)}
  try{
    const views=await api('/api/sales/saved-views');
    const list=$('savedViewsList');
    if(!list) return;
    const rows=views.views||[];
    if(!rows.length){ list.innerHTML='<div class="empty-card">No saved views yet. Set Discover filters, name the view, then Save.</div>'; }
    else{
      list.innerHTML=rows.map(v=>`<div class="view-chip">
        <div><div class="biz">${esc(v.name)}</div><div class="sub">${esc(JSON.stringify(v.filters||{}))}</div></div>
        <div class="action-stack">
          <button type="button" class="primary" data-apply-view="${v.id}">Apply</button>
          <button type="button" class="danger" data-del-view="${v.id}" data-name="${esc(v.name)}">Delete</button>
        </div>
      </div>`).join('');
      list.querySelectorAll('[data-apply-view]').forEach(btn=>btn.addEventListener('click',()=>{
        const v=rows.find(x=>String(x.id)===String(btn.dataset.applyView));
        if(!v||!v.filters) return;
        if(v.filters.product_line && $('pipelineLine')){ $('pipelineLine').value=v.filters.product_line; }
        if(v.filters.country && $('country')) $('country').value=v.filters.country;
        if(v.filters.city && $('city')) $('city').value=v.filters.city;
        if(v.filters.category && $('category')) $('category').value=v.filters.category;
        if(v.filters.status && $('statusFilter')) $('statusFilter').value=v.filters.status;
        toast('View applied: '+v.name);
        if(appPanel==='pipeline') loadPipeline().catch(e=>toast(e.message,true));
        else if(appPanel==='leads') load().catch(e=>toast(e.message,true));
        else setAppPanel('leads');
      }));
      list.querySelectorAll('[data-del-view]').forEach(btn=>btn.addEventListener('click',async()=>{
        const ok=await uiConfirm({
          title:'Delete saved view?',
          message:`“${btn.dataset.name||'This view'}” will be removed. Filters themselves are unchanged.`,
          confirmLabel:'Delete view',
          danger:true
        });
        if(!ok) return;
        await api('/api/sales/saved-views/'+btn.dataset.delView,{method:'DELETE'});
        toast('View deleted'); await loadSettings();
      }));
    }
  }catch(e){ if($('savedViewsList')) $('savedViewsList').textContent=e.message||String(e); }
  await loadRankingModesCrud().catch(()=>{});
}





















async function loadPipeline(){
  if(typeof B.loadPipeline==='function') return B.loadPipeline();
}
async function moveProspectStage(id, stageId, note='', lostReason=''){
  if(typeof B.moveProspectStage==='function') return B.moveProspectStage(id, stageId, note, lostReason);
}









async function createActivityFlow(){
  const bid=await pickProspectField('Activity for which prospect?');
  if(bid==null) return;
  const form=await uiForm({
    title:'Log activity',
    confirmLabel:'Save',
    fields:[
      {name:'activity_type', label:'Type', type:'select', required:true, options:[
        {value:'call',label:'Call'},{value:'email',label:'Email'},{value:'whatsapp',label:'WhatsApp'},
        {value:'instagram',label:'Instagram'},{value:'note',label:'Note'},{value:'meeting',label:'Meeting'}
      ]},
      {name:'summary', label:'Summary', required:true, type:'textarea', rows:2},
      {name:'outcome', label:'Outcome', placeholder:'interested / no reply / …'},
      {name:'next_action', label:'Next action', placeholder:'Send proposal Friday'},
      {name:'channel', label:'Channel', placeholder:'phone / email / wa'}
    ]
  });
  if(!form) return;
  await api('/api/sales/activities',{method:'POST',body:{business_id:bid, ...form}});
  toast('Activity logged'); await loadActivitiesFeed();
}




function renderProvidersSetup(prov){
  const el=$('providersSetup');
  if(!el) return;
  const cards=[
    {key:'stripe', name:'Stripe', hint:'Set STRIPE_SECRET_KEY + STRIPE_MODE. Auto checkout links + webhooks.'},
    {key:'paypal', name:'PayPal', hint:'Set PAYPAL_CLIENT_ID/SECRET + PAYPAL_MODE. Auto order links.'},
    {key:'payoneer', name:'Payoneer', hint:'Set PAYONEER_PAYEE_EMAIL (+ optional API key / PAYONEER_WEBHOOK_SECRET). Copy instructions to lead; Owner confirm or webhook.'},
    {key:'skrill', name:'Skrill', hint:'Set SKRILL_PAY_TO_EMAIL or SKRILL_MERCHANT_ID (+ SKRILL_WEBHOOK_SECRET). Copy instructions; Owner confirm or webhook.'},
    {key:'manual', name:'Manual / bank', hint:'Always available. Quote invoice reference on transfer.'},
  ];
  el.innerHTML=cards.map(c=>{
    const p=prov[c.key]||{};
    const ok=!!p.configured;
    return `<div class="prov-card"><strong>${esc(c.name)} · ${ok?'ready':'not configured'}</strong>
      <div class="sub">${esc(c.hint)}</div>
      <div class="sub">Auto pay link: ${p.auto_link?'yes':'no'} · Auto paid webhook: ${p.auto_paid_webhook?'yes':'no'}${p.mode?' · mode '+esc(p.mode):''}${p.note?' · '+esc(p.note):''}</div>
    </div>`;
  }).join('');
}

async function loadMoney(){
  await loadPaymentSettings().catch(()=>{});
  const [prov, inv, pay, dash]=await Promise.all([
    api('/api/sales/providers'),
    api('/api/sales/invoices'),
    api('/api/sales/payments'),
    api('/api/sales/dashboard?product_line=STANDALONE'),
  ]);
  $('testModeBanner').classList.toggle('hidden', !prov.test_mode_banner);
  renderProvidersSetup(prov);
  const collected=(dash.collected&&dash.collected.by_currency)||{};
  const outstanding=(dash.invoiced_outstanding&&dash.invoiced_outstanding.by_currency)||{};
  const fmt=o=>Object.keys(o).length?Object.entries(o).map(([c,v])=>fmtMoney(v,c)).join(' · '):'—';
  const payN=(pay.payments||[]).length;
  if($('moneyOrigin')){
    $('moneyOrigin').innerHTML=`<strong>Where money comes from</strong><br>
      Collected = succeeded payments − refunds in the Payments ledger below (n=${payN}). 
      Proposals and open invoices are <em>not</em> revenue. Confirm paid under Deals or here; refunds reduce collected.`;
  }
  $('moneyStats').innerHTML=`
    <button type="button" class="queue-card" data-goto="money:ledger"><span>Collected</span><strong style="font-size:15px">${fmt(collected)}</strong><span class="kpi-hint">→ Ledger</span></button>
    <button type="button" class="queue-card" data-goto="money:invoices"><span>Invoiced outstanding</span><strong style="font-size:15px">${fmt(outstanding)}</strong><span class="kpi-hint">→ Invoices &amp; QR</span></button>
    <button type="button" class="queue-card" data-goto="money:settings"><span>Stripe</span><strong style="font-size:14px">${prov.stripe&&prov.stripe.configured?'configured':'not set'}</strong><span class="kpi-hint">→ Settings</span></button>
    <button type="button" class="queue-card" data-goto="paySetup"><span>PayPal / Payoneer / Skrill</span><strong style="font-size:13px">${[prov.paypal&&prov.paypal.configured&&'PayPal',prov.payoneer&&prov.payoneer.configured&&'Payoneer',prov.skrill&&prov.skrill.configured&&'Skrill'].filter(Boolean).join(', ')||'manual only'}</strong><span class="kpi-hint">→ Pay setup</span></button>`;
  bindDashKpis($('moneyStats'));
  const invoices=inv.invoices||[];
  B.moneyInvoicesCache=invoices;
  if(pagerState.moneyInvoices) pagerState.moneyInvoices.page=1;
  renderMoneyInvoices();
  const payments=pay.payments||[];
  const pt=$('paymentsTable').querySelector('tbody');
  const payPager=ensurePagerHost($('paymentsTable').closest('.sales-table-wrap'),'moneyPaymentsPager');
  const paySlice=pageSlice(payments,'moneyPayments');
  if(!payments.length){
    pt.innerHTML='<tr><td colspan="6" data-label=""><div class="empty-card">No payments yet. Confirmed invoices appear here.</div></td></tr>';
    if(payPager) payPager.innerHTML='';
  }else{
    pt.innerHTML=paySlice.items.map(p=>`<tr>
      <td data-label="ID">${p.id}</td>
      <td data-label="Amount">${esc(fmtMoney(p.amount,p.currency))}${p.is_test_mode?' <span class="hint">test</span>':''}</td>
      <td data-label="Status"><span class="status-pill ${esc(p.status)}">${esc(p.status)}</span></td>
      <td data-label="Provider">${esc(p.provider)}</td>
      <td data-label="Paid at">${esc(fmtDateTime(p.paid_at))}</td>
      <td data-label="Actions">${p.status==='succeeded'?`<button type="button" class="danger" data-refund="${p.id}" data-amount="${esc(String(p.amount))}" data-currency="${esc(p.currency||'')}">Refund</button>`:'—'}</td>
    </tr>`).join('');
    renderPager(payPager, paySlice, ()=>loadMoney());
  }
  pt.querySelectorAll('[data-refund]').forEach(btn=>btn.addEventListener('click',async()=>{
    const form=await uiForm({
      title:'Record refund',
      message:`Original ${btn.dataset.currency} ${btn.dataset.amount}. Leave amount blank for a full refund. Owner only.`,
      confirmLabel:'Refund',
      danger:true,
      fields:[
        {name:'amount', label:'Amount (blank = full)', type:'number', placeholder:btn.dataset.amount||''},
        {name:'reason', label:'Reason', type:'textarea', rows:2, placeholder:'Customer request, goodwill, chargeback…'}
      ]
    });
    if(!form) return;
    const body={reason:form.reason||''};
    if(String(form.amount??'').trim()!=='' && form.amount!==undefined && form.amount!==null && !Number.isNaN(Number(form.amount))) body.amount=Number(form.amount);
    try{
      await api('/api/sales/payments/'+btn.dataset.refund+'/refund',{method:'POST',body});
      toast('Refund recorded'); await loadMoney();
    }catch(e){ toast(e.message,true); }
  }));
}

/* contact queue state: panels/contact-queue.js + core/state.js */
/* brand logo + email signature: features/email-signature.js */
















































async function createProposalInvoiceForProspect(businessId, {provider='manual', title='', productLine='', quiet=false, planId=null}={}){
  const wantProvider=(provider||'manual').toLowerCase();
  let line=(productLine||'').toUpperCase();
  let currency='USD';
  if(!line){
    try{
      const lead=await api('/api/sales/prospects/'+businessId);
      line=(lead.product_line||'STANDALONE').toUpperCase();
      currency=(lead.currency||'USD').toUpperCase();
    }catch(e){ line='STANDALONE'; }
  }else{
    try{
      const lead=await api('/api/sales/prospects/'+businessId);
      currency=(lead.currency||'USD').toUpperCase();
    }catch(e){}
  }
  // Reuse open invoice for same lead+provider (avoids spam on one-click create)
  try{
    const existingInv=await api('/api/sales/invoices?business_id='+encodeURIComponent(businessId));
    const hit=(existingInv.invoices||[]).find(i=>
      String(i.provider||'').toLowerCase()===wantProvider && (i.status==='open'||i.status==='draft')
    );
    if(hit){
      return {prop:{id:hit.proposal_id, reference:hit.number}, inv:hit, reused:true};
    }
  }catch(e){}
  // Reuse latest open/sent/accepted proposal when quiet one-click
  let reusedProp=null;
  if(quiet){
    try{
      const props=await api('/api/sales/proposals?business_id='+encodeURIComponent(businessId));
      reusedProp=(props.proposals||[]).find(p=>['draft','sent','accepted'].includes(String(p.status||'')));
    }catch(e){}
  }
  const plans=await api('/api/sales/plans?product_line='+encodeURIComponent(line));
  const list=plans.plans||[];
  const growth=list.find(p=>(p.code||'').toLowerCase().includes('growth'))||list[0];
  if(!growth) throw new Error('No plans configured for '+line);
  let form={plan_id:String(planId||growth.id), provider:wantProvider, title:title||''};
  if(!quiet){
    form=await uiForm({
      title:'Proposal + pay link',
      message:reusedProp
        ?('Will attach invoice to existing proposal '+reusedProp.reference+' when possible. Hosted pay URL appears when Stripe/PayPal is configured.')
        :('Creates a proposal and invoice. Hosted pay URL appears when Stripe/PayPal is configured.'),
      confirmLabel:'Create',
      fields:[
        {name:'plan_id', label:'Plan', type:'select', required:true, value:String(planId||growth.id), options:list.map(p=>({value:String(p.id), label:p.name||p.code}))},
        {name:'provider', label:'Invoice provider', type:'select', value:wantProvider, options:[
          {value:'manual',label:'Manual / bank'},{value:'stripe',label:'Stripe'},{value:'paypal',label:'PayPal'},
          {value:'payoneer',label:'Payoneer'},{value:'skrill',label:'Skrill'}
        ]},
        {name:'title', label:'Title', value:title||'', placeholder:'Optional custom title'}
      ]
    });
    if(!form) return null;
  }
  let prop=reusedProp;
  if(!prop){
    const plan=list.find(p=>String(p.id)===String(form.plan_id))||growth;
    prop=await api('/api/sales/proposals',{method:'POST',body:{
      business_id:Number(businessId),
      title:form.title||(plan.name+' proposal'),
      currency:currency,
      plan_id:Number(form.plan_id),
      tax_rate:0,
      tax_label:'',
      terms_url:''
    }});
  }
  if(prop.status==='draft' || !prop.status){
    try{ await api('/api/sales/proposals/'+prop.id+'/send',{method:'POST',body:{}}); prop.status='sent'; }catch(e){}
  }
  const inv=await api('/api/sales/invoices',{method:'POST',body:{proposal_id:prop.id, provider:form.provider||wantProvider}});
  return {prop, inv, reused:!!reusedProp};
}

async function sendPayLinkFlow(){
  const bid=await pickProspectField('Lead for pay link');
  if(bid==null) return;
  try{
    try{
      const conn=await api('/api/sales/be/connection');
      if(conn&&conn.connected){
        toast('AppointHive connected — open the prospect and use Send to delivery', true);
        await openProspectById(bid);
        drawerTab='appointhive'; renderDrawer();
        return;
      }
    }catch(e){}
    const res=await createProposalInvoiceForProspect(bid);
    if(!res) return;
    const url=res.inv&&res.inv.hosted_url;
    const number=(res.inv&&res.inv.number)||'';
    const live=url && !String(url).includes('/placeholder/');
    if(live){ try{ await navigator.clipboard.writeText(url); }catch(e){} }
    const channelForm=await uiForm({
      title:'Send pay link to lead',
      message: live
        ?('Hosted pay link ready for '+number+'. We will put it in compose for you to copy/paste.')
        :('Invoice '+number+' uses copyable pay instructions (Payoneer/Skrill/manual). Paste to the lead, then Confirm paid when cash lands.'),
      confirmLabel:'Open compose to copy',
      fields:[{name:'channel', label:'Channel', type:'select', value:'email', options:[
        {value:'email',label:'Email'},{value:'whatsapp',label:'WhatsApp'},{value:'instagram',label:'Instagram'}
      ]}]
    });
    await openProspectById(bid);
    if(drawerLead && channelForm){
      const instr=(res.inv&&res.inv.pay_instructions)||'';
      const payMsg=live
        ?`Hi {{name}}, here is your AppointHive payment link for invoice ${number}:\n${url}\n\nQuestions? Just reply.`
        :(instr
          ?`Hi {{name}},\n\nPlease pay invoice ${number} using these instructions:\n\n${instr}\n\nThanks.`
          :`Hi {{name}}, your AppointHive invoice ${number} is ready. Reply and we will send payment instructions.`);
      openCompose(drawerLead, channelForm.channel||'email', {allowUnready:true});
      requestAnimationFrame(()=>{
        if($('composeBody')) $('composeBody').value=payMsg.replace(/\{\{name\}\}/g, drawerLead.name||'there');
        copyComposeMessage().catch(()=>{});
      });
      toast('Message ready — paste to lead, then Confirm sent');
    }else{
      toast(live?'Pay link copied':'Invoice '+number+' created');
    }
    if(appPanel==='money') await loadMoney();
    if(appPanel==='deals') await loadDeals();
  }catch(e){ toast(e.message,true); }
}

function paylinksStore(){
  try{ return JSON.parse(localStorage.getItem('ah_paylinks')||'[]'); }catch(e){ return []; }
}
function savePaylinksStore(rows){
  localStorage.setItem('ah_paylinks', JSON.stringify(rows||[]));
}
function qrUrlFor(data){
  const payload=encodeURIComponent(data||'');
  // Primary: QR Server; fallback SVG data URI when offline is handled by onerror on <img>
  return 'https://api.qrserver.com/v1/create-qr-code/?size=180x180&data='+payload;
}
function paylinkPayload(r){
  return (r.url&&!String(r.url).includes('/placeholder/')?r.url:'')||(r.pay_instructions||'')||r.url||'';
}
function mergePaylinkRows(invoiceRows){
  const local=paylinksStore();
  const byInv=new Map(local.filter(x=>x.invoice_id).map(x=>[String(x.invoice_id),x]));
  const fromInv=(invoiceRows||[]).map(i=>{
    const live=i.hosted_url&&!String(i.hosted_url).includes('/placeholder/');
    const url=live?i.hosted_url:(i.pay_instructions||i.hosted_url||'');
    const prev=byInv.get(String(i.id))||{};
    return {
      id: prev.id||('inv-'+i.id),
      invoice_id:i.id,
      number:i.number,
      business_id:i.business_id,
      prospect_name:prev.prospect_name||i.business_name||('Invoice '+i.number),
      business_name:i.business_name||prev.prospect_name||'',
      provider:i.provider||'manual',
      url,
      pay_instructions:i.pay_instructions||'',
      status:i.status,
      total:i.total,
      currency:i.currency,
      created_at:prev.created_at||'',
      source:'invoice'
    };
  }).filter(r=>paylinkPayload(r));
  const orphans=local.filter(x=>!x.invoice_id);
  return fromInv.concat(orphans);
}
async function renderPaylinksHost(opts={}){
  const hostId=opts.hostId||'paylinksHost';
  const businessId=opts.businessId!=null?opts.businessId:null;
  const host=$(hostId);
  if(!host) return;
  host.innerHTML='<div class="hint">Loading pay links…</div>';
  let invoices=[];
  try{
    const qs=businessId!=null?('?business_id='+encodeURIComponent(businessId)):'';
    const data=await api('/api/sales/invoices'+qs);
    invoices=data.invoices||[];
  }catch(e){
    host.innerHTML=`<div class="empty-card">${esc(e.message||'Could not load invoices')}</div>`;
    return;
  }
  let rows=mergePaylinkRows(invoices);
  if(businessId==null) savePaylinksStore(rows);
  if(businessId!=null) rows=rows.filter(r=>String(r.business_id)===String(businessId));
  if(!rows.length){
    host.innerHTML=businessId!=null
      ?'<div class="empty-card">No pay links for this lead yet. Create one per provider above.</div>'
      :'<div class="empty-card">No pay links yet. Create real invoices per provider — hosted URLs and QR codes appear here.</div>';
    return;
  }
  host.innerHTML=rows.map(r=>{
    const payload=paylinkPayload(r);
    const live=r.url&&!String(r.url).includes('/placeholder/')&&!String(r.url).startsWith('Pay ')&&String(r.url).startsWith('http');
    const invId=r.invoice_id||(String(r.id||'').startsWith('inv-')?String(r.id).slice(4):'');
    return `
    <div class="paylink-card" data-pl-id="${esc(r.id)}">
      <img class="paylink-qr" src="${esc(qrUrlFor(payload))}" alt="QR for ${esc(r.provider)}" loading="lazy">
      <div class="paylink-meta">
        <strong>${esc(r.provider||'manual')}</strong>${r.number?` · <span class="sub">${esc(r.number)}</span>`:''}
        <span class="status-pill ${esc(r.status||'')}">${esc(r.status||'')}</span>
        <div class="sub">${esc(r.prospect_name||r.business_name||('#'+r.business_id))} · ${esc(r.currency||'')} ${r.total!=null?esc(String(r.total)):''}</div>
        <div class="paylink-url">${esc(payload)}</div>
        <div class="drawer-actions" style="margin-top:8px">
          ${live?`<button type="button" class="primary" data-pl-open="${esc(r.id)}">Open</button>`:''}
          <button type="button" data-pl-copy="${esc(r.id)}">Copy</button>
          <a class="chip ok" href="${esc(qrUrlFor(payload))}" download="paylink-${esc(r.provider||'qr')}.png" target="_blank" rel="noopener">Download QR</a>
          <button type="button" data-pl-print="${esc(r.id)}">Print QR</button>
          ${invId?`<button type="button" data-inv-view="${esc(invId)}">View invoice</button>
          <a class="chip ok" href="/api/sales/invoices/${esc(invId)}/pdf" target="_blank" rel="noopener">PDF</a>`:''}
          ${invId&&(r.status==='open'||r.status==='draft')?`<button type="button" class="danger" data-inv-void="${esc(invId)}" data-number="${esc(r.number||'')}">Void</button>`:''}
          ${r.source==='local'?`<button type="button" class="danger" data-pl-del="${esc(r.id)}">Delete</button>`:''}
        </div>
      </div>
    </div>`;
  }).join('');
  const storeRows=()=> {
    const all=paylinksStore();
    return rows.map(r=>all.find(x=>String(x.id)===String(r.id))||r);
  };
  host.querySelectorAll('[data-pl-open]').forEach(btn=>btn.addEventListener('click',()=>{
    const row=storeRows().find(x=>String(x.id)===String(btn.dataset.plOpen))||rows.find(x=>String(x.id)===String(btn.dataset.plOpen));
    const url=row&&paylinkPayload(row);
    if(url&&String(url).startsWith('http')) window.open(url,'_blank','noopener');
  }));
  host.querySelectorAll('[data-pl-copy]').forEach(btn=>btn.addEventListener('click',async()=>{
    const row=storeRows().find(x=>String(x.id)===String(btn.dataset.plCopy))||rows.find(x=>String(x.id)===String(btn.dataset.plCopy));
    if(!row) return;
    try{ await navigator.clipboard.writeText(paylinkPayload(row)); toast('Copied'); }catch(e){ toast('Copy failed',true); }
  }));
  host.querySelectorAll('[data-pl-print]').forEach(btn=>btn.addEventListener('click',()=>{
    const row=storeRows().find(x=>String(x.id)===String(btn.dataset.plPrint))||rows.find(x=>String(x.id)===String(btn.dataset.plPrint));
    if(!row) return;
    const payload=paylinkPayload(row);
    const w=window.open('','_blank','noopener,width=420,height=520');
    if(!w){ toast('Pop-up blocked',true); return; }
    w.document.write(`<!doctype html><title>QR</title><body style="font-family:sans-serif;text-align:center;padding:24px"><h2>${esc(row.provider)} · ${esc(row.number||'pay link')}</h2><img src="${esc(qrUrlFor(payload))}" width="220" height="220"><p style="word-break:break-all">${esc(payload)}</p>`+'<scr'+'ipt>window.onload=()=>window.print()</scr'+'ipt></body>');
    w.document.close();
  }));
  host.querySelectorAll('[data-pl-del]').forEach(btn=>btn.addEventListener('click',async()=>{
    const ok=await uiConfirm({title:'Delete local pay link?', confirmLabel:'Delete', danger:true});
    if(!ok) return;
    savePaylinksStore(paylinksStore().filter(x=>String(x.id)!==String(btn.dataset.plDel)));
    renderPaylinksHost(opts);
  }));
  bindInvoiceCardActions(host, invoices.map(i=>({...i, business_name:i.business_name})), {
    onPaid:async()=>{ await renderPaylinksHost(opts); if(appPanel==='money') await loadMoney(); },
    onVoided:async()=>{ await renderPaylinksHost(opts); if(appPanel==='money') await loadMoney(); }
  });
}
async function refreshDrawerPaylinks(){
  if(!drawerLead||!$('drawerPaylinksHost')) return;
  await renderPaylinksHost({hostId:'drawerPaylinksHost', businessId:drawerLead.id});
}
async function createDrawerPaylinkForProvider(provider){
  if(!drawerLead) return;
  const bid=drawerLead.id;
  const prospectName=drawerLead.name||('#'+bid);
  const productLine=(drawerLead.product_line||'STANDALONE').toUpperCase();
  let ahConnected=false;
  try{
    const conn=await api('/api/sales/be/connection');
    ahConnected=!!(conn&&conn.connected);
    setBeConnCache(conn);
  }catch(e){}
  if(ahConnected && productLine==='STANDALONE' && (provider==='stripe'||provider==='paypal')){
    toast('AppointHive connected — use Delivery / pay tab for Stripe/PayPal card checkout', true);
    drawerTab='appointhive'; renderDrawer();
    return;
  }
  try{
      const res=await createProposalInvoiceForProspect(bid,{
      provider,
      title:prospectName+' · '+provider,
      productLine,
      quiet:true
    });
    if(!res) return;
    const inv=res.inv||{};
    const rows=paylinksStore();
    rows.unshift({
      id:'inv-'+(inv.id||Date.now()),
      invoice_id:inv.id,
      number:inv.number,
      business_id:bid,
      prospect_name:prospectName,
      business_name:prospectName,
      provider:inv.provider||provider,
      url:inv.hosted_url||'',
      pay_instructions:inv.pay_instructions||'',
      status:inv.status,
      total:inv.total,
      currency:inv.currency,
      created_at:new Date().toISOString(),
      source:'invoice'
    });
    savePaylinksStore(rows);
    toast(res.reused
      ?((inv.provider||provider)+' pay link already open — showing existing')
      :((inv.provider||provider)+' pay link ready'));
    await refreshDrawerPaylinks();
    if(dealsTab==='paylinks') renderPaylinksHost().catch(()=>{});
    if(appPanel==='money') loadMoney().catch(()=>{});
  }catch(e){ toast(e.message||'Could not create pay link', true); }
}
async function createPaylinksFlow(){
  const bid=await pickProspectField('Prospect for pay links');
  if(bid==null) return;
  let prospectName='#'+bid;
  let productLine='STANDALONE';
  try{
    const p=await api('/api/sales/prospects/'+bid);
    prospectName=p.name||prospectName;
    productLine=(p.product_line||'STANDALONE').toUpperCase();
  }catch(e){}
  let ahConnected=false;
  try{
    const conn=await api('/api/sales/be/connection');
    ahConnected=!!(conn&&conn.connected);
    setBeConnCache(conn);
  }catch(e){}
  const defaultProviders=ahConnected&&productLine==='STANDALONE'?'manual,payoneer,skrill':'manual,stripe,paypal';
  const form=await uiForm({
    title:'Create pay links + QR',
    message:ahConnected&&productLine==='STANDALONE'
      ?('AppointHive is connected — Stripe/PayPal product checkout stays on AppointHive. Creating Manual/Payoneer/Skrill invoices for '+prospectName+'.')
      :('Creates a real proposal + invoice per provider for '+prospectName+'. Hosted pay URLs and QR codes appear in this tab.'),
    confirmLabel:'Create invoices',
    fields:[
      {name:'providers', label:'Providers (comma)', value:defaultProviders, placeholder:'manual,stripe,paypal,payoneer,skrill'}
    ]
  });
  if(!form) return;
  const providers=String(form.providers||'').split(',').map(s=>s.trim().toLowerCase()).filter(Boolean);
  if(!providers.length){ toast('Pick at least one provider',true); return; }
  let ok=0, fail=0;
  for(const provider of providers){
    try{
      if(ahConnected && productLine==='STANDALONE' && (provider==='stripe'||provider==='paypal')){
        fail++; continue;
      }
      const res=await createProposalInvoiceForProspect(bid,{provider, title:prospectName+' · '+provider, productLine});
      if(!res){ fail++; continue; }
      const inv=res.inv||{};
      const rows=paylinksStore();
      rows.unshift({
        id:'inv-'+(inv.id||Date.now()),
        invoice_id:inv.id,
        number:inv.number,
        business_id:bid,
        prospect_name:prospectName,
        provider:inv.provider||provider,
        url:inv.hosted_url||'',
        pay_instructions:inv.pay_instructions||'',
        status:inv.status,
        total:inv.total,
        currency:inv.currency,
        created_at:new Date().toISOString(),
        source:'invoice'
      });
      savePaylinksStore(rows);
      ok++;
    }catch(e){ fail++; toast((provider+': '+(e.message||'failed')).slice(0,120), true); }
  }
  dealsTab='paylinks';
  document.querySelectorAll('#dealsTabs [data-deals-tab]').forEach(b=>b.classList.toggle('active',b.dataset.dealsTab===dealsTab));
  if($('dealsProposalsPane')) $('dealsProposalsPane').classList.add('hidden');
  if($('dealsPaylinksPane')) $('dealsPaylinksPane').classList.remove('hidden');
  await renderPaylinksHost();
  if(appPanel==='deals') try{ await loadDeals(); }catch(e){}
  toast(ok?`${ok} pay link invoice(s) ready`+(fail?` · ${fail} skipped`:'') : 'No invoices created', !ok);
}
async function loadDeals(){
  const [props, inv, dash]=await Promise.all([
    api('/api/sales/proposals'),
    api('/api/sales/invoices'),
    api('/api/sales/dashboard?product_line=STANDALONE'),
  ]);
  const collected=(dash.collected&&dash.collected.by_currency)||{};
  const outstanding=(dash.invoiced_outstanding&&dash.invoiced_outstanding.by_currency)||{};
  const fmt=o=>Object.keys(o).length?Object.entries(o).map(([c,v])=>`${c} ${Number(v).toFixed(0)}`).join(' · '):'—';
  if($('dealsStats')){
    $('dealsStats').innerHTML=`
      <button type="button" class="queue-card" data-goto="money:ledger"><span>Collected</span><strong style="font-size:15px">${fmt(collected)}</strong><span class="kpi-hint">→ Ledger</span></button>
      <button type="button" class="queue-card" data-goto="money:invoices"><span>Outstanding</span><strong style="font-size:15px">${fmt(outstanding)}</strong><span class="kpi-hint">→ Invoices &amp; QR</span></button>
      <div class="queue-card"><span>Proposals</span><strong>${(props.proposals||[]).length}</strong></div>
      <div class="queue-card"><span>Open invoices</span><strong>${(inv.invoices||[]).filter(i=>i.status==='open').length}</strong></div>`;
    bindDashKpis($('dealsStats'));
  }
  const ptb=$('dealsProposalsTable')&&$('dealsProposalsTable').querySelector('tbody');
  if(ptb){
    const rows=props.proposals||[];
    const propPager=ensurePagerHost($('dealsProposalsTable').closest('.sales-table-wrap'),'dealsProposalsPager');
    const pSlice=pageSlice(rows,'dealsProposals');
    ptb.innerHTML=rows.length?pSlice.items.map(p=>`<tr>
      <td data-label="Ref"><strong>${esc(p.reference)}</strong></td>
      <td data-label="Prospect"><button type="button" data-open-prospect="${p.business_id}">#${p.business_id}</button></td>
      <td data-label="Status"><span class="status-pill ${esc(p.status)}">${esc(p.status)}</span></td>
      <td data-label="Total">${esc(p.currency)} ${esc(String(p.total))}</td>
      <td data-label="Actions"><div class="action-stack">
        ${p.status!=='sent'&&p.status!=='accepted'?`<button type="button" data-send-prop="${p.id}">Mark sent</button>`:''}
        <button type="button" class="primary" data-inv-prop="${p.id}" data-bid="${p.business_id}">Create pay link</button>
      </div></td>
    </tr>`).join(''):'<tr><td colspan="5" data-label=""><div class="empty-card">No proposals yet. Use New proposal + pay link.</div></td></tr>';
    renderPager(propPager, pSlice, ()=>loadDeals());
    ptb.querySelectorAll('[data-open-prospect]').forEach(btn=>btn.addEventListener('click',()=>openProspectById(btn.dataset.openProspect)));
    ptb.querySelectorAll('[data-send-prop]').forEach(btn=>btn.addEventListener('click',async()=>{
      await api('/api/sales/proposals/'+btn.dataset.sendProp+'/send',{method:'POST',body:{}});
      toast('Proposal marked sent · stage auto-advanced'); await loadDeals();
    }));
    ptb.querySelectorAll('[data-inv-prop]').forEach(btn=>btn.addEventListener('click',async()=>{
      try{
        const conn=await api('/api/sales/be/connection');
        if(conn&&conn.connected){
          toast('AppointHive connected — open the prospect and use Send to delivery', true);
          const bid=Number(btn.dataset.bid);
          if(bid){ await openProspectById(bid); drawerTab='sales'; renderDrawer(); }
          return;
        }
      }catch(e){}
      const form=await uiForm({
        title:'Create invoice / pay link',
        confirmLabel:'Create',
        fields:[{name:'provider', label:'Provider', type:'select', value:'manual', options:[
          {value:'manual',label:'Manual'},{value:'stripe',label:'Stripe'},{value:'paypal',label:'PayPal'},
          {value:'payoneer',label:'Payoneer'},{value:'skrill',label:'Skrill'}
        ]}]
      });
      if(!form) return;
      try{
        const invRes=await api('/api/sales/invoices',{method:'POST',body:{proposal_id:Number(btn.dataset.invProp), provider:form.provider}});
        if(invRes.hosted_url && !String(invRes.hosted_url).includes('/placeholder/')){
          try{ await navigator.clipboard.writeText(invRes.hosted_url); }catch(e){}
          toast('Pay link ready'); window.open(invRes.hosted_url,'_blank','noopener');
        }else toast('Invoice '+invRes.number+' created');
        await loadDeals();
      }catch(e){ toast(e.message,true); }
    }));
  }
  const itb=$('dealsInvoicesTable')&&$('dealsInvoicesTable').querySelector('tbody');
  if(itb){
    const invoices=inv.invoices||[];
    const invPager=ensurePagerHost($('dealsInvoicesTable').closest('.sales-table-wrap'),'dealsInvoicesPager');
    const iSlice=pageSlice(invoices,'dealsInvoices');
    itb.innerHTML=invoices.length?iSlice.items.map(i=>{
      const liveLink=i.hosted_url&&!String(i.hosted_url).includes('/placeholder/');
      const link=liveLink?`<a class="chip ok" href="${esc(i.hosted_url)}" target="_blank" rel="noopener">Pay link</a>`:'';
      const copyPay=(i.pay_instructions&&['payoneer','skrill','manual'].includes(String(i.provider||'').toLowerCase()))
        ?`<button type="button" data-deal-copy-inv="${i.id}">Copy pay instructions</button>`:'';
      const actions=i.status==='open'
        ?`<div class="action-stack">${link}${copyPay}<button type="button" class="primary" data-confirm-close="${i.id}" data-number="${esc(i.number)}" data-total="${esc(String(i.total))}" data-currency="${esc(i.currency||'')}">Confirm close / paid</button></div>`
        :'—';
      return `<tr>
        <td data-label="Number"><strong>${esc(i.number)}</strong></td>
        <td data-label="Status">${esc(i.status)}</td>
        <td data-label="Provider">${esc(i.provider)}</td>
        <td data-label="Total">${esc(i.currency)} ${esc(String(i.total))}</td>
        <td data-label="Actions">${actions}</td>
      </tr>`;
    }).join(''):'<tr><td colspan="5" data-label=""><div class="empty-card">No invoices yet.</div></td></tr>';
    renderPager(invPager, iSlice, ()=>loadDeals());
    const dealInvById=Object.fromEntries(invoices.map(i=>[String(i.id), i]));
    itb.querySelectorAll('[data-deal-copy-inv]').forEach(btn=>btn.addEventListener('click',async()=>{
      const text=(dealInvById[btn.dataset.dealCopyInv]||{}).pay_instructions||'';
      try{ await navigator.clipboard.writeText(text); toast('Pay instructions copied'); }
      catch(e){ toast('Could not copy',true); }
    }));
    itb.querySelectorAll('[data-confirm-close]').forEach(btn=>btn.addEventListener('click',async()=>{
      const form=await uiForm({
        title:'Confirm deal closed / paid',
        message:`Separate from communication. Record cash for ${btn.dataset.number} (${btn.dataset.currency} ${btn.dataset.total}).`,
        confirmLabel:'Confirm paid',
        fields:[{name:'provider_ref', label:'Payment reference', placeholder:'Bank / Stripe / PayPal id'}]
      });
      if(!form) return;
      try{
        await api('/api/sales/invoices/'+btn.dataset.confirmClose+'/confirm-paid',{method:'POST',body:{provider_ref:form.provider_ref||''}});
        toast('Deal payment recorded'); await loadDeals();
      }catch(e){ toast(e.message,true); }
    }));
  }
}

async function addUserFlow(){
  const form=await uiForm({
    title:'Add team user',
    message:'Owner only. Set a password (≥8 chars) so they can sign in with their email.',
    confirmLabel:'Add user',
    fields:[
      {name:'email', label:'Email', type:'email', required:true, placeholder:'rep@appointhive.com'},
      {name:'name', label:'Name', required:true, placeholder:'Alex Sales'},
      {name:'role', label:'Role', type:'select', value:'sales', options:[
        {value:'owner',label:'Owner'},{value:'sales',label:'Sales'},{value:'viewer',label:'Viewer'}
      ]},
      {name:'password', label:'Password', type:'password', required:true, placeholder:'min 8 characters'},
      {name:'timezone', label:'Timezone', value:'Australia/Sydney'}
    ]
  });
  if(!form) return;
  try{
    await api('/api/sales/users',{method:'POST',body:form});
    toast('User added — they can log in with their email'); await loadSettings();
  }catch(e){ toast(e.message,true); }
}

async function addTerritoryFlow(){
  const users=(await api('/api/sales/users')).users||[];
  const form=await uiForm({
    title:'Add territory',
    confirmLabel:'Create',
    fields:[
      {name:'name', label:'Name', required:true, placeholder:'Sydney metro'},
      {name:'country', label:'Country', required:true, placeholder:'Australia'},
      {name:'region', label:'Region', placeholder:'NSW'},
      {name:'owner_id', label:'Owner user', type:'select', options:[{value:'',label:'— none —'},...users.map(u=>({value:String(u.id),label:u.email+' ('+u.role+')'}))]}
    ]
  });
  if(!form) return;
  const body={name:form.name,country:form.country,region:form.region||'',active:true};
  if(form.owner_id) body.owner_id=Number(form.owner_id);
  await api('/api/sales/territories',{method:'POST',body});
  toast('Territory created'); await loadSettings();
}




function updateViewHint(){
  if(viewMode==='queue'){$('viewHint').textContent='Global queue: marked · not contacted across all cities.';$('statTotalLabel').textContent='Shown'}
  else if(viewMode==='due'){$('viewHint').textContent='Due / overdue follow-ups across all cities.';$('statTotalLabel').textContent='Shown'}
  else{$('viewHint').textContent='Fresh Discover: not To contact, not Contacted, not Has booking. Unmark returns them here.';$('statTotalLabel').textContent='Fresh'}
}
function channelLabel(ch){return composeChannelLabel(ch)}
function firstEmail(b){return composeFirstEmail(b)}
function phoneDigits(b){return composePhoneDigits(b)}
function contactChannels(b){return composeContactChannels(b)}
function siteLabel(url){try{return new URL(url).hostname.replace(/^www\./,'')}catch(e){return url}}
function instagramHandle(url){ return composeInstagramHandle(url); }
function isMobileClient(){
  const ua=navigator.userAgent||'';
  if(/Android|iPhone|iPad|iPod|Mobile|IEMobile|Opera Mini/i.test(ua)) return true;
  try{ if(navigator.maxTouchPoints>1 && window.matchMedia('(max-width:920px)').matches) return true; }catch(e){}
  return false;
}
function isAndroidClient(){ return /Android/i.test(navigator.userAgent||''); }
function isIOSClient(){ return /iPhone|iPad|iPod/i.test(navigator.userAgent||''); }
function normalizeEmailBodyBreaks(body){
  // Keep real paragraph breaks for Zoho / mailto (avoid one giant paragraph)
  return String(body||'').replace(/\r\n/g,'\n').replace(/\r/g,'\n').replace(/\n/g,'\r\n');
}
async function copyEmailHtmlSignature(plainBody){
  const abs=brandLogoAbsoluteUrl();
  // Clipboard HTML needs a logo image OR any signature text
  if(!navigator.clipboard || !window.ClipboardItem) return false;
  if(!abs && !(emailSignaturePlain()||'').trim()) return false;
  const html=withEmailSignature(plainBody,{html:true});
  const plain=normalizeEmailBodyBreaks(withEmailSignature(plainBody));
  try{
    await navigator.clipboard.write([new ClipboardItem({
      'text/html': new Blob([html],{type:'text/html'}),
      'text/plain': new Blob([plain],{type:'text/plain'})
    })]);
    return true;
  }catch(e){ return false; }
}
function zohoComposeUrl(to,subject,body){
  // Plain body with CRLF for Zoho mailto bridge; HTML logo also copied to clipboard by openChannelApp
  const clean=normalizeEmailBodyBreaks(withEmailSignature(body));
  const ct=`mailto:${to}?subject=${encodeURIComponent(subject||'')}&body=${encodeURIComponent(clean)}`;
  return `https://mail.zoho.com/mail/compose.do?extsrc=mailto&mode=compose&ct=${encodeURIComponent(ct)}`;
}
function mailtoComposeUrl(to,subject,body){
  const clean=normalizeEmailBodyBreaks(withEmailSignature(body));
  return `mailto:${to}?subject=${encodeURIComponent(subject||'')}&body=${encodeURIComponent(clean)}`;
}
function whatsappWebUrl(phone,body){
  return `https://web.whatsapp.com/send?phone=${encodeURIComponent(phone)}&text=${encodeURIComponent(body||'')}`;
}
function whatsappAppUrl(phone,body){
  const text=encodeURIComponent(body||'');
  const waMe=`https://wa.me/${phone}?text=${text}`;
  // Prefer WhatsApp Business on Android when installed; falls back to wa.me / personal WA
  if(isAndroidClient()){
    return `intent://send?phone=${phone}&text=${text}#Intent;scheme=whatsapp;package=com.whatsapp.w4b;S.browser_fallback_url=${encodeURIComponent(waMe)};end`;
  }
  // iOS / other: wa.me hands off to WhatsApp or WhatsApp Business (system chooser)
  return waMe;
}
function instagramAppUrl(profileUrl, handle, businessName){
  // Profile deep-link (IG app) / www.instagram.com/{user}/ (browser session) — never accounts/login
  return instagramOpenUrls(profileUrl, handle, { mobile: isMobileClient(), businessName: businessName||'' });
}
function openInstagramProfile(profileUrl, handle, businessName){
  const links=instagramAppUrl(profileUrl, handle, businessName);
  // Never launch bare @slug / relative paths — browser resolves those against localhost
  const web=links.web && /^https:\/\/(www\.)?instagram\.com\//i.test(links.web) ? links.web : '';
  const app=links.app && /^instagram:\/\//i.test(links.app) ? links.app : '';
  if(!web && !app){
    toast('Instagram profile URL missing — set @handle on the lead', true);
    return;
  }
  if(app) launchAppWithFallback(app, web, 800);
  else launchExternal(web);
}
/** Open WhatsApp chat (empty body OK for To-contact prep). Web on desktop, app on mobile. */
function openWhatsAppChat(phone, body){
  const digits=String(phone||'').replace(/\D/g,'');
  if(digits.length<8){
    toast('No valid phone for WhatsApp', true);
    return;
  }
  if(isMobileClient()) launchExternal(whatsappAppUrl(digits, body||''));
  else launchExternal(whatsappWebUrl(digits, body||''));
}
function launchExternal(url){
  if(!url) return;
  const raw=String(url).trim();
  // Bare @handle must never become a.href — browser resolves it as http://localhost/@handle
  if(raw.startsWith('@')){
    const fixed=instagramProfileWebUrl(raw, raw, '');
    if(fixed){ launchExternal(fixed); return; }
    toast('Instagram profile URL missing — set @handle on the lead', true);
    return;
  }
  // Custom schemes + mailto hand off more reliably via <a> than window.open on mobile Safari/Chrome
  try{
    const a=document.createElement('a');
    a.href=raw;
    a.target='_blank';
    a.rel='noopener noreferrer';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }catch(e){
    try{ window.open(raw,'_blank','noopener,noreferrer'); }catch(err){ window.location.href=raw; }
  }
}
function launchAppWithFallback(appUrl, webUrl, waitMs){
  if(appUrl && isMobileClient()){
    const start=Date.now();
    // Navigate to custom scheme; if the app isn't installed, bounce to https shortly after
    try{ window.location.href=appUrl; }catch(e){}
    setTimeout(()=>{
      // If page is still visible, app likely didn't take over
      if(document.visibilityState==='visible' && Date.now()-start>=(waitMs||700)){
        launchExternal(webUrl);
      }
    }, waitMs||700);
    return;
  }
  launchExternal(webUrl||appUrl);
}






function clearComposeConfirmReturn(){
  composeAwaitingReturn=false;
  composeReturnArmedAt=0;
  const modal=$('composeModal');
  if(modal) modal.classList.remove('confirm-ready');
  const banner=$('composeReturnBanner');
  if(banner) banner.classList.remove('show');
}
function showComposeConfirmReturn(){
  if(!composeLead) return;
  const modal=$('composeModal');
  if(!modal || modal.classList.contains('hidden')) return;
  if(!composeAwaitingReturn) return;
  // Ignore the brief blur that happens while launching the app
  if(composeReturnArmedAt && Date.now()-composeReturnArmedAt<900) return;
  composeAwaitingReturn=false;
  modal.classList.add('confirm-ready');
  const ch=openedChannel||($('composeChannel')&&$('composeChannel').value)||'message';
  const banner=$('composeReturnBanner');
  const text=$('composeReturnText');
  if(text) text.textContent=`Looks like you returned from ${channelLabel(ch)}. If you sent it, tap Confirm sent (or Confirm + next).`;
  if(banner) banner.classList.add('show');
  const btn=$('confirmSentBtn');
  if(btn){
    try{ btn.focus({preventScroll:false}); }catch(e){ try{btn.focus()}catch(err){} }
  }
  try{
    const foot=modal.querySelector('.modal-foot');
    if(foot) foot.scrollIntoView({behavior:'smooth',block:'nearest'});
  }catch(e){}
  toast('Welcome back — confirm sent when ready');
}
function fmtDate(iso){
  if(!iso) return '—';
  const d=new Date(iso);
  if(Number.isNaN(d.getTime())) return dateOnly(iso)||String(iso);
  try{return new Intl.DateTimeFormat(undefined,{year:'numeric',month:'short',day:'numeric'}).format(d)}
  catch(e){return dateOnly(iso)}
}




function isOverdue(b){if(!b.next_follow_up_at)return false;return dateOnly(b.next_follow_up_at)<localDay()}
function isDue(b){if(!b.next_follow_up_at||b.lost||b.archived)return false;return dateOnly(b.next_follow_up_at)<=localDay()}
function statusBadge(b){
  if(b.archived)return '<span class="badge">Archived</span>';
  if(b.lost)return '<span class="badge lost">Lost</span>';
  if(isOverdue(b))return '<span class="badge overdue">Overdue</span>';
  if(isDue(b))return '<span class="badge due">Due</span>';
  if(b.marked_for_contact&&!b.contacted)return '<span class="badge queue">To contact</span>';
  if(b.contacted)return '<span class="badge contacted">Contacted</span>';
  if(b.marked_for_contact)return '<span class="badge queue">Marked</span>';
  return '<span class="badge">New</span>';
}
function fillTemplate(text,b){return String(text||'')
  .split('{{name}}').join(b.name||'there')
  .split('{{city}}').join(b.city||'')
  .split('{{category}}').join(b.category||'')
  .split('{{phone}}').join(b.international_phone||b.phone||'')
  .split('{{website}}').join(b.website||'')}

function updateSelectedCount(){
  const n=selectedIds.size;
  $('selectedCount').textContent=n+' selected';
  $('bulkBar').classList.toggle('show',n>0);
  $('exportBtn').textContent=n?`Export selected (${n})`:'Export filtered';
  const boxes=[...document.querySelectorAll('input[data-select]')];
  const all=boxes.length&&boxes.every(b=>b.checked);
  const some=boxes.some(b=>b.checked);
  $('selectAll').checked=all;
  $('selectAll').indeterminate=!all&&some;
}

function localDay(){
  const d=new Date();
  const y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,'0'),day=String(d.getDate()).padStart(2,'0');
  return `${y}-${m}-${day}`;
}
function queryParams(){
  const rawStatus=$('statusFilter').value;
  const params=new URLSearchParams({
    q:$('filterText').value.trim(),
    status:rawStatus,
    contact:$('contactFilter').value,
    category:$('categoryFilter').value,
    day:localDay(),
  });
  if(viewMode==='queue'){
    params.set('scope','global');
    params.set('status','queue');
  }else if(viewMode==='due'){
    params.set('scope','global');
    if(rawStatus!=='overdue')params.set('status','due');
  }else{
    params.set('scope','city');
    // City Discover defaults to fresh when status is empty.
    params.set('status', resolveDiscoverStatus(rawStatus));
    const country=$('country').value.trim(),city=$('city').value.trim();
    if(country)params.set('country',country);
    if(city)params.set('city',city);
  }
  return params;
}

function discoverShowingFresh(){
  if(viewMode!=='city') return false;
  const st=$('statusFilter')&&$('statusFilter').value;
  return !st || st==='fresh' || st===DISCOVER_STATUS;
}

/** Drop leads that left the fresh Discover pool (marked / contacted / has booking). */
function pruneFreshDiscoverRows(){
  if(!discoverShowingFresh()) return false;
  const before=rowsData.length;
  rowsData=rowsData.filter(isFreshDiscoverLead);
  if(rowsData.length!==before){ paint(); return true; }
  return false;
}

function statsParams(){
  const params=new URLSearchParams({day:localDay()});
  if(viewMode==='city'){
    params.set('scope','city');
    const country=$('country').value.trim(),city=$('city').value.trim();
    if(country)params.set('country',country);
    if(city)params.set('city',city);
    if($('categoryFilter').value)params.set('category',$('categoryFilter').value);
  }else{
    params.set('scope','global');
  }
  return params;
}

async function loadCategories(){
  try{
    const params=new URLSearchParams();
    if(viewMode==='city'){
      params.set('scope','city');
      const country=$('country').value.trim(),city=$('city').value.trim();
      if(country)params.set('country',country);
      if(city)params.set('city',city);
    }else params.set('scope','global');
    const data=await api('/api/categories?'+params);
    const current=$('categoryFilter').value;
    $('categoryFilter').innerHTML='<option value="">All categories</option>'+(data.categories||[]).map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');
    if(current&&(data.categories||[]).includes(current))$('categoryFilter').value=current;
  }catch(e){}
}

async function loadStats(){
  try{
    const s=await api('/api/stats?'+statsParams());
    // Queue/Due labels say "Shown" — use filtered list length, not unfiltered scope total
    if(viewMode==='queue'||viewMode==='due') $('statTotal').textContent=rowsData.length;
    else if(discoverShowingFresh()) $('statTotal').textContent=s.fresh??rowsData.length;
    else $('statTotal').textContent=s.total??0;
    $('statQueue').textContent=s.queue??0;
    $('statDue').textContent=s.due??0;
    $('statUncontacted').textContent=s.uncontacted??0;
    $('statContacted').textContent=s.contacted??0;
    $('statMarked').textContent=s.marked??0;
    $('statLost').textContent=s.lost??0;
    $('statGlobal').textContent=s.global_queue??0;
  }catch(e){}
}

function reachCell(b){
  const parts=[];
  const phone=b.international_phone||b.phone;
  if(phone)parts.push(`<span class="chip ok" title="${esc(phone)}">${esc(phone)}</span>`);
  const email=firstEmail(b);
  if(email)parts.push(`<span class="chip ok" title="${esc(email)}">${esc(email)}</span>`);
  if(b.website)parts.push(`<a class="chip" href="${esc(b.website)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">${esc(siteLabel(b.website))}</a>`);
  const ig=(b.social_links||{}).instagram;
  if(ig){
    const handle=instagramHandle(ig) || '';
    const href=instagramProfileWebUrl(handle, ig, b.name||'');
    parts.push(`<button type="button" class="chip ok" data-open-ig-profile="${esc(ig)}" data-ig-handle="${esc(handle)}" data-ig-biz="${esc(b.name||'')}" title="${esc(handle?('@'+handle):href)}">Instagram</button>`);
  }
  return parts.length?`<div class="reach">${parts.join('')}</div>`:'<div class="sub">No contact details</div>';
}

function actionButtons(b){
  // Discover triage only — messaging lives on Contact queue → Ready
  const parts=[];
  const hasBook=!!(b.current_booking_software||'').trim();
  parts.push(`<button type="button" data-booking="${b.id}" data-booking-on="${hasBook?'0':'1'}">${hasBook?'Clear booking':'Has booking'}</button>`);
  parts.push(`<button type="button" data-open="${b.id}">Details</button>`);
  return `<div class="lead-actions actions">${parts.join('')}</div>`;
}

function paint(){
  const body=$('rows');
  const pagerHost=ensurePagerHost($('leads'),'leadsPager');
  if(viewMode==='queue'||viewMode==='due')$('statTotal').textContent=String(rowsData.length);
  if(!rowsData.length){
    const waiting=viewMode==='city'&&(!$('country').value.trim()||!$('city').value.trim());
    let msg='No leads match this view or filter.';
    if(waiting)msg='Select a country and city to see leads.';
    else if(discoverShowingFresh())msg='No fresh Discover leads. Marked, contacted, and has-booking leads stay in Contact queue.';
    else if(viewMode==='queue')msg='Global queue is empty. Star (★) leads in City leads first, then come back here.';
    else if(viewMode==='due')msg='No due / overdue follow-ups yet. Set a follow-up date when you confirm sent.';
    body.innerHTML=`<div class="empty">${msg}</div>`;
    if(pagerHost) pagerHost.innerHTML='';
    updateSelectedCount();
    return;
  }
  const slice=pageSlice(rowsData,'discover');
  body.innerHTML=slice.items.map(b=>{
    const checked=selectedIds.has(String(b.id))?'checked':'';
    const starred=b.marked_for_contact?'on':'';
    const follow=b.next_follow_up_at?`<div class="sub">Follow up · ${esc(dateOnly(b.next_follow_up_at))}</div>`:'';
    const labels=b.search_labels&&b.search_labels!==b.category?`<div class="sub">${esc(b.search_labels)}</div>`:'';
    const hasBook=!!(b.current_booking_software||'').trim();
    return `<div class="lead lead-row ${drawerLead&&drawerLead.id===b.id?'active':''}" data-row="${b.id}" data-maps="${esc(mapsUrlFor(b))}">
      <div class="checkcol"><input type="checkbox" data-select="${b.id}" ${checked} aria-label="Select ${esc(b.name)}"></div>
      <div class="starcol"><button type="button" class="starbtn ${starred}" data-mark="${b.id}" title="Mark for contact">${b.marked_for_contact?'★':'☆'}</button></div>
      <div class="lead-biz"><div class="biz">${esc(b.name)}${hasBook?' <span class="badge">Booking</span>':''}</div><div class="sub">${esc(b.category||'—')} · ${esc(b.city)}, ${esc(b.country)}</div>${labels}${follow}</div>
      <div class="lead-reach">${reachCell(b)}</div>
      <div class="lead-meta meta">${b.rating!=null?`<div class="rating">★ ${esc(b.rating)}</div>`:'<div class="sub">—</div>'}${statusBadge(b)}</div>
      ${actionButtons(b)}
    </div>`;
  }).join('');
  renderPager(pagerHost, slice, paint);
  updateSelectedCount();
}

async function load(){
  const seq=++loadSeq;
  if(viewMode==='city'&&(!$('country').value.trim()||!$('city').value.trim())){
    if(seq!==loadSeq)return;
    rowsData=[];paint();
    if(seq===loadSeq)await loadStats();
    return;
  }
  const body=$('rows');
  const pagerHost=body&&ensurePagerHost($('leads'),'leadsPager');
  if(body){
    body.innerHTML=listSkeletonHtml({rows:6, label:'Loading leads…'});
    if(pagerHost) pagerHost.innerHTML='';
  }
  try{
    const data=await api('/api/businesses?'+queryParams());
    if(seq!==loadSeq)return; // a newer load() won the race
    rowsData=data.results||[];
    if(pagerState.discover) pagerState.discover.page=1;
    paint();
    await Promise.all([loadStats(),loadCategories()]);
  }catch(e){
    if(seq!==loadSeq)return;
    console.error('load leads failed', e);
    showMessage('Could not load leads: '+e.message,true);
  }
}

function fillCities(country){
  const city=$('city');
  const cities=locations[country]||[];
  if(!country){city.innerHTML='<option value="">Select a country first</option>';city.disabled=true;return}
  if(!cities.length){city.innerHTML='<option value="">No cities listed</option>';city.disabled=true;return}
  city.innerHTML='<option value="">Select city</option>'+cities.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');
  city.disabled=false;
  if(country==='Australia'&&cities.includes('Sydney'))city.value='Sydney';
}

async function loadLocations(){
  const data=await api('/api/locations');
  locations=data.countries||{};
  const names=Object.keys(locations).sort((a,b)=>a.localeCompare(b));
  $('country').innerHTML=names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  $('country').value=names.includes('Australia')?'Australia':(names[0]||'');
  fillCities($('country').value);
  await load();
}

function setView(mode,opts={}){
  const prev=viewMode;
  viewMode=mode;
  document.querySelectorAll('[data-view]').forEach(btn=>btn.classList.toggle('active',btn.dataset.view===mode));
  // Queue/Due force a status. Leaving those views must reset so City leads aren't stuck empty.
  // opts.status wins when jumping from a stat chip (e.g. Contacted) so we don't load twice with the wrong filter.
  if(opts.status!=null)$('statusFilter').value=opts.status;
  else if(mode==='queue')$('statusFilter').value='queue';
  else if(mode==='due')$('statusFilter').value='due';
  else if(mode==='city'&&(prev==='queue'||prev==='due'||['queue','due','overdue'].includes($('statusFilter').value))){
    $('statusFilter').value=DISCOVER_STATUS;
  }
  updateViewHint();
  // Clear immediately so we never flash the previous view's leads under the new filter label
  if(prev!==mode){rowsData=[];selectedIds.clear();paint()}
  load();
}

async function enrichIds(ids,opts={}){
  const list=[...new Set((ids||[]).map(Number).filter(Boolean))];
  if(!list.length){toast('Nothing to enrich.');return}
  if(enriching){toast('Enrich already running…');return}
  enriching=true;
  $('enrichBtn').disabled=true;
  let enriched=0,failed=0;
  const chunk=8;
  try{
    for(let i=0;i<list.length;i+=chunk){
      const slice=list.slice(i,i+chunk);
      $('enrichBtn').textContent=`Enriching ${Math.min(i+slice.length,list.length)}/${list.length}…`;
      const data=await api('/api/businesses/enrich',{method:'POST',body:{ids:slice,force:!!opts.force,limit:slice.length}});
      enriched+=data.enriched||0;failed+=data.failed||0;
      for(const b of data.results||[]){
        const idx=rowsData.findIndex(x=>String(x.id)===String(b.id));
        if(idx>=0)rowsData[idx]=b;
      }
      paint();
    }
    toast(`Enrich done: ${enriched} updated${failed?`, ${failed} failed`:''}.`);
    await loadStats();
  }catch(e){toast(e.message,true)}
  finally{enriching=false;$('enrichBtn').disabled=false;$('enrichBtn').textContent='Enrich contacts'}
}

function undoToast(message, undoFn){
  lastUndo={run:undoFn};
  toast(message,false,{label:'Undo',onClick:async()=>{await undoFn();await load();toast('Undone.')}});
}

async function patchLead(id,body){
  const updated=await api('/api/businesses/'+id,{method:'PATCH',body});
  const idx=rowsData.findIndex(x=>String(x.id)===String(id));
  if(idx>=0)rowsData[idx]=updated;
  if(drawerLead&&String(drawerLead.id)===String(id)){
    drawerLead=updated;
    renderDrawer();
    loadActivity(id); // renderDrawer resets Activity to Loading — refill it
  }
  // Unmark / clear booking can return a lead to Discover — reload so it lands in the fresh list.
  const keys=Object.keys(body||{});
  const mayReturnToDiscover=keys.some(k=>['marked_for_contact','contacted','lost'].includes(k))
    && isFreshDiscoverLead(updated)
    && discoverShowingFresh()
    && idx<0;
  if(mayReturnToDiscover){
    await load();
  }else{
    pruneFreshDiscoverRows();
    paint();
    loadStats();
  }
  return updated;
}

async function toggleMark(id){
  const row=rowsData.find(x=>String(x.id)===String(id));
  if(!row)return;
  try{
    const nextMarked=!row.marked_for_contact;
    await patchLead(id,{marked_for_contact:nextMarked});
    if(nextMarked) toast('Marked for contact — left Discover.');
    else toast('Unmarked — back in Discover.');
  }catch(e){toast(e.message,true)}
}

function availableComposeChannels(b){return composeAvailableChannels(b)}

function renderComposeChannelControls(){
  const host=$('composeChannelControls');
  if(!host||!composeLead) return;
  const channels=availableComposeChannels(composeLead);
  const active=($('composeChannel')&&$('composeChannel').value)||composePreferred||channels[0]||'email';
  host.innerHTML=composeChannelControlsHtml(channels, active, {mobile:isMobileClient()});
  host.querySelectorAll('[data-compose-ch]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const ch=btn.dataset.composeCh;
      if(!ch||!$('composeChannel')) return;
      $('composeChannel').value=ch;
      fillComposeTemplates(ch);
    });
  });
  host.querySelectorAll('[data-compose-open]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const ch=btn.dataset.composeOpen;
      if(!ch||!$('composeChannel')) return;
      if($('composeChannel').value!==ch){
        $('composeChannel').value=ch;
        fillComposeTemplates(ch);
      }
      openChannelApp().catch(e=>toast(e.message,true));
    });
  });
}

function composeContactInfo(b,channel){
  const s=b.social_links||{};
  if(channel==='email'){
    const emails=String(b.email||'').split(',').map(e=>e.trim()).filter(Boolean);
    return{label:'Email',html:emails.length?emails.map(e=>`<a href="mailto:${esc(e)}">${esc(e)}</a>`).join('<br>'):'No email'};
  }
  if(channel==='whatsapp'){
    const display=b.international_phone||b.phone||'';
    const phone=phoneDigits(b);
    if(!display||!phone) return{label:'WhatsApp',html:'No phone'};
    const href=isMobileClient()?`https://wa.me/${phone}`:whatsappWebUrl(phone,'');
    return{label:'WhatsApp',html:`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(display)}</a>`};
  }
  if(channel==='instagram'){
    if(!s.instagram){
      if(leadNeedsIgDiscover(b)){
        return{label:'Instagram',html:`<button type="button" class="linkish" data-compose-ig-discover="${b.id}">Find IG on website</button>`};
      }
      return{label:'Instagram',html:'No Instagram'};
    }
    const handle=instagramHandle(s.instagram);
    const label=handle?('@'+handle):'Open profile';
    // Button (not blind <a href>) so mobile uses instagram:// and desktop uses www.instagram.com/{handle}/
    return{label:'Instagram',html:`<button type="button" class="linkish" data-open-ig-profile="${esc(s.instagram)}" data-ig-handle="${esc(handle)}" data-ig-biz="${esc(b.name||'')}">${esc(label)}</button>`};
  }
  return{label:'Contact',html:'—'};
}

function fillComposeTemplates(channel){
  const list=templatesFor(channel);
  const def=defaultTemplate(channel);
  if(!list.length){
    $('composeTemplate').innerHTML='<option value="">No templates — open Templates in the top menu and save one</option>';
    $('composeSubject').value='';
    $('composeBody').value='';
  }else{
    // Marked default first so the dropdown matches the message pane; soft openers next for IG/WA.
    const ordered=[...list].sort((a,b)=>{
      const ad=a&&a.isDefault?0:1;
      const bd=b&&b.isDefault?0:1;
      if(ad!==bd) return ad-bd;
      if(channel==='instagram'||channel==='whatsapp'){
        const as=(a.purpose||'')==='soft_opener'?0:1;
        const bs=(b.purpose||'')==='soft_opener'?0:1;
        if(as!==bs) return as-bs;
      }
      return String(a.name||'').localeCompare(String(b.name||''));
    });
    $('composeTemplate').innerHTML=ordered.map(t=>`<option value="${esc(t.id)}" ${String(t.id)===String(def&&def.id)?'selected':''}>${esc(t.name)}${esc(templateOptionLabel(t))}</option>`).join('');
    if(def&&def.id!=null) $('composeTemplate').value=String(def.id);
  }
  applyComposeTemplate(channel);
}
function applyComposeTemplate(channel){
  if(!composeLead)return;
  const ch=channel||($('composeChannel')&&$('composeChannel').value)||composePreferred||'email';
  const tpl=getTemplate(ch,$('composeTemplate').value);
  $('composeSubjectField').classList.toggle('hidden',ch!=='email');
  const subject=fillTemplate((tpl&&tpl.subject)||'',composeLead);
  const body=fillTemplate((tpl&&tpl.body)||'',composeLead);
  $('composeSubject').value=subject;
  $('composeBody').value=body;
  if(!body){
    // Fallback so Email/WA never open empty if templates failed to load
    $('composeBody').value=fillTemplate('Hi {{name}},\n\nI came across your {{category}} business in {{city}} and wanted to reach out.\n\nThanks!',composeLead);
    if(ch==='email'&&!$('composeSubject').value)$('composeSubject').value=fillTemplate('Quick idea for {{name}}',composeLead);
  }
  const info=composeContactInfo(composeLead,ch);
  $('composeContactLabel').textContent='Sending via '+channelLabel(ch);
  $('composeContactValue').innerHTML=info.html;
  const igBtn=$('composeContactValue').querySelector('[data-open-ig-profile]');
  if(igBtn){
    igBtn.addEventListener('click',ev=>{
      ev.preventDefault();
      openInstagramProfile(igBtn.dataset.openIgProfile||'', igBtn.dataset.igHandle||'', igBtn.dataset.igBiz||(composeLead&&composeLead.name)||'');
    });
  }
  const igFind=$('composeContactValue').querySelector('[data-compose-ig-discover]');
  if(igFind){
    igFind.addEventListener('click',async ev=>{
      ev.preventDefault();
      if(!composeLead) return;
      igFind.disabled=true;
      igFind.textContent='Finding IG…';
      try{
        const res=await ensureLeadInstagram(composeLead,{force:true});
        if(res.found && res.instagram){
          applyDiscoveredIg(composeLead, res.instagram);
          toast('Instagram found on website');
          fillComposeTemplates($('composeChannel').value);
          renderComposeChannelControls();
        }else{
          toast('No Instagram link on website', true);
          igFind.disabled=false;
          igFind.textContent='Find IG on website';
        }
      }catch(err){
        toast(err.message||'IG discover failed', true);
        igFind.disabled=false;
        igFind.textContent='Find IG on website';
      }
    });
  }
  const mobile=isMobileClient();
  $('composeNote').textContent=ch==='email'
    ?(mobile
      ?'Open app launches your mail app (set Zoho Mail as default). Message is also copied — paste if needed, then Confirm sent.'
      :'Copy subject + body, or Open Zoho web compose. Then Confirm sent.')
    :ch==='whatsapp'
      ?(mobile
        ?'Open app launches WhatsApp Business (or WhatsApp). Message is prefilled when possible — then Confirm sent.'
        :'Copy message, or Open WhatsApp Web. Then Confirm sent.')
      :(mobile
        ?'Open app launches Instagram. Message is copied — paste into DM, then Confirm sent.'
        :'Copy message, or Open Instagram web. Then Confirm sent.');
  if($('openComposeBtn')){
    $('openComposeBtn').textContent=ch==='email'
      ?(mobile?'Open Zoho / Mail app':'Open Zoho Mail')
      :ch==='whatsapp'
        ?(mobile?'Open WhatsApp app':'Open WhatsApp Web')
        :(mobile?'Open Instagram app':'Open Instagram');
  }
  renderComposeChannelControls();
  syncIgWarmComposeUi(composeLead, ch);
}

/** Re-apply default/selected template into compose message pane (after Set default). */
function refreshComposeTemplates(){
  const modal=$('composeModal');
  if(!modal || modal.classList.contains('hidden')) return;
  const ch=($('composeChannel')&&$('composeChannel').value)||composePreferred||'email';
  fillComposeTemplates(ch);
}
async function copyComposeMessage(){
  if(!composeLead)return;
  const channel=$('composeChannel').value;
  const subject=($('composeSubject')&&$('composeSubject').value||'').trim();
  let body=($('composeBody')&&$('composeBody').value||'').trim();
  if(channel==='email'){
    const htmlOk=await copyEmailHtmlSignature(body);
    body=withEmailSignature(body).trim();
    if(htmlOk){
      toast('Copied with logo image (HTML) — paste into Zoho / mail');
      return;
    }
  }
  const text=channel==='email'&&subject?`Subject: ${subject}\n\n${body}`:body;
  if(!text){ toast('Nothing to copy',true); return; }
  try{
    await navigator.clipboard.writeText(text);
    toast('Copied');
  }catch(e){
    // Fallback select
    const ta=$('composeBody');
    if(ta){ ta.focus(); ta.select(); }
    toast('Select the message and copy (⌘/Ctrl+C)',true);
  }
}
async function openCompose(b,preferred,opts={}){
  composeLead=b;openedChannel=null;composeAllowUnready=!!opts.allowUnready;
  wireIgWarmCompose();
  if(b.opt_out){showMessage('Prospect is opted out — outreach blocked.',true);return}
  // Cold outreach only from Ready (invoice/pay flows pass allowUnready)
  const alreadyTouched=!!(b.contacted || b.email_sent || b.whatsapp_sent || b.instagram_sent);
  if(!opts.allowUnready && !alreadyTouched && !b.ready_for_contact){
    showMessage('Move this lead to Ready to contact first (pick channel + IG warm on To contact).',true);
    return;
  }
  // Website may link IG even when social_links is empty — discover before gating channels
  if(preferred==='instagram' || leadNeedsIgDiscover(b)){
    try{
      const res=await ensureLeadInstagram(b);
      if(res.found && res.instagram){
        applyDiscoveredIg(b, res.instagram);
        const row=rowsData.find(x=>String(x.id)===String(b.id));
        if(row) applyDiscoveredIg(row, res.instagram);
        const qRow=(sharedState.contactQueueLeads||[]).find(x=>String(x.id)===String(b.id));
        if(qRow) applyDiscoveredIg(qRow, res.instagram);
      }
    }catch(_){ /* compose still opens with other channels */ }
  }
  try{
    const gate=await api('/api/sales/templates/preview',{method:'POST',body:{business_id:b.id,channel:preferred||'email',subject:'',body:'Hi {{name}}'}});
    if(gate&&gate.blocked){showMessage('Outreach blocked.',true);return}
  }catch(e){
    showMessage(e.message||'Outreach blocked by compliance.',true);return;
  }
  const channels=availableComposeChannels(b);
  if(!channels.length){showMessage('No email, WhatsApp, or Instagram on this lead. Edit contacts in Details or run Enrich / Find IG.',true);return}
  try{await loadTemplates()}catch(e){}
  composePreferred=preferredColdChannel(b, preferred);
  $('composeLead').textContent=`${b.name} · ${b.city}, ${b.country}`;
  $('composeChannel').innerHTML=channels.map(ch=>`<option value="${ch}" ${ch===composePreferred?'selected':''}>${channelLabel(ch)}</option>`).join('');
  $('composeOutcome').value=b.outcome||'';
  $('composeNotes').value='';
  $('composeFollowUp').value=dateOnly(b.next_follow_up_at)||'';
  $('composeFollowUp').dataset.touched='0';
  $('igCopyBox').classList.add('hidden');
  clearComposeConfirmReturn();
  fillComposeTemplates(composePreferred);
  setComposeTab('message');
  $('composeModal').classList.remove('hidden');
  document.body.style.overflow='hidden';
  const scroll=$('composeModal').querySelector('.modal-scroll');
  if(scroll)scroll.scrollTop=0;
  $('composeBody').focus();
  // Do not auto-open WhatsApp / Zoho / IG — user opens the message modal first, then Open app
  syncComposeMarkBtn();
  renderComposeChannelControls();
  await refreshIgOutreachQuota().catch(()=>{});
  syncIgWarmComposeUi(composeLead, composePreferred);
}
function setComposeTab(name){
  document.querySelectorAll('#composeTabs [data-compose-tab]').forEach(btn=>{
    const on=btn.dataset.composeTab===name;
    btn.classList.toggle('active',on);
    btn.setAttribute('aria-selected', on?'true':'false');
  });
  document.querySelectorAll('#composeModal [data-compose-pane]').forEach(pane=>{
    pane.classList.toggle('active', pane.dataset.composePane===name);
  });
}
function syncComposeMarkBtn(){
  const btn=$('composeMarkBtn');
  if(!btn||!composeLead) return;
  const already=!!composeLead.marked_for_contact;
  btn.textContent=already?'On To contact':'Mark to contact';
  btn.disabled=already;
}
function closeCompose(){
  clearComposeConfirmReturn();
  $('composeModal').classList.add('hidden');
  composeLead=null;openedChannel=null;composeAllowUnready=false;
  $('igCopyBox').classList.add('hidden');
  if($('drawer').classList.contains('hidden'))document.body.style.overflow='';
}
function nextWorkLead(afterId){
  // Walk whatever is currently on screen: Due list, queue, or city filter results
  let list=rowsData.filter(b=>!b.archived&&!b.lost);
  if(viewMode==='queue'||$('statusFilter').value==='queue'){
    list=list.filter(b=>b.marked_for_contact&&!b.contacted);
  }else if(viewMode==='due'||$('statusFilter').value==='due'||$('statusFilter').value==='overdue'){
    list=list.filter(b=>b.next_follow_up_at&&isDue(b));
  }else if($('statusFilter').value==='marked'){
    list=list.filter(b=>b.marked_for_contact);
  }
  // Prefer leads that still have a reachable channel
  const withChannel=list.filter(b=>availableComposeChannels(b).length);
  if(withChannel.length)list=withChannel;
  if(!list.length)return null;
  if(afterId==null)return list[0];
  const idx=list.findIndex(b=>String(b.id)===String(afterId));
  if(idx<0)return list[0];
  return list[idx+1]||null; // stop at end (don't wrap) so Due workflow feels finished
}
function openNextLead(afterId){
  const next=nextWorkLead(afterId);
  if(!next){toast('No more leads in this view.');return false}
  openCompose(next, preferredColdChannel(next));
  return true;
}

async function copyTextQuiet(text){
  if(!text) return false;
  try{ await navigator.clipboard.writeText(text); return true; }catch(e){ return false; }
}
async function openChannelApp(){
  if(!composeLead)return;
  const channel=$('composeChannel').value;
  if(!guardIgSoftSend(composeLead, channel)) return;
  const b=composeLead;
  const subject=$('composeSubject').value.trim();
  const body=$('composeBody').value.trim();
  const s=b.social_links||{};
  const mobile=isMobileClient();
  let tip='';
  if(channel==='email'){
    const to=firstEmail(b);if(!to){toast('No email.',true);return}
    const signed=withEmailSignature(body);
    const clip=subject?`Subject: ${subject}\n\n${signed}`:signed;
    const htmlCopied=await copyEmailHtmlSignature(body);
    const copied=htmlCopied||await copyTextQuiet(clip);
    if(mobile){
      launchExternal(mailtoComposeUrl(to,subject,body));
      tip=htmlCopied
        ?'Mail app opened. HTML signature (logo image) copied — paste into compose if needed.'
        :(copied?'Mail app opened. Message + logo URL copied.':'Mail app opened. Set Zoho Mail as default.');
    }else{
      launchExternal(zohoComposeUrl(to,subject,body));
      tip=htmlCopied
        ?'Zoho opened with line breaks. Logo HTML copied — paste once if the image is missing.'
        :'Opened Zoho Mail compose (logo URL in body). Confirm sent when done.';
    }
  }else if(channel==='whatsapp'){
    const phone=phoneDigits(b);if(!phone){toast('No phone.',true);return}
    const copied=await copyTextQuiet(body);
    if(mobile){
      launchExternal(whatsappAppUrl(phone,body));
      tip=copied
        ?'WhatsApp / Business opening with message. Confirm sent when done.'
        :'WhatsApp / Business opening. Paste message if it is blank, then Confirm sent.';
    }else{
      launchExternal(whatsappWebUrl(phone,body));
      tip='Opened WhatsApp Web. Confirm sent when done.';
    }
  }else if(channel==='instagram'){
    if(!s.instagram){toast('No Instagram.',true);return}
    $('igCopyBox').textContent=body||'(empty message)';
    $('igCopyBox').classList.remove('hidden');
    const copied=await copyTextQuiet(body);
    if(copied) toast('Copied');
    if(!copied){
      try{const range=document.createRange();range.selectNodeContents($('igCopyBox'));const sel=window.getSelection();sel.removeAllRanges();sel.addRange(range)}catch(err){}
    }
    openInstagramProfile(s.instagram, instagramHandle(s.instagram), b.name||'');
    tip=copied
      ?(mobile
        ?'Instagram app opening on their profile. Paste the copied message into DM, then Confirm sent.'
        :'Instagram profile opening. Paste into DM, then Confirm sent.')
      :(mobile
        ?'Instagram app opening. Copy from the box below, paste into DM, then Confirm sent.'
        :'Instagram profile opening. Copy from the box below, paste into DM, then Confirm sent.');
  }else{
    return;
  }
  openedChannel=channel;
  composeAwaitingReturn=true;
  composeReturnArmedAt=Date.now();
  toast(tip);
}

async function confirmSent(opts={}){
  if(!composeLead)return;
  const channel=openedChannel||$('composeChannel').value;
  if(!guardIgSoftSend(composeLead, channel)) return;
  const currentId=composeLead.id;
  clearComposeConfirmReturn();
  try{
    const body={channel};
    // Only change follow-up when the user set/cleared the date field intentionally via snooze or typing.
    // Empty field = leave existing follow-up unchanged (avoids wiping Due dates by accident).
    const follow=$('composeFollowUp').value;
    const followTouched=$('composeFollowUp').dataset.touched==='1';
    if(follow)body.next_follow_up_at=follow;
    else if(followTouched)body.next_follow_up_at=null;
    const outcome=$('composeOutcome').value;
    const notes=$('composeNotes').value.trim();
    const message=($('composeBody')&&$('composeBody').value||'').trim();
    if(outcome)body.outcome=outcome;
    if(notes)body.notes=notes;
    if(message)body.message=message;
    if(composeAllowUnready) body.allow_unready=true;
    const updated=await api('/api/businesses/'+composeLead.id+'/confirm-sent',{method:'POST',body});
    // Patch Discover + Contact queue caches so Contacted tab / badges update immediately
    const patch={
      contacted:true,
      last_contacted_at:updated.last_contacted_at||new Date().toISOString(),
      email_sent:!!updated.email_sent,
      whatsapp_sent:!!updated.whatsapp_sent,
      instagram_sent:!!updated.instagram_sent,
      ig_warm_follow:!!updated.ig_warm_follow,
      ig_warm_like:!!updated.ig_warm_like,
      ig_warm_comment:!!updated.ig_warm_comment,
      ig_warm_follow_only:!!updated.ig_warm_follow_only,
      ig_warm_complete:!!updated.ig_warm_complete,
      next_follow_up_at:updated.next_follow_up_at||'',
      outcome:updated.outcome||'',
    };
    const row=rowsData.find(x=>String(x.id)===String(currentId));
    if(row) Object.assign(row, patch);
    const qRow=(sharedState.contactQueueLeads||[]).find(x=>String(x.id)===String(currentId));
    if(qRow) Object.assign(qRow, patch, updated.stage?{stage:updated.stage}:{});
    if(channel==='instagram') await refreshIgOutreachQuota().catch(()=>{});
    const phoneOk=composePhoneDigits(composeLead||{}).length>=8;
    const callNudge=phoneOk && (channel==='instagram'||channel==='whatsapp');
    closeCompose();
    toast(
      callNudge
        ?`Confirmed ${channelLabel(channel)} sent — marked contacted. Call today if you can (optional).`
        :`Confirmed ${channelLabel(channel)} sent — marked contacted.`
    );
    // Always refresh current view so Global queue / Due lists drop completed leads
    await load();
    if(appPanel==='contactQueue' || sharedState.appPanel==='contactQueue'){
      try{
        // Stay on Ready/current tab — lead drops out of Ready into Contacted.
        await loadContactQueue({keepTab:true});
        if(!opts.next){
          if(sharedState.contactQueueTab==='ready' || sharedState.contactQueueTab==='selected' || sharedState.contactQueueTab==='all'){
            setContactQueueTab(sharedState.contactQueueTab||'ready');
          }
          renderContactQueueList();
        }
      }catch(e){}
    }
    // Pipeline columns bucket by CQ flags — refresh so Contacted / Follow-up move shows up.
    if(typeof B.loadPipeline==='function') B.loadPipeline().catch(()=>{});
    if(opts.next)openNextLead(currentId);
  }catch(e){toast(e.message,true)}
}

async function loadActivity(id){
  const list=$('activityList');
  if(!list)return;
  list.innerHTML='<li class="sub">Loading…</li>';
  try{
    const act=await api('/api/businesses/'+id+'/activity');
    if(String(drawerLead&&drawerLead.id)!==String(id))return; // drawer switched
    list.innerHTML=(act.results||[]).length?(act.results||[]).map(a=>{
      const src=a.source||'sales';
      const srcLabel=src==='appointhive'?'AH':(src==='stage'?'Stage':(src==='discover'?'Discover':'Sales'));
      const title=a.kind||a.action||a.activity_type||'';
      const note=a.note||a.summary||a.message||'';
      return `<li><strong>${esc(title)}</strong> <span class="ch-tag">${esc(srcLabel)}</span>${a.channel?' · '+esc(a.channel):''}${note?' · '+esc(note):''}<div class="sub">${esc(dateOnly(a.created_at||a.sort_at)||a.created_at||a.sort_at||'')}</div></li>`;
    }).join(''):'<li class="sub">No activity yet</li>';
  }catch(e){
    if(String(drawerLead&&drawerLead.id)===String(id))list.innerHTML='<li class="sub">Could not load activity</li>';
  }
}

async function openDrawer(id, opts={}){
  drawerMode=resolveDrawerMode(opts.panel||appPanel||sharedState.appPanel);
  let b=rowsData.find(x=>String(x.id)===String(id));
  if(!b){
    showDrawerLoading();
    try{
      try{
        b=await api('/api/sales/prospects/'+id);
      }catch(_){
        const data=await api('/api/businesses?scope=global&status=all&include_archived=true');
        b=(data.results||[]).find(x=>String(x.id)===String(id));
      }
      if(b) rowsData=[b, ...rowsData.filter(x=>String(x.id)!==String(id))];
    }catch(e){
      closeDrawer();
      toast(e.message,true);
      return;
    }
  }
  if(!b){
    closeDrawer();
    toast('Prospect #'+id+' not found',true);
    return;
  }
  drawerLead=b;
  drawerTab=clampDrawerTab(opts.tab||'overview', drawerMode);
  $('drawer').classList.remove('hidden');
  document.body.style.overflow='hidden';
  renderDrawer();
  refreshScorePreview();
  paint();
  const scroll=$('drawerBody');
  if(scroll)scroll.scrollTop=0;
  await loadActivity(id);
}
function closeDrawer(){
  $('drawer').classList.add('hidden');
  drawerLead=null;
  drawerTab='overview';
  drawerMode='crm';
  paint();
  if($('composeModal').classList.contains('hidden'))document.body.style.overflow='';
}

function renderDrawer(){
  const b=drawerLead;if(!b)return;
  $('drawerTitle').textContent=b.name;
  $('drawerSub').textContent=`${b.category||'—'} · ${b.city}, ${b.country}`;
  const s=b.social_links||{};
  const outreach=drawerMode==='discover'||drawerMode==='queue'||drawerMode==='outreach';
  const discoverOnly=drawerMode==='discover';
  const queueOnly=drawerMode==='queue';
  const tab=clampDrawerTab(drawerTab||'overview', drawerMode);
  drawerTab=tab;
  const crmTabs=outreach?'':`
      <button type="button" data-dtab="sales" class="${tab==='sales'?'active':''}">Sales</button>
      <button type="button" data-dtab="proposal" class="${tab==='proposal'?'active':''}">Proposal / pay</button>
      <button type="button" data-dtab="appointhive" class="${tab==='appointhive'?'active':''}">Delivery / pay</button>`;
  const pipelineTab=discoverOnly||queueOnly?'':`<button type="button" data-dtab="pipeline" class="${tab==='pipeline'?'active':''}">Pipeline</button>`;
  const activityTab=discoverOnly?'':`<button type="button" data-dtab="activity" class="${tab==='activity'?'active':''}">Activity</button>`;
  const crmPanes=outreach?'':`
    <div class="drawer-pane ${tab==='sales'?'active':''}" data-dpane="sales">
      <div class="field"><label for="drawerDealValue">Deal value</label><input id="drawerDealValue" type="number" step="0.01" value="${esc(b.deal_value??'')}" placeholder="e.g. 2500"></div>
      <div class="field"><label for="drawerCurrency">Currency</label>${currencySelectHtml(b.currency||'USD','drawerCurrency')}<p class="hint" style="margin:6px 0 0">Default is USD. Switch to AUD/EUR/etc. only when this prospect needs a local currency.</p></div>
      <div class="field"><label for="drawerNextAction">Next action</label><input id="drawerNextAction" value="${esc(b.next_action||'')}"></div>
      <div class="field"><label for="drawerLostReason">Lost reason</label><input id="drawerLostReason" value="${esc(b.lost_reason||'')}"></div>
      <label class="flag"><input type="checkbox" id="drawerOptOut" ${b.opt_out?'checked':''}> Opted out (blocks outreach)</label>
      <div class="field" style="margin-top:10px"><label>Qualification score</label>
        <div class="score-card" id="scoreCard">
          <p class="score-live" id="scoreLiveHint">${b.qualification_score!=null?`Current · <strong style="color:var(--text)">${esc(b.qualification_score)}/10</strong> — adjust criteria then save`:`Not scored yet — rate each factor 0–2, then save`}</p>
          <div class="score-grid">
            <label for="scoreFit">Business fit</label>
            <select id="scoreFit" data-score-key="business_fit">${scoreOpts(b,'business_fit',2)}</select>
            <label for="scorePain">Operational pain</label>
            <select id="scorePain" data-score-key="operational_pain">${scoreOpts(b,'operational_pain',2)}</select>
            <label for="scoreUrgency">Buying urgency</label>
            <select id="scoreUrgency" data-score-key="buying_urgency">${scoreOpts(b,'buying_urgency',1)}</select>
            <label for="scoreAccess">Decision-maker access</label>
            <select id="scoreAccess" data-score-key="decision_maker_access">${scoreOpts(b,'decision_maker_access',1)}</select>
            <label for="scoreProduct">Product fit</label>
            <select id="scoreProduct" data-score-key="product_fit">${scoreOpts(b,'product_fit',1)}</select>
          </div>
          <div class="score-actions">
            <button type="button" class="primary" id="scoreDrawerBtn">Save score</button>
            <span class="hint" style="margin:0" id="scorePreview">Total preview updates as you change selects</span>
          </div>
        </div>
      </div>
      <div class="field"><label for="drawerDiscoveryPain">Discovery · painful issue</label><textarea id="drawerDiscoveryPain" rows="3" placeholder="Most painful operational issue"></textarea></div>
      <div class="field"><label for="drawerDemoOutcome">Demo outcome</label><input id="drawerDemoOutcome" placeholder="positive / concerns / …"></div>
      <div class="field"><label for="drawerMrr">SaaS MRR</label><input id="drawerMrr" type="number" step="0.01" value="${esc(b.mrr??'')}" placeholder="e.g. 149"></div>
      <div class="field"><label for="drawerLinkedId">Linked prospect ID (STANDALONE↔SAAS)</label><input id="drawerLinkedId" type="number" value="${esc(b.linked_prospect_id??'')}" placeholder="other product-line row"></div>
      <div class="drawer-actions">
        <button type="button" id="saveSalesBtn">Save sales fields</button>
        <button type="button" id="saveDiscoveryBtn">Save discovery</button>
        <button type="button" id="saveDemoBtn">Save demo</button>
        <button type="button" id="linkProspectBtn">Link product line</button>
        <button type="button" id="undoStageBtn">Undo last stage</button>
        <button type="button" data-dtab-jump="proposal">Open proposal / pay</button>
        <button type="button" data-dtab-jump="appointhive">Open Delivery / pay</button>
      </div>
    </div>
    <div class="drawer-pane ${tab==='proposal'?'active':''}" data-dpane="proposal">
      <p class="hint" style="margin-top:0">Create a pay link + QR per provider for <strong>${esc(b.name||'')}</strong>. View, copy, open, and print below.</p>
      <div class="field"><label>Create pay link by provider</label>
        <div class="provider-create-row" id="drawerProviderCreateRow">
          <button type="button" class="primary" data-drawer-pay-provider="manual">Manual</button>
          ${!(beConnCache&&beConnCache.connected&&(b.product_line||'STANDALONE').toUpperCase()==='STANDALONE')?`<button type="button" data-drawer-pay-provider="stripe">Stripe</button>
          <button type="button" data-drawer-pay-provider="paypal">PayPal</button>`:`<span class="hint" style="align-self:center">Stripe/PayPal → Delivery / pay tab</span>`}
          <button type="button" data-drawer-pay-provider="payoneer">Payoneer</button>
          <button type="button" data-drawer-pay-provider="skrill">Skrill</button>
        </div>
      </div>
      <div class="drawer-actions" style="margin-bottom:10px">
        <button type="button" class="primary" id="createProposalBtn">Create with plan picker…</button>
        <button type="button" id="drawerRefreshPaylinksBtn">Refresh</button>
      </div>
      <h4 class="panel-title" style="margin-top:4px">This lead · pay links &amp; QR</h4>
      <div id="drawerPaylinksHost" class="invoice-card-grid"><div class="hint">Loading…</div></div>
    </div>
    <div class="drawer-pane ${tab==='appointhive'?'active':''}" data-dpane="appointhive">
      <div id="beDrawerBlock" class="prov-card" style="margin-top:0">
        <strong>Delivery / pay · Send to AppointHive</strong>
        <div class="sub" id="beDrawerStatus">Checking connection…</div>
        <div id="beDrawerPrices" class="hint" style="margin-top:8px"></div>
        <div class="drawer-actions" style="margin-top:8px">
          <button type="button" class="primary" id="beSendDeliveryBtn" disabled>Send to delivery (pay-ready)</button>
          <button type="button" id="beSendRequestBtn" disabled>Soft request (await quote)</button>
          <button type="button" id="beSendUpgradeBtn" disabled title="Requires installation_id from prior delivery">Send upgrade</button>
          <button type="button" id="beRefreshDeliveryBtn" disabled>Refresh status</button>
        </div>
        <div id="beDrawerCommercial" class="hint" style="margin-top:10px"></div>
        <div id="beDrawerPay" class="hint" style="margin-top:8px"></div>
      </div>
    </div>`;
  $('drawerBody').innerHTML=`
    <div class="drawer-tabs" role="tablist">
      <button type="button" data-dtab="overview" class="${tab==='overview'?'active':''}">Overview</button>
      <button type="button" data-dtab="contacts" class="${tab==='contacts'?'active':''}">Contacts</button>
      ${crmTabs}
      ${pipelineTab}
      ${activityTab}
    </div>
    <div class="drawer-pane ${tab==='overview'?'active':''}" data-dpane="overview">
      <div class="field"><label>Status</label><div>${statusBadge(b)}</div></div>
      <div class="sub">${b.rating!=null?`★ ${esc(b.rating)} · ${esc(b.reviews_count??'—')} reviews`:''}</div>
      <div class="sub">${b.editorial_summary?esc(b.editorial_summary):''}</div>
      <div class="sub">Labels · ${esc(b.search_labels||b.category||'—')}</div>
      <div class="sub" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:6px">
        <span>Line · ${esc(b.product_line||'STANDALONE')}</span>
        ${b.deal_value!=null?`<span class="value-pill has">${esc(b.currency||'USD')} ${esc(b.deal_value)}</span>`:`<span class="value-pill muted-dash">No value</span>`}
        ${b.qualification_score!=null?`<span class="score-pill has">${esc(b.qualification_score)}/10</span>`:`<span class="score-pill muted-dash">Not scored</span>`}
      </div>
      <div class="sub" style="margin-top:8px">Place ID · ${esc(String(b.place_id||'').replace(/^places\//,''))}</div>
      <div class="links" style="margin-top:10px">${b.google_maps_url?`<a href="${esc(b.google_maps_url)}" target="_blank" rel="noopener noreferrer">Google Maps</a>`:''}</div>
      ${(b.marked_for_contact||(b.current_booking_software||'').trim())?`<div class="sub" style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">
        ${b.marked_for_contact?'<span class="badge queue">To contact</span>':''}
        ${(b.current_booking_software||'').trim()?'<span class="badge">Has booking</span>':''}
      </div>`:''}
    </div>
    <div class="drawer-pane ${tab==='contacts'?'active':''}" data-dpane="contacts">
      <div class="field"><label for="drawerEmail">Email</label><input id="drawerEmail" value="${esc(b.email||'')}" placeholder="name@business.com"></div>
      <div class="field"><label for="drawerPhone">Phone</label><input id="drawerPhone" value="${esc(b.phone||'')}" placeholder="Local phone"></div>
      <div class="field"><label for="drawerIntlPhone">International phone</label><input id="drawerIntlPhone" value="${esc(b.international_phone||'')}" placeholder="+61…"></div>
      <div class="field"><label for="drawerWebsite">Website</label><input id="drawerWebsite" value="${esc(b.website||'')}" placeholder="https://"></div>
      <div class="field"><label for="drawerInstagram">Instagram URL</label><input id="drawerInstagram" value="${esc(s.instagram||'')}" placeholder="https://instagram.com/…"></div>
      <div class="field"><label for="drawerAddress">Address</label><input id="drawerAddress" value="${esc(b.address||'')}"></div>
      <div class="drawer-actions">
        <button type="button" class="primary" id="saveDrawerBtn">Save contacts & flags</button>
        <button type="button" id="enrichDrawerBtn">Enrich</button>
        ${!s.instagram && (b.website||'').trim() ? `<button type="button" data-ig-discover="${b.id}">Find IG</button>` : ''}
      </div>
    </div>
    ${crmPanes}
    ${discoverOnly||queueOnly?'':`<div class="drawer-pane ${tab==='pipeline'?'active':''}" data-dpane="pipeline">
      <p class="hint" style="margin-top:0">Flags sync with pipeline: To contact · Ready to contact · Contacted match Contact queue tabs. Due +3/+7 open Follow-up columns; Engaged → Won/Lost unchanged.</p>
      <label class="flag"><input type="checkbox" data-dfield="marked_for_contact" ${b.marked_for_contact?'checked':''}> Marked for contact</label>
      <label class="flag"><input type="checkbox" data-dfield="email_sent" ${b.email_sent?'checked':''}> Email sent</label>
      <label class="flag"><input type="checkbox" data-dfield="whatsapp_sent" ${b.whatsapp_sent?'checked':''}> WhatsApp sent</label>
      <label class="flag" title="${b.instagram_sent?'':'Use Compose → Confirm sent (warm + daily cap)'}">
        <input type="checkbox" data-dfield="instagram_sent" ${b.instagram_sent?'checked':''} ${b.instagram_sent?'':'disabled'}>
        Instagram sent ${b.instagram_sent?'':'(use Confirm sent)'}
      </label>
      <label class="flag"><input type="checkbox" data-dfield="contacted" ${b.contacted?'checked':''}> Contacted</label>
      <label class="flag"><input type="checkbox" data-dfield="lost" ${b.lost?'checked':''}> Lost</label>
      <div class="field" style="margin-top:10px"><label for="drawerFollowUp">Next follow up</label><input id="drawerFollowUp" type="date" value="${esc(dateOnly(b.next_follow_up_at))}"></div>
      <div class="field"><label for="drawerOutcome">Outcome</label>
        <select id="drawerOutcome">
          <option value="">—</option>
          ${['interested','not_now','booked','no_reply','wrong_contact','other'].map(o=>`<option value="${o}" ${b.outcome===o?'selected':''}>${o.replace('_',' ')}</option>`).join('')}
        </select>
      </div>
      <div class="field"><label for="drawerNotes">Notes</label><textarea id="drawerNotes">${esc(b.notes||'')}</textarea></div>
      <div class="field" style="margin-top:8px"><label>Snooze follow-up</label>
        <div class="snooze">
          <button type="button" data-drawer-snooze="1">Tomorrow</button>
          <button type="button" data-drawer-snooze="3">+3 days</button>
          <button type="button" data-drawer-snooze="7">+7 days</button>
        </div>
      </div>
      <div class="drawer-actions">
        <button type="button" class="primary" id="saveDrawerBtn">Save</button>
        ${b.archived
          ?'<button type="button" id="unarchiveDrawerBtn">Unarchive</button>'
          :'<button type="button" id="archiveDrawerBtn">Archive</button>'}
        <button type="button" class="danger" id="deleteDrawerBtn">Delete</button>
      </div>
    </div>`}
    ${discoverOnly?'':`<div class="drawer-pane ${tab==='activity'?'active':''}" data-dpane="activity">
      <ul class="activity" id="activityList"><li class="sub">Loading…</li></ul>
    </div>`}`;
  $('drawerBody').querySelectorAll('[data-dtab]').forEach(btn=>btn.addEventListener('click',()=>{
    drawerTab=clampDrawerTab(btn.dataset.dtab, drawerMode); renderDrawer(); refreshScorePreview(); if(drawerTab==='activity'&&drawerLead) loadActivity(drawerLead.id);
  }));
  $('drawerBody').querySelectorAll('[data-dtab-jump]').forEach(btn=>btn.addEventListener('click',()=>{
    drawerTab=clampDrawerTab(btn.dataset.dtabJump, drawerMode); renderDrawer(); refreshScorePreview();
  }));
  $('drawerBody').querySelectorAll('select[data-score-key]').forEach(sel=>sel.addEventListener('change',refreshScorePreview));
  if(!outreach) refreshScorePreview();
  if(!outreach) hydrateBeDrawer().catch(()=>{});
  if(!outreach && tab==='proposal') refreshDrawerPaylinks().catch(()=>{});
}

async function hydrateBeDrawer(){
  const st=$('beDrawerStatus');
  const pay=$('beDrawerPay');
  const prices=$('beDrawerPrices');
  const commercial=$('beDrawerCommercial');
  const sendBtn=$('beSendDeliveryBtn');
  const reqBtn=$('beSendRequestBtn');
  const upBtn=$('beSendUpgradeBtn');
  const refBtn=$('beRefreshDeliveryBtn');
  if(!st||!drawerLead) return;
  try{
    const conn=await api('/api/sales/be/connection');
    setBeConnCache(conn);
    const connected=!!conn.connected;
    const standalone=(drawerLead.product_line||'STANDALONE')==='STANDALONE';
    if(!standalone){
      st.textContent='AppointHive product handover is for STANDALONE deals.';
      if(prices) prices.textContent='';
      if(commercial) commercial.textContent='';
      return;
    }
    st.innerHTML=connected
      ?`Connected as <strong>AppointHive Sales OS</strong> · commercial snapshot is read-only from AppointHive. Local PSP blocked.`
      :`Not connected — <button type="button" class="chip ok" data-goto="appoinHive:connect">Connect AppointHive</button> first.`;
    if(sendBtn) sendBtn.disabled=!connected;
    if(reqBtn) reqBtn.disabled=!connected;
    if(refBtn) refBtn.disabled=!connected;
    if(!connected){ if(pay) pay.textContent=''; if(prices) prices.textContent=''; if(commercial) commercial.textContent=''; if(upBtn) upBtn.disabled=true; return; }
    try{
      const data=await api('/api/sales/be/revisions');
      beRevisionsCache=data.revisions||[];
      if(prices){
        prices.innerHTML=beRevisionsCache.length
          ?`<div class="sub"><strong>Live packages</strong> (refetched on open)</div>`+beRevisionsCache.slice(0,6).map(r=>
            `<div class="sub">${esc(r.label||r.productName||r.edition||'Package')} · ${esc(r.currency||'USD')} ${(Number(r.priceCents||0)/100).toFixed(2)}</div>`
          ).join('')
          :'<div class="sub">No FINALIZED packages on AppointHive yet.</div>';
      }
    }catch(e){
      if(prices) prices.textContent=e.message||'Could not load live prices';
    }
    try{
      const snap=await api('/api/sales/be/prospects/'+drawerLead.id+'/commercial');
      let histHtml='';
      try{
        const hist=await api('/api/sales/prospects/'+drawerLead.id+'/stage-history');
        const rows=(hist.history||[]).slice(0,8);
        if(rows.length){
          histHtml=`<div class="sub" style="margin-top:10px"><strong>Stage history</strong></div>
            <div class="sales-table-wrap"><table class="sales-table"><thead><tr><th>When</th><th>Change</th><th>Note</th></tr></thead><tbody>
            ${rows.map(h=>`<tr><td class="sub">${esc((h.created_at||'').replace('T',' ').slice(0,16))}</td>
              <td>${esc(h.from_stage||'—')} → ${esc(h.to_stage||'—')}</td>
              <td class="sub">${esc(h.note||'')}</td></tr>`).join('')}
            </tbody></table></div>`;
        }
      }catch(_e){}
      if(commercial){
        commercial.innerHTML=`<div class="sub"><strong>Commercial status</strong> (AppointHive authority · read-only)</div>`+renderCommercialPanel(snap,{businessId:drawerLead.id})+histHtml;
        bindCommercialPanelActions(commercial,{onDone:()=>hydrateBeDrawer()});
      }
    }catch(e){
      if(commercial) commercial.innerHTML=`<div class="sub">Commercial status unavailable: ${esc(e.message||'')}</div>`;
    }
    const dels=await api('/api/sales/be/deliveries?business_id='+drawerLead.id);
    const latest=(dels.deliveries||[])[0];
    const installId=(latest&&latest.installation_id)||drawerLead.be_installation_id||'';
    if(upBtn){
      upBtn.disabled=!installId;
      upBtn.title=installId
        ?`Upgrade using installation ${installId}`
        :'Requires installation_id from prior AppointHive delivery webhook';
    }
    if(latest && pay){
      const fin=latest.financial_status||latest.status||'';
      const label=latest.purpose==='request'&&!latest.pay_url?'Awaiting AppointHive quote'
        :(latest.purpose==='upgrade'?'Upgrade · '+latest.status
        :(fin==='partially_paid'?'Partially paid'
        :(latest.pay_url?'Pay-ready':'Status '+latest.status)));
      const payUrl=latest.pay_url||'';
      pay.innerHTML=`<div><span class="status-pill ${esc(latest.status)}">${esc(label)}</span> · ${esc(latest.currency||'')} ${((latest.quoted_amount_cents||0)/100).toFixed(2)}
        ${latest.financial_status?` · financial ${esc(latest.financial_status)}`:''}
        ${latest.delivery_status?` · delivery ${esc(latest.delivery_status)}`:''}</div>
        ${payUrl?`<div class="paylink-card" style="margin-top:8px" data-be-pay-card>
          <img class="paylink-qr" src="${esc(qrUrlFor(payUrl))}" alt="Pay QR" width="140" height="140" loading="lazy">
          <div class="paylink-meta">
            <div class="paylink-url">${esc(payUrl)}</div>
            <div class="action-stack" style="margin-top:6px">
              <button type="button" class="primary" data-be-open-pay="${esc(payUrl)}">Open pay URL</button>
              <button type="button" data-be-copy-pay="${esc(payUrl)}">Copy pay URL</button>
              <a class="chip ok" href="${esc(qrUrlFor(payUrl))}" download="appointhive-pay-qr.png" target="_blank" rel="noopener">Download QR</a>
              <button type="button" data-be-print-pay-qr="${esc(payUrl)}">Print QR</button>
            </div>
          </div>
        </div>`:''}
        <div class="sub">purchase ${esc(latest.purchase_id||'—')} · inbound ${esc(latest.inbound_request_id||'—')} · install ${esc(installId||'—')}</div>
        ${installId?`<div class="sub" style="margin-top:4px">Upgrade path ready (installation_id stored). Use <strong>Send upgrade</strong> after customer is live.</div>`
          :`<div class="sub" style="margin-top:4px">Upgrade unlocks after AppointHive sends <code>installation.active</code>.</div>`}`;
      pay.querySelectorAll('[data-be-open-pay]').forEach(btn=>btn.addEventListener('click',()=>window.open(btn.dataset.beOpenPay,'_blank','noopener')));
      pay.querySelectorAll('[data-be-copy-pay]').forEach(btn=>btn.addEventListener('click',async()=>{
        try{ await navigator.clipboard.writeText(btn.dataset.beCopyPay); toast('Pay URL copied'); }catch(e){ toast('Copy failed',true); }
      }));
      pay.querySelectorAll('[data-be-print-pay-qr]').forEach(btn=>btn.addEventListener('click',()=>{
        const url=btn.dataset.bePrintPayQr||'';
        if(!url) return;
        const w=window.open('','_blank');
        if(!w){ toast('Pop-up blocked',true); return; }
        w.document.write(`<!doctype html><title>Pay QR</title><body style="font-family:sans-serif;text-align:center;padding:24px"><h2>AppointHive payment</h2><img src="${esc(qrUrlFor(url))}" width="220" height="220"><p style="word-break:break-all">${esc(url)}</p>`+'<scr'+'ipt>window.onload=()=>window.print()</scr'+'ipt></body>');
        w.document.close();
      }));
      if(refBtn) refBtn.dataset.deliveryId=String(latest.id);
    }else if(pay){
      pay.innerHTML=installId
        ?`No open handover. installation_id <code>${esc(installId)}</code> on file — use Soft request or Send upgrade.`
        :'No AppointHive handover yet for this prospect.';
    }
  }catch(e){
    st.textContent=e.message||'Could not load AppointHive status';
  }
}

async function saveDrawer(){
  if(!drawerLead)return;
  try{
    const body={};
    const put=(id,key,fn)=>{ const el=$(id); if(el) body[key]=fn?fn(el):el.value; };
    put('drawerEmail','email',el=>el.value.trim());
    put('drawerPhone','phone',el=>el.value.trim());
    put('drawerIntlPhone','international_phone',el=>el.value.trim());
    put('drawerWebsite','website',el=>el.value.trim());
    put('drawerInstagram','instagram',el=>el.value.trim());
    put('drawerAddress','address',el=>el.value.trim());
    put('drawerNotes','notes',el=>el.value);
    put('drawerOutcome','outcome',el=>el.value);
    put('drawerFollowUp','next_follow_up_at',el=>el.value||null);
    const panel=$('drawerPanel');
    ['marked_for_contact','email_sent','whatsapp_sent','instagram_sent','contacted','lost'].forEach(k=>{
      const el=panel&&panel.querySelector(`[data-dfield="${k}"]`);
      // Disabled IG-sent checkbox must not be posted as false (would clear a prior confirm).
      if(el && !el.disabled) body[k]=!!el.checked;
    });
    if(!Object.keys(body).length){ toast('Nothing to save on this tab',true); return; }
    await patchLead(drawerLead.id,body);
    toast('Lead saved.');
  }catch(e){toast(e.message,true)}
}

async function bulk(body,opts={}){
  const ids=[...selectedIds].map(Number);
  if(!ids.length){toast('Select leads first.',true);return []}
  try{
    await api('/api/businesses/bulk',{method:'POST',body:{ids,...body}});
    selectedIds.clear();
    await load();
    if(!opts.silent)toast('Updated '+ids.length+' leads.');
    return ids;
  }catch(e){toast(e.message,true);return []}
}

// Events
document.querySelectorAll('[data-view]').forEach(btn=>btn.addEventListener('click',()=>setView(btn.dataset.view)));
document.querySelectorAll('#stats .stat').forEach(btn=>btn.addEventListener('click',()=>{
  if(btn.dataset.viewJump){setView(btn.dataset.viewJump);return}
  const status=btn.dataset.status;
  if(status==='queue'){setView('queue');return}
  if(status==='due'){setView('due');return}
  document.querySelectorAll('#stats .stat').forEach(s=>s.classList.toggle('active',s===btn));
  // One load only: pass status into setView so we don't race (reset→fresh then overwrite→contacted)
  if(viewMode!=='city')setView('city',{status:status||DISCOVER_STATUS});
  else{$('statusFilter').value=status||DISCOVER_STATUS;load()}
}));
$('country').addEventListener('change',()=>{fillCities($('country').value);if(viewMode==='city')load()});
$('city').addEventListener('change',()=>{if(viewMode==='city')load()});
let filterTimer;
$('filterText').addEventListener('input',()=>{clearTimeout(filterTimer);filterTimer=setTimeout(load,250)});
$('statusFilter').addEventListener('change',load);
$('categoryFilter').addEventListener('change',load);
$('contactFilter').addEventListener('change',load);
$('refreshBtn').addEventListener('click',load);
$('selectAll').addEventListener('change',()=>{
  const on=$('selectAll').checked;
  document.querySelectorAll('input[data-select]').forEach(box=>{box.checked=on;const id=String(box.dataset.select);if(on)selectedIds.add(id);else selectedIds.delete(id)});
  updateSelectedCount();
});
$('clearSelection').addEventListener('click',()=>{selectedIds.clear();paint()});
$('bulkMark').addEventListener('click',()=>bulk({marked_for_contact:true}));
$('bulkUnmark').addEventListener('click',()=>bulk({marked_for_contact:false}));
$('bulkContacted').addEventListener('click',()=>bulk({contacted:true}));
$('bulkLost').addEventListener('click',()=>bulk({lost:true}));
$('bulkFollowUp').addEventListener('click',()=>bulk({next_follow_up_at:plusDays(Number($('bulkSnooze').value||3))}));
$('bulkArchive').addEventListener('click',async()=>{
  const ids=await bulk({archived:true},{silent:true});
  if(ids.length)undoToast('Archived '+ids.length+' leads.',async()=>{await api('/api/businesses/bulk-unarchive',{method:'POST',body:{ids}})});
});
$('bulkUnarchive').addEventListener('click',()=>bulk({archived:false}));
$('exportBtn').addEventListener('click',()=>{
  if(selectedIds.size){
    const params=new URLSearchParams({ids:[...selectedIds].join(',')});
    window.location.href='/api/export.csv?'+params;
    toast('Exporting '+selectedIds.size+' selected leads.');
    return;
  }
  window.location.href='/api/export.csv?'+queryParams().toString();
  toast('Exporting current filtered view.');
});
$('enrichBtn').addEventListener('click',()=>{
  const ids=selectedIds.size?[...selectedIds].map(Number):rowsData.filter(b=>b.website&&(!firstEmail(b)||!(b.social_links&&Object.keys(b.social_links).length))).map(b=>b.id);
  enrichIds(ids.length?ids:rowsData.map(b=>b.id));
});
// Event delegation so nav clicks always work after redeploy / re-render
($('appNav')||$('sidebar'))&&($('appNav')||$('sidebar')).addEventListener('click',e=>{
  const btn=e.target&&e.target.closest&&e.target.closest('[data-app-panel]');
  if(!btn) return;
  e.preventDefault();
  setAppPanel(btn.getAttribute('data-app-panel')||btn.dataset.appPanel);
});
$('navToggle')&&$('navToggle').addEventListener('click',()=>{
  const open=!document.body.classList.contains('sidebar-open');
  document.body.classList.toggle('sidebar-open',open);
  $('navToggle').setAttribute('aria-expanded',open?'true':'false');
  closeTopMenus();
});
$('sidebarBackdrop')&&$('sidebarBackdrop').addEventListener('click',closeSidebar);
function toggleMenu(btnId, menuId){
  const btn=$(btnId), menu=$(menuId);
  if(!btn||!menu) return;
  const willOpen=!menu.classList.contains('open');
  closeTopMenus();
  if(willOpen){
    menu.classList.add('open');
    btn.setAttribute('aria-expanded','true');
  }
}
$('notifBtn')&&$('notifBtn').addEventListener('click',e=>{
  e.stopPropagation();
  const opening=!$('notifMenu').classList.contains('open');
  toggleMenu('notifBtn','notifMenu');
  if(opening) refreshNotifications().catch(()=>{});
});
$('notifClearAll')&&$('notifClearAll').addEventListener('click',e=>{
  e.preventDefault(); e.stopPropagation();
  clearAllNotifications(getNotifLastItems());
  refreshNotifications().catch(()=>{});
  toast('Notifications cleared');
});
$('profileBtn')&&$('profileBtn').addEventListener('click',e=>{
  e.stopPropagation();
  toggleMenu('profileBtn','profileMenu');
});
$('profileSettingsBtn')&&$('profileSettingsBtn').addEventListener('click',()=>{ closeTopMenus(); setAppPanel('settings'); });
$('notifMenu')&&$('notifMenu').addEventListener('click',e=>e.stopPropagation());
$('profileMenu')&&$('profileMenu').addEventListener('click',e=>e.stopPropagation());
document.addEventListener('click',e=>{
  const gotoBtn=e.target.closest('[data-goto]');
  if(gotoBtn){
    const raw=gotoBtn.getAttribute('data-goto')||gotoBtn.dataset.goto||'';
    if(raw){
      e.preventDefault();
      closeTopMenus();
      const drawer=$('drawer');
      if(drawer && !drawer.classList.contains('hidden')) closeDrawer();
      setAppPanel(raw);
      return;
    }
  }
  closeTopMenus();
});
/* pipeline filters/refresh wired in panels/pipeline.js */
$('tasksRefresh')&&$('tasksRefresh').addEventListener('click',()=>loadTasks().catch(e=>toast(e.message,true)));
$('taskCreateBtn')&&$('taskCreateBtn').addEventListener('click',()=>createTaskFlow().catch(e=>toast(e.message,true)));
$('prospectsRefresh')&&$('prospectsRefresh').addEventListener('click',()=>loadProspects().catch(e=>toast(e.message,true)));
$('prospectsQ')&&$('prospectsQ').addEventListener('keydown',e=>{if(e.key==='Enter')loadProspects().catch(err=>toast(err.message,true))});
$('prospectsCountry')&&$('prospectsCountry').addEventListener('change',()=>{
  if($('prospectsCity')) $('prospectsCity').value='';
  fillProspectsLocationFilters(($('prospectsCountry')&&$('prospectsCountry').value)||'', '');
  loadProspects().catch(e=>toast(e.message,true));
});
$('prospectsCity')&&$('prospectsCity').addEventListener('change',()=>loadProspects().catch(e=>toast(e.message,true)));
$('activitiesRefresh')&&$('activitiesRefresh').addEventListener('click',()=>{
  if(commsTab==='invoices') loadCommsInvoices().catch(e=>toast(e.message,true));
  else loadActivitiesFeed().catch(e=>toast(e.message,true));
});
document.querySelectorAll('#commsTabs [data-comms-tab]').forEach(btn=>btn.addEventListener('click',()=>setCommsTab(btn.dataset.commsTab)));
document.querySelectorAll('#moneyTabs [data-money-tab]').forEach(btn=>btn.addEventListener('click',()=>setMoneyTab(btn.dataset.moneyTab)));
$('activityCreateBtn')&&$('activityCreateBtn').addEventListener('click',()=>createActivityFlow().catch(e=>toast(e.message,true)));
$('demosRefresh')&&$('demosRefresh').addEventListener('click',()=>loadDemos().catch(e=>toast(e.message,true)));
$('demoAssetsRefresh')&&$('demoAssetsRefresh').addEventListener('click',()=>loadDemoAssets().catch(e=>toast(e.message,true)));
$('demoAssetAddBtn')&&$('demoAssetAddBtn').addEventListener('click',()=>addDemoAssetFlow().catch(e=>toast(e.message,true)));
$('demoAssetFile')&&$('demoAssetFile').addEventListener('change',async ev=>{
  const f=ev.target.files&&ev.target.files[0];
  if(!f) return;
  try{ await uploadDemoAssetFile(f); }catch(e){ toast(e.message,true); }
  ev.target.value='';
});
$('rankingModeAddBtn')&&$('rankingModeAddBtn').addEventListener('click',()=>editRankingModeFlow(null).catch(e=>toast(e.message,true)));
$('rankingModesRefresh')&&$('rankingModesRefresh').addEventListener('click',()=>loadRankingModesCrud().catch(e=>toast(e.message,true)));
$('demoCreateBtn')&&$('demoCreateBtn').addEventListener('click',()=>createDemoFlow().catch(e=>toast(e.message,true)));
$('guideSteps')&&$('guideSteps').addEventListener('click',e=>{
  const step=e.target.closest('[data-goto]');
  if(step) setAppPanel(step.dataset.goto);
});
$('profileGuideBtn')&&$('profileGuideBtn').addEventListener('click',()=>{ closeTopMenus(); setAppPanel('guide'); });
$('reportsRefresh')&&$('reportsRefresh').addEventListener('click',()=>loadReports().catch(e=>toast(e.message,true)));
$('moneyRefresh')&&$('moneyRefresh').addEventListener('click',()=>loadMoney().catch(e=>toast(e.message,true)));
$('paySetupRefresh')&&$('paySetupRefresh').addEventListener('click',()=>loadPaySetup().catch(e=>toast(e.message,true)));
$('paySetupSave')&&$('paySetupSave').addEventListener('click',()=>savePaySetup().catch(e=>toast(e.message,true)));
document.querySelectorAll('#ahTabs [data-ah-tab]').forEach(btn=>btn.addEventListener('click',()=>setAhTab(btn.dataset.ahTab)));
$('beCatalogRefresh')&&$('beCatalogRefresh').addEventListener('click',()=>loadBeCatalog().catch(e=>toast(e.message,true)));
$('beCatalogLine')&&$('beCatalogLine').addEventListener('change',()=>{ if(ahTab==='catalogs') loadBeCatalog().catch(()=>{}); });
$('beConnRefresh')&&$('beConnRefresh').addEventListener('click',()=>loadAppointHive().catch(e=>toast(e.message,true)));
$('beEventsRefresh')&&$('beEventsRefresh').addEventListener('click',()=>loadBeEvents().catch(e=>toast(e.message,true)));
$('beEventsStatus')&&$('beEventsStatus').addEventListener('change',()=>loadBeEvents().catch(()=>{}));
$('beHeartbeatBtn')&&$('beHeartbeatBtn').addEventListener('click',async()=>{
  if(!isOwnerRole()){ toast('Owner only',true); return; }
  try{
    const r=await api('/api/sales/be/sync/heartbeat',{method:'POST',body:{}});
    toast(r.message||(r.ok?'Heartbeat ok':'Heartbeat failed'), !r.ok);
    await loadAppointHive();
  }catch(e){ toast(e.message,true); }
});
$('beSaveManual')&&$('beSaveManual').addEventListener('click',async()=>{
  if(!isOwnerRole()){ toast('Owner only',true); return; }
  try{
    const base=($('beBaseUrl')&&$('beBaseUrl').value||'').trim();
    const secret=($('beSharedSecret')&&$('beSharedSecret').value||'').trim();
    if(!base){ toast('AppointHive base URL required',true); return; }
    if(!secret && !(beConnCache&&beConnCache.secret_configured)){
      toast('Shared secret required (or generate a pairing code)',true); return;
    }
    const body={base_url:base, shared_secret:secret||''};
    await api('/api/sales/be/connection/manual',{method:'POST',body});
    if($('beSharedSecret')) $('beSharedSecret').value='';
    toast(secret?'Saved — click Test connection':'URL saved (kept existing secret) — click Test connection');
    await loadAppointHive();
  }catch(e){ toast(e.message,true); }
});
$('beGenPair')&&$('beGenPair').addEventListener('click',async()=>{
  if(!isOwnerRole()){ toast('Owner only',true); return; }
  try{
    const r=await api('/api/sales/be/connection/pair-code',{method:'POST',body:{}});
    if($('bePairBox')){
      $('bePairBox').innerHTML=`<strong>Pairing code</strong> <code style="user-select:all">${esc(r.code)}</code> · expires ${esc(r.expires_at||'')} (${esc(String(r.ttl_minutes||15))} min). In AppointHive Super Admin, open AppointHive Sales OS connection and paste this Sales OS base URL + code.`;
    }
    try{ await navigator.clipboard.writeText(r.code); toast('Pairing code copied'); }catch(e){ toast('Pairing code ready'); }
  }catch(e){ toast(e.message,true); }
});
$('beTestConn')&&$('beTestConn').addEventListener('click',async()=>{
  if(!isOwnerRole()){ toast('Owner only',true); return; }
  try{
    const r=await api('/api/sales/be/connection/test',{method:'POST',body:{}});
    toast(r.message||(r.connected?'Connected':'Not connected'), !r.connected);
    await loadAppointHive();
  }catch(e){ toast(e.message,true); }
});
$('beDisconnect')&&$('beDisconnect').addEventListener('click',async()=>{
  if(!isOwnerRole()){ toast('Owner only',true); return; }
  const ok=await uiConfirm({title:'Disconnect AppointHive?', message:'Clears the shared secret path and blocks catalog/send until reconnected.', confirmLabel:'Disconnect'});
  if(!ok) return;
  try{
    await api('/api/sales/be/connection/disconnect',{method:'POST',body:{}});
    toast('Disconnected');
    await loadAppointHive();
  }catch(e){ toast(e.message,true); }
});
$('paySetupValidate')&&$('paySetupValidate').addEventListener('click',()=>validatePaySetup().catch(e=>toast(e.message,true)));
$('paySetupLocalDefaults')&&$('paySetupLocalDefaults').addEventListener('click',()=>applyLocalPayDefaults().catch(e=>toast(e.message,true)));
$('paySetupTabs')&&$('paySetupTabs').addEventListener('click',e=>{
  const btn=e.target.closest('[data-pay-tab]');
  if(!btn) return;
  paySetupTab=btn.dataset.payTab;
  renderPaySetupForm();
  paySetupSetStatus('Editing '+paySetupTab+'. Fill fields → Save → Validate.', 'info');
});
$('settingsRefresh')&&$('settingsRefresh').addEventListener('click',()=>loadSettings().catch(e=>toast(e.message,true)));
document.querySelectorAll('#settingsTabs [data-settings-tab]').forEach(btn=>btn.addEventListener('click',()=>setSettingsTab(btn.dataset.settingsTab)));
$('contactQueueRefresh')&&$('contactQueueRefresh').addEventListener('click',()=>loadContactQueue({keepTab:true}).catch(e=>toast(e.message,true)));
$('contactQueueMode')&&$('contactQueueMode').addEventListener('change',()=>loadContactQueue({keepTab:true}).catch(e=>toast(e.message,true)));
$('contactQueueCountry')&&$('contactQueueCountry').addEventListener('change',()=>{
  if($('contactQueueCity')) $('contactQueueCity').value='';
  fillContactQueueLocationFilters(getContactQueueLocations(), ($('contactQueueCountry')&&$('contactQueueCountry').value)||'', '');
  loadContactQueue({keepTab:true}).catch(e=>toast(e.message,true));
});
$('contactQueueCity')&&$('contactQueueCity').addEventListener('change',()=>loadContactQueue({keepTab:true}).catch(e=>toast(e.message,true)));
$('contactQueueSaveMode')&&$('contactQueueSaveMode').addEventListener('click',()=>saveContactQueueMode().catch(e=>toast(e.message,true)));
$('contactQueueTabs')&&$('contactQueueTabs').addEventListener('click',e=>{
  const btn=e.target.closest('[data-cq-tab]');
  if(!btn) return;
  setContactQueueTab(btn.dataset.cqTab||'all');
  renderContactQueueList();
  // scroll handled inside setContactQueueTab
});
$('contactQueueChannel')&&$('contactQueueChannel').addEventListener('change',()=>{
  setContactQueueChannel(($('contactQueueChannel').value||''));
  renderContactQueueList();
});
$('contactQueueSearch')&&$('contactQueueSearch').addEventListener('input',()=>{
  setContactQueueQuery($('contactQueueSearch').value||'');
  syncContactQueueSearchUi();
  renderContactQueueList();
});
$('contactQueueSearchClear')&&$('contactQueueSearchClear').addEventListener('click',()=>{
  if($('contactQueueSearch')) $('contactQueueSearch').value='';
  setContactQueueQuery('');
  syncContactQueueSearchUi();
  renderContactQueueList();
  if($('contactQueueSearch')) $('contactQueueSearch').focus();
});
$('contactQueueMarkBtn')&&$('contactQueueMarkBtn').addEventListener('click',async()=>{
  const ids=[...getContactQueuePick()];
  if(!ids.length){ toast('Pick leads on All first',true); return; }
  try{
    await markContactQueueIds(ids, true);
    await loadContactQueue({keepTab:true});
    toast(ids.length+' added to To contact — staying on All');
  }catch(e){ toast(e.message,true); }
});
$('contactQueueClearPick')&&$('contactQueueClearPick').addEventListener('click',()=>{
  clearContactQueuePick();
  renderContactQueueList();
});
$('contactQueueMessageNext')&&$('contactQueueMessageNext').addEventListener('click',async()=>{
  const next=contactQueueReadyLeads()[0];
  if(!next){ toast('No leads on Ready to contact',true); return; }
  try{
    const preferred=(next.outreach_channel||'').trim()||preferredColdChannel(next);
    await openCompose(next, preferred);
  }catch(e){ toast(e.message,true); }
});
$('contactQueueClearReady')&&$('contactQueueClearReady').addEventListener('click',async()=>{
  const ids=contactQueueReadyLeads().map(b=>b.id);
  if(!ids.length) return;
  const ok=await uiConfirm({title:'Demote Ready list?', message:'Moves all Ready leads back to To contact prep (keeps channel + warm).', confirmLabel:'Demote all', danger:false});
  if(!ok) return;
  try{
    for(const id of ids){
      await api('/api/sales/prospects/'+id+'/demote-to-contact',{method:'POST',body:{}});
    }
    await loadContactQueue({keepTab:true});
    if(typeof B.loadPipeline==='function') B.loadPipeline().catch(()=>{});
    toast('Demoted to To contact');
  }catch(e){ toast(e.message,true); }
});
$('dealsRefresh')&&$('dealsRefresh').addEventListener('click',()=>loadDeals().catch(e=>toast(e.message,true)));
$('dealCreateBtn')&&$('dealCreateBtn').addEventListener('click',()=>sendPayLinkFlow().catch(e=>toast(e.message,true)));
$('dealsTabs')&&$('dealsTabs').addEventListener('click',e=>{
  const btn=e.target.closest('[data-deals-tab]');
  if(!btn) return;
  dealsTab=btn.dataset.dealsTab==='paylinks'?'paylinks':'proposals';
  document.querySelectorAll('#dealsTabs [data-deals-tab]').forEach(b=>b.classList.toggle('active',b.dataset.dealsTab===dealsTab));
  if($('dealsProposalsPane')) $('dealsProposalsPane').classList.toggle('hidden', dealsTab!=='proposals');
  if($('dealsPaylinksPane')) $('dealsPaylinksPane').classList.toggle('hidden', dealsTab!=='paylinks');
  if(dealsTab==='paylinks') renderPaylinksHost().catch(e=>toast(e.message,true));
});
$('paylinkCreateBtn')&&$('paylinkCreateBtn').addEventListener('click',()=>createPaylinksFlow().catch(e=>toast(e.message,true)));
$('paylinkRefreshBtn')&&$('paylinkRefreshBtn').addEventListener('click',()=>renderPaylinksHost().catch(e=>toast(e.message,true)));
/* brand logo + email signature wired in panels/settings.js */
$('sendPayLinkBtn')&&$('sendPayLinkBtn').addEventListener('click',()=>sendPayLinkFlow().catch(e=>toast(e.message,true)));
$('userAddBtn')&&$('userAddBtn').addEventListener('click',()=>addUserFlow().catch(e=>toast(e.message,true)));
$('territoryAddBtn')&&$('territoryAddBtn').addEventListener('click',()=>addTerritoryFlow().catch(e=>toast(e.message,true)));
$('enrichJobBtn')&&$('enrichJobBtn').addEventListener('click',async()=>{
  const ids=[...selectedIds].map(Number);
  const starred=rowsData.filter(b=>b.marked_for_contact).map(b=>b.id);
  const batch=ids.length?ids:starred;
  if(!batch.length){ toast('Select leads or star prospects first',true); return; }
  try{
    const job=await api('/api/sales/jobs',{method:'POST',body:{job_type:'enrich_batch',payload:{business_ids:batch}}});
    let tick=await api('/api/jobs/'+job.id+'/tick',{method:'POST',body:{}});
    while(!tick.done){ tick=await api('/api/jobs/'+job.id+'/tick',{method:'POST',body:{}}); }
    toast('Enrich job done · touched '+(tick.touched||0)+' / '+(tick.total||batch.length));
  }catch(e){ toast(e.message,true); }
});
$('autoSetupProspectsBtn')&&$('autoSetupProspectsBtn').addEventListener('click',async()=>{
  const ok=await uiConfirm({title:'Auto-setup all prospects?', message:'Backfills product line, currency, pipeline stage, and follow-up tasks for leads missing Sales OS fields.', confirmLabel:'Run auto-setup'});
  if(!ok) return;
  try{
    const res=await api('/api/sales/prospects/auto-setup',{method:'POST',body:{}});
    toast(`Auto-setup done · updated ${res.updated||0} / ${res.total||0}`);
  }catch(e){ toast(e.message,true); }
});
$('normalizeCurrencyBtn')&&$('normalizeCurrencyBtn').addEventListener('click',async()=>{
  const from=(($('currencyNormalizeFrom')&&$('currencyNormalizeFrom').value)||'AUD').toUpperCase();
  const to=(($('currencyNormalizeTo')&&$('currencyNormalizeTo').value)||'USD').toUpperCase();
  if(from===to){ toast('From and To are the same',true); return; }
  const fromLabel=from==='ALL'?'all currencies':(from+' / blank');
  const ok=await uiConfirm({
    title:`Convert ${fromLabel} → ${to}?`,
    message:from==='ALL'
      ?`Sets every prospect not already on ${to} to ${to}. You can still change any lead later in Sales → Currency.`
      :`Updates prospects on ${from} (and blank) to ${to}. Other currencies are left alone. Change a lead later in Sales → Currency.`,
    confirmLabel:`Convert → ${to}`
  });
  if(!ok) return;
  try{
    const qs=new URLSearchParams({from_currency:from, to_currency:to});
    const res=await api('/api/sales/prospects/normalize-currency?'+qs.toString(),{method:'POST',body:{}});
    toast(`Currency updated · ${res.updated||0} → ${res.to_currency||to} · ${res.kept||0} kept · ${res.total||0} total`);
    if(appPanel==='prospects') loadProspects().catch(()=>{});
  }catch(e){ toast(e.message,true); }
});
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='visible') showComposeConfirmReturn();
});
window.addEventListener('pageshow',()=>showComposeConfirmReturn());
window.addEventListener('focus',()=>showComposeConfirmReturn());
$('saveWonPolicy')&&$('saveWonPolicy').addEventListener('click',async()=>{
  try{
    const body={
      auto_won_on_be_paid:!!($('wonAutoOnPaid')&&$('wonAutoOnPaid').checked),
      require_fully_paid:!!($('wonRequireFullyPaid')&&$('wonRequireFullyPaid').checked),
      require_agreement_accepted:!!($('wonRequireAgreement')&&$('wonRequireAgreement').checked),
      require_initial_milestone_paid:!!($('wonRequireMilestone')&&$('wonRequireMilestone').checked),
    };
    await api('/api/sales/be/won-policy',{method:'PUT',body});
    toast('Won policy saved');
    await loadSettings();
  }catch(e){ toast(e.message,true); }
});
$('saveSaasFlag')&&$('saveSaasFlag').addEventListener('click',async()=>{
  try{
    const on=$('saasEnabledToggle').checked;
    await api('/api/sales/settings/saas_enabled?value='+encodeURIComponent(on?'true':'false'),{method:'PUT'});
    toast('SaaS flag saved');
    await loadSettings();
  }catch(e){toast(e.message||String(e),true)}
});
document.querySelectorAll('[data-snooze]').forEach(btn=>btn.addEventListener('click',()=>{
  const v=btn.dataset.snooze;
  $('composeFollowUp').value=v==='clear'?'':plusDays(Number(v));
  $('composeFollowUp').dataset.touched='1';
}));
$('composeFollowUp').addEventListener('change',()=>{$('composeFollowUp').dataset.touched='1'});
$('composeFollowUp').addEventListener('input',()=>{$('composeFollowUp').dataset.touched='1'});
$('importFile').addEventListener('change',async e=>{
  const file=e.target.files&&e.target.files[0];
  if(!file)return;
  const fd=new FormData();fd.append('file',file);
  try{
    const data=await api('/api/import.csv',{method:'POST',body:fd});
    const hints=Number(data.duplicate_hint_count||0);
    let msg=`Import complete: ${data.inserted} new, ${data.updated} updated, ${data.skipped} skipped.`;
    if(hints>0){
      const sample=(data.duplicate_hints||[]).slice(0,3).map(h=>h.name||h.place_id||'row').join(', ');
      msg+=` Soft duplicate hints: ${hints}${sample?` (e.g. ${sample})`:''}.`;
    }
    showMessage(msg);
    if(hints>0){
      toast(`${hints} possible duplicate(s)`);
      const hint=(data.duplicate_hints||[])[0];
      if(hint && (hint.existing_id||hint.matched_id)){
        const primary=Number(hint.existing_id||hint.matched_id);
        const secondary=Number(hint.new_id||0);
        const form=await uiForm({
          title:'Resolve duplicate',
          message:`${hint.name||'Match'} — primary #${primary}`+(secondary?` vs #${secondary}`:''),
          confirmLabel:'Apply',
          fields:[
            {name:'action', label:'Action', type:'select', value:'ignore', options:[
              {value:'ignore',label:'Ignore'},
              {value:'link',label:'Link both ways'},
              {value:'merge',label:'Merge into primary (archive other)'}
            ]},
            {name:'primary_id', label:'Primary ID', type:'number', required:true, value:String(primary)},
            {name:'secondary_id', label:'Secondary ID', type:'number', required:!!secondary, value:secondary?String(secondary):''}
          ]
        });
        if(form && form.secondary_id){
          await api('/api/sales/duplicates/resolve',{method:'POST',body:{
            action:form.action, primary_id:Number(form.primary_id), secondary_id:Number(form.secondary_id)
          }});
          toast('Duplicate '+form.action);
        }
      }
    }
    await load();
  }catch(err){showMessage(err.message,true)}
  e.target.value='';
});
$('saveViewBtn')&&$('saveViewBtn').addEventListener('click',async()=>{
  let name=($('savedViewName')&&$('savedViewName').value.trim())||'';
  if(!name){
    const form=await uiForm({
      title:'Save current filters',
      message:'Name this Discover / Pipeline filter set for quick recall.',
      confirmLabel:'Save view',
      fields:[{name:'name', label:'View name', required:true, placeholder:'e.g. AU barbers queue'}]
    });
    if(!form) return;
    name=form.name.trim();
  }
  const filters={
    product_line:($('pipelineLine')&&$('pipelineLine').value)||'STANDALONE',
    country:($('country')&&$('country').value)||'',
    city:($('city')&&$('city').value)||'',
    category:($('category')&&$('category').value)||'',
    status:($('statusFilter')&&$('statusFilter').value)||'all',
  };
  try{
    await api('/api/sales/saved-views',{method:'POST',body:{name,filters}});
    if($('savedViewName')) $('savedViewName').value='';
    toast('Saved view created');
    await loadSettings();
  }catch(e){ toast(e.message,true); }
});
$('searchForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const country=$('country').value.trim(),city=$('city').value.trim(),category=$('category').value.trim();
  if(!country||!city||!category)return;
  viewMode='city';
  document.querySelectorAll('[data-view]').forEach(btn=>btn.classList.toggle('active',btn.dataset.view==='city'));
  $('statusFilter').value=DISCOVER_STATUS;
  updateViewHint();
  $('searchBtn').disabled=true;$('searchBtn').innerHTML='<span class="loading"></span>Searching';
  const maxRaw=$('maxResults').value;
  const maxResults=Number(maxRaw);
  const fetchAll=!maxRaw||maxResults===0;
  showMessage(fetchAll
    ?'Scanning city map tiles until all Places results are collected (can take a minute)…'
    :`Searching Google Places (up to ${maxResults})…`);
  try{
    const d=await api('/api/search',{method:'POST',body:{country,city,category,max_results:fetchAll?0:maxResults}});
    const meta=d.search_meta||{};
    let tileNote='';
    if(meta.mode==='city_scan'){
      tileNote=` · ${meta.tiles||'?'} map tiles`;
      if(meta.tiles_with_hits!=null)tileNote+=` (${meta.tiles_with_hits} with hits)`;
    }else if(meta.note){
      tileNote=` · ${meta.note}`;
    }
    showMessage(`Search complete: ${d.found} found, ${d.inserted} new, ${d.updated} refreshed${tileNote}. Query: ${d.query}`);
    $('categoryFilter').value=category;
    await load();
    if((d.enrich_ids||[]).length){
      toast(`Enriching email/social for ${d.enrich_ids.length} sites in background…`);
      enrichIds(d.enrich_ids);
    }
  }catch(err){showMessage(err.message,true)}
  finally{$('searchBtn').disabled=false;$('searchBtn').textContent='Search Maps'}
});
$('rows').addEventListener('click',async e=>{
  const mark=e.target.closest('[data-mark]');
  if(mark){await toggleMark(mark.dataset.mark);return}
  const booking=e.target.closest('[data-booking]');
  if(booking){
    try{
      const on=booking.dataset.bookingOn==='1';
      await markBookingIds([booking.dataset.booking], on);
      const row=rowsData.find(x=>String(x.id)===String(booking.dataset.booking));
      if(row){
        row.current_booking_software=on?'Has booking':'';
        row.has_booking=!!on;
        if(on){ row.marked_for_contact=false; row.ready_for_contact=false; }
      }
      if(on){
        pruneFreshDiscoverRows();
        paint();
        setAppPanel('contactQueue');
        setContactQueueTab('booking');
        await loadContactQueue({keepTab:true});
        renderContactQueueList();
        toast('Moved to Has booking');
      }else if(row && isFreshDiscoverLead(row) && discoverShowingFresh()){
        await load();
        toast('Cleared booking — back in Discover');
      }else{
        paint();
        toast('Cleared booking flag');
      }
      loadStats();
    }catch(err){ toast(err.message,true); }
    return;
  }
  const igProf=e.target.closest('[data-open-ig-profile]');
  if(igProf){
    e.stopPropagation();
    const rowBiz=(rowsData.find(x=>String(x.id)===String(igProf.closest('[data-row]')?.dataset?.row))||{}).name||'';
    openInstagramProfile(igProf.dataset.openIgProfile||'', igProf.dataset.igHandle||'', igProf.dataset.igBiz||rowBiz);
    return;
  }
  const igDisc=e.target.closest('[data-ig-discover]');
  if(igDisc){
    e.stopPropagation();
    const id=igDisc.dataset.igDiscover;
    const b=rowsData.find(x=>String(x.id)===String(id));
    if(!b){ toast('Lead not found',true); return; }
    igDisc.disabled=true;
    igDisc.textContent='Finding IG…';
    try{
      const res=await ensureLeadInstagram(b,{force:true});
      if(res.found && res.instagram){
        applyDiscoveredIg(b, res.instagram);
        toast('Instagram found on website');
        paint();
      }else{
        toast('No Instagram link on website', true);
        igDisc.disabled=false;
        igDisc.textContent='Find IG';
      }
    }catch(err){
      toast(err.message||'IG discover failed', true);
      igDisc.disabled=false;
      igDisc.textContent='Find IG';
    }
    return;
  }
  const compose=e.target.closest('[data-compose]');
  if(compose){const b=rowsData.find(x=>String(x.id)===String(compose.dataset.compose));if(b)openCompose(b,compose.dataset.channel);return}
  const open=e.target.closest('[data-open]');
  if(open){openDrawer(open.dataset.open);return}
  const row=e.target.closest('[data-row]');
  if(row&&!e.target.closest('input,button,a')){
    const maps=(row.dataset.maps||'').trim();
    if(maps && (e.altKey||e.metaKey)){ window.open(maps,'_blank','noopener'); return; }
    if(maps && e.shiftKey){ window.open(maps,'_blank','noopener'); return; }
    // plain row click: Google Maps when URL known; Details still via button
    if(maps){ window.open(maps,'_blank','noopener'); return; }
    openDrawer(row.dataset.row);
  }
});
$('rows').addEventListener('change',e=>{
  const el=e.target;
  if(el.matches('input[data-select]')){
    const id=String(el.dataset.select);
    if(el.checked)selectedIds.add(id);else selectedIds.delete(id);
    updateSelectedCount();
  }
});
$('closeComposeBtn').addEventListener('click',closeCompose);
$('composeModal').addEventListener('click',e=>{if(e.target===$('composeModal'))closeCompose()});
$('composeChannel').addEventListener('change',()=>fillComposeTemplates($('composeChannel').value));
$('composeTemplate').addEventListener('change',applyComposeTemplate);
$('copyComposeBtn')&&$('copyComposeBtn').addEventListener('click',()=>copyComposeMessage().catch(e=>toast(e.message,true)));
$('openComposeBtn').addEventListener('click',openChannelApp);
$('copyComposeIconBtn')&&$('copyComposeIconBtn').addEventListener('click',()=>copyComposeMessage().catch(e=>toast(e.message,true)));
$('composeTabs')&&$('composeTabs').addEventListener('click',e=>{
  const btn=e.target.closest('[data-compose-tab]');
  if(!btn) return;
  setComposeTab(btn.dataset.composeTab);
});
$('composeMarkBtn')&&$('composeMarkBtn').addEventListener('click',async()=>{
  if(!composeLead||composeLead.marked_for_contact) return;
  try{
    await markContactQueueIds([composeLead.id], true);
    composeLead.marked_for_contact=true;
    syncComposeMarkBtn();
    toast('Marked to contact');
  }catch(e){ toast(e.message,true); }
});
$('confirmSentBtn').addEventListener('click',()=>confirmSent());
$('confirmNextBtn').addEventListener('click',()=>confirmSent({next:true}));
$('skipNextBtn').addEventListener('click',()=>{
  const id=composeLead&&composeLead.id;
  closeCompose();
  openNextLead(id);
});
$('closeDrawerBtn').addEventListener('click',closeDrawer);
$('drawer').addEventListener('click',e=>{if(e.target===$('drawer'))closeDrawer()});
$('drawerPanel').addEventListener('click',async e=>{
  const igProf=e.target.closest('[data-open-ig-profile]');
  if(igProf){
    e.preventDefault();
    openInstagramProfile(
      igProf.dataset.openIgProfile||'',
      igProf.dataset.igHandle||'',
      igProf.dataset.igBiz||(drawerLead&&drawerLead.name)||''
    );
    return;
  }
  const snooze=e.target.closest('[data-drawer-snooze]');
  if(snooze){$('drawerFollowUp').value=plusDays(Number(snooze.dataset.drawerSnooze));return}
  if(e.target.id==='saveDrawerBtn'){await saveDrawer();return}
  if(e.target.id==='saveSalesBtn'){
    if(!drawerLead)return;
    try{
      const body={
        deal_value:$('drawerDealValue').value===''?null:Number($('drawerDealValue').value),
        currency:$('drawerCurrency').value.trim()||'USD',
        next_action:$('drawerNextAction').value.trim(),
        lost_reason:$('drawerLostReason').value.trim(),
        opt_out:!!$('drawerOptOut').checked,
      };
      if($('drawerMrr')) body.mrr=$('drawerMrr').value===''?null:Number($('drawerMrr').value);
      await api('/api/sales/prospects/'+drawerLead.id,{method:'PATCH',body});
      Object.assign(drawerLead, body);
      toast('Sales fields saved');
    }catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='linkProspectBtn'){
    if(!drawerLead)return;
    const other=Number(($('drawerLinkedId')&&$('drawerLinkedId').value)||0);
    if(!other){ toast('Enter linked prospect ID',true); return; }
    try{
      await api('/api/sales/prospects/'+drawerLead.id+'/link',{method:'POST',body:{linked_prospect_id:other}});
      drawerLead.linked_prospect_id=other;
      toast('Prospects linked both ways');
      renderDrawer();
    }catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='undoStageBtn'){
    if(!drawerLead)return;
    try{
      const res=await api('/api/sales/prospects/'+drawerLead.id+'/undo-stage',{method:'POST',body:{}});
      if(res.prospect) Object.assign(drawerLead,res.prospect);
      toast('Stage undone');
      renderDrawer();
    }catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='scoreDrawerBtn'){
    if(!drawerLead)return;
    try{
      const {criteria,total}=readScoreCriteria();
      const res=await api('/api/sales/prospects/'+drawerLead.id+'/score',{method:'POST',body:criteria});
      drawerLead.qualification_score=res.computed_score;
      drawerLead.score_criteria=res.criteria||criteria;
      toast('Score '+res.display_score+'/10 · '+res.band);
      renderDrawer();
      refreshScorePreview();
      if(appPanel==='prospects') try{await loadProspects()}catch(_){}
    }catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='saveDiscoveryBtn'){
    if(!drawerLead)return;
    try{
      await api('/api/sales/prospects/'+drawerLead.id+'/discovery',{method:'POST',body:{painful_issue:$('drawerDiscoveryPain').value,next_action:$('drawerNextAction').value.trim()}});
      toast('Discovery saved · Contact queue will re-rank');
    }catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='saveDemoBtn'){
    if(!drawerLead)return;
    try{
      await api('/api/sales/prospects/'+drawerLead.id+'/demo',{method:'POST',body:{
        outcome:($('drawerDemoOutcome')&&$('drawerDemoOutcome').value)||'',
        agreed_next_step:($('drawerNextAction')&&$('drawerNextAction').value)||'',
        call_notes:($('drawerDiscoveryPain')&&$('drawerDiscoveryPain').value)||''
      }});
      toast('Demo note saved');
    }catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='beSendDeliveryBtn'){
    if(!drawerLead)return;
    try{
      const revs=await api('/api/sales/be/revisions');
      const rows=revs.revisions||[];
      if(!rows.length){
        toast('No finalized AppointHive packages yet. Finalize a package in AppointHive Product Factory, then retry.',true);
        return;
      }
      const form=await uiForm({
        title:'Send to AppointHive delivery',
        message:'Pay-ready handover. AppointHive owns cash + ZIP. Share the pay URL with the prospect.',
        confirmLabel:'Send',
        fields:[{name:'revision_id', label:'Package revision', type:'select', required:true, value:String(rows[0].productRevisionId||rows[0].id),
          options:rows.map(r=>({value:String(r.productRevisionId||r.id), label:`${r.label||r.productName||r.edition} · ${(Number(r.priceCents||0)/100).toFixed(2)} ${r.currency||'USD'}`}))}]
      });
      if(!form) return;
      const res=await api('/api/sales/be/send-to-delivery',{method:'POST',body:{business_id:drawerLead.id, product_revision_id:form.revision_id}});
      const url=res.pay_url||(res.delivery&&res.delivery.pay_url)||'';
      if(url){ try{ await navigator.clipboard.writeText(url); }catch(e){} window.open(url,'_blank','noopener'); }
      toast(res.label||'Sent to AppointHive — pay link ready');
      if(res.soft_intake_warning){
        toast('Soft intake warning: '+(String(res.soft_intake_warning).slice(0,160)), true);
      }
      await hydrateBeDrawer();
    }catch(err){ toast(err.message||'Could not send to AppointHive delivery',true); }
    return;
  }
  if(e.target.id==='beSendRequestBtn'){
    if(!drawerLead)return;
    try{
      const res=await api('/api/sales/be/send-request',{method:'POST',body:{business_id:drawerLead.id, message:''}});
      toast(res.label||'Awaiting AppointHive quote');
      await hydrateBeDrawer();
    }catch(err){ toast(err.message||'Could not send quote request to AppointHive',true); }
    return;
  }
  if(e.target.id==='beSendUpgradeBtn'){
    if(!drawerLead)return;
    try{
      let rows=beRevisionsCache;
      if(!rows.length){
        const revs=await api('/api/sales/be/revisions');
        rows=revs.revisions||[];
        beRevisionsCache=rows;
      }
      if(!rows.length){
        toast('No finalized AppointHive packages yet. Finalize a package in AppointHive Product Factory, then retry.',true);
        return;
      }
      const form=await uiForm({
        title:'Send upgrade via AppointHive',
        message:'Requires installation id from a prior paid install. Quote + checkout stay on AppointHive.',
        confirmLabel:'Send upgrade',
        fields:[{name:'revision_id', label:'Target revision', type:'select', required:true, value:String(rows[0].productRevisionId||rows[0].id),
          options:rows.map(r=>({value:String(r.productRevisionId||r.id), label:`${r.label||r.productName||r.edition} · ${(Number(r.priceCents||0)/100).toFixed(2)} ${r.currency||'USD'}`}))}]
      });
      if(!form) return;
      const res=await api('/api/sales/be/send-upgrade',{method:'POST',body:{business_id:drawerLead.id, to_revision_id:form.revision_id}});
      const url=res.pay_url||(res.delivery&&res.delivery.pay_url)||'';
      if(url){ try{ await navigator.clipboard.writeText(url); }catch(e){} window.open(url,'_blank','noopener'); }
      toast(res.label||'Upgrade sent to AppointHive');
      await hydrateBeDrawer();
    }catch(err){ toast(err.message||'Could not send upgrade to AppointHive',true); }
    return;
  }
  if(e.target.id==='beRefreshDeliveryBtn'){
    if(!drawerLead)return;
    try{
      const id=e.target.dataset.deliveryId;
      if(!id){ toast('No delivery to refresh yet — send to delivery first.',true); return; }
      await api('/api/sales/be/deliveries/'+id+'/refresh',{method:'POST',body:{}});
      toast('Delivery status refreshed');
      await hydrateBeDrawer();
    }catch(err){ toast(err.message||'Could not refresh delivery status',true); }
    return;
  }
  if(e.target.id==='drawerRefreshPaylinksBtn'){
    refreshDrawerPaylinks().catch(err=>toast(err.message,true));
    return;
  }
  if(e.target.closest&&e.target.closest('[data-drawer-pay-provider]')){
    const btn=e.target.closest('[data-drawer-pay-provider]');
    createDrawerPaylinkForProvider(btn.dataset.drawerPayProvider).catch(err=>toast(err.message,true));
    return;
  }
  if(e.target.id==='createProposalBtn'){
    if(!drawerLead)return;
    try{
      let connected=false;
      try{
        const conn=await api('/api/sales/be/connection');
        connected=!!(conn&&conn.connected);
        setBeConnCache(conn);
      }catch(e){}
      const line=(drawerLead.product_line||'STANDALONE').toUpperCase();
      if(connected && line==='STANDALONE'){
        const path=await uiForm({
          title:'AppointHive is connected',
          message:'Card checkout for STANDALONE should use drawer Delivery / pay → Send to delivery. You can still create a local Manual/Payoneer/Skrill proposal + pay link/QR.',
          confirmLabel:'Continue',
          fields:[{name:'path', label:'What do you want?', type:'select', value:'local', options:[
            {value:'local', label:'Local proposal (Manual / Payoneer / Skrill)'},
            {value:'be', label:'Open Delivery / pay (Send to delivery)'}
          ]}]
        });
        if(!path) return;
        if(path.path==='be'){ drawerTab='appointhive'; renderDrawer(); return; }
      }
      const plans=await api('/api/sales/plans?product_line='+encodeURIComponent(line));
      const list=plans.plans||[];
      if(!list.length){toast('No plans seeded for '+line,true);return}
      const growth=list.find(p=>(p.code||'').toLowerCase().includes('growth'))||list[0];
      const providerOpts=connected&&line==='STANDALONE'
        ?[{value:'manual',label:'Manual / bank'},{value:'payoneer',label:'Payoneer'},{value:'skrill',label:'Skrill'}]
        :[{value:'manual',label:'Manual / bank'},{value:'stripe',label:'Stripe'},{value:'paypal',label:'PayPal'},{value:'payoneer',label:'Payoneer'},{value:'skrill',label:'Skrill'}];
      const form=await uiForm({
        title:'Create proposal & invoice',
        message:`For ${drawerLead.name}. Creates proposal + invoice; pay link/QR also lands under Deals → Pay links.`,
        confirmLabel:'Create',
        fields:[
          {name:'plan_id', label:'Plan', type:'select', required:true, value:String(growth.id), options:list.map(p=>({value:String(p.id), label:p.name||p.code}))},
          {name:'provider', label:'Invoice provider', type:'select', value:'manual', options:providerOpts}
        ]
      });
      if(!form) return;
      const plan=list.find(p=>String(p.id)===String(form.plan_id))||growth;
      const prop=await api('/api/sales/proposals',{method:'POST',body:{
        business_id:drawerLead.id,title:plan.name+' for '+drawerLead.name,currency:drawerLead.currency||'USD',plan_id:plan.id,tax_rate:0,tax_label:'',terms_url:''
      }});
      await api('/api/sales/proposals/'+prop.id+'/send',{method:'POST',body:{}});
      const inv=await api('/api/sales/invoices',{method:'POST',body:{proposal_id:prop.id,provider:form.provider||'manual'}});
      if(inv.hosted_url && !String(inv.hosted_url).includes('/placeholder/')){
        try{ await navigator.clipboard.writeText(inv.hosted_url); }catch(e){}
        toast('Proposal '+prop.reference+' · pay link ready');
        window.open(inv.hosted_url,'_blank','noopener');
      }else toast('Proposal '+prop.reference+' · Invoice '+inv.number+' — QR & instructions below');
      const rows=paylinksStore();
      rows.unshift({
        id:'inv-'+(inv.id||Date.now()),
        invoice_id:inv.id,
        number:inv.number,
        business_id:drawerLead.id,
        prospect_name:drawerLead.name,
        business_name:drawerLead.name,
        provider:inv.provider||form.provider,
        url:inv.hosted_url||'',
        pay_instructions:inv.pay_instructions||'',
        status:inv.status,
        total:inv.total,
        currency:inv.currency,
        created_at:new Date().toISOString(),
        source:'invoice'
      });
      savePaylinksStore(rows);
      await refreshDrawerPaylinks();
      if(dealsTab==='paylinks') renderPaylinksHost().catch(()=>{});
      if(appPanel==='money') loadMoney().catch(()=>{});
    }catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='enrichDrawerBtn'){
    if(!drawerLead)return;
    const id=drawerLead.id;
    await enrichIds([id],{force:true});
    const refreshed=rowsData.find(x=>String(x.id)===String(id));
    if(refreshed){drawerLead=refreshed;renderDrawer();loadActivity(id)}
    return;
  }
  if(e.target.id==='archiveDrawerBtn'){
    if(!drawerLead)return;
    const id=drawerLead.id;
    try{
      await patchLead(id,{archived:true});
      closeDrawer();
      await load();
      undoToast('Archived.',async()=>{await api('/api/businesses/'+id+'/unarchive',{method:'POST'})});
    }catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='unarchiveDrawerBtn'){
    if(!drawerLead)return;
    try{await api('/api/businesses/'+drawerLead.id+'/unarchive',{method:'POST'});toast('Unarchived.');closeDrawer();await load()}catch(err){toast(err.message,true)}
    return;
  }
  if(e.target.id==='deleteDrawerBtn'){
    if(!drawerLead)return;
    const id=drawerLead.id;
    const ok=await uiConfirm({
      title:'Remove lead from active lists?',
      message:`“${drawerLead.name||'This lead'}” will be archived (soft delete). You can Undo from the toast, or Unarchive later.`,
      confirmLabel:'Archive lead',
      danger:true
    });
    if(!ok) return;
    try{
      await api('/api/businesses/'+id,{method:'DELETE'});
      closeDrawer();
      await load();
      undoToast('Lead removed (archived).',async()=>{await api('/api/businesses/'+id+'/unarchive',{method:'POST'})});
    }catch(err){toast(err.message,true)}
  }
});
$('uiDialogConfirm')&&$('uiDialogConfirm').addEventListener('click',()=>closeUiDialog(true));
$('uiDialogCancel')&&$('uiDialogCancel').addEventListener('click',()=>closeUiDialog(null));
$('uiDialog')&&$('uiDialog').addEventListener('click',e=>{ if(e.target===$('uiDialog')) closeUiDialog(null); });
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if($('uiDialog')&&!$('uiDialog').classList.contains('hidden')){ closeUiDialog(null); return; }
    if($('notifMenu')&&$('notifMenu').classList.contains('open')||$('profileMenu')&&$('profileMenu').classList.contains('open')){ closeTopMenus(); return; }
    if(!$('composeModal').classList.contains('hidden')){closeCompose();return}
    if(!$('drawer').classList.contains('hidden')){closeDrawer();return}
    return;
  }
  if($('uiDialog')&&!$('uiDialog').classList.contains('hidden')){
    if(e.key==='Enter' && e.target && e.target.tagName!=='TEXTAREA'){ e.preventDefault(); closeUiDialog(true); }
    return;
  }
  const typing=e.target&&(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT');
  if(typing)return;
  const modalOpen=!$('composeModal').classList.contains('hidden');
  if((e.key==='n'||e.key==='N')&&modalOpen){e.preventDefault();confirmSent({next:true});return}
  if((e.key==='s'||e.key==='S')&&modalOpen){e.preventDefault();const id=composeLead&&composeLead.id;closeCompose();openNextLead(id);return}
  if((e.key==='n'||e.key==='N')&&!modalOpen){e.preventDefault();openNextLead(drawerLead&&drawerLead.id)}
});
updateViewHint();

function setAuthed(username){
  document.body.classList.add('authed');
  const side=$('sidebar'); if(side) side.classList.add('is-open');
  closeSidebar();
  closeTopMenus();
  $('loginCard').classList.add('hidden');
  $('app').classList.remove('hidden');
  if($('headerPill')) $('headerPill').classList.add('hidden');
  setAppPanel('leads');
  loadProfileBar(username).catch(()=>{});
  refreshNotifications().catch(()=>{});
  refreshSignatureUi().catch(()=>{});
}
function setLoggedOut(){
  clearAuthTokens();
  document.body.classList.remove('authed');
  const side=$('sidebar'); if(side) side.classList.remove('is-open');
  closeSidebar();
  closeTopMenus();

  document.documentElement.classList.remove('templates-mode');
  document.body.classList.remove('templates-mode');
  $('app').classList.add('hidden');$('loginCard').classList.remove('hidden');
  if($('headerPill')) $('headerPill').classList.remove('hidden');
  if($('notifBadge')) $('notifBadge').classList.remove('show');
  $('password').value='';showLoginError('');
}
async function enterApp(username){
  setAuthed(username);
  await loadTemplates();
  setTplChannel('email');
  await loadLocations();
  refreshNotifications().catch(()=>{});
}
$('pwToggle')&&$('pwToggle').addEventListener('click',()=>{
  const input=$('password'); const btn=$('pwToggle'); if(!input||!btn) return;
  const show=input.type==='password';
  input.type=show?'text':'password';
  btn.textContent=show?'Hide':'Show';
  btn.setAttribute('aria-label', show?'Hide password':'Show password');
});
$('loginForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const username=$('username').value.trim(),password=$('password').value;
  if(!username||!password)return;
  $('loginBtn').disabled=true;showLoginError('');
  const prev=$('loginBtn').textContent;
  $('loginBtn').innerHTML='<span class="loading" aria-hidden="true"></span>Signing in…';
  try{
    const d=await api('/api/login',{method:'POST',body:{username,password,client:'web'}});
    setAuthTokens(d);
    await enterApp(d.username||username);
  }
  catch(err){showLoginError(err.message)}
  finally{$('loginBtn').disabled=false;$('loginBtn').textContent=prev||'Sign in'}
});
$('logoutBtn').addEventListener('click',async()=>{
  try{await logoutAuth()}catch(e){}
  clearAuthTokens();
  setLoggedOut();
});

/* --- Bridge wiring (Dependency Inversion for peeled modules) --- */
B.setAppPanel = setAppPanel;
B.openProspectById = openProspectById;
B.openDrawer = typeof openDrawer === 'function' ? openDrawer : null;
B.openCompose = typeof openCompose === 'function' ? openCompose : null;
B.refreshComposeTemplates = typeof refreshComposeTemplates === 'function' ? refreshComposeTemplates : null;
B.openWhatsAppChat = typeof openWhatsAppChat === 'function' ? openWhatsAppChat : null;
B.openInstagramProfile = typeof openInstagramProfile === 'function' ? openInstagramProfile : null;
B.getComposeLead = () => composeLead;
B.getContactQueueLeads = () => sharedState.contactQueueLeads || [];
B.loadSettings = typeof loadSettings === 'function' ? loadSettings : null;
B.refreshBeConnBanners = refreshBeConnBanners;
B.setMoneyTab = setMoneyTab;
B.setCommsTab = setCommsTab;
B.setAhTab = setAhTab;
B.setSettingsTab = setSettingsTab;
B.applyBeConnectOwnerGate = applyBeConnectOwnerGate;
B.loadBeCatalog = loadBeCatalog;
B.loadAppointHive = typeof loadAppointHive === 'function' ? loadAppointHive : null;
B.loadMoney = typeof loadMoney === 'function' ? loadMoney : null;
B.qrUrlFor = typeof qrUrlFor === 'function' ? qrUrlFor : null;
B.closeTopMenus = closeTopMenus;
B.availableComposeChannels = availableComposeChannels;
B.reloadDiscover = typeof load === 'function' ? load : null;
B.appPanel = appPanel;

/* --- Panel registry (SOLID Open/Closed) --- */
registerPanel({ id: 'guide', refresh: async () => {} });
/* dashboard registered in panels/dashboard.js */
registerPanel({ id: 'leads', refresh: safeRefresh(typeof load === 'function' ? load : null) });
/* contactQueue registered in panels/contact-queue.js */
/* pipeline registered in panels/pipeline.js */
/* prospects registered in panels/prospects.js */
/* tasks registered in panels/tasks.js */
registerPanel({ id: 'activities', refresh: safeRefresh(typeof loadActivitiesFeed === 'function' ? loadActivitiesFeed : null) });
/* demos registered in panels/demos.js */
/* templates registered in panels/templates.js */
registerPanel({ id: 'deals', refresh: safeRefresh(typeof loadDeals === 'function' ? loadDeals : null) });
registerPanel({ id: 'money', refresh: safeRefresh(typeof loadMoney === 'function' ? loadMoney : null) });
/* reports registered in panels/reports.js */
/* appoinHive registered in panels/appointhive.js */
registerPanel({ id: 'paySetup', refresh: safeRefresh(typeof loadPaySetup === 'function' ? loadPaySetup : null) });
registerPanel({ id: 'settings', refresh: safeRefresh(typeof loadSettings === 'function' ? loadSettings : null) });

(async function boot(){try{const d=await api('/api/me');await enterApp(d.username)}catch(e){setLoggedOut()}})();

