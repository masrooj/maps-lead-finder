import { $ } from '../core/dom.js';

import { esc, api, toast } from '../core/api.js';

import { openUiDialog } from '../core/dialog.js';

import { B } from '../app/bridge.js';



export function wireProspectPicker(root, hiddenName, labelText){
  const search=root.querySelector('[data-pp-search]');
  const list=root.querySelector('[data-pp-list]');
  const hidden=root.querySelector('[name="'+hiddenName+'"]');
  if(!search||!list||!hidden) return;
  let timer=null;
  const render=(rows)=>{
    if(!rows.length){ list.innerHTML='<div class="notif-empty">No matches</div>'; list.classList.add('open'); return; }
    list.innerHTML=rows.map(p=>`<button type="button" data-id="${p.id}" data-label="${esc(p.name||'')} · ${esc(p.city||'')}">
      <div class="biz">${esc(p.name||'—')}</div><div class="sub">#${p.id} · ${esc(p.city||'')}</div>
    </button>`).join('');
    list.classList.add('open');
    list.querySelectorAll('button').forEach(btn=>btn.addEventListener('click',()=>{
      hidden.value=btn.dataset.id;
      search.value=btn.dataset.label||('#'+btn.dataset.id);
      list.classList.remove('open');
    }));
  };
  search.addEventListener('input',()=>{
    clearTimeout(timer);
    const q=search.value.trim();
    hidden.value='';
    if(q.length<2){ list.classList.remove('open'); return; }
    timer=setTimeout(async()=>{
      try{
        const data=await api('/api/sales/search?q='+encodeURIComponent(q));
        render(data.prospects||[]);
      }catch(e){ list.innerHTML=`<div class="notif-empty">${esc(e.message)}</div>`; list.classList.add('open'); }
    },220);
  });
  search.addEventListener('focus',()=>{ if(list.innerHTML) list.classList.add('open'); });
}

export async function pickProspectField(label='Select prospect'){
  const bodyHtml=`
    <div class="field"><label>${esc(label)} *</label>
      <div class="prospect-picker" id="uiProspectPicker">
        <input data-pp-search type="search" placeholder="Search name, city, or email…" autocomplete="off">
        <input type="hidden" name="business_id" id="uiField_business_id" value="">
        <div class="prospect-picker-list" data-pp-list></div>
      </div>
    </div>`;
  const done=openUiDialog({title:label, message:'Search and pick a prospect — no raw ID needed.', bodyHtml, confirmLabel:'Continue', focusSelector:'[data-pp-search]'});
  requestAnimationFrame(()=>{
    const root=$('uiDialogBody')&&$('uiDialogBody').querySelector('.prospect-picker');
    if(root) wireProspectPicker(root,'business_id');
  });
  const ok=await done;
  if(!ok) return null;
  const id=Number(($('uiField_business_id')&&$('uiField_business_id').value)||0);
  if(!id){
    toast('Pick a prospect from the search list',true);
    return pickProspectField(label);
  }
  return id;
}
