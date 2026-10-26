'use strict';
/* PSI Stage10: explicit materialization of pinned evidence, human review optional.
   This widget never calls an LLM, crawler, or paid API. */
(() => {
  const el=(name,text)=>{const e=document.createElement(name);if(text!==undefined)e.textContent=text;return e;};
  const btn=(label,fn)=>{const b=el('button',label);b.type='button';b.className='project-text-button';b.addEventListener('click',fn);return b;};
  async function request(url,body){
    const opts=body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(body)};
    const res=await fetch(url,opts);let data;
    try{data=await res.json();}catch(_){throw Error('Некорректный ответ сервера.');}
    if(!res.ok||!data.ok)throw Error(data.error||'HTTP '+res.status);
    return data;
  }
  function mount(parent,pid,source){
    const base='/api/projects/'+encodeURIComponent(pid),sid=source.source_id;
    const prefix=base+'/sources/'+encodeURIComponent(sid)+'/evidence';
    const wrap=el('section');wrap.className='project-evidence-source';
    const title=el('strong','Evidence / проверка источника');
    const notice=el('span');notice.setAttribute('role','status');
    const list=el('div');list.className='project-upload-list';
    const launch=btn('Извлечь проверяемые фрагменты',async()=>{
      launch.disabled=true;
      try{const v=await request(prefix+'/enqueue',{});notice.textContent='Evidence '+v.state+' (worker обрабатывает отдельно)';await refresh();}
      catch(e){notice.textContent=e.message;}finally{launch.disabled=false;}
    });
    const reload=btn('Обновить Evidence',()=>refresh().catch(e=>notice.textContent=e.message));
    wrap.append(title,launch,reload,notice,list);parent.append(wrap);
    async function refresh(){
      const [j,v]=await Promise.all([request(prefix+'/jobs'),request(prefix+'/items?limit=30')]);
      list.replaceChildren();
      if(!j.jobs.length)list.append(el('p','Нормализация ещё не запускалась. Начните с готового output документа, сайта или видео.'));
      for(const job of j.jobs.slice(0,4)){
        const row=el('div');row.className='project-upload-row';row.append(el('span','Evidence · '+job.state+' · '+job.count+' фрагментов'));
        if(['failed','unavailable'].includes(job.state))row.append(btn('Повторить',async()=>{
          try{await request(prefix+'/jobs/'+job.job_id+'/retry',{});await refresh();}catch(e){notice.textContent=e.message;}
        }));
        list.append(row);
      }
      for(const item of v.items){
        const row=el('div');row.className='project-upload-row';
        row.append(el('p',item.exact_quote));
        row.append(el('small',item.evidence_kind+' · '+item.knowledge_scope+' · '+item.trust_label+
          ' · дублей: '+item.exact_duplicate_occurrences+' · требует сравнения: '+item.relationship_flags+
          (item.is_current_version?'':' · старая версия')));
        const citation=el('code',item.citation);row.append(citation);
        row.append(btn('Проверить цитату',async()=>{
          try{const result=await request(base+'/evidence/'+item.evidence_id+'/verify');
            notice.textContent=result.valid?'Цитата подтверждена сохранённым источником. '+(result.trust_note||''):'Не подтверждена.';
          }catch(e){notice.textContent=e.message;}
        }));
        if(item.is_current_version&&!item.human_decision){
          row.append(btn('Принять фрагмент',async()=>decision(item,'approved')));
          row.append(btn('Отклонить фрагмент',async()=>decision(item,'rejected')));
        }else if(item.human_decision)row.append(el('small','Решение: '+item.human_decision));
        list.append(row);
      }
      if(v.items.length===30)list.append(el('small','Показаны первые 30 фрагментов. Остальные доступны через API пагинации.'));
    }
    async function decision(item,decision){
      if(!window.confirm('Сохранить неизменяемое решение '+decision+'? Это не делает внешний источник доказательством функций продукта.'))return;
      try{await request(base+'/evidence/'+item.evidence_id+'/review',{decision});notice.textContent='Решение записано.';await refresh();}
      catch(e){notice.textContent=e.message;}
    }
    refresh().catch(e=>notice.textContent=e.message);
  }
  function mountProfile(root,pid){
    const wrap=el('section');wrap.className='settings-card project-evidence-profile';
    wrap.append(el('h3','Evidence из анкеты'));
    wrap.append(el('p','Импорт сохраняет версии полей анкеты и ставит локальные задания. Исходную анкету не изменяет.'));
    const notice=el('span');notice.setAttribute('role','status');
    const preview=btn('Проверить поля',async()=>{
      try{let result=await request('/api/projects/'+encodeURIComponent(pid)+'/evidence/profile/import',{dry_run:true});
        notice.textContent='Поля для импорта: '+result.fields+'.';}catch(e){notice.textContent=e.message;}
    });
    const importBtn=btn('Импортировать Evidence из анкеты',async()=>{
      importBtn.disabled=true;
      try{let result=await request('/api/projects/'+encodeURIComponent(pid)+'/evidence/profile/import',{});
        notice.textContent='В очереди: '+result.jobs_queued+' · существующих: '+result.reused;
      }catch(e){notice.textContent=e.message;}finally{importBtn.disabled=false;}
    });
    wrap.append(preview,importBtn,notice);root.append(wrap);
  }
  window.ContentMachineEvidenceSources={mount,mountProfile};
})();
