'use strict';
/* Block 5: cost quote and spending consent; NO actual generation calls. */
(() => {
  const $=id=>document.getElementById(id);
  let projectId=null, quote=null, consent=null, ticket=0, busy=false;
  const money=value=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',
    minimumFractionDigits:2,maximumFractionDigits:6}).format(Number(value||0));
  const roles={fact_extraction:'Fact Extraction',
    taxonomy_generation:'Taxonomy Generation',taxonomy_critic:'Taxonomy Critic'};
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
  function controls(){
    $('tbCostAuthorize').disabled=busy||!quote||!!consent?.consent_id&&!consent.revoked||
      !Number.isFinite(Number($('tbCostBudget').value))||
      Number($('tbCostBudget').value)<Number(quote.high_usd)||
      Number($('tbCostBudget').value)>10000||
      !$('tbCostAckUncertainty').checked||
      (!$('tbCostDocsWrap').hidden&&!$('tbCostAckDocuments').checked);
    $('tbCostRequote').disabled=busy;
    $('tbCostRevoke').disabled=busy;
  }
  function render(){
    const present=!!quote;
    $('tbCostLoading').hidden=present;
    $('tbCostSummary').hidden=!present;
    $('tbCostBreakdownSection').hidden=!present;
    $('tbCostAssumptions').hidden=!present;
    $('tbCostConsent').hidden=!present||!!consent&&!consent.revoked;
    $('tbCostAuthorized').hidden=!present||!consent||consent.revoked;
    if(!present){
      $('tbCostMeta').textContent='Не удалось получить действующую смету. Можно вернуться в Pipeline Studio.';
      return;
    }
    const details=quote.details;
    $('tbCostMeta').textContent='Pipeline v'+details.pipeline_version+
      ' · анкета v'+details.profile_revision+
      ' · смета действует до '+new Date(quote.expires_at).toLocaleString('ru-RU');
    $('tbCostLow').textContent=money(quote.low_usd);
    $('tbCostExpected').textContent=money(quote.expected_usd);
    $('tbCostHigh').textContent=money(quote.high_usd);
    const list=$('tbCostBreakdown');list.replaceChildren();
    for(const s of details.stages){
      const entry=node('div','','tb-cost-row');
      const main=node('div','','tb-cost-row-main');
      main.append(node('strong',roles[s.role]||s.role));
      const reason=s.reasoning?.mode==='effort'?'reasoning: '+s.reasoning.effort:
        s.reasoning?.mode==='budget'?'reasoning budget: '+s.reasoning.budget_tokens:
        'reasoning: по умолчанию';
      main.append(node('small',s.model_id+' · '+reason+
        ' · temperature '+(s.temperature===null?'провайдера':s.temperature)));
      const amounts=node('div','','tb-cost-row-amounts');
      amounts.append(node('span','Низкий '+money(s.scenarios.low.estimated_usd)),
        node('strong','Ожидаемый '+money(s.scenarios.expected.estimated_usd)),
        node('span','Повышенный '+money(s.scenarios.high.estimated_usd)));
      entry.append(main,amounts);list.append(entry);
    }
    const assumptions=details.assumptions;
    $('tbCostScope').textContent='В расчёте учтена только текстовая анкета. '+
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
    projectId=id;const current=++ticket;quote=null;consent=null;
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
      if(force||state.status==='quote_required'||state.status==='quote_stale'){
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
  function openNew(id){
    if(!id)return;
    document.dispatchEvent(new CustomEvent('cm-taxonomy-cost-show',{detail:{projectId:id}}));
    load(id,true);
  }
  window.ContentMachineTaxonomyCost={open,openNew};
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
  $('tbCostRequote').addEventListener('click',()=>load(projectId,true));
})();