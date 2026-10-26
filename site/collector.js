'use strict';
/* VC-MOD-06 operator UI. Nothing is submitted automatically. */
(() => {
  const el = id => document.getElementById(id);
  const root = el('collectorV1');
  if (!root) return;
  const path = '/api/intelligence/collector/';
  let project = '', manifest = null, shard = null, preview = null, held = null;
  let busy = false;
  let commitKey = null, releaseKey = null;
  const report = value => {
    el('collectorV1Result').textContent = JSON.stringify(value, null, 2);
  };
  const status = msg => { el('collectorV1Status').textContent = msg; };
  async function call(url, body) {
    const opts = body === undefined ? {cache:'no-store'} : {
      method:'POST', headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(body)
    };
    const res = await fetch(path+url,opts);
    const data = await res.json();
    if (!res.ok || !data.ok) throw Error(data.error?.message || 'HTTP '+res.status);
    return data;
  }
  function resetRun() {
    preview = null;
    held = null;
    commitKey = null;
    releaseKey = null;
    el('collectorV1Commit').disabled = true;
    el('collectorV1Release').disabled = true;
    el('collectorV1Confirm').checked = false;
    el('collectorV1ReleaseConfirm').checked = false;
    el('collectorV1RunId').textContent = 'Нет';
  }
  function selected() {
    if (!manifest || !shard) throw Error('Выбери опубликованный manifest и shard');
    return {project_id:project,manifest_id:manifest.manifest_id,
            query_set_id:shard.query_set_id,source_id:'dtk_tiktok',
            limits:{max_tasks:50,max_pages_per_task:1,max_results_per_page:50,
                    max_total_requests:50,max_total_candidates:2500}};
  }
  async function guarded(fn) {
    if (busy) return;
    busy = true;
    try { await fn(); } catch (err) { status(err.message); }
    finally { busy = false; }
  }
  el('collectorV1Load').addEventListener('click',()=>guarded(async()=>{
    const pid = el('collectorV1Project').value.trim().toLowerCase();
    if (!/^[a-f0-9]{32}$/.test(pid)) throw Error('Укажи ID проекта (32 hex)');
    project = pid; manifest=null; shard=null; resetRun();
    const data = await call('manifests?project_id='+encodeURIComponent(pid));
    const select = el('collectorV1Manifest');
    select.replaceChildren(new Option('Выбери manifest',''));
    data.manifests.forEach(m=>select.add(new Option(
      m.status+' · '+m.selected_count+' запросов · '+m.manifest_id.slice(0,8),
      m.manifest_id)));
    el('collectorV1Shard').replaceChildren(new Option('Выбери shard',''));
    report(data);
    status('Манифесты проекта загружены. Только чтение.');
  }));
  el('collectorV1Manifest').addEventListener('change',()=>guarded(async()=>{
    const id = el('collectorV1Manifest').value;
    manifest=null; shard=null; resetRun();
    el('collectorV1Shard').replaceChildren(new Option('Выбери shard',''));
    if (!id) return;
    const data = await call('manifests/'+encodeURIComponent(id)+
      '?project_id='+encodeURIComponent(project));
    manifest=data;
    data.shards.forEach(s=>el('collectorV1Shard').add(
      new Option('#'+(s.shard_ordinal+1)+' · '+s.query_count+' запросов',s.query_set_id)));
    report({status:data.status, selected_count:data.selected_count,
            coverage:data.coverage, shards:data.shards});
    status(data.status==='no_queries'
      ? 'QV выбрал 0 запросов: запуск недоступен.'
      : 'Выбери один из доверенных shards для предварительного просмотра.');
  }));
  el('collectorV1Shard').addEventListener('change',()=>{
    resetRun();
    shard=manifest?.shards.find(s=>s.query_set_id===el('collectorV1Shard').value)||null;
    status(shard?'Shard выбран. Нажми «Предпросмотр».':'Выбери shard.');
  });
  el('collectorV1Preview').addEventListener('click',()=>guarded(async()=>{
    const data = await call('manifest-preview',selected());
    preview=data; held=null; commitKey=null; releaseKey=null;
    el('collectorV1Release').disabled=true;
    const blocking=new Set(data.blocks||[]);
    blocking.delete('provider_connection_not_verified');
    const allowed=blocking.size===0 && data.tasks.length>0;
    el('collectorV1Commit').disabled=!allowed;
    report(data);
    status(allowed
      ? 'Предпросмотр готов. Для сохранения отложенных задач отдельно подтвердите действие.'
      : 'План заблокирован: '+[...blocking].join(', '));
  }));
  el('collectorV1Commit').addEventListener('click',()=>guarded(async()=>{
    if (!preview || !shard || !el('collectorV1Confirm').checked)
      throw Error('Подтверди сохранение отложенного плана');
    const data=await call('manifest-runs',{
      ...selected(), plan_hash:preview.plan_hash,
      idempotency_key:commitKey || (commitKey='vc-'+crypto.randomUUID()),
      confirm:true, max_spend_usd:'0.00'
    });
    held=data; el('collectorV1RunId').textContent=String(data.run_id);
    el('collectorV1Commit').disabled=true;
    el('collectorV1Release').disabled=false;
    report(data);
    status('Задачи СОХРАНЕНЫ, но НЕ отправлены DTK. Release — отдельное действие.');
  }));
  el('collectorV1Release').addEventListener('click',()=>guarded(async()=>{
    if (!held || !el('collectorV1ReleaseConfirm').checked)
      throw Error('Отдельно подтверди разрешение DTK и настройку архива');
    const data=await call('runs/'+held.run_id+'/release',{
      project_id:project,run_id:held.run_id,confirm:true,
      idempotency_key:releaseKey || (releaseKey='vc-release-'+crypto.randomUUID()),
      max_spend_usd:'0.00',max_provider_requests:preview.tasks.length,
      acknowledge_archive_configuration:true
    });
    report(data);
    el('collectorV1Release').disabled=true;
    status('Release подтверждён: очередь может быть обработана Worker, только если включён серверный флаг и проверки пройдены.');
  }));

  // VC-MOD-12: every read is explicit and scoped by the selected project.
  // No automatic DTK polling, provider submission, page 2 or release.
  let nextRunCursor = null, nextVideoCursor = null, resultsRun = null;
  const runsSelect = el('collectorV1Runs');
  function ownerProject() {
    const typed=el('collectorV1Project').value.trim().toLowerCase();
    if (!/^[a-f0-9]{32}$/.test(typed) || typed !== project)
      throw Error('Сначала загрузи QV manifests этого проекта; проверь project ID.');
    return project;
  }
  function selectedRun() {
    ownerProject();
    const id = runsSelect.value;
    if (!/^[1-9][0-9]{0,17}$/.test(id)) throw Error('Выбери реальный запуск');
    return id;
  }
  function clearResults() {
    nextVideoCursor=null;
    resultsRun=null;
    el('collectorV1Videos').replaceChildren();
    el('collectorV1Trace').textContent='Выбери запуск.';
    el('collectorV1ResultsStatus').textContent='Сохранённые видео не загружены.';
    el('collectorV1LoadTrace').disabled=true;
    el('collectorV1NextResults').disabled=true;
  }
  async function loadRuns(append=false) {
    const pid=ownerProject();
    const cursor=append ? nextRunCursor : null;
    if (append && !cursor) return;
    const params=new URLSearchParams({project_id:pid,limit:'20'});
    if (cursor) params.set('before_run_id',String(cursor));
    const data=await call('runs?'+params.toString());
    if (!append) {
      runsSelect.replaceChildren(new Option('Выбери запуск',''));
      clearResults();
    }
    for (const run of data.runs) {
      const detail=run.d1_persisted_pages+' D1 checkpoints / '+
                   run.d1_saved_videos+' видео';
      runsSelect.add(new Option(
        '#'+run.run_id+' · '+run.stage+' · '+detail, String(run.run_id)));
    }
    nextRunCursor=data.next_before_run_id;
    el('collectorV1MoreRuns').disabled=!data.has_more;
    status(data.runs.length ? 'Запуски получены из Collector PostgreSQL.' :
      'Для этого проекта ещё нет запусков Collector.');
  }
  function addVideoCard(video) {
    const box=document.createElement('section');
    box.className='settings-card';
    const heading=document.createElement('div');
    const name=document.createElement('strong');
    name.textContent='#'+video.video_id+' · '+(video.title||'Без проверенного названия');
    heading.append(name);
    box.append(heading);
    const info=document.createElement('p');
    info.textContent='D1 checkpoint #'+video.page_id+' · '+video.quality+
      ' · '+video.content_kind+' · SRE доставка: '+
      (video.sre.delivery_state||'не создана')+
      ' · релевантность: НЕ оценивалась';
    box.append(info);
    const lineage=document.createElement('p');
    lineage.textContent='QV queries: '+(video.candidate_ids||[]).join(', ')+
      ' | hypotheses: '+(video.hypothesis_ids||[]).join(', ')+
      ' | discovery #'+video.discovery_id;
    box.append(lineage);
    const source=document.createElement('p');
    if (/^https:\/\/www\.tiktok\.com\/@[A-Za-z0-9._]+\/video\/[0-9]{8,24}$/.test(
      String(video.canonical_url||''))) {
      const a=document.createElement('a');
      a.textContent='Открыть TikTok';
      a.href=video.canonical_url;
      a.target='_blank';
      a.rel='noopener noreferrer';
      source.append(a);
    } else {
      source.textContent='Каноническая ссылка не подтверждена.';
    }
    box.append(source);
    el('collectorV1Videos').append(box);
  }
  async function loadResults(run,append=false) {
    const pid=ownerProject();
    if (String(run)!==selectedRun()) throw Error('Проект или запуск изменён');
    const params=new URLSearchParams({project_id:pid,limit:'20'});
    if (append && nextVideoCursor) params.set('after',nextVideoCursor);
    if (append && !nextVideoCursor) return;
    const data=await call('runs/'+encodeURIComponent(run)+'/results?'+params.toString());
    if (!append) el('collectorV1Videos').replaceChildren();
    for (const video of data.videos) addVideoCard(video);
    resultsRun=run;
    nextVideoCursor=data.next_after;
    el('collectorV1NextResults').disabled=!data.has_more;
    el('collectorV1ResultsStatus').textContent=data.videos.length ?
      'Показаны только реальные D1 видео, без предварительных кандидатов.' :
      (append ? 'Следующих видео нет.' :
       'Пока нет сохранённых видео. Проверь состояние Worker и D1 checkpoint.');
  }
  async function loadTrace(run) {
    const pid=ownerProject();
    const params=new URLSearchParams({project_id:pid});
    const [detail,trace]=await Promise.all([
      call('runs/'+run+'?'+params.toString()),
      call('runs/'+run+'/trace?'+params.toString())
    ]);
    if (String(run)!==selectedRun()) return;
    const displayed={
      run_id:run, state:detail.state, stage:detail.status,
      cancelled:detail.cancel_requested, unknown_outcomes:detail.unknown_outcomes,
      d1_completion_verified:detail.d1_completion_verified,
      d1_integrity_warning:detail.d1_integrity_warning,
      d1_pages:detail.d1_persisted_pages,
      d1_saved_videos:detail.d1_saved_videos,
      d1_new_videos:detail.d1_new_videos,
      d1_duplicate_videos:detail.d1_duplicate_videos,
      sre_ready_events:detail.sre_ready_events,
      qv_lineage:trace.lineage, steps:trace.tasks,
      sre_delivery_confirmed_events:trace.summary.sre_confirmed_delivered,
      relevance_verified:false,
    };
    el('collectorV1Trace').textContent=JSON.stringify(displayed,null,2);
  }
  el('collectorV1LoadRuns').addEventListener('click',()=>guarded(async()=>{
    await loadRuns();
  }));
  el('collectorV1MoreRuns').addEventListener('click',()=>guarded(async()=>{
    await loadRuns(true);
  }));
  runsSelect.addEventListener('change',()=>guarded(async()=>{
    clearResults();
    if (!runsSelect.value) return;
    const run=selectedRun();
    el('collectorV1LoadTrace').disabled=false;
    await loadTrace(run);
    await loadResults(run);
  }));
  el('collectorV1LoadTrace').addEventListener('click',()=>guarded(async()=>{
    await loadTrace(selectedRun());
  }));
  el('collectorV1NextResults').addEventListener('click',()=>guarded(async()=>{
    const run=selectedRun();
    if (run!==String(resultsRun)) throw Error('Выбран другой запуск');
    await loadResults(run,true);
  }));

  status('Никаких автоматических поисковых запросов или release.');
})();
