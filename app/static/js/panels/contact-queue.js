import { $ } from '../core/dom.js';

import { esc, api, toast, fmtMoney, fmtDateTime, dateOnly } from '../core/api.js';

import { pageSlice, renderPager, ensurePagerHost, pagerState } from '../core/pager.js';

import { listSkeletonHtml } from '../core/skeleton.js';

import { uiConfirm, uiForm } from '../core/dialog.js';

import { registerPanel } from './registry.js';

import { B } from '../app/bridge.js';

import { state } from '../core/state.js';

import {
  availableComposeChannels as composeChannels,
  channelLabel,
  contactChannels,
  preferredColdChannel,
  phoneDigits,
  instagramHandle,
  getIgOutreachQuota,
  igSoftSendBlockReason,
  refreshIgOutreachQuota,
} from '../features/compose.js';
import {
  cannotContactVia,
  cannotViaLabel,
  clearCannotContact,
  isCannotContact,
  markCannotContact,
} from '../features/cannot-contact.js';
import {
  alreadyConnectedPrimaryId,
  clearAlreadyConnected,
  fetchSameContactSiblings,
  isAlreadyConnected,
  markAlreadyConnected,
  markEngaged,
  setIgAwaitingFollowBack,
} from '../features/already-connected.js';
import { leadHasBooking as sharedLeadHasBooking } from '../features/lead-workflow.js';
import {
  applyDiscoveredIg,
  discoverInstagram,
  leadNeedsIgDiscover,
} from '../features/instagram-discover.js';
import { isFreshDiscoverLead } from './discover.js';

let contactQueueModes = [];
let contactQueuePick = new Set();
let contactQueueQuery = '';
let contactQueueChannel = '';
/** Contacted-tab only: '' | today | sent | plus3 | plus7 */
let contactQueueFollowFilter = '';
let contactQueueLocations = {};
let igDiscoverStarted = new Set();

function cqLocalDay(){
  const d=new Date();
  const y=d.getFullYear();
  const m=String(d.getMonth()+1).padStart(2,'0');
  const day=String(d.getDate()).padStart(2,'0');
  return `${y}-${m}-${day}`;
}

/** Days since last contact (0 = today). -1 if unknown. */
function daysSinceLastContact(b){
  const last=dateOnly(b&&b.last_contacted_at);
  if(!last) return -1;
  const a=new Date(last+'T12:00:00');
  const bDay=new Date(cqLocalDay()+'T12:00:00');
  return Math.floor((bDay-a)/(24*60*60*1000));
}

/**
 * Exclusive Contacted timing bucket (every contacted lead lands in exactly one):
 * today → sent/waiting (incl. 1–2d gap) → plus3 (due or 3–6d) → plus7 (due or 7d+).
 */
export function contactQueueFollowBucket(b){
  const today=cqLocalDay();
  const last=dateOnly(b&&b.last_contacted_at);
  const due=dateOnly(b&&b.next_follow_up_at);
  const kind=String((b&&b.follow_up_kind)||'').trim().toLowerCase();
  const since=daysSinceLastContact(b);
  const duePassed=!!due && due<=today;

  if(last===today) return 'today';
  if(kind==='call_today' && !duePassed) return 'today';

  // 7+ days since last contact, or plus7 kind that is due/overdue
  if(since>=7 || (kind==='plus7' && duePassed)) return 'plus7';

  // Due/overdue or 3–6 days since → +3 (default horizon)
  if(duePassed || since>=3) return 'plus3';

  // Future follow-up still waiting, or 1–2 days since send with no due yet
  return 'sent';
}

export function contactQueueFollowMatch(b, filter){
  if(!filter) return true;
  return contactQueueFollowBucket(b)===filter;
}

export function getContactQueuePick() { return contactQueuePick; }
export function clearContactQueuePick() { contactQueuePick.clear(); }

export function scrollContactQueueTop() {
  const main = document.querySelector('.main');
  if (main) {
    main.scrollTop = 0;
    try { main.scrollTo({ top: 0, behavior: 'auto' }); } catch (_) { /* ignore */ }
  }
  const list = $('contactQueueList');
  if (list) {
    try { list.scrollIntoView({ block: 'start', behavior: 'auto' }); } catch (_) { /* ignore */ }
  }
  try { window.scrollTo(0, 0); } catch (_) { /* ignore */ }
}

export function setContactQueueTab(tab) {
  if (tab === 'selected' || tab === 'ready' || tab === 'booking' || tab === 'contacted' || tab === 'cannot' || tab === 'connected') {
    state.contactQueueTab = tab;
  } else {
    state.contactQueueTab = 'all';
  }
  requestAnimationFrame(() => {
    scrollContactQueueTop();
    setTimeout(scrollContactQueueTop, 40);
  });
}

/** Ensure Contact queue panel is open on the given tab (after Ready / Has booking). */
async function goContactQueueTab(tab){
  setContactQueueTab(tab);
  if(state.appPanel!=='contactQueue' && typeof B.setAppPanel==='function'){
    B.setAppPanel('contactQueue');
  }
  await loadContactQueue({keepTab:true});
  renderContactQueueList();
}

/** Patch CQ row stage from promote/demote/confirm API prospect payload. */
function patchQueueLeadStage(row, prospect){
  if(!row || !prospect) return;
  if(prospect.stage) row.stage = prospect.stage;
  if(prospect.ready_for_contact != null) row.ready_for_contact = !!prospect.ready_for_contact;
  if(prospect.marked_for_contact != null) row.marked_for_contact = !!prospect.marked_for_contact;
  if(prospect.contacted != null) row.contacted = !!prospect.contacted;
  if(prospect.outreach_channel != null) row.outreach_channel = prospect.outreach_channel;
}

/** Keep Pipeline kanban in sync after CQ tab moves (flags + stage). */
function refreshPipelineAfterQueueMove(){
  if(typeof B.loadPipeline === 'function'){
    B.loadPipeline().catch(()=>{});
  }
}
export function setContactQueueQuery(q) { contactQueueQuery = q || ''; }
export function setContactQueueChannel(ch) { contactQueueChannel = ch || ''; }
export function setContactQueueFollowFilter(v) {
  const allowed = new Set(['', 'today', 'sent', 'plus3', 'plus7']);
  contactQueueFollowFilter = allowed.has(v || '') ? (v || '') : '';
}
export function getContactQueueFollowFilter() { return contactQueueFollowFilter; }
export function getContactQueueLocations() { return contactQueueLocations; }

function availableComposeChannels(b) {
  if (typeof B.availableComposeChannels === 'function') return B.availableComposeChannels(b);
  return composeChannels(b);
}


function leadHasBooking(b){
  return sharedLeadHasBooking(b);
}

/** Prep queue: marked, not ready, not contacted, not parks. */
export function contactQueueSelectedLeads(){
  return (state.contactQueueLeads||[]).filter(b=>
    b.marked_for_contact && !b.ready_for_contact && !b.contacted && !b.opt_out && !isCannotContact(b)
    && !leadHasBooking(b) && !isAlreadyConnected(b)
    && !(b.contacted || b.email_sent || b.whatsapp_sent || b.instagram_sent)
  );
}

/** Messaging queue: ready_for_contact and not yet contacted / parked. */
export function contactQueueReadyLeads(){
  return (state.contactQueueLeads||[]).filter(b=>
    b.ready_for_contact && !b.contacted && !b.opt_out && !isCannotContact(b)
    && !leadHasBooking(b) && !isAlreadyConnected(b)
    && !(b.email_sent || b.whatsapp_sent || b.instagram_sent)
  );
}

