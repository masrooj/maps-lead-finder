import { $ } from '../core/dom.js';

import { esc, api, toast, fmtMoney } from '../core/api.js';

import { uiForm } from '../core/dialog.js';

import { B } from '../app/bridge.js';

let currentRole = 'owner';
/** Shared connection cache — single owner module (do not redeclare in runtime). */
export let beConnCache = { connected: false, connection_status: 'disconnected' };

export function setBeConnCache(conn) {
  beConnCache = conn || { connected: false, connection_status: 'disconnected' };
  return beConnCache;
}

export function getBeConnCache() {
  return beConnCache;
}

export function setBeRole(role){ currentRole = role || 'owner'; }



export async function refreshBeConnBanners(){
  try{
    const conn=await api('/api/sales/be/connection');
    setBeConnCache(conn);
  }catch(e){ return; }
  const connected=!!beConnCache.connected;
  const msg=connected
    ?'AppointHive connected — STANDALONE product cash uses AppointHive pay links (local PSP blocked).'
    :'AppointHive disconnected — connect under Workspace → AppointHive before STANDALONE catalog / send / upgrade.';
  [['beDealsBanner',!connected],['beMoneyBanner',!connected],['bePipelineBanner',!connected]].forEach(([id,show])=>{
    const el=$(id); if(!el) return;
    el.classList.toggle('hidden', !show);
    if(show) el.innerHTML=`${esc(msg)} <button type="button" class="chip ok" data-goto="appoinHive:connect">Open AppointHive</button>`;
  });
}

export function renderBeConnDetails(conn){
  const host=$('beConnDetails');
  if(!host) return;
  const st=conn.connection_status||'disconnected';
  const rows=[
    ['Status', conn.connected?'Connected':st],
    ['AppointHive URL', conn.base_url||'—'],
    ['Pairing ID', conn.pairing_id||'—'],
    ['Bridge', conn.bridge_enabled?'enabled':'disabled'],
    ['Secret', conn.secret_configured?'configured (hidden)':'not set'],
    ['Secret rotation', conn.secret_rotation_status||'—'],
    ['Webhook', conn.webhook_path||'/api/webhooks/booking-engine'],
    ['Last health', conn.last_health_at||'—'],
    ['Last heartbeat', conn.last_heartbeat_at||'—'],
    ['Last sync OK', conn.last_sync_ok_at||'—'],
    ['Last sync fail', conn.last_sync_fail_at||'—'],
    ['Pending events', String(conn.pending_events??0)],
    ['Failed events', String(conn.failed_events??0)],
    ['Permissions', Array.isArray(conn.permissions)?conn.permissions.join(', '):'—'],
    ['Won policy', conn.won_policy?(conn.won_policy.auto_won_on_be_paid?'auto-Won on paid':'payment ≠ Won'):'—'],
    ['Pairing code', conn.pairing_code_active?(`active until ${conn.pairing_expires_at||'—'}`):'none'],
    ['Product', conn.product_name||'AppointHive Sales OS'],
  ];
  host.innerHTML=`<strong>${conn.connected?'Connected':'Not connected'}</strong>
    <div class="sales-table-wrap" style="margin-top:8px"><table class="sales-table"><tbody>
      ${rows.map(([k,v])=>`<tr><td style="width:160px"><strong>${esc(k)}</strong></td><td>${esc(String(v))}</td></tr>`).join('')}
    </tbody></table></div>
    ${conn.connected?'':`<div class="sub" style="margin-top:8px">Not connected — open <button type="button" class="chip ok" data-goto="appoinHive:connect">Connect</button> to save URL/secret or pair, then Test connection.</div>`}`;
}

function _money(cents, cur){
  const n=Number(cents||0)/100;
  try{ return fmtMoney(n, cur||'USD'); }catch(e){ return `${cur||'USD'} ${n.toFixed(2)}`; }
}

