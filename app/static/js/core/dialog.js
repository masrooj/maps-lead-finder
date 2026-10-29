import { $ } from './dom.js';
import { esc, toast } from './api.js';


let _uiDialogResolver = null;


export function closeUiDialog(result){
  const dlg=$('uiDialog');
  if(dlg) dlg.classList.add('hidden');
  const resolve=_uiDialogResolver;
  _uiDialogResolver=null;
  if(resolve) resolve(result);
}

export function openUiDialog({title, message='', bodyHtml='', confirmLabel='Confirm', cancelLabel='Cancel', danger=false, focusSelector=''}){
  return new Promise(resolve=>{
    if(_uiDialogResolver) closeUiDialog(null);
    _uiDialogResolver=resolve;
    $('uiDialogTitle').textContent=title||'Confirm';
    const msg=$('uiDialogMsg');
    if(message){ msg.textContent=message; msg.classList.remove('hidden'); }
    else { msg.textContent=''; msg.classList.add('hidden'); }
    $('uiDialogBody').innerHTML=bodyHtml||'';
    $('uiDialogBody').classList.toggle('hidden', !bodyHtml);
    const conf=$('uiDialogConfirm');
    conf.textContent=confirmLabel;
    conf.className=danger?'primary danger':'primary';
    $('uiDialogCancel').textContent=cancelLabel;
    $('uiDialog').classList.remove('hidden');
    requestAnimationFrame(()=>{
      const focusEl=focusSelector?$('uiDialogBody').querySelector(focusSelector):conf;
      if(focusEl&&focusEl.focus) focusEl.focus();
    });
  });
}

export async function uiConfirm({title='Confirm', message='', confirmLabel='Confirm', cancelLabel='Cancel', danger=false}={}){
  const ok=await openUiDialog({title, message, confirmLabel, cancelLabel, danger});
  return !!ok;
}

export async function uiForm({title, message='', fields=[], confirmLabel='Save', cancelLabel='Cancel', danger=false}={}){
  const bodyHtml=(fields||[]).map(f=>{
    const id='uiField_'+f.name;
    const req=f.required?' required':'';
    const label=`<label for="${id}">${esc(f.label||f.name)}${f.required?' *':''}</label>`;
    if(f.type==='select'){
      const opts=(f.options||[]).map(o=>`<option value="${esc(o.value)}" ${String(o.value)===String(f.value||'')?'selected':''}>${esc(o.label)}</option>`).join('');
      return `<div class="field">${label}<select id="${id}" name="${esc(f.name)}"${req}>${opts}</select></div>`;
    }
    if(f.type==='textarea'){
      return `<div class="field">${label}<textarea id="${id}" name="${esc(f.name)}" rows="${f.rows||3}" placeholder="${esc(f.placeholder||'')}"${req}>${esc(f.value||'')}</textarea></div>`;
    }
    return `<div class="field">${label}<input id="${id}" name="${esc(f.name)}" type="${esc(f.type||'text')}" value="${esc(f.value||'')}" placeholder="${esc(f.placeholder||'')}"${req}></div>`;
  }).join('');
  const first=fields[0]?'#uiField_'+fields[0].name:'';
  const ok=await openUiDialog({title, message, bodyHtml, confirmLabel, cancelLabel, danger, focusSelector:first});
  if(!ok) return null;
  const out={};
  for(const f of fields){
    const el=$('uiField_'+f.name);
    if(!el) continue;
    let v=el.value;
    if(f.required && !String(v||'').trim()){
      toast((f.label||f.name)+' is required', true);
      return uiForm({title, message, fields:fields.map(x=>x.name===f.name?{...x,value:v}:x), confirmLabel, cancelLabel, danger});
    }
    if(f.type==='number' && String(v).trim()!=='') v=Number(v);
    out[f.name]=v;
  }
  return out;
}