/** Marked businesses whose first cold message was confirmed sent. */
export function contactQueueContactedLeads(){
  return (state.contactQueueLeads||[]).filter(b=>{
    if (b.opt_out || isCannotContact(b) || isAlreadyConnected(b)) return false;
    return !!(b.contacted || b.email_sent || b.whatsapp_sent || b.instagram_sent);
  });
}

export function contactQueueConnectedLeads(){
  return (state.contactQueueLeads||[]).filter(b=>!b.opt_out && isAlreadyConnected(b));
}

export function availableOutreachChannels(b){
  // Match server _channel_facts + compose contactChannels (WA needs ≥8 digits).
  const c=contactChannels(b);
  return ['whatsapp','instagram','email'].filter(ch=>c[ch]);
}

/** Warm complete from ticks (do not trust a stale ig_warm_complete flag alone). Cap never applies here. */
export function igWarmTicksComplete(b){
  if(!b) return false;
  const followOnly = !!(b.ig_warm_follow_only || b.contacted || b.instagram_sent);
  if(followOnly) return !!b.ig_warm_follow;
  return !!(b.ig_warm_follow && (b.ig_warm_like || b.ig_warm_comment));
}

export function canPromoteReady(b){
  // IG soft-DM daily cap must NEVER block To contact → Ready (Confirm sent only).
  const ch=(b.outreach_channel||'').trim().toLowerCase();
  if(!ch) return {ok:false, reason:'Pick WhatsApp, Instagram, or Email first'};
  const avail=availableOutreachChannels(b);
  if(!avail.includes(ch)) return {ok:false, reason:`No ${ch} on this lead`};
  if(ch==='instagram'){
    if(!igWarmTicksComplete(b)){
      if(!b.ig_warm_follow){
        return {ok:false, reason:'Tick Follow (required) — Like+Comment alone is not enough'};
      }
      return {ok:false, reason:'Tick Like or Comment (need Follow + 1)'};
    }
  }
  return {ok:true, reason:''};
}

/** Keep queue channel flags aligned after business PATCH (response has no `channels`). */
function syncQueueLeadFromBusiness(row, updated){
  if(!row||!updated) return;
  Object.assign(row, updated);
  const c=contactChannels(row);
  row.channels={ email:!!c.email, whatsapp:!!c.whatsapp, instagram:!!c.instagram };
  row.has_booking=leadHasBooking(row);
}

/** Unreachable on WA / IG / both — dedicated Can't contact tab. */
export function contactQueueCannotLeads(){
  return (state.contactQueueLeads||[]).filter(b=>!b.opt_out && isCannotContact(b));
}

export function fillContactQueueLocationFilters(locations, keepCountry, keepCity){
  const countrySel=$('contactQueueCountry');
  const citySel=$('contactQueueCity');
  if(!countrySel||!citySel) return;
  contactQueueLocations=locations||{};
  const countries=Object.keys(contactQueueLocations).sort((a,b)=>a.localeCompare(b));
  const curCountry=keepCountry!=null?keepCountry:(countrySel.value||'');
  countrySel.innerHTML='<option value="">All countries</option>'+countries.map(c=>`<option value="${esc(c)}" ${c===curCountry?'selected':''}>${esc(c)}</option>`).join('');
  if(curCountry && ![...countrySel.options].some(o=>o.value===curCountry)) countrySel.value='';
  const activeCountry=countrySel.value;
  const cities=activeCountry
    ? (contactQueueLocations[activeCountry]||[]).slice().sort((a,b)=>a.localeCompare(b))
    : [...new Set(Object.values(contactQueueLocations).flat())].sort((a,b)=>a.localeCompare(b));
  const curCity=keepCity!=null?keepCity:(citySel.value||'');
  citySel.innerHTML='<option value="">All cities</option>'+cities.map(c=>`<option value="${esc(c)}" ${c===curCity?'selected':''}>${esc(c)}</option>`).join('');
  if(curCity && ![...citySel.options].some(o=>o.value===curCity)) citySel.value='';
}

export function contactQueueMatchQuery(b, q){
  if(!q) return true;
  const ch=b.channels||{};
  const hay=[
    b.name, b.city, b.country, b.category, b.email, b.phone, b.website, b.why,
    b.stage&&b.stage.name, b.stage&&b.stage.slug,
    ch.email?'email':'', ch.whatsapp?'whatsapp phone':'', ch.instagram?'instagram':''
  ].join(' ').toLowerCase();
  return q.split(/\s+/).filter(Boolean).every(tok=>hay.includes(tok));
}

export function contactQueueBookingLeads(){
  return (state.contactQueueLeads||[]).filter(b=>leadHasBooking(b));
}

export function contactQueueChannelMatch(b, ch){
  if(!ch) return true;
  const c=b.channels||{};
  const hasEmail=!!(c.email || b.email);
  const hasWa=!!(c.whatsapp || b.phone || b.international_phone);
  const hasIg=!!c.instagram;
  const hasWeb=!!(b.website||'').trim();
  // On Ready, hide IG follow-back holds when focusing WA/Email send work.
  if((ch==='email'||ch==='whatsapp') && state.contactQueueTab==='ready' && b.ig_awaiting_follow_back){
    return false;
  }
  if(ch==='email') return hasEmail;
  if(ch==='whatsapp') return hasWa;
  if(ch==='instagram') return hasIg;
  if(ch==='website') return hasWeb;
  if(ch==='no_email') return !hasEmail;
  if(ch==='no_website') return !hasWeb;
  if(ch==='ig_only') return hasIg && !hasEmail && !hasWa;
  return true;
}

export function contactQueueIsParked(b){
  // Marked / ready / booking / can't-contact / already-connected → sink to bottom of All tab
  return !!(b&&(b.marked_for_contact || b.ready_for_contact || leadHasBooking(b) || isCannotContact(b) || isAlreadyConnected(b)));
}

export function contactQueueVisibleLeads(){
  let base;
  if(state.contactQueueTab==='selected') base=contactQueueSelectedLeads();
  else if(state.contactQueueTab==='ready') base=contactQueueReadyLeads();
  else if(state.contactQueueTab==='booking') base=contactQueueBookingLeads();
  else if(state.contactQueueTab==='contacted') base=contactQueueContactedLeads();
  else if(state.contactQueueTab==='cannot') base=contactQueueCannotLeads();
  else if(state.contactQueueTab==='connected') base=contactQueueConnectedLeads();
  else base=(state.contactQueueLeads||[]).slice();
  // All: ranked open leads first; prep/ready/booking parked at bottom
  if(state.contactQueueTab==='all'){
    base.sort((a,b)=>{
      const aPark=contactQueueIsParked(a);
      const bPark=contactQueueIsParked(b);
      if(aPark!==bPark) return aPark?1:-1;
      return (Number(b.rank_score)||0)-(Number(a.rank_score)||0);
    });
  } else if(state.contactQueueTab==='contacted' || state.contactQueueTab==='cannot' || state.contactQueueTab==='ready' || state.contactQueueTab==='connected'){
    base.sort((a,b)=>{
      const aAt=a.updated_at||a.last_contacted_at||'';
      const bAt=b.updated_at||b.last_contacted_at||'';
      if(aAt!==bAt) return aAt<bAt?1:-1;
      return (Number(b.rank_score)||0)-(Number(a.rank_score)||0);
    });
  }
  const ch=contactQueueChannel||(($('contactQueueChannel')&&$('contactQueueChannel').value)||'');
  if(ch) base=base.filter(b=>contactQueueChannelMatch(b,ch));
  if(state.contactQueueTab==='contacted' && contactQueueFollowFilter){
    base=base.filter(b=>contactQueueFollowMatch(b, contactQueueFollowFilter));
  }
  const q=(contactQueueQuery||'').trim().toLowerCase();
  if(!q) return base;
  return base.filter(b=>contactQueueMatchQuery(b,q));
}

