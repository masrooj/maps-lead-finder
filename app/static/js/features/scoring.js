import { $ } from '../core/dom.js';

import { esc } from '../core/api.js';



export function scoreOpts(b,key,fallback){
  const crit=(b&&b.score_criteria)||{};
  let cur=crit[key];
  if(cur==null||cur==='') cur=fallback;
  cur=Number(cur); if(![0,1,2].includes(cur)) cur=fallback;
  const labels={0:'0 · none',1:'1 · partial',2:'2 · strong'};
  return [0,1,2].map(n=>`<option value="${n}" ${n===cur?'selected':''}>${labels[n]}</option>`).join('');
}

export function readScoreCriteria(){
  const keys=['business_fit','operational_pain','buying_urgency','decision_maker_access','product_fit'];
  const out={};
  let total=0;
  keys.forEach(k=>{
    const el=document.querySelector(`[data-score-key="${k}"]`);
    const v=el?Number(el.value||0):0;
    out[k]=[0,1,2].includes(v)?v:0;
    total+=out[k];
  });
  return {criteria:out,total};
}

export function refreshScorePreview(){
  const el=$('scorePreview'); if(!el) return;
  const {total}=readScoreCriteria();
  const band=total>=8?'Priority':total>=5?'Investigate':'Deprioritize';
  el.textContent=`Preview ${total}/10 · ${band} · saves to Prospects Score column`;
}
