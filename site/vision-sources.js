'use strict';
/* Stage 08: explicit local prep then priced Vision; no automatic model calls. */
(() => {
  const el=(tag,text)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=text;return x;};
  const button=(text,fn)=>{const x=el('button',text);x.type='button';x.className='project-text-button';x.addEventListener('click',fn);return x;};
  const path=(pid,sid)=>'/api/projects/'+encodeURIComponent(pid)+'/sources/'+encodeURIComponent(sid)+'/vision';
  async function api(url,data){
    const response=await fetch(url,data===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(data)});
    let body;try{body=await response.json();}catch(_){throw Error('Некорректный ответ сервера.');}
    if(!response.ok||!body.ok)throw Error(body.error||'HTTP '+response.status);
    return body;
  }
  function mount(root,pid,source){
    if(source.link_kind!=='video')return;
    const base=path(pid,source.source_id);
    const block=el('div');block.className='project-detail-field';
    block.append(el('strong','Vision: кадры, OCR и сцены'));
    const explanation=el('p','Подготовка кадров и OCR — локально. Анализ Sonnet 4.6 — только после сметы и подтверждения.');
    explanation.className='project-files-hint';block.append(explanation);
    const status=el('p');status.setAttribute('role','status');block.append(status);
    const list=el('div');block.append(list);
    let busy=false;
    async function perform(fn){if(busy)return;busy=true;status.textContent='Обработка запроса…';
      try{await fn();await refresh();}catch(e){status.textContent=e.message||'Ошибка Vision';}finally{busy=false;}}
    const prepare=button('Подготовить кадры и OCR',()=>perform(async()=>{
      const r=await api(base+'/prepare',{config:{max_frames:8,early_focus:true,ocr:true}});
      status.textContent=r.created?'Добавлено задание подготовки.':'Подготовка уже зарегистрирована: '+r.state;
    }));block.append(prepare);
    block.append(button('Обновить Vision',()=>perform(refresh)));
    async function show(job){
      const r=await api(base+'/jobs/'+encodeURIComponent(job.job_id));
      const detail=el('div');detail.className='project-files-hint';
      if(r.result){
        detail.append(el('p','Схема: '+r.result.schema_version+' · статус: '+r.result.status));
        const parts=r.result.scenes||[];
        for(const item of parts.slice(0,12)){
          const summary=item.visual_description||'Выбранные кадры сцены: '+(item.frame_ids||[]).join(', ');
          detail.append(el('p',(item.scene_id||'сцена')+' · '+(item.start_ms||0)+'–'+(item.end_ms||0)+' мс · '+summary));
        }
        detail.append(el('p','Покрытие: отдельные выбранные кадры, не все кадры ролика.'));
      }else{detail.textContent='Результат ещё не готов.';}
      list.prepend(detail);
    }
    async function refresh(){
      const data=await api(base+'/jobs');list.replaceChildren();
      if(!data.jobs.length){list.append(el('p','Заданий Vision пока нет.'));return;}
      for(const job of data.jobs){
        const row=el('div');row.className='project-upload-row';
        row.append(el('span',job.mode+' · '+job.state+' · '+job.job_id.slice(0,8)));
        row.append(button('Результат',()=>perform(()=>show(job))));
        if(job.mode==='prepare'&&['completed','partial'].includes(job.state)){
          row.append(button('Смета Vision AI',()=>perform(async()=>{
            const q=await api(base+'/quote',{prepare_job_id:job.job_id});
            const message='Отправить до 8 кадров модели '+q.model+'? Плановая стоимость $'+q.estimate_usd+
              '. Это НЕ гарантированный лимит итогового счёта. Запрос платный и не будет автоматически повторён. Подтвердить?';
            if(!window.confirm(message)){status.textContent='Vision AI не запускался.';return;}
            const a=await api(base+'/analyze',{quote_id:q.quote_id,approved_estimate_usd:q.estimate_usd});
            status.textContent='Платный анализ зарегистрирован: '+a.job_id;
          })));
        }
        if(job.mode==='prepare'&&['failed','unavailable'].includes(job.state)){
          row.append(button('Повтор подготовки',()=>perform(()=>api(base+'/jobs/'+job.job_id+'/retry',{}))));
        }
        list.append(row);
      }
    }
    root.append(block);
    refresh().catch(e=>{status.textContent='Vision сейчас недоступен: '+e.message;});
  }
  window.ContentMachineVisionSources={mount};
})();