export function fillContactQueueModes(modes, active){
  const sel=$('contactQueueMode');
  if(!sel) return;
  contactQueueModes=modes||[];
  const cur=active||sel.value||'default';
  sel.innerHTML=contactQueueModes.map(m=>{
    const id=m.id||m.slug;
    const label=m.label||m.name||id;
    return `<option value="${esc(id)}" ${id===cur?'selected':''}>${esc(label)}</option>`;
  }).join('');
  if(cur && [...sel.options].some(o=>o.value===cur)) sel.value=cur;
}

export function renderContactQueueExplain(data){
  const box=$('contactQueueExplain');
  if(!box) return;
  const mode=contactQueueModes.find(m=>m.id===(data.mode||''))||{};
  const parts=(data.leads||[]).slice(0,3).map((b,i)=>`#${i+1} ${b.name||'—'} (${b.rank_score}) — ${b.why||'—'}`);
  const shown=esc(String(data.sample_size||0));
  const total=esc(String(data.total_candidates||0));
  box.innerHTML=`
    <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start">
      <div style="min-width:0;flex:1 1 220px">
        <div class="sub" style="margin:0 0 4px;text-transform:uppercase;letter-spacing:.04em;font-weight:700;font-size:11px">Ranking balance</div>
        <strong style="color:var(--text);font-size:15px">${esc(data.mode_label||mode.label||'Ranking')}</strong>
        <p class="hint" style="margin:6px 0 0">${esc(data.definition||mode.description||'')}</p>
      </div>
      <div style="display:flex;gap:18px;flex:0 0 auto">
        <div><div class="sub" style="margin:0">Showing</div><strong style="font-size:22px;letter-spacing:-.03em">${shown}</strong></div>
        <div><div class="sub" style="margin:0">In scope</div><strong style="font-size:22px;letter-spacing:-.03em">${total}</strong></div>
      </div>
    </div>
    <p class="hint" style="margin:12px 0 0"><strong style="color:var(--text)">Workflow:</strong> All → Mark to contact → pick channel (+ IG warm 2/3) → Ready to contact → Message → Confirm sent. Can't contact parks unreachable.</p>
    ${igCapBannerHtml()}
    ${parts.length?`<p class="hint" style="margin:8px 0 0"><strong style="color:var(--text)">Top why:</strong> ${esc(parts.join(' · '))}</p>`:''}
  `;
}

/** Cap blocks IG Confirm sent only — promote To contact → Ready stays allowed. */
function igCapBannerHtml(){
  const q=getIgOutreachQuota();
  if(!(q&&q.remaining<=0)) return '';
  const tz=q.timezone?` · ${esc(q.timezone)}`:'';
  return `<p class="hint" style="margin:8px 0 0" role="status"><strong style="color:var(--danger, #b42318)">IG soft DM daily cap hit</strong> (${esc(String(q.used))}/${esc(String(q.cap))}${tz}). You can still move <strong>To contact → Ready</strong>; Confirm sent unlocks when the daily quota resets.</p>`;
}

export function syncContactQueueSearchUi(){
  const wrap=$('contactQueueSearchWrap');
  const inp=$('contactQueueSearch');
  if(!wrap||!inp) return;
  wrap.classList.toggle('has-query', !!(inp.value||'').trim());
}

export function syncContactQueueTabs(){
  const q=(contactQueueQuery||'').trim().toLowerCase();
  const ch=contactQueueChannel||(($('contactQueueChannel')&&$('contactQueueChannel').value)||'');
  const follow=contactQueueFollowFilter||(($('contactQueueFollowFilter')&&$('contactQueueFollowFilter').value)||'');
  const pass=(list, {applyFollow=false}={})=>list.filter(b=>{
    if(ch && !contactQueueChannelMatch(b,ch)) return false;
    if(applyFollow && follow && !contactQueueFollowMatch(b, follow)) return false;
    if(q && !contactQueueMatchQuery(b,q)) return false;
    return true;
  });
  const allBase=state.contactQueueLeads||[];
  const selBase=contactQueueSelectedLeads();
  const readyBase=contactQueueReadyLeads();
  const bookBase=contactQueueBookingLeads();
  const contactedBase=contactQueueContactedLeads();
  const cannotBase=contactQueueCannotLeads();
  const connectedBase=contactQueueConnectedLeads();
  if($('contactQueueAllCount')) $('contactQueueAllCount').textContent=String(pass(allBase).length);
  if($('contactQueueSelectedCount')) $('contactQueueSelectedCount').textContent=String(pass(selBase).length);
  if($('contactQueueReadyCount')) $('contactQueueReadyCount').textContent=String(pass(readyBase).length);
  if($('contactQueueBookingCount')) $('contactQueueBookingCount').textContent=String(pass(bookBase).length);
  if($('contactQueueContactedCount')) $('contactQueueContactedCount').textContent=String(pass(contactedBase, {applyFollow:true}).length);
  if($('contactQueueCannotCount')) $('contactQueueCannotCount').textContent=String(pass(cannotBase).length);
  if($('contactQueueConnectedCount')) $('contactQueueConnectedCount').textContent=String(pass(connectedBase).length);
  const followField=$('contactQueueFollowField');
  if(followField) followField.hidden=state.contactQueueTab!=='contacted';
  document.querySelectorAll('#contactQueueTabs [data-cq-tab]').forEach(btn=>{
    const on=btn.dataset.cqTab===state.contactQueueTab;
    btn.classList.toggle('active',on);
    btn.setAttribute('aria-selected', on?'true':'false');
  });
  const allBar=$('contactQueueBulkBar');
  const selBar=$('contactQueueSelectedBar');
  const readyBar=$('contactQueueReadyBar');
  if(allBar) allBar.classList.toggle('show', state.contactQueueTab==='all' && contactQueuePick.size>0);
  if(selBar) selBar.classList.toggle('show', state.contactQueueTab==='selected' && selBase.length>0);
  if(readyBar) readyBar.classList.toggle('show', state.contactQueueTab==='ready' && readyBase.length>0);
  if($('contactQueuePickCount')) $('contactQueuePickCount').textContent=contactQueuePick.size+' picked';
  if($('contactQueueSelectedHint')) $('contactQueueSelectedHint').textContent=selBase.length?`${selBase.length} in prep · pick channel, warm IG, then Ready`:'No leads in To contact yet';
  if($('contactQueueReadyHint')) $('contactQueueReadyHint').textContent=readyBase.length?`${readyBase.length} ready · message from here`:'No leads ready to contact yet';
  if($('contactQueueMessageNext')) $('contactQueueMessageNext').disabled=!readyBase.length;
}

export function mapsUrlFor(b){
  const direct=(b&&b.google_maps_url||'').trim();
  if(direct) return direct;
  const q=[b&&b.name,b&&b.address,b&&b.city,b&&b.country].filter(Boolean).join(' ').trim();
  if(!q) return '';
  return 'https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(q);
}

