/* QDP FIX-16: safe local Studio. No raw HTML, providers, media or paid work. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const validProject = value => /^[0-9a-f]{32}$/.test(value);
  const validUuid = value => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
  let projectEpoch = 0;
  let chosenRun = '';
  let chosenConfig = null;
  let pickerCursor = null;
  let runCursor = null;
  let itemCursor = null;
  let eventCursor = null;
  let previewFingerprint = '';
  const put = (id, value) => { $(id).textContent = String(value ?? ''); };
  const clear = id => $(id).replaceChildren();
  const endpoint = tail => '/api/projects/' + $('qdpProject').value + '/qualified-data/' + tail;
  const query = args => new URLSearchParams(args).toString();
  const addText = (target, content, cls='settings-description') => {
    const p = document.createElement('p');
    p.className = cls;
    p.textContent = content;
    target.append(p);
    return p;
  };
  const addButton = (target,label,callback) => {
    const b=document.createElement('button');
    b.type='button';
    b.className='button';
    b.textContent=label;
    b.addEventListener('click',()=>safe(callback));
    target.append(b);
    return b;
  };
  async function api(url, body) {
    const request = body===undefined ? {cache:'no-store'} : {
      method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(body),cache:'no-store'
    };
    const response=await fetch(url,request);
    const value=await response.json();
    if (!response.ok || value.ok===false) {
      const err=value.error;
      throw Error((err && typeof err==='object' ? err.message : err) || 'HTTP '+response.status);
    }
    return value;
  }
  function config() {
    const age=$('qdpMaxAge').value.trim();
    if (age && (!/^[0-9]+$/.test(age) || +age<1 || +age>365)) {
      throw Error('Возраст публикации: 1–365 дней или без ограничения.');
    }
    return {max_age_days:age ? +age : null,require_published_at:$('qdpRequireDate').checked};
  }
  function resetPreview() {
    previewFingerprint='';
    $('qdpStart').disabled=true;
    put('qdpPreview','Выберите подтверждённый SRE LIVE run и выполните предварительную проверку.');
  }
  function fingerprint() {
    return $('qdpProject').value+'|'+$('qdpSreRun').value+'|'+JSON.stringify(config());
  }
  function resetRun() {
    chosenRun='';chosenConfig=null;itemCursor=null;eventCursor=null;
    clear('qdpItems');clear('qdpEvents');clear('qdpDiagnostics');
    put('qdpDetailSummary','Выберите QDP run в истории.');
    put('qdpManifest','');put('qdpLineage','');
    $('qdpRefreshRun').disabled=true;$('qdpCancelRun').disabled=true;
    $('qdpMoreItems').hidden=true;$('qdpMoreEvents').hidden=true;
  }
  async function loadSre(reset=true) {
    if (!validProject($('qdpProject').value)) return;
    const epoch=projectEpoch;
    if (reset) {pickerCursor=null;clear('qdpSreRun');resetPreview();}
    const old=$('qdpSreRun').value;
    const args={limit:'25'};
    if (pickerCursor) args.cursor=pickerCursor;
    const data=await api(endpoint('eligible-sre-runs?'+query(args)));
    if (epoch!==projectEpoch) return;
    const picker=$('qdpSreRun');
    for (const run of data.runs || []) {
      if (!validUuid(run.id)) continue;
      const option=document.createElement('option');
      option.value=run.id;
      option.textContent='LIVE · '+run.id.slice(0,8)+' · Taxonomy '+run.taxonomy_version_id+
        (run.finished_at?' · '+String(run.finished_at).slice(0,16):'');
      picker.append(option);
    }
    if (reset && Array.from(picker.options).some(x=>x.value===old)) picker.value=old;
    pickerCursor=data.next_cursor;
    $('qdpMoreSre').hidden=!pickerCursor;
    put('qdpSreStatus',picker.options.length
      ? 'Только completed LIVE + approved taxonomy. Выбор не запускает классификацию.'
      : 'Нет подтверждённых LIVE SRE runs. QDP не примет offline/legacy запуск.');
  }
  async function loadProjects() {
    const data=await api('/api/projects');
    const el=$('qdpProject'),previous=el.value;
    el.replaceChildren();
    for (const p of data.projects || []) {
      if (!validProject(p.id)) continue;
      const option=document.createElement('option');
      option.value=p.id;option.textContent=p.name || p.id;
      el.append(option);
    }
    if (Array.from(el.options).some(p=>p.value===previous)) el.value=previous;
    await projectChanged();
  }
  async function projectChanged() {
    projectEpoch++;
    resetPreview();resetRun();
    pickerCursor=null;runCursor=null;
    clear('qdpSreRun');clear('qdpRuns');
    if (!validProject($('qdpProject').value)) {
      put('qdpSreStatus','Сначала создайте проект.');
      return;
    }
    await Promise.all([loadSre(),history(true),operations()]);
  }
  async function preview() {
    const run=$('qdpSreRun').value;
    if (!validUuid(run)) throw Error('Выберите LIVE SRE run в списке.');
    const key=fingerprint();
    const epoch=projectEpoch;
    const data=await api(endpoint('preview?'+query({sre_run_id:run})));
    if (epoch!==projectEpoch || key!==fingerprint()) return;
    previewFingerprint=key;
    $('qdpStart').disabled=(data.totals?.qualified??0)===0;
    put('qdpPreview','SRE '+run+
      '\nQualified: '+data.totals.qualified+
      ' · Human review: '+data.totals.needs_review+
      ' · Excluded: '+data.totals.excluded+
      '\nТолько оценка отбора, без платных вызовов и скачивания медиа.');
  }
  async function create() {
    const run=$('qdpSreRun').value;
    if (!validUuid(run) || previewFingerprint!==fingerprint()) {
      throw Error('Настройки или SRE run изменились. Проверьте набор ещё раз.');
    }
    const project=$('qdpProject').value;
    const response=await api(endpoint('runs'),{
      sre_run_id:run,idempotency_key:'qdp-studio-'+crypto.randomUUID(),
      config:config()
    });
    if (project!==$('qdpProject').value) return;
    put('qdpPreview','Создано: '+response.run_id+
      '. Подготовка не является выполненным анализом.');
    await history(true);
    await openRun(response.run_id);
  }
  async function operations() {
    if (!validProject($('qdpProject').value)) return;
    const epoch=projectEpoch;
    const view=await api(endpoint('observability?limit=20'));
    if (epoch!==projectEpoch) return;
    clear('qdpOperations');
    const target=$('qdpOperations');
    const runs=view.runs||{};
    addText(target,'Qualified snapshots: '+(runs.qualified_snapshots??0)+
      ' · Review: '+(runs.review_snapshots??0)+
      ' · Excluded: '+(runs.excluded_snapshots??0)+
      ' · Active runs: '+(runs.active_runs??0));
    for (const group of view.readiness_by_group || []) {
      addText(target,'Group '+group.analysis_group+': '+group.currently_ready+
        ' current ready / '+group.historical_ready+' historical ready');
    }
    for (const d of view.delivery_by_state || []) {
      addText(target,d.consumer+' · '+d.event_type+' · '+d.state+
        ': '+d.n+' total, '+d.due_now+' due, '+d.expired_leases+' stale leases');
    }
    for (const j of view.worker_jobs_by_state || []) {
      addText(target,'QDP worker '+j.state+': '+j.n+
        ' (due '+j.due_now+', expired '+j.expired_leases+')');
    }
    addText(target,'Revocations: '+view.revocations+
      '. Confirmed Group analysis completed: unavailable (peer evidence required).');
    for (const d of view.oldest_unresolved || []) {
      addText(target,'Unresolved '+d.consumer+' · '+d.event_type+
        ' · '+d.state+' · '+String(d.event_id).slice(0,8)+
        ' · '+d.attempts+' attempts');
    }
  }
  async function history(reset=true) {
    if (!validProject($('qdpProject').value)) return;
    const epoch=projectEpoch;
    if (reset) {clear('qdpRuns');runCursor=null;}
    const args={limit:'25'};
    if (runCursor) args.cursor=runCursor;
    const response=await api(endpoint('runs?'+query(args)));
    if (epoch!==projectEpoch) return;
    for (const run of response.runs || []) {
      addButton($('qdpRuns'),
        (run.status||'?')+' · '+run.total_qualified+' qualified · '+run.id.slice(0,8),
        ()=>openRun(run.id));
    }
    runCursor=response.next_cursor;
    $('qdpMoreRuns').hidden=!runCursor;
    if (reset && !(response.runs||[]).length) addText($('qdpRuns'),'Запусков ещё нет.');
  }
  async function openRun(runId) {
    if (!validUuid(runId)) throw Error('Некорректный QDP run');
    chosenRun=runId;chosenConfig=null;itemCursor=null;eventCursor=null;
    const epoch=projectEpoch;
    put('qdpLineage','');clear('qdpItems');clear('qdpEvents');clear('qdpDiagnostics');
    const [page,summary,manifest]=await Promise.all([
      api(endpoint('runs/'+runId+'?limit=50')),
      api(endpoint('runs/'+runId+'/diagnostics')),
      api(endpoint('runs/'+runId+'/manifest'))
    ]);
    if (epoch!==projectEpoch || chosenRun!==runId) return;
    chosenConfig=page.run.config;
    put('qdpDetailSummary','QDP '+runId+' · '+page.run.status+
      ' · '+page.run.total_qualified+' qualified · '+page.run.total_review+
      ' review · '+page.run.total_excluded+' excluded');
    $('qdpRefreshRun').disabled=false;
    $('qdpCancelRun').disabled=!['queued','running'].includes(page.run.status);
    const meta=manifest.manifest;
    put('qdpManifest','Manifest SHA256: '+meta.manifest_sha256+
      '\nSelection SHA256: '+meta.selected_sha256+
      '\nTaxonomy SHA256: '+meta.taxonomy_sha256+
      '\nExpected videos: '+meta.expected_videos+' · expected pairs: '+meta.expected_pairs);
    const stats=$('qdpDiagnostics');
    for (const g of summary.groups || []) {
      addText(stats,'Group '+g.analysis_group+' · current ready: '+g.currently_ready+
        ' · historical ready: '+g.historical_ready+' · blocked: '+g.historical_blocked);
    }
    for (const d of summary.deliveries || []) {
      addText(stats,(d.consumer||'consumer')+' / '+d.event_type+' / '+d.state+
        ': '+d.n+' events');
    }
    addText(stats,'Revocations: '+summary.invalidation_count+
      ' · generation: '+summary.invalidation_generation+
      '. ACK = accepted inbox, not completed analysis.');
    renderItems(page);
    await loadEvents(true);
  }
  function renderItems(page) {
    for (const v of page.items || []) {
      const p=$('qdpItems');
      const states=Object.entries(v.groups||{}).map(([g,s])=>g+':'+s.state+
        (s.reason_code?' ('+s.reason_code+')':'')).join(' · ');
      addButton(p,'Видео '+v.video_id+' · '+v.status+
        (v.reason_code?' ['+v.reason_code+']':'')+
        (v.metadata?.title?' · '+v.metadata.title:'')+
        (states?' · '+states:''),
        ()=>loadLineage(v.video_id));
    }
    itemCursor=page.next_cursor;
    $('qdpMoreItems').hidden=!itemCursor;
    if (!page.items?.length && !itemCursor && !$('qdpItems').childElementCount) {
      addText($('qdpItems'),'Нет квалифицированных видео в этом запуске.');
    }
  }
  async function moreItems() {
    if (!chosenRun || !itemCursor) return;
    const epoch=projectEpoch,run=chosenRun;
    const page=await api(endpoint('runs/'+run+'?'+query({
      limit:'50',after_video_id:String(itemCursor)})));
    if (epoch===projectEpoch && chosenRun===run) renderItems(page);
  }
  async function loadEvents(reset=true) {
    if (!chosenRun) return;
    const epoch=projectEpoch,run=chosenRun;
    if (reset) {eventCursor=null;clear('qdpEvents');}
    const args={limit:'40'};
    if (eventCursor) args.cursor=eventCursor;
    const page=await api(endpoint('runs/'+run+'/events?'+query(args)));
    if (epoch!==projectEpoch || chosenRun!==run) return;
    for (const e of page.events || []) {
      addText($('qdpEvents'),(e.created_at||'')+' · '+e.event_type+
        (e.video_id?' · video '+e.video_id:'')+
        (e.analysis_group?' · '+e.analysis_group:'')+
        (e.consumer?' · '+e.consumer+': '+e.delivery_state+
           ' (attempts '+e.attempts+')':'')+
        (e.reason_code?' · '+e.reason_code:''));
    }
    eventCursor=page.next_cursor;
    $('qdpMoreEvents').hidden=!eventCursor;
  }
  async function loadLineage(videoId) {
    if (!chosenRun) return;
    const epoch=projectEpoch,run=chosenRun;
    const result=await api(endpoint('runs/'+run+'/lineage?'+query({video_id:String(videoId)})));
    if (epoch!==projectEpoch || chosenRun!==run) return;
    if (!result.current) {
      put('qdpLineage','Исторические доказательства недоступны: '+result.reason_code);
      return;
    }
    const rows=(result.lineage||[]).map(x=>
      x.category_key+' · result '+x.sre_result_id+' · model '+x.model_decision+
      ' → effective '+x.effective_decision+
      ' · evidence SHA '+x.evidence_sha256).join('\n');
    const source=result.source||{};
    put('qdpLineage','Video '+videoId+' · Текущие права подтверждены\n'+rows+
      '\nSource: '+(source.source_id||'?')+
      ' · policy '+(source.source_policy_version_id||'?')+
      ' · metadata '+(source.metadata_id||'?')+
      ' · asset '+(source.media_asset_id||'none')+
      ' · metric IDs '+JSON.stringify(source.metric_snapshot_ids||[]));
  }
  async function refreshRun() {
    if (!chosenRun || !chosenConfig || !window.confirm(
      'Создать НОВЫЙ QDP run? Старый immutable run и журнал отзывов сохранятся.')) return;
    const prior=chosenRun;
    const response=await api(endpoint('runs/'+prior+'/refresh'),{
      idempotency_key:'qdp-refresh-'+crypto.randomUUID(),config:chosenConfig
    });
    put('qdpPreview','Новая ревизия '+response.new_run_id+' создана отдельно от '+prior);
    await history(true);await openRun(response.new_run_id);
  }
  async function cancelRun() {
    if (!chosenRun || !window.confirm('Отменить новые задачи в этом QDP run?')) return;
    await api(endpoint('runs/'+chosenRun+'/cancel'),{});
    await openRun(chosenRun);
  }
  async function safe(fn) {
    try {put('qdpError','');await fn();}
    catch (e) {put('qdpError',String(e.message||e).slice(0,400));}
  }
  $('qdpProject').addEventListener('change',()=>safe(projectChanged));
  for (const id of ['qdpSreRun','qdpMaxAge','qdpRequireDate']) {
    $(id).addEventListener('input',resetPreview);
    $(id).addEventListener('change',resetPreview);
  }
  $('qdpPreviewButton').addEventListener('click',()=>safe(preview));
  $('qdpStart').addEventListener('click',()=>safe(create));
  $('qdpMoreSre').addEventListener('click',()=>safe(()=>loadSre(false)));
  $('qdpRefresh').addEventListener('click',()=>safe(()=>history(true)));
  $('qdpOpsRefresh').addEventListener('click',()=>safe(operations));
  $('qdpGroupAReadiness').addEventListener('click',()=>safe(async()=>{
    const result=await api(endpoint('group-a/readiness'));
    put('qdpGroupAStatus',JSON.stringify(result,null,2));
  }));
  $('qdpGroupADeliver').addEventListener('click',()=>safe(async()=>{
    if(!validProject($('qdpProject').value))throw Error('Выбери проект QDP');
    const result=await api(endpoint('deliver-group-a'),{});
    put('qdpGroupAStatus',JSON.stringify(result,null,2));
    if(result.result?.state==='acknowledged')await history(true);
  }));
  $('qdpMoreRuns').addEventListener('click',()=>safe(()=>history(false)));
  $('qdpMoreItems').addEventListener('click',()=>safe(moreItems));
  $('qdpMoreEvents').addEventListener('click',()=>safe(()=>loadEvents(false)));
  $('qdpRefreshRun').addEventListener('click',()=>safe(refreshRun));
  $('qdpCancelRun').addEventListener('click',()=>safe(cancelRun));
  document.addEventListener('cm-qdp-open',()=>safe(loadProjects));
  resetPreview();resetRun();
})();
