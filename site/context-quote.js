'use strict';
/* Stage13: explicit Context selection + read-only preflight before paid RTB. */
(() => {
  const $=id=>document.getElementById(id);
  const MODE='approved_project_context_v1', LEGACY='legacy_questionnaire_v1';
  let pid=null, contextId=null, budget=1200, preflight=null, sequence=0, busy=false;
  const element=(tag,text)=>{
    const el=document.createElement(tag);
    if(text!==undefined)el.textContent=text;
    return el;
  };
  async function api(url,data){
    const res=await fetch(url,data===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(data)});
    let value;
    try{value=await res.json();}catch(_){throw Error('Не удалось прочитать ответ сервера.');}
    if(!res.ok||value.ok!==true)throw Error(value.error||'HTTP '+res.status);
    return value;
  }
  const root=()=>'/api/projects/'+encodeURIComponent(pid);
  const status=(msg)=>{$('tbContextStatus').textContent=msg;};
  function clear(){
    ++sequence;preflight=null;
    $('tbContextAckSelection').checked=false;
    $('tbContextAckPartial').checked=false;
    $('tbContextEvidence').replaceChildren();
    $('tbContextPartialWrap').hidden=true;
    $('tbContextCreateQuote').disabled=true;
    status('Проверь конкретную выборку, затем подтверди её и создай смету.');
  }
  function controls(){
    $('tbContextOptions').hidden=$('tbCostInputMode').value!==MODE;
    const partial=preflight?.context_binding?.approval_decision==='approved_partial';
    $('tbContextPartialWrap').hidden=!partial;
    $('tbContextCreateQuote').disabled=busy||!preflight||
      !$('tbContextAckSelection').checked||
      (partial&&!$('tbContextAckPartial').checked);
    $('tbContextCheck').disabled=busy||!contextId;
    $('tbContextRefresh').disabled=busy;
    $('tbContextSelect').disabled=busy;
  }
  async function populate(preferred){
    const current=sequence;
    const data=await api(root()+'/context/snapshots?limit=100');
    if(current!==sequence)return;
    const approved=(data.snapshots||[]).filter(x=>
      x.decision==='approved'||x.decision==='approved_partial');
    const select=$('tbContextSelect');select.replaceChildren();
    if(!approved.length){
      const option=element('option','Нет утверждённых снимков');
      option.value='';select.append(option);contextId=null;
      status('Сначала создай и явно утверди Project Context на странице проекта.');
      controls();return;
    }
    for(const item of approved){
      const option=element('option','v'+item.context_version+' · '+item.decision+
        ' · ревизия '+item.profile_revision);
      option.value=item.context_id;select.append(option);
    }
    contextId=approved.some(x=>x.context_id===preferred)?preferred:approved[0].context_id;
    select.value=contextId;controls();
  }
  async function open(projectId,preferred=null,tokenBudget=1200){
    if(!projectId)return;
    pid=projectId;
    budget=Number(tokenBudget);
    if(!Number.isInteger(budget)||budget<100||budget>8000)budget=1200;
    $('tbContextTokenBudget').value=String(budget);
    $('tbCostInputMode').value=MODE;
    window.ContentMachineTaxonomyCost?.showContext(projectId);
    clear();controls();
    const generation=sequence;
    try{
      await populate(preferred||contextId);
      if(generation!==sequence)return;
      status(contextId?
        'Версия выбрана. Нажми «Проверить точную выборку». Это ничего не списывает.':
        'Для исследования нужен утверждённый контекст.');
    }catch(error){if(generation===sequence)status(error.message);}
  }
  async function check(){
    if(busy)return;
    clear();controls();
    if(!contextId){status('Нет выбранного контекста.');return;}
    const raw=$('tbContextTokenBudget').value;
    if(!/^[0-9]+$/.test(raw) || Number(raw)<100 || Number(raw)>8000){
      status('Введи целое число от 100 до 8000.');return;
    }
    budget=Number(raw);
    const thisPid=pid,id=contextId,serial=sequence;
    busy=true;controls();status('Проверяем закреплённые Evidence, цитаты и актуальность…');
    try{
      const data=await api(root()+'/taxonomy-builder/context-preview?context_id='+
        encodeURIComponent(id)+'&token_budget='+budget);
      if(serial!==sequence||pid!==thisPid||contextId!==id)return;
      const bound=data.context_binding;
      if(data.input_mode!==MODE||!bound||bound.context_id!==id||
         bound.token_budget!==budget||data.no_provider_call!==true||
         data.selected_count!==data.evidence.length){
        throw Error('Неправильный ответ предварительной проверки Context.');
      }
      preflight=data;
      status('Context SHA256: '+bound.context_sha256+
        ' · Evidence: '+data.selected_count+' · подтверждённых для фактов: '+
        data.eligible_fact_count+' · вне выборки: '+bound.excluded_evidence_count+
        ' · решение: '+bound.approval_decision);
      const destination=$('tbContextEvidence');
      for(const ev of data.evidence){
        const row=element('details');
        row.append(element('summary',(ev.citation||ev.evidence_id)+
          ' · '+ev.source_type+' · '+ev.trust_label));
        row.append(element('p','ID: '+ev.evidence_id+' · '+ev.evidence_kind+
          ' · '+ev.knowledge_scope+' · допустимые факты: '+
          (ev.allowed_fact_kinds.join(', ')||'только референс')));
        row.append(element('p',ev.text));
        destination.append(row);
      }
    }catch(error){
      if(serial===sequence){preflight=null;status(error.message);}
    }finally{busy=false;controls();}
  }
  const same=(a,b)=>!!a&&!!b&&
    a.context_id===b.context_id&&a.context_sha256===b.context_sha256&&
    a.approval_id===b.approval_id&&a.selection_sha256===b.selection_sha256&&
    a.token_budget===b.token_budget;
  async function quote(){
    if(busy||!preflight||$('tbContextCreateQuote').disabled)return;
    const current=sequence,expected=preflight.context_binding;
    busy=true;controls();status('Повторно проверяем Context перед неизменяемой сметой…');
    try{
      // Do not trust an earlier preview after a source update.
      const fresh=await api(root()+'/taxonomy-builder/context-preview?context_id='+
        encodeURIComponent(contextId)+'&token_budget='+budget);
      if(current!==sequence)return;
      if(!same(fresh.context_binding,expected)){
        clear();throw Error('Контекст или выборка изменились. Проверь и подтверди новую версию.');
      }
      const cost=await api(root()+'/taxonomy-builder/cost');
      if(current!==sequence)return;
      const created=await api(root()+'/taxonomy-builder/cost/quote',{
        pipeline_id:cost.pipeline_id,context_id:contextId,token_budget:budget,force_new:true});
      if(current!==sequence)return;
      if(created.quote?.details?.input_mode!==MODE||
         !same(created.quote.details.context_binding,expected))
        throw Error('Смета не совпадает с подтверждённым контекстом.');
      window.ContentMachineTaxonomyCost.applyContextQuote(pid,created.quote,expected);
      status('Смета Context сохранена. Далее отдельно утверди финансовый лимит. Платных вызовов нет.');
    }catch(error){if(current===sequence)status(error.message);}
    finally{busy=false;controls();}
  }
  $('tbCostInputMode').addEventListener('change',()=>{
    if($('tbCostInputMode').value===LEGACY){
      clear();pid&&window.ContentMachineTaxonomyCost?.open(pid);
    }else if($('tbCostInputMode').value===MODE)open(pid);
  });
  $('tbContextSelect').addEventListener('change',()=>{
    contextId=$('tbContextSelect').value||null;
    if(pid)window.ContentMachineTaxonomyCost?.showContext(pid);
    clear();controls();
  });
  $('tbContextTokenBudget').addEventListener('input',()=>{
    if(pid)window.ContentMachineTaxonomyCost?.showContext(pid);
    clear();controls();
  });
  $('tbContextRefresh').addEventListener('click',()=>{
    clear();populate(contextId).catch(error=>status(error.message));
  });
  $('tbContextCheck').addEventListener('click',check);
  $('tbContextAckSelection').addEventListener('change',controls);
  $('tbContextAckPartial').addEventListener('change',controls);
  $('tbContextCreateQuote').addEventListener('click',quote);
  window.ContentMachineContextQuote={open};
})();