export function channelTagsHtml(b){
  const ch=b.channels||{};
  const tags=[
    {on:!!(ch.email||b.email), cls:'email', label:'#email'},
    {on:!!(ch.whatsapp||b.phone||b.international_phone), cls:'wa', label:'#whatsapp'},
    {on:!!ch.instagram, cls:'ig', label:'#instagram'},
    {on:!!(b.website||'').trim(), cls:'web', label:'#website'},
    {on:!!(b.has_booking||(b.current_booking_software||'').trim()), cls:'web', label:'#booking'}
  ];
  return tags.map(t=>`<span class="ch-tag ${t.cls}${t.on?'':' off'}">${t.label}</span>`).join(' ');
}

function igReadyWaitChipHtml(b){
  const wait=b&&b.ig_ready_wait;
  if(!wait||!wait.applies||wait.unlocked!==false) return '';
  const tip=esc(wait.reason||'Warming — soft ask not unlocked yet');
  return `<span class="ch-tag" title="${tip}">${tip}</span>`;
}

function composeChosenChannelButtonHtml(b){
  const ch=(b.outreach_channel||'').trim().toLowerCase();
  if(!ch) return '';
  const short={whatsapp:'WA',email:'Email',instagram:'IG'};
  const label=short[ch]||channelLabel(ch);
  // Messaging gates only (parks / hold / Ready wait / daily cap / warm) — never promote-Ready.
  const blockReason=igSoftSendBlockReason(b, ch)||(b.can_message===false?(b.can_message_reason||'Cannot message yet'):'')||'';
  const tip=blockReason
    ? esc(blockReason)
    : `Open ${channelLabel(ch)} compose`;
  return `<button type="button" class="primary" data-compose-queue="${b.id}" data-compose-channel="${ch}" data-block-reason="${esc(blockReason)}" ${blockReason?'disabled':''} title="${tip}">${label}</button>`;
}

/** Open existing Contacts → Instagram URL field (validated + locked on save). */
function prepAddIgHtml(b){
  const c=contactChannels(b);
  if(c.instagram) return '';
  return `<button type="button" data-cq-add-ig="${b.id}" title="Opens Contacts → Instagram URL (validated before save)">Add IG</button>`;
}

function prepChannelPickerHtml(b){
  const avail=availableOutreachChannels(b);
  const cur=(b.outreach_channel||'').trim().toLowerCase();
  const labels={whatsapp:'WA',instagram:'IG',email:'Email'};
  if(!avail.length){
    const findIg=(b.website||'').trim()
      ? `<button type="button" data-cq-ig-discover="${b.id}" title="Find Instagram on website">Find IG</button>`
      : '';
    return `<span class="cq-prep-hint" title="Add IG opens Contacts → Instagram URL, or Find IG on website">No WA / IG / Email — Add IG or Open → Contacts</span>${findIg}`;
  }
  const pills=avail.map(ch=>{
    const on=cur===ch?'primary':'';
    return `<button type="button" class="${on}" data-cq-channel="${b.id}" data-channel="${ch}" title="Outreach via ${channelLabel(ch)}">${labels[ch]||ch}</button>`;
  }).join('');
  const findIg=!avail.includes('instagram') && (b.website||'').trim()
    ? `<button type="button" data-cq-ig-discover="${b.id}" title="Find Instagram on website">${igDiscoverStarted.has(String(b.id))?'Finding IG…':'Find IG'}</button>`
    : '';
  return pills+findIg;
}

function prepWarmChecklistHtml(b){
  if((b.outreach_channel||'').trim().toLowerCase()!=='instagram') return '';
  const ticks=[
    {f:'ig_warm_follow', lab:'Follow'},
    {f:'ig_warm_like', lab:'Like'},
    {f:'ig_warm_comment', lab:'Comment'},
  ];
  return `<span class="cq-warm-inline" title="Follow + like or comment">${ticks.map(t=>
    `<label class="cq-warm-tick"><input type="checkbox" data-cq-warm="${b.id}" data-warm-field="${t.f}" ${b[t.f]?'checked':''}> ${t.lab}</label>`
  ).join('')}</span>`;
}

function prepReadyButtonHtml(b){
  const gate=canPromoteReady(b);
  const tip=gate.ok
    ? 'Move to Ready to contact (daily IG soft-DM cap does not block this)'
    : (gate.reason||'Finish channel + IG warm first');
  // Keep clickable when blocked so toast always surfaces the real gate (not silent disabled).
  const blockedCls=gate.ok?'':' is-blocked';
  const hint=gate.ok?'':`<span class="cq-gate-hint" role="status">${esc(gate.reason||'Not ready')}</span>`;
  return `<button type="button" class="primary${blockedCls}" data-cq-promote="${b.id}" data-gate-ok="${gate.ok?'1':'0'}" data-gate-reason="${esc(gate.reason||'')}" aria-disabled="${gate.ok?'false':'true'}" title="${esc(tip)}">Ready to contact</button>${hint}`;
}

/** Open WA / Open IG for prep (warm + reachability) — not compose/messaging. */
function prepOpenAppsHtml(b){
  const c=contactChannels(b);
  const bits=[];
  if(c.whatsapp){
    bits.push(`<button type="button" data-cq-open-wa="${b.id}" title="Open WhatsApp (web or app)">Open WA</button>`);
  }
  if(c.instagram){
    bits.push(`<button type="button" data-cq-open-ig="${b.id}" title="Open Instagram profile (web or app)">Open IG</button>`);
  }
  return bits.join('');
}

async function promptCannotContact(id){
  const form=await uiForm({
    title:"Can't contact",
    message:'Park this lead when WhatsApp, Instagram, or both are unreachable. They move to the Can\'t contact tab.',
    confirmLabel:'Save',
    fields:[
      {
        name:'via',
        label:'Channel',
        type:'select',
        required:true,
        value:'both',
        options:[
          {value:'whatsapp',label:'WhatsApp only'},
          {value:'instagram',label:'Instagram only'},
          {value:'both',label:'WhatsApp + Instagram'},
        ],
      },
      {
        name:'notes',
        label:'Note (optional)',
        type:'textarea',
        rows:3,
        placeholder:'e.g. WA number invalid · IG DMs closed',
      },
    ],
  });
  if(!form) return;
  try{
    const updated=await markCannotContact(id, { via: form.via, notes: form.notes||'' });
    const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
    if(row && updated) Object.assign(row, updated);
    else if(row){
      row.cannot_contact=true;
      row.cannot_contact_via=form.via;
      row.marked_for_contact=true;
      row.contacted=false;
    }
    await loadContactQueue({keepTab:true});
    toast(`Can't contact · ${cannotViaLabel(form.via)}`);
  }catch(e){ toast(e.message,true); }
}

