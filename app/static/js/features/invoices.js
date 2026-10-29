import { $ } from '../core/dom.js';
import { esc, api, toast, fmtMoney, fmtDateTime } from '../core/api.js';
import { pageSlice, renderPager, ensurePagerHost } from '../core/pager.js';
import { openUiDialog, closeUiDialog, uiConfirm, uiForm } from '../core/dialog.js';
import { B } from '../app/bridge.js';

export const CURRENCY_OPTIONS=['USD','AUD','EUR','GBP','NZD','CAD','AED','SAR','SGD','INR'];

function qrUrlFor(data) {
  if (typeof B.qrUrlFor === 'function') return B.qrUrlFor(data);
  const payload = encodeURIComponent(data || '');
  return 'https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=' + payload;
}

async function reloadMoney() {
  if (typeof B.loadMoney === 'function') await B.loadMoney();
}



export function groupByLeadName(rows, nameKey='business_name', idKey='business_id'){
  const map=new Map();
  (rows||[]).forEach(r=>{
    const id=r[idKey];
    const key=id!=null?String(id):('_'+ (r[nameKey]||'unknown'));
    if(!map.has(key)){
      map.set(key,{
        business_id:id,
        name:r[nameKey]||(id!=null?('#'+id):'Unknown'),
        items:[]
      });
    }
    map.get(key).items.push(r);
  });
  return [...map.values()].sort((a,b)=>String(a.name).localeCompare(String(b.name)));
}

export function invoicePayload(i){
  if(!i) return '';
  const url=i.hosted_url||'';
  if(url && !String(url).includes('/placeholder/') && String(url).startsWith('http')) return url;
  return i.pay_instructions||url||'';
}

export function invoicePreviewHtml(v){
  const ent=v.billing_entity||{};
  const biz=v.business||{};
  const from=(ent.lines&&ent.lines.length)?ent.lines:( [ent.legal_name,ent.address,ent.tax_id?('Tax ID: '+ent.tax_id):''].filter(Boolean));
  const to=(biz.lines&&biz.lines.length)?biz.lines:( [biz.name,biz.address,[biz.city,biz.country].filter(Boolean).join(', '),biz.email,biz.phone].filter(Boolean));
  const rows=(v.line_items||[]).map(li=>{
    const qty=li.qty!=null?li.qty:(li.quantity!=null?li.quantity:1);
    const unit=li.unit_fmt||fmtMoney(li.unit_amount!=null?li.unit_amount:li.amount,v.currency);
    const tot=li.line_total_fmt||fmtMoney(li.line_total!=null?li.line_total:(Number(li.amount||0)*Number(qty||1)),v.currency);
    return `<tr><td>${esc(li.name||li.description||'Item')}</td><td class="num">${esc(String(qty))}</td><td class="num">${esc(unit)}</td><td class="num">${esc(tot)}</td></tr>`;
  }).join('')||`<tr><td colspan="4" class="sub">No line items</td></tr>`;
  const issued=(v.created_at||'').slice(0,19).replace('T',' ');
  const due=(v.expires_at||'').slice(0,19).replace('T',' ');
  const logo=v.logo_url?`<img class="inv-logo" src="${esc(v.logo_url)}" alt="Logo">`:'';
  let totals=`<div>Subtotal</div><div class="num">${esc(v.subtotal_fmt||fmtMoney(v.subtotal,v.currency))}</div>`;
  if(Number(v.discount_amount||0)>0) totals+=`<div>${esc(v.discount_reason||'Discount')}</div><div class="num">−${esc(v.discount_fmt||fmtMoney(v.discount_amount,v.currency))}</div>`;
  if(Number(v.tax_amount||0)>0 || v.tax_rate){
    let tl=v.tax_label||'Tax';
    if(v.tax_rate!=null) tl+=` (${(Number(v.tax_rate)*100).toFixed(1)}%)`;
    totals+=`<div>${esc(tl)}</div><div class="num">${esc(v.tax_fmt||fmtMoney(v.tax_amount,v.currency))}</div>`;
  }
  totals+=`<div class="grand">Total</div><div class="grand num">${esc(v.total_fmt||fmtMoney(v.total,v.currency))}</div>`;
  const payBits=[];
  if(v.hosted_url&&!String(v.hosted_url).includes('/placeholder/')) payBits.push(`<div><a class="chip ok" href="${esc(v.hosted_url)}" target="_blank" rel="noopener">Open pay link</a></div>`);
  if(v.pay_instructions) payBits.push(`<pre style="white-space:pre-wrap;margin:6px 0 0;font:12px/1.45 ui-monospace,monospace">${esc(v.pay_instructions)}</pre>`);
  return `<div class="invoice-preview">
    <div class="inv-top">${logo}<div class="inv-title"><strong>INVOICE</strong><span>#${esc(v.number||'')}</span><div class="sub"><span class="status-pill ${esc(v.status||'')}">${esc(v.status||'')}</span>${v.is_test_mode?' · test':''}</div></div></div>
    <div class="inv-cols"><div><h5>From</h5><p>${esc(from.join('\n'))}</p></div><div><h5>Bill to</h5><p>${esc(to.join('\n'))}</p></div></div>
    <div class="inv-meta">${issued?`<span>Issued ${esc(issued)}</span>`:''}${due?`<span>Due ${esc(due)}</span>`:''}${v.proposal_reference?`<span>Proposal ${esc(v.proposal_reference)}</span>`:''}${v.provider?`<span>${esc(v.provider)}</span>`:''}</div>
    ${v.proposal_title?`<p class="sub" style="margin:0 0 8px">Re: ${esc(v.proposal_title)}</p>`:''}
    <table><thead><tr><th>Description</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Amount</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="inv-totals">${totals}</div>
    ${payBits.length?`<div class="inv-pay"><h5>Payment</h5>${payBits.join('')}</div>`:''}
    <div class="inv-actions">
      <a class="chip ok" href="/api/sales/invoices/${v.id}/pdf" target="_blank" rel="noopener">Download PDF</a>
      ${v.business&&v.business.id?`<button type="button" data-open-prospect="${v.business.id}">Open lead</button>`:''}
    </div>
  </div>`;
}

