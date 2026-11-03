import { $ } from '../core/dom.js';

import { esc, api, toast, plusDays } from '../core/api.js';

import { pageSlice, renderPager, ensurePagerHost, pagerState } from '../core/pager.js';

import { listSkeletonHtml } from '../core/skeleton.js';

import { uiConfirm, uiForm } from '../core/dialog.js';

import { registerPanel, getPanel } from './registry.js';

import { B } from '../app/bridge.js';

import { safeRefresh } from '../shell/router.js';

import { pickProspectField } from '../features/prospect-picker.js';

import { refreshNotifications } from '../core/notifications.js';



let tasksCache = [];


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
  list.innerHTML=slice.items.map(t=>`
    <article class="task-row ${t.overdue?'overdue':''}">
      <div>
        <div class="biz">${esc(t.title)}</div>
        <div class="sub">${t.overdue?'Overdue · ':''}Due ${esc(t.due_at||'—')} · priority ${esc(t.priority||'normal')} · prospect #${t.business_id||'—'}</div>
      </div>
      <div class="action-stack">
        ${t.business_id?`<button type="button" data-open-prospect="${t.business_id}">Open</button>`:''}
        <button type="button" data-reschedule="${t.id}" data-due="${esc(t.due_at||'')}" data-title="${esc(t.title)}">Reschedule</button>
        <button type="button" class="primary" data-complete="${t.id}">Done</button>
        <button type="button" class="danger" data-del-task="${t.id}">Delete</button>
      </div>
    </article>`).join('');
  renderPager(pagerHost, slice, renderTasksList);
  list.querySelectorAll('[data-complete]').forEach(btn=>btn.addEventListener('click',async()=>{
    await api('/api/sales/tasks/'+btn.dataset.complete+'/complete',{method:'POST',body:{}});
    toast('Task completed'); await loadTasks(); refreshNotifications().catch(()=>{});
  }));
  list.querySelectorAll('[data-open-prospect]').forEach(btn=>btn.addEventListener('click',()=>B.openProspectById(btn.dataset.openProspect)));
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

