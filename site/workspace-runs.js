'use strict';
/* Project-scoped read adapters. This surface has no mutation transport. */
(() => {
  const {projectId,errorText,runState,roles,roleState}=window.ContentMachineWorkspaceCore;
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
  const runId=(stage,value)=>stage==='collector'?/^[1-9][0-9]*$/.test(String(value)):uuid(value);
  const sources=[
    {id:'rtb',name:'Research Taxonomy',limit:30,path:pid=>'/api/projects/'+pid+'/taxonomy-builder/runs'},
    {id:'collector',name:'Video Collector',limit:20,path:pid=>'/api/intelligence/collector/runs?project_id='+pid+'&limit=20'},
    {id:'qdp',name:'Qualified Data',limit:25,path:pid=>'/api/projects/'+pid+'/qualified-data/runs?limit=25'}
  ];
  function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;}
  function button(text,fn,primary=false){const b=node('button',text,'workspace-button'+(primary?' workspace-button-dark':''));b.type='button';b.addEventListener('click',fn);return b;}
  function anchor(href,text,cls='workspace-text-link'){const a=node('a',text,cls);a.href=href;return a;}
  function status(stage,row){const s=runState(stage,row);return node('span',s.label,'cm-state cm-state-'+s.kind);}
  function date(value){const d=new Date(value);return value&&!Number.isNaN(d.valueOf())?d.toLocaleString('ru-RU',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):'Дата не передана';}
  function idOf(row){return String(row.run_id??row.id??'');}
  function resultSummary(stage,row){
    if(stage==='rtb')return [row.verified_fact_count==null?null:row.verified_fact_count+' фактов',row.valid_topic_count==null?null:row.valid_topic_count+' тем',row.published?'Опубликовано':null].filter(Boolean).join(' · ')||'Артефакты в инспекторе';
    if(stage==='collector')return row.d1_saved_videos==null?'Результаты сбора':row.d1_saved_videos+' сохранённых видео';
    return row.total_qualified==null?'Qualified Set':row.total_qualified+' qualified'+(row.total_review?' · '+row.total_review+' на проверке':'');
  }
  async function read(path,pid){
    const reply=await fetch(path,{cache:'no-store'});let data;
    try{data=await reply.json();}catch{throw Error('Сервер вернул нечитаемый ответ');}
    if(!reply.ok||data.ok===false||data.error)throw Error(errorText(data,reply.status));
    if(data.project_id&&data.project_id!==pid)throw Error('Ответ относится к другому проекту');
    return data;
  }
  async function load(pid){
    if(!projectId(pid))throw Error('Выберите сохранённый проект');
    return Promise.all(sources.map(async source=>{
      try{
        const data=await read(source.path(pid),pid);
        if(!Array.isArray(data.runs)||data.runs.some(r=>!r||!runId(source.id,idOf(r))||(r.project_id&&r.project_id!==pid)))throw Error('Некорректная история запусков');
        return {...source,rows:data.runs,more:!!(data.has_more||data.next_cursor||data.runs.length>=source.limit)};
      }catch(error){return {...source,rows:[],error:error.message};}
    }));
  }
  function jsonPanel(title,value,open=false){
    const details=node('details',undefined,'cm-json-disclosure');details.open=open;
    details.append(node('summary',title),node('pre',JSON.stringify(value,null,2),'cm-code'));return details;
  }
  function download(filename,value){
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const a=anchor(url,'');a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function exportButton(filename,value){return button('Скачать JSON',()=>download(filename,value));}
  function renderArtifacts(host,output){
    const value=output.normalized||{};
    const items=value.facts||value.topics||value.findings;
    if(!Array.isArray(items)){host.append(jsonPanel('Сохранённый результат',value,true));return;}
    host.append(node('p','Записей: '+items.length,'cm-subtle'));
    if(!items.length){host.append(node('p','Модель вернула пустой список.','cm-empty-inline'));return;}
    const list=node('div',undefined,'cm-artifact-list');
    items.forEach((item,index)=>{
      const entry=node('article',undefined,'cm-artifact');
      const title=node('h3',item.name||item.topic_name||item.text||'Запись '+(index+1));
      entry.append(node('span',String(index+1).padStart(2,'0'),'cm-artifact-index'),title);
      for(const [key,label] of [['kind','Тип'],['definition','Определение'],['include','Включать'],['exclude','Исключать'],['source_quote','Исходная цитата'],['issue','Замечание'],['suggestion','Рекомендация'],['severity','Важность']]){
        if(item[key]){const p=node('p');p.append(node('strong',label+': '),node('span',item[key]));entry.append(p);}
      }
      for(const [key,label] of [['positive_examples','Подходящие примеры'],['negative_examples','Неподходящие примеры']]){
        if(Array.isArray(item[key])&&item[key].length)entry.append(node('p',label+': '+item[key].join('; ')));
      }
      list.append(entry);
    });host.append(list,jsonPanel('Все структурированные поля',value));
  }
  function inspectorLink(stage,id,deep=false){return '#stage/'+stage+'/results?run='+encodeURIComponent(id)+(deep?'&deep=1':'');}
  async function mountHistory(host,{pid,isCurrent,onlyStage}){
    const toolbar=node('div',undefined,'cm-list-toolbar');
    const label=node('label','Модуль','cm-filter');const select=node('select');select.setAttribute('aria-label','Фильтр истории по модулю');
    for(const source of [{id:'',name:'Все доступные модули'},...sources]){const o=node('option',source.name);o.value=source.id;select.append(o);}
    select.value=onlyStage||'';label.append(select);toolbar.append(label);
    const refresh=button('Обновить',()=>loadPage());toolbar.append(refresh);host.append(toolbar);
    const notice=node('p','Читаем историю проекта…','cm-subtle');notice.setAttribute('role','status');
    const list=node('div',undefined,'cm-history');host.append(notice,list);
    let ticket=0,data=[];
    function paint(){
      list.replaceChildren();
      for(const source of data.filter(s=>!select.value||s.id===select.value)){
        const section=node('section',undefined,'cm-history-section');section.append(node('h2',source.name));
        if(source.error){
          const message=/PostgreSQL.*(?:enabled|configured)/i.test(source.error)?'База данных не подключена. Сохранённый профиль доступен.':source.error;
          section.append(node('p','История недоступна. '+message,'cm-inline-error'),anchor('#stage/'+source.id+'/work','Открыть модуль'));list.append(section);continue;
        }
        if(!source.rows.length)section.append(node('p','Сохранённых запусков нет.','cm-empty-inline'));
        else{
          const table=node('table',undefined,'cm-run-table');table.append(node('caption','Сохранённые запуски '+source.name,'sr-only'));
          const head=node('thead');const tr=node('tr');for(const v of ['Запуск','Состояние','Результат',''])tr.append(node('th',v));head.append(tr);table.append(head);
          const body=node('tbody');
          for(const row of source.rows){
            const tr=node('tr');const title=node('td');title.append(anchor(inspectorLink(source.id,idOf(row),!!row.deep_status),date(row.created_at)),node('code',idOf(row),'cm-run-id'));
            const st=node('td');st.append(status(source.id,row));const result=node('td',resultSummary(source.id,row));const open=node('td');open.append(anchor(inspectorLink(source.id,idOf(row),!!row.deep_status),'Открыть →'));
            tr.append(title,st,result,open);body.append(tr);
          }table.append(body);const scroll=node('div',undefined,'cm-table-scroll');scroll.append(table);section.append(scroll);
        }
        if(source.more)section.append(anchor('#stage/'+source.id+'/work','Показана последняя страница ('+source.limit+'). Полная история в модуле →'));
        list.append(section);
      }
    }
    async function loadPage(){
      const t=++ticket;refresh.disabled=true;notice.textContent='Читаем историю проекта…';
      data=await load(pid);if(t!==ticket||!isCurrent())return;
      notice.textContent='Сохранённые запуски · '+data.filter(s=>!s.error).length+' из '+data.length+' источников доступны. Просмотр не запускает модели.';
      refresh.disabled=false;paint();
    }
    select.addEventListener('change',paint);await loadPage();
    if(!isCurrent())return;
    const access=node('details',undefined,'cm-json-disclosure');access.append(node('summary','Другие истории и Deep V2'));
    access.append(node('p','Search Planner и SRE требуют подключения в своём модуле. У Deep V2 нет API общего списка запусков.'));
    access.append(anchor('#stage/search/work','Search Planner →'),anchor('#stage/sre/work','SRE Studio →'),anchor('#stage/rtb/results?mode=deep-v2','Открыть Deep V2 по ID →'));
    host.append(access);
  }
  async function mountInspector(host,{pid,stage,id,isCurrent,onWork,deepV2=false,legacyDeep=false}){
    if(!projectId(pid))return;
    if(!runId(stage,id)){
      if(deepV2){mountDeepPicker(host,{pid,isCurrent,onWork});return;}
      await mountHistory(host,{pid,isCurrent,onlyStage:stage});return;
    }
    const loading=node('p','Загружаем сохранённый запуск…','cm-subtle');loading.setAttribute('role','status');host.append(loading);
    try{
      let data;
      if(stage==='rtb')data=await read('/api/projects/'+pid+'/taxonomy-builder/runs/'+id,pid);
      else if(stage==='collector'){
        const base='/api/intelligence/collector/runs/'+id;
        const [results,detail]=await Promise.all([read(base+'/results?project_id='+pid+'&limit=20',pid),read(base+'?project_id='+pid,pid)]);
        data={...results,state:detail.state,stage:detail.status,diagnostics:detail};
      }
      else data=await read('/api/projects/'+pid+'/qualified-data/runs/'+id+'?limit=50',pid);
      if(!isCurrent())return;
      if(stage==='collector'&&String(data.run_id)!==id)throw Error('Ответ содержит другой запуск');
      if(stage==='rtb'&&data.run_id!==id)throw Error('Ответ содержит другой запуск');
      if(stage==='qdp'&&data.run?.id!==id)throw Error('Ответ содержит другой запуск');
      host.replaceChildren();
      const top=node('div',undefined,'cm-inspector-heading');const meta=node('div');meta.append(node('h2','Запуск '+(stage==='collector'?'#'+id:id.slice(0,8))),node('code',id,'cm-subtle'));
      top.append(meta,status(stage,stage==='qdp'?data.run:data),exportButton(stage+'-'+id+'.json',data));host.append(top);
      if(stage!=='rtb'){
        const content=data.videos||data.items||[];
        host.append(node('p',stage==='qdp'?'Сохранённая страница Qualified Set':'Сохранённые результаты Collector','cm-subtle'));
        host.append(jsonPanel('Результаты и происхождение данных',content,true),jsonPanel('Ответ API и диагностика',data));
        if(data.has_more||data.next_after_video_id)host.append(node('p','Это сохранённая страница результатов. Остальные записи доступны в модуле.','cm-subtle'));
        host.append(button('Управление запуском в модуле',()=>onWork(stage,id)));return;
      }
      const layout=node('div',undefined,'cm-inspector-layout');const rail=node('nav',undefined,'cm-model-rail');rail.setAttribute('aria-label','Модели запуска');const pane=node('section',undefined,'cm-model-pane');
      layout.append(rail,pane);host.append(layout);
      let activeRole=roles.find(r=>(data.stage_outputs||[]).some(o=>o.role===r.id))?.id||'fact_extraction',activeTab='result';
      const controls=[];
      function render(){
        controls.forEach(({b,role})=>{const active=role.id===activeRole;b.setAttribute('aria-pressed',String(active));});
        pane.replaceChildren();const role=roles.find(r=>r.id===activeRole);const output=(data.stage_outputs||[]).find(o=>o.role===activeRole);const attempt=(data.stages||[]).find(a=>a.role===activeRole);
        pane.append(node('h3',role.name,'cm-pane-title'));
        pane.append(node('p',output?.model_id||attempt?.model_id||'Модель ещё не вызывалась','cm-model-id'));
        const tabs=node('div',undefined,'cm-inspector-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','Представление артефакта');
        for(const [key,title] of [['result','Результат'],['input','Вход'],['raw','Исходный ответ'],['technical','Диагностика']]){
          const b=button(title,()=>{activeTab=key;render();});b.setAttribute('aria-pressed',String(activeTab===key));tabs.append(b);
        }pane.append(tabs);const body=node('div',undefined,'cm-inspector-body');pane.append(body);
        if(activeTab==='result'){
          if(output)renderArtifacts(body,output);
          else body.append(node('p',attempt?.error||'Сохранённого ответа нет. Этап не завершён или ещё не запускался.','cm-empty-inline'));
        }else if(activeTab==='input'){
          body.append(node('p','Доступные сохранённые входы запуска. Полный текст промпта API не возвращает.','cm-subtle'));
          if(activeRole==='fact_extraction'){
            body.append(jsonPanel('Снимок профиля · ревизия '+data.profile_revision,data.profile||{},true));
            if(data.context_binding)body.append(jsonPanel('Закреплённый контекст',data.context_binding),node('p','Содержимое закреплённого Evidence не входит в этот ответ API.','cm-subtle'));
          }else{
            body.append(jsonPanel('Утверждённые факты',data.approved_facts||[],true));
            if(activeRole==='taxonomy_critic'&&data.generation_reviewed)body.append(jsonPanel('Темы, утверждённые перед Critic',data.topic_proposals||[],true));
            if(data.context_binding)body.append(jsonPanel('Привязка контекста',data.context_binding));
          }
        }else if(activeTab==='raw'){
          if(output?.raw_model_json)body.append(node('p','Оригинальный сохранённый JSON модели.','cm-subtle'),node('pre',JSON.stringify(output.raw_model_json,null,2),'cm-code'));
          else body.append(node('p','Оригинальный ответ не сохранён для этого этапа.','cm-empty-inline'));
        }else body.append(jsonPanel('Попытка, usage и ошибки',attempt||{status:'not_started'},true));
        if(output){
          const tools=node('div',undefined,'cm-artifact-tools');const value=activeTab==='raw'?output.raw_model_json:activeTab==='input'?{profile:data.profile,approved_facts:data.approved_facts,context_binding:data.context_binding}:output;
          const copied=node('span',undefined,'cm-subtle');copied.setAttribute('role','status');
          tools.append(button('Копировать JSON',async()=>{try{await navigator.clipboard.writeText(JSON.stringify(value,null,2));copied.textContent='Скопировано';}catch{copied.textContent='Копирование недоступно. Скачайте JSON.';}}),exportButton('rtb-'+id+'-'+activeRole+'.json',value),copied);body.append(tools);
        }
      }
      for(const role of roles){
        const b=button('',()=>{activeRole=role.id;render();});b.className='cm-model-choice';const st=roleState(data,role.id);
        b.append(node('strong',role.name),node('span',st.label,'cm-state cm-state-'+st.kind));rail.append(b);controls.push({b,role});
      }
      render();
      const footer=node('div',undefined,'cm-inspector-footer');
      footer.append(node('p','Результаты доступны на любом этапе. Решения принимаются в рабочем экране.','cm-subtle'),button(runState('rtb',data).kind==='review'?'Перейти к проверке':'Открыть рабочий экран',()=>onWork(stage,id),true));host.append(footer);
      if(legacyDeep){
        const deep=await read('/api/projects/'+pid+'/taxonomy-builder/runs/'+id+'/deep',pid);
        if(isCurrent())host.append(jsonPanel('Двухпроходное дерево · ответы и проверки',deep,true));
      }
    }catch(error){
      if(!isCurrent())return;loading.textContent='Не удалось открыть результат: '+error.message;
      host.append(button('Повторить',()=>{host.replaceChildren();mountInspector(host,{pid,stage,id,isCurrent,onWork,deepV2,legacyDeep});}));
    }
  }
  function mountDeepPicker(host,{pid,isCurrent,onWork}){
    host.append(node('h2','Deep V2 · сохранённый запуск'),node('p','Введите ID запуска. Общий список Deep V2 сервер пока не возвращает.','cm-subtle'));
    const form=node('form',undefined,'cm-deep-picker');const label=node('label','Run UUID');const input=node('input');input.required=true;input.setAttribute('aria-label','Deep V2 run UUID');input.placeholder='xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx';
    try{input.value=localStorage.getItem('cm.rtb.v2.last.'+pid)||'';}catch{}
    label.append(input);const submit=node('button','Открыть','workspace-button');submit.type='submit';form.append(label,submit);const result=node('div');host.append(form,result);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(!uuid(input.value)){result.replaceChildren(node('p','Укажите корректный UUID.','cm-inline-error'));return;}
      submit.disabled=true;result.replaceChildren(node('p','Загружаем данные…'));const rid=input.value;
      try{
        const base='/api/projects/'+pid+'/taxonomy-builder/deep-v2/runs/'+rid;
        const responses=await Promise.allSettled(['status','pass2-status','review1','review2-preview','publication'].map(path=>read(base+'/'+path,pid)));
        if(!isCurrent()||input.value!==rid)return;
        result.replaceChildren();
        for(const [i,value] of responses.entries())result.append(value.status==='fulfilled'?jsonPanel(['Первый проход','Второй проход','Human Review 1','Human Review 2 preview','Публикация'][i],value.value,i===0):node('p',['Первый проход','Второй проход','Human Review 1','Human Review 2 preview','Публикация'][i]+': '+value.reason.message,'cm-subtle'));
        result.append(button('Открыть управление Deep V2',()=>onWork('foundation')));
      }finally{submit.disabled=false;}
    });
  }
  window.ContentMachineWorkspaceRuns=Object.freeze({load,mountHistory,mountInspector,runState,resultSummary,idOf,read,sources,status,uuid});
})();
