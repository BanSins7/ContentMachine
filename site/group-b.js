'use strict';
/* GB-MOD-26: local, same-origin operator Studio. UI NEVER authorizes QDP,
 * media evidence, paid receipts, reviewer identity or a qualified result. */
(() => {
  const BLOCKS = ['B1','B2','B3','B4','B5','B6','B7','B8'];
  const NAMES = {
    B1:'Hook / привлечение',
    B2:'Формат публикации',
    B3:'Структура истории',
    B4:'Визуальные признаки',
    B5:'Монтаж и смена кадров',
    B6:'Субтитры',
    B7:'Призыв к действию',
    B8:'Тематика и категория',
  };
  const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
  const SHA = /^[a-f0-9]{64}$/;
  const by = (tag, cls, label) => {
    const node=document.createElement(tag);
    if (cls) node.className=cls;
    if (label!==undefined) node.textContent=label;
    return node;
  };
  const input = (label,opts={}) => {
    const wrap=by('label','gb-field');
    const title=by('span','gb-field-label',label);
    const node=opts.lines ? by('textarea') : by(opts.choices?'select':'input');
    if (opts.choices) opts.choices.forEach(([value,name])=>{
      const choice=by('option',null,name);choice.value=value;node.append(choice);
    });
    if (!opts.choices && !opts.lines) node.type=opts.type||'text';
    node.placeholder=opts.placeholder||'';
    node.maxLength=opts.maxLength||256;
    node.required=Boolean(opts.required);
    if (opts.value!==undefined) node.value=opts.value;
    wrap.append(title,node);
    return {wrap,node};
  };
  const call=async (path,payload) => {
    const options=payload===undefined ?
      {cache:'no-store',credentials:'same-origin'} :
      {method:'POST',cache:'no-store',credentials:'same-origin',
       headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
       body:JSON.stringify(payload)};
    const response=await fetch(path,options);
    let data;
    try {data=await response.json();}
    catch(_) {throw Error('Сервер вернул некорректный ответ.');}
    if (!response.ok || data?.ok!==true) {
      throw Error(data?.code||'Запрос отклонён сервером');
    }
    return data;
  };
  const blocksValue=text=>Array.from(new Set(text.split(/[\s,]+/)
    .map(x=>x.toUpperCase()).filter(Boolean)));
  const showValue=value=>typeof value==='string'?value:JSON.stringify(value);
  const forms = [
    {action:'create',title:'Новый запуск (только подтверждённое QDP)',
     fields:[
       ['inbox_id','ID подтверждённого QDP inbox',{required:true,placeholder:'UUID'}],
       ['idempotency_key','Ключ повторного запроса',{required:true,placeholder:'не менее 12 символов'}],
     ]},
    {action:'quote',title:'Рассчитать стоимость (без оплаты)',fields:[
       ['model_id','Проверенная модель',{required:true,placeholder:'provider/model'}],
       ['blocks','Блоки через запятую',{required:true,value:'B1,B2,B3,B4,B7,B8'}],
       ['idempotency_key','Ключ расчёта',{required:true}],
     ]},
    {action:'approve',title:'Одобрить бюджет',fields:[
       ['quote_id','ID расчёта',{required:true,placeholder:'UUID'}],
       ['authorized_usd','Сумма USD точно как в расчёте',{required:true,placeholder:'0.05'}],
       ['confirmation','Подтверждаю платное использование',{type:'checkbox'}],
     ]},
    {action:'start',title:'Поставить блоки в очередь (без платного HTTP)',fields:[
       ['blocks','Блоки через запятую',{required:true,value:'B5'}],
       ['request_key','Ключ запуска',{required:true}],
     ]},
    {action:'review',title:'Проверка экспертом (требуются оригинальные доказательства)',fields:[
       ['kind','Тип',{choices:[['correction','Исправление'],['gold_annotation','Эталонная аннотация']]}],
       ['block','Блок',{choices:BLOCKS.map(x=>[x,x+' · '+NAMES[x]])}],
       ['target','Целевая строка',{required:true,value:'topic'}],
       ['index','Индекс слайда или CTA',{placeholder:'пусто для одной строки'}],
       ['field','Поле',{required:true,value:'subtopic_id'}],
       ['value','Значение JSON',{required:true,placeholder:'null / 1 / true / "string"'}],
       ['evidence_refs','SHA-256 исходных доказательств',{required:true,lines:true,placeholder:'по одному SHA на строку'}],
       ['reason','Основание',{required:true,lines:true}],
       ['gold_version','Версия эталона',{placeholder:'только для gold'}],
       ['sample_key','Ключ эталона',{placeholder:'вычисленный по исходному анализу'}],
     ]},
  ];

  function mount(root,projectId) {
    if (!root || !/^[0-9a-f]{32}$/.test(projectId)) return;
    const path='/api/projects/'+projectId+'/group-b';
    const state={runId:null,offset:0,limit:10,detail:null,loading:false};
    const section=by('section','settings-card gb-studio');
    section.setAttribute('aria-label','Group B · анализ контента');
    const header=by('div','gb-head');
    header.append(by('h2',null,'Group B · Content Features'),
                  by('p','settings-description','B1–B8 · только подтверждённые результаты исходных данных.'));
    const msg=by('p','settings-feedback gb-notice','');
    msg.setAttribute('role','status');msg.setAttribute('aria-live','polite');
    const nav=by('div','settings-key-actions');
    const refresh=by('button','button settings-secondary','Обновить запуски');
    refresh.type='button';
    const previous=by('button','button settings-secondary','← Назад');
    previous.type='button';previous.disabled=true;
    const next=by('button','button settings-secondary','Вперёд →');
    next.type='button';
    nav.append(refresh,previous,next);
    const runs=by('div','gb-run-list');
    const detail=by('div','gb-run-detail');
    const actions=by('div','gb-actions');
    const blocklist=by('div','gb-blocks');
    section.append(header,msg,nav,runs,detail,blocklist,actions);
    root.append(section);
    const notice=(text,error=false)=>{
      msg.textContent=text;msg.dataset.status=error?'error':'ok';
    };

    async function load() {
      if(state.loading) return;
      state.loading=true;
      refresh.disabled=true;
      notice('Загружаю запуски Group B…');
      try {
        const data=await call(path+'/runs?offset='+state.offset+'&limit='+state.limit);
        runs.replaceChildren();
        if(!data.items.length) runs.append(by('p','gb-hint','Запусков пока нет.'));
        data.items.forEach(item=>{
          const row=by('div','gb-run');
          const open=by('button','button settings-secondary',
            '#'+item.video_id+' · '+item.state+' · '+item.run_id.slice(0,8));
          open.type='button';
          open.addEventListener('click',()=>openRun(item.run_id));
          row.append(open,
            by('span','gb-hint','Изменено: '+(item.updated_at||'—')));
          runs.append(row);
        });
        previous.disabled=state.offset===0;
        next.disabled=data.items.length<state.limit ||
          (data.total!==undefined && state.offset+data.items.length>=data.total);
        notice('Записей: '+data.items.length+'. Фильтр проекта включён.');
      }catch(e){
        runs.replaceChildren();
        notice('Group B: '+e.message,true);
        next.disabled=true;
      }finally{refresh.disabled=false;state.loading=false;}
    }

    async function openRun(id) {
      if (!UUID.test(id))return;
      state.runId=id;
      detail.replaceChildren();
      blocklist.replaceChildren();
      notice('Читаю результат и исходные статусы…');
      try {
        const data=await call(path+'/runs/'+id);
        state.detail=data;
        const run=data.run;
        detail.append(by('h3',null,'Запуск '+id),
          by('p','gb-hint',
            'Видео: '+run.video_id+' · '+run.content_type+
            ' · состояние: '+run.state+' · анализ: '+(run.result_analysis_id||'отсутствует')),
          by('p','gb-hint',
            'Доказательства: '+(run.media_manifest_sha256||'не подтверждены')+
            ' · Group A: '+(run.a_snapshot_sha256||'нет привязки')),
          by('p','gb-hint',
            'Ревизия проверки: '+data.review_revision+
            ' · исправлений: '+data.correction_count+
            (data.invalidated?' · РЕЗУЛЬТАТ УСТАРЕЛ':'')+
            ' · квалификация: не проверена'));
        blocklist.append(by('h3',null,'Состояние B1–B8'));
        for(const block of BLOCKS){
          const entry=data.blocks.find(x=>x.block===block);
          const item=by('div','gb-block');
          const title=by('strong',null,block+' · '+NAMES[block]);
          const status=by('span','gb-status',
            entry?entry.status+' · '+(entry.error_code||'без ошибки'):'нет данных');
          const evidence=by('button','button settings-secondary','Доказательства');
          evidence.type='button';
          evidence.addEventListener('click',()=>loadEvidence(block,item));
          item.append(title,status,evidence);
          blocklist.append(item);
        }
        showActions();
        notice('История доступна только для чтения; тексты оригинальных медиа защищены.');
      }catch(e){
        notice('Запуск: '+e.message,true);
      }
    }

    async function loadEvidence(block,container){
      const old=container.querySelector('.gb-evidence');
      if(old){old.remove();return;}
      const area=by('pre','gb-evidence','Загружаю ссылочные SHA…');
      container.append(area);
      try {
        const result=await call(path+'/runs/'+state.runId+
          '/evidence?block='+block+'&offset=0&limit=20');
        area.textContent=JSON.stringify(result.items,null,2);
      }catch(e){area.textContent='Нет разрешения / доказательств: '+e.message;}
    }

    function showActions(){
      actions.replaceChildren();
      if(!state.runId)return;
      const cancellation=by('button','button settings-secondary','Отменить запуск');
      cancellation.type='button';
      cancellation.addEventListener('click',async()=>{
        if(!window.confirm('Отменить незавершённые задания Group B? Платные резервы не возвращаются автоматически.'))return;
        cancellation.disabled=true;
        try{
          await call(path+'/runs/'+state.runId+'/cancel',{confirm:true});
          notice('Отмена записана. Расходы требуют отдельной сверки.');
          await openRun(state.runId);
        }catch(e){notice('Отмена отклонена: '+e.message,true);}
        finally{cancellation.disabled=false;}
      });
      actions.append(cancellation);
      for(const entry of forms.filter(f=>f.action!=='create')) actions.append(form(entry));
    }

    function form(entry){
      const panel=by('details','gb-action');
      panel.append(by('summary',null,entry.title));
      const frm=by('form','gb-form');
      const fields={};
      for(const [key,label,opt] of entry.fields){
        const element=input(label,opt);
        fields[key]=element.node;
        frm.append(element.wrap);
      }
      const submit=by('button','button primary','Отправить на проверку');
      submit.type='submit';
      const feedback=by('p','settings-feedback gb-hint','');
      feedback.setAttribute('role','status');
      frm.append(submit,feedback);
      frm.addEventListener('submit',async event=>{
        event.preventDefault();
        const payload={};
        for(const [key,el] of Object.entries(fields)){
          if(el.type==='checkbox')payload[key]=el.checked;
          else payload[key]=el.value.trim();
        }
        if(['quote','start'].includes(entry.action)){
          payload.blocks=blocksValue(payload.blocks);
          if(!payload.blocks.length || payload.blocks.some(x=>!BLOCKS.includes(x))){
            feedback.textContent='Недопустимый набор блоков.';return;
          }
        }
        if(entry.action==='review'){
          try{payload.value=JSON.parse(payload.value);}
          catch(_){feedback.textContent='Значение должно быть корректным JSON.';return;}
          payload.index=payload.index?Number(payload.index):null;
          payload.expected_revision=state.detail?.review_revision||0;
          payload.previous_sha256=null;
          payload.evidence_refs=payload.evidence_refs.split(/[\s,]+/).filter(Boolean);
          if(payload.evidence_refs.some(x=>!SHA.test(x))){
            feedback.textContent='Нужны настоящие SHA-256 ссылок на оригинальные доказательства.';return;
          }
          if(payload.kind==='correction'){
            payload.gold_version=null;payload.sample_key=null;
          }else{
            payload.gold_version=payload.gold_version||null;
            payload.sample_key=payload.sample_key||null;
          }
          if(payload.expected_revision>0){
            feedback.textContent='Для последующих ревизий требуется предыдущий SHA из проверенного журнала. UI не создаёт его автоматически.';
            return;
          }
        }
        submit.disabled=true;
        feedback.textContent='Проверка доверенным сервером…';
        try{
          const target=entry.action==='create'?path+'/runs':
            path+'/runs/'+state.runId+'/'+entry.action;
          const data=await call(target,payload);
          feedback.textContent='Сервер подтвердил операцию: '+
            JSON.stringify(data[entry.action]||data);
          if(state.runId)await openRun(state.runId);else await load();
        }catch(e){feedback.textContent='Операция отклонена: '+e.message;}
        finally{submit.disabled=false;}
      });
      panel.append(frm);
      return panel;
    }

    const create=by('div','gb-create');
    create.append(form(forms[0]));
    header.append(create);
    refresh.addEventListener('click',load);
    previous.addEventListener('click',()=>{state.offset=Math.max(0,state.offset-state.limit);load();});
    next.addEventListener('click',()=>{state.offset+=state.limit;load();});
    load();
  }

  window.ContentMachineGroupB=Object.freeze({mount});
})();
