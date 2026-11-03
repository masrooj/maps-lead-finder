import { $ } from './dom.js';
import { authHeaders, refreshAccessToken, setAuthTokens, getAccessToken, getRefreshToken } from './auth.js';


export function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

const FIELD_LABELS={
  buyerEmail:'buyer email',
  contactEmail:'contact email',
  productRevisionId:'package revision',
  provider:'payment provider',
  crmLeadId:'CRM lead id',
  contactPhone:'phone',
  contactName:'contact name',
  buyerName:'buyer name',
  crmProspect:'prospect details',
  installationId:'installation id',
  toRevisionId:'target revision',
};

function fieldLabel(key){return FIELD_LABELS[key]||String(key||'').replace(/_/g,' ')}

function formatFieldErrors(details){
  if(!details||typeof details!=='object'||Array.isArray(details)) return '';
  return Object.entries(details).map(([key,vals])=>{
    const label=fieldLabel(key);
    if(Array.isArray(vals)&&vals.length) return `${label} (${vals.join(', ')})`;
    if(vals!=null&&vals!=='') return `${label} (${vals})`;
    return label;
  }).filter(Boolean).join(', ');
}

/** Map cryptic AppointHive / API strings to operator-facing copy. */
export function friendlyApiMessage(raw, detail, status){
  const text=String(raw||'').trim();
  const lower=text.toLowerCase();
  const body=(detail&&typeof detail==='object')?detail:{};
  const be=(body.be&&typeof body.be==='object')?body.be:{};
  const details=body.details||be.details||(body.error&&body.error.details)||null;
  const fields=formatFieldErrors(details);
  const keys=details&&typeof details==='object'&&!Array.isArray(details)?Object.keys(details):[];

  // Only map AppointHive/BE auth failures — never hijack Sales OS login/token 401s.
  const beHint=/bridge|crm not connected|crm bridge|booking engine/i.test(lower)
    || !!(body.be)
    || !!(body.code&&/be_|bridge/i.test(String(body.code)));
  if(beHint && (status===401||status===403||/unauthorized|forbidden/.test(lower))){
    return 'AppointHive bridge is not authorized. Open AppointHive → Connect and pair again.';
  }
  if(/crm bridge disabled|bridge disabled/.test(lower)){
    return 'AppointHive CRM bridge is disabled. Enable it in AppointHive Super Admin, then retry.';
  }
  if(/crm not connected|not connected/.test(lower)){
    return 'AppointHive is not connected. Open AppointHive → Connect, then retry.';
  }
  if(/unreachable|failed to fetch|networkerror|econnrefused/.test(lower)||status===502||status===503||status===504){
    return 'AppointHive is unreachable. Check that AppointHive is running and the base URL is correct.';
  }
  if(/no payment provider configured/.test(lower)){
    return 'No card payment provider is configured on AppointHive. Add Stripe or PayPal there, then retry.';
  }
  if(/validation failed|invalid input/.test(lower)){
    if(keys.includes('buyerEmail')||keys.includes('contactEmail')){
      return 'This prospect needs one valid buyer email before Send to delivery. Open the Contact tab, set a real email, then retry.';
    }
    if(keys.includes('productRevisionId')||keys.includes('toRevisionId')){
      return 'Choose a finalized AppointHive package revision, then retry Send to delivery.';
    }
    if(keys.includes('provider')){
      return 'Payment provider is invalid. Use Stripe or PayPal from AppointHive, or leave provider blank.';
    }
    if(fields) return `AppointHive rejected the handover — check ${fields}.`;
    return 'AppointHive rejected the handover (validation). Check buyer email and package revision, then retry.';
  }
  if(/immutable/.test(lower)){
    return 'That AppointHive package already has a purchase. Pick another finalized revision, or open the existing delivery.';
  }
  if(/purchase not found/.test(lower)){
    return 'AppointHive purchase was not found. Refresh Delivery / pay and try again.';
  }
  if(/internal server error/.test(lower)||(status>=500)){
    return 'AppointHive had an internal error. Try again in a moment; if it persists, check AppointHive logs.';
  }
  // Prefer server-provided friendly error (already mapped) when present.
  if(text&&!/^(validation failed|unauthorized|forbidden|error|request failed)$/i.test(text)){
    return text;
  }
  if(fields) return `AppointHive rejected the handover — check ${fields}.`;
  return text||'';
}

