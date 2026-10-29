'use strict';
/* PIE-MOD-34: explicitly nonpublishable Studio; no innerHTML/untrusted HTML. */
(() => {
  const el=id=>document.getElementById(id);
  const PROJECT=/^[0-9a-f]{32}$/;
  const STATUS=['exploratory','rejected','needs_review'];
  const VIEWS=['overview','patterns','datasets','candidates','validations','results','insights','reviews'];
  const state={epoch:0,project:'',cursor:null,back:[],next:null,page:1,view:'overview'};
  function textNode(tag,text,cls){
    const node=document.createElement(tag);
    if(cls)node.className=cls;
    node.textContent=String(text==null?'—':text);
    return node;
  }
  function setStatus(message,kind='neutral'){
    el('pieConnection').textContent=message;
    el('pieConnection').dataset.state=kind;
  }
  function reset(){
    ++state.epoch; // Invalidate pending responses when filters change.
    el('pieCards').replaceChildren();
    el('pieChart').replaceChildren();
    el('pieResults').hidden=true;
    el('pieMetrics').hidden=true;
    el('pieError').hidden=true;
    el('pieNext').disabled=true;
    el('pieBack').disabled=true;
    state.cursor=null;state.back=[];state.next=null;state.page=1;
  }
  function error(message){
    el('pieError').hidden=false;
    el('pieError').textContent=message;
    el('pieInfo').textContent='Данные не показаны — текущая проверка доступа не завершилась.';
    setStatus('Доступ не подтверждён','error');
  }
  function query(){
    const project=el('pieProject').value.trim();
    const view=el('pieView').value;
    if(!PROJECT.test(project))throw Error('Укажите точный ID проекта из 32 символов.');
    if(!VIEWS.includes(view))throw Error('Неизвестное представление.');
    const q=new URLSearchParams();
    q.set('page_size','20');
    if(el('pieStatus').value){
      if(!STATUS.includes(el('pieStatus').value))throw Error('Некорректный статус.');
      q.set('status',el('pieStatus').value);
    }
    const platform=el('piePlatform').value.trim();
    if(platform){
      if(!/^[a-z][a-z0-9_]{0,31}$/.test(platform))throw Error('Некорректная платформа.');
      q.set('platform',platform);
    }
    const n=el('pieMinPairs').value.trim();
    if(!/^[0-9]{1,6}$/.test(n)||Number(n)<1||Number(n)>100000)
      throw Error('Число пар должно быть от 1 до 100000.');
    q.set('min_matched_pairs',n);
    if(state.cursor)q.set('cursor',state.cursor);
    return {project,view,path:'/api/projects/'+project+'/pie/'+view+'?'+q.toString()};
  }
  function caveats(card,entry){
    const ul=textNode('ul','');
    const items=Array.isArray(entry.caveats)?entry.caveats:[
      'Недостаточно данных для независимой проверки.',
      'Текст Insight не опубликован и не отображается.',
    ];
    for(const c of items)ul.append(textNode('li',c));
    card.append(ul);
  }
  function renderPattern(entry,index){
    const card=textNode('article','', 'pie-card');
    const title=entry.feature_name||entry.factor_id||'Кандидат';
    card.append(textNode('h3',title));
    card.append(textNode('p',entry.status_label||'Предварительный результат','pie-muted'));
    const n=Number.isSafeInteger(entry.matched_pair_count)?entry.matched_pair_count:0;
    card.append(textNode('p','Сопоставленных пар: '+n+' · Поддерживаемых групп: '+(entry.supported_bin_count??'—')));
    const raw=entry.descriptive_raw_mean==null?'не рассчитано':entry.descriptive_raw_mean+' '+(entry.descriptive_source_unit||'');
    card.append(textNode('p','Наблюдаемая разность: '+raw+' (дескриптивно, не причинно)'));
    card.append(textNode('small','Ревизия '+(entry.revision||'—')+' · '+(entry.platform||'без платформы')));
    caveats(card,entry);
    return card;
  }
  function renderInternal(entry){
    const card=textNode('article','','pie-card');
    card.append(textNode('h3','Insight-кандидат · ревизия '+(entry.revision||'—')));
    card.append(textNode('p','Проверка: '+(entry.verification_state||'не выполнялась')));
    card.append(textNode('p','Событий внутреннего ревью: '+(entry.internal_review_event_count??0)));
    if(entry.reason_code)card.append(textNode('p','Причина ограничения: '+entry.reason_code));
    card.append(textNode('p','Текст недоступен. Публикация не разрешена.','pie-warning'));
    caveats(card,entry);
    return card;
  }
  function renderBars(items){
    const chart=el('pieChart');
    chart.replaceChildren();
    const comparable=items.filter(x=>Number.isSafeInteger(x.matched_pair_count)&&x.matched_pair_count>=0).slice(0,12);
    if(!comparable.length){chart.hidden=true;return;}
    chart.hidden=false;
    const max=Math.max(1,...comparable.map(x=>x.matched_pair_count));
    comparable.forEach(entry=>{
      const row=textNode('div','','pie-bar-row');
      const label=textNode('span',entry.feature_name||entry.factor_id||'Кандидат');
      const rail=textNode('progress','','pie-bar-track');
      rail.max=max;
      rail.value=entry.matched_pair_count;
      rail.setAttribute('aria-label','Сопоставленные пары: '+entry.matched_pair_count);
      row.append(label,rail,textNode('strong',entry.matched_pair_count));
      chart.append(row);
    });
  }
  function render(payload,view){
    if(!payload||payload.schema_version!=='pie-pattern-explorer/v1'
       ||payload.ok!==true||payload.publication_allowed!==false
       ||payload.project_id!==state.project||payload.view!==view
       ||!Array.isArray(payload.items)||payload.items.length>80
       ||payload.items.some(x=>!x||x.can_publish!==false
           ||x.causal_claim!==false||x.independent_holdout_confirmed!==false))
      throw Error('Сервер вернул несовместимый или непроверенный ответ.');
    el('pieResults').hidden=false;
    el('pieMetrics').hidden=false;
    const items=payload.items;
    el('pieCount').textContent=String(items.length);
    el('piePairs').textContent=String(items.reduce((sum,x)=>
      sum+(Number.isSafeInteger(x.matched_pair_count)?x.matched_pair_count:0),0));
    el('pieCards').replaceChildren();
    renderBars(items);
    if(!items.length)el('pieCards').append(textNode('p',
      view==='datasets'?'Исходные наборы данных не доступны через read-only Studio.':
      'Нет доступных записей. Источник отсутствует, не готов или права на его строки не подтверждены.',
      'pie-muted'));
    else for(const entry of items)el('pieCards').append(
      view==='insights'||view==='reviews'?renderInternal(entry):renderPattern(entry));
    state.next=typeof payload.next_cursor==='string'?payload.next_cursor:null;
    el('pieNext').disabled=!state.next;
    el('pieBack').disabled=state.back.length===0;
    el('piePageIndex').textContent='Страница '+state.page;
    el('piePageNote').textContent='Только доступные записи текущей страницы; не весь корпус';
    el('pieInfo').textContent=items.length?
      'Права проверены сейчас. Результаты остаются предварительными и не разрешены к публикации.':
      'Сейчас нет записей для показа. Это не означает отсутствия закономерностей в проекте.';
    setStatus('Просмотр ограниченных кандидатов','ready');
  }
  async function load(){
    const myEpoch=++state.epoch;
    el('pieError').hidden=true;
    let req;
    try{req=query();}catch(err){error(err.message);return;}
    state.project=req.project;state.view=req.view;
    setStatus('Проверяем актуальные права…','loading');
    // Never keep previously authorized content visible while rights are refreshed.
    el('pieCards').replaceChildren();
    el('pieChart').replaceChildren();
    el('pieResults').hidden=true;
    el('pieMetrics').hidden=true;
    el('pieLoad').disabled=true;
    try{
      const response=await fetch(req.path,{credentials:'same-origin',
        headers:{'Accept':'application/json'},cache:'no-store'});
      if(myEpoch!==state.epoch)return;
      const data=await response.json();
      if(myEpoch!==state.epoch)return;
      if(!response.ok)throw Error(
        data?.error?.message||'Сервер не подтвердил права или доступность UAE.');
      render(data,req.view);
    }catch(err){
      if(myEpoch===state.epoch){
        el('pieCards').replaceChildren();
        el('pieChart').replaceChildren();
        el('pieResults').hidden=true;
        el('pieMetrics').hidden=true;
        error(err.message||'Проверка недоступна.');
      }
    }finally{if(myEpoch===state.epoch)el('pieLoad').disabled=false;}
  }
  el('pieFilterForm').addEventListener('submit',ev=>{
    ev.preventDefault();reset();load();
  });
  el('pieNext').addEventListener('click',()=>{
    if(!state.next)return;
    state.back.push(state.cursor);
    state.cursor=state.next;
    state.page+=1;load();
  });
  el('pieBack').addEventListener('click',()=>{
    if(!state.back.length)return;
    state.cursor=state.back.pop();
    state.page=Math.max(1,state.page-1);load();
  });
  for(const id of ['pieView','pieStatus','piePlatform','pieMinPairs']){
    el(id).addEventListener('change',reset);
  }
  const params=new URLSearchParams(location.search);
  const project=params.get('project');
  if(project&&PROJECT.test(project))el('pieProject').value=project;
  if(project&&PROJECT.test(project))load();
})();
