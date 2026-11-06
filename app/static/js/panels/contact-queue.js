import { $ } from '../core/dom.js';

import { esc, api, toast, fmtMoney, fmtDateTime } from '../core/api.js';

import { pageSlice, renderPager, ensurePagerHost, pagerState } from '../core/pager.js';

import { listSkeletonHtml } from '../core/skeleton.js';

import { uiConfirm, uiForm } from '../core/dialog.js';

import { registerPanel } from './registry.js';

import { B } from '../app/bridge.js';

import { state } from '../core/state.js';

import { availableComposeChannels as composeChannels, channelLabel, preferredColdChannel } from '../features/compose.js';
import {
  cannotContactVia,
  cannotViaLabel,
  clearCannotContact,
  isCannotContact,
  markCannotContact,
} from '../features/cannot-contact.js';
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
let contactQueueLocations = {};
let igDiscoverStarted = new Set();

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
  if (tab === 'selected' || tab === 'ready' || tab === 'booking' || tab === 'contacted' || tab === 'cannot') {
    state.contactQueueTab = tab;
  } else {
    state.contactQueueTab = 'all';
  }
  requestAnimationFrame(() => {
    scrollContactQueueTop();
    setTimeout(scrollContactQueueTop, 40);
  });
}
export function setContactQueueQuery(q) { contactQueueQuery = q || ''; }
export function setContactQueueChannel(ch) { contactQueueChannel = ch || ''; }
export function getContactQueueLocations() { return contactQueueLocations; }

function availableComposeChannels(b) {
  if (typeof B.availableComposeChannels === 'function') return B.availableComposeChannels(b);
  return composeChannels(b);
}


/** Prep queue: marked, not ready, not contacted. */
export function contactQueueSelectedLeads(){
  return (state.contactQueueLeads||[]).filter(b=>
    b.marked_for_contact && !b.ready_for_contact && !b.contacted && !b.opt_out && !isCannotContact(b)
    && !(b.contacted || b.email_sent || b.whatsapp_sent || b.instagram_sent)
  );
}

/** Messaging queue: ready_for_contact and not yet contacted. */
export function contactQueueReadyLeads(){
  return (state.contactQueueLeads||[]).filter(b=>
    b.ready_for_contact && !b.contacted && !b.opt_out && !isCannotContact(b)
    && !(b.email_sent || b.whatsapp_sent || b.instagram_sent)
  );
}

/** Marked businesses whose first cold message was confirmed sent. */
export function contactQueueContactedLeads(){
  return (state.contactQueueLeads||[]).filter(b=>{
    if (b.opt_out || isCannotContact(b)) return false;
    return !!(b.contacted || b.email_sent || b.whatsapp_sent || b.instagram_sent);
  });
}

export function availableOutreachChannels(b){
  const ch=b.channels||{};
  const out=[];
  if(ch.whatsapp||b.phone||b.international_phone) out.push('whatsapp');
  if(ch.instagram||(b.social_links&&b.social_links.instagram)) out.push('instagram');
  if(ch.email||b.email) out.push('email');
  return out;
}

export function canPromoteReady(b){
  const ch=(b.outreach_channel||'').trim().toLowerCase();
  if(!ch) return {ok:false, reason:'Pick WhatsApp, Instagram, or Email'};
  const avail=availableOutreachChannels(b);
  if(!avail.includes(ch)) return {ok:false, reason:`No ${ch} on this lead`};
  if(ch==='instagram'){
    if(!b.ig_warm_follow) return {ok:false, reason:'Tick Follow first'};
    if(!(b.ig_warm_like || b.ig_warm_comment)) return {ok:false, reason:'Tick Like or Comment'};
  }
  return {ok:true, reason:''};
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
  return (state.contactQueueLeads||[]).filter(b=>b.has_booking || (b.current_booking_software||'').trim());
}

export function contactQueueChannelMatch(b, ch){
  if(!ch) return true;
  const c=b.channels||{};
  const hasEmail=!!(c.email || b.email);
  const hasWa=!!(c.whatsapp || b.phone || b.international_phone);
  const hasIg=!!c.instagram;
  const hasWeb=!!(b.website||'').trim();
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
  // Marked / ready / booking / can't-contact → sink to bottom of All tab
  return !!(b&&(b.marked_for_contact || b.ready_for_contact || b.has_booking || (b.current_booking_software||'').trim() || isCannotContact(b)));
}