export function renderBeMonitor(mon){
  const host=$('beMonitorHost');
  if(!host) return;
  if(!mon){ host.textContent='No monitor data'; return; }
  const funnel=mon.funnel||{};
  const counts=mon.event_counts||{};
  const cards=[
    ['Pending events', counts.pending||0],
    ['Failed events', counts.failed||0],
    ['Outbound failures', mon.outbound_failed||0],
    ['AH requests', funnel.appointhive_requests||0],
    ['Awaiting quote', funnel.awaiting_quotations||0],
    ['Awaiting payment', funnel.awaiting_payments||0],
    ['Partially paid', funnel.partially_paid_orders||0],
    ['Fully paid', funnel.fully_paid_orders||0],
    ['Awaiting delivery', funnel.awaiting_delivery||0],
    ['Delivered', funnel.delivered_customers||0],
    ['Upgrade-ready', funnel.upgrade_opportunities||0],
  ];
  const recent=(mon.recent_events||[]).slice(0,8);
  const outbound=(mon.recent_outbound_failures||[]).slice(0,6);
  host.innerHTML=`
    <div class="kpi-row" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">
      ${cards.map(([l,v])=>`<div class="queue-card" style="min-width:120px"><span>${esc(l)}</span><strong>${esc(String(v))}</strong></div>`).join('')}
    </div>
    <div class="sales-table-wrap"><table class="sales-table"><thead><tr>
      <th>When</th><th>Event</th><th>Status</th><th>Prospect</th>
    </tr></thead><tbody>
      ${recent.length?recent.map(e=>`<tr>
        <td class="sub">${esc((e.created_at||'').replace('T',' ').slice(0,19))}</td>
        <td><code>${esc(e.event_type||'')}</code></td>
        <td><span class="status-pill">${esc(e.status||'')}</span></td>
        <td>${e.business_id?`<button type="button" class="chip ok" data-be-open-prospect="${esc(String(e.business_id))}">${esc(e.prospect_name||('#'+e.business_id))}</button>`:'—'}</td>
      </tr>`).join(''):`<tr><td colspan="4" class="hint">No events yet.</td></tr>`}
    </tbody></table></div>
    ${outbound.length?`<div class="sub" style="margin-top:12px"><strong>Outbound failures</strong> (Sales OS → AppointHive)</div>
      <div class="sales-table-wrap"><table class="sales-table"><thead><tr><th>Op</th><th>Error</th><th>Prospect</th><th></th></tr></thead><tbody>
        ${outbound.map(o=>`<tr>
          <td><code>${esc(o.operation||'')}</code></td>
          <td class="sub">${esc((o.error||'').slice(0,120))}</td>
          <td>${o.business_id?`<button type="button" class="chip ok" data-be-open-prospect="${esc(String(o.business_id))}">${esc(o.prospect_name||('#'+o.business_id))}</button>`:'—'}</td>
          <td><button type="button" data-be-resolve-outbound="${esc(String(o.id))}">Mark resolved</button></td>
        </tr>`).join('')}
      </tbody></table></div>`:''}`;
  host.querySelectorAll('[data-be-open-prospect]').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const id=Number(btn.dataset.beOpenProspect);
      if(id && typeof B.openProspectById==='function') B.openProspectById(id);
    });
  });
  host.querySelectorAll('[data-be-resolve-outbound]').forEach(btn=>{
    btn.addEventListener('click',async()=>{
      if(!isOwnerRole()){ toast('Owner only',true); return; }
      try{
        await api('/api/sales/be/outbound-failures/'+btn.dataset.beResolveOutbound+'/resolve',{method:'POST',body:{}});
        toast('Marked resolved');
        await loadBeMonitor();
      }catch(err){ toast(err.message||'Resolve failed',true); }
    });
  });
}

export async function loadBeMonitor(){
  const host=$('beMonitorHost');
  if(!host) return;
  host.textContent='Loading monitor…';
  try{
    const mon=await api('/api/sales/be/monitor');
    renderBeMonitor(mon);
  }catch(e){
    host.innerHTML=`<div class="pay-status err">${esc(e.message||'Monitor failed')}</div>`;
  }
}

