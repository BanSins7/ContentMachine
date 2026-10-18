'use strict';
/* Inert nine-step project intake. Local records and actual attachments, no collectors. */
(() => {
  const el = id => document.getElementById(id);
  const DRAFT_KEY = 'cm.projectWizard.draft.v1';
  const TEXT_KEYS = [
    'name', 'niche', 'goal', 'product_description', 'product_url',
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
      fields:[{key:'product_description',label:'Описание',hint:'Функциональность, аудитория, преимущества, особенности…'}],
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
    try {localStorage.setItem(DRAFT_KEY, JSON.stringify({draft,step}));}
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
    } catch (_) { /* Ignore broken draft. */ }
  }

  function navigate() {
    document.dispatchEvent(new Event('cm-projects-open'));
  }

  function show(section) {
    el('projectOverview').hidden = section !== 'overview';
    el('projectWizard').hidden = section !== 'wizard';
    el('projectDetails').hidden = section !== 'details';
  }

  function field(label, hint, value, multiline, key) {
    const wrap = document.createElement('div');
    wrap.className = 'project-field';
    const caption = document.createElement('label');
    caption.htmlFor='project-input-'+key;
    caption.textContent=label;
    const input = document.createElement(multiline === false ? 'input' : 'textarea');
    input.id='project-input-'+key;
    input.maxLength= key === 'name' ? 120 : 12000;
    input.placeholder=hint;
    input.value=value;
    if (multiline !== false) input.rows=6;
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
    if (config.files) {
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
      step===STEPS.length-1?'Создать проект ✓':'Подтвердить и продолжить →';
    saveDraft();
  }

  function createNew() {
    navigate();
    renderStep();
    window.scrollTo({top:0,behavior:'smooth'});
    const first=el('projectStepFields').querySelector('input:not([type=file]), textarea');
    if (first) first.focus();
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
      const files=[];
      for(const file of uploads) {
        files.push({name:file.name,data_base64:await toBase64(file)});
      }
      const result=await request('/api/projects',{...draft,research_files:files});
      try {localStorage.removeItem(DRAFT_KEY);} catch (_) {}
      draft=Object.fromEntries(TEXT_KEYS.map(key=>[key,'']));
      step=0;
      uploads=[];
      await refreshList();
      renderDetails(result.project);
    } catch (error) {
      showError(error.message||'Не получилось сохранить проект.');
    } finally {
      saving=false;
      el('projectNext').disabled=false;
      el('projectNext').textContent='Создать проект ✓';
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
    STEPS.forEach(config=>{
      if(config.files) {
        const files=project.files||[];
        textLine(root,'Готовые исследования',files.length?
          files.map(file=>file.name+' · '+formatSize(file.size)).join('\n'):'Файлы не прикреплены');
      }else{
        for(const item of config.fields) {
          if (item.key==='name') continue;
          textLine(root,item.label,project[item.key]);
        }
      }
    });
    window.scrollTo({top:0,behavior:'smooth'});
  }

  async function openProject(id) {
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