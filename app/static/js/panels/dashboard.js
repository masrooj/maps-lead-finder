import { $ } from '../core/dom.js';

import { api } from '../core/api.js';

import { registerPanel } from './registry.js';

import { B } from '../app/bridge.js';

import { safeRefresh } from '../shell/router.js';





const PANEL_ALIASES={
  discover:'leads',
  discovery:'leads',
  queue:'contactQueue',
  contact:'contactQueue',
  comms:'activities',
  communication:'activities',
};

function resolveGoto(raw){
  const value=String(raw||'').trim();
  if(!value) return '';
  if(value.includes(':')){
    const [panel,...rest]=value.split(':');
    const mapped=PANEL_ALIASES[panel]||panel;
    return [mapped, ...rest].join(':');
  }
  return PANEL_ALIASES[value]||value;
}

export function bindDashKpis(root){
  if(!root) return;
  root.querySelectorAll('[data-goto]').forEach(el=>{
    const go=()=>{
      const target=resolveGoto(el.getAttribute('data-goto')||el.dataset.goto||'');
      if(!target) return;
      if(typeof B.setAppPanel==='function') B.setAppPanel(target);
    };
    el.addEventListener('click',e=>{ e.preventDefault(); e.stopPropagation(); go(); });
    el.addEventListener('keydown',e=>{
      if(e.key==='Enter'||e.key===' '){ e.preventDefault(); e.stopPropagation(); go(); }
    });
  });
}

function dashFilterQuery(){
  const params=new URLSearchParams({ product_line:'STANDALONE' });
  const map={
    date_from:'dashDateFrom',
    date_to:'dashDateTo',
    country:'dashCountry',
    city:'dashCity',
    package:'dashPackage',
    sales_stage:'dashStage',
    payment_status:'dashPayStatus',
    delivery_status:'dashDelStatus',
    owner_id:'dashOwnerId',
  };
  Object.entries(map).forEach(([k,id])=>{
    const el=$(id);
    const v=el&&String(el.value||'').trim();
    if(v) params.set(k, v);
  });
  return params;
}