export async function loadBeEvents(){
  const host=$('beEventsHost');
  if(!host) return;
  const status=(($('beEventsStatus')&&$('beEventsStatus').value)||'').trim();
  host.textContent='Loading events…';
  try{
    const q=status?('?status='+encodeURIComponent(status)):'';
    const data=await api('/api/sales/be/events'+q);
    const rows=data.events||[];
    const counts=data.counts||{};
    host.innerHTML=`<div class="sub" style="margin-bottom:8px">pending ${esc(String(counts.pending||0))} · failed ${esc(String(counts.failed||0))} · processed ${esc(String(counts.processed||0))}</div>
      <div class="sales-table-wrap"><table class="sales-table"><thead><tr>
        <th>ID</th><th>Type</th><th>Status</th><th>Prospect</th><th>Attempts</th><th></th>
      </tr></thead><tbody>
        ${rows.length?rows.map(e=>`<tr>
          <td>${esc(String(e.id))}</td>
          <td><code>${esc(e.event_type||'')}</code><div class="sub">${esc((e.created_at||'').slice(0,19))}</div></td>
          <td>${esc(e.status||'')}${e.error?`<div class="sub pay-status err">${esc(e.error)}</div>`:''}</td>
          <td>${e.business_id?`<button type="button" class="chip ok" data-be-open-prospect="${esc(String(e.business_id))}">${esc(e.prospect_name||('#'+e.business_id))}</button>`:'—'}</td>
          <td>${esc(String(e.attempts||1))}</td>
          <td>${(e.status==='failed'||e.status==='pending')?`<button type="button" data-be-retry-event="${esc(String(e.id))}">Retry</button>`:''}</td>
        </tr>`).join(''):`<tr><td colspan="6" class="hint">No events.</td></tr>`}
      </tbody></table></div>`;
    host.querySelectorAll('[data-be-open-prospect]').forEach(btn=>{
      btn.addEventListener('click',()=>{
        const id=Number(btn.dataset.beOpenProspect);
        if(id && typeof B.openProspectById==='function') B.openProspectById(id);
      });
    });
    host.querySelectorAll('[data-be-retry-event]').forEach(btn=>{
      btn.addEventListener('click',async()=>{
        if(!isOwnerRole()){ toast('Owner only',true); return; }
        try{
          await api('/api/sales/be/events/'+btn.dataset.beRetryEvent+'/retry',{method:'POST',body:{}});
          toast('Event reprocessed');
          await loadBeEvents();
          await loadBeMonitor();
        }catch(err){ toast(err.message||'Retry failed',true); }
      });
    });
  }catch(e){
    host.innerHTML=`<div class="pay-status err">${esc(e.message||'Failed to load events')}</div>`;
  }
}

function _canSubmitEvidence(c){
  const pid=(c&&c.purchase_id)||'';
  if(!pid) return false;
  const st=String((c.payment_status||c.financial_status||'')).toLowerCase();
  if(st==='paid'||st==='refunded') return false;
  return true;
}

export function renderCommercialPanel(c, opts={}){
  if(!c) return '<div class="sub">No commercial snapshot.</div>';
  const businessId=opts.businessId||c.business_id||'';
  const ms=c.milestone_summary||{};
  const milestones=c.milestones||[];
  const rows=[
    ['Connection', c.connection&&c.connection.connected?'Connected':(c.connection&&c.connection.connection_status)||'—'],
    ['Customer ref', c.customer_reference||'—'],
    ['AH order ref', c.order_reference||c.purchase_order_reference||'—'],
    ['Purchase id', c.purchase_id||'—'],
    ['Inbound request', c.inbound_request_id||'—'],
    ['Package', c.package||'—'],
    ['Revision', c.package_revision||'—'],
    ['Order type', c.order_type||'—'],
    ['Quotation', c.quotation_status||'—'],
    ['Agreement', c.agreement_status||'—'],
    ['Invoice', c.invoice_status||'—'],
    ['Payment', c.payment_status||'—'],
    ['Paid', _money(c.paid_amount_cents, c.currency)],
    ['Outstanding', _money(c.outstanding_balance_cents, c.currency)],
    ['Currency', c.currency||'—'],
    ['Delivery', c.delivery_status||'—'],
    ['Release', c.release_status||'—'],
    ['Installation', c.installation_id||'—'],
    ['Upgrade', c.upgrade_status||'—'],
    ['Last sync', c.last_synchronized_at||'—'],
  ];
  let mileHtml='';
  if(ms.milestone_count){
    mileHtml=`<div class="sub" style="margin-top:10px"><strong>Milestones</strong> (read-only from AppointHive) · ${esc(String(ms.milestone_count))} total · paid ${esc(String(ms.paid_milestones||0))} · pending ${esc(String(ms.pending_milestones||0))} · overdue ${esc(String(ms.overdue_milestones||0))}
      · collected ${_money(ms.amount_collected_cents, c.currency)} · outstanding ${_money(ms.outstanding_balance_cents, c.currency)}
      ${ms.next_due_at?` · next due ${esc(ms.next_due_at.slice(0,10))}`:''}</div>
      <div class="sales-table-wrap" style="margin-top:6px"><table class="sales-table"><thead><tr><th>Name</th><th>Amount</th><th>Due</th><th>Status</th></tr></thead><tbody>
        ${milestones.map(m=>`<tr>
          <td>${esc(m.name||'')}</td>
          <td>${_money(m.amount_cents, m.currency||c.currency)}</td>
          <td>${esc((m.due_at||'').slice(0,10)||'—')}</td>
          <td><span class="status-pill">${esc(m.status||'')}</span></td>
        </tr>`).join('')}
      </tbody></table></div>`;
  }
  const evidence=_canSubmitEvidence(c)&&businessId
    ?`<div class="drawer-actions" style="margin-top:10px">
        <button type="button" data-be-payment-evidence="${esc(String(businessId))}" data-be-purchase-id="${esc(c.purchase_id||'')}" data-be-currency="${esc(c.currency||'USD')}" data-be-outstanding="${esc(String(c.outstanding_balance_cents||0))}">Submit bank evidence (PENDING)</button>
        <span class="hint">AppointHive verifies — Sales OS cannot mark paid</span>
      </div>`
    :'';
  return `<div class="sales-table-wrap"><table class="sales-table"><tbody>
    ${rows.map(([k,v])=>`<tr><td style="width:140px"><strong>${esc(k)}</strong></td><td>${esc(String(v))}</td></tr>`).join('')}
  </tbody></table></div>${mileHtml}${evidence}`;
}

