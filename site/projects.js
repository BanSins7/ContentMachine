'use strict';
/* Inert nine-step project intake. Local records and actual attachments, no collectors. */
(() => {
  const el = id => document.getElementById(id);
  const DRAFT_KEY = 'cm.projectWizard.draft.v1';
  const TEXT_KEYS = [
    'name', 'niche', 'goal', 'product_description', 'product_type',
    'product_functions', 'audience_problems', 'product_url',
    'competitors', 'competitor_socials', 'promoter_accounts', 'video_references'
  ];
  const STEPS = [
    {
      title: 'Ниша проекта',
      help: 'Расскажи, в какой тематике будет работать проект. Если название уже придумано — добавь его тоже.',
      fields: [
        {key:'name',label:'Название проекта (необязательно)',hint:'Например: FitFlow',multiline:false},
        {key:'niche',label:'Ниша',hint:'Например: Health & Fitness',multiline:true},
      ],
    },
    {
      title: 'Цель проекта',
      help: 'Какого результата хочешь добиться с помощью коротких видео?',
      fields:[{key:'goal',label:'Цель',hint:'Например: привлекать пользователей приложения через TikTok, Reels и Shorts'}],
    },
    {
      title: 'Описание собственного продукта',
      help: 'Опиши приложение или продукт простыми словами: что оно делает, кому помогает и почему люди им пользуются.',
      fields:[
        {key:'product_description',label:'Общее описание',hint:'Контекст о продукте, аудитории и особенностях'},
        {key:'product_type',label:'Тип продукта',hint:'Выбери тип продукта',multiline:false},
        {key:'product_functions',label:'Что продукт реально умеет',hint:'Опиши функции: например, считает калории по фотографии'},
        {key:'audience_problems',label:'Какие проблемы есть у аудитории',hint:'Опиши трудности и потребности людей отдельно от функций продукта'},
      ],
    },
    {
      title: 'Ссылка на свой продукт',
      help: 'Укажи ссылку на приложение, сайт или страницу продукта. Можно вставить несколько ссылок или пояснения.',
      fields:[{key:'product_url',label:'Ссылка или пояснение',hint:'https://apps.apple.com/…'}],
    },
    {
      title: 'Конкуренты',
      help: 'Добавь названия и ссылки на конкурирующие приложения или продукты. Каждый конкурент — с новой строки.',
      fields:[{key:'competitors',label:'Продукты конкурентов',hint:'Название — https://…'}],
    },
    {
      title: 'Социальные сети конкурентов',
      help: 'Ссылки на их аккаунты в TikTok, Instagram, YouTube и других социальных сетях.',
      fields:[{key:'competitor_socials',label:'Аккаунты и ссылки',hint:'TikTok: https://…\nInstagram: https://…\nYouTube: https://…'}],
    },
    {
      title: 'Аккаунты, рекламирующие конкурентов',
      help: 'Блогеры, партнёры и сторонние страницы, которые продвигают похожие продукты.',
      fields:[{key:'promoter_accounts',label:'Блогеры и рекламные аккаунты',hint:'Название / @аккаунт / ссылка и краткий комментарий'}],
    },
    {
      title: 'Референсы успешных видео',
      help: 'Добавь ролики, которые считаешь удачными. Можно из других ниш — если важны формат, подача или механика.',
      fields:[{key:'video_references',label:'Ссылки на видео и комментарии',hint:'https://www.tiktok.com/… — нравится подача\nhttps://www.youtube.com/shorts/… — хороший хук'}],
    },
    {
      title: 'Готовые исследования',
      help: 'Последний шаг: прикрепи готовые исследования рынка, ASO, конкурентов, трендов или рекламных форматов.',
      files:true, fields:[],
    },
  ];
  let draft = Object.fromEntries(TEXT_KEYS.map(key => [key,'']));
  let step = 0;
  let uploads = [];
  let saving = false;
  let editingProject = null;
  const PRODUCT_TYPES = [
    ['', 'Не выбран'], ['mobile_app', 'Мобильное приложение'],
    ['digital_service', 'Цифровой сервис'], ['physical_product', 'Физический товар'],
    ['service', 'Услуга / консультация'], ['course', 'Курс / обучение'],
    ['no_product', 'Продукта пока нет'], ['other', 'Другое']
  ];
  const MAX_FILES = 10;
  const MAX_FILE_SIZE = 10 * 1024 * 1024;
  const MAX_TOTAL_SIZE = 24 * 1024 * 1024;
  const ALLOWED = /\.(pdf|doc|docx|txt|md|csv|xls|xlsx|ppt|pptx|png|jpe?g)$/i;

  async function request(url, payload) {
    const response = await fetch(url, payload === undefined ? {cache:'no-store'} : {
      method:'POST',
      headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(payload)
    });
    let body;
    try {body = await response.json();} catch (_) {throw Error('Не удалось прочитать ответ локального сервера.');}
    if (!response.ok || !body.ok) throw Error(body.error || 'Ошибка '+response.status);
    return body;
  }

  function saveDraft() {
    try {localStorage.setItem(DRAFT_KEY, JSON.stringify({draft,step,editingProject}));}
    catch (_) { /* Optional session persistence. */ }
  }

  function restoreDraft() {
    try {
      const value = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
      if (value.draft && typeof value.draft === 'object') {
        for (const key of TEXT_KEYS) {
          if (typeof value.draft[key] === 'string') draft[key] = value.draft[key].slice(0,12000);
        }
      }
      if (Number.isInteger(value.step) && value.step >= 0 && value.step < STEPS.length) step=value.step;
      if (value.editingProject && /^[a-f0-9]{32}$/.test(value.editingProject.id) &&
          Number.isInteger(value.editingProject.revision)) editingProject=value.editingProject;
    } catch (_) { /* Ignore broken draft. */ }
  }

  function navigate() {
    document.dispatchEvent(new Event('cm-projects-open'));
  }

  function show(section) {
    el('projectOverview').hidden = section !== 'overview';
    el('projectWizard').hidden = section !== 'wizard';
    el('projectDetails').hidden = section !== 'details';
    el('projectReview').hidden = section !== 'review';
    el('projectPipeline').hidden = section !== 'pipeline';
    el('projectCost').hidden = section !== 'cost';
    el('projectRun').hidden = section !== 'run';
  }

  function field(label, hint, value, multiline, key) {
    const wrap = document.createElement('div');
    wrap.className = 'project-field';
    const caption = document.createElement('label');
    caption.htmlFor='project-input-'+key;
    caption.textContent=label;
    const input = document.createElement(key === 'product_type' ? 'select' : (multiline === false ? 'input' : 'textarea'));
    input.id='project-input-'+key;
    if (key === 'product_type') {
      for (const [id,title] of PRODUCT_TYPES) {
        const option = document.createElement('option');
        option.value=id; option.textContent=title; input.append(option);
      }
    } else {
      input.maxLength= key === 'name' ? 120 : 12000;
      input.placeholder=hint;
      if (multiline !== false) input.rows=6;
    }
    input.value=value || '';
    input.addEventListener('input', () => {
      draft[key] = input.value;
      saveDraft();
    });
    wrap.append(caption,input);
    return wrap;
  }

  function showError(message='') {
    const error=el('projectStepError');
    error.textContent=message;
    error.hidden=!message;
  }

  function formatSize(bytes) {
    return bytes >= 1024 * 1024
      ? (bytes / (1024*1024)).toFixed(1) + ' МБ'
      : Math.max(1,Math.round(bytes/1024)) + ' КБ';
  }

  function renderUploads(target) {
    const list=document.createElement('div');
    list.className='project-upload-list';
    for (const [index,file] of uploads.entries()) {
      const row=document.createElement('div');
      row.className='project-upload-row';
      const name=document.createElement('span');
      name.textContent=file.name+' · '+formatSize(file.size);
      const remove=document.createElement('button');
      remove.type='button'; remove.className='project-text-button';
      remove.textContent='Удалить';
      remove.setAttribute('aria-label','Удалить '+file.name);
      remove.addEventListener('click', () => {
        uploads.splice(index,1);
        renderStep();
      });
      row.append(name,remove);
      list.append(row);
    }
    if (!uploads.length) {
      const hint=document.createElement('p');
      hint.className='project-files-hint';
      hint.textContent='Файлы пока не выбраны. Этот шаг можно пропустить.';
      list.append(hint);
    }
    target.append(list);
  }

  function addFiles(files) {
    const items=Array.from(files);
    if (uploads.length + items.length > MAX_FILES) {
      showError('Можно добавить не больше 10 документов.');
      return;
    }
    let total=uploads.reduce((sum,file)=>sum+file.size,0);
    for (const file of items) {
      if (!ALLOWED.test(file.name)) {showError('Неподдерживаемый тип: '+file.name);return;}
      if (!file.size || file.size > MAX_FILE_SIZE) {showError('Файл должен быть не больше 10 МБ: '+file.name);return;}
      total+=file.size;
      if (total > MAX_TOTAL_SIZE) {showError('Общий размер файлов не должен превышать 24 МБ.');return;}
    }
    uploads.push(...items);
    renderStep();
  }

  function renderStep() {
    show('wizard');
    showError('');
    const config=STEPS[step];
    el('projectStepCounter').textContent='Шаг '+(step+1)+' из '+STEPS.length;
    el('projectStepTitle').textContent=config.title;
    el('projectStepHelp').textContent=config.help;
    const progress=el('projectStepProgress');
    progress.replaceChildren();
    STEPS.forEach((_,index)=>{
      const part=document.createElement('span');
      part.className='project-progress-segment'+(index<=step?' filled':'');
      part.setAttribute('aria-hidden','true');
      progress.append(part);
    });
    const root=el('projectStepFields');
    root.replaceChildren();
    for (const item of config.fields) {
      root.append(field(item.label,item.hint,draft[item.key],item.multiline,item.key));
    }
    if (config.files && editingProject) {
      const hint=document.createElement('p');
      hint.className='project-files-hint';
      hint.textContent='Ранее загруженные документы сохраняются. Добавление новых файлов при редактировании пока не поддерживается.';
      root.append(hint);
    } else if (config.files) {
      const label=document.createElement('label');
      label.className='project-file-drop';
      label.htmlFor='projectResearchFiles';
      const title=document.createElement('strong');
      title.textContent='Выбрать исследования и документы';
      const extra=document.createElement('span');
      extra.textContent='PDF, Word, Excel, PowerPoint, TXT, CSV или изображения';
      const input=document.createElement('input');
      input.type='file';input.id='projectResearchFiles';input.multiple=true;
      input.accept='.pdf,.doc,.docx,.txt,.md,.csv,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg';
      input.addEventListener('change',()=>addFiles(input.files));
      label.append(title,extra,input);
      root.append(label);
      const limits=document.createElement('p');
      limits.className='project-files-hint';
      limits.textContent='До 10 файлов, каждый до 10 МБ, суммарно до 24 МБ. Файлы хранятся только на этом компьютере.';
      root.append(limits);
      renderUploads(root);
    }
    el('projectBack').disabled=step===0 || saving;
    el('projectNext').disabled=saving;
    el('projectNext').textContent=saving?'Сохраняем проект…':
      step===STEPS.length-1?(editingProject?'Сохранить изменения ✓':'Создать проект ✓'):'Подтвердить и продолжить →';
    saveDraft();
  }

  let reviewProjectId=null;
  let reviewSnapshot=null;
  let confirming=false;

  function tbText(parent, label, value) {
    const item=document.createElement('div');
    item.className='tb-review-item';
    const labelEl=document.createElement('small');
    labelEl.textContent=label;
    const content=document.createElement('p');
    content.textContent=value || 'Не указано';
    item.append(labelEl,content);
    parent.append(item);
  }

  function clearReviewError(message='') {
    el('projectReviewErrors').textContent=message;
    el('projectReviewErrors').hidden=!message;
  }

  function displayIntake(source) {
    reviewSnapshot=source;
    show('review');
    el('projectReviewMeta').textContent=source.project_name+
      ' · Версия анкеты '+source.profile_revision+
      (source.confirmed?' · Подтверждена':' · Ожидает подтверждения');
    const fields=el('projectReviewFields');
    fields.replaceChildren();
    const profile=source.profile||{};
    const product=(PRODUCT_TYPES.find(item=>item[0]===profile.product_type)||['','Не выбран'])[1];
    tbText(fields,'Ниша',profile.niche);
    tbText(fields,'Тип продукта',product);
    tbText(fields,'Функции продукта',profile.product_functions);
    tbText(fields,'Проблемы аудитории',profile.audience_problems);
    tbText(fields,'Общее описание',profile.product_description);
    tbText(fields,'Документы исследования',String((source.research_documents||[]).length)+' шт.');
    const hints=el('projectReviewChecklist');
    hints.replaceChildren();
    for(const issue of source.blockers||[]){
      const text=document.createElement('p');
      text.className='tb-check-error';
      text.textContent='• '+issue.message;
      hints.append(text);
    }
    if(source.database_blocker){
      const text=document.createElement('p');
      text.className='tb-check-error';
      text.textContent='• '+source.database_blocker;
      hints.append(text);
    }
    if(!source.blockers.length && !source.database_blocker){
      const status=document.createElement('p');
      status.className='tb-check-success';
      status.textContent=source.confirmed
        ? '✓ Эта версия анкеты подтверждена и готова к настройке моделей.'
        : '✓ Данные подходят для Taxonomy Builder. Проверь их и подтверди.';
      hints.append(status);
    }
    const button=el('projectReviewConfirm');
    button.disabled=!source.can_confirm || confirming;
    button.textContent=source.confirmed?'Открыть Pipeline Studio →':'Подтвердить анкету →';
    clearReviewError();
    window.scrollTo({top:0,behavior:'smooth'});
  }

  async function openReview(projectId) {
    reviewProjectId=projectId;
    navigate();show('review');
    el('projectReviewMeta').textContent='Проверяем актуальную версию анкеты…';
    el('projectReviewFields').replaceChildren();
    el('projectReviewChecklist').replaceChildren();
    el('projectReviewConfirm').disabled=true;
    clearReviewError();
    try {
      const response=await request('/api/projects/'+encodeURIComponent(projectId)+'/taxonomy-builder/intake');
      if(reviewProjectId!==projectId) return;
      displayIntake(response);
    }catch(error){
      if(reviewProjectId!==projectId) return;
      reviewSnapshot=null;
      clearReviewError(error.message||'Не удалось проверить анкету.');
    }
  }

  async function editReviewedProject() {
    if(!reviewProjectId) return;
    try {
      const response=await request('/api/projects/'+encodeURIComponent(reviewProjectId));
      editProject(response.project);
    }catch(error){
      await openReview(reviewProjectId);
      clearReviewError(error.message||'Не удалось загрузить проект.');
    }
  }

  async function openPipeline() {
    if(!reviewProjectId) return;
    const id=reviewProjectId;
    try {
      const source=await request('/api/projects/'+encodeURIComponent(id)+'/taxonomy-builder/intake');
      if(reviewProjectId!==id) return;
      if(!source.pipeline_studio_available){
        displayIntake(source);
        clearReviewError('Версия анкеты требует подтверждения, прежде чем открыть Pipeline.');
        return;
      }
      reviewSnapshot=source;
      navigate();show('pipeline');
      el('projectPipelineState').textContent='Загружаем сохранённые настройки Pipeline…';
      document.dispatchEvent(new CustomEvent('cm-taxonomy-pipeline-open',{
        detail:{projectId:id}
      }));
      window.scrollTo({top:0,behavior:'smooth'});
    }catch(error){
      await openReview(id);
      clearReviewError(error.message||'Не удалось открыть Pipeline.');
    }
  }

  async function confirmReviewedIntake() {
    if(confirming || !reviewSnapshot || !reviewSnapshot.can_confirm) return;
    if(reviewSnapshot.confirmed){await openPipeline();return;}
    const id=reviewProjectId;
    confirming=true;
    el('projectReviewConfirm').disabled=true;
    clearReviewError();
    try {
      const result=await request('/api/projects/'+encodeURIComponent(id)+'/taxonomy-builder/intake/confirm',{
        expected_revision:reviewSnapshot.profile_revision,
        expected_hash:reviewSnapshot.profile_hash
      });
      if(reviewProjectId!==id)return;
      reviewSnapshot=result;
      await openPipeline();
    }catch(error){
      if(reviewProjectId!==id)return;
      await openReview(id);
      clearReviewError(error.message||'Не удалось подтвердить анкету.');
    }finally{
      confirming=false;
      if(reviewSnapshot && reviewProjectId===id)
        el('projectReviewConfirm').disabled=!reviewSnapshot.can_confirm;
    }
  }

  function canLeaveStudio() {
    if(!el('projectRun').hidden &&
       window.ContentMachineTaxonomyRun?.confirmLeave()===false)return false;
    return el('projectPipeline').hidden ||
      window.ContentMachineTaxonomyStudio?.confirmLeave()!==false;
  }

  function createNew() {
    if(!canLeaveStudio())return;
    editingProject=null;
    draft=Object.fromEntries(TEXT_KEYS.map(key=>[key,'']));
    step=0; uploads=[];
    navigate();
    renderStep();
    window.scrollTo({top:0,behavior:'smooth'});
    const first=el('projectStepFields').querySelector('input:not([type=file]), textarea');
    if (first) first.focus();
  }

  function editProject(project) {
    editingProject={id:project.id,revision:project.profile_revision || 1};
    draft=Object.fromEntries(TEXT_KEYS.map(key=>[key,typeof project[key] === 'string' ? project[key] : '']));
    step=0; uploads=[];
    navigate(); renderStep();
    window.scrollTo({top:0,behavior:'smooth'});
  }

  async function toBase64(file) {
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onload=()=>{
        const contents=String(reader.result||'');
        const comma=contents.indexOf(',');
        if (comma<0) reject(Error('Не удалось прочитать '+file.name));
        else resolve(contents.slice(comma+1));
      };
      reader.onerror=()=>reject(Error('Не удалось прочитать '+file.name));
      reader.readAsDataURL(file);
    });
  }

  async function finish() {
    if (saving) return;
    saving=true; renderStep();
    try {
      let result;
      if (editingProject) {
        result=await request('/api/projects/'+encodeURIComponent(editingProject.id)+'/update',
          {...draft,expected_revision:editingProject.revision});
      } else {
        const files=[];
        for(const file of uploads) {
          files.push({name:file.name,data_base64:await toBase64(file)});
        }
        result=await request('/api/projects',{...draft,research_files:files});
      }
      try {localStorage.removeItem(DRAFT_KEY);} catch (_) {}
      draft=Object.fromEntries(TEXT_KEYS.map(key=>[key,'']));
      step=0;
      uploads=[];
      editingProject=null;
      await refreshList();
      await openReview(result.project.id);
    } catch (error) {
      showError(error.message||'Не получилось сохранить проект.');
    } finally {
      saving=false;
      el('projectNext').disabled=false;
      el('projectNext').textContent=editingProject?'Сохранить изменения ✓':'Создать проект ✓';
    }
  }

  function textLine(parent,label,value) {
    const row=document.createElement('div');
    row.className='project-detail-field';
    const title=document.createElement('h3');
    title.textContent=label;
    const text=document.createElement('p');
    text.textContent=value || 'Не указано';
    row.append(title,text);
    parent.append(row);
  }

  function renderDetails(project) {
    navigate();
    show('details');
    el('projectDetailsTitle').textContent=project.name;
    const root=el('projectDetailsFields');
    root.replaceChildren();
    const edit=document.createElement('button');
    edit.type='button'; edit.className='button settings-secondary';
    edit.textContent='Редактировать проект';
    edit.addEventListener('click',()=>editProject(project));
    root.append(edit);
    const begin=document.createElement('button');
    begin.type='button';
    begin.className='button primary tb-detail-action';
    begin.textContent='Продолжить к Taxonomy Builder →';
    begin.addEventListener('click',()=>openReview(project.id));
    root.append(begin);
    if (window.ContentMachineResearchFiles)
      window.ContentMachineResearchFiles.render(root,project);
    if (window.ContentMachineProjectSources)
      window.ContentMachineProjectSources.render(root,project);
    if (window.ContentMachineEvidenceSources)
      window.ContentMachineEvidenceSources.mountProfile(root,project.id);
    const status=document.createElement('p');
    status.className='project-files-hint';
    status.textContent='Ревизия профиля: '+(project.profile_revision||1)+
      (project.profile_db_sync && project.profile_db_sync!=='synced' && project.profile_db_sync!=='disabled'
        ? ' · PostgreSQL: '+project.profile_db_sync : '');
    root.append(status);
    STEPS.forEach(config=>{
      if(config.files) {
        const files=project.files||[];
        const group=document.createElement('div');
        group.className='project-detail-field';
        const heading=document.createElement('h3');
        heading.textContent='Готовые исследования';
        group.append(heading);
        if (!files.length) {
          const empty=document.createElement('p');
          empty.textContent='Файлы не прикреплены';
          group.append(empty);
        }
        for(const [index,file] of files.entries()) {
          const link=document.createElement('a');
          link.className='project-file-download';
          link.href='/api/projects/'+encodeURIComponent(project.id)+'/files/'+index;
          link.download=file.name;
          link.textContent='↓ '+file.name+' · '+formatSize(file.size);
          group.append(link);
        }
        root.append(group);
      }else{
        for(const item of config.fields) {
          if (item.key==='name') continue;
          textLine(root,item.label,item.key==='product_type'
            ? (PRODUCT_TYPES.find(type=>type[0]===project[item.key])||['','Не выбран'])[1]
            : project[item.key]);
        }
      }
    });
    window.scrollTo({top:0,behavior:'smooth'});
  }

  async function openProject(id) {
    if(!canLeaveStudio())return;
    navigate();
    try {
      const response=await request('/api/projects/'+encodeURIComponent(id));
      renderDetails(response.project);
    } catch(error) {
      show('overview');
      const notice=el('projectOverviewError');
      notice.textContent='Не удалось открыть проект: '+error.message;
      notice.hidden=false;
    }
  }

  async function refreshList() {
    const root=el('projectSidebarList');
    try {
      const response=await request('/api/projects');
      root.replaceChildren();
      const projects=response.projects||[];
      if (!projects.length) {
        const empty=document.createElement('p');
        empty.className='project-sidebar-empty';
        empty.textContent='Пока нет проектов';
        root.append(empty);
      }
      for(const project of projects){
        const button=document.createElement('button');
        button.type='button';button.className='project-sidebar-entry';
        button.textContent=project.name;
        button.title=project.name;
        button.addEventListener('click',()=>openProject(project.id));
        root.append(button);
      }
      const overviewList=el('projectOverviewList');
      overviewList.replaceChildren();
      for(const project of projects) {
        const card=document.createElement('button');
        card.type='button';
        card.className='project-overview-entry';
        const headline=document.createElement('strong');
        headline.textContent=project.name;
        const meta=document.createElement('span');
        const created=new Date(project.created_at);
        meta.textContent=(project.niche||'Ниша не указана')+
          ' · '+(Number.isNaN(created.valueOf()) ? 'Сохранён' : created.toLocaleDateString('ru-RU'))+
          (project.file_count ? ' · Документов: '+project.file_count : '');
        const arrow=document.createElement('b');
        arrow.textContent='→';
        card.append(headline,meta,arrow);
        card.addEventListener('click',()=>openProject(project.id));
        overviewList.append(card);
      }
      el('projectOverviewError').hidden=true;
      el('projectOverview').querySelector('h2').textContent=projects.length
        ? 'Твои проекты сохранены' : 'Создай свой первый проект';
      el('projectOverview').querySelector('p').textContent=projects.length
        ? 'Выбери проект в левом меню или создай новый. Исследование и генерация пока отключены.'
        : 'Добавь исходную информацию о продукте и рынке. Исследование и генерация пока не запускаются.';
    } catch (_) {
      root.replaceChildren();
      const error=document.createElement('p');
      error.className='project-sidebar-empty';
      error.textContent='Нет связи с сервером';
      root.append(error);
    }
  }

  el('projectReviewBack').addEventListener('click',()=>openProject(reviewProjectId));
  el('projectReviewEdit').addEventListener('click',editReviewedProject);
  el('projectReviewConfirm').addEventListener('click',confirmReviewedIntake);
  document.addEventListener('cm-taxonomy-cost-show',event=>{
    if(event.detail?.projectId===reviewProjectId){navigate();show('cost');}
  });
  document.addEventListener('cm-taxonomy-run-show',event=>{
    if(event.detail?.projectId===reviewProjectId){navigate();show('run');}
  });
  document.addEventListener('cm-taxonomy-run-return',event=>{
    if(event.detail?.projectId===reviewProjectId)window.ContentMachineTaxonomyCost?.open(reviewProjectId);
  });
  document.addEventListener('cm-taxonomy-cost-return',event=>{
    if(event.detail?.projectId===reviewProjectId)openPipeline();
  });
  el('projectPipelineBack').addEventListener('click',()=>{
    if(canLeaveStudio())openReview(reviewProjectId);
  });
  el('projectPipelineEdit').addEventListener('click',()=>{
    if(canLeaveStudio())editReviewedProject();
  });
  el('projectPipelineProjects').addEventListener('click',()=>{
    if(!canLeaveStudio())return;
    navigate();show('overview');refreshList();
  });
  el('projectsNav').addEventListener('click',createNew);
  el('projectOverviewCreate').addEventListener('click',createNew);
  el('projectWizardClose').addEventListener('click',()=>{
    saveDraft(); navigate(); show('overview');
  });
  el('projectDetailsBack').addEventListener('click',()=>{
    navigate();show('overview');refreshList();
  });
  el('projectBack').addEventListener('click',()=>{
    if (step>0 && !saving) {step--;renderStep();}
  });
  el('projectStepForm').addEventListener('submit',event=>{
    event.preventDefault();
    if (saving) return;
    if (step<STEPS.length-1) {
      step++;renderStep();
      window.scrollTo({top:0,behavior:'smooth'});
    } else {
      finish();
    }
  });
  restoreDraft();
  refreshList();
})();