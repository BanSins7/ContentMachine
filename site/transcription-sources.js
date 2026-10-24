'use strict';
/* Stage 07: all actions explicit. No automatic ASR, network or paid work. */
(() => {
  const el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  async function call(url,payload){
    const response=await fetch(url,payload===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok||!data.ok)throw Error(data.error||'HTTP '+response.status);
    return data;
  }
  function button(text,fn){const b=el('button',text);b.type='button';b.className='project-text-button';b.addEventListener('click',fn);return b;}
  function mount(item,pid,source){
    if(source.link_kind!=='video'||!['tiktok','youtube','instagram'].includes(source.platform))return;
    const base='/api/projects/'+encodeURIComponent(pid)+'/sources/'+encodeURIComponent(source.source_id)+'/transcription';
    const box=el('div');box.className='project-files-hint';
    const title=el('strong','Единый транскрипт');const state=el('p','Преобразование SRT/TXT не запускалось.');state.setAttribute('role','status');
    const list=el('div');box.append(title,state);item.append(box);
    let busy=false;
    async function action(body){if(busy)return;busy=true;try{
      const j=await call(base+'/start',body);state.textContent='Задание '+j.state+' · '+j.job_id.slice(0,8)+' · ожидание локального worker';await refresh();
    }catch(e){state.textContent=e.message;}finally{busy=false;}}
    box.append(button('Собрать таймкоды из SRT',()=>action({mode:'reuse_sidecar',language:'auto'})));
    box.append(button('Распознать заново локально',()=>{if(window.confirm('Создать отдельную версию локального ASR без платных API?'))action({mode:'local_asr',language:'auto'});}));
    async function refresh(){const r=await call(base+'/jobs');list.replaceChildren();
      if(!r.jobs.length)list.append(el('small','Заданий пока нет.'));
      for(const j of r.jobs){
        const row=el('div');row.append(el('span',j.mode+' · '+j.state+' · '+j.job_id.slice(0,8)));
        row.append(button('Результат',async()=>{try{const d=await call(base+'/jobs/'+j.job_id);
          const t=d.transcript;
          state.textContent=t?'Речь: '+t.speech_status+' · '+t.segment_count+' сегментов · покрытие: '+(t.timing_coverage.fraction_of_video===null?'неизвестно':Math.round(t.timing_coverage.fraction_of_video*100)+'%')+' · '+(t.warnings||[]).join(', '):'Состояние: '+d.state+(d.error_code?' · '+d.error_code:'');
          const old=row.querySelector('[data-transcript-preview]');if(old)old.remove();
          if(t){const preview=el('div');preview.dataset.transcriptPreview='1';
            for(const s of t.segments.slice(0,12))preview.append(el('p',`${(s.start_ms/1000).toFixed(2)}–${(s.end_ms/1000).toFixed(2)}с · ${s.language} · ${s.text}`));
            row.append(preview);}
        }catch(e){state.textContent=e.message;}}));
        if(['failed','unavailable'].includes(j.state))row.append(button('Повторить',async()=>{try{await call(base+'/jobs/'+j.job_id+'/retry',{});await refresh();}catch(e){state.textContent=e.message;}}));
        list.append(row);
      }
    }
    box.append(button('История транскриптов',()=>refresh().catch(e=>{state.textContent=e.message;})),list);
  }
  window.ContentMachineUnifiedTranscription={mount};
})();
