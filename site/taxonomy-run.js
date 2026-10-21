'use strict';
/* Block 6: real step-by-step Taxonomy Builder. Never auto-dispatches LLM calls. */
(() => {
  const $=id=>document.getElementById(id);
  let pid=null,rid=null,current=null,working=false,serial=0,runHistory=[];
  const money=x=>new Intl.NumberFormat('en-US',{style:'currency',
    currency:'USD',maximumFractionDigits:6}).format(Number(x||0));
  const statusText={
    extraction_pending:'Готов к извлечению фактов — нажми запуск этапа',
    extraction_running:'Модель извлекает факты…',
    fact_review:'Нужна твоя проверка фактов',
    generation_pending:'Факты утверждены. Можно запустить генерацию категорий',
    generation_running:'Генерируются категории…',
    critic_pending:'Можно запустить дополнительную проверку категорий',
    critic_running:'Critic анализирует категории…',
    topic_review:'Нужно проверить и утвердить категории',
    approved:'Research Taxonomy опубликована',
    cancelled:'Выполнение остановлено',
    failed:'Этап завершился ошибкой. Автоматического платного повтора нет.',
    uncertain:'Расход провайдера неизвестен. Попытку нельзя повторить без сверки.',
    paused_budget:'Бюджета недостаточно для безопасного следующего запроса.',
    paused_price:'Изменилась модель или цена. Запуск остановлен.'
  };
  const actionStates=new Set(['extraction_pending','generation_pending','critic_pending']);
  const endStates=new Set(['approved','cancelled','failed','uncertain','paused_budget','paused_price']);
  function showMessage(err='',info=''){
    $('tbRunError').hidden=!err;$('tbRunError').textContent=err;
    $('tbRunInfo').hidden=!info;$('tbRunInfo').textContent=info;
  }
  async function api(path,body){
    const reply=await fetch(path,body===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(body)});
    const result=await reply.json();
    if(!reply.ok||!result.ok)throw Error(result.error||'Ошибка сервера');
    return result;
  }
  const base=()=>'/api/projects/'+encodeURIComponent(pid)+'/taxonomy-builder/runs';
  const route=path=>base()+'/'+encodeURIComponent(rid)+(path||'');
  function element(tag,text,cls){
    const result=document.createElement(tag);result.textContent=text||'';
    if(cls)result.className=cls;return result;
  }
  function factRow(f){
    const outer=element('div','','tb-run-review');
    outer.dataset.id=f.id;outer.dataset.original=f.text;
    const header=element('label','','tb-run-approval-check');
    const box=document.createElement('input');box.type='checkbox';box.checked=false;box.className='tb-run-keep';
    header.append(box,element('strong',f.kind+' · '+f.source_field));
    outer.append(header);
    const textarea=document.createElement('textarea');
    textarea.className='tb-run-edit';textarea.value=f.text;textarea.rows=2;textarea.maxLength=500;
    textarea.setAttribute('aria-label','Редактировать факт');outer.append(textarea);
    outer.append(element('p','Точная исходная цитата: «'+f.source_quote+'»','tb-run-evidence'));
    return outer;
  }
  function topicRow(t){
    const outer=element('div','','tb-run-review');
    outer.dataset.id=t.id;outer.dataset.name=t.name;
    outer.dataset.definition=t.definition;outer.dataset.include=t.include;
    outer.dataset.exclude=t.exclude;
    const header=element('label','','tb-run-approval-check');
    const box=document.createElement('input');box.type='checkbox';box.checked=false;box.className='tb-run-keep';
    header.append(box,element('strong',t.relation+' · '+t.origin));
    outer.append(header);
    const input=document.createElement('input');input.type='text';input.className='tb-run-topic-name';
    input.maxLength=160;input.value=t.name;input.setAttribute('aria-label','Название категории');
    outer.append(input);
    for(const [key,label] of [
      ['definition','Определение направления'],
      ['include','Включать ролики'],
      ['exclude','Исключать ролики']
    ]) {
      const caption=element('label',label,'tb-run-field-label');
      const area=document.createElement('textarea');
      area.className='tb-run-topic-'+key;
      area.value=t[key];area.rows=2;area.maxLength=750;
      area.setAttribute('aria-label',label);
      outer.append(caption,area);
    }
    outer.append(element('p','Подходящие иллюстративные примеры: '+(Array.isArray(t.positive_examples)?t.positive_examples:[]).join('; '),'tb-run-evidence'));
    outer.append(element('p','Неподходящие иллюстративные примеры: '+(Array.isArray(t.negative_examples)?t.negative_examples:[]).join('; '),'tb-run-evidence'));
    if(t.normalization_notes?.length)
      outer.append(element('p','Проверь формат полей: '+t.normalization_notes.join('; '),'tb-run-evidence'));
    return outer;
  }
  function render(){
    if(!current)return;
    const st=current.status;
    $('tbRunStatus').textContent=statusText[st]||st;
    $('tbRunBudget').hidden=false;
    $('tbRunSpent').textContent=money(current.spent_usd);
    $('tbRunCap').textContent=money(current.authorized_usd);
    $('tbRunReserved').textContent=money(current.reserved_usd);
    $('tbRunStages').hidden=false;
    const stages=$('tbRunStages');stages.replaceChildren(element('h3','Попытки LLM'));
    if(!current.stages.length)stages.append(element('p','Платных вызовов ещё не было.'));
    for(const item of current.stages){
      const line=item.role+' · '+(item.model_id||'модель неизвестна')+
        ' · '+(item.prompt_version||'')+' · '+item.status+
        ' · '+(item.charged_usd===null?'Списание неизвестно · резерв '+money(item.reservation_usd):
          ' списано '+money(item.charged_usd));
      const usage=item.usage||{};
      const tokens=usage.prompt_tokens!==undefined
        ?' · вход '+usage.prompt_tokens+' / выход '+usage.completion_tokens+
          (usage.reasoning_tokens===undefined||usage.reasoning_tokens===null?'':' / reasoning '+usage.reasoning_tokens)
        :'';
      stages.append(element('p',line+tokens+
        (item.error?' · ошибка: '+item.error:''),'tb-run-stage-line'));
    }
    const factEditable=st==='fact_review';
    const facts=$('tbRunFacts');
    facts.hidden=!factEditable &&
      (['extraction_pending','extraction_running'].includes(st) ||
       !(current.fact_proposals||[]).length);
    if(!facts.hidden) {
      const excluded=current.excluded_fact_proposals||[];
      const warning=$('tbRunExcludedFacts');
      warning.hidden=excluded.length===0;
      warning.textContent=excluded.length
        ? 'Неподтверждённые предложения модели: '+excluded.length+
          '. Они не доступны для автоматического утверждения. Проверь анкету и добавь подтверждённый факт вручную при необходимости.\n'+
          excluded.map(x=>x.kind+' · '+x.text+' — '+x.reason).join('\n')
        : '';
      const rows=$('tbRunFactRows');rows.replaceChildren();
      for(const f of current.fact_proposals||[]){
        const entry=factRow(f);
        if(!factEditable) {
          entry.querySelector('.tb-run-keep').disabled=true;
          entry.querySelector('.tb-run-edit').readOnly=true;
        }
        rows.append(entry);
      }
      $('tbRunReviewFacts').hidden=!factEditable;
      $('tbRunNewFact').disabled=!factEditable;
      $('tbRunNewFactType').disabled=!factEditable;
    }
    const topicsEditable=st==='topic_review';
    const topics=$('tbRunTopics');
    // A failed Critic must not hide already PAID and source-verified Generation.
    topics.hidden=!topicsEditable &&
      (['extraction_pending','extraction_running','fact_review',
        'generation_pending','generation_running'].includes(st) ||
       !(current.topic_proposals||[]).length);
    if(!topics.hidden){
      const blocked=current.excluded_topic_proposals||[];
      const warnings=$('tbRunExcludedTopics');
      warnings.hidden=blocked.length===0;
      warnings.textContent=blocked.length
        ? 'Отклонённые предложения модели: '+blocked.length+
          '. Они не доступны для публикации. При необходимости добавь категорию вручную.\n'+
          blocked.map(x=>x.name+' — '+x.reason).join('\n') : '';
      const rows=$('tbRunTopicRows');rows.replaceChildren();
      for(const t of current.topic_proposals||[]){
        const entry=topicRow(t);
        if(!topicsEditable) {
          entry.querySelector('.tb-run-keep').disabled=true;
          entry.querySelector('.tb-run-topic-name').readOnly=true;
          for(const key of ['definition','include','exclude'])
            entry.querySelector('.tb-run-topic-'+key).readOnly=true;
        }
        rows.append(entry);
      }
      $('tbRunPublish').hidden=!topicsEditable;
      for(const id of ['tbRunTopicAddName','tbRunTopicAddDefinition',
                       'tbRunTopicAddInclude','tbRunTopicAddExclude',
                       'tbRunTopicAddPositive','tbRunTopicAddNegative']){
        $(id).disabled=!topicsEditable;
      }
      const findings=current.critic_findings||[];
      $('tbRunCritic').hidden=!findings.length;
      $('tbRunCritic').textContent=findings.length?findings.map(f=>
        f.severity+': '+f.topic_name+' — '+f.issue+' → '+f.suggestion).join('\n'):'';
    }
    const published=$('tbRunPublished');published.hidden=st!=='approved';
    if(!published.hidden){
      const version=current.publication?.version;
      $('tbRunPublishedText').textContent='Утверждённая Research Taxonomy v'+version+
        '. Данные сохранены в PostgreSQL и не изменяются после публикации.';
      const list=$('tbRunPublishedTopics');list.replaceChildren();
      for(const t of current.publication?.topics||[])list.append(
        element('p',t.name+' · '+t.relation,'tb-run-stage-line'));
    }
    $('tbRunAdvance').hidden=!actionStates.has(st);
    $('tbRunAdvance').disabled=working;
    $('tbRunCancel').hidden=st==='approved'||st==='cancelled'||
      st==='extraction_running'||st==='generation_running'||st==='critic_running'||st==='uncertain';
    $('tbRunCancel').disabled=working;
    $('tbRunReviewFacts').disabled=working;
    $('tbRunPublish').disabled=working||st!=='topic_review';
    $('tbRunExport').disabled=working||!current;
    $('tbRunNextHint').textContent=actionStates.has(st)
      ? 'Нажатие выполнит один платный LLM-запрос; перед ним будет проверен резерв бюджета.'
      : st==='fact_review'||st==='topic_review'
        ? 'ИИ ожидает твоего решения; новые платные вызовы не выполняются.'
        : 'Автоматические платные повторы отключены.';
  }
  function renderHistory() {
    const list=$('tbRunHistoryList');
    list.replaceChildren();
    if(!runHistory.length){
      list.append(element('p','Запусков пока нет. Создай запуск после утверждения модели и бюджета.'));
      return;
    }
    for(const row of runHistory){
      const btn=element('button','','tb-history-entry');
      btn.type='button';btn.className+=' tb-run-history-entry';
      if(row.run_id===rid)btn.setAttribute('aria-current','true');
      btn.append(element('strong',new Date(row.created_at).toLocaleString('ru-RU')+
        ' · '+(statusText[row.status]||row.status)));
      btn.append(element('small',(row.stages||[]).map(s=>
        s.role+': '+s.model_id+' · '+(s.max_output_tokens||'?')+' токенов').join(' | ')));
      const metrics=[];
      if(row.verified_fact_count!==null && row.verified_fact_count!==undefined)
        metrics.push('Фактов '+row.verified_fact_count);
      if(row.valid_topic_count!==null && row.valid_topic_count!==undefined)
        metrics.push('Категорий '+row.valid_topic_count);
      if(row.critic_finding_count!==null && row.critic_finding_count!==undefined)
        metrics.push('Замечаний Critic '+row.critic_finding_count);
      if(row.published)metrics.push('Опубликовано в SRE');
      if(metrics.length)btn.append(element('small',metrics.join(' · ')));
      btn.append(element('small','Потрачено '+money(row.spent_usd)+
        ' · резерв '+money(row.reserved_usd)));
      btn.addEventListener('click',()=>{
        if(working||row.run_id===rid)return;
        if(!confirmLeave())return;
        open(pid,row.run_id);
      });
      list.append(btn);
    }
  }
  async function loadHistory(){
    if(!pid)return;
    try{
      const result=await api(base());
      runHistory=result.runs||[];
      renderHistory();
    }catch(ex){
      $('tbRunHistoryList').replaceChildren(
        element('p','Историю не удалось загрузить: '+ex.message));
    }
  }
  function exportCurrent() {
    if(!current||working)return;
    // Download a fully local JSON snapshot, no external upload or model calls.
    const blob=new Blob([JSON.stringify(current,null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob);
    try {
      const link=document.createElement('a');
      link.href=url;link.download='taxonomy-run-'+current.run_id+'.json';
      document.body.append(link);link.click();link.remove();
    }finally{URL.revokeObjectURL(url);}
  }
  async function refresh(){
    if(!pid||!rid)return;
    const e=serial;
    try{
      const result=await api(route());
      if(e!==serial)return;
      current=result;render();
    }catch(err){if(e===serial)showMessage(err.message);}
  }
  async function open(project,run){
    pid=project;rid=run;serial++;working=false;
    document.dispatchEvent(new CustomEvent('cm-taxonomy-run-show',{detail:{projectId:pid}}));
    showMessage();
    $('tbRunStatus').textContent='Получаем состояние запуска…';
    await refresh();
    await loadHistory();
  }
  async function start(project,quote,consent){
    if(!project||!quote||!consent||!consent.consent_id)return;
    if(working)return;
    pid=project;working=true;showMessage();
    const key='tb.run.idempotency.'+project+'.'+quote.quote_id;
    let id=localStorage.getItem(key);
    if(!id){id='ui-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
      localStorage.setItem(key,id);}
    try{
      const res=await api('/api/projects/'+encodeURIComponent(project)+'/taxonomy-builder/runs',
        {quote_id:quote.quote_id,consent_id:consent.consent_id,idempotency_key:id});
      localStorage.setItem('tb.last.run.'+project,res.run_id);
      await open(project,res.run_id);
    }catch(err){working=false;showMessage(err.message);}
  }
  async function lastRun(project){
    // Prefer server-side run history over browser-local storage.
    try {
      const response=await api('/api/projects/'+encodeURIComponent(project)+
        '/taxonomy-builder/runs');
      const latest=(response.runs||[])[0];
      if(latest){await open(project,latest.run_id);return;}
    }catch(_){ /* Historic installs can fall back to the old local pointer. */ }
    const id=localStorage.getItem('tb.last.run.'+project);
    if(id)await open(project,id);
  }
  async function advance(){
    if(working||!current||!actionStates.has(current.status))return;
    working=true;showMessage('', 'Отправляем один платный запрос. Не обновляй страницу до ответа.');
    $('tbRunAdvance').disabled=true;
    try{
      const result=await api(route('/advance'),{});
      await refresh();
      if(result.message)showMessage(result.status==='uncertain'?result.message:'',result.message);
    }catch(err){await refresh();showMessage(err.message);}
    finally{working=false;render();}
  }
  async function facts(){
    if(working||current?.status!=='fact_review')return;
    const decisions=[];
    for(const item of $('tbRunFactRows').children){
      const id=item.dataset.id,original=item.dataset.original;
      const keep=item.querySelector('.tb-run-keep').checked;
      const text=item.querySelector('.tb-run-edit').value.trim();
      decisions.push(keep?text===original?{id,action:'approve'}:
        {id,action:'edit',text}:{id,action:'reject'});
    }
    const newText=$('tbRunNewFact').value.trim();
    if(newText)decisions.push({action:'add',kind:$('tbRunNewFactType').value,text:newText});
    working=true;showMessage();
    try{
      await api(route('/facts/review'),{decisions});await refresh();
      showMessage('', 'Факты утверждены. Генерацию категорий можно запустить вручную.');
    }catch(err){showMessage(err.message);}finally{working=false;render();}
  }
  async function publish(){
    if(working||current?.status!=='topic_review')return;
    const decisions=[];
    for(const item of $('tbRunTopicRows').children){
      const id=item.dataset.id,original=item.dataset.name;
      const keep=item.querySelector('.tb-run-keep').checked;
      const fields={name:item.querySelector('.tb-run-topic-name').value.trim(),
        definition:item.querySelector('.tb-run-topic-definition').value.trim(),
        include:item.querySelector('.tb-run-topic-include').value.trim(),
        exclude:item.querySelector('.tb-run-topic-exclude').value.trim()};
      const changed=Object.entries(fields).some(([key,value])=>
        value!==(key==='name'?original:item.dataset[key]));
      decisions.push(keep?changed?{id,action:'edit',...fields}:
        {id,action:'approve'}:{id,action:'reject'});
    }
    const manualName=$('tbRunTopicAddName').value.trim();
    if(manualName){
      decisions.push({
        action:'add',name:manualName,
        definition:$('tbRunTopicAddDefinition').value.trim(),
        include:$('tbRunTopicAddInclude').value.trim(),
        exclude:$('tbRunTopicAddExclude').value.trim(),
        positive_examples:[$('tbRunTopicAddPositive').value.trim()],
        negative_examples:[$('tbRunTopicAddNegative').value.trim()]
      });
    }
    working=true;showMessage();
    try{
      await api(route('/topics/publish'),{decisions});await refresh();
      showMessage('', 'Research Taxonomy опубликована.');
    }catch(err){showMessage(err.message);}finally{working=false;render();}
  }
  async function cancel(){
    if(working||!current)return;
    if(!confirm('Остановить этот запуск? Уже оплаченные запросы не возвращаются.'))return;
    working=true;showMessage();
    try{await api(route('/cancel'),{});await refresh();}
    catch(err){showMessage(err.message);}finally{working=false;render();}
  }
  function back(){
    if(working){showMessage('Дождись завершения запроса перед переходом назад.');return;}
    document.dispatchEvent(new CustomEvent('cm-taxonomy-run-return',{detail:{projectId:pid}}));
  }
  function confirmLeave(){
    if(working)showMessage('Платный запрос ещё выполняется. Дождись завершения.');
    return !working;
  }
  window.ContentMachineTaxonomyRun={start,open,lastRun,confirmLeave};
  $('tbRunHistoryRefresh').addEventListener('click',loadHistory);
  $('tbRunExport').addEventListener('click',exportCurrent);
  $('tbRunNewTest').addEventListener('click',()=>{
    if(working||!pid)return;
    if(!confirm('Новый тест требует новой сметы и нового разрешения расходов. Продолжить?'))return;
    window.ContentMachineTaxonomyCost?.openNew(pid);
  });
  $('tbRunBack').addEventListener('click',back);
  $('tbRunAdvance').addEventListener('click',advance);
  $('tbRunReviewFacts').addEventListener('click',facts);
  $('tbRunPublish').addEventListener('click',publish);
  $('tbRunCancel').addEventListener('click',cancel);
  window.addEventListener('beforeunload',event=>{
    if(working){event.preventDefault();event.returnValue='';}
  });
})();