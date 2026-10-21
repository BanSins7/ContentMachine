'use strict';
/* Block 6: real step-by-step Taxonomy Builder. Never auto-dispatches LLM calls. */
(() => {
  const $=id=>document.getElementById(id);
  let pid=null,rid=null,current=null,working=false,serial=0,runHistory=[];
  // Only one review card is visible. The backing rows retain all edits until
  // explicit user submission; no draft action ever calls the paid provider.
  let deckKey='',deckPhase='facts',deckIndex=0,criticVotes={};
  const kinds={
    function:'Функция приложения',problem:'Потребность аудитории',
    audience:'Целевая аудитория',product_type:'Тип продукта'
  };
  const sources={
    product_functions:'Функции продукта',audience_problems:'Проблемы аудитории',
    product_description:'Описание продукта',product_type:'Тип продукта',
    niche:'Ниша',goal:'Цель проекта'
  };
  const relations={
    direct:'О продукте',problem_related:'Проблема аудитории',
    adjacent:'Смежная тема'
  };
  const severityLabels={high:'Важное замечание',medium:'Стоит проверить',low:'Рекомендация'};
  const roleLabels={fact_extraction:'Извлечение фактов',
    taxonomy_generation:'Поиск направлений',taxonomy_critic:'Проверка качества'};

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

  const dkey=()=>rid?'tb.review.draft.'+rid+'.'+(current?.status||''):'';
  function persistDraft(){
    if(!current||!rid)return;
    const snapshot={
      index:deckIndex,phase:deckPhase,criticVotes,
      facts:Array.from($('tbRunFactRows').children).map(row=>({
        id:row.dataset.id,decision:row.dataset.decision||'',
        text:row.querySelector('.tb-run-edit')?.value||''
      })),
      topics:Array.from($('tbRunTopicRows').children).map(row=>({
        id:row.dataset.id,decision:row.dataset.decision||'',
        ...Object.fromEntries(['name','definition','include','exclude'].map(k=>
          [k,row.querySelector('.tb-run-topic-'+k)?.value||'']))
      }))
    };
    try{localStorage.setItem(dkey(),JSON.stringify(snapshot));}catch(_){}
  }
  function restoreDraft(){
    let snapshot;
    try{snapshot=JSON.parse(localStorage.getItem(dkey())||'null');}catch(_){return;}
    if(!snapshot||typeof snapshot!=='object')return;
    function apply(list,rows,selectors){
      if(!Array.isArray(list))return;
      const lookup=new Map(list.map(d=>[d.id,d]));
      for(const row of rows.children){
        const data=lookup.get(row.dataset.id);
        if(!data)continue;
        const chosen=['approve','reject'].includes(data.decision)?data.decision:'';
        row.dataset.decision=chosen;
        row.querySelector('.tb-run-keep').checked=chosen==='approve';
        for(const [key,selector] of Object.entries(selectors)){
          if(typeof data[key]==='string')row.querySelector(selector).value=data[key];
        }
      }
    }
    apply(snapshot.facts,$('tbRunFactRows'),{text:'.tb-run-edit'});
    apply(snapshot.topics,$('tbRunTopicRows'),{
      name:'.tb-run-topic-name',definition:'.tb-run-topic-definition',
      include:'.tb-run-topic-include',exclude:'.tb-run-topic-exclude'
    });
    if(snapshot.criticVotes&&typeof snapshot.criticVotes==='object')
      criticVotes=Object.fromEntries(Object.entries(snapshot.criticVotes)
        .filter(([k,v])=>/^\d+$/.test(k)&&['seen','skip'].includes(v)));
    deckPhase=['facts','critic','topics'].includes(snapshot.phase)?snapshot.phase:deckPhase;
    deckIndex=Number.isInteger(snapshot.index)&&snapshot.index>=0?snapshot.index:0;
  }
  function rowsForPhase(){
    return deckPhase==='facts'?Array.from($('tbRunFactRows').children):
      deckPhase==='topics'?Array.from($('tbRunTopicRows').children):
      current?.critic_findings||[];
  }
  function editableDeck(){
    return current?.status==='fact_review'&&deckPhase==='facts' ||
      current?.status==='topic_review'&&(deckPhase==='topics'||deckPhase==='critic');
  }
  function addText(parent,tag,value,cls){
    const node=element(tag,value,cls);parent.append(node);return node;
  }
  function addLabeled(parent,label,value){
    const wrapper=element('div','','tb-review-field');
    addText(wrapper,'span',label,'tb-review-label');
    addText(wrapper,'p',value||'Не указано','tb-review-paragraph');
    parent.append(wrapper);
  }
  function addExamples(parent,label,examples){
    if(!Array.isArray(examples)||!examples.length)return;
    const wrapper=element('div','','tb-review-field');
    addText(wrapper,'span',label,'tb-review-label');
    const list=element('ul','','tb-review-examples');
    for(const example of examples)addText(list,'li',example);
    wrapper.append(list);parent.append(wrapper);
  }
  function editableField(parent,label,selector,row,lines=2){
    const wrapper=element('label','','tb-review-field tb-review-input');
    addText(wrapper,'span',label,'tb-review-label');
    const original=row.querySelector(selector);
    const editor=document.createElement(lines===1?'input':'textarea');
    if(lines===1)editor.type='text';else editor.rows=lines;
    editor.value=original.value;
    editor.maxLength=original.maxLength||750;
    editor.disabled=!editableDeck();
    editor.addEventListener('input',()=>{original.value=editor.value;persistDraft();});
    wrapper.append(editor);parent.append(wrapper);
  }
  function reviewTopicCard(row){
    const t=(current.topic_proposals||[]).find(x=>x.id===row.dataset.id);
    if(!t)return;
    const card=$('tbReviewCard');
    addText(card,'span',(relations[t.relation]||'Направление')+' · '+(t.origin==='explicit'?'подтверждено анкетой':'предложено моделью'),'tb-review-kicker');
    addText(card,'h3',row.querySelector('.tb-run-topic-name').value,'tb-review-title');
    addLabeled(card,'О чём искать ролики',row.querySelector('.tb-run-topic-definition').value);
    const rules=element('div','','tb-review-columns');
    addLabeled(rules,'Подходят',row.querySelector('.tb-run-topic-include').value);
    addLabeled(rules,'Не подходят',row.querySelector('.tb-run-topic-exclude').value);
    card.append(rules);
    addExamples(card,'Примеры подходящих видео',t.positive_examples);
    addExamples(card,'Примеры неподходящих видео',t.negative_examples);
    const about=(current.approved_facts||[]).filter(f=>(t.fact_ids||[]).includes(f.id));
    if(about.length){
      const details=document.createElement('details');
      details.className='tb-review-evidence';
      const summary=document.createElement('summary');
      summary.textContent='На чём основана тема · '+about.length;
      details.append(summary);
      for(const fact of about)addText(details,'p',fact.text,'tb-review-evidence-text');
      card.append(details);
    }else if(t.independent)addText(card,'p','Смежная тема не обязана ссылаться на функцию приложения.','tb-review-footnote');
    const issues=(current.critic_findings||[]).filter(x=>x.topic_name===t.name);
    if(issues.length){
      const details=document.createElement('details');
      details.className='tb-review-evidence';
      const summary=document.createElement('summary');
      summary.textContent='Замечания независимой проверки · '+issues.length;
      details.append(summary);
      for(const issue of issues){
        addText(details,'p',(severityLabels[issue.severity]||'Замечание')+': '+issue.issue,'tb-review-evidence-text');
        addText(details,'p','Рекомендация: '+issue.suggestion,'tb-review-evidence-text');
      }
      card.append(details);
    }
    const edit=document.createElement('details');
    edit.className='tb-review-edit';
    const summary=document.createElement('summary');
    summary.textContent='Изменить название и правила';
    edit.append(summary);
    editableField(edit,'Название','.tb-run-topic-name',row,1);
    editableField(edit,'Описание темы','.tb-run-topic-definition',row);
    editableField(edit,'Включать видео','.tb-run-topic-include',row);
    editableField(edit,'Исключать видео','.tb-run-topic-exclude',row);
    card.append(edit);
  }
  function reviewFactCard(row){
    const f=(current.fact_proposals||[]).find(x=>x.id===row.dataset.id);
    if(!f)return;
    const card=$('tbReviewCard');
    addText(card,'span',kinds[f.kind]||'Факт о продукте','tb-review-kicker');
    addText(card,'h3',row.querySelector('.tb-run-edit').value,'tb-review-title');
    addText(card,'p','Модель извлекла это утверждение из анкеты. Одобри его, если оно верно, или отклони.','tb-review-support');
    const details=document.createElement('details');
    details.className='tb-review-evidence';
    const summary=document.createElement('summary');summary.textContent='Показать исходную цитату';
    details.append(summary);
    addText(details,'span','Поле анкеты: '+(sources[f.source_field]||'Описание проекта'),'tb-review-label');
    addText(details,'blockquote',f.source_quote||'Цитата отсутствует','tb-review-quote');
    card.append(details);
    const edit=document.createElement('details');
    edit.className='tb-review-edit';
    const summary2=document.createElement('summary');summary2.textContent='Изменить формулировку';
    edit.append(summary2);
    editableField(edit,'Точный смысл факта','.tb-run-edit',row,3);
    card.append(edit);
  }
  function reviewCriticCard(item,index){
    const card=$('tbReviewCard');
    addText(card,'span',severityLabels[item.severity]||'Замечание','tb-review-kicker');
    addText(card,'h3',item.topic_name,'tb-review-title');
    addLabeled(card,'Что заметила модель',item.issue);
    addLabeled(card,'Что предлагается улучшить',item.suggestion);
    addText(card,'p','Рекомендация не меняет тему автоматически. Когда закончишь просмотр, ты сможешь самостоятельно поправить любую категорию.','tb-review-footnote');
  }
  function showReviewDeck(){
    const st=current?.status;
    const facts=(current?.fact_proposals||[]).length;
    const topics=(current?.topic_proposals||[]).length;
    const show=st==='fact_review'||st==='topic_review'||
      (st!=='approved'&&endStates.has(st)&&!!(facts||topics));
    $('tbRunReviewDeck').hidden=!show;
    if(!show)return;
    if(deckPhase==='facts'&&!facts)deckPhase='topics';
    if(deckPhase==='topics'&&!topics)deckPhase='facts';
    if(deckPhase==='critic'&&!(current.critic_findings||[]).length)deckPhase='topics';
    const rows=rowsForPhase();
    if(!rows.length)return;
    deckIndex=Math.min(Math.max(0,deckIndex),rows.length-1);
    const active=rows[deckIndex];
    const editable=editableDeck();
    const reviewComplete=deckPhase==='critic'
      ?rows.every((_,i)=>!!criticVotes[i])
      :rows.every(row=>!!row.dataset.decision);
    const accepted=deckPhase==='critic'?0:rows.filter(row=>row.dataset.decision==='approve').length;
    const reviewed=deckPhase==='critic'?rows.filter((_,i)=>!!criticVotes[i]).length:
      rows.filter(row=>!!row.dataset.decision).length;
    $('tbReviewPhase').textContent=deckPhase==='facts'?'Проверка фактов':
      deckPhase==='topics'?'Выбор тем':'Независимая проверка тем';
    $('tbReviewCount').textContent=(deckIndex+1)+' из '+rows.length+
      ' · проверено '+reviewed;
    const track=$('tbReviewProgressTrack');
    track.setAttribute('aria-valuenow',String(Math.round(100*reviewed/rows.length)));
    $('tbReviewProgressFill').setAttribute('style','width:'+(100*reviewed/rows.length)+'%');
    $('tbReviewCard').replaceChildren();
    if(deckPhase==='facts')reviewFactCard(active);
    else if(deckPhase==='topics')reviewTopicCard(active);
    else reviewCriticCard(active,deckIndex);
    const vote=deckPhase==='critic'?criticVotes[deckIndex]:active.dataset.decision;
    $('tbReviewApprove').textContent=deckPhase==='critic'?'Прочитано ✓':'Одобрить ✓';
    $('tbReviewReject').textContent=deckPhase==='critic'?'Не применять':'Отклонить';
    $('tbReviewApprove').hidden=!editable;
    $('tbReviewReject').hidden=!editable;
    $('tbReviewApprove').className='button primary'+((vote==='approve'||vote==='seen')?' tb-review-selected':'');
    $('tbReviewReject').className='tb-quiet-button tb-review-reject'+((vote==='reject'||vote==='skip')?' tb-review-selected':'');
    $('tbReviewPrev').disabled=deckIndex===0&&(deckPhase!=='topics'||!(current.critic_findings||[]).length);
    $('tbReviewNext').disabled=deckIndex===rows.length-1&&
      !(deckPhase==='critic'&&st==='topic_review');
    const commit=$('tbRunReviewCommit');
    commit.hidden=!editable||!reviewComplete||deckPhase==='critic';
    const hasManual=!!$(deckPhase==='facts'?'tbRunNewFact':'tbRunTopicAddName').value.trim();
    commit.disabled=working||(deckPhase==='topics'?
      accepted+(hasManual?1:0)<2:accepted+(hasManual?1:0)<1);
    commit.textContent=deckPhase==='facts'
      ?'Подтвердить '+accepted+' фактов →':
       'Опубликовать '+accepted+' тем →';
    $('tbReviewHint').textContent=!editable?
      'Это сохранённый результат. Изменения и повторные платные запросы для завершённого запуска недоступны.':
      deckPhase==='critic'?
       'Ознакомься с замечаниями. Советы не применяются автоматически.':
      reviewComplete?
       (deckPhase==='topics'&&accepted+(hasManual?1:0)<2?
         'Для публикации выбери хотя бы две темы или добавь собственную.':
         'Все карточки просмотрены. Можно вернуться и изменить решение.'):
       'Решения сохраняются как черновик в этом браузере. Публикация произойдёт только после отдельного подтверждения.';
    if(vote) $('tbReviewCard').dataset.reviewed='true';
    else $('tbReviewCard').dataset.reviewed='false';
  }
  function voteReview(kind){
    if(working||!editableDeck())return;
    const rows=rowsForPhase();
    if(!rows.length)return;
    if(deckPhase==='critic')criticVotes[deckIndex]=kind==='approve'?'seen':'skip';
    else{
      const row=rows[deckIndex];
      row.dataset.decision=kind;
      row.querySelector('.tb-run-keep').checked=kind==='approve';
    }
    if(deckIndex<rows.length-1)deckIndex++;
    else if(deckPhase==='critic'){deckPhase='topics';deckIndex=0;}
    persistDraft();showReviewDeck();
    $('tbReviewCard').scrollTop=0;
  }
  function goReview(direction){
    if(working||$('tbRunReviewDeck').hidden)return;
    const rows=rowsForPhase();
    if(direction===-1&&deckIndex===0&&deckPhase==='topics'&&
       current?.status==='topic_review'&&(current.critic_findings||[]).length){
      deckPhase='critic';deckIndex=current.critic_findings.length-1;
    }else if(direction===1&&deckIndex===rows.length-1&&deckPhase==='critic'){
      deckPhase='topics';deckIndex=0;
    }else deckIndex=Math.max(0,Math.min(rows.length-1,deckIndex+direction));
    persistDraft();showReviewDeck();
    $('tbReviewCard').scrollTop=0;
  }
  function setupReviewForState(){
    const key=rid+':'+current.status;
    if(deckKey!==key){
      deckKey=key;deckIndex=0;criticVotes={};
      deckPhase=current.status==='fact_review'?'facts':
        current.status==='topic_review'&&(current.critic_findings||[]).length?'critic':
        (current.topic_proposals||[]).length?'topics':'facts';
    }
    restoreDraft();showReviewDeck();
  }
  function togglePanel(id,btn){
    const panel=$(id),control=$(btn);
    panel.hidden=!panel.hidden;
    control.setAttribute('aria-expanded',String(!panel.hidden));
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
      const line=(roleLabels[item.role]||'Этап обработки')+' · '+(item.model_id||'модель неизвестна')+
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
    // Only the active review shows editing controls. Historic proposals use
    // the same focused deck in read-only mode, never extra disabled forms.
    facts.hidden=!factEditable;
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
    topics.hidden=!topicsEditable;
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
        element('p',t.name+' · '+(relations[t.relation]||'Направление'),'tb-run-stage-line'));
    }
    $('tbRunAdvance').hidden=!actionStates.has(st);
    $('tbRunAdvance').disabled=working;
    $('tbRunCancel').hidden=st==='approved'||st==='cancelled'||
      st==='extraction_running'||st==='generation_running'||st==='critic_running'||st==='uncertain';
    $('tbRunCancel').disabled=working;
    $('tbRunReviewFacts').disabled=working;
    $('tbRunPublish').disabled=working||st!=='topic_review';
    $('tbRunExport').disabled=working||!current;
    setupReviewForState();
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
        (roleLabels[s.role]||'Этап')+': '+s.model_id).join(' · ')));
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
      if(!['approve','reject'].includes(item.dataset.decision)){
        showMessage('Просмотри каждый факт: одобри или отклони его.');return;
      }
      decisions.push(keep?text===original?{id,action:'approve'}:
        {id,action:'edit',text}:{id,action:'reject'});
    }
    const newText=$('tbRunNewFact').value.trim();
    if(newText)decisions.push({action:'add',kind:$('tbRunNewFactType').value,text:newText});
    working=true;showMessage();
    try{
      await api(route('/facts/review'),{decisions});      try{localStorage.removeItem(dkey());}catch(_){}      await refresh();
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
      if(!['approve','reject'].includes(item.dataset.decision)){
        showMessage('Просмотри каждую тему: одобри или отклони её.');return;
      }
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
      await api(route('/topics/publish'),{decisions});      try{localStorage.removeItem(dkey());}catch(_){}      await refresh();
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
    if(working){
      showMessage('Платный запрос ещё выполняется. Дождись завершения.');
      return false;
    }
    // Each decision is locally saved as a draft. Leaving a review does not
    // approve any facts or categories on the server.
    if(current&&['fact_review','topic_review'].includes(current.status)){
      const hasDecisions=[...$('tbRunFactRows').children,...$('tbRunTopicRows').children]
        .some(row=>!!row.dataset.decision);
      if(hasDecisions)return confirm('Решения ещё не подтверждены на сервере. Черновик сохранён в браузере. Выйти?');
    }
    return true;
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
  $('tbRunOpenHistory').addEventListener('click',()=>togglePanel('tbRunHistoryPanel','tbRunOpenHistory'));
  $('tbRunOpenDetails').addEventListener('click',()=>togglePanel('tbRunDetailsPanel','tbRunOpenDetails'));
  $('tbRunCloseHistory').addEventListener('click',()=>togglePanel('tbRunHistoryPanel','tbRunOpenHistory'));
  $('tbRunCloseDetails').addEventListener('click',()=>togglePanel('tbRunDetailsPanel','tbRunOpenDetails'));
  $('tbReviewApprove').addEventListener('click',()=>voteReview('approve'));
  $('tbReviewReject').addEventListener('click',()=>voteReview('reject'));
  $('tbReviewPrev').addEventListener('click',()=>goReview(-1));
  $('tbReviewNext').addEventListener('click',()=>goReview(1));
  $('tbRunNewFact').addEventListener('input',showReviewDeck);
  $('tbRunTopicAddName').addEventListener('input',showReviewDeck);
  $('tbRunReviewCommit').addEventListener('click',()=>{
    if(!editableDeck()||working)return;
    if(deckPhase==='facts')facts();
    else if(deckPhase==='topics'&&confirm('Опубликовать одобренные темы в SRE? Это окончательное действие.'))publish();
  });
  if(typeof document.addEventListener==='function')document.addEventListener('keydown',event=>{
    if($('tbRunReviewDeck').hidden||!editableDeck()||working)return;
    const target=event.target;
    if(target&&['INPUT','TEXTAREA','SELECT'].includes(target.tagName))return;
    if(event.altKey||event.ctrlKey||event.metaKey)return;
    if(event.key==='ArrowRight')goReview(1);
    else if(event.key==='ArrowLeft')goReview(-1);
    else if(event.key.toLowerCase()==='a')voteReview('approve');
    else if(event.key.toLowerCase()==='r')voteReview('reject');
  });
  window.addEventListener('beforeunload',event=>{
    if(working){event.preventDefault();event.returnValue='';}
  });
})();