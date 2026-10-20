'use strict';
/* Taxonomy Pipeline Studio, Block 4. No paid requests or fake pricing. */
(() => {
  const $ = id => document.getElementById(id);
  const ROLE = [
    {id:'fact_extraction',title:'Fact Extraction',prompt:'facts-v1',
      detail:'Выделение функций продукта и проблем аудитории с цитатами из анкеты.',tokens:3000},
    {id:'taxonomy_generation',title:'Taxonomy Generation',prompt:'taxonomy-v1',
      detail:'Создание исследовательских направлений из утверждённых человеком фактов.',tokens:5000},
    {id:'taxonomy_critic',title:'Taxonomy Critic',prompt:'critic-v1',
      detail:'Дополнительная проверка пересечений, пробелов и противоречий.',tokens:2500}
  ];
  let projectId=null,epoch=0,state=null,models=[],config=null,selected=0;
  let draftRevision=0,dirty=false,busy=false,refreshBusy=false,loaded=false;
  const clone = value => JSON.parse(JSON.stringify(value));
  const emptyStage = (role) => ({
    role:role.id,enabled:true,model_id:'',catalog_version_id:null,
    reasoning:{mode:'default'},temperature:null,max_output_tokens:role.tokens,
    prompt_version:role.prompt
  });
  const freshConfig = () => ({schema_version:1,stages:[
    emptyStage(ROLE[0]),emptyStage(ROLE[1]),{role:ROLE[2].id,enabled:false}
  ]});
  function editableConfig(raw) {
    // Backend correctly permits a two-stage Pipeline with NO Critic entry.
    // Studio always displays the third optional slot.
    const value=clone(raw);
    if(!value.stages.some(stage=>stage.role==='taxonomy_critic'))
      value.stages.push({role:'taxonomy_critic',enabled:false});
    return value;
  }
  const model = id => models.find(m=>m.model_id===id);
  const activeStage = () => config?.stages?.[selected] || null;
  const usable = row => row?.selectable===true && Number.isInteger(row?.catalog_version_id);
  const statusLabels = {
    usable:'Готова к выбору',missing:'Не найдена в последней проверке OpenRouter',
    stale:'Каталог устарел — обнови метаданные',
    unverified:'Ещё не проверена в OpenRouter',
    unsupported:'Нет подтверждённого JSON-вывода'
  };
  async function api(url,body) {
    const response=await fetch(url,body===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(body)
    });
    let data;
    try {data=await response.json();}
    catch (_) {throw Error('Сервер вернул нечитаемый ответ.');}
    if(!response.ok || !data.ok) {
      const err=Error(data.error||'Ошибка сервера '+response.status);
      err.status=response.status;
      throw err;
    }
    return data;
  }
  const base=()=>'/api/projects/'+encodeURIComponent(projectId)+'/taxonomy-builder';
  function report(error='',notice='') {
    $('tbStudioError').hidden=!error;
    $('tbStudioError').textContent=error;
    $('tbStudioNotice').hidden=!notice;
    $('tbStudioNotice').textContent=notice;
  }
  function setBusy(value) {busy=value;actions();}
  function describeReason(reason) {
    if(!reason) return 'Не выбран';
    if(reason.mode==='default') return 'По умолчанию';
    if(reason.mode==='effort') return reason.effort;
    return reason.budget_tokens+' токенов';
  }
  function stageSummary(stage) {
    if(!stage.enabled) return 'Необязательный этап отключён';
    return (model(stage.model_id)?.display_name||'Модель не выбрана')+' · '+describeReason(stage.reasoning);
  }
  function updateRail() {
    const list=$('tbStageList');
    list.replaceChildren();
    for(let i=0;i<ROLE.length;i++) {
      const stage=config.stages[i],role=ROLE[i];
      const item=document.createElement('button');
      item.type='button';item.className='tb-rail-stage'+(selected===i?' active':'');
      item.setAttribute('aria-pressed',String(selected===i));
      const num=document.createElement('span');num.className='tb-rail-stage-num';num.textContent=String(i+1).padStart(2,'0');
      const description=document.createElement('span');
      const title=document.createElement('strong');title.textContent=role.title;
      const sub=document.createElement('small');sub.textContent=stageSummary(stage);
      description.append(title,sub);item.append(num,description);
      item.addEventListener('click',()=>{selected=i;render();});
      list.append(item);
      if(i===0) {
        const checkpoint=document.createElement('p');
        checkpoint.className='tb-rail-checkpoint';
        checkpoint.textContent='↳ Проверка фактов человеком';
        list.append(checkpoint);
      }
    }
  }
  function option(select,value,label,disabled=false) {
    const node=document.createElement('option');
    node.value=value;node.textContent=label;node.disabled=disabled;select.append(node);
    return node;
  }
  function fillModels(search) {
    const select=$('tbModelSelect');
    const stage=activeStage();
    const current=stage.model_id;
    select.replaceChildren();
    option(select,'','Выбери модель',false);
    const query=(search||'').trim().toLocaleLowerCase();
    const filtered=models.filter(m=>!query ||
      (m.display_name+' '+m.model_id).toLocaleLowerCase().includes(query)||
      m.model_id===current);
    for(const row of filtered) {
      const isCurrent=row.model_id===current;
      option(select,row.model_id,
        row.display_name+' · '+(statusLabels[row.status]||row.status),
        !usable(row)&&!isCurrent);
    }
    if(current && !models.some(m=>m.model_id===current))
      option(select,current,current+' · Модель отсутствует в каталоге',true);
    select.value=current||'';
    select.disabled=busy||refreshBusy||!stage.enabled;
  }
  function temperatureAllowed(stage,cap) {
    if(!cap?.supports_temperature) return [false,'Модель не поддерживает temperature.'];
    const reason=stage.reasoning || {mode:'default'};
    if(reason.mode==='budget')
      return [false,'Совместимость temperature с reasoning budget не подтверждена.'];
    if(reason.mode==='effort' &&
       !(cap.temperature_compatible_efforts||[]).includes(reason.effort))
      return [false,'Совместимость temperature с выбранным reasoning не подтверждена.'];
    if(reason.mode==='default' &&
       (cap.reasoning_mandatory||cap.reasoning_default_enabled) &&
       !cap.temperature_default_compatible)
      return [false,'Совместимость temperature с reasoning по умолчанию не подтверждена.'];
    return [true,'Можно задать температуру от 0 до 2. Если оставить выключенной, провайдер выберет значение сам.'];
  }
  function renderSettings() {
    const role=ROLE[selected],stage=activeStage(),entry=model(stage.model_id);
    const cap=(entry?.capabilities)||null;
    $('tbStageIndex').textContent='ЭТАП '+String(selected+1).padStart(2,'0');
    $('tbStageTitle').textContent=role.title;
    $('tbStageDescription').textContent=role.detail;
    $('tbCriticToggleWrap').hidden=selected!==2;
    $('tbCriticEnabled').checked=!!stage.enabled;
    $('tbCriticEnabled').disabled=busy;
    $('tbStageControls').hidden=!stage.enabled;
    $('tbDisabledCritic').hidden=!!stage.enabled;
    if(!stage.enabled) return;
    $('tbModelSearch').value='';
    $('tbModelSearch').disabled=busy;
    fillModels('');
    const modelInfo=entry?.capabilities;
    $('tbModelHint').textContent=!entry?
      'Выбери модель из каталога. Сначала обнови его при необходимости.':
      (statusLabels[entry.status]||'Нет сведений о модели')+
      '. JSON: '+(cap?.supports_structured_outputs?'подтверждён':'не подтверждён')+
      '. Максимум вывода: '+(cap?.max_output_tokens||'не указан')+'.';
    const reason=$('tbReasoningMode');reason.replaceChildren();
    option(reason,'default','Настройка провайдера');
    if((cap?.reasoning_efforts||[]).length) option(reason,'effort','Уровень reasoning');
    if(cap?.reasoning_budget_limits) option(reason,'budget','Бюджет reasoning-токенов');
    const selectedMode=stage.reasoning?.mode||'default';
    if(!Array.from(reason.options).some(x=>x.value===selectedMode))
      option(reason,selectedMode,'Режим недоступен в текущем каталоге',true);
    reason.value=selectedMode;
    reason.disabled=busy || !usable(entry);
    $('tbReasoningEffortWrap').hidden=selectedMode!=='effort';
    const effort=$('tbReasoningEffort');effort.replaceChildren();
    for(const value of cap?.reasoning_efforts||[]) option(effort,value,value);
    if(selectedMode==='effort' && !(cap?.reasoning_efforts||[]).includes(stage.reasoning.effort))
      option(effort,stage.reasoning.effort||'','Ранее сохранённый уровень недоступен',true);
    effort.value=stage.reasoning.effort||'';
    effort.disabled=busy||!usable(entry);
    $('tbReasoningBudgetWrap').hidden=selectedMode!=='budget';
    const budget=$('tbReasoningBudget');
    const bounds=cap?.reasoning_budget_limits;
    budget.min=bounds?.min||1;budget.max=bounds?.max||100000;
    budget.value=stage.reasoning.budget_tokens||'';
    budget.disabled=busy||!usable(entry);
    $('tbReasoningHint').textContent=(!cap?.reasoning_efforts?.length&&!bounds)
      ? 'Параметры reasoning для этой модели не подтверждены: можно оставить режим провайдера.'
      : 'Варианты получены из сохранённого снимка OpenRouter для этой модели.';
    const [canTemperature,tempHint]=temperatureAllowed(stage,cap);
    $('tbTemperatureHint').textContent=tempHint;
    $('tbTemperatureEnable').checked=stage.temperature!==null;
    $('tbTemperatureEnable').disabled=busy||!usable(entry)||!canTemperature;
    $('tbTemperature').value=stage.temperature===null?'0.7':String(stage.temperature);
    $('tbTemperature').disabled=busy||!usable(entry)||!canTemperature||stage.temperature===null;
    const max=$('tbOutputTokens');
    max.max=cap?.max_output_tokens||1000000;max.value=stage.max_output_tokens;
    max.disabled=busy||!usable(entry);
    $('tbOutputHint').textContent='Минимум 128 токенов'+
      (cap?.max_output_tokens?', максимум модели: '+cap.max_output_tokens:'; максимальный лимит провайдера не указан')+
      '. Reasoning может использовать часть лимита.';
    $('tbPromptLabel').textContent='Промпт '+stage.prompt_version+' · проверка фактов и утверждение человеком обязательны';
  }
  function getInvalidity() {
    if(!loaded) return 'Не удалось загрузить Pipeline и каталог моделей.';
    for(const stage of config.stages) {
      if(!stage.enabled) continue;
      const row=model(stage.model_id);
      if(!usable(row)||row.catalog_version_id!==stage.catalog_version_id)
        return 'Выбери подтверждённую модель для '+stage.role+'.';
      const cap=row.capabilities||{};
      if(!cap.text_output||!cap.supports_structured_outputs)
        return 'Для '+stage.role+' необходим поддерживаемый JSON-вывод.';
      if(!Number.isSafeInteger(stage.max_output_tokens)||stage.max_output_tokens<128||
        (cap.max_output_tokens && stage.max_output_tokens>cap.max_output_tokens))
        return 'Проверь лимит токенов в '+stage.role+'.';
      if(stage.reasoning.mode==='effort' &&
         !(cap.reasoning_efforts||[]).includes(stage.reasoning.effort))
        return 'Этот уровень reasoning недоступен в '+stage.role+'.';
      if(stage.reasoning.mode==='budget'&&
        (!cap.reasoning_budget_limits ||
         !Number.isSafeInteger(stage.reasoning.budget_tokens) ||
         stage.reasoning.budget_tokens>=stage.max_output_tokens))
        return 'Недоступный reasoning budget в '+stage.role+'.';
      if(stage.temperature!==null) {
        const [valid]=temperatureAllowed(stage,cap);
        if(!valid||!Number.isFinite(stage.temperature)||stage.temperature<0||stage.temperature>2)
          return 'Temperature несовместима с reasoning в '+stage.role+'.';
      }
    }
    return '';
  }
  function actions() {
    const invalid=getInvalidity();
    const canSave=loaded&&!busy&&!refreshBusy&&!invalid;
    $('tbSaveDraft').disabled=!canSave||!dirty;
    $('tbDiscardChanges').disabled=!loaded||busy||refreshBusy||!dirty;
    const approved=state?.status==='pipeline_approved'&&!dirty;
    $('tbApprove').disabled=!loaded||busy||refreshBusy||dirty||!draftRevision||!!invalid||approved;
    $('tbApprove').textContent=approved?'Pipeline утверждён ✓':'Утвердить Pipeline →';
    $('tbSaveState').textContent=busy?'Сохраняем изменения…':
      dirty?(invalid||'Есть несохранённые изменения'):
      approved?'Утверждённая конфигурация · версия '+state.approved_versions[0].pipeline_version:
      draftRevision?'Черновик v'+draftRevision+' сохранён':'Новый черновик — ещё не сохранён';
    $('tbApprovedNext').hidden=!approved;
    $('tbRefreshCatalog').disabled=busy||refreshBusy;
    $('tbRefreshCatalog').textContent=refreshBusy?'Обновление…':'↻ Обновить каталог моделей';
  }
  function render() {
    if(!config||!loaded)return;
    updateRail();renderSettings();
    $('tbStudioLayout').hidden=false;
    $('tbStudioBottom').hidden=false;
    actions();
  }
  function changed() {
    dirty=true;
    report();
    render();
  }
  function updateStage(fn) {
    if(busy||!loaded)return;
    fn(activeStage());
    changed();
  }
  function useModel(value) {
    updateStage(stage=>{
      const row=model(value);
      stage.model_id=value;
      stage.catalog_version_id=usable(row)?row.catalog_version_id:null;
      stage.reasoning={mode:'default'};
      stage.temperature=null;
      const limit=row?.capabilities?.max_output_tokens;
      stage.max_output_tokens=limit?Math.min(ROLE[selected].tokens,limit):ROLE[selected].tokens;
    });
  }
  function changeReasoningMode(mode) {
    updateStage(stage=>{
      const cap=model(stage.model_id)?.capabilities||{};
      if(mode==='effort') stage.reasoning={mode:'effort',effort:cap.reasoning_efforts?.[0]};
      else if(mode==='budget')
        stage.reasoning={mode:'budget',budget_tokens:cap.reasoning_budget_limits?.min||1};
      else stage.reasoning={mode:'default'};
      const [allowed]=temperatureAllowed(stage,cap);
      if(!allowed) stage.temperature=null;
    });
  }
  function history() {
    const root=$('tbHistoryList');root.replaceChildren();
    const rows=state?.approved_versions||[];
    $('tbHistorySection').hidden=!rows.length;
    for(const version of rows) {
      const btn=document.createElement('button');
      btn.type='button';btn.className='tb-history-entry';
      const title=document.createElement('strong');
      title.textContent='Версия '+version.pipeline_version;
      const text=document.createElement('span');
      text.textContent=new Date(version.approved_at).toLocaleString('ru-RU')+
        ' · '+version.config_sha256.slice(0,9)+'…';
      const action=document.createElement('b');action.textContent='Создать черновик ↗';
      btn.append(title,text,action);
      btn.addEventListener('click',()=>{
        if(busy||!loaded)return;
        if(dirty&&!confirm('Текущие несохранённые изменения будут заменены. Продолжить?'))return;
        config=editableConfig(version.config);selected=0;dirty=true;
        report('', 'Скопирована версия '+version.pipeline_version+
          '. Сохрани новый черновик, чтобы утвердить её повторно.');
        render();
      });
      root.append(btn);
    }
  }
  async function load(id) {
    projectId=id;const ticket=++epoch;
    loaded=false;busy=false;refreshBusy=false;config=null;state=null;dirty=false;
    $('tbStudioLayout').hidden=true;$('tbStudioBottom').hidden=true;
    $('tbHistorySection').hidden=true;$('tbApprovedNext').hidden=true;
    $('projectPipelineState').textContent='Получаем подтверждённую версию анкеты и каталог моделей…';
    report();
    try {
      const [pipeline,catalog]=await Promise.all([
        api('/api/projects/'+encodeURIComponent(id)+'/taxonomy-builder/pipeline'),
        api('/api/projects/'+encodeURIComponent(id)+'/taxonomy-builder/models')
      ]);
      if(ticket!==epoch)return;
      state=pipeline;models=catalog.models||[];
      config=pipeline.draft?editableConfig(pipeline.draft.config):freshConfig();
      draftRevision=pipeline.draft?.draft_revision||0;
      dirty=false;selected=0;loaded=true;
      $('tbStudioProjectName').textContent='Pipeline проекта';
      $('tbStudioRevision').textContent='Анкета v'+pipeline.profile_revision+' · подтверждена';
      $('projectPipelineState').textContent='Редактор готов · '+models.filter(x=>x.selectable).length+
        ' из '+models.length+' моделей подтверждены';
      if(!models.some(m=>m.selectable))
        report('Нет доступных подтверждённых моделей. Нажми «Обновить каталог моделей». Если модели недоступны у провайдера, выбрать их пока нельзя.');
      else report();
      render();history();
    }catch(ex){
      if(ticket!==epoch)return;
      $('projectPipelineState').textContent='Редактор не загрузился';
      report(ex.message+' Вернись к проверке анкеты или открой Studio заново.');
    }
  }
  async function refresh() {
    if(busy||refreshBusy)return;
    refreshBusy=true;actions();report('', 'Запрашиваем публичные метаданные OpenRouter. Платные LLM-вызовы не выполняются.');
    const id=projectId,ticket=epoch;
    try {
      const refreshed=await api('/api/taxonomy-builder/models/refresh',{});
      const list=await api(base()+'/models');
      if(ticket!==epoch||projectId!==id)return;
      models=list.models;
      report('', 'Каталог обновлён: '+refreshed.verified+' из '+refreshed.total+
        ' моделей найдены и проверены. Недоступные варианты по-прежнему заблокированы.');
      // Never silently upgrade pinned IDs: changing a catalog revision requires
      // explicit model reselection and new human approval.
      render();
    }catch(ex){if(ticket===epoch)report('Каталог не обновлён: '+ex.message);}
    finally {if(ticket===epoch){refreshBusy=false;render();}}
  }
  async function save() {
    if(busy||!dirty||getInvalidity())return;
    setBusy(true);report();
    const id=projectId,ticket=epoch;
    try {
      const saved=await api(base()+'/pipeline/draft',{
        expected_draft_revision:draftRevision,config
      });
      if(ticket!==epoch||projectId!==id)return;
      state=await api(base()+'/pipeline');
      if(ticket!==epoch)return;
      config=editableConfig(state.draft.config);
      draftRevision=saved.draft_revision;dirty=false;
      report('', 'Черновик v'+draftRevision+' сохранён. Нажми «Утвердить Pipeline», чтобы зафиксировать эту конфигурацию.');
      history();
    }catch(ex){
      if(ticket===epoch)report(ex.message+
        (ex.status===409?' Сервер сохранил другую версию. Необходимо перезагрузить Studio; текущие данные не были отправлены.':''));
    }finally {if(ticket===epoch){setBusy(false);render();}}
  }
  async function approve() {
    if(busy||dirty||!draftRevision||getInvalidity()||state?.status==='pipeline_approved')return;
    setBusy(true);report();
    const id=projectId,ticket=epoch;
    try {
      const approved=await api(base()+'/pipeline/approve',{expected_draft_revision:draftRevision});
      if(ticket!==epoch||projectId!==id)return;
      state=await api(base()+'/pipeline');
      if(ticket!==epoch)return;
      report('', 'Pipeline v'+approved.pipeline_version+' утверждён. Следующий шаг — расчёт стоимости в блоке 5; платные запросы ещё недоступны.');
      history();
    }catch(ex){
      if(ticket===epoch)report(ex.message+
        (ex.status===409?' Анкета или настройки изменились — проверь состояние проекта.':''));
    }finally {if(ticket===epoch){setBusy(false);render();}}
  }
  function confirmLeave() {
    return !dirty||confirm('Изменения Pipeline ещё не сохранены. Покинуть редактор?');
  }
  window.ContentMachineTaxonomyStudio={confirmLeave};
  window.addEventListener?.('beforeunload',event=>{
    if(dirty){event.preventDefault();event.returnValue='';}
  });
  document.addEventListener('cm-taxonomy-pipeline-open',event=>{
    if(!event.detail?.projectId)return;
    load(event.detail.projectId);
  });
  $('tbRefreshCatalog').addEventListener('click',refresh);
  $('tbModelSearch').addEventListener('input',e=>fillModels(e.target.value));
  $('tbModelSelect').addEventListener('change',e=>useModel(e.target.value));
  $('tbReasoningMode').addEventListener('change',e=>changeReasoningMode(e.target.value));
  $('tbReasoningEffort').addEventListener('change',e=>updateStage(s=>{
    s.reasoning={mode:'effort',effort:e.target.value};
    if(!temperatureAllowed(s,model(s.model_id)?.capabilities)[0])s.temperature=null;
  }));
  $('tbReasoningBudget').addEventListener('change',e=>updateStage(s=>{
    s.reasoning={mode:'budget',budget_tokens:Number(e.target.value)};
    s.temperature=null;
  }));
  $('tbTemperatureEnable').addEventListener('change',e=>updateStage(s=>{
    s.temperature=e.target.checked?0.7:null;
  }));
  $('tbTemperature').addEventListener('change',e=>updateStage(s=>{s.temperature=Number(e.target.value);}));
  $('tbOutputTokens').addEventListener('change',e=>updateStage(s=>{
    s.max_output_tokens=Number(e.target.value);
  }));
  $('tbCriticEnabled').addEventListener('change',e=>{
    if(selected!==2)return;
    const before=clone(activeStage());
    config.stages[2]=e.target.checked
      ?(before.enabled?before:emptyStage(ROLE[2]))
      :{role:'taxonomy_critic',enabled:false};
    changed();
  });
  $('tbSaveDraft').addEventListener('click',save);
  $('tbApprove').addEventListener('click',approve);
  $('tbDiscardChanges').addEventListener('click',()=>{
    if(!dirty)return;
    if(!confirm('Сбросить несохранённые изменения Pipeline?'))return;
    config=state.draft?editableConfig(state.draft.config):freshConfig();
    dirty=false;report('', 'Несохранённые изменения отменены.');render();
  });
})();