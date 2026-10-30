import { $ } from '../core/dom.js';

import { esc, api, toast, fmtDateTime } from '../core/api.js';

import { pageSlice, renderPager, ensurePagerHost, pagerState } from '../core/pager.js';

import { uiConfirm, uiForm } from '../core/dialog.js';

import { registerPanel } from './registry.js';

import { B } from '../app/bridge.js';

import { safeRefresh } from '../shell/router.js';

import { pickProspectField } from '../features/prospect-picker.js';



let demosCache = [];


export function renderDemosTable(){
  const rows=demosCache||[];
  const tb=$('demosTable').querySelector('tbody');
  const pagerHost=ensurePagerHost($('demosTable').closest('.sales-table-wrap'),'demosPager');
  if(!rows.length){
    tb.innerHTML='<tr><td colspan="6" data-label=""><div class="empty-card">No demos yet. Log one after a discovery call.</div></td></tr>';
    if(pagerHost) pagerHost.innerHTML='';
    return;
  }
  const slice=pageSlice(rows,'demos');
  tb.innerHTML=slice.items.map(d=>`<tr>
    <td data-label="ID">${d.id}</td>
    <td data-label="Prospect"><button type="button" data-open-prospect="${d.business_id}">#${d.business_id}</button></td>
    <td data-label="Outcome">${esc(d.outcome||'—')}</td>
    <td data-label="Next step">${esc(d.agreed_next_step||'—')}</td>
    <td data-label="When">${esc(fmtDateTime(d.demo_at||d.created_at||''))}</td>
    <td data-label="Actions"><button type="button" class="danger" data-del-demo="${d.id}">Delete</button></td>
  </tr>`).join('');
  renderPager(pagerHost, slice, renderDemosTable);
  tb.querySelectorAll('[data-open-prospect]').forEach(btn=>btn.addEventListener('click',()=>B.openProspectById(btn.dataset.openProspect)));
  tb.querySelectorAll('[data-del-demo]').forEach(btn=>btn.addEventListener('click',async()=>{
    const ok=await uiConfirm({title:'Delete demo note?', confirmLabel:'Delete', danger:true});
    if(!ok) return;
    await api('/api/sales/demos/'+btn.dataset.delDemo,{method:'DELETE'});
    toast('Demo deleted'); await loadDemos();
  }));
}

export async function loadDemos(){
  const data=await api('/api/sales/demos?limit=200');
  demosCache=data.demos||[];
  if(pagerState.demos) pagerState.demos.page=1;
  renderDemosTable();
  await loadDemoAssets().catch(()=>{});
}

export async function loadDemoAssets(){
  const panel=$('demoAssetsPanel');
  if(!panel) return;
  const data=await api('/api/sales/demo-assets');
  const rows=data.assets||[];
  if(!rows.length){
    panel.innerHTML='<div class="empty-card">No demo links or videos yet.</div>';
    return;
  }
  panel.innerHTML=`<div class="sales-table-wrap"><table class="sales-table"><thead><tr><th>Title</th><th>Kind</th><th>Link</th><th></th></tr></thead><tbody>
    ${rows.map(a=>{
      const href=a.url||a.file_url||'';
      return `<tr>
        <td><strong>${esc(a.title)}</strong><div class="sub">${esc(a.notes||'')}</div></td>
        <td>${esc(a.kind)}</td>
        <td>${href?`<a class="chip ok" href="${esc(href)}" target="_blank" rel="noopener">Open</a>`:'—'}</td>
        <td><div class="action-stack">
          <button type="button" data-copy-demo="${a.id}">Copy for message</button>
          <button type="button" class="danger" data-del-demo-asset="${a.id}">Delete</button>
        </div></td>
      </tr>`;
    }).join('')}
  </tbody></table></div>`;
  const byId=Object.fromEntries(rows.map(a=>[String(a.id),a]));
  panel.querySelectorAll('[data-copy-demo]').forEach(btn=>btn.addEventListener('click',async()=>{
    const a=byId[btn.dataset.copyDemo];
    const href=a.url||(a.file_url?(location.origin+a.file_url):'');
    const text=`Hi {{name}},\n\nHere’s a short AppointHive demo: ${href}\n\nLet me know what you think.\n`;
    try{ await navigator.clipboard.writeText(text); toast('Demo message copied'); }
    catch(e){ toast('Could not copy',true); }
  }));
  panel.querySelectorAll('[data-del-demo-asset]').forEach(btn=>btn.addEventListener('click',async()=>{
    const ok=await uiConfirm({title:'Delete demo asset?', confirmLabel:'Delete', danger:true});
    if(!ok) return;
    await api('/api/sales/demo-assets/'+btn.dataset.delDemoAsset,{method:'DELETE'});
    toast('Deleted'); await loadDemoAssets();
  }));
}

export async function addDemoAssetFlow(){
  const form=await uiForm({
    title:'Add demo link / URL',
    confirmLabel:'Save',
    fields:[
      {name:'title', label:'Title', required:true, placeholder:'AppointHive 3-min walkthrough'},
      {name:'kind', label:'Kind', type:'select', value:'video', options:[
        {value:'video',label:'Video'},{value:'link',label:'Link'},{value:'file',label:'File URL'}
      ]},
      {name:'url', label:'URL', required:true, placeholder:'https://loom.com/… or YouTube'},
      {name:'notes', label:'Notes', type:'textarea', rows:2, placeholder:'Optional internal note'}
    ]
  });
  if(!form) return;
  await api('/api/sales/demo-assets',{method:'POST',body:form});
  toast('Demo asset saved'); await loadDemoAssets();
}

export async function uploadDemoAssetFile(file){
  const fd=new FormData();
  fd.append('file', file);
  fd.append('title', file.name||'Demo video');
  await api('/api/sales/demo-assets/upload',{method:'POST',body:fd});
  toast('Video uploaded'); await loadDemoAssets();
}

export async function createDemoFlow(){
  const bid=await pickProspectField('Demo for which prospect?');
  if(bid==null) return;
  const form=await uiForm({
    title:'Log demo',
    confirmLabel:'Save demo',
    fields:[
      {name:'outcome', label:'Outcome', required:true, placeholder:'completed / no-show / reschedule'},
      {name:'agreed_next_step', label:'Agreed next step', type:'textarea', rows:2},
      {name:'call_notes', label:'Notes', type:'textarea', rows:2}
    ]
  });
  if(!form) return;
  await api('/api/sales/prospects/'+bid+'/demo',{method:'POST',body:form});
  toast('Demo logged'); await loadDemos();
}


registerPanel({ id: 'demos', refresh: safeRefresh(loadDemos) });

