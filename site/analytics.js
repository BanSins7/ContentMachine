'use strict';
// Minimal local operator UI. It never initiates a Collector / paid model request.
(() => {
  const el = id => document.getElementById(id);
  let busy = false;
  const message = (text, bad=false) => {
    const node = el('analyticsNotice');
    if (node) {
      node.textContent = text;
      node.style.color = bad ? 'var(--danger, #b24040)' : '';
    }
  };
  async function api(path, payload) {
    const options = payload === undefined
      ? {cache:'no-store'}
      : {method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
         body:JSON.stringify(payload)};
    const res = await fetch(path, options);
    const json = await res.json();
    if (!res.ok || json.ok === false) {
      throw new Error(json.error?.message || json.error || 'HTTP ' + res.status);
    }
    return json;
  }
  const projectId = () => el('analyticsProject')?.value || '';
  const base = () => '/api/projects/' + projectId() + '/analytics';
  const source = () => el('analyticsSource').value.trim();
  const ids = () => {
    const raw = el('analyticsVideoIds').value.trim();
    if (!raw) throw new Error('Укажи video_id из Discovery / SRE.');
    const found = raw.split(/[\s,;]+/).filter(Boolean);
    if (found.length < 1 || found.length > 50 ||
        found.some(n => !/^[1-9][0-9]{0,15}$/.test(n) || !Number.isSafeInteger(Number(n)))) {
      throw new Error('Нужны от 1 до 50 положительных целочисленных video_id.');
    }
    if (new Set(found).size !== found.length) throw new Error('Повторяется video_id.');
    return found.map(Number);
  };
  function key(prefix) {
    return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
  }
  function render(container, rows, titleField='id') {
    const root = el(container);
    if (!root) return;
    root.replaceChildren();
    if (!rows.length) {
      const p = document.createElement('p');
      p.textContent = 'Записей нет. Это не ошибка: до подтверждённых данных A/B/C qualified результатов может не быть.';
      root.append(p);
      return;
    }
    for (const item of rows) {
      const details = document.createElement('details');
      details.className = 'tb-workspace-disclosure';
      const head = document.createElement('summary');
      head.textContent = String(item.video_id ?? item[titleField] ?? '') +
        ' · ' + String(item.status ?? item.state ?? 'unknown') +
        (item.metrics_c?.breakout_score == null ? '' : ' · C7 ' + item.metrics_c.breakout_score);
      const pre = document.createElement('pre');
      pre.className = 'discovery-trace';
      pre.textContent = JSON.stringify(item, null, 2);
      details.append(head, pre);
      root.append(details);
    }
  }
  async function loadProjects() {
    try {
      const data = await api('/api/projects');
      const select = el('analyticsProject');
      const old = select.value;
      select.replaceChildren();
      for (const project of data.projects || []) {
        if (!/^[0-9a-f]{32}$/.test(project.id || '')) continue;
        const option = document.createElement('option');
        option.value = project.id;
        option.textContent = (project.name || 'Проект') + ' · ' + project.id.slice(0, 8);
        select.append(option);
      }
      if ([...select.options].some(o => o.value === old)) select.value = old;
      if (select.value) await refresh();
      else message('Сначала создай проект в «Мои проекты».');
    } catch (err) { message(String(err.message || err), true); }
  }
  async function refresh() {
    if (!projectId()) return;
    try {
      const [results, jobs] = await Promise.all([
        api(base()+'?include_partial=1&limit=25'),
        api(base()+'/jobs?limit=25')
      ]);
      render('analyticsSnapshots', results.results || []);
      render('analyticsJobs', jobs.jobs || []);
      message('Готово. Отображаются только доступные по текущей политике данные.');
    } catch (err) { message(String(err.message || err), true); }
  }
  async function withBusy(fn) {
    if (busy) return;
    busy = true;
    for (const id of ['analyticsRun','analyticsBatch','analyticsWork','analyticsAggregate']) {
      if (el(id)) el(id).disabled = true;
    }
    try { await fn(); }
    catch (err) { message(String(err.message || err), true); }
    finally {
      busy = false;
      for (const id of ['analyticsRun','analyticsBatch','analyticsWork','analyticsAggregate']) {
        if (el(id)) el(id).disabled = false;
      }
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    if (!el('analyticsPanel')) return;
    el('analyticsProject').addEventListener('change', refresh);
    el('analyticsRefresh').addEventListener('click', loadProjects);
    el('analyticsRun').addEventListener('click', () => withBusy(async () => {
      const videos = ids();
      if (videos.length !== 1) throw new Error('Для прямой проверки введи один video_id.');
      const data = await api(base()+'/qualify',
        {video_id:videos[0], source_id:source(), request_key:key('aql-direct')});
      message('Проверка завершена: '+data.snapshot.status+
              (data.snapshot.blockers.length ? '. Причины: '+data.snapshot.blockers.join(', ') : '.'));
      await refresh();
    }));
    el('analyticsBatch').addEventListener('click', () => withBusy(async () => {
      const data = await api(base()+'/batch',
        {video_ids:ids(), source_id:source(), batch_key:key('aql-batch')});
      message('Поставлено задач: '+data.jobs.length+'. Нажми «Обработать» для локального DB-only расчёта.');
      await refresh();
    }));
    el('analyticsAggregate').addEventListener('click', () => withBusy(async () => {
      const config = Number(el('analyticsConfigId').value);
      const sample = Number(el('analyticsMinSample').value);
      if (!Number.isSafeInteger(config) || config < 1 ||
          !Number.isSafeInteger(sample) || sample < 2 || sample > 10000) {
        throw new Error('Нужны реальный C config ID и min_sample от 2 до 10000.');
      }
      const search = new URLSearchParams({
        source_id:source(), config_id:String(config),
        dimension:el('analyticsDimension').value, min_sample:String(sample)
      });
      const result = await api(base()+'/aggregate?'+search.toString());
      el('analyticsAggregateResults').textContent = JSON.stringify(result.summary, null, 2);
      message('Сводка рассчитана по qualified данным без причинных утверждений.');
    }));
    el('analyticsWork').addEventListener('click', () => withBusy(async () => {
      const data = await api(base()+'/jobs/work',{max_jobs:5});
      message('Обработано локальных заданий: '+data.processed+'.');
      await refresh();
    }));
    loadProjects();
  });
})();