export function invoiceCardHtml(i,{compact=false}={}){
  const payload=invoicePayload(i);
  const live=payload && String(payload).startsWith('http') && !String(payload).includes('/placeholder/');
  const qr=payload?qrUrlFor(payload):'';
  const canVoid=i.status==='open'||i.status==='draft';
  if(compact){
    return `
    <article class="task-row" data-inv-card="${i.id}">
      <div>
        <div class="biz">${esc(i.number||('#'+i.id))} · ${esc(i.provider||'manual')} · <span class="status-pill ${esc(i.status||'')}">${esc(i.status||'')}</span></div>
        <div class="sub">${esc(fmtMoney(i.total,i.currency))}${i.is_test_mode?' · test':''}</div>
      </div>
      <div class="action-stack">
        <button type="button" data-inv-view="${i.id}">View</button>
        <a class="chip ok" href="/api/sales/invoices/${i.id}/pdf" target="_blank" rel="noopener">PDF</a>
        ${i.business_id?`<button type="button" data-open-prospect="${i.business_id}">Open lead</button>`:''}
        <button type="button" data-goto="money:invoices">Money</button>
      </div>
    </article>`;
  }
  return `
  <div class="paylink-card" data-inv-card="${i.id}">
    ${qr?`<img class="paylink-qr" src="${esc(qr)}" alt="QR ${esc(i.number||'')}" loading="lazy">`:''}
    <div class="paylink-meta">
      <strong>${esc(i.number||('#'+i.id))}</strong>
      <span class="status-pill ${esc(i.status||'')}">${esc(i.status||'')}</span>
      <span class="sub"> · ${esc(i.provider||'manual')}${i.is_test_mode?' · test':''}</span>
      <div class="sub">${esc(fmtMoney(i.total,i.currency))} · ${esc(i.business_name||('#'+(i.business_id||'')))}</div>
      ${payload?`<div class="paylink-url">${esc(payload)}</div>`:'<div class="hint">No pay URL or instructions yet</div>'}
      <div class="drawer-actions" style="margin-top:8px">
        <button type="button" data-inv-view="${i.id}">View</button>
        <a class="chip ok" href="/api/sales/invoices/${i.id}/pdf" target="_blank" rel="noopener">PDF</a>
        ${live?`<button type="button" class="primary" data-inv-open="${i.id}">Open link</button>`:''}
        ${payload?`<button type="button" data-inv-copy-pay="${i.id}">Copy</button>`:''}
        ${qr?`<a class="chip ok" href="${esc(qr)}" download="invoice-${esc(i.number||i.id)}-qr.png" target="_blank" rel="noopener">QR</a>
        <button type="button" data-inv-print-qr="${i.id}">Print QR</button>`:''}
        <button type="button" data-inv-compose="${i.id}">Copy message</button>
        <button type="button" class="primary" data-inv-send="${i.id}">Email + PDF</button>
        ${i.status==='open'?`<button type="button" class="primary" data-pay="${i.id}" data-number="${esc(i.number)}" data-total="${esc(String(i.total))}" data-currency="${esc(i.currency||'')}">Confirm paid</button>`:''}
        ${canVoid?`<button type="button" class="danger" data-inv-void="${i.id}" data-number="${esc(i.number||'')}">Void</button>`:''}
        ${i.business_id?`<button type="button" data-open-prospect="${i.business_id}">Open lead</button>`:''}
      </div>
    </div>
  </div>`;
}

