import { $ } from '../core/dom.js';

import { esc, api, toast } from '../core/api.js';

import { pageSlice, renderPager, ensurePagerHost, pagerState } from '../core/pager.js';

import { registerPanel } from './registry.js';

import { B } from '../app/bridge.js';

import { safeRefresh } from '../shell/router.js';

import { mapsUrlFor, markBookingIds } from './contact-queue.js';



let prospectsCache = [];
let prospectsLocCache = {};


export function prospectsScopeLabel(){
  const country=($('prospectsCountry')&&$('prospectsCountry').value)||'';
  const city=($('prospectsCity')&&$('prospectsCity').value)||'';
  if(country&&city) return `${city} · ${country}`;
  if(city) return city;
  if(country) return country;
  return '';
}

export function mergeProspectsLocCache(rows){
  (rows||[]).forEach(p=>{
    const ctry=(p.country||'').trim();
    const cty=(p.city||'').trim();
    if(!ctry) return;
    if(!prospectsLocCache[ctry]) prospectsLocCache[ctry]=new Set();
    if(cty) prospectsLocCache[ctry].add(cty);
  });
}

export function fillProspectsLocationFilters(keepCountry, keepCity){
  const countrySel=$('prospectsCountry');
  const citySel=$('prospectsCity');
  if(!countrySel||!citySel) return;
  const loc=prospectsLocCache||{};
  const countries=Object.keys(loc).sort((a,b)=>a.localeCompare(b));
  const curCountry=keepCountry!=null?keepCountry:(countrySel.value||'');
  countrySel.innerHTML='<option value="">All countries</option>'+countries.map(c=>`<option value="${esc(c)}" ${c===curCountry?'selected':''}>${esc(c)}</option>`).join('');
  if(curCountry && ![...countrySel.options].some(o=>o.value===curCountry)) countrySel.value='';
  const activeCountry=countrySel.value;
  const cities=activeCountry
    ? [...(loc[activeCountry]||[])].sort((a,b)=>a.localeCompare(b))
    : [...new Set(Object.values(loc).flatMap(s=>[...s]))].sort((a,b)=>a.localeCompare(b));
  const curCity=keepCity!=null?keepCity:(citySel.value||'');
  citySel.innerHTML='<option value="">All cities</option>'+cities.map(c=>`<option value="${esc(c)}" ${c===curCity?'selected':''}>${esc(c)}</option>`).join('');
  if(curCity && ![...citySel.options].some(o=>o.value===curCity)) citySel.value='';
}

export function renderProspectsTable(){
  const scope=prospectsScopeLabel();
  const tb=$('prospectsTable').querySelector('tbody');
  const pagerHost=ensurePagerHost($('prospectsTable').closest('.sales-table-wrap'),'prospectsPager');
  const rows=prospectsCache||[];
  if(!rows.length){
    if($('prospectsMeta')) $('prospectsMeta').textContent='No prospects match. Pick a country/city or run Discover with Fetch: All.';
    tb.innerHTML='<tr><td colspan="7" data-label=""><div class="empty-card">No prospects match. Pick a country and city, or run Discover with Fetch: All.</div></td></tr>';
    if(pagerHost) pagerHost.innerHTML='';
    return;
  }
  const slice=pageSlice(rows,'prospects');
  if($('prospectsMeta')){
    $('prospectsMeta').textContent=`${slice.total} prospects${scope?' in '+scope:''} · page ${slice.page}/${slice.pages}. Country and city stay separate columns.`;
  }
  tb.innerHTML=slice.items.map(p=>{
    const hasVal=p.deal_value!=null&&p.deal_value!=='';
    const hasScore=p.qualification_score!=null&&p.qualification_score!=='';
    const valHtml=hasVal
      ?`<span class="value-pill has">${esc(p.currency||'USD')} ${esc(p.deal_value)}</span>`
      :`<span class="value-pill muted-dash">No value</span>`;
    const scoreHtml=hasScore
      ?`<span class="score-pill has">${esc(p.qualification_score)}/10</span>`
      :`<span class="score-pill muted-dash">Not scored</span>`;
    const hasBook=!!(p.current_booking_software||'').trim();
    return `<tr data-open-prospect="${p.id}" data-maps="${esc(mapsUrlFor(p))}" style="cursor:pointer">
    <td data-label="Name"><strong>${esc(p.name)}</strong>${hasBook?' <span class="badge">Booking</span>':''}</td>
    <td data-label="Country">${esc(p.country||'—')}</td>
    <td data-label="City">${esc(p.city||'—')}</td>
    <td data-label="Stage">${esc((p.stage&&p.stage.name)||'—')}</td>
    <td data-label="Value">${valHtml}</td>
    <td data-label="Score">${scoreHtml}</td>
    <td data-label="Actions"><div class="action-stack">
      <button type="button" data-open-prospect="${p.id}">Open</button>
      <button type="button" data-prospect-booking="${p.id}" data-booking-on="${hasBook?'0':'1'}">${hasBook?'Clear booking':'Has booking'}</button>
    </div></td>
  </tr>`;
  }).join('');
  renderPager(pagerHost, slice, renderProspectsTable);
  tb.querySelectorAll('[data-open-prospect]').forEach(el=>el.addEventListener('click',async ev=>{
    if(ev.target.closest('[data-prospect-booking]')) return;
    ev.stopPropagation();
    const id=el.dataset.openProspect||el.closest('[data-open-prospect]')?.dataset.openProspect;
    if(id) await B.openProspectById(id);
  }));
  tb.querySelectorAll('[data-prospect-booking]').forEach(btn=>btn.addEventListener('click',async ev=>{
    ev.stopPropagation();
    try{
      const on=btn.dataset.bookingOn==='1';
      await markBookingIds([btn.dataset.prospectBooking], on);
      const row=prospectsCache.find(x=>String(x.id)===String(btn.dataset.prospectBooking));
      if(row) row.current_booking_software=on?'Has booking':'';
      renderProspectsTable();
      toast(on?'Marked as has booking':'Cleared booking flag');
    }catch(e){ toast(e.message,true); }
  }));
  tb.querySelectorAll('tr[data-maps]').forEach(tr=>tr.addEventListener('click',ev=>{
    if(ev.target.closest('button,a,input')) return;
    // double-duty: open maps only when alt/meta, else open prospect (existing open handler on name/open)
  }));
}

export async function loadProspects(){
  const q=($('prospectsQ')&&$('prospectsQ').value)||'';
  const country=($('prospectsCountry')&&$('prospectsCountry').value)||'';
  const city=($('prospectsCity')&&$('prospectsCity').value)||'';
  // Seed full country/city menus once so filters never collapse to the active scope
  if(!Object.keys(prospectsLocCache).length){
    const seed=await api('/api/sales/prospects?limit=2000');
    mergeProspectsLocCache(seed.prospects||[]);
  }
  const qs=new URLSearchParams({limit:'2000',q});
  if(country) qs.set('country',country);
  if(city) qs.set('city',city);
  const data=await api('/api/sales/prospects?'+qs.toString());
  prospectsCache=data.prospects||[];
  mergeProspectsLocCache(prospectsCache);
  fillProspectsLocationFilters(country, city);
  if(pagerState.prospects) pagerState.prospects.page=1;
  renderProspectsTable();
}


registerPanel({ id: 'prospects', refresh: safeRefresh(loadProspects) });