async function promptAlreadyConnected(id){
  let siblings=[];
  try{
    const res=await fetchSameContactSiblings(id);
    siblings=res&&res.siblings||[];
  }catch(_){ siblings=[]; }
  const options=siblings.map(s=>{
    const shared=(s.shared_channels||[]).join('+')||'shared';
    const where=[s.city,s.country].filter(Boolean).join(', ');
    const flag=s.contacted?' · contacted':'';
    return {
      value:String(s.id),
      label:`#${s.id} ${s.name||'—'}${where?` · ${where}`:''} (${shared})${flag}`,
    };
  });
  options.push({value:'__manual__',label:'Enter primary ID manually…'});
  const form=await uiForm({
    title:'Already connected',
    message:'Same brand, other location — shared IG / WhatsApp / email already used on a primary. Parks this Ready lead.',
    confirmLabel:'Park',
    fields:[
      {
        name:'primary_pick',
        label:'Primary lead',
        type:'select',
        required:true,
        value:options[0]?options[0].value:'__manual__',
        options:options.length?options:[{value:'__manual__',label:'Enter primary ID manually…'}],
      },
      {
        name:'primary_id',
        label:'Primary ID (if manual)',
        type:'number',
        placeholder:'Business id of the location you already message',
      },
      {
        name:'notes',
        label:'Note (optional)',
        type:'textarea',
        rows:2,
        placeholder:'e.g. same IG as Downtown location',
      },
    ],
  });
  if(!form) return;
  let primaryId=form.primary_pick==='__manual__'?Number(form.primary_id):Number(form.primary_pick);
  if(!primaryId||!Number.isFinite(primaryId)){
    toast('Pick or enter a primary lead id',true);
    return;
  }
  try{
    const updated=await markAlreadyConnected(id, { primary_id: primaryId, notes: form.notes||'' });
    const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
    if(row){
      row.already_connected=true;
      row.already_connected_primary_id=primaryId;
      row.ready_for_contact=false;
      row.can_message=false;
      if(updated&&updated.prospect) Object.assign(row, updated.prospect, {
        already_connected:true,
        already_connected_primary_id:primaryId,
        ready_for_contact:false,
      });
    }
    await goContactQueueTab('connected');
    toast(`Already connected → primary #${primaryId}`);
  }catch(e){ toast(e.message,true); }
}

function queueDiscoverInstagramForVisible(leads){
  const need=(leads||[]).filter((b)=>leadNeedsIgDiscover(b) && !igDiscoverStarted.has(String(b.id))).slice(0, 8);
  if(!need.length) return;
  const onQueueTab=state.contactQueueTab==='selected' || state.contactQueueTab==='ready';
  need.forEach((b)=>{
    const id=String(b.id);
    igDiscoverStarted.add(id);
    discoverInstagram(b.id).then((res)=>{
      const row=state.contactQueueLeads.find((x)=>String(x.id)===id);
      if(res.found && res.instagram && row) applyDiscoveredIg(row, res.instagram);
      else igDiscoverStarted.delete(id);
      if(onQueueTab) renderContactQueueList();
    }).catch(()=>{
      igDiscoverStarted.delete(id);
      if(onQueueTab) renderContactQueueList();
    });
  });
  if(onQueueTab) renderContactQueueList();
}

function sentChannelsHtml(b){
  const bits=[];
  if(b.whatsapp_sent) bits.push('<span class="ch-tag wa">WA sent</span>');
  if(b.email_sent) bits.push('<span class="ch-tag email">Email sent</span>');
  if(b.instagram_sent) bits.push('<span class="ch-tag ig">IG sent</span>');
  if(!bits.length && b.contacted) bits.push('<span class="badge contacted">Contacted</span>');
  return bits.join(' ');
}

