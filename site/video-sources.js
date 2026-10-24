'use strict';
/* PSI stage 06: no download on render; user explicitly starts a durable job. */
(() => {
  const mk=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  async function api(url,payload){
    const options=payload===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(payload)};
    const resp=await fetch(url,options);let result;
    try{result=await resp.json();}catch(_){throw Error('Некорректный ответ сервера.');}
    if(!resp.ok||!result.ok)throw Error(result.error||'Ошибка '+resp.status);
    return result;
  }
  function mount(item,pid,source){
    if(source.link_kind!=='video'||!['tiktok','youtube','instagram'].includes(source.platform))return;
    const base='/api/projects/'+encodeURIComponent(pid)+'/sources/'+encodeURIComponent(source.source_id)+'/video';
    const box=mk('div');box.className='project-files-hint';
    const state=mk('p','Видео ещё не запрошено. Загрузка запускается вручную.');state.setAttribute('role','status');
    const start=mk('button','Получить видео');start.type='button';start.className='project-text-button';
    const refresh=mk('button','Повторно проверить источник');refresh.type='button';refresh.className='project-text-button';
    const history=mk('button','Задания');history.type='button';history.className='project-text-button';
    const list=mk('div');box.append(state,start,refresh,history,list);item.append(box);
    let busy=false;
    async function run(payload){if(busy)return;busy=true;start.disabled=refresh.disabled=true;
      try{const r=await api(base+'/start',payload);state.textContent='Задание '+r.state+' · '+r.job_id.slice(0,8)+' — обработка в локальном worker';await load();}
      catch(e){state.textContent=e.message;}finally{busy=false;start.disabled=refresh.disabled=false;}}
    start.addEventListener('click',()=>run({}));
    refresh.addEventListener('click',()=>{if(window.confirm('Создать новое задание для этого видео? Сначала будет проверен локальный кэш.'))run({refresh:true});});
    async function load(){
      const r=await api(base+'/jobs');list.replaceChildren();
      for(const j of r.jobs){
        const line=mk('div');line.append(mk('span',j.platform+' '+j.native_id+' · '+j.state+' · '+j.phase+(j.progress===null?'':' '+j.progress+'%')));
        const detail=mk('button','Результат');detail.type='button';detail.className='project-text-button';
        detail.addEventListener('click',async()=>{
          try{
            const data=await api(base+'/jobs/'+j.job_id),m=data.result||{};
            state.textContent=data.state+
              (data.unavailable_reason?' · '+data.unavailable_reason:'')+
              (data.error_code?' · '+data.error_code:'')+
              (m.video_sha256?' · SHA: '+m.video_sha256.slice(0,16):'');
            const previous=line.querySelector('[data-video-preview]');
            if(previous){previous.remove();return;}
            const view=mk('div');view.className='psi-video-preview';view.dataset.videoPreview='1';
            view.append(mk('p','Состояние: '+data.state+
              ' · '+(m.platform||j.platform)+' · '+(m.byte_size?
                (m.byte_size/1024/1024).toFixed(2)+' MB':'размер неизвестен')));
            if(data.state==='completed'&&m.availability_status==='available'){
              const video=mk('video');video.controls=true;video.preload='metadata';
              video.setAttribute('aria-label','Локальный MP4 из подтверждённого архива');
              video.src=base+'/jobs/'+j.job_id+'/asset';view.append(video);
            }else{
              view.append(mk('p','MP4-превью доступно только для завершённого задания с проверенным локальным asset.'));
            }
            const metadata=m.metadata||{};
            const facts={};
            for(const field of ['title','duration','width','height','fps','uploader','upload_date']){
              if(['string','number'].includes(typeof metadata[field]))
                facts[field]=String(metadata[field]).slice(0,500);
            }
            view.append(mk('pre',JSON.stringify({
              platform:m.platform,native_id:m.native_id,video_sha256:m.video_sha256,
              metadata:facts,available:m.availability_status,cache_reused:data.cache_reused
            },null,2)));
            line.append(view);
          }catch(e){state.textContent=e.message;}
        });line.append(detail);
        if(['queued','processing'].includes(j.state)){
          const stop=mk('button','Отменить');stop.type='button';stop.className='project-text-button';
          stop.addEventListener('click',async()=>{try{await api(base+'/jobs/'+j.job_id+'/cancel',{});await load();}catch(e){state.textContent=e.message;}});line.append(stop);
        }else if(['failed','unavailable','cancelled'].includes(j.state)){
          const retry=mk('button','Повторить');retry.type='button';retry.className='project-text-button';
          retry.addEventListener('click',async()=>{try{await api(base+'/jobs/'+j.job_id+'/retry',{});await load();}catch(e){state.textContent=e.message;}});line.append(retry);
        }
        list.append(line);
      }
      if(!r.jobs.length)list.append(mk('small','Нет заданий.'));
    }
    history.addEventListener('click',()=>load().catch(e=>{state.textContent=e.message;}));
    // Persisted jobs are restored by GET after a browser reload; no download on render.
    load().catch(e=>{state.textContent='История видео недоступна: '+e.message;});
  }
  window.ContentMachineVideoSources={mount};
})();