export function bindInvoiceCardActions(root, invoices, {onPaid, onVoided}={}){
  if(!root) return;
  const byId=Object.fromEntries((invoices||[]).map(i=>[String(i.id), i]));
  root.querySelectorAll('[data-open-prospect]').forEach(btn=>btn.addEventListener('click',()=>B.openProspectById(btn.dataset.openProspect)));
  root.querySelectorAll('[data-inv-open]').forEach(btn=>btn.addEventListener('click',()=>{
    const url=invoicePayload(byId[btn.dataset.invOpen]);
    if(url&&String(url).startsWith('http')) window.open(url,'_blank','noopener');
  }));
  root.querySelectorAll('[data-inv-copy-pay]').forEach(btn=>btn.addEventListener('click',async()=>{
    const text=invoicePayload(byId[btn.dataset.invCopyPay])||'';
    try{ await navigator.clipboard.writeText(text); toast('Copied'); }catch(e){ toast('Could not copy',true); }
  }));
  root.querySelectorAll('[data-inv-print-qr]').forEach(btn=>btn.addEventListener('click',()=>{
    const inv=byId[btn.dataset.invPrintQr]; if(!inv) return;
    const payload=invoicePayload(inv);
    const w=window.open('','_blank','noopener,width=420,height=520');
    if(!w){ toast('Pop-up blocked',true); return; }
    w.document.write(`<!doctype html><title>QR</title><body style="font-family:sans-serif;text-align:center;padding:24px"><h2>${esc(inv.provider||'')} · ${esc(inv.number||'')}</h2><img src="${esc(qrUrlFor(payload))}" width="220" height="220"><p style="word-break:break-all">${esc(payload)}</p>`+'<scr'+'ipt>window.onload=()=>window.print()</scr'+'ipt></body>');
    w.document.close();
  }));
  root.querySelectorAll('[data-inv-view]').forEach(btn=>btn.addEventListener('click',async()=>{
    try{
      const v=await api('/api/sales/invoices/'+btn.dataset.invView+'/view');
      const dlg=openUiDialog({
        title:`Invoice ${v.number||''}`,
        message:'',
        bodyHtml:invoicePreviewHtml(v),
        confirmLabel:'Close',
        cancelLabel:'Download PDF',
        focusSelector:'.invoice-preview'
      });
      const host=$('uiDialogBody');
      if(host) host.querySelectorAll('[data-open-prospect]').forEach(b=>b.addEventListener('click',()=>{
        closeUiDialog(true);
        B.openProspectById(b.dataset.openProspect);
      }));
      const ok=await dlg;
      if(ok===null) window.open('/api/sales/invoices/'+v.id+'/pdf','_blank','noopener');
    }catch(e){ toast(e.message,true); }
  }));
  root.querySelectorAll('[data-inv-compose]').forEach(btn=>btn.addEventListener('click',async()=>{
    try{
      const c=await api('/api/sales/invoices/'+btn.dataset.invCompose+'/compose-copy');
      const text=`Subject: ${c.subject}\n\n${c.body}\n\n—\n${c.note||''}\nPDF: ${location.origin}${c.pdf_url}`;
      await navigator.clipboard.writeText(text);
      toast(c.smtp_ready?'Message copied (or use Email + PDF)':'Invoice message copied — download PDF and attach manually');
      if(!c.smtp_ready) window.open(c.pdf_url,'_blank');
    }catch(e){ toast(e.message,true); }
  }));
  root.querySelectorAll('[data-inv-send]').forEach(btn=>btn.addEventListener('click',async()=>{
    try{
      const c=await api('/api/sales/invoices/'+btn.dataset.invSend+'/compose-copy');
      const form=await uiForm({
        title:'Send invoice email with PDF',
        message:c.smtp_ready?'Sends via SMTP with PDF attached.':'SMTP not ready — configure Mail under Money → Settings.',
        confirmLabel:'Send',
        fields:[
          {name:'to', label:'To', required:true, value:c.to||''},
          {name:'subject', label:'Subject', required:true, value:c.subject||''},
          {name:'body', label:'Body', type:'textarea', rows:8, value:c.body||''}
        ]
      });
      if(!form) return;
      await api('/api/sales/invoices/'+btn.dataset.invSend+'/send-email',{method:'POST',body:{to:form.to, subject:form.subject, body:form.body, template_id:c.template_id||null}});
      toast('Invoice emailed with PDF');
    }catch(e){ toast(e.message,true); }
  }));
  root.querySelectorAll('[data-pay]').forEach(btn=>btn.addEventListener('click',async()=>{
    const form=await uiForm({
      title:'Confirm payment received',
      message:`Record cash for invoice ${btn.dataset.number} (${fmtMoney(btn.dataset.total,btn.dataset.currency)}). Owner only.`,
      confirmLabel:'Mark paid',
      fields:[{name:'provider_ref', label:'Payment reference', placeholder:'Bank ref / Stripe id (optional)'}]
    });
    if(!form) return;
    try{
      await api('/api/sales/invoices/'+btn.dataset.pay+'/confirm-paid',{method:'POST',body:{provider_ref:form.provider_ref||''}});
      toast('Payment recorded');
      if(typeof onPaid==='function') await onPaid();
      else await reloadMoney();
    }catch(e){ toast(e.message,true); }
  }));
  root.querySelectorAll('[data-inv-void]').forEach(btn=>btn.addEventListener('click',async()=>{
    const form=await uiForm({
      title:'Void invoice '+btn.dataset.number+'?',
      message:'Owner only. Paid invoices cannot be voided — refund instead. Related follow-up tasks are cancelled.',
      confirmLabel:'Void',
      danger:true,
      fields:[{name:'reason', label:'Reason (optional)', placeholder:'Duplicate, wrong amount, cancelled deal…'}]
    });
    if(!form) return;
    try{
      await api('/api/sales/invoices/'+btn.dataset.invVoid+'/void',{method:'POST',body:{reason:form.reason||''}});
      toast('Invoice voided');
      if(typeof onVoided==='function') await onVoided();
      else if(typeof onPaid==='function') await onPaid();
      else await reloadMoney();
    }catch(e){ toast(e.message,true); }
  }));
  root.querySelectorAll('[data-goto]').forEach(btn=>btn.addEventListener('click',()=>B.setAppPanel(btn.dataset.goto)));
}