export function renderContactQueueList(){
  const list=$('contactQueueList');
  if(!list) return;
  syncContactQueueTabs();
  const isSelected=state.contactQueueTab==='selected';
  const isReady=state.contactQueueTab==='ready';
  const isBooking=state.contactQueueTab==='booking';
  const isContacted=state.contactQueueTab==='contacted';
  const isCannot=state.contactQueueTab==='cannot';
  const isConnected=state.contactQueueTab==='connected';
  const leads=contactQueueVisibleLeads();
  const q=(contactQueueQuery||'').trim();
  const pagerHost=ensurePagerHost(list,'contactQueuePager');
  const tabLabel=isSelected?'To contact':(isReady?'Ready to contact':(isBooking?'Has booking':(isContacted?'Contacted':(isCannot?"Can't contact":(isConnected?'Already connected':'All')))));
  if(!leads.length){
    if(q || contactQueueChannel || (isContacted && contactQueueFollowFilter)){
      list.innerHTML=`<div class="empty-card">No matches on <strong>${tabLabel}</strong> for this search/filter.</div>`;
    }else{
      list.innerHTML=isSelected
        ? '<div class="empty-card">Nothing in prep yet. On <strong>All</strong>, pick leads and use <strong>Mark to contact</strong>.</div>'
        : isReady
          ? '<div class="empty-card">Nothing ready. On <strong>To contact</strong>, pick a channel (and IG warm 2/3), then <strong>Ready to contact</strong>.</div>'
        : isBooking
          ? '<div class="empty-card">No leads marked as already having booking. Use <strong>Has booking</strong> on Discover or All.</div>'
          : isContacted
            ? '<div class="empty-card">No confirmed sends yet. From <strong>Ready to contact</strong>, message then <strong>Confirm sent</strong>. Use <strong>Follow-up +3 / +7</strong> here after first send.</div>'
          : isCannot
            ? '<div class="empty-card">No parked leads. On <strong>To contact</strong> or <strong>Ready</strong>, use <strong>Can\'t contact</strong> when unreachable.</div>'
          : isConnected
            ? '<div class="empty-card">No already-connected parks. On <strong>Ready</strong>, use <strong>Already connected</strong> when the shared IG/WA/email is another location.</div>'
          : '<div class="empty-card">No leads match this ranking mode. Try Balanced, or Discover + star prospects first.</div>';
    }
    if(pagerHost) pagerHost.innerHTML='';
    return;
  }
  const slice=pageSlice(leads, 'contactQueue:'+state.contactQueueTab);
  list.innerHTML=slice.items.map((b,i)=>{
    const whyParts=(b.why_parts&&b.why_parts.length)?b.why_parts:String(b.why||'').split(',').map(s=>s.trim()).filter(Boolean);
    const chips=whyParts.map(w=>`<span class="chip ok">${esc(w)}</span>`).join('');
    const picked=contactQueuePick.has(String(b.id));
    const already=!!b.marked_for_contact;
    const isReadyLead=!!b.ready_for_contact;
    const hasBook=!!(b.has_booking||(b.current_booking_software||'').trim());
    const rankNo=slice.start+i+1;
    const mapsUrl=mapsUrlFor(b);
    const noCheck=isSelected||isReady||isBooking||isContacted||isCannot||isConnected;
    const checkCell=noCheck?'':`<div class="checkcol"><input type="checkbox" data-cq-pick="${b.id}" ${picked?'checked':''} aria-label="Pick ${esc(b.name||'lead')}"></div>`;
    const holdOn=!!b.ig_awaiting_follow_back;
    let actions='';
    if(isSelected){
      actions=`
        <div class="cq-prep">${prepChannelPickerHtml(b)} ${prepAddIgHtml(b)} ${prepWarmChecklistHtml(b)}</div>
        ${prepOpenAppsHtml(b)}
        ${prepReadyButtonHtml(b)}
        <button type="button" data-cq-booking="${b.id}" data-booking-on="1">Has booking</button>
        <button type="button" data-cq-cannot="${b.id}">Can't contact</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>
        <button type="button" data-cq-unmark="${b.id}">Remove</button>`;
    }else if(isReady){
      actions=`
        ${igReadyWaitChipHtml(b)}
        ${holdOn?'<span class="ch-tag" title="Waiting for follow-back">Awaiting follow-back</span>':''}
        ${composeChosenChannelButtonHtml(b)}
        <button type="button" data-cq-already-connected="${b.id}">Already connected</button>
        <button type="button" data-cq-ig-hold="${b.id}" data-awaiting="${holdOn?'0':'1'}">${holdOn?'Clear follow-back hold':'Awaiting follow-back'}</button>
        <button type="button" data-cq-booking="${b.id}" data-booking-on="1">Has booking</button>
        <button type="button" data-cq-demote="${b.id}">Demote</button>
        <button type="button" data-cq-cannot="${b.id}">Can't contact</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>`;
    }else if(isBooking){
      actions=`
        <button type="button" data-open-prospect="${b.id}">Open</button>
        <button type="button" data-cq-booking="${b.id}" data-booking-on="0">Clear booking</button>`;
    }else if(isContacted){
      const badge=b.follow_up_badge&&b.follow_up_badge.label
        ?`<span class="ch-tag" title="Follow-up">${esc(b.follow_up_badge.label)}</span>`
        :'';
      const preferredCh=esc((b.outreach_channel||'').trim()||preferredColdChannel(b)||'');
      actions=`
        ${badge}
        <button type="button" class="primary" data-compose-queue="${b.id}" data-compose-channel="${preferredCh}" title="Open compose (keeps current follow-up date)">Message</button>
        <button type="button" data-cq-follow-send="${b.id}" data-follow-days="3" title="Compose follow-up and schedule +3 days — Confirm sent re-flags automatically">Follow-up +3</button>
        <button type="button" data-cq-follow-send="${b.id}" data-follow-days="7" title="Compose follow-up and schedule +7 days — Confirm sent re-flags automatically">Follow-up +7</button>
        <button type="button" data-cq-engaged="${b.id}">Engaged</button>
        <button type="button" data-cq-revert="${b.id}">Back to Ready</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>`;
    }else if(isCannot){
      const via=cannotViaLabel(cannotContactVia(b));
      actions=`
        <button type="button" class="primary" data-cq-clear-cannot="${b.id}">Back to queue</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>
        <span class="ch-tag" title="Unreachable">${esc(via)}</span>`;
    }else if(isConnected){
      const prim=alreadyConnectedPrimaryId(b);
      actions=`
        <button type="button" class="primary" data-cq-clear-connected="${b.id}">Back to queue</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>
        ${prim?`<span class="ch-tag" title="Primary">Primary #${esc(String(prim))}</span>`:''}`;
    }else{
      const markLabel=isReadyLead?'On Ready':(already?'On To contact':'Mark to contact');
      actions=`
        <button type="button" class="primary" data-cq-mark="${b.id}" ${already||isReadyLead||isAlreadyConnected(b)?'disabled':''}>${markLabel}</button>
        <button type="button" data-cq-booking="${b.id}" data-booking-on="${hasBook?'0':'1'}">${hasBook?'Clear booking':'Has booking'}</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>`;
    }
    const contactedBadge=isContacted?` ${sentChannelsHtml(b)}`:'';
    const cannotBadge=isCannot?` <span class="badge">${esc(cannotViaLabel(cannotContactVia(b)))}</span>`:(isCannotContact(b)&&!isSelected&&!isReady?` <span class="badge">Can't contact</span>`:'');
    const connectedBadge=isConnected||isAlreadyConnected(b)?` <span class="badge">Connected</span>`:'';
    const stageBadge=isReadyLead&&!isReady&&!isContacted&&!isCannot&&!isConnected?' <span class="badge queue">Ready</span>'
      :(already&&!isSelected&&!isReady&&!isContacted&&!isCannot&&!isConnected?' <span class="badge queue">To contact</span>':'');
    const warmGate=isSelected&&(b.outreach_channel||'')==='instagram'?canPromoteReady(b):null;
    const warmChip=warmGate
      ?(warmGate.ok
        ?' <span class="cq-warm-chip done" title="Warm ready (Follow + like or comment)">Warm ✓</span>'
        :` <span class="cq-warm-chip" title="${esc(warmGate.reason||'Need Follow + like or comment')}">Warm –</span>`)
      :'';
    return `
    <article class="queue-lead ${noCheck?'no-check':''}" data-row="${b.id}" data-maps="${esc(mapsUrl)}" title="${mapsUrl?'Open Google Maps':''}">
      ${checkCell}
      <div class="queue-rank meta" style="min-width:48px;align-items:flex-start"><strong style="font-size:18px">#${rankNo}</strong><span class="sub">${esc(String(b.rank_score))}</span></div>
      <div class="queue-body">
        <div class="biz">${esc(b.name||'—')}${stageBadge}${hasBook?' <span class="badge">Booking</span>':''}${warmChip}${contactedBadge}${cannotBadge}${connectedBadge}</div>
        <div class="sub"><span>${esc(b.country||'—')}</span> · <span>${esc(b.city||'—')}</span> · ${esc((b.stage&&b.stage.name)||'—')}${(b.outreach_channel)?` · ${esc(b.outreach_channel)}`:''}</div>
        <div class="reach" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px">${channelTagsHtml(b)}</div>
        <div class="reach" style="margin-top:6px">${chips||`<span class="chip">${esc(b.why||'ranked')}</span>`}</div>
      </div>
      <div class="actions">${actions}
      </div>
    </article>`;
  }).join('');
  renderPager(pagerHost, slice, renderContactQueueList);
  queueDiscoverInstagramForVisible(slice.items);
  list.querySelectorAll('[data-cq-pick]').forEach(inp=>inp.addEventListener('change',()=>{
    const id=String(inp.dataset.cqPick);
    if(inp.checked) contactQueuePick.add(id); else contactQueuePick.delete(id);
    syncContactQueueTabs();
  }));
  list.querySelectorAll('[data-open-prospect]').forEach(btn=>btn.addEventListener('click',ev=>{
    ev.stopPropagation();
    B.openProspectById(btn.dataset.openProspect);
  }));
  list.querySelectorAll('[data-compose-queue]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    if(btn.disabled){
      const reason=(btn.dataset.blockReason||btn.title||'').trim();
      toast(
        reason
          ? (/daily cap/i.test(reason)
            ? `${reason}. To contact → Ready still works — messaging unlocks when the daily quota resets.`
            : reason)
          : 'Cannot open message yet',
        true,
      );
      return;
    }
    const id=btn.dataset.composeQueue;
    const lead=state.contactQueueLeads.find(x=>String(x.id)===String(id));
    if(!lead){ toast('Lead not in queue',true); return; }
    const preferred=preferredColdChannel(lead, btn.dataset.composeChannel||'');
    try{
      await B.openCompose(lead, preferred);
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-follow-send]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const id=btn.dataset.cqFollowSend;
    const days=Number(btn.dataset.followDays||3);
    const lead=state.contactQueueLeads.find(x=>String(x.id)===String(id));
    if(!lead){ toast('Lead not in queue',true); return; }
    if(typeof B.openCompose!=='function'){ toast('Compose unavailable',true); return; }
    const preferred=(lead.outreach_channel||'').trim()||preferredColdChannel(lead);
    try{
      await B.openCompose(lead, preferred, { followUpDays: days, preferFollowUp: true });
      toast(`Follow-up +${days} preset — Confirm sent re-flags and schedules automatically`);
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-open-wa]').forEach(btn=>btn.addEventListener('click',ev=>{
    ev.stopPropagation();
    const lead=state.contactQueueLeads.find(x=>String(x.id)===String(btn.dataset.cqOpenWa));
    if(!lead){ toast('Lead not in queue',true); return; }
    const digits=phoneDigits(lead);
    if(digits.length<8){ toast('No phone on this lead',true); return; }
    if(typeof B.openWhatsAppChat==='function') B.openWhatsAppChat(digits, '');
    else toast('WhatsApp open unavailable',true);
  }));
  list.querySelectorAll('[data-cq-open-ig]').forEach(btn=>btn.addEventListener('click',ev=>{
    ev.stopPropagation();
    const lead=state.contactQueueLeads.find(x=>String(x.id)===String(btn.dataset.cqOpenIg));
    if(!lead){ toast('Lead not in queue',true); return; }
    const ig=((lead.social_links||{}).instagram)||'';
    const handle=instagramHandle(ig);
    if(!ig && !handle){ toast('No Instagram on this lead',true); return; }
    if(typeof B.openInstagramProfile==='function'){
      B.openInstagramProfile(ig, handle, lead.name||'');
    }else toast('Instagram open unavailable',true);
  }));
  list.querySelectorAll('[data-cq-mark]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    if(btn.disabled) return;
    try{
      await markContactQueueIds([btn.dataset.cqMark], true);
      await loadContactQueue({keepTab:true});
      toast('Added to To contact — staying on All');
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-unmark]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    try{
      const id=btn.dataset.cqUnmark;
      const before=state.contactQueueLeads.find(x=>String(x.id)===String(id));
      await markContactQueueIds([id], false);
      await loadContactQueue({keepTab:true});
      const back=isFreshDiscoverLead({...(before||{}), marked_for_contact:false});
      toast(back?'Removed from To contact — back in Discover':'Removed from To contact');
      if(back && typeof B.reloadDiscover==='function') B.reloadDiscover().catch(()=>{});
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-revert]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const ok=await uiConfirm({
      title:'Back to Ready?',
      message:'Clears Contacted / channel-sent flags and returns this lead to Ready to contact (keeps channel + warm).',
      confirmLabel:'Move back',
      danger:false
    });
    if(!ok) return;
    try{
      await api('/api/sales/prospects/'+btn.dataset.cqRevert+'/revert-to-contact',{method:'POST',body:{}});
      await loadContactQueue({keepTab:true});
      toast('Moved back to Ready to contact');
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-channel]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const id=btn.dataset.cqChannel;
    const ch=btn.dataset.channel;
    try{
      const updated=await api('/api/businesses/'+id,{method:'PATCH',body:{outreach_channel:ch}});
      const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
      if(row&&updated) syncQueueLeadFromBusiness(row, updated);
      else if(row){
        row.outreach_channel=ch;
        if(ch==='instagram'&&!canPromoteReady({...row,outreach_channel:ch}).ok) row.ready_for_contact=false;
        syncQueueLeadFromBusiness(row, row);
      }
      renderContactQueueList();
      toast('Channel: '+channelLabel(ch));
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-warm]').forEach(inp=>inp.addEventListener('change',async ev=>{
    ev.stopPropagation();
    const id=inp.dataset.cqWarm;
    const field=inp.dataset.warmField;
    const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
    // Optimistic tick + recompute complete so Ready unlocks immediately (avoids PATCH race).
    if(row){
      row[field]=!!inp.checked;
      row.ig_warm_complete=igWarmTicksComplete(row);
    }
    try{
      const body={[field]:!!inp.checked};
      const updated=await api('/api/businesses/'+id,{method:'PATCH',body});
      if(row&&updated){
        // Merge server warm flags but keep local tick just set (outrunning responses).
        row.ig_warm_follow = field==='ig_warm_follow' ? !!inp.checked : !!updated.ig_warm_follow;
        row.ig_warm_like = field==='ig_warm_like' ? !!inp.checked : !!updated.ig_warm_like;
        row.ig_warm_comment = field==='ig_warm_comment' ? !!inp.checked : !!updated.ig_warm_comment;
        row.ig_warm_follow_only = !!updated.ig_warm_follow_only;
        row.ig_warm_complete = igWarmTicksComplete(row);
        if(updated.outreach_channel!=null) row.outreach_channel=updated.outreach_channel;
        syncQueueLeadFromBusiness(row, {
          ...updated,
          ig_warm_follow: row.ig_warm_follow,
          ig_warm_like: row.ig_warm_like,
          ig_warm_comment: row.ig_warm_comment,
          ig_warm_complete: row.ig_warm_complete,
        });
      }
      renderContactQueueList();
    }catch(e){
      inp.checked=!inp.checked;
      if(row){
        row[field]=!!inp.checked;
        row.ig_warm_complete=igWarmTicksComplete(row);
      }
      toast(e.message,true);
      renderContactQueueList();
    }
  }));
  list.querySelectorAll('[data-cq-promote]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const id=btn.dataset.cqPromote;
    const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
    const gate=canPromoteReady(row||{});
    if(!gate.ok || btn.dataset.gateOk==='0'){
      toast(gate.reason || btn.dataset.gateReason || 'Finish channel + IG warm first', true);
      return;
    }
    try{
      const res=await api('/api/sales/prospects/'+id+'/promote-ready',{method:'POST',body:{}});
      if(row){
        row.ready_for_contact=true;
        row.marked_for_contact=true;
        if(res&&res.outreach_channel!=null) row.outreach_channel=res.outreach_channel;
        if(res&&res.ig_ready_at!=null) row.ig_ready_at=res.ig_ready_at;
        if(res&&res.ig_ready_wait) row.ig_ready_wait=res.ig_ready_wait;
        if(res&&res.prospect) patchQueueLeadStage(row, res.prospect);
      }
      await goContactQueueTab('ready');
      refreshPipelineAfterQueueMove();
      const wait=res&&res.ig_ready_wait;
      const ch=((row&&row.outreach_channel)||(res&&res.outreach_channel)||'').toLowerCase();
      const q=getIgOutreachQuota();
      if(wait&&wait.applies&&wait.unlocked===false){
        toast(wait.reason||'On Ready — soft ask unlocks after wait');
      }else if(ch==='instagram' && q.remaining<=0){
        toast('Moved to Ready — IG daily soft DM cap hit; messaging unlocks when the daily quota resets');
      }else{
        toast('Moved to Ready to contact');
      }
    }catch(e){ toast(e.message||'Could not move to Ready',true); }
  }));
  list.querySelectorAll('[data-cq-demote]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    try{
      const id=btn.dataset.cqDemote;
      const res=await api('/api/sales/prospects/'+id+'/demote-to-contact',{method:'POST',body:{}});
      const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
      if(row && res && res.prospect) patchQueueLeadStage(row, res.prospect);
      await goContactQueueTab('selected');
      refreshPipelineAfterQueueMove();
      toast('Demoted to To contact');
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-booking]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    try{
      const on=btn.dataset.bookingOn==='1';
      const id=btn.dataset.cqBooking;
      const before=state.contactQueueLeads.find(x=>String(x.id)===String(id));
      await markBookingIds([id], on);
      if(on){
        await goContactQueueTab('booking');
        toast('Moved to Has booking');
      }else{
        await loadContactQueue({keepTab:true});
        renderContactQueueList();
        const back=isFreshDiscoverLead({
          ...(before||{}),
          current_booking_software:'',
          has_booking:false,
          marked_for_contact:false,
        });
        toast(back?'Cleared booking — back in Discover':'Cleared booking flag');
        if(back && typeof B.reloadDiscover==='function') B.reloadDiscover().catch(()=>{});
      }
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-cannot]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    await promptCannotContact(btn.dataset.cqCannot);
  }));
  list.querySelectorAll('[data-cq-clear-cannot]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const ok=await uiConfirm({
      title:'Back to To contact?',
      message:'Clears can\'t-contact and returns this lead to To contact.',
      confirmLabel:'Move back',
    });
    if(!ok) return;
    try{
      const updated=await clearCannotContact(btn.dataset.cqClearCannot, true);
      const row=state.contactQueueLeads.find(x=>String(x.id)===String(btn.dataset.cqClearCannot));
      if(row && updated) Object.assign(row, updated);
      await loadContactQueue({keepTab:true});
      toast('Moved back to To contact');
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-already-connected]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    await promptAlreadyConnected(btn.dataset.cqAlreadyConnected);
  }));
  list.querySelectorAll('[data-cq-clear-connected]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const ok=await uiConfirm({
      title:'Back to To contact?',
      message:'Clears already-connected and returns this lead to To contact.',
      confirmLabel:'Move back',
    });
    if(!ok) return;
    try{
      const updated=await clearAlreadyConnected(btn.dataset.cqClearConnected, true);
      const row=state.contactQueueLeads.find(x=>String(x.id)===String(btn.dataset.cqClearConnected));
      if(row && updated) Object.assign(row, updated);
      await goContactQueueTab('selected');
      toast('Moved back to To contact');
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-ig-hold]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const id=btn.dataset.cqIgHold;
    const awaiting=btn.dataset.awaiting==='1';
    try{
      const res=await setIgAwaitingFollowBack(id, awaiting);
      const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
      if(row){
        row.ig_awaiting_follow_back=!!(res&&res.ig_awaiting_follow_back);
        if(res&&res.can_message!=null) row.can_message=!!res.can_message;
        if(res&&res.can_message_reason!=null) row.can_message_reason=res.can_message_reason||'';
        if(res&&res.ig_ready_wait) row.ig_ready_wait=res.ig_ready_wait;
      }
      renderContactQueueList();
      toast(awaiting?'Held — awaiting IG follow-back':'Follow-back hold cleared');
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-engaged]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const id=btn.dataset.cqEngaged;
    try{
      const form=await uiForm({
        title:'Mark Engaged',
        message:'Use when they reply or show real interest (does not skip from discovery/score alone).',
        confirmLabel:'Engaged',
        fields:[{name:'note',label:'Note (optional)',type:'text'}],
      });
      if(!form) return;
      await markEngaged(id, form.note||'');
      await loadContactQueue({keepTab:true});
      refreshPipelineAfterQueueMove();
      toast('Marked Engaged');
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-ig-discover]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const id=btn.dataset.cqIgDiscover;
    btn.disabled=true;
    btn.textContent='Finding IG…';
    try{
      const res=await discoverInstagram(id, { force:true });
      if(res.found && res.instagram){
        const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
        if(row){
          applyDiscoveredIg(row, res.instagram);
          // Match manual Add IG: put lead on IG process so warm / Open IG unlock.
          try{
            const updated=await api('/api/businesses/'+id,{method:'PATCH',body:{outreach_channel:'instagram'}});
            if(updated) syncQueueLeadFromBusiness(row, updated);
            else row.outreach_channel='instagram';
          }catch(_){
            row.outreach_channel='instagram';
          }
        }
        toast('Instagram found — IG channel on');
        renderContactQueueList();
        return;
      }
      toast('No Instagram link on website — paste URL in Add IG', true);
      btn.textContent='No IG';
    }catch(e){
      toast(e.message||'IG discover failed', true);
      btn.disabled=false;
      btn.textContent='Find IG';
    }
  }));
  list.querySelectorAll('[data-cq-add-ig]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const id=btn.dataset.cqAddIg;
    try{
      if(typeof B.openProspectById==='function'){
        await B.openProspectById(id, { tab:'contacts', focus:'drawerInstagram' });
      }else{
        toast('Open Contacts to add Instagram URL', true);
      }
    }catch(e){ toast(e.message||'Could not open Contacts', true); }
  }));
  list.querySelectorAll('article.queue-lead').forEach(row=>row.addEventListener('click',ev=>{
    if(ev.target.closest('button,a,input,label,.actions,.checkcol')) return;
    const url=(row.dataset.maps||'').trim();
    if(url) window.open(url,'_blank','noopener');
    else toast('No map location for this lead',true);
  }));
}

