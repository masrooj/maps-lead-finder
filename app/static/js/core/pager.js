import { esc } from './api.js';


export const PAGE_SIZE_DEFAULT=25;

export const pagerState={};

export function pageSlice(items,key,size){
  const list=Array.isArray(items)?items:[];
  const st=pagerState[key]||(pagerState[key]={page:1,size:size||PAGE_SIZE_DEFAULT});
  if(size) st.size=size;
  const total=list.length;
  const pages=Math.max(1, Math.ceil(total/Math.max(1,st.size))||1);
  if(st.page>pages) st.page=pages;
  if(st.page<1) st.page=1;
  const start=(st.page-1)*st.size;
  return {items:list.slice(start,start+st.size), page:st.page, pages, total, start, size:st.size, key};
}

export function renderPager(host, slice, onChange){
  if(!host) return;
  if(!slice || slice.total<=slice.size){
    if(slice && slice.total){
      host.innerHTML=`<div class="pager"><span class="pager-meta">${slice.total} item${slice.total===1?'':'s'}</span></div>`;
    }else{
      host.innerHTML='';
    }
    return;
  }
  const from=slice.start+1;
  const to=Math.min(slice.start+slice.size, slice.total);
  host.innerHTML=`<div class="pager" data-pager="${esc(slice.key)}">
    <span class="pager-meta">${from}–${to} of ${slice.total}</span>
    <div class="pager-actions">
      <button type="button" data-pager-act="prev" ${slice.page<=1?'disabled':''}>Prev</button>
      <span class="pager-meta">Page ${slice.page}/${slice.pages}</span>
      <button type="button" data-pager-act="next" ${slice.page>=slice.pages?'disabled':''}>Next</button>
      <select data-pager-size aria-label="Rows per page">
        ${[10,25,50,100].map(n=>`<option value="${n}" ${n===slice.size?'selected':''}>${n}/page</option>`).join('')}
      </select>
    </div>
  </div>`;
  host.querySelectorAll('[data-pager-act]').forEach(btn=>btn.addEventListener('click',()=>{
    const st=pagerState[slice.key]||(pagerState[slice.key]={page:1,size:slice.size});
    if(btn.dataset.pagerAct==='prev') st.page=Math.max(1,st.page-1);
    if(btn.dataset.pagerAct==='next') st.page=Math.min(slice.pages,st.page+1);
    onChange&&onChange();
  }));
  const sel=host.querySelector('[data-pager-size]');
  if(sel) sel.addEventListener('change',()=>{
    const st=pagerState[slice.key]||(pagerState[slice.key]={page:1,size:slice.size});
    st.size=Number(sel.value)||PAGE_SIZE_DEFAULT;
    st.page=1;
    onChange&&onChange();
  });
}

export function ensurePagerHost(afterEl, id){
  // Use getElementById directly — never depend on `$` (module vs cached globals).
  let host=document.getElementById(id);
  if(host) return host;
  if(!afterEl||!afterEl.parentNode) return null;
  host=document.createElement('div');
  host.id=id;
  afterEl.insertAdjacentElement('afterend', host);
  return host;
}
