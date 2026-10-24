'use strict';
/* PSI Stage 09: deterministic fusion of already acquired, project-scoped media.
   No paid model calls; do not interpret missing classifications as false. */
(() => {
  const el=(tag,text)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=text;return x;};
  const button=(text,fn)=>{const x=el('button',text);x.type='button';x.className='project-text-button';x.addEventListener('click',fn);return x;};
  const path=(pid,sid)=>'/api/projects/'+encodeURIComponent(pid)+'/sources/'+encodeURIComponent(sid)+'/fusion';
  async function api(url,body){
    const response=await fetch(url,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(body)});
    let data;try{data=await response.json();}catch(_){throw Error('Некорректный ответ сервера.');}
    if(!response.ok||!data.ok)throw Error(data.error||'Ошибка '+response.status);
    return data;
  }
  function mount(parent,pid,source){
    if(source.link_kind!=='video'||source.ingestion_mode!=='video_reference')return;
    const base=path(pid,source.source_id),wrap=el('div'),notice=el('span'),listing=el('div');
    wrap.className='project-vision-source';notice.setAttribute('role','status');
    const head=el('strong','Общий таймлайн видео');wrap.append(head);
    const run=button('Собрать Timeline Fusion',async()=>{
      run.disabled=true;notice.textContent='Создаём локальную задачу…';
      try{const result=await api(base+'/start',{});notice.textContent='Задание '+result.state;await refresh();}
      catch(e){notice.textContent=e.message;}finally{run.disabled=false;}
    });
    const refreshButton=button('Обновить',()=>refresh().catch(e=>{notice.textContent=e.message;}));
    wrap.append(run,refreshButton,notice,listing);parent.append(wrap);
    async function refresh(){
      const data=await api(base+'/jobs');listing.replaceChildren();
      if(!data.jobs.length){listing.append(el('span','Таймлайн ещё не собран. Нужен результат транскрипции или подготовки Vision.'));return;}
      for(const job of data.jobs.slice(0,8)){
        const row=el('div');row.className='project-upload-row';
        row.append(el('span','Fusion · '+job.state+' · '+job.created_at));
        if(job.state==='failed'||job.state==='unavailable')row.append(button('Повторить',async()=>{
          try{await api(base+'/jobs/'+job.job_id+'/retry',{});await refresh();}catch(e){notice.textContent=e.message;}
        }));
        if(job.ready)row.append(button('Открыть хронологию',async()=>{
          try{
            const result=await api(base+'/jobs/'+job.job_id+'?limit=50');
            const data=result.intelligence;
            const view=el('div');view.className='project-upload-list';
            view.append(el('p','Покрытие: '+JSON.stringify(data.coverage)+' · статус '+data.processing_status));
            for(const part of data.timeline){
              const line=el('p');line.textContent=(part.start_ms/1000).toFixed(2)+'–'+(part.end_ms/1000).toFixed(2)+' сек: '+
                [part.spoken_text?'Речь: '+part.spoken_text:null,part.on_screen_text?'OCR: '+part.on_screen_text:null,
                 part.visual_description?'Кадр (AI, требует проверки): '+part.visual_description:null].filter(Boolean).join(' · ');
              view.append(line);
            }
            if(data.has_more)view.append(el('p','Отображены первые 50 интервалов. Полная JSON-версия доступна отдельно.'));
            const download=el('a','Открыть полный JSON');download.href=base+'/jobs/'+job.job_id+'/export';download.target='_blank';download.rel='noopener noreferrer';view.append(download);
            row.append(view);
          }catch(e){notice.textContent=e.message;}
        }));
        listing.append(row);
      }
    }
    refresh().catch(e=>{notice.textContent=e.message;});
  }
  window.ContentMachineFusionSources={mount};
})();
