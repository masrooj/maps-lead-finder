import { $ } from './dom.js';
import { api, toast, esc } from './api.js';
import { B } from '../app/bridge.js';

const NOTIF_DISMISS_KEY='ah_notif_dismissed_v1';
let _notifLastItems = [];

export function getNotifLastItems() {
  return _notifLastItems;
}

export function notifDismissStore(){
  try{ return JSON.parse(localStorage.getItem(NOTIF_DISMISS_KEY)||'{}')||{}; }
  catch(e){ return {}; }
}

export function saveNotifDismissStore(map){
  try{ localStorage.setItem(NOTIF_DISMISS_KEY, JSON.stringify(map||{})); }catch(e){}
}

export function isNotifDismissed(id, count){
  const row=notifDismissStore()[id];
  if(!row) return false;
  // Stay hidden until the live count rises above what was dismissed
  return Number(count||0) <= Number(row.count||0);
}

export function dismissNotification(id, count){
  if(!id) return;
  const map=notifDismissStore();
  map[id]={count:Number(count||0), at:new Date().toISOString()};
  saveNotifDismissStore(map);
}

export function clearAllNotifications(items){
  const map=notifDismissStore();
  (items||[]).forEach(i=>{ map[i.id]={count:Number(i.count||0), at:new Date().toISOString()}; });
  saveNotifDismissStore(map);
}

export function buildNotificationItems(dashboard){
  const d=dashboard||{};
  const q=d.queues||{};
  return [
    {id:'overdue', goto:'tasks', count:q.overdue_count||0, label:'Overdue tasks', sub:'Needs action now', urgent:true},
    {id:'due_today', goto:'tasks', count:q.due_today_count||0, label:'Due today', sub:'Follow up before EOD'},
    {id:'due_soon', goto:'tasks', count:q.due_soon_count||0, label:'Due in 7 days', sub:'Upcoming follow-ups'},
    {id:'to_contact', goto:'contactQueue', count:q.contact_queue_count||q.to_contact_count||0, label:'To contact', sub:'Open Contact queue'},
    {id:'no_next_action', goto:'prospects', count:q.no_next_action_count||0, label:'No next action', sub:'Prospects without a next step'},
    {id:'open_invoices', goto:'money:invoices', count:(d.invoiced_outstanding&&d.invoiced_outstanding.sample_size)||0, label:'Open invoices', sub:'Outstanding billed amounts'}
  ].filter(i=>Number(i.count)>0);
}

export async function refreshNotifications(){
  const list=$('notifList');
  const badge=$('notifBadge');
  if(!list) return;
  try{
    const d=await api('/api/sales/dashboard?product_line=STANDALONE');
    const all=buildNotificationItems(d);
    _notifLastItems=all;
    const items=all.filter(i=>!isNotifDismissed(i.id, i.count));
    const total=items.reduce((s,i)=>s+Number(i.count||0),0);
    if(badge){
      badge.textContent=total>99?'99+':String(total||0);
      badge.classList.toggle('show', total>0);
    }
    if($('notifClearAll')) $('notifClearAll').disabled=!all.length;
    if(!all.length){
      list.innerHTML='<div class="notif-empty">You\'re clear — no overdue tasks or open queues.</div>';
      return;
    }
    if(!items.length){
      list.innerHTML='<div class="notif-empty">All caught up. New items appear when counts rise.<br><button type="button" class="chip ok" id="notifRestoreBtn" style="margin-top:10px">Show dismissed</button></div>';
      const restore=$('notifRestoreBtn');
      if(restore) restore.addEventListener('click',e=>{
        e.preventDefault(); e.stopPropagation();
        saveNotifDismissStore({});
        refreshNotifications().catch(()=>{});
      });
      return;
    }
    list.innerHTML=items.map(i=>`
      <div class="notif-item" role="menuitem" data-notif-id="${esc(i.id)}" data-notif-count="${esc(String(i.count))}" data-goto="${esc(i.goto)}">
        <div class="n-count" style="${i.urgent?'color:var(--red)':''}">${esc(String(i.count))}</div>
        <div class="n-body">
          <div class="n-label">${esc(i.label)}</div>
          <div class="n-sub">${esc(i.sub)}</div>
        </div>
        <button type="button" class="n-dismiss" data-notif-dismiss="${esc(i.id)}" data-notif-count="${esc(String(i.count))}" title="Remove" aria-label="Remove notification">×</button>
      </div>`).join('');
    list.querySelectorAll('.notif-item[data-goto]').forEach(row=>row.addEventListener('click',e=>{
      if(e.target.closest('[data-notif-dismiss]')) return;
      const id=row.dataset.notifId;
      const count=Number(row.dataset.notifCount||0);
      dismissNotification(id, count); // read = clear from badge
      if (typeof B.closeTopMenus === 'function') B.closeTopMenus();
      if (typeof B.setAppPanel === 'function') B.setAppPanel(row.dataset.goto);
      refreshNotifications().catch(()=>{});
    }));
    list.querySelectorAll('[data-notif-dismiss]').forEach(btn=>btn.addEventListener('click',e=>{
      e.preventDefault(); e.stopPropagation();
      dismissNotification(btn.dataset.notifDismiss, Number(btn.dataset.notifCount||0));
      refreshNotifications().catch(()=>{});
      toast('Notification cleared');
    }));
  }catch(e){
    list.innerHTML=`<div class="notif-empty">${esc(e.message||'Could not load notifications')}</div>`;
    if(badge) badge.classList.remove('show');
  }
}
