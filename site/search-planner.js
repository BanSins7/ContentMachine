'use strict';
/* SP-14 six-stage project workflow; no HTML from backend and no credential storage.
 * This module uses ONLY SP-13 exact project-scoped, owner-gated endpoints. */
(() => {
  const core=window.CMSearchPlannerCore;
  const root=document.getElementById('spView');
  if (!core || !root) return;
  const el=id=>document.getElementById(id);
  const panels=['c9','generation','validation','planning','handoff','history'];
  const paneResult={c9:'spContextsResult',generation:'spGenerationResult',
    validation:'spValidationResult',planning:'spPlanningResult',handoff:'spInboxResult'};
  let project=null,client=null,busy=false,requestController=null;
  let currentStep='c9',draftQuote=null,lastOverview=null,modelCatalog=null;
  const lastOutputs=new Map();

  function note(text,isError=false) {
    const target=el('spNotice');
    target.textContent=String(text || '');
    target.classList.toggle('sp-error',isError);
  }
  function messageFor(error) {
    if (error && error.name==='AbortError') return 'Операция прервана при смене проекта.';
    return String(error?.message || 'Не удалось выполнить действие').slice(0,300);
  }
  function value(id) {return String(el(id).value || '').trim();}
  function id(idName) {return core.uuid(value(idName));}
  function ownerReady() {
    if (!client || !client.hasSecret()) throw Error('Введите секрет локального владельца');
    return true;
  }
  function checked(idName,text) {
    if (!el(idName).checked) throw Error(text);
  }
  function moneyCheck() {checked('spConsentCheck','Подтверди смету и разрешённую сумму отдельно');}
  function readLimits() {return core.json(value('spLimitsJson'),null);}
  function qv() {return id('spValidationId');}
  function generation() {return id('spGenerationId');}
  function publication() {return id('spPublication');}
  function publicationSource() {
    const option=el('spPublication').selectedOptions[0];
    return option?.dataset?.source==='rtb_deep_v2'?'rtb_deep_v2':'deep_c9_v1';
  }
  function policy() {
    return core.policy({languages:value('spLanguages'),markets:value('spMarkets'),
      target:value('spTarget'),perHyp:value('spMaxHyp'),total:value('spMaxTotal'),
      keyword:el('spKeyword').checked,hashtag:el('spHashtag').checked});
  }
  function requestKey(field) {
    const item=el(field);
    if (!item.value.trim()) item.value=core.newKey();
    return core.idempotencyKey(item.value.trim());
  }
  function resetChecks() {
    for(const idName of ['spConsentCheck','spPaidConfirm','spHeldConfirm','spDeliveryConfirm'])
      el(idName).checked=false;
    draftQuote=null;
    el('spQuoteHint').textContent='После обновления исходных данных получите актуальную смету.';
  }
  function clearDownstream(reason) {
    for(const target of ['spQuoteId','spConsentId','spSourceOutbox','spValidationId',
                         'spInboxId','spPaidKey','spHeldKey']) el(target).value='';
    resetChecks();
    if(reason) note(reason);
  }
  function feedback(action,output) {
    if(action==='model-build') return {
      text:'Подготовлен локальный черновик JSON; зарегистрируй конфигурацию отдельно.',
      warning:false
    };
    const status=output && !Array.isArray(output) && typeof output.status==='string'
      ? output.status : '';
    if(['needs_attention','uncertain','incompatible','paused_budget','paused_price',
        'blocked_config','failed'].includes(status))return {
      text:action+' · '+status+'. Требуется ручная сверка, не выполняй автоматический повтор.',
      warning:true
    };
    if(status==='awaiting_collector'||output?.collector_available===false)return {
      text:action+' · сохранено в SP. Collector пока не подтвердил приём.',
      warning:false
    };
    if(status==='no_work')return {
      text:action+' · подтверждено отсутствие выбранных запросов (no_work).',
      warning:false
    };
    return {text:action+' · ответ получен от сервера. Проверь фактический статус и UUID.',
            warning:false};
  }
  function setStep(next) {
    if (!panels.includes(next)) return;
    currentStep=next;
    root.querySelectorAll('[data-sp-step]').forEach(b=>{
      const active=b.dataset.spStep===next;
      b.classList.toggle('is-active',active);
      if(active)b.setAttribute('aria-current','step');
      else b.removeAttribute('aria-current');
    });
    root.querySelectorAll('[data-sp-panel]').forEach(p=>{
      p.hidden=p.dataset.spPanel!==next;
    });
  }
  function clearResults() {
    for(const pane of Object.values(paneResult))el(pane).replaceChildren();
    for(const pane of ['spHistoryPaid','spHistoryConfigs','spHistoryGenerations',
                      'spHistoryValidations','spHistoryInbox'])
      el(pane).replaceChildren();
    lastOutputs.clear();
  }
  function lock() {
    if(client)client.forget();
    el('spOwnerToken').value='';
    el('spAuthState').textContent='Только чтение. Для POST введи секрет владельца.';
    el('spLock').disabled=true;
  }
  function stopPending() {
    if(requestController){requestController.abort();requestController=null;}
    busy=false;
    el('spBusy').hidden=true;
    root.querySelectorAll('[data-sp-action]').forEach(b=>b.disabled=false);
  }
  function close() {
    stopPending();lock();
    project=null;client=null;lastOverview=null;modelCatalog=null;
    el('spModelSelect').replaceChildren(new Option('Сначала загрузи каталог',''));
    el('spPublication').replaceChildren(new Option('Публикации не загружены',''));
    for(const idName of ['spConfigId','spGenerationId','spQuoteId',
      'spSourceOutbox','spValidationId','spInboxId','spConfigJson',
      'spLimitsJson','spPlanKey','spPaidKey','spHeldKey','spConsentId','spAttemptId','spMatchJson'])
      if(el(idName)) el(idName).value='';
    clearResults();resetChecks();note('');
  }
  function qvStructuredSummary(parent,data) {
    if(!data || data.schema_version!=='validated_queries/v1' ||
        !Array.isArray(data.coverage) || !Array.isArray(data.queries))return;
    const summary=document.createElement('div');summary.className='sp-qv-summary';
    const title=document.createElement('h4');
    title.textContent='Query Validation · проверенное покрытие по гипотезам';
    summary.append(title);
    const caption=document.createElement('p');caption.className='sp-muted';
    caption.textContent='Показатели similarity/MMR — сравнительные оценки эмбеддингов, не реальная точность поиска. Выбранные запросы ещё не отправлены источникам.';
    summary.append(caption);
    const selected=new Map();
    for(const q of data.queries){
      if(typeof q.hypothesis_id !== 'string')continue;
      if(!selected.has(q.hypothesis_id))selected.set(q.hypothesis_id,[]);
      selected.get(q.hypothesis_id).push(q);
    }
    const reasons=new Map();
    for(const d of (data.decisions||[])){
      const code=String(d.reason_code||'unknown');
      if(d.decision==='rejected')reasons.set(code,(reasons.get(code)||0)+1);
    }
    if(reasons.size){
      const heading=document.createElement('p');heading.className='sp-muted';
      heading.textContent='Причины отклонений: '+[...reasons.entries()].map(
        ([k,n])=>k+' · '+n).join(' / ');
      summary.append(heading);
    }
    for(const coverage of data.coverage){
      const section=document.createElement('details');
      section.className='sp-qv-group';
      const heading=document.createElement('summary');
      heading.textContent=String(coverage.hypothesis_id)+' · выбрано '+
        String(coverage.selected_count)+' из '+String(coverage.source_count)+
        ' · '+String(coverage.outcome);
      section.append(heading);
      if(coverage.reason){
        const reason=document.createElement('p');reason.className='sp-muted';
        reason.textContent='Причина отсутствия результатов: '+String(coverage.reason);
        section.append(reason);
      }
      for(const q of selected.get(coverage.hypothesis_id)||[]){
        const row=document.createElement('div');row.className='sp-qv-query';
        const text=document.createElement('strong');text.textContent=String(q.text);
        const meta=document.createElement('span');
        meta.textContent=String(q.query_mode)+' · '+String(q.language)+
          ' · relevance '+String(q.relevance_score)+
          ' · MMR '+String(q.mmr_score)+
          ' · ID '+String(q.candidate_id);
        row.append(text,meta);section.append(row);
      }
      summary.append(section);
    }
    parent.append(summary);
  }
  function safelyShowResult(targetId,label,result) {
    const parent=el(targetId);
    if(!parent) return;
    parent.replaceChildren();
    const header=document.createElement('div');
    header.className='sp-result-head';
    const title=document.createElement('strong');
    title.textContent=label;
    header.append(title);
    const full=JSON.stringify(result,null,2);
    const download=document.createElement('button');
    download.type='button';download.className='sp-action';
    download.textContent='Сохранить полный JSON';
    download.addEventListener('click',()=>{
      const blob=new Blob([full],{type:'application/json;charset=utf-8'});
      const url=URL.createObjectURL(blob);
      const a=document.createElement('a');
      a.href=url;a.download='content-machine-search-planner-result.json';
      document.body.append(a);a.click();a.remove();
      window.setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    header.append(download);
    const pre=document.createElement('pre');
    pre.className='sp-json';
    const cap=100000;
    pre.textContent=full.length>cap
      ? full.slice(0,cap)+'\n… На экране показана только часть. Полные данные доступны через «Сохранить полный JSON».'
      : full;
    parent.append(header);
    qvStructuredSummary(parent,result);
    parent.append(pre);
    lastOutputs.set(targetId,result);
  }
  function remember(output) {
    if (!output || typeof output!=='object')return;
    if(coreIsUUID(output.config_id))el('spConfigId').value=output.config_id;
    if(coreIsUUID(output.generation_id)) {
      if(el('spGenerationId').value && el('spGenerationId').value!==output.generation_id)
        clearDownstream('Generation изменился; прежняя смета и downstream IDs очищены.');
      el('spGenerationId').value=output.generation_id;
    }
    if(coreIsUUID(output.quote_id))el('spQuoteId').value=output.quote_id;
    if(coreIsUUID(output.consent_id))el('spConsentId').value=output.consent_id;
    if(coreIsUUID(output.validation_run_id))el('spValidationId').value=output.validation_run_id;
    if(coreIsUUID(output.event_id))el('spInboxId').value=output.event_id;
    if(coreIsUUID(output.source_outbox_id))el('spSourceOutbox').value=output.source_outbox_id;
  }
  function coreIsUUID(x) {try {core.uuid(x);return true;}catch(_){return false;}}
  function addHistoryGroup(targetId,title,rows,formatter,selector) {
    const container=el(targetId);container.replaceChildren();
    const heading=document.createElement('h4');heading.textContent=title;
    container.append(heading);
    if (!Array.isArray(rows) || rows.length===0){
      const empty=document.createElement('p');empty.className='sp-muted';
      empty.textContent='Подтверждённых записей нет';
      container.append(empty);return;
    }
    for(const row of rows){
      const card=document.createElement('div');card.className='sp-history-item';
      const left=document.createElement('span');left.textContent=formatter(row);
      const button=document.createElement('button');button.type='button';
      button.className='sp-action';button.textContent='Выбрать';
      button.addEventListener('click',()=>{
        selector(row);note('Выбрана исходная запись из PostgreSQL, без запуска задачи.');
      });
      card.append(left,button);container.append(card);
    }
  }
  function renderOverview(data) {
    lastOverview=data;
    addHistoryGroup('spHistoryPaid','Платные HTTP-запросы — используй прежний ключ при повторе',data.paid_http_requests,
      r=>r.status+' · '+r.idempotency_key+' · '+r.generation_id,
      r=>{el('spPaidKey').value=r.idempotency_key;
        el('spGenerationId').value=r.generation_id;
        el('spPaidConfirm').checked=false;setStep('generation');});
    addHistoryGroup('spHistoryConfigs','Конфигурации',data.configs,
      r=>'Версия '+r.revision+' · '+r.config_id+' · '+(r.model?.model_id||'model ID скрыт'),
      r=>{el('spConfigId').value=r.config_id;setStep('generation');});
    addHistoryGroup('spHistoryGenerations','Generation Runs',data.generations,
      r=>r.status+' · '+r.generation_id,
      r=>{el('spGenerationId').value=r.generation_id;
        el('spConfigId').value=r.config_id;
        if(coreIsUUID(r.publication_id))el('spPublication').value=r.publication_id;
        if(r.source_outbox_id)el('spSourceOutbox').value=r.source_outbox_id;
        resetChecks();setStep('generation');});
    addHistoryGroup('spHistoryValidations','Query Validation',data.validations,
      r=>r.embedding_mode+' · '+r.selected_count+'/'+r.candidate_count+' · '+r.validation_run_id,
      r=>{el('spValidationId').value=r.validation_run_id;
        el('spGenerationId').value=r.generation_id;
        setStep('validation');});
    addHistoryGroup('spHistoryInbox','Durable SP Inbox',data.inbox,
      r=>r.status+' · '+r.selected_count+' запросов · '+r.chunk_count+' пакетов · '+r.event_id,
      r=>{el('spInboxId').value=r.event_id;
        el('spValidationId').value=r.validation_run_id;
        setStep('handoff');});
  }
  async function run(action) {
    if (!project || busy) return;
    const selectedProject=project.id;
    busy=true;
    const ac=new AbortController();requestController=ac;
    el('spBusy').hidden=false;
    root.querySelectorAll('[data-sp-action]').forEach(b=>b.disabled=true);
    let dest=paneResult[currentStep],output=null;
    try {
      const get=(p)=>client.get(p,ac.signal);
      const post=(p,v={})=>{ownerReady();return client.post(p,v,ac.signal);};
      switch(action){
        case 'publications': {
          dest='spContextsResult';
          output=await get('/publications?limit=50');
          let v2=null;
          try {v2=await get('/publications-v2?limit=50');}
          catch(err) {
            note('RTB Deep V2 недоступен: настрой CM_RTB_REVIEWER_ID и миграцию. Список Deep C9 сохранён.');
          }
          const list=[...(output.publications||[]),...(v2?.publications||[])];
          output={...output,publications:list,count:list.length,
            deep_v2_available:v2!==null};
          const select=el('spPublication'),old=select.value;
          select.replaceChildren(new Option('Выбери одобренную публикацию',''));
          for(const p of output.publications||[]){
            if(!coreIsUUID(p.taxonomy_publication_id))continue;
            const source=p.publication_source==='rtb_deep_v2'?'rtb_deep_v2':'deep_c9_v1';
            const title=(source==='rtb_deep_v2'?'RTB Deep V2 · ':'Deep C9 · ')+
              'v'+p.taxonomy_version+' · '+p.taxonomy_publication_id+
              ' · '+p.approved_hypothesis_count+' гипотез';
            const option=new Option(title,p.taxonomy_publication_id);
            option.dataset.source=source;
            select.append(option);
          }
          if([...select.options].some(x=>x.value===old))select.value=old;
          break;
        }
        case 'contexts':
          output=await post('/contexts/preview',{publication_id:publication(),search_policy:policy(),
            publication_source:publicationSource()});
          dest='spContextsResult';break;
        case 'models': {
          dest='spGenerationResult';
          output=await get('/models');
          modelCatalog=output;
          const select=el('spModelSelect');
          select.replaceChildren(new Option('Выбери verified usable модель',''));
          for(const model of output.models||[]){
            if(model.selectable!==true || !Number.isSafeInteger(model.catalog_version_id))continue;
            const label=(model.display_name||model.model_id)+' · snapshot #'+model.catalog_version_id;
            select.append(new Option(label,String(model.catalog_version_id)));
          }
          el('spModelHint').textContent=String(output.available_count||0)+
            ' пригодных моделей. Отсутствие свежих снимков не обходится вручную.';
          break;
        }
        case 'model-build': {
          dest='spGenerationResult';
          if(!modelCatalog)throw Error('Сначала обнови серверный каталог моделей');
          const selected=Number(value('spModelSelect'));
          const model=(modelCatalog.models||[]).find(
            item=>item.catalog_version_id===selected && item.selectable===true);
          output=core.buildGenerationConfig(policy(),model,
            modelCatalog.prompt_pins,value('spModelOutputTokens'));
          el('spConfigJson').value=JSON.stringify(output,null,2);
          note('Конфигурация подготовлена локально из SHA-подтверждённых версий. Для сохранения отдельно нажми «Зарегистрировать конфигурацию».');
          break;
        }
        case 'config':
          output=await post('/generation/configs',{configuration:core.json(value('spConfigJson'))});
          dest='spGenerationResult';break;
        case 'plan':
          output=await post('/generation/plan',{publication_id:publication(),config_id:id('spConfigId'),
            idempotency_key:requestKey('spPlanKey'),publication_source:publicationSource()});
          dest='spGenerationResult';break;
        case 'generation-status':
          output=await get('/generation/'+generation());
          dest='spGenerationResult';break;
        case 'generation-candidates':
          output=await get('/generation/'+generation()+'/candidates');
          dest='spGenerationResult';break;
        case 'execution-status':
          output=await get('/generation/'+generation()+'/execution');
          dest='spGenerationResult';break;
        case 'stale-reservations':
          output=await get('/generation/'+generation()+'/stale-reservations');
          dest='spGenerationResult';break;
        case 'quote':
          output=await post('/generation/'+generation()+'/quote',{});
          dest='spGenerationResult';break;
        case 'budget':
          output=await get('/generation/'+generation()+'/budget');
          dest='spGenerationResult';break;
        case 'consent':
          moneyCheck();
          core.requireUsd(value('spAuthorizedUsd'));
          if(!window.confirm('Подтвердить только денежный лимит '+value('spAuthorizedUsd')+' USD? Это ещё не запуск модели.'))return;
          output=await post('/generation/'+generation()+'/consent',{
            quote_id:id('spQuoteId'),authorized_usd:core.requireUsd(value('spAuthorizedUsd'))});
          dest='spGenerationResult';el('spConsentCheck').checked=false;break;
        case 'revoke':
          if(!window.confirm('Отозвать финансовое согласие? Будущие платные запросы по нему станут запрещены.'))return;
          output=await post('/consent/'+id('spConsentId')+'/revoke',{});
          dest='spGenerationResult';el('spConsentCheck').checked=false;break;
        case 'release-reservation':
          if(!window.confirm('Освободить только подтверждённо НЕ отправленный резерв? Не освобождай деньги для dispatch_intent или неопределённых расходов.'))return;
          output=await post('/generation/'+generation()+'/reservation/'+id('spAttemptId'),{});
          dest='spGenerationResult';break;
        case 'preflight':
          output=await get('/generation/'+generation()+'/preflight');
          dest='spGenerationResult';break;
        case 'execute':
          checked('spPaidConfirm','Отдельно подтверди один платный batch');
          if(!window.confirm('Реально выполнить ОДИН платный AI Query batch? Сервер проверит включение worker и деньги.'))return;
          output=await post('/generation/'+generation()+'/execute-one',{
            idempotency_key:requestKey('spPaidKey')});
          dest='spGenerationResult';el('spPaidConfirm').checked=false;break;
        case 'cancel':
          if(!window.confirm('Отменить Generation и сохранить финансовые записи об уже возможных расходах?'))return;
          output=await post('/generation/'+generation()+'/cancel',{});
          dest='spGenerationResult';break;
        case 'recover':
          output=await post('/generation/'+generation()+'/recover',{});
          dest='spGenerationResult';break;
        case 'validate':
          output=await post('/qv/validate',{source_outbox_id:id('spSourceOutbox'),
            policy:core.json(value('spQvPolicy'))});
          dest='spValidationResult';break;
        case 'validated':
          output=await get('/qv/'+qv()+'/validated');
          dest='spValidationResult';break;
        case 'links':
          output=await get('/qv/'+qv()+'/links');
          dest='spValidationResult';break;
        case 'capabilities-match':
          output=await post('/capabilities/match',{query:core.json(value('spMatchJson'))});
          dest='spPlanningResult';break;
        case 'capabilities':
          output=await get('/capabilities');
          dest='spPlanningResult';break;
        case 'tasks-preview':
          output=await post('/tasks/'+qv()+'/preview',{limits:readLimits()});
          dest='spPlanningResult';break;
        case 'tasks-commit':
          checked('spHeldConfirm','Подтверди источник, лимиты и HOLD-план');
          if(!window.confirm('Сохранить immutable HOLD-план? Collector и поиск не запускаются.'))return;
          output=await post('/tasks/'+qv()+'/commit',{
            idempotency_key:requestKey('spHeldKey'),limits:readLimits()});
          dest='spPlanningResult';el('spHeldConfirm').checked=false;break;
        case 'handoff-preview':
          output=await get('/handoff/'+qv()+'/preview?chunk_size=50');
          dest='spInboxResult';break;
        case 'handoff-drain':
          if(!window.confirm('Атомарно сохранить до 10 ожидающих production QV-событий в SP? Это подтвердит QV→SP, но не Collector.'))return;
          output=await post('/handoff/drain',{limit:10});
          dest='spInboxResult';break;
        case 'handoff-ingest':
          output=await post('/handoff/'+qv()+'/ingest',{});
          dest='spInboxResult';break;
        case 'inbox-status':
          output=await get('/inbox/'+id('spInboxId')+'/status');
          dest='spInboxResult';break;
        case 'deliver':
          checked('spDeliveryConfirm','Подтверди готовность Collector и разрешение на один intake');
          if(!window.confirm('Передать ОДИН пакет в Collector? Это не авторизация внешнего поиска.'))return;
          output=await post('/inbox/'+id('spInboxId')+'/deliver',{});
          dest='spInboxResult';el('spDeliveryConfirm').checked=false;break;
        case 'reconcile': {
          const index=Number(value('spReconcileIndex'));
          if(!Number.isSafeInteger(index)||index<0||index>=480)
            throw Error('Укажи индекс пакета от 0 до 479');
          output=await post('/inbox/'+id('spInboxId')+'/reconcile',{chunk_index:index});
          dest='spInboxResult';break;
        }
        case 'history':
          output=await get('/overview?limit=30');
          renderOverview(output);
          dest=null;break;
        default:throw Error('Неизвестная операция');
      }
      if(project?.id!==selectedProject || ac.signal.aborted)return;
      remember(output);
      if(action==='quote'&&output?.quote_id) {
        draftQuote=output;
        el('spQuoteHint').textContent='Смета получена: верхний прогноз '+
          String(output.high_usd||'—')+' USD. Отдельно подтверди лимит ниже.';
        el('spConsentCheck').checked=false;
      }
      if(dest) safelyShowResult(dest,'Подтверждённый ответ сервера · '+action,output);
      const status=feedback(action,output);
      note(status.text,status.warning);
    }catch(err){
      if(project?.id===selectedProject && !ac.signal.aborted)
        note(messageFor(err),true);
    }finally{
      if(requestController===ac){
        requestController=null;busy=false;
        el('spBusy').hidden=true;
        root.querySelectorAll('[data-sp-action]').forEach(b=>b.disabled=false);
      }
    }
  }

  function open(projectId,name) {
    close();
    const pid=core.projectId(projectId);
    project={id:pid,name:String(name||'Проект')};
    client=core.createClient(window.fetch.bind(window),pid);
    el('spProjectCaption').textContent='Проект: '+project.name+
      '. Работа только с подтверждёнными C9 и production QV, без автоматического поиска.';
    el('spQvPolicy').value=JSON.stringify(core.QV_DEFAULT,null,2);
    setStep('c9');
    note('Режим только чтения. Обнови публикации или открой историю из PostgreSQL.');
    run('publications');
  }
  root.querySelectorAll('[data-sp-step]').forEach(b=>
    b.addEventListener('click',()=>setStep(b.dataset.spStep)));
  root.querySelectorAll('[data-sp-action]').forEach(b=>
    b.addEventListener('click',()=>run(b.dataset.spAction)));
  root.querySelectorAll('[data-sp-new-key]').forEach(button=>{
    button.addEventListener('click',()=>{
      const idName=button.dataset.spNewKey;
      if(!['spPlanKey','spPaidKey','spHeldKey'].includes(idName))return;
      if(idName==='spPaidKey' && !window.confirm(
        'Создать НОВЫЙ ключ платного запроса? Если предыдущий результат неизвестен, сначала проверь бюджет и SQL ledger. Новый ключ разрешит другой batch только после отдельного согласия.'
      ))return;
      try{
        el(idName).value=core.newKey();
        if(idName==='spPaidKey')el('spPaidConfirm').checked=false;
        if(idName==='spHeldKey')el('spHeldConfirm').checked=false;
        note('Новый ключ подготовлен. Для повторного запроса используй исходный ключ, а не создавай новый.');
      }catch(error){note(messageFor(error),true);}
    });
  });
  el('spUnlock').addEventListener('click',()=>{
    try{
      if(!client)throw Error('Сначала выбери проект');
      client.setSecret(el('spOwnerToken').value);
      el('spOwnerToken').value='';
      el('spAuthState').textContent='Секрет подготовлен в памяти вкладки. Подлинность сервер проверит при каждом POST.';
      el('spLock').disabled=false;note('Разрешение подготовлено. Серверная авторизация ещё не выполнена.');
    }catch(err){note(messageFor(err),true);}
  });
  for(const idName of ['spLock','spLockFooter'])
    el(idName).addEventListener('click',()=>{lock();note('Секрет очищен из памяти вкладки.');});
  el('spOwnerToken').addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();el('spUnlock').click();}
  });
  for(const idName of ['spPublication','spLanguages','spMarkets','spTarget',
    'spMaxHyp','spMaxTotal','spKeyword','spHashtag','spConfigId',
    'spModelSelect','spModelOutputTokens']) {
    el(idName).addEventListener('change',()=>{
      if(['spPublication','spLanguages','spMarkets','spTarget',
          'spMaxHyp','spMaxTotal','spKeyword','spHashtag'].includes(idName)) {
        el('spConfigId').value='';
        el('spGenerationId').value='';
        el('spPlanKey').value='';
      }
      clearDownstream('Изменился источник или настройка. Старые Quote/QV/Inbox ID очищены.');
    });
  }
  el('spGenerationId').addEventListener('change',()=>{
    clearDownstream('Generation изменён. Проверь смету, QV и Inbox заново.');
  });
  el('spValidationId').addEventListener('change',()=>{
    el('spInboxId').value='';el('spHeldKey').value='';
    resetChecks();
  });
  for(const idName of ['spLimitsJson','spAuthorizedUsd'])
    el(idName).addEventListener('change',resetChecks);
  el('spBack').addEventListener('click',()=>{
    const id=project?.id;
    close();
    if(id)document.dispatchEvent(new CustomEvent('cm-search-planner-return',
      {detail:{projectId:id}}));
  });
  window.addEventListener('beforeunload',lock);
  document.addEventListener('cm-sp-leave',()=>close());
  window.ContentMachineSearchPlanner={open,close,lock};
})();