export function renderGroupedInvoiceCards(host, invoices, {pagerKey, onRender, onPaid, onVoided, compact}={}){
  if(!host) return;
  const list=invoices||[];
  const pagerHost=pagerKey?ensurePagerHost(host,pagerKey+'Pager'):null;
  if(!list.length){
    host.innerHTML='<div class="empty-card">No invoices yet. Create one from a prospect drawer → Proposal / pay.</div>';
    if(pagerHost) pagerHost.innerHTML='';
    return;
  }
  const groups=groupByLeadName(list);
  const flatForPage=groups; // page by lead groups
  const slice=pagerKey?pageSlice(flatForPage,pagerKey):{items:flatForPage};
  host.innerHTML=slice.items.map(g=>`
    <section class="lead-group">
      <div class="lead-group-head">
        <strong>${esc(g.name)}</strong>
        <div class="action-stack">
          ${g.business_id?`<button type="button" data-open-prospect="${g.business_id}">Open lead</button>`:''}
          ${compact?`<button type="button" data-goto="money:invoices">Open in Money</button>`:''}
          <span class="sub">${g.items.length} invoice${g.items.length===1?'':'s'}</span>
        </div>
      </div>
      <div class="lead-group-body">${g.items.map(i=>invoiceCardHtml(i,{compact})).join('')}</div>
    </section>`).join('');
  if(pagerKey) renderPager(pagerHost, slice, onRender||(()=>renderGroupedInvoiceCards(host,invoices,{pagerKey,onRender,onPaid,onVoided,compact})));
  bindInvoiceCardActions(host, list, {onPaid, onVoided: onVoided||onPaid});
}

export function renderMoneyInvoices(){
  renderGroupedInvoiceCards($('moneyInvoicesHost'), B.moneyInvoicesCache, {
    pagerKey:'moneyInvoices',
    onRender:renderMoneyInvoices,
    onPaid:async()=>{ await reloadMoney(); if(B.commsTab==='invoices') await loadCommsInvoices(); }
  });
}

export function renderCommsInvoices(){
  renderGroupedInvoiceCards($('commsInvoicesHost'), B.commsInvoicesCache, {
    pagerKey:'commsInvoices',
    onRender:renderCommsInvoices,
    onPaid:async()=>{ await loadCommsInvoices(); if(B.appPanel==='money') await reloadMoney(); },
    compact:true
  });
}

export async function loadCommsInvoices(){
  const data=await api('/api/sales/invoices');
  B.commsInvoicesCache=data.invoices||[];
  if(pagerState.commsInvoices) pagerState.commsInvoices.page=1;
  renderCommsInvoices();
}

export function currencySelectHtml(selected, id){
  const cur=((selected||'USD')+'').trim().toUpperCase()||'USD';
  const opts=CURRENCY_OPTIONS.slice();
  if(cur && !opts.includes(cur)) opts.unshift(cur);
  // USD first as default, then the prospect's current (if not USD), then the rest
  const ordered=[...new Set(['USD', cur, ...opts].filter(Boolean))];
  return `<select id="${esc(id||'drawerCurrency')}" aria-label="Currency">${ordered.map(c=>`<option value="${esc(c)}" ${c===cur?'selected':''}>${esc(c)}${c==='USD'?' · default':''}</option>`).join('')}</select>`;
}