/** Bank/manual evidence → AH PENDING only. */
export async function submitPaymentEvidenceFlow({businessId, purchaseId='', currency='USD', outstandingCents=0}={}){
  if(!businessId){ toast('Missing prospect',true); return null; }
  const defaultAmt=outstandingCents>0?(Number(outstandingCents)/100).toFixed(2):'';
  const form=await uiForm({
    title:'Submit payment evidence to AppointHive',
    message:'Sends bank/manual reference as PENDING. AppointHive Super Admin verifies receipt — this never marks paid.',
    confirmLabel:'Submit evidence',
    fields:[
      {name:'method', label:'Method', type:'select', required:true, value:'BANK_TRANSFER',
        options:[
          {value:'BANK_TRANSFER', label:'Bank transfer'},
          {value:'MANUAL_INVOICE', label:'Manual invoice'},
        ]},
      {name:'amount', label:`Amount (${currency||'USD'})`, type:'number', required:true, value:defaultAmt},
      {name:'reference_note', label:'Reference / note', type:'textarea', rows:2, placeholder:'Bank ref, remitter name…'},
    ],
  });
  if(!form) return null;
  const amount=Number(form.amount);
  if(!(amount>=0) || Number.isNaN(amount)){ toast('Enter a valid amount',true); return null; }
  const res=await api('/api/sales/be/payment-evidence',{
    method:'POST',
    body:{
      business_id:Number(businessId),
      purchase_id:purchaseId||undefined,
      method:form.method,
      amount_cents:Math.round(amount*100),
      currency:currency||'USD',
      reference_note:form.reference_note||'',
    },
  });
  toast(res.label||'Evidence submitted — AppointHive verifies');
  return res;
}

export function bindCommercialPanelActions(root, {onDone}={}){
  if(!root) return;
  root.querySelectorAll('[data-be-payment-evidence]').forEach(btn=>{
    btn.addEventListener('click', async()=>{
      try{
        await submitPaymentEvidenceFlow({
          businessId:btn.dataset.bePaymentEvidence,
          purchaseId:btn.dataset.bePurchaseId||'',
          currency:btn.dataset.beCurrency||'USD',
          outstandingCents:Number(btn.dataset.beOutstanding||0),
        });
        if(typeof onDone==='function') await onDone();
      }catch(err){ toast(err.message||'Could not submit evidence',true); }
    });
  });
}

export function beCatalogIsPlan(sku){
  const t=String((sku&&sku.productType)||'').toUpperCase();
  return t==='EDITION' || t==='PLAN' || t==='PACKAGE';
}

export function beCatalogRows(data){
  if(!data || typeof data!=='object') return [];
  // BE /catalog returns { productLine, skus:[{ name, productKey, productType, options:[{ priceCents, ...}] }] }
  const skus=data.skus;
  if(Array.isArray(skus) && skus.length){
    const flat=[];
    for(const sku of skus){
      const isPlan=beCatalogIsPlan(sku);
      const planName=sku.name||sku.productName||sku.productKey||'';
      const opts=Array.isArray(sku.options)?sku.options:[];
      const active=opts.filter(o=>o && o.active!==false);
      const use=active.length?active:(opts.length?opts:[{}]);
      for(const opt of use){
        flat.push({
          ...sku,
          ...opt,
          _isPlan:isPlan,
          label:planName,
          name:planName,
          // Edition column: plan display name only — never addon productKey/codes
          edition:isPlan?planName:'',
          productLine:sku.catalogKind||sku.productLine||data.productLine||'',
          productRevisionId:opt.optionId||sku.productRevisionId||sku.id||'',
          priceCents:opt.priceCents!=null?opt.priceCents:sku.priceCents,
          currency:opt.currency||sku.currency||'USD',
          billingInterval:opt.billingInterval||opt.purchaseType||'',
        });
      }
    }
    // Plans first, then addons (stable within each group)
    flat.sort((a,b)=>Number(!!b._isPlan)-Number(!!a._isPlan));
    return flat;
  }
  const candidates=[data.items, data.packages, data.products, data.catalog, data.revisions, data.rows];
  for(const c of candidates){ if(Array.isArray(c) && c.length) return c; }
  if(Array.isArray(data)) return data;
  // Nested { STANDALONE: [...] } style
  for(const v of Object.values(data)){
    if(Array.isArray(v) && v.length && typeof v[0]==='object') return v;
  }
  return [];
}

