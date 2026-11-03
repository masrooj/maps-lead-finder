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

export async function loadDashboard(){
  const d=await api('/api/sales/dashboard?product_line=STANDALONE');
  const q=d.queues||{};
  $('dashQueues').innerHTML=`
    <button type="button" class="queue-card" data-goto="contactQueue" title="Open Contact queue"><span>Contact queue</span><strong style="font-size:16px">Ranked</strong><span class="kpi-hint">→ Queue</span></button>
    <button type="button" class="queue-card" data-goto="tasks" title="Open Tasks"><span>Overdue tasks</span><strong>${q.overdue_count||0}</strong><span class="kpi-hint">→ Tasks</span></button>
    <button type="button" class="queue-card" data-goto="tasks" title="Open Tasks"><span>Due today</span><strong>${q.due_today_count||0}</strong><span class="kpi-hint">→ Tasks</span></button>
    <button type="button" class="queue-card" data-goto="tasks" title="Open Tasks"><span>Due soon (7d)</span><strong>${q.due_soon_count||0}</strong><span class="kpi-hint">→ Tasks</span></button>
    <button type="button" class="queue-card" data-goto="prospects" title="Open Prospects"><span>No next action</span><strong>${q.no_next_action_count||0}</strong><span class="kpi-hint">→ Prospects</span></button>
    <button type="button" class="queue-card" data-goto="deals" title="Open Deals"><span>Deals / close</span><strong style="font-size:16px">Confirm</strong><span class="kpi-hint">→ Deals</span></button>
    <button type="button" class="queue-card" data-goto="prospects" title="Open Prospects"><span>Qualified (score≥5)</span><strong>${d.qualified_count||0}</strong><span class="kpi-hint">→ Prospects</span></button>`;
  const open=(d.pipeline&&d.pipeline.open_pipeline_value)||{};
  const weighted=(d.pipeline&&d.pipeline.weighted_forecast)||{};
  const collected=(d.collected&&d.collected.by_currency)||{};
  const fmt=o=>Object.keys(o).length?Object.entries(o).map(([c,v])=>`${c} ${Number(v).toFixed(0)}`).join(' · '):'—';
  $('dashMoney').innerHTML=`
    <button type="button" class="queue-card" data-goto="pipeline" title="Open Pipeline"><span>Open pipeline</span><strong style="font-size:16px">${fmt(open)}</strong><span class="kpi-hint">→ Pipeline</span></button>
    <button type="button" class="queue-card" data-goto="pipeline" title="Open Pipeline"><span>Weighted forecast (est.)</span><strong style="font-size:16px">${fmt(weighted)}</strong><span class="kpi-hint">→ Pipeline</span></button>
    <button type="button" class="queue-card" data-goto="money" title="Open Money"><span>Collected (live)</span><strong style="font-size:16px">${fmt(collected)}</strong><span class="kpi-hint">→ Money</span></button>
    <button type="button" class="queue-card" data-goto="money" title="Open Money"><span>Collected n=</span><strong>${(d.collected&&d.collected.sample_size)||0}</strong><span class="kpi-hint">→ Money</span></button>
    <button type="button" class="queue-card" data-goto="reports" title="Open Reports"><span>Reports</span><strong style="font-size:16px">Summary</strong><span class="kpi-hint">→ Reports</span></button>`;
  $('dashNote').textContent=(q.notifications_note||'')+' Collected = verified payments only — never proposals. Click a KPI to open that panel.';
  bindDashKpis($('dashQueues'));
  bindDashKpis($('dashMoney'));
}


registerPanel({ id: 'dashboard', refresh: safeRefresh(loadDashboard) });

