'use strict';
/* Block 6: real step-by-step Taxonomy Builder. Never auto-dispatches LLM calls. */
(() => {
  const $=id=>document.getElementById(id);
  let pid=null,rid=null,current=null,working=false,serial=0;
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
    const header=element('label','','tb-run-approval-check');
    const box=document.createElement('input');box.type='checkbox';box.checked=false;box.className='tb-run-keep';
    header.append(box,element('strong',t.relation+' · '+t.origin));
    outer.append(header);
    const input=document.createElement('input');input.type='text';input.className='tb-run-topic-name';
    input.maxLength=160;input.value=t.name;input.setAttribute('aria-label','Название категории');
    outer.append(input);
    outer.append(element('p',t.definition,'tb-run-evidence'));
    outer.append(element('p','Включать: '+t.include,'tb-run-evidence'));
    outer.append(element('p','Исключать: '+t.exclude,'tb-run-evidence'));
    outer.append(element('p','Примеры иллюстративные, не реальные проверенные видео','tb-run-evidence'));
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
      stages.append(element('p',item.role+' · '+item.status+
        ' · учтено '+money(item.charged_usd||0)+
        (item.error?' · '+item.error:''),'tb-run-stage-line'));
    }
    const facts=$('tbRunFacts');facts.hidden=st!=='fact_review';
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
      for(const f of current.fact_proposals||[])rows.append(factRow(f));
    }
    const topics=$('tbRunTopics');topics.hidden=st!=='topic_review';
    if(!topics.hidden){
      const rows=$('tbRunTopicRows');rows.replaceChildren();
      for(const t of current.topic_proposals||[])rows.append(topicRow(t));
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
    $('tbRunPublish').disabled=working;
    $('tbRunNextHint').textContent=actionStates.has(st)
      ? 'Нажатие выполнит один платный LLM-запрос; перед ним будет проверен резерв бюджета.'
      : st==='fact_review'||st==='topic_review'
        ? 'ИИ ожидает твоего решения; новые платные вызовы не выполняются.'
        : 'Автоматические платные повторы отключены.';
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
      const name=item.querySelector('.tb-run-topic-name').value.trim();
      decisions.push(keep?name===original?{id,action:'approve'}:
        {id,action:'edit',name}:{id,action:'reject'});
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
  $('tbRunBack').addEventListener('click',back);
  $('tbRunAdvance').addEventListener('click',advance);
  $('tbRunReviewFacts').addEventListener('click',facts);
  $('tbRunPublish').addEventListener('click',publish);
  $('tbRunCancel').addEventListener('click',cancel);
  window.addEventListener('beforeunload',event=>{
    if(working){event.preventDefault();event.returnValue='';}
  });
})();