export function formatBePrice(row){
  const cents=row.priceCents!=null?Number(row.priceCents):(row.price_cents!=null?Number(row.price_cents):null);
  const amount=row.price!=null?Number(row.price):(row.amount!=null?Number(row.amount):null);
  const cur=row.currency||row.Currency||'USD';
  const interval=(row.billingInterval||row.purchaseType||'').toString().replace(/_/g,' ');
  let price='—';
  if(cents!=null && !Number.isNaN(cents)) price=`${cur} ${(cents/100).toFixed(2)}`;
  else if(amount!=null && !Number.isNaN(amount)) price=`${cur} ${amount.toFixed(2)}`;
  if(price!=='—' && interval) return `${price} · ${interval}`;
  return price;
}

export function renderBeCatalogBody(rows, line){
  const parts=[];
  let sawAddonSep=false;
  const hasPlans=rows.some(r=>r._isPlan);
  const hasAddons=rows.some(r=>r && r._isPlan===false);
  for(const r of rows){
    if(hasPlans && hasAddons && r._isPlan===false && !sawAddonSep){
      sawAddonSep=true;
      parts.push(`<tr class="ah-catalog-sep"><td colspan="5"><strong>Add-ons</strong></td></tr>`);
    }
    const name=r.label||r.name||r.productName||r.product_name||r.title||r.productId||r.product_id||'—';
    const edition=r._isPlan ? (r.edition||r.name||'') : '';
    const id=r.productRevisionId||r.optionId||r.product_revision_id||r.revisionId||r.id||r.sku||'';
    const pl=r.productLine||r.catalogKind||r.product_line||line;
    parts.push(`<tr>
      <td><strong>${esc(name)}</strong></td>
      <td>${esc(edition)}</td>
      <td>${esc(formatBePrice(r))}</td>
      <td style="overflow:hidden;text-overflow:ellipsis"><code>${esc(String(id))}</code></td>
      <td>${esc(pl)}</td>
    </tr>`);
  }
  return parts.join('');
}

export async function loadBeCatalog(){
  const host=$('beCatalogHost');
  if(!host) return;
  if(!(beConnCache&&beConnCache.connected)){
    host.innerHTML=`<div class="empty-card">Connect AppointHive first. <button type="button" class="chip ok" data-goto="appoinHive:connect">Open Connect</button></div>`;
    return;
  }
  const line=(($('beCatalogLine')&&$('beCatalogLine').value)||'STANDALONE').toUpperCase();
  host.textContent='Loading catalog from AppointHive…';
  try{
    const data=await api('/api/sales/be/catalog?product_line='+encodeURIComponent(line));
    const rows=beCatalogRows(data);
    if(!rows.length){
      const keys=data&&typeof data==='object'&&!Array.isArray(data)?Object.keys(data).slice(0,12).join(', '):'(array/empty)';
      host.innerHTML=`<div class="empty-card">No catalog rows for ${esc(line)}. Response keys: ${esc(keys||'—')}</div>`;
      return;
    }
    host.innerHTML=`<div class="sales-table-wrap"><table class="sales-table" style="table-layout:fixed;width:100%"><thead><tr>
      <th style="width:28%">Name</th><th style="width:16%">Edition</th><th style="width:20%">Price</th><th style="width:24%">Option id</th><th style="width:12%">Line</th>
    </tr></thead><tbody>
      ${renderBeCatalogBody(rows, line)}
    </tbody></table></div>`;
  }catch(e){
    host.innerHTML=`<div class="pay-status err">${esc(e.message||'Failed to load catalog')}</div>`;
  }
}

export function isOwnerRole(){ return String(currentRole||'').toLowerCase()==='owner'; }

export function applyBeConnectOwnerGate(){
  const owner=isOwnerRole();
  ['beSaveManual','beGenPair','beTestConn','beDisconnect','beHeartbeatBtn'].forEach(id=>{
    const el=$(id); if(!el) return;
    el.disabled=!owner;
    el.title=owner?'':'Owner only — ask an owner to connect or disconnect';
  });
  const hint=$('beConnectOwnerHint');
  if(hint) hint.classList.toggle('hidden', owner);
}