export function contactQueueVisibleLeads(){
  let base;
  if(state.contactQueueTab==='selected') base=contactQueueSelectedLeads();
  else if(state.contactQueueTab==='ready') base=contactQueueReadyLeads();
  else if(state.contactQueueTab==='booking') base=contactQueueBookingLeads();
  else if(state.contactQueueTab==='contacted') base=contactQueueContactedLeads();
  else if(state.contactQueueTab==='cannot') base=contactQueueCannotLeads();
  else base=(state.contactQueueLeads||[]).slice();
  // All: ranked open leads first; prep/ready/booking parked at bottom
  if(state.contactQueueTab==='all'){
    base.sort((a,b)=>{
      const aPark=contactQueueIsParked(a);
      const bPark=contactQueueIsParked(b);
      if(aPark!==bPark) return aPark?1:-1;
      return (Number(b.rank_score)||0)-(Number(a.rank_score)||0);
    });
  } else if(state.contactQueueTab==='contacted' || state.contactQueueTab==='cannot' || state.contactQueueTab==='ready'){
    base.sort((a,b)=>{
      const aAt=a.updated_at||a.last_contacted_at||'';
      const bAt=b.updated_at||b.last_contacted_at||'';
      if(aAt!==bAt) return aAt<bAt?1:-1;
      return (Number(b.rank_score)||0)-(Number(a.rank_score)||0);
    });
  }
  const ch=contactQueueChannel||(($('contactQueueChannel')&&$('contactQueueChannel').value)||'');
  if(ch) base=base.filter(b=>contactQueueChannelMatch(b,ch));
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
    ${parts.length?`<p class="hint" style="margin:8px 0 0"><strong style="color:var(--text)">Top why:</strong> ${esc(parts.join(' · '))}</p>`:''}
  `;
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
  const pass=(list)=>list.filter(b=>{
    if(ch && !contactQueueChannelMatch(b,ch)) return false;
    if(q && !contactQueueMatchQuery(b,q)) return false;
    return true;
  });
  const allBase=state.contactQueueLeads||[];
  const selBase=contactQueueSelectedLeads();
  const readyBase=contactQueueReadyLeads();
  const bookBase=contactQueueBookingLeads();
  const contactedBase=contactQueueContactedLeads();
  const cannotBase=contactQueueCannotLeads();
  if($('contactQueueAllCount')) $('contactQueueAllCount').textContent=String(pass(allBase).length);
  if($('contactQueueSelectedCount')) $('contactQueueSelectedCount').textContent=String(pass(selBase).length);
  if($('contactQueueReadyCount')) $('contactQueueReadyCount').textContent=String(pass(readyBase).length);
  if($('contactQueueBookingCount')) $('contactQueueBookingCount').textContent=String(pass(bookBase).length);
  if($('contactQueueContactedCount')) $('contactQueueContactedCount').textContent=String(pass(contactedBase).length);
  if($('contactQueueCannotCount')) $('contactQueueCannotCount').textContent=String(pass(cannotBase).length);
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

function composeChosenChannelButtonHtml(b){
  const ch=(b.outreach_channel||'').trim().toLowerCase();
  if(!ch) return '';
  const short={whatsapp:'WA',email:'Email',instagram:'IG'};
  const label=short[ch]||channelLabel(ch);
  return `<button type="button" class="primary" data-compose-queue="${b.id}" data-compose-channel="${ch}" title="Open ${channelLabel(ch)} compose">${label}</button>`;
}

function prepChannelPickerHtml(b){
  const avail=availableOutreachChannels(b);
  const cur=(b.outreach_channel||'').trim().toLowerCase();
  const labels={whatsapp:'WA',instagram:'IG',email:'Email'};
  if(!avail.length){
    const findIg=(b.website||'').trim()
      ? `<button type="button" data-cq-ig-discover="${b.id}" title="Find Instagram on website">Find IG</button>`
      : '';
    return `<span class="cq-prep-hint" title="Open → Contacts to add phone/email, or Find IG">No WA / IG / Email — use Open → Contacts</span>${findIg}`;
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
  return `<button type="button" class="primary" data-cq-promote="${b.id}" ${gate.ok?'':'disabled'} title="${esc(gate.reason||'Move to Ready to contact')}">Ready to contact</button>`;
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
  const leads=contactQueueVisibleLeads();
  const q=(contactQueueQuery||'').trim();
  const pagerHost=ensurePagerHost(list,'contactQueuePager');
  const tabLabel=isSelected?'To contact':(isReady?'Ready to contact':(isBooking?'Has booking':(isContacted?'Contacted':(isCannot?"Can't contact":'All'))));
  if(!leads.length){
    if(q || contactQueueChannel){
      list.innerHTML=`<div class="empty-card">No matches on <strong>${tabLabel}</strong> for this search/filter.</div>`;
    }else{
      list.innerHTML=isSelected
        ? '<div class="empty-card">Nothing in prep yet. On <strong>All</strong>, pick leads and use <strong>Mark to contact</strong>.</div>'
        : isReady
          ? '<div class="empty-card">Nothing ready. On <strong>To contact</strong>, pick a channel (and IG warm 2/3), then <strong>Ready to contact</strong>.</div>'
        : isBooking
          ? '<div class="empty-card">No leads marked as already having booking. Use <strong>Has booking</strong> on Discover or All.</div>'
          : isContacted
            ? '<div class="empty-card">No confirmed sends yet. From <strong>Ready to contact</strong>, message then <strong>Confirm sent</strong>.</div>'
          : isCannot
            ? '<div class="empty-card">No parked leads. On <strong>To contact</strong> or <strong>Ready</strong>, use <strong>Can\'t contact</strong> when unreachable.</div>'
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
    const noCheck=isSelected||isReady||isBooking||isContacted||isCannot;
    const checkCell=noCheck?'':`<div class="checkcol"><input type="checkbox" data-cq-pick="${b.id}" ${picked?'checked':''} aria-label="Pick ${esc(b.name||'lead')}"></div>`;
    let actions='';
    if(isSelected){
      actions=`
        <div class="cq-prep">${prepChannelPickerHtml(b)} ${prepWarmChecklistHtml(b)}</div>
        ${prepReadyButtonHtml(b)}
        <button type="button" data-cq-cannot="${b.id}">Can't contact</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>
        <button type="button" data-cq-unmark="${b.id}">Remove</button>`;
    }else if(isReady){
      actions=`
        ${composeChosenChannelButtonHtml(b)}
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
      actions=`
        ${badge}
        <button type="button" data-cq-revert="${b.id}">Back to Ready</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>`;
    }else if(isCannot){
      const via=cannotViaLabel(cannotContactVia(b));
      actions=`
        <button type="button" class="primary" data-cq-clear-cannot="${b.id}">Back to queue</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>
        <span class="ch-tag" title="Unreachable">${esc(via)}</span>`;
    }else{
      const markLabel=isReadyLead?'On Ready':(already?'On To contact':'Mark to contact');
      actions=`
        <button type="button" class="primary" data-cq-mark="${b.id}" ${already||isReadyLead?'disabled':''}>${markLabel}</button>
        <button type="button" data-cq-booking="${b.id}" data-booking-on="${hasBook?'0':'1'}">${hasBook?'Clear booking':'Has booking'}</button>
        <button type="button" data-open-prospect="${b.id}">Open</button>`;
    }
    const contactedBadge=isContacted?` ${sentChannelsHtml(b)}`:'';
    const cannotBadge=isCannot?` <span class="badge">${esc(cannotViaLabel(cannotContactVia(b)))}</span>`:(isCannotContact(b)&&!isSelected&&!isReady?` <span class="badge">Can't contact</span>`:'');
    const stageBadge=isReadyLead&&!isReady&&!isContacted&&!isCannot?' <span class="badge queue">Ready</span>'
      :(already&&!isSelected&&!isReady&&!isContacted&&!isCannot?' <span class="badge queue">To contact</span>':'');
    const warmChip=isSelected&&(b.outreach_channel||'')==='instagram'
      ?(canPromoteReady(b).ok
        ?' <span class="cq-warm-chip done" title="Warm ready (2/3)">Warm ✓</span>'
        :' <span class="cq-warm-chip" title="Need 2 of 3 warm">Warm –</span>')
      :'';
    return `
    <article class="queue-lead ${noCheck?'no-check':''}" data-row="${b.id}" data-maps="${esc(mapsUrl)}" title="${mapsUrl?'Open Google Maps':''}">
      ${checkCell}
      <div class="queue-rank meta" style="min-width:48px;align-items:flex-start"><strong style="font-size:18px">#${rankNo}</strong><span class="sub">${esc(String(b.rank_score))}</span></div>
      <div class="queue-body">
        <div class="biz">${esc(b.name||'—')}${stageBadge}${hasBook?' <span class="badge">Booking</span>':''}${warmChip}${contactedBadge}${cannotBadge}</div>
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
    const id=btn.dataset.composeQueue;
    const lead=state.contactQueueLeads.find(x=>String(x.id)===String(id));
    if(!lead){ toast('Lead not in queue',true); return; }
    const preferred=preferredColdChannel(lead, btn.dataset.composeChannel||'');
    try{
      await B.openCompose(lead, preferred);
    }catch(e){ toast(e.message,true); }
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
      if(row&&updated) Object.assign(row, updated);
      else if(row){ row.outreach_channel=ch; if(ch==='instagram'&&!canPromoteReady({...row,outreach_channel:ch}).ok) row.ready_for_contact=false; }
      renderContactQueueList();
      toast('Channel: '+channelLabel(ch));
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-warm]').forEach(inp=>inp.addEventListener('change',async ev=>{
    ev.stopPropagation();
    const id=inp.dataset.cqWarm;
    const field=inp.dataset.warmField;
    try{
      const body={[field]:!!inp.checked};
      const updated=await api('/api/businesses/'+id,{method:'PATCH',body});
      const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
      if(row&&updated) Object.assign(row, updated);
      else if(row) row[field]=!!inp.checked;
      renderContactQueueList();
    }catch(e){
      inp.checked=!inp.checked;
      toast(e.message,true);
    }
  }));
  list.querySelectorAll('[data-cq-promote]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    if(btn.disabled) return;
    try{
      await api('/api/sales/prospects/'+btn.dataset.cqPromote+'/promote-ready',{method:'POST',body:{}});
      await loadContactQueue({keepTab:true});
      setContactQueueTab('ready');
      renderContactQueueList();
      toast('Moved to Ready to contact');
    }catch(e){ toast(e.message,true); }
  }));
  list.querySelectorAll('[data-cq-demote]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    try{
      await api('/api/sales/prospects/'+btn.dataset.cqDemote+'/demote-to-contact',{method:'POST',body:{}});
      await loadContactQueue({keepTab:true});
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
      await loadContactQueue({keepTab:true});
      if(on){
        toast('Marked as has booking');
      }else{
        const back=isFreshDiscoverLead({
          ...(before||{}),
          current_booking_software:'',
          has_booking:false,
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
  list.querySelectorAll('[data-cq-ig-discover]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    const id=btn.dataset.cqIgDiscover;
    btn.disabled=true;
    btn.textContent='Finding IG…';
    try{
      const res=await discoverInstagram(id, { force:true });
      if(res.found && res.instagram){
        const row=state.contactQueueLeads.find(x=>String(x.id)===String(id));
        if(row) applyDiscoveredIg(row, res.instagram);
        toast('Instagram found on website');
        renderContactQueueList();
        return;
      }
      toast('No Instagram link on website', true);
      btn.textContent='No IG';
    }catch(e){
      toast(e.message||'IG discover failed', true);
      btn.disabled=false;
      btn.textContent='Find IG';
    }
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
    await api('/api/sales/prospects/'+id,{method:'PATCH',body:{current_booking_software:value}});
    const row=state.contactQueueLeads.find(x=>Number(x.id)===Number(id));
    if(row){
      row.current_booking_software=value;
      row.has_booking=!!hasBooking;
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

