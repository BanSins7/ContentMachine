'use strict';
(() => {
  const el = id => document.getElementById(id);
  let busy = false;
  let lastSnapshot = null;
  const qs = new URLSearchParams(location.search);
  const lines = value => value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  const put = (node, tag, text, css) => {
    const item = document.createElement(tag);
    item.textContent = text;
    if (css) item.className = css;
    node.append(item);
    return item;
  };
  async function api(url, data) {
    const opts = data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type':'application/json', 'X-ContentMachine':'1'},
      body: JSON.stringify(data)
    };
    const response = await fetch(url, opts);
    const body = await response.json();
    if (!response.ok || body.ok === false) {
      throw new Error(body.error?.message || body.error || 'Ошибка запроса');
    }
    return body;
  }
  const uri = (pid, path) => '/api/projects/' + encodeURIComponent(pid) + '/market-intelligence/' + path;
  function selected() { return el('miProject').value; }
  function notice(text) { el('miStatus').textContent = text; }
  function render(snapshot, id) {
    lastSnapshot = snapshot;
    const c = snapshot.coverage;
    el('miMetrics').replaceChildren();
    const values = [
      [c.seed_videos, 'Исходных видео'], [c.seed_authors, 'Авторов'],
      [c.matched_discovery_videos, 'Видео в Discovery'],
      [c.eligible_metric_videos, 'Проверенных метрик'],
      [c.analyzed_media_videos, 'Анализов MI'],
      [snapshot.format_families.length, 'Групп форматов']
    ];
    for (const [n, label] of values) {
      const div = put(el('miMetrics'),'div','', 'metric');
      put(div, 'strong', String(n));
      put(div, 'span', label);
    }
    el('miMetrics').hidden = false;
    el('miFormats').replaceChildren();
    for (const f of snapshot.format_families) {
      const div = put(el('miFormats'), 'div', '', 'format');
      put(div,'strong',f.format_family + ' · ' + f.platform);
      put(div,'small',f.observed_videos + ' видео · ' + f.independent_creators +
        ' авторов · коэффициент автора: ' +
        (f.median_creator_ratio === null ? 'нет данных' : f.median_creator_ratio + '×'));
      put(div,'div', f.readiness === 'preliminary_observed' ?
        'Предварительная рыночная закономерность; нуждается в проверке.' :
        'Данных пока недостаточно.');
    }
    el('miVideoComparison').replaceChildren();
    const scored=(snapshot.video_evidence||[])
      .filter(v=>v.creator_ratio!==null && v.creator_baseline_views!==null)
      .sort((a,b)=>b.creator_ratio-a.creator_ratio).slice(0,20);
    if(scored.length) {
      put(el('miVideoComparison'),'h3','Ролики относительно обычного уровня автора');
      for(const v of scored) {
        const card=put(el('miVideoComparison'),'div','', 'format');
        put(card,'strong',(v.creator_id||v.platform)+' · '+v.creator_ratio.toFixed(2)+'×');
        put(card,'small','Просмотры: '+v.views+' · медиана: '+
          Math.round(v.creator_baseline_views)+' · сопоставимых роликов: '+v.creator_baseline_post_count);
        const link=put(card,'a','Открыть видео');
        link.href=v.url;link.target='_blank';link.rel='noopener noreferrer';
      }
    }
    el('miLimitations').replaceChildren();
    for(const msg of snapshot.limitations) put(el('miLimitations'),'p','• '+msg);
    el('miSnapshot').textContent = id ? 'Сохранённый отчёт: '+id : 'Предпросмотр, без сохранения';
    notice(snapshot.status === 'no_discovery_data' ?
      'Ссылки есть, но соответствующие видео ещё не обнаружены в Discovery.' :
      'Исследование сформировано из реально доступных данных.');
  }
  async function showPreview() {
    if (!selected()) return;
    const result = await api(uri(selected(), 'preview'));
    render(result.snapshot, null);
  }
  async function freeze() {
    const result = await api(uri(selected(), 'freeze'), {});
    const saved = await api(uri(selected(), 'snapshots/' + result.snapshot_id));
    render(saved.snapshot, result.snapshot_id);
  }
  async function trackAuthors(ids) {
    if (!ids.length) return;
    for (let attempt=0;attempt<160;attempt++) {
      const state=await api(uri(selected(), 'authors'));
      renderAuthorStatus(state);
      const wanted=new Set(ids);
      const rows=state.jobs.filter(j=>wanted.has(j.job_id));
      if(rows.length===ids.length && rows.every(j=>['completed','failed','cancelled'].includes(j.state))) return state;
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
    notice('Исследование профилей продолжается. Обнови отчёт позже.');
    return null;
  }
  function renderAuthorStatus(data) {
    const panel=el('miAuthorsStatus');
    if(!panel)return;
    panel.replaceChildren();
    const profiles=data.profiles||[];
    put(panel,'p','Найдено публикаций: '+(data.total_discovered_video_links||0));
    for(const p of profiles) {
      put(panel,'p',p.platform+' · '+p.profile_url+' · '+p.discovered_videos+' видео');
    }
    for(const task of (data.jobs||[]).slice(0,10)){
      const extra=task.error ? ' · '+task.error : task.result?.capped ? ' · достигнут лимит выборки': '';
      put(panel,'p',task.platform+' · '+task.state+extra);
    }
  }
  function renderMediaStatus(data) {
    const panel=el('miMediaStatus');
    if(!panel)return;
    panel.replaceChildren();
    const counts=data.jobs||{};
    put(panel,'p','Локальные видео: '+(data.media_analyses||0)+
      ' проанализировано · '+(counts.queued||0)+' в очереди · '+
      (counts.running||0)+' обрабатывается · '+(counts.failed||0)+' ошибок');
    if(!data.media_analyses) put(panel,'p',
      'Детальная структура пока недоступна: нужны уже скачанные и разрешённые для анализа видео.');
  }
  async function refreshMediaStatus() {
    renderMediaStatus(await api(uri(selected(),'media')));
  }
  function renderProviderStatus(data) {
    const panel=el('miProviderStatus');
    if(!panel)return;
    panel.replaceChildren();
    put(panel,'p', data.provider?.ready ? 'Bright Data: сбор метрик подключён' :
      data.provider?.configured ? 'Bright Data: ещё не включён или не подтверждены права' :
      'Bright Data: требуется однократная настройка на сервере.');
    const jobs=data.jobs||[];
    put(panel,'p','Метрики: '+jobs.filter(j=>j.state==='completed').length+
      ' пакетов готово · '+jobs.filter(j=>['queued','running'].includes(j.state)).length+
      ' в работе · '+jobs.filter(j=>j.state==='failed').length+' ошибок');
  }
  async function refreshProviderStatus(){
    renderProviderStatus(await api(uri(selected(),'metrics')));
  }
  async function trackImport(runId) {
    for (let attempt=0; attempt<100; attempt++) {
      const result = await api('/api/intelligence/search-runs?id=' + encodeURIComponent(runId));
      const status = result.state || result.run?.state || result.run?.status;
      if (['completed','partial','failed','cancelled'].includes(status)) return status;
      notice('Импорт видео в Discovery: ' + (status || 'ожидаем результат') + '…');
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
    return 'processing';
  }
  el('miForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !selected()) return;
    const videos = lines(el('miVideos').value);
    const authors = lines(el('miAuthors').value);
    if (!videos.length && !authors.length) { notice('Добавь хотя бы одну ссылку.'); return; }
    busy = true; el('miStart').disabled = true;
    try {
      notice('Сохраняем ссылки в проекте…');
      const result = await api(uri(selected(), 'seeds'), {videos, authors});
      const statuses = await Promise.allSettled([
        result.discovery_import?.run_id ? trackImport(result.discovery_import.run_id) : Promise.resolve(null),
        (result.author_jobs||[]).length ? trackAuthors(result.author_jobs.map(j=>j.job_id)) : Promise.resolve(null)
      ]);
      if(statuses.some(s=>s.status==='rejected')) notice('Часть источников недоступна. Проверь статус сбора.');
      await api(uri(selected(),'analyze'),{});
      await freeze();
      const jobs=await api(uri(selected(), 'authors'));
      renderAuthorStatus(jobs);
      await refreshMediaStatus();
      await refreshProviderStatus();
    } catch(e) { notice(String(e.message || e)); }
    finally { busy = false; el('miStart').disabled = false; }
  });
  el('miRefresh').addEventListener('click', async () => {
    if (busy || !selected()) return;
    busy=true;el('miRefresh').disabled=true;
    try {
      await api(uri(selected(),'analyze'),{});
      await freeze();
      renderAuthorStatus(await api(uri(selected(),'authors')));
      await refreshMediaStatus();
      await refreshProviderStatus();
    }
    catch(e) { notice(String(e.message || e)); }
    finally { busy=false;el('miRefresh').disabled=false; }
  });
  el('miProject').addEventListener('change', async () => {
    if (!selected()) return;
    history.replaceState(null,'','?project_id='+encodeURIComponent(selected()));
    try {
      await showPreview();
      renderAuthorStatus(await api(uri(selected(),'authors')));
      await refreshMediaStatus();
      await refreshProviderStatus();
    }
    catch(e) { notice(String(e.message || e)); }
  });
  (async () => {
    try {
      const data = await api('/api/projects');
      const select = el('miProject');
      select.replaceChildren();
      put(select, 'option', 'Выбери проект').value='';
      for (const p of data.projects || []) {
        const entry = put(select, 'option', p.name || p.title || p.id);
        entry.value=p.id;
      }
      const requested=qs.get('project_id');
      if(requested && [...select.options].some(x=>x.value===requested)) select.value=requested;
      else if(data.projects?.length) select.value=data.projects[0].id;
      if(selected()) {
        await showPreview();
        renderAuthorStatus(await api(uri(selected(), 'authors')));
        await refreshMediaStatus();
      await refreshProviderStatus();
      }
    } catch(e) { notice(String(e.message || e)); }
  })();
})();
