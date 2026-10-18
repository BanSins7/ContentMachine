'use strict';
/* Separate D1 read-only catalog and manual import UI; legacy downloaders unchanged. */
(() => {
  const el = id => document.getElementById(id);
  let before = null;
  let lastRun = null;
  let busy = false;
  const request = async (path, obj) => {
    const resp = await fetch(path,obj === undefined ? {cache:'no-store'} : {
      method:'POST', headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(obj)
    });
    let data;
    try { data = await resp.json(); } catch {throw Error('Некорректный ответ сервера.');}
    if (!resp.ok || !data.ok) throw Error(data.error?.message || 'Ошибка '+resp.status);
    return data;
  };
  const notice = message => {el('discoveryNotice').textContent=message;};
  const line = (parent,label,value) => {
    const div=document.createElement('div');
    div.className='discovery-line';
    const title=document.createElement('strong');
    title.textContent=label;
    const rest=document.createElement('span');
    rest.textContent=value;
    div.append(title,rest);
    parent.appendChild(div);
    return div;
  };
  async function runs() {
    const r=await request('/api/intelligence/search-runs');
    const root=el('discoveryRuns');
    root.replaceChildren();
    if (!r.runs.length) {line(root,'Нет запусков','Добавь список URL или CSV.');return;}
    for(const run of r.runs) {
      const row=line(root,'#'+run.id+' — '+run.state,run.config.rows+' строк · '+new Date(run.created_at).toLocaleString('ru-RU'));
      const details=document.createElement('button');
      details.type='button'; details.className='button settings-secondary'; details.textContent='Детали';
      details.addEventListener('click',async()=>{
        try {
          const d=await request('/api/intelligence/search-runs?id='+run.id);
          notice('Запуск #'+d.run_id+' · '+d.state+' · новых: '+d.counts.new+
            ' · повторов: '+d.counts.duplicate+' · ошибок в строках: '+d.counts.rejected+
            ' · обработано пакетов: '+d.counts.completed);
          lastRun=run.id;
        } catch(ex) {notice(ex.message);}
      });
      const stop=document.createElement('button');
      stop.type='button';stop.className='button settings-secondary';stop.textContent='Отменить';
      stop.disabled=!['created','queued','running'].includes(run.state);
      stop.addEventListener('click',async()=>{
        try {await request('/api/intelligence/search-runs/'+run.id+'/cancel',{}); await refresh();}
        catch(ex){notice(ex.message);}
      });
      row.append(details,stop);
    }
  }
  async function videos(append=false) {
    const suffix = append && before ? '&before='+before : '';
    const r=await request('/api/intelligence/videos?limit=50'+suffix);
    const root=el('discoveryVideos');
    if(!append)root.replaceChildren();
    if(!append&&!r.videos.length)line(root,'Пусто','После импорта ролики появятся здесь.');
    for(const video of r.videos) {
      const row=line(root,video.platform+' · '+(video.title||video.platform_video_id),
                     video.published_at ? video.published_at.slice(0,10) : 'Дата публикации неизвестна');
      const a=document.createElement('a');
      a.href=video.url; a.target='_blank';a.rel='noopener noreferrer';a.textContent='Открыть ↗';
      const button=document.createElement('button');button.type='button';
      button.className='button settings-secondary';button.textContent='История';
      button.addEventListener('click',async()=>{
        try {
          const trace=await request('/api/intelligence/videos/'+video.id+'/trace');
          el('discoveryTrace').textContent=JSON.stringify(trace,null,2);
        } catch(ex) {notice(ex.message);}
      });
      row.append(a,button);
    }
    before=r.videos.length ? r.videos[r.videos.length-1].id : null;
    el('discoveryMore').disabled=r.videos.length<50;
  }
  async function refresh() {
    if(busy)return;
    busy=true;
    try {
      const results=await Promise.all([runs(),videos(),request('/api/intelligence/diagnostics')]);
      const kpi=results[2].monthly;
      el('discoveryKPI').textContent='За месяц: обнаружено '+kpi.discovered+
        ' · подтверждённых свежих и релевантных: '+kpi.qualified_fresh+
        ' / '+kpi.monthly_target+' (только ручной импорт)';
    }
    catch(ex){notice(ex.message);}
    finally{busy=false;}
  }
  el('discoveryForm').addEventListener('submit',async event=>{
    event.preventDefault();
    let text=el('discoveryText').value;
    let fmt=el('discoveryFormat').value;
    const file=el('discoveryFile').files[0];
    if (file) {
      if(file.size>2000000){notice('Файл превышает 2 МБ.');return;}
      fmt=file.name.toLowerCase().endsWith('.csv')?'csv':'urls';
      text=await file.text();
    }
    if(!text.trim()){notice('Добавь ссылки или CSV.');return;}
    el('discoveryImport').disabled=true;
    try {
      const r=await request('/api/intelligence/manual-imports',{
        text,format:fmt,topic:el('discoveryTopic').value,
        idempotency_key:crypto.randomUUID()
      });
      lastRun=r.run_id;
      notice('Создан запуск #'+r.run_id+' · '+r.queued_rows+' строк. Импорт выполняется в фоне.');
      await refresh();
    } catch(ex){notice(ex.message);}
    finally {el('discoveryImport').disabled=false;}
  });
  el('discoveryMore').addEventListener('click',()=>videos(true).catch(ex=>notice(ex.message)));
  el('discoveryRefresh').addEventListener('click',refresh);
  document.addEventListener('cm-discovery-open',refresh);
  setInterval(()=>{
    if(!el('discoveryView').hidden)refresh();
  },4000);
})();
