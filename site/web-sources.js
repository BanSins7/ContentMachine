'use strict';
/* Explicit user-confirmed paid OpenRouter web extraction. No auto fetch. */
(() => {
  const node=(tag,cls,content)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(content!==undefined)e.textContent=content;return e;};
  const api=async(url,body)=>{
    const resp=await fetch(url,body===undefined?{cache:'no-store'}:{method:'POST',
      headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(body)});
    const data=await resp.json();if(!resp.ok||!data.ok)throw Error(data.error||'HTTP '+resp.status);return data;
  };
  function mount(parent,pid,source){
    if(source.ingestion_mode!=='web_ai'||source.link_kind!=='website')return;
    const base='/api/projects/'+encodeURIComponent(pid)+'/sources/'+encodeURIComponent(source.source_id)+'/web';
    const row=node('div','project-wizard-actions');
    const status=node('small','project-files-hint','Веб-анализ не запускался');
    const start=node('button','project-text-button','Анализировать сайт (Sonnet 4.6)');start.type='button';
    const show=node('button','project-text-button','История веб-анализа');show.type='button';
    const preview=node('div','project-files-hint');
    row.append(start,show);parent.append(row,status,preview);
    let busy=false;
    async function refresh(){
      const out=await api(base+'/jobs');
      preview.replaceChildren();
      if(!out.jobs.length){status.textContent='Веб-анализ ещё не запускался';return;}
      const latest=out.jobs[0];status.textContent='Последнее задание: '+latest.state+' · учёт стоимости: '+latest.charge_state;
      for(const job of out.jobs.slice(0,8)){
        const b=node('button','project-text-button',job.state+' · '+job.created_at+' · '+(job.actual_cost_usd===null?'стоимость неизвестна':'$'+job.actual_cost_usd));
        b.type='button';b.addEventListener('click',()=>detail(job.job_id).catch(e=>{status.textContent=e.message;}));preview.append(b);
      }
    }
    async function detail(jobId){
      const d=await api(base+'/jobs/'+encodeURIComponent(jobId));
      const r=d.result;const box=node('div','project-files-hint');
      box.append(node('p','',d.state+' · версия '+(d.result_source_version_id||'нет')));
      if(r){
        box.append(node('strong','',r.title||r.requested_url));
        box.append(node('p','',r.summary));
        box.append(node('p','','Источник: модель сообщила о веб-данных; исходный HTML не был независимо подтверждён.'));
        for(const item of (r.sections||[]).slice(0,15)){
          box.append(node('strong','',item.heading),node('p','',item.text));
        }
      }else box.append(node('p','',d.error_code||'Задание ожидает выполнения отдельным worker.'));
      preview.replaceChildren(box);
    }
    start.addEventListener('click',async()=>{
      if(busy)return;busy=true;start.disabled=true;
      try{
        const quote=await api(base+'/quote',{});
        const message='Запустить платный веб-анализ '+source.canonical_url+' через '+quote.model+
          ' + OpenRouter Web Fetch?\nОценка: $'+quote.estimated_usd+ '. Это НЕ жёсткий лимит выставления счёта.\nЗапуск требует активного локального web worker.';
        if(!window.confirm(message)){status.textContent='Платный анализ не запущен.';return;}
        const job=await api(base+'/start',{quote_id:quote.quote_id,approve_estimate_usd:quote.estimated_usd});
        status.textContent='Задача '+job.job_id+' поставлена в очередь. Для обработки запусти web worker.';
        await refresh();
      }catch(e){status.textContent=e.message;}
      finally{busy=false;start.disabled=false;}
    });
    show.addEventListener('click',()=>refresh().catch(e=>{status.textContent=e.message;}));
  }
  window.ContentMachineWebSources={mount};
})();
