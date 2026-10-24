'use strict';
/* Block 5: cost quote and spending consent; NO actual generation calls. */
(() => {
  const $=id=>document.getElementById(id);
  let projectId=null, quote=null, consent=null, ticket=0, busy=false;
  let activeMode='legacy_questionnaire_v1', contextBinding=null;
  const money=value=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',
    minimumFractionDigits:2,maximumFractionDigits:6}).format(Number(value||0));
  const roles={fact_extraction:'Проверка фактов',
    taxonomy_generation:'Поиск тем для контента',taxonomy_critic:'Проверка качества тем'};
  const base=()=>'/api/projects/'+encodeURIComponent(projectId)+'/taxonomy-builder/cost';
  const node=(tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text;
    if(cls)el.className=cls;return el;};
  async function api(url,payload) {
    const resp=await fetch(url,payload===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(payload)});
    let data;
    try {data=await resp.json();}
    catch(_){throw Error('Сервер вернул неправильный ответ.');}
    if(!resp.ok||data.ok!==true)throw Error(data.error||'HTTP '+resp.status);
    return data;
  }
  function message(error='',info=''){
    $('tbCostError').hidden=!error;$('tbCostError').textContent=error;
    $('tbCostNotice').hidden=!info;$('tbCostNotice').textContent=info;
  }
  function quoteMatches(q=quote){
    if(!q)return false;
    if(activeMode==='legacy_questionnaire_v1')
      return (q.details?.input_mode||'legacy_questionnaire_v1')==='legacy_questionnaire_v1';
    const bound=q.details?.context_binding;
    return q.details?.input_mode==='approved_project_context_v1' && !!bound &&
      !!contextBinding && bound.context_id===contextBinding.context_id &&
      bound.context_sha256===contextBinding.context_sha256 &&
      bound.selection_sha256===contextBinding.selection_sha256 &&
      bound.approval_id===contextBinding.approval_id &&
      bound.token_budget===contextBinding.token_budget;
  }
  function controls(){
    $('tbCostAuthorize').disabled=busy||!quote||!quoteMatches()||!!consent?.consent_id&&!consent.revoked||
      !Number.isFinite(Number($('tbCostBudget').value))||
      Number($('tbCostBudget').value)<Number(quote.high_usd)||
      Number($('tbCostBudget').value)>10000||
      !$('tbCostAckUncertainty').checked||
      (!$('tbCostDocsWrap').hidden&&!$('tbCostAckDocuments').checked);
    $('tbCostRequote').disabled=busy;
    $('tbCostRevoke').disabled=busy;
  }
  function render(){
    const present=!!quote&&quoteMatches();
    $('tbCostLoading').hidden=present;
    $('tbCostSummary').hidden=!present;
    $('tbCostBreakdownSection').hidden=!present;
    $('tbCostAssumptions').hidden=!present;
    $('tbCostConsent').hidden=!present||!!consent&&!consent.revoked;
    $('tbCostAuthorized').hidden=!present||!consent||consent.revoked;
    if(!present){
      $('tbCostMeta').textContent=activeMode==='approved_project_context_v1'?
        'Проверь Evidence и создай смету именно по выбранному Context.':
        'Не удалось получить действующую смету. Можно вернуться в Pipeline Studio.';
      return;
    }
    const details=quote.details;
    $('tbCostMeta').textContent=(activeMode==='approved_project_context_v1'?
      'Контекст '+quote.details.context_binding.context_id+' · ': 'Только анкета · ')+
      'Смета действует до '+
      new Date(quote.expires_at).toLocaleString('ru-RU');
    $('tbCostLow').textContent=money(quote.low_usd);
    $('tbCostExpected').textContent=money(quote.expected_usd);
    $('tbCostHigh').textContent=money(quote.high_usd);
    const list=$('tbCostBreakdown');list.replaceChildren();
    for(const s of details.stages){
      const entry=node('div','','tb-cost-row');
      const main=node('div','','tb-cost-row-main');
      main.append(node('strong',roles[s.role]||s.role));
      const reason=s.reasoning?.mode==='effort'?'Глубина анализа: '+
          ({low:'быстрая',minimal:'минимальная',medium:'обычная',high:'глубокая'}[s.reasoning.effort]||s.reasoning.effort):
        s.reasoning?.mode==='budget'?'Лимит рассуждения: '+s.reasoning.budget_tokens:
        'Стандартный режим';
      main.append(node('small',s.model_id+' · '+reason));
      const amounts=node('div','','tb-cost-row-amounts');
      amounts.append(node('span','Низкий '+money(s.scenarios.low.estimated_usd)),
        node('strong','Ожидаемый '+money(s.scenarios.expected.estimated_usd)),
        node('span','Повышенный '+money(s.scenarios.high.estimated_usd)));
      entry.append(main,amounts);list.append(entry);
    }
    const assumptions=details.assumptions;
    $('tbCostScope').textContent=activeMode==='approved_project_context_v1'?
      'Включены анкета и '+details.context_binding.selected_evidence_ids.length+
      ' закреплённых Evidence-фрагментов (не все документы и видео). Исключено: '+
      details.context_binding.excluded_evidence_count+
      '. Повышенный сценарий не является гарантированным потолком.' :
      'В расчёте учтена только текстовая анкета. '+
      (assumptions.excluded_documents_count?
       'Загруженные документы ('+assumptions.excluded_documents_count+' шт.) исключены.':
       'Дополнительных документов нет.')+
      ' Дальнейшая проверка человеком может изменить объём текста для генерации.';
    $('tbCostPriceSource').textContent='Тарифы: '+details.stages.map(s=>s.model_id+
      ' — '+(s.pricing_verified_at?new Date(s.pricing_verified_at).toLocaleString('ru-RU'):'дата неизвестна'))
      .join('; ')+' · источник: openrouter.ai/api/v1/models';
    $('tbCostDocsWrap').hidden=!assumptions.excluded_documents_count;
    if(consent&&!consent.revoked){
      $('tbCostAuthorizedText').textContent='Разрешено до '+money(consent.authorized_usd)+
        '. Согласие сохранено. Платные вызовы начнутся только после отдельного нажатия «Выполнить следующий LLM-этап».';
    } else if(consent?.revoked){
      $('tbCostConsent').hidden=true;
      message('', 'Согласие отозвано. Для нового разрешения создай новую смету.');
    }
    if(!$('tbCostBudget').value) {
      const high=Number(quote.high_usd);
      $('tbCostBudget').value=Math.min(10000,Math.max(0.01,high*1.5)).toFixed(6);
    }
    controls();
  }
  async function load(id,force=false){
    projectId=id;activeMode='legacy_questionnaire_v1';contextBinding=null;
    const current=++ticket;quote=null;consent=null;
    $('tbCostBudget').value='';$('tbCostAckUncertainty').checked=false;
    $('tbCostAckDocuments').checked=false;
    $('tbCostLoading').hidden=false;$('tbCostSummary').hidden=true;
    $('tbCostBreakdownSection').hidden=true;$('tbCostAssumptions').hidden=true;
    $('tbCostConsent').hidden=true;$('tbCostAuthorized').hidden=true;
    $('tbCostMeta').textContent='Проверяем действующую конфигурацию Pipeline…';
    message();busy=true;
    try{
      let state=await api(base());
      if(ticket!==current)return;
      // Stage13: reopening after browser restart must NEVER replace a Context
      // quote/consent with a new legacy quote without an explicit mode switch.
      if(!force && state.quote?.details?.input_mode==='approved_project_context_v1'){
        const bound=state.quote.details.context_binding;
        if(!bound||typeof bound.context_id!=='string')throw Error('Привязка ранее созданной сметы отсутствует.');
        busy=false;controls();
        if(!window.ContentMachineContextQuote?.open)
          throw Error('Экран Project Context недоступен. Смета не изменена.');
        await window.ContentMachineContextQuote.open(id,bound.context_id,bound.token_budget);
        return;
      }
      if(force||state.status==='quote_required'||state.status==='quote_stale'||
         (state.quote?.details?.input_mode||'legacy_questionnaire_v1')!=='legacy_questionnaire_v1'){
        const created=await api(base()+'/quote',{pipeline_id:state.pipeline_id,force_new:force});
        if(ticket!==current)return;
        message('',created.reused?'Используем действующую смету.':'Новая смета рассчитана без LLM-вызовов.');
        state=await api(base());
      }
      if(ticket!==current)return;
      quote=state.quote;consent=state.consent;
      if(state.stale_reason)throw Error(state.stale_reason);
      render();
    }catch(err){if(ticket===current){quote=null;consent=null;render();message(err.message);}}
    finally{if(ticket===current){busy=false;controls();}}
  }
  function open(id) {
    if(!id)return;
    document.dispatchEvent(new CustomEvent('cm-taxonomy-cost-show',{detail:{projectId:id}}));
    load(id);
  }
  function back(){
    document.dispatchEvent(new CustomEvent('cm-taxonomy-cost-return',{detail:{projectId}}));
  }
  async function authorize() {
    if(busy||!quote||$('tbCostAuthorize').disabled)return;
    const current=ticket;busy=true;controls();message();
    try{
      const result=await api(base()+'/consent',{
        quote_id:quote.quote_id,authorized_usd:$('tbCostBudget').value,
        acknowledge_documents_excluded:$('tbCostDocsWrap').hidden||
                                       $('tbCostAckDocuments').checked});
      if(current!==ticket)return;
      consent=result.consent;message('', 'Лимит расходов подтверждён. Ни один платный запрос не запущен.');
      render();
    }catch(err){if(current===ticket)message(err.message);}
    finally{if(current===ticket){busy=false;controls();}}
  }
  async function revoke(){
    if(busy||!consent||consent.revoked)return;
    if(!confirm('Отозвать разрешение на будущие расходы?'))return;
    const current=ticket;busy=true;controls();
    try{
      await api(base()+'/revoke',{consent_id:consent.consent_id});
      if(current!==ticket)return;
      consent.revoked=true;
      message('', 'Разрешение отозвано. Для нового согласия нужна свежая смета.');
      render();
    }catch(err){if(current===ticket)message(err.message);}
    finally{if(current===ticket){busy=false;controls();}}
  }
  function openLegacy(id){
    if(!id)return;
    document.dispatchEvent(new CustomEvent('cm-taxonomy-cost-show',{detail:{projectId:id}}));
    // Only an EXPLICIT switch or a new test may replace a contextual quote.
    load(id,true);
  }
  function showContext(id){
    if(!id)return;
    projectId=id;activeMode='approved_project_context_v1';contextBinding=null;
    ++ticket;quote=null;consent=null;
    $('tbCostBudget').value='';$('tbCostAckUncertainty').checked=false;
    $('tbCostAckDocuments').checked=false;
    document.dispatchEvent(new CustomEvent('cm-taxonomy-cost-show',{detail:{projectId:id}}));
    render();controls();
  }
  function restoreContextQuote(id,proposed,binding,authorized){
    if(!id||id!==projectId||activeMode!=='approved_project_context_v1')
      throw Error('Изменился проект или режим контекста.');
    contextBinding=binding;
    if(!quoteMatches(proposed)){
      contextBinding=null;
      throw Error('Историческая смета не совпадает с текущей выборкой.');
    }
    quote=proposed;
    consent=authorized&&authorized.quote_id===proposed.quote_id&&!authorized.revoked
      ?authorized:null;
    $('tbCostBudget').value='';
    $('tbCostAckUncertainty').checked=false;
    $('tbCostAckDocuments').checked=false;
    render();
  }
  function applyContextQuote(id,proposed,binding){
    if(id!==projectId || activeMode!=='approved_project_context_v1')
      throw Error('Изменился проект или режим сметы.');
    contextBinding=binding;
    if(!quoteMatches(proposed)){contextBinding=null;throw Error('Смета не соответствует выбранному Context.');}
    quote=proposed;consent=null;
    $('tbCostBudget').value='';$('tbCostAckUncertainty').checked=false;
    $('tbCostAckDocuments').checked=false;
    render();
  }
  function openNew(id){
    if(!id)return;
    document.dispatchEvent(new CustomEvent('cm-taxonomy-cost-show',{detail:{projectId:id}}));
    if(activeMode==='approved_project_context_v1'){
      showContext(id);
      window.ContentMachineContextQuote?.open(id);
      return;
    }
    load(id,true);
  }
  window.ContentMachineTaxonomyCost={open,openLegacy,openNew,showContext,
    applyContextQuote,restoreContextQuote,currentProjectId:()=>projectId};
  $('tbCostBack').addEventListener('click',back);
  $('tbCostBudget').addEventListener('input',controls);
  $('tbCostAckUncertainty').addEventListener('change',controls);
  $('tbCostAckDocuments').addEventListener('change',controls);
  $('tbRunStart').addEventListener('click',()=>{
    if(busy||!quote||!consent||consent.revoked)return;
    window.ContentMachineTaxonomyRun?.start(projectId,quote,consent);
  });
  $('tbRunResume').addEventListener('click',()=>{
    if(!projectId)return;
    window.ContentMachineTaxonomyRun?.lastRun(projectId);
  });
  $('tbCostDecline').addEventListener('click',back);
  $('tbCostAuthorize').addEventListener('click',authorize);
  $('tbCostRevoke').addEventListener('click',revoke);
  $('tbCostRequote').addEventListener('click',()=>{
    if(activeMode==='approved_project_context_v1'){
      showContext(projectId);window.ContentMachineContextQuote?.open(projectId);return;
    }
    load(projectId,true);
  });
})();