export async function loadDashboard(){
  const params=dashFilterQuery();
  const d=await api('/api/sales/dashboard?'+params.toString());
  const q=d.queues||{};
  const filtered=d.filtered||{};
  const byStage=filtered.by_stage||{};
  if($('dashFilterNote')){
    const applied=filtered.filters_applied||{};
    const keys=Object.keys(applied);
    $('dashFilterNote').textContent=keys.length
      ?`Filters active (${keys.join(', ')}): ${filtered.total_leads||0} leads · by stage ${Object.entries(byStage).map(([k,v])=>k+':'+v).join(', ')||'—'}`
      :`Showing all STANDALONE leads (${filtered.total_leads||0}). Use filters above to narrow commercial views.`;
  }
  $('dashQueues').innerHTML=`
    <button type="button" class="queue-card" data-goto="contactQueue" title="Open Contact queue"><span>Contact queue</span><strong style="font-size:16px">Ranked</strong><span class="kpi-hint">→ Queue</span></button>
    <button type="button" class="queue-card" data-goto="tasks" title="Open Tasks"><span>Overdue tasks</span><strong>${q.overdue_count||0}</strong><span class="kpi-hint">→ Tasks</span></button>
    <button type="button" class="queue-card" data-goto="tasks" title="Open Tasks"><span>Due today</span><strong>${q.due_today_count||0}</strong><span class="kpi-hint">→ Tasks</span></button>
    <button type="button" class="queue-card" data-goto="tasks" title="Open Tasks"><span>Due soon (7d)</span><strong>${q.due_soon_count||0}</strong><span class="kpi-hint">→ Tasks</span></button>
    <button type="button" class="queue-card" data-goto="prospects" title="Open Prospects"><span>No next action</span><strong>${q.no_next_action_count||0}</strong><span class="kpi-hint">→ Prospects</span></button>
    <button type="button" class="queue-card" data-goto="deals" title="Open Deals"><span>Deals / close</span><strong style="font-size:16px">Confirm</strong><span class="kpi-hint">→ Deals</span></button>
    <button type="button" class="queue-card" data-goto="prospects" title="Open Prospects"><span>Qualified (score≥5)</span><strong>${d.qualified_count||0}</strong><span class="kpi-hint">→ Prospects</span></button>
    <button type="button" class="queue-card" data-goto="prospects" title="Filtered leads"><span>Filtered leads</span><strong>${filtered.total_leads||0}</strong><span class="kpi-hint">→ Prospects</span></button>
    <button type="button" class="queue-card" data-goto="pipeline" title="Won"><span>Won (filtered)</span><strong>${byStage.won||byStage.subscribed||0}</strong><span class="kpi-hint">→ Pipeline</span></button>
    <button type="button" class="queue-card" data-goto="pipeline" title="Lost"><span>Lost (filtered)</span><strong>${byStage.lost||byStage.churned||0}</strong><span class="kpi-hint">→ Pipeline</span></button>`;
  const open=(d.pipeline&&d.pipeline.open_pipeline_value)||{};
  const weighted=(d.pipeline&&d.pipeline.weighted_forecast)||{};
  const collected=(d.collected&&d.collected.by_currency)||{};
  const fmt=o=>Object.keys(o).length?Object.entries(o).map(([c,v])=>`${c} ${Number(v).toFixed(0)}`).join(' · '):'—';
  const ah=d.appointhive||{};
  const funnel=ah.funnel||{};
  const beCol=((ah.be_verified_collected||{}).by_currency)||{};
  const beOut=((ah.be_outstanding||{}).by_currency)||{};
  $('dashMoney').innerHTML=`
    <button type="button" class="queue-card" data-goto="pipeline" title="Open Pipeline"><span>Open pipeline</span><strong style="font-size:16px">${fmt(open)}</strong><span class="kpi-hint">→ Pipeline</span></button>
    <button type="button" class="queue-card" data-goto="pipeline" title="Open Pipeline"><span>Weighted forecast (est.)</span><strong style="font-size:16px">${fmt(weighted)}</strong><span class="kpi-hint">→ Pipeline</span></button>
    <button type="button" class="queue-card" data-goto="money" title="Open Money"><span>Collected (local)</span><strong style="font-size:16px">${fmt(collected)}</strong><span class="kpi-hint">→ Money</span></button>
    <button type="button" class="queue-card" data-goto="appoinHive" title="Open AppointHive"><span>AH verified collected</span><strong style="font-size:16px">${fmt(beCol)}</strong><span class="kpi-hint">→ AppointHive</span></button>
    <button type="button" class="queue-card" data-goto="appoinHive" title="Open AppointHive"><span>AH outstanding</span><strong style="font-size:16px">${fmt(beOut)}</strong><span class="kpi-hint">→ AppointHive</span></button>
    <button type="button" class="queue-card" data-goto="appoinHive:events" title="Open AppointHive events"><span>Awaiting AH payment</span><strong>${funnel.awaiting_payments||0}</strong><span class="kpi-hint">→ Events</span></button>
    <button type="button" class="queue-card" data-goto="appoinHive" title="Open AppointHive"><span>Partially paid (AH)</span><strong>${funnel.partially_paid_orders||0}</strong><span class="kpi-hint">→ AppointHive</span></button>
    <button type="button" class="queue-card" data-goto="appoinHive" title="Open AppointHive"><span>Awaiting delivery</span><strong>${funnel.awaiting_delivery||0}</strong><span class="kpi-hint">→ AppointHive</span></button>
    <button type="button" class="queue-card" data-goto="appoinHive:events" title="Outbound failures"><span>Outbound failures</span><strong>${ah.outbound_failed||0}</strong><span class="kpi-hint">→ Events</span></button>
    <button type="button" class="queue-card" data-goto="reports" title="Open Reports"><span>Reports</span><strong style="font-size:16px">Summary</strong><span class="kpi-hint">→ Reports</span></button>`;
  $('dashNote').textContent=(q.notifications_note||'')+' Local collected = Sales OS verified payments. AH collected = AppointHive payment snapshots only — never proposals. Click a KPI to open that panel.';
  bindDashKpis($('dashQueues'));
  bindDashKpis($('dashMoney'));
}

if(typeof document!=='undefined'){
  document.addEventListener('DOMContentLoaded',()=>{
    const btn=document.getElementById('dashApplyFilters');
    if(btn && !btn.dataset.bound){
      btn.dataset.bound='1';
      btn.addEventListener('click',()=>loadDashboard().catch(()=>{}));
    }
  });
}


registerPanel({ id: 'dashboard', refresh: safeRefresh(loadDashboard) });
