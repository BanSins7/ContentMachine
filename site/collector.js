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
  status('Никаких автоматических поисковых запросов или release.');
})();
