import { $ } from './dom.js';


export function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

export async function api(url,options={}){const opts={credentials:'same-origin',...options};if(opts.body&&typeof opts.body==='object'&&!(opts.body instanceof FormData)){opts.headers={'Content-Type':'application/json',...(opts.headers||{})};opts.body=JSON.stringify(opts.body)}const r=await fetch(url,opts);const text=await r.text();let data=null;try{data=text?JSON.parse(text):null}catch(e){data=null}if(!r.ok)throw Error(apiError(data&&data.detail,(typeof text==='string'&&text&&!data)?text:fallbackStatus(r.status)));return data}

export function toast(text,error=false,action){
  const wrap=$('toastWrap');
  const el=document.createElement('div');
  el.className='toast '+(error?'error':'ok');
  const msg=document.createElement('span');msg.className='toast-msg';msg.textContent=text;el.appendChild(msg);
  if(action&&action.label&&action.onClick){
    const btn=document.createElement('button');btn.type='button';btn.textContent=action.label;
    btn.addEventListener('click',async()=>{btn.disabled=true;try{await action.onClick();el.remove()}catch(e){toast(e.message,true)}});
    el.appendChild(btn);
  }
  wrap.appendChild(el);
  setTimeout(()=>{if(el.isConnected)el.remove()},action?8000:3200);
}

export function showMessage(text,error=false){$('message').textContent=text;$('message').className='message show'+(error?' error':'');toast(text,error)}

export function fmtMoney(amount, currency){
  const cur=(currency||'USD').toUpperCase();
  const n=Number(amount);
  if(Number.isFinite(n)){
    try{return new Intl.NumberFormat(undefined,{style:'currency',currency:cur,currencyDisplay:'narrowSymbol'}).format(n)}
    catch(e){return cur+' '+n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}
  }
  return amount==null||amount===''?'—':String(amount);
}

export function fmtDateTime(iso){
  if(!iso) return '—';
  const d=new Date(iso);
  if(Number.isNaN(d.getTime())) return String(iso).replace('T',' ').slice(0,16);
  try{return new Intl.DateTimeFormat(undefined,{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(d)}
  catch(e){return String(iso).replace('T',' ').slice(0,16)}
}

export function dateOnly(iso){if(!iso)return '';return String(iso).slice(0,10)}

export function plusDays(n){const d=new Date();d.setDate(d.getDate()+n);return d.toISOString().slice(0,10)}
