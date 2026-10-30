'use strict';
/* Navigation never starts a run, approves a result, or calls a paid provider. */
(() => {
  const {stages,groups,route,projectId,errorText}=window.ContentMachineWorkspaceCore;
  const $=id=>document.getElementById(id);
  let projects=[],selected='',current=null,tab='overview',opening=false,epoch=0;
  let acceptedHash='#pipeline',listEpoch=0;
  let contextStage=null;
  const getStage=id=>stages.find(s=>s.id===id);
  const project=()=>projects.find(p=>p.id===selected);
  function visit(hash){if(location.hash===hash)navigate();else location.hash=hash;}
  function remember(){try{localStorage.setItem('cm.workspace.project',selected);}catch{}}
  const mobile=window.matchMedia('(max-width:700px)');
  function closeMenu(){
    document.body.classList.remove('is-menu-open');$('workspaceMenu').setAttribute('aria-expanded','false');
    $('workspaceSidebar').inert=mobile.matches;
  }
  mobile.addEventListener('change',closeMenu);
  function link(href,text,cls='workspace-text-link') {
    const a=document.createElement('a');a.href=href;a.className=cls;a.textContent=text;return a;
  }
  function action(text,callback,cls='workspace-button workspace-button-dark') {
    const b=document.createElement('button');b.type='button';b.className=cls;b.textContent=text;
    b.addEventListener('click',callback);return b;
  }
  function chrome(){
    const work=window.ContentMachineApp.currentView()!=='workspace';
    $('workspaceLocation').textContent=current?.name||(work?'Инструменты':'Обзор');
    if(current)document.title=current.name+' — ContentMachine';
    document.querySelectorAll('.workspace-stage-link').forEach(a=>{
      const active=a.dataset.stage===current?.id;a.classList.toggle('is-active',active);
      if(active)a.setAttribute('aria-current','step');else a.removeAttribute('aria-current');
    });
    document.querySelector('.workspace-overview-link').classList.toggle('is-active',!current&&!work);
    $('workspaceContext').hidden=!work||!current;
    if(work&&current&&contextStage!==current.id){
      contextStage=current.id;
      const content=document.createElement('div');
      const n=document.createElement('span');n.className='workspace-stage-tag';n.textContent=current.number+' / 11';
      const title=document.createElement('strong');title.textContent=current.name;
      content.append(n,title,link('#stage/'+current.id,'Обзор этапа ↗'));
      const next=stages[current.index+1];
      $('workspaceContext').replaceChildren(content,next?link('#stage/'+next.id,'Следующий этап →'):link('#pipeline','К pipeline ↗'));
    }
  }
  function buildNavigation(){
    for(let g=0;g<groups.length;g++){
      const label=document.createElement('div');label.className='workspace-nav-label';label.textContent=groups[g].toUpperCase();
      $('workspaceStageNav').append(label);
      const section=document.createElement('div');section.className='workspace-pipeline-group';
      const heading=document.createElement('div');heading.className='workspace-pipeline-label';
      heading.innerHTML='<span>0'+(g+1)+'</span>'+groups[g];section.append(heading);
      for(const stage of stages.filter(s=>s.group===g)){
        const a=link('#stage/'+stage.id+'/work','','workspace-stage-link');a.dataset.stage=stage.id;
        a.innerHTML='<span class="workspace-nav-number">'+stage.number+'</span><span>'+stage.short+'</span><span class="workspace-nav-arrow" aria-hidden="true">↗</span>';
        $('workspaceStageNav').append(a);
        const row=document.createElement('div');row.className='workspace-pipeline-row';
        const name=link('#stage/'+stage.id,'','workspace-row-name');
        name.innerHTML='<span class="workspace-stage-number">'+stage.number+'</span><strong>'+stage.name+'</strong>';
        const task=document.createElement('span');task.className='workspace-row-task';task.textContent=stage.short;
        row.append(name,task,link('#stage/'+stage.id+'/work','Открыть →','workspace-button workspace-button-dark workspace-row-open'));
        section.append(row);
      }
      $('workspacePipelineRows').append(section);
    }
  }
  function renderProjects(){
    const picker=$('workspaceProject');picker.replaceChildren();
    const empty=document.createElement('option');empty.value='';empty.textContent='Выбери проект';picker.append(empty);
    for(const p of projects){const o=document.createElement('option');o.value=p.id;o.textContent=p.name||'Без названия';picker.append(o);}
    if(!projects.some(p=>p.id===selected))selected='';
    picker.value=selected;
    $('workspaceCurrentProject').textContent=project()?.name||'Выбери проект, чтобы открыть рабочий этап.';
    remember();
  }
  async function loadProjects(){
    const ticket=++listEpoch;
    try{
      const data=await read('/api/projects');if(ticket!==listEpoch)return;
      projects=(data.projects||[]).filter(p=>projectId(p.id));renderProjects();
      $('workspaceConnection').textContent='';$('workspaceConnection').hidden=true;
      $('workspaceConnection').classList.remove('is-error');$('workspaceRetry').hidden=true;
    }catch{
      if(ticket!==listEpoch)return;
      $('workspaceConnection').hidden=false;
      $('workspaceConnection').textContent='Не удалось загрузить проекты. Проверь подключение к ContentMachine и повтори попытку.';
      $('workspaceConnection').classList.add('is-error');$('workspaceRetry').hidden=false;
    }
  }
  async function read(url){
    const response=await fetch(url,{cache:'no-store'});
    const data=await response.json();if(!response.ok||data.ok===false||data.error)throw Error(errorText(data,response.status));return data;
  }
  function emptyState(host,title,copy,button){
    const box=document.createElement('div');box.className='workspace-empty';
    const h=document.createElement('h2');h.textContent=title;
    const p=document.createElement('p');p.textContent=copy;box.append(h,p);if(button)box.append(button);host.append(box);
  }
  function chooseProject(host){
    emptyState(host,'Начни с проекта','Выбери проект в верхнем меню или создай новый. Все этапы будут работать в контексте выбранного проекта.',action('Создать проект +',createProject));
  }
  function renderStage(stage,nextTab){
    const root=$('workspaceStage');root.replaceChildren();
    const header=document.createElement('div');header.className='workspace-stage-top';
    header.append(link('#pipeline','← Весь pipeline'));
    const position=document.createElement('span');position.className='workspace-stage-position';position.textContent=stage.number+' / 11 · '+groups[stage.group];header.append(position);root.append(header);
    const title=document.createElement('div');title.className='workspace-stage-heading';
    title.innerHTML='<h1 tabindex="-1">'+stage.name+'</h1><p>'+stage.description+'</p>';
    if(nextTab==='overview')title.append(link('#stage/'+stage.id+'/work','Открыть этап →','workspace-button workspace-button-dark'));
    root.append(title);
    const tabs=document.createElement('nav');tabs.className='workspace-stage-tabs';tabs.setAttribute('aria-label','Разделы этапа');
    for(const [id,name] of [['overview','Обзор'],['work','Рабочий экран'],['results','Результаты']]){
      const a=link('#stage/'+stage.id+(id==='overview'?'':'/'+id),name,'');
      if(id===nextTab)a.setAttribute('aria-current','page');tabs.append(a);
    }root.append(tabs);
    const host=document.createElement('div');root.append(host);
    if(nextTab==='overview'){
      const flow=document.createElement('div');flow.className='workspace-stage-flow';
      for(const [key,kicker,name] of [['input','01 / НА ВХОДЕ','Исходные данные'],['work','02 / В ПРОЦЕССЕ','Что делает этап'],['output','03 / НА ВЫХОДЕ','Результат']]){
        const card=document.createElement('article');card.className='workspace-flow-card';
        card.innerHTML='<h3>'+name+'</h3><ul>'+stage[key].map(v=>'<li>'+v+'</li>').join('')+'</ul>';flow.append(card);
      }host.append(flow);
    }else if(nextTab==='work'){
      if(!stage.surface){
        const ticket=epoch,pid=selected;
        window.ContentMachineWorkspaceAnalysis.mount(host,{stage,pid:project()?pid:'',isCurrent:()=>ticket===epoch&&pid===selected});
      }else if(!project())chooseProject(host);
      else {const status=document.createElement('p');status.setAttribute('role','status');status.textContent='Открываем '+stage.name+'…';host.append(status);}
    }else showResults(host,stage);
    const foot=document.createElement('div');foot.className='workspace-stage-foot';
    foot.append(stage.index?link('#stage/'+stages[stage.index-1].id,'← '+stages[stage.index-1].name):link('#pipeline','← Обзор pipeline'));
    foot.append(stage.index<stages.length-1?link('#stage/'+stages[stage.index+1].id,stages[stage.index+1].name+' →'):link('#pipeline','Весь pipeline ↗'));root.append(foot);
  }
  async function showResults(host,stage){
    const ticket=epoch,pid=selected;
    if(!project()){chooseProject(host);return;}
    const endpoint=stage.id==='foundation'?'/api/projects/'+pid:stage.id==='collector'?'/api/intelligence/collector/runs?project_id='+pid+'&limit=20':stage.id==='qdp'?'/api/projects/'+pid+'/qualified-data/runs?limit=20':null;
    if(!endpoint){
      emptyState(host,stage.surface?'Проверь результаты в рабочем экране':'Результаты пока недоступны',stage.surface?'Здесь пока нет общей ленты результатов. Открой рабочий экран, чтобы посмотреть сохранённые запуски и их состояние.':'Результаты этого этапа ещё не подключены к рабочему пространству. После подключения здесь появятся реальные данные проекта.',stage.surface?action(stage.action+' ↗',()=>openSurface(stage)):null);return;
    }
    const box=document.createElement('section');box.className='workspace-results';
    const h=document.createElement('h2');h.textContent='Сохранённые результаты';
    const status=document.createElement('p');status.className='workspace-results-status';status.setAttribute('role','status');status.textContent='Загружаем данные проекта…';box.append(h,status);host.append(box);
    try{
      const data=await read(endpoint);if(ticket!==epoch||pid!==selected)return;
      if(stage.id==='foundation'){
        const p=data.project;if(!p||p.id!==pid)throw Error('Проект недоступен');
        status.textContent='Профиль сохранён · ревизия '+(p.profile_revision||1);
        for(const [label,value] of [['Проект',p.name||'Без названия'],['Ниша',p.niche||'Не указана'],['Документы',String((p.files||[]).length)]]){
          const row=document.createElement('div');row.className='workspace-result-row';const labelNode=document.createElement('span');labelNode.textContent=label;const v=document.createElement('span');v.textContent=value;row.append(labelNode,v);box.append(row);
        }return;
      }
      const runs=data.runs||[];status.textContent=runs.length?'Загружено запусков: '+runs.length+' · состояние получено от сервера':'Сохранённых запусков пока нет.';
      for(const run of runs){
        const row=document.createElement('div');row.className='workspace-result-row';
        const id=document.createElement('code');id.textContent=String(run.run_id??run.id??'—');
        const state=document.createElement('span');state.className='workspace-result-state';state.textContent=String(run.state??run.status??'Состояние не указано');row.append(id,state);box.append(row);
      }
    }catch{
      if(ticket!==epoch||pid!==selected)return;
      status.textContent='Не удалось получить результаты. Проверь доступность данных и повтори попытку.';
      box.append(action('Обновить ↻',()=>{host.replaceChildren();showResults(host,stage);},'workspace-button'));
    }
  }
  async function openSurface(stage){
    if(!project()||opening)return;
    opening=true;const pid=selected,ticket=++epoch;
    try{
      let ok=true;
      if(stage.surface==='sre'){
        if(!window.ContentMachineApp.canLeave())return;
        location.href='/sre-studio.html?project='+pid;return;
      }
      if(stage.surface==='project'||stage.surface==='rtb'){
        ok=await window.ContentMachineProjects.open(pid,()=>ticket===epoch&&pid===selected);
        if(ticket!==epoch||pid!==selected)return;
        if(ok&&stage.surface==='rtb'){
          $('tbV2Panel').open=true;$('tbV2Panel').scrollIntoView({behavior:'smooth',block:'start'});
        }
      }else if(stage.surface==='search')ok=await window.ContentMachineProjects.search(pid,()=>ticket===epoch&&pid===selected);
      else if(stage.surface==='collector'){
        ok=window.ContentMachineApp.openView('discovery');
        if(ok){$('collectorV1Project').value=pid;$('collectorV1Project').dispatchEvent(new Event('input',{bubbles:true}));$('collectorV1').scrollIntoView({behavior:'smooth',block:'start'});}
      }else if(stage.surface==='qdp')ok=window.ContentMachineApp.openView('qdp');
      if(ok===false)return;
      current=stage;tab='work';history.replaceState(null,'','#stage/'+stage.id+'/work');acceptedHash=location.hash;
      document.title=stage.name+' — ContentMachine';chrome();
    }catch{
      const root=$('workspaceStage');
      emptyState(root,'Не удалось открыть рабочий экран','Проверь подключение и попробуй ещё раз.',action('Повторить ↻',()=>openSurface(stage)));
    }finally{opening=false;}
  }
  function navigate(){
    closeMenu();
    const tools=/^#tools\/(downloader|settings|discovery|qdp|projects)$/.exec(location.hash);
    if(tools){if(window.ContentMachineApp.openView(tools[1])){current=null;chrome();acceptedHash=location.hash;}else history.replaceState(null,'',acceptedHash);return;}
    if(!window.ContentMachineApp.openView('workspace')){history.replaceState(null,'',acceptedHash);return;}
    ++epoch;const next=route(location.hash);current=getStage(next.stage)||null;tab=next.tab;acceptedHash=location.hash||'#pipeline';
    $('workspaceOverview').hidden=!!current;$('workspaceStage').hidden=!current;
    if(current)renderStage(current,tab);
    document.title=(current?current.name:'Pipeline')+' — ContentMachine';chrome();
    window.scrollTo({top:0,behavior:'instant'});
    const heading=current?$('workspaceStage').querySelector('h1'):$('workspaceTitle');heading?.focus({preventScroll:true});
    if(current&&tab==='work'&&current.surface&&project())void openSurface(current);
  }
  function createProject(){
    if(window.ContentMachineProjects.create()===false)return;
    current=getStage('foundation');
    history.replaceState(null,'','#stage/foundation/work');acceptedHash=location.hash;chrome();closeMenu();
  }
  $('workspaceProject').addEventListener('change',()=>{
    const next=$('workspaceProject').value;
    if(!window.ContentMachineApp.openView('workspace')){$('workspaceProject').value=selected;return;}
    selected=projectId(next)?next:'';remember();renderProjects();
    visit(current?'#stage/'+current.id:'#pipeline');
  });
  $('workspaceMenu').addEventListener('click',()=>{const open=document.body.classList.toggle('is-menu-open');$('workspaceMenu').setAttribute('aria-expanded',String(open));$('workspaceSidebar').inert=mobile.matches&&!open;});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeMenu();$('workspaceMenu').focus();}});
  document.addEventListener('click',event=>{if(document.body.classList.contains('is-menu-open')&&!event.target.closest('.sidebar')&&!event.target.closest('#workspaceMenu'))closeMenu();});
  $('workspaceCreate').addEventListener('click',createProject);
  $('workspaceRetry').addEventListener('click',loadProjects);
  document.addEventListener('cm-project-list',event=>{
    ++listEpoch;projects=(event.detail?.projects||[]).filter(p=>projectId(p.id));renderProjects();
    $('workspaceConnection').textContent='';$('workspaceConnection').hidden=true;$('workspaceConnection').classList.remove('is-error');$('workspaceRetry').hidden=true;
  });
  document.addEventListener('cm-project-selected',event=>{
    const p=event.detail?.project;if(!projectId(p?.id))return;
    if(!projects.some(x=>x.id===p.id))projects.push(p);selected=p.id;renderProjects();
    if(!opening){current=getStage('foundation');history.replaceState(null,'','#stage/foundation/work');acceptedHash=location.hash;}chrome();
  });
  document.addEventListener('cm-view-change',event=>{
    if(event.detail.view!=='workspace'&&!opening){
      const expected=current&&((['project','rtb','search'].includes(current.surface)&&event.detail.view==='projects')||(current.surface==='collector'&&event.detail.view==='discovery')||(current.surface==='qdp'&&event.detail.view==='qdp'));
      if(!expected){current=null;history.replaceState(null,'','#tools/'+event.detail.view);acceptedHash=location.hash;}
    }chrome();
  });
  window.addEventListener('hashchange',navigate);
  window.ContentMachineWorkspace=Object.freeze({projectId:()=>selected});
  try{const saved=localStorage.getItem('cm.workspace.project');if(projectId(saved))selected=saved;}catch{}
  buildNavigation();
  // An explicit downloader URL remains usable from external links.
  if(!new URLSearchParams(location.search).has('url'))navigate();else chrome();
  loadProjects().then(()=>{
    if(current&&window.ContentMachineApp.currentView()==='workspace'){
      renderStage(current,tab);
      if(tab==='work'&&current.surface&&project())void openSurface(current);
    }
  });
})();