export async function markContactQueueIds(ids, marked){
  const list=(ids||[]).map(Number).filter(Boolean);
  if(!list.length) return;
  await api('/api/businesses/bulk',{method:'POST',body:{ids:list, marked_for_contact:!!marked}});
  list.forEach(id=>{
    const row=state.contactQueueLeads.find(x=>Number(x.id)===Number(id));
    if(row) row.marked_for_contact=!!marked;
    contactQueuePick.delete(String(id));
  });
}

export async function markBookingIds(ids, hasBooking){
  const list=(ids||[]).map(Number).filter(Boolean);
  if(!list.length) return;
  const value=hasBooking?'Has booking':'';
  for(const id of list){
    // Server clears marked/ready when booking is set so lead leaves To contact / Ready.
    await api('/api/sales/prospects/'+id,{method:'PATCH',body:{current_booking_software:value}});
    const row=state.contactQueueLeads.find(x=>Number(x.id)===Number(id));
    if(row){
      row.current_booking_software=value;
      row.has_booking=!!hasBooking;
      if(hasBooking){
        row.marked_for_contact=false;
        row.ready_for_contact=false;
      }
    }
  }
}

export async function loadContactQueue(opts={}){
  if(!opts.keepTab){ /* preserve tab when refreshing after mark */ }
  const list=$('contactQueueList');
  const pagerHost=list&&ensurePagerHost(list,'contactQueuePager');
  if(list){
    list.innerHTML=listSkeletonHtml({rows:5, label:'Loading contact queue…'});
    if(pagerHost) pagerHost.innerHTML='';
  }
  const mode=($('contactQueueMode')&&$('contactQueueMode').value)||'';
  const country=($('contactQueueCountry')&&$('contactQueueCountry').value)||'';
  const city=($('contactQueueCity')&&$('contactQueueCity').value)||'';
  const qs=new URLSearchParams({product_line:'STANDALONE',limit:'0'});
  if(mode) qs.set('mode', mode);
  if(country) qs.set('country', country);
  if(city) qs.set('city', city);
  const data=await api('/api/sales/contact-queue?'+qs.toString());
  await refreshIgOutreachQuota().catch(()=>{});
  fillContactQueueModes(data.modes||[], data.mode||'default');
  fillContactQueueLocationFilters(data.locations||{}, country, city);
  const scope=[country,city].filter(Boolean).join(' · ');
  if($('contactQueueDef')){
    $('contactQueueDef').textContent=(data.definition||'')
      +(scope?` · Scoped to ${scope}.`:' · All countries/cities combined — pick a country then city to separate.')
      +` · All → Mark to contact → channel (+ IG warm) → Ready → Message → Confirm sent.`;
  }
  renderContactQueueExplain(data);
  state.contactQueueLeads=data.leads||[];
  igDiscoverStarted.clear();
  // Drop picks that are no longer in the ranked list
  const alive=new Set(state.contactQueueLeads.map(b=>String(b.id)));
  [...contactQueuePick].forEach(id=>{ if(!alive.has(id)) contactQueuePick.delete(id); });
  if($('contactQueueSearch') && contactQueueQuery && $('contactQueueSearch').value!==contactQueueQuery){
    $('contactQueueSearch').value=contactQueueQuery;
  }
  if(pagerState['contactQueue:'+state.contactQueueTab]) pagerState['contactQueue:'+state.contactQueueTab].page=1;
  syncContactQueueSearchUi();
  renderContactQueueList();
}

export async function saveContactQueueMode(){
  const mode=($('contactQueueMode')&&$('contactQueueMode').value)||'default';
  await api('/api/sales/settings/contact_queue_mode?value='+encodeURIComponent(mode),{method:'PUT'});
  toast('Ranking mode saved');
  await loadContactQueue({keepTab:true});
}


registerPanel({ id: 'contactQueue', refresh: async () => { await loadContactQueue(); } });

