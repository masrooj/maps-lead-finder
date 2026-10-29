import { $ } from '../core/dom.js';

import { esc, api } from '../core/api.js';

import { registerPanel } from './registry.js';

import { B } from '../app/bridge.js';

import { safeRefresh } from '../shell/router.js';

import { bindDashKpis } from './dashboard.js';





export function renderBars(el, rows, labelKey='name', valueKey='count'){
  if(!el) return;
  const max=Math.max(1,...rows.map(r=>Number(r[valueKey]||0)));
  if(!rows.length){ el.innerHTML='<div class="hint">No data yet</div>'; return; }
  el.innerHTML=rows.map(r=>{
    const n=Number(r[valueKey]||0);
    const pct=Math.round((n/max)*100);
    return `<div class="report-bar"><span>${esc(r[labelKey]||r.slug||'')}</span><div class="track"><div class="fill" style="width:${pct}%"></div></div><strong>${n}</strong></div>`;
  }).join('');
}

export async function loadReports(){
  const data=await api('/api/sales/reports/summary?product_line=STANDALONE');
  const open=(data.pipeline&&data.pipeline.open_pipeline_value)||{};
  const collected=(data.collected&&data.collected.by_currency)||{};
  const fmt=o=>Object.keys(o).length?Object.entries(o).map(([c,v])=>`${c} ${Number(v).toFixed(0)}`).join(' · '):'—';
  const wr=data.win_rate||{};
  $('reportsStats').innerHTML=`
    <button type="button" class="queue-card" data-goto="pipeline"><span>Open pipeline</span><strong style="font-size:15px">${fmt(open)}</strong><span class="kpi-hint">→ Pipeline</span></button>
    <button type="button" class="queue-card" data-goto="money"><span>Collected</span><strong style="font-size:15px">${fmt(collected)}</strong><span class="kpi-hint">→ Money</span></button>
    <button type="button" class="queue-card" data-goto="contactQueue"><span>Contact queue</span><strong style="font-size:15px">Ranked</strong><span class="kpi-hint">→ Queue</span></button>
    <button type="button" class="queue-card" data-goto="deals"><span>Win rate</span><strong style="font-size:15px">${wr.rate==null?'n/a':(wr.rate*100).toFixed(0)+'%'}</strong><span class="kpi-hint">→ Deals</span></button>`;
  bindDashKpis($('reportsStats'));
  renderBars($('reportsFunnel'), data.funnel||[]);
  const bands=data.score_bands||{};
  renderBars($('reportsScores'), Object.entries(bands).map(([k,v])=>({name:k,count:v})));
  const eng=data.engagement||{};
  if($('reportsEngage')){
    $('reportsEngage').innerHTML=`
      <button type="button" class="queue-card" data-goto="activities"><span>Contacted</span><strong>${eng.contacted||0}</strong><span class="kpi-hint">→ Comms</span></button>
      <button type="button" class="queue-card" data-goto="prospects"><span>Discovery notes</span><strong>${eng.discovery_notes||0}</strong><span class="kpi-hint">→ Prospects</span></button>
      <button type="button" class="queue-card" data-goto="deals"><span>Proposals</span><strong>${eng.proposals||0}</strong><span class="kpi-hint">→ Deals</span></button>
      <button type="button" class="queue-card" data-goto="money:invoices"><span>Open invoices</span><strong>${eng.invoices_open||0}</strong><span class="kpi-hint">→ Invoices &amp; QR</span></button>`;
    bindDashKpis($('reportsEngage'));
  }
  $('reportsLost').textContent=Object.entries(data.lost_reasons||{}).map(([k,v])=>`${k}: ${v}`).join(' · ')||'—';
  $('reportsSources').textContent=Object.entries(data.lead_sources||{}).map(([k,v])=>`${k}: ${v}`).join(' · ')||'—';
  $('reportsWin').textContent=`Win rate: ${wr.rate==null?'n/a':(wr.rate*100).toFixed(0)+'%'} (won ${wr.won||0} / closed ${wr.sample_size||0}). ${wr.note||''}`;
  const mrr=data.saas_mrr||{};
  const mrrFmt=Object.keys(mrr.by_currency||{}).length?Object.entries(mrr.by_currency).map(([c,v])=>`${c} ${Number(v).toFixed(0)}`).join(' · '):'—';
  if($('reportsEngage')){
    const extra=document.createElement('div');
    extra.className='queue-grid';
    extra.style.marginTop='10px';
    extra.innerHTML=`
      <div class="queue-card"><span>SaaS MRR (tracked)</span><strong style="font-size:15px">${esc(mrrFmt)}</strong></div>
      <div class="queue-card"><span>With MRR set</span><strong>${mrr.with_mrr||0}</strong></div>
      <div class="queue-card"><span>Trialing</span><strong>${mrr.trialing||0}</strong></div>`;
    if(!$('reportsSaasMrr')){ extra.id='reportsSaasMrr'; $('reportsEngage').after(extra); }
    else $('reportsSaasMrr').innerHTML=extra.innerHTML;
  }
}


registerPanel({ id: 'reports', refresh: safeRefresh(loadReports) });

