'use strict';
/* UA-MOD-27: UAE Studio. Local read/explicit manual actions ONLY.
 * No token persistence, no automatic qualification, and no fake score values.
 * Each response still requires server-side project token+Host/Origin/CSRF.
 */
(() => {
  const VERSION='uae-local-http/v1';
  const HEX32=/^[0-9a-f]{32}$/;
  const HEX64=/^[0-9a-f]{64}$/;
  const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const STATES=Object.freeze({
    pending:'Ожидает запуска',retry:'Ожидает повтора',
    leased:'В работе',succeeded:'Задание завершено (не квалификация)',
    dead:'Задание завершилось ошибкой',cancelled:'Отменено',
    registered:'Зарегистрирован',processing:'Обрабатывается',
    partial:'Частичный снимок',blocked:'Заблокированный снимок'
  });
  const LABELS=Object.freeze([
    ['source','Источник и права','Нет подтверждённой текущей проверки для Studio'],
    ['qdp','QDP','Точный вход и допуск проверяются сервером'],
    ['sre','SRE','Одобрение актуального SRE не получено'],
    ['a','Group A','Состав признаков A недоступен из защищённого API'],
    ['b','Group B','Типизированные категории B недоступны из защищённого API'],
    ['c','Group C','Breakout Score и компоненты не опубликованы'],
    ['aggregates','Агрегаты','Нужна проверенная выборка UA19; нет основания выводить нули']
  ]);
  const make=(tag,text,cls)=>{
    const e=document.createElement(tag);
    if(text!==undefined)e.textContent=String(text);
    if(cls)e.className=cls;
    return e;
  };
  const button=(label,fn,cls='uae-button')=>{
    const e=make('button',label,cls);
    e.type='button';
    e.addEventListener('click',fn);
    return e;
  };
  const field=(label,type='text',hint='')=>{
    const wrap=make('label',undefined,'uae-field');
    wrap.append(make('span',label));
    const inp=make('input');inp.type=type;
    inp.autocomplete='off';inp.spellcheck=false;
    if(hint)inp.placeholder=hint;
    wrap.append(inp);
    return {wrap,input:inp};
  };
  const meta=(label,value)=>{
    const e=make('div',undefined,'uae-kv');
    e.append(make('dt',label),make('dd',value==null?'—':value));
    return e;
  };
  const date=value=>{
    if(typeof value!=='string')return 'Неизвестно';
    const d=new Date(value);
    return Number.isNaN(d.getTime())?'Неизвестно':d.toLocaleString('ru-RU');
  };
  const codeError=Object.freeze({
    project_forbidden:'Доступ к этому проекту отклонён. Проверь токен проекта.',
    unsupported_api_version:'Версия API несовместима с интерфейсом.',
    database_unavailable:'PostgreSQL недоступен; данные не загружены.',
    dependency_unavailable:'Сервис аналитики временно недоступен.',
    not_found:'Запись не найдена в выбранном проекте.',
    qualification_not_ready:'Запрос ещё не прошёл необходимые проверки QDP и прав источника.',
    idempotency_conflict:'Этот ключ повтора уже связан с другими исходными данными.',
    job_terminal:'Задание уже завершено. Отмена невозможна.',
    aggregate_requires_live_qualified_pins:'Агрегаты пока закрыты: нет серверной подтверждённой выборки UA19.',
    invalid_body:'Неверные параметры задания. Проверь неизменяемые идентификаторы.',
    unsupported_content_kind:'Неподдерживаемый тип контента.'
  });
  function mount(parent,pid){
    if(!HEX32.test(pid))return;
    const root=make('section',undefined,'uae-studio settings-card');
    root.setAttribute('aria-labelledby','uae-title-'+pid);
    const header=make('header',undefined,'uae-header');
    const heading=make('div');
    heading.append(make('div','CONTENT MACHINE  /  INTELLIGENCE','uae-eyebrow'));
    const title=make('h3','Unified Analytics Studio');title.id='uae-title-'+pid;
    heading.append(title,make('p',
      'Только сохранённые состояния. Очередь, частичный снимок и квалифицированная аналитика — разные вещи.'));
    const badge=make('span','Нет подключения','uae-status-badge');
    header.append(heading,badge);root.append(header);
    const status=make('p','Для просмотра укажи локальный токен доступа к проекту.',
      'uae-notice');
    status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const auth=make('div',undefined,'uae-auth');
    const token=field('Токен проекта (64 символа hex)','password','Вводится вручную, не сохраняется');
    token.input.maxLength=64;
    token.input.setAttribute('aria-label','Токен UAE для текущего проекта');
    const connect=button('Подключить и обновить',()=>load(true),'uae-button uae-primary');
    const disconnect=button('Очистить доступ',()=>clear(),'uae-button uae-secondary');
    auth.append(token.wrap,connect,disconnect);root.append(auth,status);
    const info=make('p',
      'Токен не сохраняется в браузере. Запросы выполняются только по нажатию. '+
      'Операции с оплатой, моделями и публикацией здесь отсутствуют.','uae-footnote');
    root.append(info);

    const layout=make('div',undefined,'uae-layout');
    const main=make('div',undefined,'uae-main');
    const side=make('aside',undefined,'uae-aside');side.setAttribute('aria-label','Проверяемость аналитики');
    const summary=make('section',undefined,'uae-panel');
    summary.append(make('h4','Снимки и состояние данных'));
    const counts=make('div',undefined,'uae-counts');
    const snapList=make('ol',undefined,'uae-timeline');
    snapList.setAttribute('aria-label','История доступных снимков');
    const pager=make('div',undefined,'uae-toolbar');
    const next=button('Показать предыдущие',()=>loadNext());
    next.hidden=true;
    const refresh=button('Обновить список',()=>load(false),'uae-button uae-secondary');
    refresh.disabled=true;
    pager.append(refresh,next);
    summary.append(counts,snapList,pager);main.append(summary);

    const inspector=make('section',undefined,'uae-panel');
    inspector.append(make('h4','Проверка выбранного снимка'));
    const snapshotDetails=make('dl',undefined,'uae-details');
    snapshotDetails.append(meta('Состояние','Не выбран'));
    inspector.append(snapshotDetails);main.append(inspector);
    const jobs=make('section',undefined,'uae-panel');
    jobs.append(make('h4','Путь квалификационного задания'));
    jobs.append(make('p','Завершённое задание не означает подтверждённый Breakout Score.',
      'uae-muted'));
    const jobField=field('UUID задания','text','00000000-0000-0000-0000-000000000000');
    const inspectJob=button('Проверить статус',()=>jobStatus());
    const jobInfo=make('dl',undefined,'uae-details');
    const cancel=button('Отменить незавершённое задание',()=>cancelJob(),
      'uae-button uae-danger');
    cancel.hidden=true;
    jobs.append(jobField.wrap,inspectJob,jobInfo,cancel);
    main.append(jobs);

    const query=make('section',undefined,'uae-panel');
    query.append(make('h4','Предпросмотр зарегистрированного запроса'));
    const req=field('UUID запроса','text','UUID зарегистрированного запроса');
    const reqButton=button('Проверить без записи',()=>requestPreview());
    const reqInfo=make('dl',undefined,'uae-details');
    query.append(req.wrap,reqButton,reqInfo);main.append(query);

    const admission=make('details',undefined,'uae-panel uae-advanced');
    admission.append(make('summary','Ручная постановка подтверждённого запроса в очередь'));
    admission.append(make('p',
      'Действие изменяет состояние очереди. Нужны точный request SHA256, UUID и ключ повторного запроса. '+
      'Задача не получает автоматически статус qualified.','uae-muted'));
    const sha=field('request_sha256','text','64 символа SHA256');
    const idem=field('idempotency_key','text','8–180 допустимых символов');
    const batch=field('batch_key','text','1–120 допустимых символов');
    const submit=button('Подтвердить постановку',()=>enqueue(),'uae-button uae-primary');
    admission.append(sha.wrap,idem.wrap,batch.wrap,submit);
    main.append(admission);

    const evidence=make('section',undefined,'uae-panel');
    evidence.append(make('h4','Проверка цепочки данных'));
    evidence.append(make('p',
      'Статусы источников и модулей ниже не вычисляются по названию или наличию ID: '+
      'сейчас API не предоставляет их актуальные проверенные доказательства.','uae-muted'));
    const phases=make('ul',undefined,'uae-evidence');
    for(const [,label,detail] of LABELS){
      const item=make('li',undefined,'uae-evidence-row');
      const cell=make('div');cell.append(make('strong',label),make('span',detail));
      const phase=make('span','Недоступно','uae-pill');
      item.append(cell,phase);phases.append(item);
    }
    evidence.append(phases);side.append(evidence);

    const metrics=make('section',undefined,'uae-panel');
    metrics.append(make('h4','Breakout Score · компоненты'));
    const noScores=make('div','Значения и компоненты не опубликованы',
      'uae-unavailable');
    metrics.append(noScores,make('p',
      'Шкала 0–100 не используется. Отсутствие подтверждённых метрик — не нулевой результат.',
      'uae-muted'));
    side.append(metrics);

    const aggregation=make('section',undefined,'uae-panel');
    aggregation.append(make('h4','Агрегаты и измерения'));
    const dims=make('div',undefined,'uae-dimensions');
    for(const dim of ['Тип контента','Хук','Формат видео','Роль в истории',
                      'Монтаж','Субтитры','CTA','Тематика']){
      dims.append(make('span',dim+' · недоступно','uae-dim'));
    }
    const agg=button('Проверить доступность агрегатов',()=>checkAggregates(),
      'uae-button uae-secondary');
    aggregation.append(dims,make('p',
      'Нужна текущая проверенная выборка не менее пяти видео. '+
      'Ни p-value, ни достоверность или причинность здесь не предполагаются.','uae-muted'),
      agg);
    side.append(aggregation);
    layout.append(main,side);root.append(layout);parent.append(root);
    let busy=false,ready=false,before=null,serial=0,selectedJob=null;

    const inform=(message,kind='info')=>{
      status.textContent=message;status.dataset.kind=kind;
      badge.textContent=kind==='ok'?'Локально подключено':
        kind==='error'?'Ошибка доступа':'Нет подтверждённой аналитики';
    };
    function eraseReadside(){
      selectedJob=null;
      counts.replaceChildren();snapList.replaceChildren();jobInfo.replaceChildren();
      reqInfo.replaceChildren();snapshotDetails.replaceChildren();
      snapshotDetails.append(meta('Состояние','Не выбран'));
      next.hidden=true;refresh.disabled=true;cancel.hidden=true;
    }
    function clear(){
      serial++;ready=false;before=null;
      token.input.value='';jobField.input.value='';req.input.value='';
      sha.input.value='';idem.input.value='';batch.input.value='';
      eraseReadside();
      inform('Доступ очищен. Введи токен проекта для нового подключения.');
    }
    async function request(path,body){
      if(!HEX64.test(token.input.value)){
        throw {code:'project_forbidden',status:403};
      }
      const currentToken=token.input.value;
      const headers={
        'X-UAE-Project-Token':currentToken,
        'X-UAE-Api-Version':VERSION
      };
      const options={cache:'no-store',credentials:'same-origin',headers};
      if(body!==undefined){
        options.method='POST';headers['X-ContentMachine']='1';
        headers['Content-Type']='application/json';options.body=JSON.stringify(body);
      }
      let response;
      try{response=await fetch(
        '/api/projects/'+pid+'/unified-analytics'+path,options);
      }catch(_){throw {code:'dependency_unavailable',status:503};}
      let data;
      try{data=await response.json();}catch(_){
        throw {code:'dependency_unavailable',status:503};
      }
      // A token change/disconnect while fetch was pending revokes this
      // response for the current DOM. Never paint a former project's data.
      if(currentToken!==token.input.value){
        throw {code:'project_forbidden',status:403};
      }
      if(!response.ok||!data||data.ok!==true){
        throw {code:(data&&data.code)||'dependency_unavailable',status:response.status};
      }
      if(data.schema_version!==VERSION){
        throw {code:'unsupported_api_version',status:409};
      }
      return data;
    }
    function failure(err){
      if(!root.isConnected)return;
      if(err&&err.code==='project_forbidden'){
        serial++;ready=false;before=null;
        eraseReadside();
        token.input.value='';
      }
      inform(codeError[err&&err.code]||'Запрос не выполнен. Состояние неизвестно.',
        'error');
    }
    function lock(fn){
      return async(...args)=>{
        if(busy)return;
        busy=true;connect.disabled=true;refresh.disabled=true;
        try{await fn(...args);}catch(err){failure(err);}
        finally{
          busy=false;connect.disabled=false;
          refresh.disabled=!ready;
        }
      };
    }
    function draw(list,append=false){
      if(!Array.isArray(list)||list.some(item=>!item||
        !['partial','blocked'].includes(item.status)||
        !Number.isSafeInteger(item.id)||item.id<=0||
        !Number.isSafeInteger(item.snapshot_version)||item.snapshot_version<=0)){
        throw {code:'dependency_unavailable'};
      }
      if(!append){counts.replaceChildren();snapList.replaceChildren();}
      let partial=0,blocked=0;
      for(const item of list){
        if(item.status==='partial')partial++;else blocked++;
        const li=make('li',undefined,'uae-timeline-row');
        const head=make('div',undefined,'uae-line-title');
        head.append(make('strong','Снимок #'+item.id+' · v'+item.snapshot_version),
          make('span',STATES[item.status],'uae-pill '+item.status));
        li.append(head,make('p','Видео #'+item.video_id+
          ' · '+date(item.created_at)));
        const open=button('Открыть сведения',()=>openSnapshot(item.id),
          'uae-button uae-secondary');
        li.append(open);snapList.append(li);
      }
      if(!snapList.children.length)
        snapList.append(make('li','Пока нет доступных частичных или заблокированных снимков.',
          'uae-empty'));
      // Counts are about THIS page only. Never project-wide coverage.
      counts.append(
        make('div','На странице: '+list.length,'uae-count'),
        make('div','Частичных: '+partial,'uae-count'),
        make('div','Заблокированных: '+blocked,'uae-count')
      );
    }
    const base=()=>'/snapshots?limit=10';
    const load=lock(async()=>{
      const ticket=++serial;
      const data=await request(base());
      if(ticket!==serial||!root.isConnected)return;
      draw(data.snapshots);
      ready=true;before=data.next_before;
      next.hidden=before==null;
      inform('Показана только доступная страница истории. '+
        'Не опубликованные qualified-снимки не учитываются.','ok');
    });
    const loadNext=lock(async()=>{
      if(!ready||before==null)return;
      const ticket=++serial;
      const data=await request(base()+'&before='+encodeURIComponent(String(before)));
      if(ticket!==serial||!root.isConnected)return;
      draw(data.snapshots,true);
      before=data.next_before;
      next.hidden=before==null;
      inform('Добавлена страница снимков. Счётчики отражают последнюю страницу, '+
        'не все данные проекта.','ok');
    });
    async function inspect(id){
      const data=await request('/snapshots/'+encodeURIComponent(String(id)));
      if(!root.isConnected)return;
      const item=data.snapshot;
      if(!item||!['partial','blocked'].includes(item.status)){
        throw {code:'dependency_unavailable'};
      }
      snapshotDetails.replaceChildren();
      for(const [label,value] of [
        ['ID снимка',item.id],['Видео',item.video_id],
        ['Состояние',STATES[item.status]],
        ['Версия снимка',item.snapshot_version],
        ['Контракт квалификации',item.gate_schema],
        ['Версия входа',item.input_schema],
        ['Создан',date(item.created_at)],
        ['Доказательства A/B/C','Не выдаются через этот API'],
        ['Причины блокировки','Подробные приватные причины не опубликованы'],
        ['Breakout Score','Неизвестно / недоступно']
      ])snapshotDetails.append(meta(label,value));
      inform('Снимок частичный или заблокирован. Это не подтверждённая статистика.');
    }
    const openSnapshot=lock(async(id)=>{await inspect(id);});
    const jobStatus=lock(async()=>{
      const id=jobField.input.value.trim();
      if(!UUID.test(id))throw {code:'invalid_body'};
      const data=await request('/jobs/'+encodeURIComponent(id));
      if(!root.isConnected)return;
      jobInfo.replaceChildren();
      const j=data.job;
      for(const [label,value] of [
        ['Задание',j.job_id],['Состояние',STATES[j.state]||'Неизвестно'],
        ['Попытки',String(j.attempts)+' / '+j.max_attempts],
        ['Начато',date(j.created_at)],['Обновлено',date(j.updated_at)],
        ['Снимок',j.snapshot_id==null?'Не сформирован':j.snapshot_id],
        ['Код ошибки',j.last_error_code||'Не указан'],
        ['Квалификация','Не доказана статусом очереди']
      ])jobInfo.append(meta(label,value));
      selectedJob=id;
      cancel.hidden=!['pending','retry','leased'].includes(j.state);
      inform('Состояние очереди обновлено. Подтверждения аналитики не выдавались.');
    });
    const cancelJob=lock(async()=>{
      if(!selectedJob||!UUID.test(selectedJob))throw {code:'invalid_body'};
      if(!window.confirm('Отменить только это задание? Публикация и аналитика не запускаются.'))return;
      const data=await request('/jobs/'+selectedJob+'/cancel',{});
      cancel.hidden=true;
      inform(data.cancelled?'Отмена принята. Обнови статус задания.':
        'Отмена не подтверждена.');
    });
    const requestPreview=lock(async()=>{
      const id=req.input.value.trim();
      if(!UUID.test(id))throw {code:'invalid_body'};
      const data=await request('/preview?request_id='+encodeURIComponent(id));
      if(!root.isConnected)return;
      const x=data.request;
      reqInfo.replaceChildren();
      for(const [label,value] of [
        ['UUID',x.request_id],['Видео',x.video_id],
        ['Состояние',STATES[x.state]||x.state],
        ['Источник',x.source_id],
        ['Политика источника',x.source_policy_version_id],
        ['Результат квалификации','Не проверен']
      ])reqInfo.append(meta(label,value));
      inform('Предпросмотр выполнен без записи или запуска модели.');
    });
    const enqueue=lock(async()=>{
      const rid=req.input.value.trim(),digest=sha.input.value.trim();
      const idemKey=idem.input.value.trim(),batchKey=batch.input.value.trim();
      if(!UUID.test(rid)||!HEX64.test(digest)||
         !/^[A-Za-z0-9_.:-]{8,180}$/.test(idemKey)||
         !/^[A-Za-z0-9_.:-]{1,120}$/.test(batchKey))
        throw {code:'invalid_body'};
      if(!window.confirm('Поставить зарегистрированный запрос в очередь? '+
        'Это не даёт статус qualified и не запускает платную модель.'))return;
      const data=await request('/qualify',{
        request_id:rid,request_sha256:digest,
        idempotency_key:idemKey,batch_key:batchKey
      });
      if(!root.isConnected)return;
      jobField.input.value=data.job_id;
      inform(data.result==='replayed'?'Ранее созданное задание найдено.':
        'Задание зарегистрировано в очереди. Квалификация не подтверждена.');
      idem.input.value='';batch.input.value='';
    });
    const checkAggregates=lock(async()=>{
      try{
        await request('/aggregates');
        // The server currently MUST never return successful raw aggregates
        // through this incomplete qualified-pins route.
        throw {code:'dependency_unavailable',status:503};
      }catch(err){
        if(err&&err.code==='aggregate_requires_live_qualified_pins'){
          inform(codeError[err.code]);return;
        }
        throw err;
      }
    });
    token.input.addEventListener('input',()=>{
      const exposed=ready||snapList.children.length>0||
        jobInfo.children.length>0||reqInfo.children.length>0||
        snapshotDetails.children.length>1;
      serial++;ready=false;before=null;
      eraseReadside();
      if(exposed)inform('Токен изменён. Подключись повторно, чтобы не смешивать права.');
    });
    // No automatic fetch or timer on mounting. Explicit interaction only.
  }
  window.ContentMachineUnifiedAnalyticsStudio={mount};
})();
