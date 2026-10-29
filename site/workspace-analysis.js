'use strict';
/* Read-only views for the independent analysis modules. No run can be started here. */
((root)=>{
  const panels={
    'group-a':{heading:'Измерения и сигналы',copy:'Речь, текст и медиа — с привязкой к первоисточнику.',fields:[['technical','Медиа','Параметры и сцены'],['speech','Речь','Транскрипция и темп речи'],['screen_text','Экранный текст','OCR и текстовые треки'],['audio','Аудио','Звуки и аудиособытия'],['subtitles','Субтитры','Наличие и покрытие'],['slides','Карусели','Слайды и их порядок']]},
    'group-b':{heading:'Анатомия креатива',copy:'Восемь аспектов, которые объясняют устройство контента.',fields:[['hook','Хук','Как видео захватывает внимание'],['format','Формат','Как устроена подача'],['story','История','Как развивается повествование'],['visual','Визуальный стиль','Что видит зритель'],['editing','Монтаж','Как соединяются фрагменты'],['subtitles','Субтитры','Как оформлена речь'],['cta','Призыв к действию','К чему ведёт видео'],['topic','Тематика','О чём этот креатив']]},
    'group-c':{heading:'Результат в контексте',copy:'Проверенные метрики. Без подстановок вместо отсутствующих данных.',fields:[['creator','Относительно автора','Сравнение с привычным уровнем'],['growth','Рост просмотров','Изменение за время наблюдения'],['engagement','Вовлечённость','Сглаженная оценка реакции'],['freshness','Свежесть','Возраст на момент наблюдения'],['breakout','Breakout Score','Составной показатель · без шкалы 0–100']]},
    analytics:{heading:'Одна выборка. Вся картина.',copy:'Общий снимок исследования объединяет сигналы, креатив и результат.',fields:[['a','Медиа и сигналы','Измерения Group A'],['b','Креативные признаки','Классификация Group B'],['c','Метрики результата','Расчёты Group C']]},
    insights:{heading:'От наблюдений к выводам',copy:'Предварительные паттерны требуют проверки устойчивости и независимой оценки.',fields:[['patterns','Паттерны','Повторяющиеся связи признаков и результата'],['evidence','Доказательства','Сопоставимые группы и наблюдения'],['confidence','Устойчивость','Неопределённость и ограничения'],['insights','Инсайты','Выводы после проверки']]},
  };
  const decimal=value=>typeof value==='string'&&value.length<=110&&/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(value);
  // Never paint the score of a stale, foreign, incomplete or differently scaled run.
  function score(data,pid,vid){
    const d=data?.data,s=d?.score,keys=['creator','growth','engagement','freshness'];
    if(data?.schema_version!=='group-c-http/v1'||d?.schema_version!=='group-c-studio/v1'||d.project_id!==pid||String(d.video_id)!==vid||d.state!=='valid_current'||s?.scale!=='unbounded_not_0_to_100'||s.metric_kind!=='weighted_components_not_probability'||!decimal(s.breakout_score)||!Array.isArray(s.components)||s.components.length!==4||!keys.every((key,i)=>s.components[i]?.key===key&&decimal(s.components[i]?.value)))return null;
    return Object.fromEntries([...s.components.map(c=>[c.key,c.value]),['breakout',s.breakout_score]]);
  }
  function patterns(data,pid){
    if(data?.schema_version!=='pie-pattern-explorer/v1'||data.ok!==true||data.project_id!==pid||data.view!=='patterns'||data.publication_allowed!==false||!Array.isArray(data.items)||data.items.length>80||data.items.some(p=>!p||p.can_publish!==false||p.causal_claim!==false||p.independent_holdout_confirmed!==false))throw Error('Непроверенный ответ исследования');
    return data.items.map(p=>({name:p.feature_name||p.factor_id||'Кандидат',state:p.status_label||'Предварительный результат'}));
  }
  function mount(host,{stage,pid,isCurrent}){
    const config=panels[stage.id],d=document;
    let requestEpoch=0;
    function node(tag,text,cls){const n=d.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
    const heading=node('div','','workspace-analysis-heading');
    const text=node('div','');text.append(node('span','ПРОСМОТР АНАЛИЗА','workspace-eyebrow'),node('h2',config.heading),node('p',config.copy));
    const chip=node('span','Только чтение','workspace-stage-tag');heading.append(text,chip);host.append(heading);
    const status=node('p',pid?'Результаты ещё не загружены.':'Выбери проект в верхнем меню для загрузки результатов.','workspace-analysis-status');status.setAttribute('role','status');
    const toolbar=node('div','','workspace-analysis-toolbar');
    let video=null;
    if(stage.id==='group-c'){
      const label=node('label','ID видео');video=node('input','');video.inputMode='numeric';video.placeholder='Например, 123';video.maxLength=19;label.append(video);toolbar.append(label);
    }
    const refresh=node('button','Загрузить результаты ↻','workspace-button');refresh.type='button';refresh.disabled=!pid;toolbar.append(refresh);host.append(toolbar,status);
    const grid=node('div','','workspace-analysis-grid');grid.dataset.module=stage.id;const values=new Map();
    for(const [key,title,description] of config.fields){
      const card=node('article','','workspace-signal-card');
      const top=node('div','','workspace-signal-top');top.append(node('span',title),node('span','↗'));
      const value=node('strong','—');values.set(key,value);
      card.append(top,value,node('p',description),node('small','Нет подтверждённого результата'));grid.append(card);
    }host.append(grid);
    const output=node('section','','workspace-analysis-output');
    output.append(node('div',stage.id==='insights'?'Предварительные паттерны':stage.id==='analytics'?'Снимки исследования':'Сохранённые результаты','workspace-analysis-output-title'));
    const rows=node('div','');rows.append(node('p','После загрузки здесь появятся доступные записи выбранного проекта.','workspace-analysis-output-empty'));output.append(rows);host.append(output);
    refresh.addEventListener('click',async()=>{
      const ticket=++requestEpoch;if(!pid)return;
      let path,headers={};
      const base='/api/projects/'+pid;
      if(stage.id==='group-a')path=base+'/group-a/v1/runs?limit=20';
      else if(stage.id==='group-b')path=base+'/group-b/runs?limit=20&offset=0';
      else if(stage.id==='analytics'){path=base+'/unified-analytics/snapshots?limit=10';headers={'X-UAE-Api-Version':'uae-local-http/v1'};}
      else if(stage.id==='insights')path=base+'/pie/patterns?page_size=20&min_matched_pairs=1';
      else{
        const id=video.value.trim();
        if(!/^[1-9][0-9]{0,18}$/.test(id)||BigInt(id)>9223372036854775807n){status.textContent='Укажи корректный ID видео для просмотра метрик.';return;}
        path='/api/group-c/v1/projects/'+pid+'/videos/'+id+'/studio';
      }
      refresh.disabled=true;status.textContent='Загружаем результаты…';rows.replaceChildren();
      for(const v of values.values()){v.textContent='—';v.parentElement.querySelector('small').textContent='Нет подтверждённого результата';}
      try{
        const response=await fetch(path,{cache:'no-store',headers});const data=await response.json();
        if(ticket!==requestEpoch||!isCurrent())return;
        if(!response.ok||data?.ok===false)throw Error('Данные недоступны');
        let entries=[];
        if(stage.id==='group-c'){
          const checked=score(data,pid,video.value.trim());
          if(!checked)throw Error('Нет актуального подтверждённого результата');
          for(const [key,value] of Object.entries(checked)){const v=values.get(key);v.textContent=value;v.parentElement.querySelector('small').textContent='Текущий подтверждённый результат';}
          status.textContent='Показаны подтверждённые метрики выбранного видео.';
          rows.append(node('p','Значения приведены в исходных единицах. Breakout Score не является вероятностью и не ограничен шкалой 0–100.','workspace-analysis-output-empty'));return;
        }
        if(stage.id==='insights')entries=patterns(data,pid);
        else{
          if(stage.id==='group-a'&&data.schema_version!=='ga21-runs/v1')throw Error('Несовместимый ответ');
          if(stage.id==='analytics'&&data.schema_version!=='uae-local-http/v1')throw Error('Несовместимый ответ');
          const list=stage.id==='group-a'?data.items:stage.id==='analytics'?data.snapshots:data.runs;
          if(!Array.isArray(list)||list.length>50||list.some(item=>!item||(item.project_id&&item.project_id!==pid)))throw Error('Несовместимый ответ');
          entries=list.map(item=>({name:'Запись '+String(item.run_id??item.id??'—'),state:String(item.state??item.status??'Состояние не указано')}));
        }
        status.textContent=entries.length?'Получено записей: '+entries.length:'Доступных записей пока нет.';
        if(!entries.length)rows.append(node('p','Нет доступных результатов для выбранного проекта.','workspace-analysis-output-empty'));
        for(const entry of entries){const row=node('div','','workspace-result-row');row.append(node('span',entry.name),node('span',entry.state,'workspace-result-state'));rows.append(row);}
      }catch{
        if(ticket!==requestEpoch||!isCurrent())return;
        status.textContent='Результаты пока недоступны. Проверь подключение этапа и повтори загрузку.';
        rows.replaceChildren(node('p','Данные анализа не получены. Значения не подставляются.','workspace-analysis-output-empty'));
      }finally{if(ticket===requestEpoch&&isCurrent())refresh.disabled=false;}
    });
  }
  const api=Object.freeze({mount,score,patterns});
  if(typeof module==='object'&&module.exports)module.exports=api;else root.ContentMachineWorkspaceAnalysis=api;
})(typeof window==='object'?window:globalThis);
