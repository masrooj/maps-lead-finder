import { $ } from '../core/dom.js';

import { esc, api, toast, fmtMoney } from '../core/api.js';

import { B } from '../app/bridge.js';

let currentRole = 'owner';

export function setBeRole(role){ currentRole = role || 'owner'; }



export async function refreshBeConnBanners(){
  try{
    const conn=await api('/api/sales/be/connection');
    beConnCache=conn||{};
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
    ['Base URL', conn.base_url||'—'],
    ['Bridge', conn.bridge_enabled?'enabled':'disabled'],
    ['Secret', conn.secret_configured?(conn.secret_hint||'configured'):'not set'],
    ['Webhook', conn.webhook_path||'/api/webhooks/booking-engine'],
    ['Last health', conn.last_health_at||'—'],
    ['Pairing', conn.pairing_code_active?(`active until ${conn.pairing_expires_at||'—'}`):'none'],
    ['Product', conn.product_name||'AppointHive Sales OS'],
  ];
  host.innerHTML=`<strong>${conn.connected?'Connected':'Not connected'}</strong>
    <div class="sales-table-wrap" style="margin-top:8px"><table class="sales-table"><tbody>
      ${rows.map(([k,v])=>`<tr><td style="width:140px"><strong>${esc(k)}</strong></td><td>${esc(String(v))}</td></tr>`).join('')}
    </tbody></table></div>
    ${conn.connected?'':`<div class="sub" style="margin-top:8px">Not connected — open <button type="button" class="chip ok" data-goto="appoinHive:connect">Connect</button> to save URL/secret or pair, then Test connection.</div>`}`;
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
  ['beSaveManual','beGenPair','beTestConn','beDisconnect'].forEach(id=>{
    const el=$(id); if(!el) return;
    el.disabled=!owner;
    el.title=owner?'':'Owner only — ask an owner to connect or disconnect';
  });
  const hint=$('beConnectOwnerHint');
  if(hint) hint.classList.toggle('hidden', owner);
}
