'use strict';
/* Stage13: explicit Context evidence review, immutable versions and RTB handoff. */
(() => {
  const make=(tag,text)=>{
    const e=document.createElement(tag);
    if(text!==undefined)e.textContent=text;
    return e;
  };
  const button=(title,fn)=>{
    const e=make('button',title);e.type='button';
    e.className='project-text-button';
    e.addEventListener('click',fn);return e;
  };
  async function api(path,payload){
    const opts=payload===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(payload)};
    const res=await fetch(path,opts);let data;
    try{data=await res.json();}catch(_){throw Error('Некорректный ответ сервера.');}
    if(!res.ok||!data.ok)throw Error(data.error||'HTTP '+res.status);
    return data;
  }
  function mount(root,pid){
    const base='/api/projects/'+encodeURIComponent(pid)+'/context';
    const wrap=make('section');wrap.className='settings-card project-context-section';
    wrap.append(make('h3','Project Context · источники, доказательства и решения'));
    wrap.append(make('p','Каждый snapshot закрепляет анкету, версии источников, анализы и Evidence. Одобрение Context не делает неподтверждённые наблюдения доказанными фактами.'));
    const status=make('p');status.setAttribute('role','status');status.className='psi-output';
    const draft=make('div'),inspector=make('div'),history=make('div'),comparison=make('div');
    const actions=make('div');actions.className='psi-actions';
    let candidate=null,historyRows=[],active=null,busy=false,epoch=0;
    const inform=value=>{status.textContent=value;};
    const counts=(container,c)=>{
      const group=make('div');group.className='psi-summary';
      for(const [label,num] of [
        ['Источников',c.sources_total],['Без Evidence',c.sources_incomplete],
        ['Analysis',c.analysis_outputs],['Evidence',c.evidence_total],
        ['Evidence утверждено',c.reviewed_approved],
        ['Ожидает проверки',c.review_pending],
        ['Отклонено',c.reviewed_rejected]]){
        const cell=make('span',label+': '+num);group.append(cell);
      }
      container.append(group);
    };
    function sources(container,context){
      const details=make('details');
      details.append(make('summary','Источники и закреплённые версии ('+
        context.source_manifest.length+')'));
      for(const x of context.source_manifest){
        const row=make('div');row.className='psi-row';
        row.append(make('strong',(x.label||x.source_type)+' · '+x.source_type));
        row.append(make('p','source_id: '+x.source_id+
          ' · версия: '+(x.source_version_id||'нет')+
          ' · status: '+x.processing_status));
        row.append(make('p','Evidence: '+x.approved_evidence_count+'/'+x.evidence_count+
          ' утверждено · хранение: '+x.sha256_kind+
          (x.excluded_reason?' · исключение: '+x.excluded_reason:'')));
        details.append(row);
      }
      container.append(details);
      const ev=make('details');
      ev.append(make('summary','Происхождение и решения по Evidence ('+
        context.evidence_manifest.length+')'));
      for(const x of context.evidence_manifest){
        const row=make('div');row.className='psi-row';
        row.append(make('strong',x.citation||x.evidence_id));
        row.append(make('p',x.kind+' · '+x.knowledge_scope+
          ' · trust: '+x.trust_label+
          ' · решение: '+(x.review_decision||'не рассмотрено')+
          ' · Evidence ID: '+x.evidence_id));
        ev.append(row);
      }
      container.append(ev);
    }
    function content(container,context,digest){
      container.replaceChildren();
      container.append(make('p','SHA256: '+digest));
      container.append(make('p','Анкета v'+context.profile.revision+
        ' · '+context.schema_version+' · политика: '+context.selection_policy));
      counts(container,context.coverage);
      if(context.warnings.length)
        container.append(make('p','Требует внимания: '+context.warnings.join(', ')));
      if(context.unavailable_or_excluded_sources.length){
        const block=make('details');block.open=true;
        block.append(make('summary','Неполные / исключённые источники ('+
          context.unavailable_or_excluded_sources.length+')'));
        for(const x of context.unavailable_or_excluded_sources)
          block.append(make('p',x.source_id+' · '+x.reason));
        container.append(block);
      }
      sources(container,context);
    }
    const previewBtn=button('1. Проверить проект и Evidence',async()=>{
      if(busy)return;
      const thisEpoch=++epoch;busy=true;previewBtn.disabled=true;
      try{
        candidate=await api(base+'/draft');
        if(thisEpoch!==epoch)return;
        content(draft,candidate.context,candidate.context_sha256);
        inform('Просмотр собран без записи и без LLM. Создание snapshot требует отдельного подтверждения.');
      }catch(e){candidate=null;inform(e.message);}
      finally{busy=false;previewBtn.disabled=false;}
    });
    const buildBtn=button('2. Создать неизменяемую версию',async()=>{
      if(busy)return;
      if(!candidate){inform('Сначала проверь актуальный состав данных.');return;}
      const expected=candidate.context_sha256;
      if(!window.confirm('Зафиксировать этот Context с точным SHA256 '+expected+'?'))return;
      busy=true;buildBtn.disabled=true;
      try{
        const snap=await api(base+'/build',{expected_context_sha256:expected});
        candidate=null;draft.replaceChildren();
        inform('Context v'+snap.context_version+
          (snap.created?' создан':' уже существует')+'. Выбери его для отдельного решения.');
        await refresh();await inspect(snap.context_id);
      }catch(e){candidate=null;draft.replaceChildren();inform(e.message);}
      finally{busy=false;buildBtn.disabled=false;}
    });
    const reload=button('Обновить версии',()=>refresh().catch(e=>inform(e.message)));
    actions.append(previewBtn,buildBtn,reload);
    wrap.append(actions,status,draft,make('h4','История и явные решения'),history,
      inspector,make('h4','Сравнение двух снимков'),comparison);
    root.append(wrap);
    async function inspect(id){
      const info=await api(base+'/snapshots/'+id);
      if(active && info.context_id!==active.context_id && busy)return;
      active=info;
      content(inspector,info.context,info.context_sha256);
      const state=make('p','Статус: '+(info.approval?.decision||'требуется решение')+
        ' · Context v'+info.context_version);
      inspector.prepend(state);
      if(info.approval?.decision==='approved'||info.approval?.decision==='approved_partial'){
        const plan=button('3. Использовать этот Context в RTB →',()=>{
          if(!window.ContentMachineContextQuote){
            inform('Экран Context-сметы ещё не подключён.');return;
          }
          window.ContentMachineContextQuote.open(pid,info.context_id,1200);
        });
        inspector.append(plan);
        inspector.append(make('p','Следующий шаг — точная проверка выбранного Evidence и смета. Никакой платный этап автоматически не запускается.'));
        return;
      }
      if(info.approval){inform('Решение по этой версии уже неизменно. Создай новую версию для нового решения.');return;}
      const excluded=info.context.unavailable_or_excluded_sources.map(x=>x.source_id).sort();
      const selector=make('select');selector.className='psi-select';
      const values=excluded.length?[
        ['approved_partial','Утвердить с явными исключениями'],
        ['rejected','Отклонить Context']
      ]:[
        ['approved','Утвердить полный Context'],
        ['rejected','Отклонить Context']
      ];
      for(const [v,title] of values){
        const o=make('option',title);o.value=v;selector.append(o);
      }
      selector.value=values[0][0];inspector.append(selector);
      const ack=make('label');ack.className='psi-check';
      const checkbox=make('input');checkbox.type='checkbox';checkbox.checked=false;
      ack.append(checkbox,make('span',excluded.length?
        'Подтверждаю ВСЕ исключения: '+excluded.join(', ')+
        '. Понимаю, что эти источники не вошли в выбранный контекст.':
        'Проверил источники, их Evidence и происхождение; понимаю, что это не независимая проверка истинности утверждений.'));
      inspector.append(ack);
      const decideBtn=button('Сохранить окончательное решение',async()=>{
        if(busy)return;
        const decision=selector.value;
        if(decision!=='rejected'&&!checkbox.checked){
          inform('Нужно явно подтвердить состав и исключения.');return;
        }
        if(!window.confirm('Сохранить неизменяемое решение '+decision+
          ' для Context v'+info.context_version+'?'))return;
        busy=true;decideBtn.disabled=true;
        try{
          const response=await api(base+'/snapshots/'+info.context_id+'/decide',{
            decision,expected_context_sha256:info.context_sha256,
            acknowledge_partial:decision==='approved_partial',
            excluded_source_ids:excluded});
          inform('Сохранено решение: '+response.decision+'.');
          await refresh();await inspect(info.context_id);
        }catch(e){inform(e.message);}
        finally{busy=false;decideBtn.disabled=false;}
      });
      inspector.append(decideBtn);
    }
    async function compare(left,right){
      const data=await api(base+'/compare?left='+encodeURIComponent(left)+
        '&right='+encodeURIComponent(right));
      const report=['Анкета изменена: '+(data.profile_changed?'да':'нет')];
      for(const kind of ['sources','analyses','evidence'])
        report.push(kind+': добавлено '+data[kind].added.length+
          ', удалено '+data[kind].removed.length+
          ', изменено '+data[kind].changed.length);
      report.push('Предупреждения до: '+data.warnings_before.join(', '));
      report.push('Предупреждения после: '+data.warnings_after.join(', '));
      const output=make('pre',report.join('\n'));output.className='psi-output';
      const old=comparison.querySelector?.('pre');if(old)old.remove();
      comparison.append(output);
    }
    async function refresh(){
      const response=await api(base+'/snapshots?limit=80');
      historyRows=response.snapshots||[];history.replaceChildren();
      for(const row of historyRows){
        const item=make('div');item.className='psi-row';
        item.append(make('strong','v'+row.context_version+' · '+
          (row.decision||'не рассмотрено')));
        item.append(make('p','Анкета v'+row.profile_revision+
          ' · Evidence: '+row.coverage.evidence_total+
          ' · неполных источников: '+row.coverage.sources_incomplete));
        item.append(button('Открыть и принять решение',()=>inspect(row.context_id).catch(e=>inform(e.message))));
        history.append(item);
      }
      if(!historyRows.length)history.append(make('p','Нет snapshots. Сначала проверь проект.'));
      comparison.replaceChildren();
      if(historyRows.length>1){
        const a=make('select'),b=make('select');
        a.className='psi-select';b.className='psi-select';
        for(const snap of historyRows){
          for(const target of [a,b]){
            const option=make('option','v'+snap.context_version);
            option.value=snap.context_id;target.append(option);
          }
        }
        b.value=historyRows[1].context_id;
        comparison.append(a,b,button('Сравнить версии',()=>
          compare(a.value,b.value).catch(e=>inform(e.message))));
      }else comparison.append(make('p','Для сравнения нужны минимум две версии.'));
    }
    refresh().catch(e=>inform(e.message));
  }
  window.ContentMachineProjectContext={mount};
})();