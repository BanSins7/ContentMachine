'use strict';
/* Navigation never starts a run, approves a result, or calls a paid provider. */
(() => {
  const {stages,groups,route,projectId,errorText}=window.ContentMachineWorkspaceCore;
  const runs=window.ContentMachineWorkspaceRuns;
  const $=id=>document.getElementById(id);
  let projects=[],selected='',current=null,tab='overview',opening=false,epoch=0;
  let acceptedHash='#pipeline',listEpoch=0,runEpoch=0,summaries=[],summaryProject='',contextKey='';
  let nextAction=()=>{$('workspaceProject').focus();};
  const summaryRun=source=>source?.rows.find(r=>runs.runState(source.id,r).kind==='running')||source?.rows[0];
  const rowNodes=new Map(),getStage=id=>stages.find(s=>s.id===id),project=()=>projects.find(p=>p.id===selected);
  const mobile=window.matchMedia('(max-width:700px)');
  function visit(hash){if(location.hash===hash)navigate();else location.hash=hash;}
  function remember(){try{localStorage.setItem('cm.workspace.project',selected);}catch{}}
  function closeMenu(){document.body.classList.remove('is-menu-open');$('workspaceMenu').setAttribute('aria-expanded','false');$('workspaceSidebar').inert=mobile.matches;}
  mobile.addEventListener('change',closeMenu);
  function link(href,text,cls='workspace-text-link'){const a=document.createElement('a');a.href=href;a.className=cls;a.textContent=text;return a;}
  function action(text,callback,cls='workspace-button'){const b=document.createElement('button');b.type='button';b.className=cls;b.textContent=text;b.addEventListener('click',callback);return b;}
  function text(tag,value,cls){const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n;}
  function chrome(){
    const view=window.ContentMachineApp.currentView(),work=view!=='workspace';
    const inHistory=location.hash==='#history';
    $('workspaceLocation').textContent=current?.name||(view==='projects'?'Проекты':inHistory?'История запусков':work?'Инструменты':'Обзор');
    if(current)document.title=current.name+' — ContentMachine';
    document.querySelectorAll('.workspace-stage-link').forEach(a=>{
      const active=a.dataset.stage===current?.id;a.classList.toggle('is-active',active);
      if(active)a.setAttribute('aria-current','step');else a.removeAttribute('aria-current');
    });
    for(const [selector,active] of [['.workspace-overview-link[href="#pipeline"]',!current&&!work&&!inHistory],['#workspaceHistoryNav',inHistory&&!work],['#workspaceLibraryNav',view==='projects'&&!current]]){
      const a=document.querySelector(selector);a.classList.toggle('is-active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
    }
    $('workspaceContext').hidden=!work||!current;
    const key=current?.id+'|'+selected;
    if(work&&current&&contextKey!==key){
      contextKey=key;const content=document.createElement('div');
      content.append(text('span',current.number+' / 11','workspace-stage-tag'),text('strong',current.name));
      const nav=document.createElement('nav');nav.setAttribute('aria-label','Навигация модуля');
      nav.append(link('#stage/'+current.id+'/results','Результаты'),link('#history','История'),link('#pipeline','Все этапы'));
      $('workspaceContext').replaceChildren(content,nav);
    }
  }
  function buildNavigation(){
    for(let g=0;g<groups.length;g++){
      $('workspaceStageNav').append(text('div',groups[g],'workspace-nav-label'));
      const section=text('div','','workspace-pipeline-group');section.append(text('div',groups[g],'workspace-pipeline-label'));
      for(const stage of stages.filter(s=>s.group===g)){
        const a=link('#stage/'+stage.id+'/work','','workspace-stage-link');a.dataset.stage=stage.id;
        a.append(text('span',stage.number,'workspace-nav-number'),text('span',stage.short));$('workspaceStageNav').append(a);
        const row=text('div','','workspace-pipeline-row');row.dataset.stage=stage.id;
        const name=link('#stage/'+stage.id+'/work','','workspace-row-name');
        name.append(text('span',stage.number,'workspace-stage-number'),text('strong',stage.name));
        const state=text('div','','workspace-row-state'),result=text('div','','workspace-row-task');
        row.append(name,state,result,link('#stage/'+stage.id+'/results','Результаты →','workspace-row-open'));
        rowNodes.set(stage.id,{row,state,result});section.append(row);
      }$('workspacePipelineRows').append(section);
    }paintSummary();
  }
  function paintSummary(){
    for(const stage of stages){
      const n=rowNodes.get(stage.id);if(!n)continue;
      const source=summaryProject===selected?summaries.find(s=>s.id===stage.id):null;
      let state={kind:'unknown',label:project()?'Доступ в модуле':'Нет проекта'},result=stage.short;
      if(stage.id==='foundation'&&project()){state={kind:'complete',label:'Профиль сохранён'};result='Ревизия '+(project().profile_revision||1);}
      else if(!stage.surface){state={kind:'unknown',label:'API не подключён'};result='Просмотр при подключении backend';}
      else if(source){
        state=source.error?{kind:'unknown',label:'Данные недоступны'}:runs.runState(stage.id,summaryRun(source));
        result=source.error?'Проверьте подключение в модуле':summaryRun(source)?runs.resultSummary(stage.id,summaryRun(source)):'Нет сохранённых запусков';
        if(stage.id==='rtb'&&source.rows[0])result='RTB · '+result;
      }else if(project()&&['rtb','qdp','collector'].includes(stage.id)){state={kind:'unknown',label:'Загрузка…'};result='Читаем сохранённые запуски';}
      n.state.replaceChildren(text('span',state.label,'cm-state cm-state-'+state.kind));n.result.textContent=result;
      n.row.dataset.state=state.kind;
    }
    let title='Выберите проект',hint='Состояния и результаты появятся после выбора проекта.',label='Выбрать проект';
    nextAction=()=>{$('workspaceProject').focus();};
    if(project()){
      const rtb=summaries.find(s=>s.id==='rtb'),latest=summaryProject===selected?summaryRun(rtb):null;
      const state=latest?runs.runState('rtb',latest):null;
      title=state?.kind==='review'?'Результат ожидает проверки':state?.kind==='running'?'Исследование выполняется':latest?'Продолжите исследование':'Подготовьте исследование';
      hint=latest?'Research Taxonomy · '+(latest.deep_status||latest.status)+' · '+runs.resultSummary('rtb',latest):'Начните с профиля, настроек моделей и отдельного разрешения бюджета.';
      if(state?.kind==='error'){title='Запуск требует внимания';hint='Откройте сохранённые результаты и диагностику. Новый запуск требует отдельной сметы.';}
      if(rtb?.error&&summaryProject===selected){title='Состояние RTB недоступно';hint='Проверьте подключение модуля. Сохранённый профиль остаётся доступен.';}
      label=latest?'Открыть запуск':'Открыть Research Taxonomy';
      nextAction=()=>latest?visit('#stage/rtb/results?run='+encodeURIComponent(runs.idOf(latest))):visit('#stage/rtb/work');
    }else if(!projects.length){title='Создайте первый проект';hint='Анкета, источники и результаты сохраняются в проекте.';label='Создать проект';nextAction=createProject;}
    $('workspaceNextTitle').textContent=title;$('workspaceNextHint').textContent=hint;$('workspaceNextAction').textContent=label;
  }
  async function refreshSummary(){
    const pid=selected,ticket=++runEpoch;if(!project()){summaries=[];summaryProject='';paintSummary();return;}
    $('workspaceRefresh').disabled=true;
    const data=await runs.load(pid);if(ticket!==runEpoch||pid!==selected)return;
    summaries=data;summaryProject=pid;paintSummary();$('workspaceRefresh').disabled=false;
    const unavailable=data.filter(s=>s.error);
    $('workspaceConnection').hidden=false;
    $('workspaceConnection').textContent=unavailable.length?'История недоступна: '+unavailable.map(s=>s.name).join(', ')+'. Локальный профиль сохранён.':'Состояния обновлены · '+new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
    $('workspaceRetry').hidden=!unavailable.length;
  }
  function renderProjects(){
    const picker=$('workspaceProject');picker.replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent='Выберите проект';picker.append(empty);
    for(const p of projects){const o=document.createElement('option');o.value=p.id;o.textContent=p.name||'Без названия';picker.append(o);}
    if(!projects.some(p=>p.id===selected))selected='';picker.value=selected;
    $('workspaceTitle').textContent=project()?.name||'Pipeline';$('workspaceCurrentProject').textContent=project()?'Pipeline · 11 модулей · запуск по этапам':'Выберите проект или создайте новый.';
    remember();paintSummary();
  }
  async function loadProjects(){
    const ticket=++listEpoch;
    try{
      const data=await runs.read('/api/projects');if(ticket!==listEpoch)return;
      projects=(data.projects||[]).filter(p=>projectId(p.id));renderProjects();void refreshSummary();
    }catch(error){
      if(ticket!==listEpoch)return;$('workspaceConnection').hidden=false;$('workspaceConnection').textContent='Проекты недоступны. '+error.message;$('workspaceRetry').hidden=false;
    }
  }
  function emptyState(host,title,copy,control){const box=text('div','','workspace-empty');box.append(text('h2',title),text('p',copy));if(control)box.append(control);host.append(box);}
  function chooseProject(host){emptyState(host,'Выберите проект','Выберите сохранённый проект в верхнем меню или откройте библиотеку.',action('Открыть проекты',()=>visit('#projects')));}
  function renderStage(stage,nextTab){
    const root=$('workspaceStage');root.replaceChildren();
    const header=text('div','','workspace-stage-top');header.append(link('#pipeline','← Pipeline'),text('span',stage.number+' / 11 · '+groups[stage.group],'workspace-stage-position'));root.append(header);
    const title=text('div','','workspace-stage-heading');const h=text('h1',stage.name);h.tabIndex=-1;title.append(h,text('p',stage.short+' · '+(project()?.name||'Проект не выбран')));
    if(nextTab==='overview')title.append(link('#stage/'+stage.id+'/work','Открыть модуль','workspace-button workspace-button-dark'));root.append(title);
    const tabs=text('nav','','workspace-stage-tabs');tabs.setAttribute('aria-label','Разделы модуля');
    for(const [id,name] of [['overview','Обзор'],['work','Рабочий экран'],['results','Результаты']]){const a=link('#stage/'+stage.id+(id==='overview'?'':'/'+id),name,'');if(id===nextTab)a.setAttribute('aria-current','page');tabs.append(a);}root.append(tabs);
    const host=document.createElement('div');root.append(host);
    if(nextTab==='overview'){
      const flow=text('div','','workspace-stage-flow');for(const [key,name] of [['input','Вход'],['work','Операции'],['output','Выход']]){
        const row=text('article','','workspace-flow-card');const ul=document.createElement('ul');stage[key].forEach(v=>ul.append(text('li',v)));row.append(text('h3',name),ul);flow.append(row);
      }host.append(flow);
    }else if(!project())chooseProject(host);
    else if(nextTab==='results')showResults(host,stage);
    else if(!stage.surface){const ticket=epoch,pid=selected;window.ContentMachineWorkspaceAnalysis.mount(host,{stage,pid,isCurrent:()=>ticket===epoch&&pid===selected});}
    else host.append(text('p','Открываем модуль…','cm-subtle'));
  }
  function showResults(host,stage){
    const ticket=epoch,pid=selected,isCurrent=()=>ticket===epoch&&pid===selected;
    if(['rtb','collector','qdp'].includes(stage.id)){
      const params=new URLSearchParams(location.hash.split('?')[1]||'');
      void runs.mountInspector(host,{pid,stage:stage.id,id:params.get('run'),deepV2:params.get('mode')==='deep-v2',legacyDeep:params.get('deep')==='1',isCurrent,onWork:(id,rid)=>void openSurface(getStage(id),rid)});return;
    }
    if(stage.id==='foundation'){
      host.append(text('p','Читаем сохранённый профиль…','cm-subtle'));
      runs.read('/api/projects/'+pid,pid).then(data=>{
        if(!isCurrent())return;host.replaceChildren();const p=data.project;if(p?.id!==pid)throw Error('Проект не совпадает');
        const dl=text('dl','','cm-profile-summary');for(const [label,value] of [['Проект',p.name],['Ниша',p.niche],['Цель',p.goal],['Ревизия',p.profile_revision||1]]){const row=document.createElement('div');row.append(text('dt',label),text('dd',value||'Не указано'));dl.append(row);}host.append(dl,action('Открыть профиль и источники',()=>openSurface(stage)));
      }).catch(error=>{if(isCurrent()){host.replaceChildren();emptyState(host,'Профиль недоступен',error.message,action('Повторить',()=>{host.replaceChildren();showResults(host,stage);}));}});return;
    }
    if(!stage.surface){window.ContentMachineWorkspaceAnalysis.mount(host,{stage,pid,isCurrent});return;}
    emptyState(host,'Результаты доступны в модуле','Для доступа к истории '+stage.name+' требуется подключение в его рабочем экране.',action('Открыть '+stage.name,()=>openSurface(stage)));
  }
  async function openSurface(stage,rid){
    if(!project()||opening||!stage)return;
    if(!window.ContentMachineApp.canLeave())return;
    opening=true;const pid=selected,ticket=++epoch;current=stage;
    try{
      let ok=true;
      if(stage.surface==='sre'){location.href='/sre-studio.html?project='+pid;return;}
      if(stage.surface==='project')ok=await window.ContentMachineProjects.open(pid,()=>ticket===epoch&&pid===selected);
      else if(stage.surface==='rtb'){
        const latest=summaryProject===pid?summaryRun(summaries.find(s=>s.id==='rtb')):null;
        const id=rid||latest?.run_id;
        ok=id?await window.ContentMachineProjects.run(pid,id,()=>ticket===epoch&&pid===selected):await window.ContentMachineProjects.review(pid,()=>ticket===epoch&&pid===selected);
      }else if(stage.surface==='search')ok=await window.ContentMachineProjects.search(pid,()=>ticket===epoch&&pid===selected);
      else if(stage.surface==='collector'){
        ok=window.ContentMachineApp.openView('discovery');if(ok){$('collectorV1Project').value=pid;$('collectorV1Project').dispatchEvent(new Event('input',{bubbles:true}));$('collectorV1').scrollIntoView({block:'start'});}
      }else if(stage.surface==='qdp')ok=window.ContentMachineApp.openView('qdp');
      if(ticket!==epoch||pid!==selected||ok===false)return false;
      current=stage;tab='work';history.replaceState(null,'','#stage/'+stage.id+'/work');acceptedHash=location.hash;chrome();return true;
    }catch(error){
      if(ticket!==epoch||pid!==selected)return false;
      window.ContentMachineApp.openView('workspace');$('workspaceOverview').hidden=true;$('workspaceStage').hidden=false;renderStage(stage,'overview');
      emptyState($('workspaceStage'),'Модуль недоступен',error.message,action('Повторить',()=>openSurface(stage,rid)));return false;
    }finally{opening=false;}
  }
  function navigate(){
    closeMenu();
    if(opening){history.replaceState(null,'',acceptedHash);return;}
    const hash=location.hash;
    if(hash==='#projects'||hash==='#tools/projects'){
      if(window.ContentMachineProjects.library()===false){history.replaceState(null,'',acceptedHash);return;}
      current=null;acceptedHash=hash;chrome();return;
    }
    const tools=/^#tools\/(downloader|settings|discovery|qdp|test-lab)$/.exec(hash);
    if(tools){if(window.ContentMachineApp.openView(tools[1])){current=null;chrome();acceptedHash=hash;}else history.replaceState(null,'',acceptedHash);return;}
    if(!window.ContentMachineApp.openView('workspace')){history.replaceState(null,'',acceptedHash);return;}
    ++epoch;const next=route(hash);current=getStage(next.stage)||null;tab=next.tab;acceptedHash=hash||'#pipeline';
    const isHistory=hash==='#history';$('workspaceOverview').hidden=!!current||isHistory;$('workspaceStage').hidden=!current;$('workspaceHistory').hidden=!isHistory;
    if(current)renderStage(current,tab);
    if(isHistory){
      $('workspaceHistoryProject').textContent=project()?.name||'Проект не выбран';const host=$('workspaceHistoryBody');host.replaceChildren();
      if(!project())chooseProject(host);else{const ticket=epoch,pid=selected;void runs.mountHistory(host,{pid,isCurrent:()=>ticket===epoch&&pid===selected});}
    }
    document.title=(current?current.name:isHistory?'История запусков':'Pipeline')+' — ContentMachine';chrome();window.scrollTo({top:0,behavior:'instant'});
    (current?$('workspaceStage').querySelector('h1'):isHistory?$('workspaceHistory').querySelector('h1'):$('workspaceTitle'))?.focus({preventScroll:true});
    if(current&&tab==='work'&&current.surface&&project())void openSurface(current);
  }
  function createProject(){if(window.ContentMachineProjects.create()===false)return;current=getStage('foundation');history.replaceState(null,'','#stage/foundation/work');acceptedHash=location.hash;chrome();closeMenu();}
  $('workspaceProject').addEventListener('change',()=>{
    const next=$('workspaceProject').value;if(opening){$('workspaceProject').value=selected;return;}
    if(!window.ContentMachineApp.openView('workspace')){$('workspaceProject').value=selected;return;}
    selected=projectId(next)?next:'';++epoch;++runEpoch;summaries=[];summaryProject='';$('workspaceRefresh').disabled=false;renderProjects();void refreshSummary();
    visit(location.hash==='#history'?'#history':current?'#stage/'+current.id:'#pipeline');
  });
  $('workspaceMenu').addEventListener('click',()=>{const open=document.body.classList.toggle('is-menu-open');$('workspaceMenu').setAttribute('aria-expanded',String(open));$('workspaceSidebar').inert=mobile.matches&&!open;});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&document.body.classList.contains('is-menu-open')){closeMenu();$('workspaceMenu').focus();}});
  document.addEventListener('click',event=>{if(document.body.classList.contains('is-menu-open')&&!event.target.closest('.sidebar')&&!event.target.closest('#workspaceMenu'))closeMenu();});
  $('workspaceCreate').addEventListener('click',createProject);$('workspaceNextAction').addEventListener('click',()=>nextAction());
  $('workspaceRefresh').addEventListener('click',refreshSummary);$('workspaceRetry').addEventListener('click',loadProjects);
  document.addEventListener('cm-project-list',event=>{++listEpoch;projects=(event.detail?.projects||[]).filter(p=>projectId(p.id));renderProjects();void refreshSummary();});
  document.addEventListener('cm-project-selected',event=>{
    const p=event.detail?.project;if(!projectId(p?.id))return;const index=projects.findIndex(x=>x.id===p.id);if(index<0)projects.push(p);else projects[index]=p;
    selected=p.id;renderProjects();void refreshSummary();if(!opening){current=getStage('foundation');history.replaceState(null,'','#stage/foundation/work');acceptedHash=location.hash;}chrome();
  });
  document.addEventListener('cm-project-section',event=>{
    const section=event.detail?.section;
    if(section==='overview')current=null;
    else if(section==='wizard'){
      current=getStage('foundation');selected=projectId(event.detail.projectId)?event.detail.projectId:'';renderProjects();
    }else if(['review','pipeline','cost','run'].includes(section))current=getStage('rtb');
    else if(section==='searchPlanner')current=getStage('search');
    else if(section==='details')current=getStage('foundation');
    else return;
    history.replaceState(null,'',current?'#stage/'+current.id+'/work':'#projects');acceptedHash=location.hash;chrome();
  });
  document.addEventListener('cm-view-change',event=>{
    if(event.detail.view!=='workspace'&&!opening){
      const expected=current&&((['project','rtb','search'].includes(current.surface)&&event.detail.view==='projects')||(current.surface==='collector'&&event.detail.view==='discovery')||(current.surface==='qdp'&&event.detail.view==='qdp'));
      if(!expected){current=null;history.replaceState(null,'',event.detail.view==='projects'?'#projects':'#tools/'+event.detail.view);acceptedHash=location.hash;}
    }chrome();
  });
  window.addEventListener('hashchange',navigate);
  function openStage(pid,stageId){const stage=getStage(stageId);if(!stage||!projectId(pid)||!projects.some(p=>p.id===pid)||!window.ContentMachineApp.openView('workspace'))return false;
    selected=pid;renderProjects();void refreshSummary();visit('#stage/'+stage.id+(stage.surface?'/work':''));return true;}
  function openProject(pid){
    if(!projectId(pid)||!projects.some(p=>p.id===pid)||!window.ContentMachineApp.openView('workspace'))return false;
    selected=pid;renderProjects();void refreshSummary();visit('#pipeline');return true;
  }
  window.ContentMachineWorkspace=Object.freeze({projectId:()=>selected,openStage,openProject});
  try{const saved=localStorage.getItem('cm.workspace.project');if(projectId(saved))selected=saved;}catch{}
  buildNavigation();if(!new URLSearchParams(location.search).has('url'))navigate();else chrome();
  loadProjects().then(()=>{if(window.ContentMachineApp.currentView()==='workspace')navigate();});
  // Refresh only while an observed run is active; never reconstruct an editor.
  setInterval(()=>{if(!document.hidden&&window.ContentMachineApp.currentView()==='workspace'&&!current&&location.hash!=='#history'&&summaries.some(s=>s.rows.some(r=>runs.runState(s.id,r).kind==='running')))void refreshSummary();},6000);
})();