/** Normalize FastAPI / bridge error payloads into a single toast-friendly string. */
export function apiError(detail,fallback,status){
  if(typeof detail==='string'&&detail){
    return friendlyApiMessage(detail, null, status)||detail||fallback;
  }
  if(Array.isArray(detail)){
    const joined=detail.map(x=>{
      if(!x) return '';
      if(typeof x==='string') return x;
      return x.msg||x.message||x.error||(typeof x.detail==='string'?x.detail:'')||'';
    }).filter(Boolean).join('; ');
    return friendlyApiMessage(joined, {details:detail}, status)||joined||fallback;
  }
  if(detail&&typeof detail==='object'){
    const nested=detail.error||detail.msg||detail.message||detail.detail;
    if(typeof nested==='string'&&nested){
      return friendlyApiMessage(nested, detail, status)||nested||fallback;
    }
    if(nested&&typeof nested==='object'){
      return apiError(nested, fallback, status);
    }
    const fields=formatFieldErrors(detail.details)||formatFieldErrors(detail.be&&detail.be.details);
    if(fields) return friendlyApiMessage('Validation failed', detail, status)||`Check ${fields}.`;
    try{
      const s=JSON.stringify(detail);
      if(s&&s!=='{}') return friendlyApiMessage(s, detail, status)||fallback;
    }catch(e){}
  }
  return friendlyApiMessage(fallback, detail, status)||fallback;
}

export function fallbackStatus(code){
  if(code===401) return 'Sign in again to continue.';
  if(code===403) return 'You do not have permission for that action.';
  if(code===404) return 'That record was not found. Refresh and try again.';
  if(code===409) return 'AppointHive is not connected. Open AppointHive → Connect, then retry.';
  if(code===422) return 'Check the form fields and try again.';
  if(code===429) return 'Too many requests. Wait a moment and try again.';
  if(code>=500) return 'Server error. Try again in a moment.';
  return 'Request failed. Try again.';
}

function isAuthBypassUrl(url){
  const path=String(url||'').split('?')[0];
  return path==='/api/login' || path==='/api/auth/refresh' || path==='/api/logout' || path==='/api/auth/logout';
}

async function parseResponse(r){
  const text=await r.text();
  let data=null;
  try{data=text?JSON.parse(text):null}catch(e){data=null}
  return {text, data};
}

function throwApi(data, text, status){
  throw Error(apiError(
    data&&(data.detail!=null?data.detail:data),
    (typeof text==='string'&&text&&!data)?text:fallbackStatus(status),
    status
  ));
}

function buildFetchOpts(url, options={}){
  const opts={credentials:'same-origin',...options};
  delete opts._retriedAuth;
  const headers={...(options.headers||{})};
  if(opts.body&&typeof opts.body==='object'&&!(opts.body instanceof FormData)){
    headers['Content-Type']=headers['Content-Type']||'application/json';
    opts.body=JSON.stringify(opts.body);
  }
  opts.headers=isAuthBypassUrl(url)?headers:authHeaders(headers);
  return opts;
}

/**
 * Frontend → API client.
 * Attaches Bearer access token when present; backend validate → route → service → JSON.
 * On 401, tries one refresh rotation then retries (except login/refresh/logout).
 */
export async function api(url,options={}){
  let opts=buildFetchOpts(url, options);
  let r=await fetch(url,opts);
  let {text, data}=await parseResponse(r);

  if(r.status===401 && !isAuthBypassUrl(url) && !options._retriedAuth && (getAccessToken()||getRefreshToken())){
    const next=await refreshAccessToken();
    if(next){
      opts=buildFetchOpts(url, {...options, _retriedAuth:true});
      r=await fetch(url,opts);
      ({text, data}=await parseResponse(r));
    }
  }

  if(!r.ok) throwApi(data, text, r.status);

  if((url==='/api/login' || url==='/api/auth/refresh') && data && data.access_token){
    setAuthTokens(data);
  }
  return data;
}

export function toast(text,error=false,action){
  const wrap=$('toastWrap');
  if(!wrap){
    try{ console[error?'warn':'info'](text); }catch(_){}
    return;
  }
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

/** Shoptime-style stamp: weekday, day month year, 24h time — e.g. Tue, 3 November 2026, 15:05 */
export function fmtDateTime(iso){
  if(!iso) return '—';
  const d=new Date(iso);
  if(Number.isNaN(d.getTime())) return String(iso).replace('T',' ').slice(0,16);
  try{
    const parts=new Intl.DateTimeFormat(undefined,{
      weekday:'short', day:'numeric', month:'long', year:'numeric',
      hour:'2-digit', minute:'2-digit', hour12:false, hourCycle:'h23'
    }).formatToParts(d);
    const pick=t=>parts.find(p=>p.type===t)?.value||'';
    const hour=pick('hour');
    const minute=pick('minute');
    const time=hour&&minute?`${hour}:${minute}`:(hour||minute||'');
    return `${pick('weekday')}, ${pick('day')} ${pick('month')} ${pick('year')}, ${time}`;
  }catch(e){
    try{return new Intl.DateTimeFormat(undefined,{year:'numeric',month:'long',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(d)}
    catch(err){return String(iso).replace('T',' ').slice(0,16)}
  }
}

export function dateOnly(iso){if(!iso)return '';return String(iso).slice(0,10)}

export function plusDays(n){const d=new Date();d.setDate(d.getDate()+n);return d.toISOString().slice(0,10)}
