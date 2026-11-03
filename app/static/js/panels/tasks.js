import { $ } from '../core/dom.js';

import { esc, api, toast, plusDays, fmtDateTime } from '../core/api.js';

import { pageSlice, renderPager, ensurePagerHost, pagerState } from '../core/pager.js';

import { listSkeletonHtml } from '../core/skeleton.js';

import { uiConfirm, uiForm } from '../core/dialog.js';

import { registerPanel, getPanel } from './registry.js';

import { B } from '../app/bridge.js';

import { safeRefresh } from '../shell/router.js';

import { pickProspectField } from '../features/prospect-picker.js';

import { refreshNotifications } from '../core/notifications.js';

import { channelLabel } from '../features/compose.js';



let tasksCache = [];

function channelFromTask(t){
  if(t.channel) return String(t.channel).toLowerCase();
  const m=String(t.title||'').match(/after\s+(\w+)\s*$/i);
  return m?m[1].toLowerCase():'';
}

export function renderTasksList(){
  const tasks=tasksCache||[];
  const list=$('tasksList');
  const pagerHost=ensurePagerHost(list,'tasksPager');
  if(!tasks.length){
    list.innerHTML='<div class="empty-card">No open tasks. Create one or complete work from Pipeline.</div>';
    if(pagerHost) pagerHost.innerHTML='';
    return;
  }
  const slice=pageSlice(tasks,'tasks');
  list.innerHTML=slice.items.map(t=>{
    const biz=t.business_name||(t.business_id?('#'+t.business_id):'');
    const ch=channelFromTask(t);
    const msg=t.message||t.description||'';
    const dueLabel=t.due_at?fmtDateTime(t.due_at):(t.due_at||'—');
    return `
    <article class="task-row ${t.overdue?'overdue':''}">
      <div class="task-main">
        <div class="biz">${esc(t.title||'Task')}</div>
        <div class="sub">${t.overdue?'Overdue · ':''}Due ${esc(dueLabel)} · priority ${esc(t.priority||'normal')}${biz?' · '+esc(biz):''}${ch?' · '+esc(channelLabel(ch)):''}</div>
        ${msg?`<pre class="sent-message">${esc(msg)}</pre>`:''}
      </div>
      <div class="action-stack">
        ${t.business_id?`<button type="button" class="primary" data-followup-task="${t.id}" data-business="${t.business_id}" data-channel="${esc(ch)}" title="Open compose with this message">Follow up</button>`:''}
        ${t.business_id?`<button type="button" data-open-prospect="${t.business_id}">Open</button>`:''}
        <button type="button" data-reschedule="${t.id}" data-due="${esc(t.due_at||'')}" data-title="${esc(t.title)}">Reschedule</button>
        <button type="button" data-complete="${t.id}">Done</button>
        <button type="button" class="danger" data-del-task="${t.id}">Delete</button>
      </div>
    </article>`;
  }).join('');
  renderPager(pagerHost, slice, renderTasksList);
  list.querySelectorAll('[data-complete]').forEach(btn=>btn.addEventListener('click',async()=>{
    await api('/api/sales/tasks/'+btn.dataset.complete+'/complete',{method:'POST',body:{}});
    toast('Task completed'); await loadTasks(); refreshNotifications().catch(()=>{});
  }));
  list.querySelectorAll('[data-open-prospect]').forEach(btn=>btn.addEventListener('click',()=>B.openProspectById(btn.dataset.openProspect)));
  list.querySelectorAll('[data-followup-task]').forEach(btn=>btn.addEventListener('click',async()=>{
    const task=tasksCache.find(x=>String(x.id)===String(btn.dataset.followupTask));
    const bid=btn.dataset.business;
    if(!bid){ toast('No prospect on this task',true); return; }
    try{
      const lead=await api('/api/sales/prospects/'+bid);
      const channel=btn.dataset.channel||channelFromTask(task||{})||'';
      if(typeof B.openCompose==='function'){
        await B.openCompose(lead, channel||undefined);
        const msg=(task&&(task.message||task.description))||'';
        if(msg && $('composeBody')) $('composeBody').value=msg;
        toast('Follow-up ready — edit and send');
      }else if(typeof B.openProspectById==='function'){
        B.openProspectById(bid);
      }
    }catch(e){ toast(e.message||'Could not open follow-up',true); }
  }));
  list.querySelectorAll('[data-reschedule]').forEach(btn=>btn.addEventListener('click',async()=>{
    const form=await uiForm({
      title:'Reschedule task',
      confirmLabel:'Save',
      fields:[
        {name:'title', label:'Title', required:true, value:btn.dataset.title||''},
        {name:'due_at', label:'Due date', type:'date', value:(btn.dataset.due||'').slice(0,10)}
      ]
    });
    if(!form) return;
    await api('/api/sales/tasks/'+btn.dataset.reschedule+'/reschedule',{method:'POST',body:{title:form.title, due_at:form.due_at||null}});
    toast('Task updated'); await loadTasks();
  }));
  list.querySelectorAll('[data-del-task]').forEach(btn=>btn.addEventListener('click',async()=>{
    const ok=await uiConfirm({
      title:'Delete task?',
      message:'If this prospect was Contacted, they will move back to To contact.',
      confirmLabel:'Delete',
      danger:true
    });
    if(!ok) return;
    const res=await api('/api/sales/tasks/'+btn.dataset.delTask,{method:'DELETE'});
    toast(res&&res.reverted_to_contact?'Task deleted — prospect back on To contact':'Task deleted');
    await loadTasks();
    refreshNotifications().catch(()=>{});
    if(res&&res.reverted_to_contact){
      const cq=getPanel('contactQueue');
      if(cq&&typeof cq.refresh==='function') cq.refresh().catch(()=>{});
    }
  }));
}

export async function loadTasks(){
  const list=$('tasksList');
  const pagerHost=list&&ensurePagerHost(list,'tasksPager');
  if(list){
    list.innerHTML=listSkeletonHtml({rows:4, label:'Loading tasks…'});
    if(pagerHost) pagerHost.innerHTML='';
  }
  const data=await api('/api/sales/tasks');
  tasksCache=data.tasks||[];
  if(pagerState.tasks) pagerState.tasks.page=1;
  renderTasksList();
}

export async function createTaskFlow(){
  const bid=await pickProspectField('Task for which prospect?');
  if(bid==null) return;
  const form=await uiForm({
    title:'New follow-up task',
    confirmLabel:'Create task',
    fields:[
      {name:'title', label:'Title', required:true, placeholder:'Call back about demo'},
      {name:'due_at', label:'Due date', type:'date', required:true, value:plusDays(1)},
      {name:'priority', label:'Priority', type:'select', value:'normal', options:[
        {value:'low',label:'Low'},{value:'normal',label:'Normal'},{value:'high',label:'High'}
      ]},
      {name:'description', label:'Notes', type:'textarea', rows:2}
    ]
  });
  if(!form) return;
  await api('/api/sales/tasks',{method:'POST',body:{business_id:bid, title:form.title, due_at:form.due_at, priority:form.priority, description:form.description||''}});
  toast('Task created'); await loadTasks(); refreshNotifications().catch(()=>{});
}


registerPanel({ id: 'tasks', refresh: safeRefresh(loadTasks) });
