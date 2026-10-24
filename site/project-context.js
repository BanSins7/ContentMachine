'use strict';
/* Stage 11: immutable project context; no auto-approval, provider calls or RTB writes. */
(() => {
  const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const button=(title,fn)=>{const e=make('button',title);e.type='button';e.className='project-text-button';e.addEventListener('click',fn);return e;};
  async function api(path,payload){
    const opts=payload===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(payload)};
    const res=await fetch(path,opts);let data;
    try{data=await res.json();}catch(_){throw Error('Некорректный ответ сервера.');}
    if(!res.ok||!data.ok)throw Error(data.error||'HTTP '+res.status);
    return data;
  }
  function mount(root,pid){
    const base='/api/projects/'+encodeURIComponent(pid)+'/context';
    const wrap=make('section');wrap.className='settings-card project-context-section';
    wrap.append(make('h3','Project Context · версии и утверждение'));
    wrap.append(make('p','Снимок закрепляет анкету, версии источников, анализы и Evidence. Не заменяет проверку внешних утверждений.'));
    const status=make('span');status.setAttribute('role','status');
    const detail=make('div'),history=make('div');
    let preview=null;
    const previewButton=button('Просмотреть кандидат',async()=>{
      previewButton.disabled=true;
      try{preview=await api(base+'/draft');const c=preview.context;
        detail.replaceChildren();detail.append(make('p','Хеш: '+preview.context_sha256));
        detail.append(make('p','Ревизия анкеты: '+c.profile.revision+' · источники: '+c.coverage.sources_total+
          ' · Evidence: '+c.coverage.evidence_total+' · без Evidence: '+c.coverage.sources_incomplete));
        detail.append(make('p','Предупреждения: '+(c.warnings.join(', ')||'нет')));
        if(c.unavailable_or_excluded_sources.length){
          detail.append(make('h4','Неполные источники (нужны для partial approval)'));
          for(const item of c.unavailable_or_excluded_sources)
            detail.append(make('p',item.source_id+' · '+item.reason));
        }
        status.textContent='Предварительный состав собран без изменения БД.';
      }catch(e){status.textContent=e.message;}finally{previewButton.disabled=false;}
    });
    const build=button('Сохранить неизменяемый snapshot',async()=>{
      if(!preview){status.textContent='Сначала просмотрите кандидат.';return;}
      if(!window.confirm('Сохранить immutable snapshot с указанными версиями источников?'))return;
      build.disabled=true;
      try{const v=await api(base+'/build',{expected_context_sha256:preview.context_sha256});
        status.textContent='Snapshot '+v.context_version+' · '+(v.created?'новый':'существующий');await refresh();}
      catch(e){status.textContent=e.message;}finally{build.disabled=false;}
    });
    const reload=button('Обновить историю',()=>refresh().catch(e=>{status.textContent=e.message;}));
    wrap.append(previewButton,build,reload,status,detail,make('h4','История контекста'),history);root.append(wrap);
    async function refresh(){
      const r=await api(base+'/snapshots?limit=30');history.replaceChildren();
      if(!r.snapshots.length){history.append(make('p','Снимков ещё нет.'));return;}
      for(const s of r.snapshots){
        const row=make('div');row.className='project-upload-row';
        row.append(make('span','Контекст v'+s.context_version+' · '+(s.decision||'ожидает решения')));
        const link=make('a','JSON');link.href=base+'/snapshots/'+s.context_id;link.target='_blank';link.rel='noopener noreferrer';row.append(link);
        row.append(button('Manifest',()=>{window.open(base+'/snapshots/'+s.context_id+'/manifest','_blank','noopener');}));
        if(!s.decision)row.append(button('Решение',async()=>{
          try{
            const v=await api(base+'/snapshots/'+s.context_id),unavailable=v.context.unavailable_or_excluded_sources;
            const decision=unavailable.length?'approved_partial':'approved';
            const excluded=unavailable.map(x=>x.source_id);
            const question=unavailable.length?
              'Утвердить НЕПОЛНЫЙ контекст? Источники: '+excluded.join(', ')+' (сохранится навсегда)':
              'Утвердить контекст? Это НЕ делает внешние утверждения фактами продукта.';
            if(!window.confirm(question))return;
            await api(base+'/snapshots/'+s.context_id+'/decide',{
              decision,expected_context_sha256:v.context_sha256,
              acknowledge_partial:unavailable.length>0,excluded_source_ids:excluded});
            status.textContent='Решение сохранено отдельно от snapshot.';await refresh();
          }catch(e){status.textContent=e.message;}
        }));
        history.append(row);
      }
    }
    refresh().catch(e=>status.textContent=e.message);
  }
  window.ContentMachineProjectContext={mount};
})();
