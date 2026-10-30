'use strict';
// One minimal interface, independent search/download state for each platform.
const $ = id => document.getElementById(id);
const config = {
  instagram: {
    title: 'Instagram Downloader',
    placeholder: 'instagram.com/reel/... или instagram.com/username/',
    source: 'Instagram',
  },
  tiktok: {
    title: 'TikTok Downloader',
    placeholder: 'tiktok.com/@username/video/... или tiktok.com/@username',
    source: 'TikTok',
  },
  youtube: {
    title: 'YouTube Downloader',
    placeholder: 'youtube.com/shorts/ID или youtube.com/@channel',
    source: 'YouTube',
  },
};
const contexts = Object.fromEntries(Object.keys(config).map(key => [key, {
  target: '', selection: null, discoveryId: null, jobIds: [], finding: false,
  downloading: false, error: '', progress: null, cookieBrowser: 'none',
  maxCount: '20', language: 'auto', transcriptionMode: 'mai',
  stopping: false, ytStats: null, ytLogs: [],
}]));
let platform = 'instagram';
let view = 'workspace';
let snapshot = null;
const api = async (path, payload) => {
  const options = payload === undefined ? {cache:'no-store'} : {
    method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
    body:JSON.stringify(payload),
  };
  const response = await fetch(path, options);
  let body;
  try { body = await response.json(); } catch { throw Error('Не удалось прочитать ответ сервера.'); }
  if (!response.ok || body.error) throw Error(body.error || 'Ошибка '+response.status);
  return body;
};
const ctx = () => contexts[platform];
const money = n => '$' + Number(n || 0).toFixed(4);
const formatCount = n => Number(n || 0).toLocaleString('ru-RU');
function progress(title, detail, percent=null, value='') {
  return {title, detail, percent, value};
}
function render() {
  const current = ctx();
  const details = config[platform];
  const inSettings = view === 'settings';
  const inDiscovery = view === 'discovery';
  const inProjects = view === 'projects';
  const inQdp = view === 'qdp';
  const inWorkspace = view === 'workspace';
  const inTestLab = view === 'test-lab';
  // SP-14: moving away from a project closes the ephemeral owner token.
  // Never retain Search Planner authorization in another tab of the app.
  if (!inProjects && !$('spView').hidden)
    document.dispatchEvent(new Event('cm-sp-leave'));
  if ($('workspaceView')) $('workspaceView').hidden = !inWorkspace;
  $('testLabView').hidden = !inTestLab;
  $('testLabNav').classList.toggle('is-active', inTestLab);
  if (inTestLab) $('testLabNav').setAttribute('aria-current','page');
  else $('testLabNav').removeAttribute('aria-current');
  $('downloaderView').hidden = inWorkspace || inSettings || inDiscovery || inProjects || inQdp || inTestLab;
  $('qdpView').hidden = !inQdp;
  $('qdpNav').classList.toggle('selected',inQdp);
  if(inQdp) $('qdpNav').setAttribute('aria-current','page');
  else $('qdpNav').removeAttribute('aria-current');
  $('discoveryView').hidden = !inDiscovery;
  $('projectsView').hidden = !inProjects;
  $('projectsNav').classList.toggle('selected', inProjects);
  if (inProjects) $('projectsNav').setAttribute('aria-current','page');
  else $('projectsNav').removeAttribute('aria-current');
  $('discoveryNav').classList.toggle('selected', inDiscovery);
  if (inDiscovery) $('discoveryNav').setAttribute('aria-current','page');
  else $('discoveryNav').removeAttribute('aria-current');
  $('settingsView').hidden = !inSettings;
  $('settingsNav').classList.toggle('selected', inSettings);
  if (inSettings) $('settingsNav').setAttribute('aria-current','page');
  else $('settingsNav').removeAttribute('aria-current');
  $('toolTitle').textContent = details.title;
  if (!inWorkspace) document.title = (inTestLab ? 'Тестовый стенд' : inProjects ? 'Проекты' : inDiscovery ? 'Discovery Engine' : inSettings ? 'Параметры' : inQdp ? 'Qualified Data' : details.title)+' — ContentMachine';
  const status = $('openrouterStatus');
  const configured = snapshot?.configuration?.openrouter_configured;
  status.textContent = configured === undefined ? 'Проверяем…' :
    configured ? 'Подключено' : 'Не настроено';
  status.classList.toggle('is-connected',configured === true);
  status.classList.toggle('is-missing',configured === false);
  document.querySelectorAll('[data-platform]').forEach(button => {
    const selected = !inWorkspace && !inQdp && !inSettings && !inDiscovery && !inProjects && !inTestLab && button.dataset.platform === platform;
    button.classList.toggle('selected', selected);
    if (selected) button.setAttribute('aria-current','page');
    else button.removeAttribute('aria-current');
  });
  if (document.activeElement !== $('targetInput') && $('targetInput').value !== current.target) {
    $('targetInput').value = current.target;
  }
  $('targetInput').placeholder = details.placeholder;
  $('youtubeSettings').hidden = platform !== 'youtube';
  $('youtubePaste').hidden = platform !== 'youtube';
  $('youtubeActions').hidden = platform !== 'youtube';
  $('youtubeStop').hidden = !(current.downloading || current.discoveryId);
  $('youtubeStop').disabled = current.stopping;
  $('youtubeMaxCount').value = current.maxCount;
  $('youtubeLanguage').value = current.language;
  $('youtubeMode').value = current.transcriptionMode;
  $('youtubeCookieBrowser').value = current.cookieBrowser;
  $('youtubeMaxCount').disabled = /(?:\/shorts\/[\w-]{11}|youtu\.be\/[\w-]{11}|[?&]v=[\w-]{11})/.test(current.target);
  $('youtubeSettingHint').textContent = current.transcriptionMode === 'mai'
      ? 'MAI создаёт TXT и SRT с таймкодами. Распознавание через OpenRouter платное.'
      : current.transcriptionMode === 'whisper'
      ? 'Whisper распознаёт речь локально, без расходов на API.'
      : 'Будет скачано только видео. Субтитры и текст останутся пустыми.';
  $('youtubeResults').hidden = platform !== 'youtube' || !current.ytStats;
  if (current.ytStats) {
    $('youtubeDownloaded').textContent = formatCount(current.ytStats.downloaded);
    $('youtubeSkipped').textContent = formatCount(current.ytStats.skipped);
    $('youtubeFailed').textContent = formatCount(current.ytStats.failed);
    $('youtubeCancelled').textContent = formatCount(current.ytStats.cancelled);
    const log = $('youtubeLogLines');
    log.replaceChildren();
    for (const line of current.ytLogs.slice(0,15)) {
      const row = document.createElement('div');
      row.textContent = line;
      log.appendChild(row);
    }
  }
  const stats = snapshot?.platforms?.[platform];
  $('videoCount').textContent = stats ? formatCount(stats.count) : '—';
  $('spentAmount').textContent = stats ? money(stats.spent_usd) : '—';
  $('findButton').disabled = current.finding;
  $('findButton').textContent = current.finding ? 'Поиск…' : 'Найти →';
  $('errorBox').hidden = !current.error;
  $('errorBox').textContent = current.error;
  $('resultBox').hidden = !current.selection?.length;
  $('foundCount').textContent = formatCount(current.selection?.length);
  $('downloadButton').disabled = current.downloading;
  document.dispatchEvent(new CustomEvent('cm-view-change',{detail:{view}}));
  const p = current.progress;
  $('progressBox').hidden = !p;
  if (!p) return;
  $('progressTitle').textContent = p.title;
  $('progressDetail').textContent = p.detail || '';
  $('progressValue').textContent = p.value || '';
  const bar = $('progressTrack');
  bar.classList.toggle('indeterminate',p.percent === null);
  if (p.percent === null) {
    bar.removeAttribute('aria-valuenow');
    $('progressFill').style.width = '';
  } else {
    const n = Math.min(100, Math.max(0, Number(p.percent)));
    bar.setAttribute('aria-valuenow',String(Math.round(n)));
    $('progressFill').style.width = n + '%';
  }
}
// Only non-secret display settings are persisted in this browser.
const youtubePrefsKey = 'cm.youtube.settings.v1';
function saveYoutubePrefs() {
  const {cookieBrowser,maxCount,language,transcriptionMode} = contexts.youtube;
  try { localStorage.setItem(youtubePrefsKey, JSON.stringify({cookieBrowser,maxCount,language,transcriptionMode})); }
  catch (_) { /* Incognito/blocked storage: settings still work until refresh. */ }
}
for (const [id,key] of [
  ['youtubeCookieBrowser','cookieBrowser'],
  ['youtubeMaxCount','maxCount'],
  ['youtubeLanguage','language'],
  ['youtubeMode','transcriptionMode'],
]) {
  $(id).addEventListener('change',event => {
    contexts.youtube[key] = event.target.value;
    saveYoutubePrefs();
    if (key === 'maxCount' && !contexts.youtube.downloading) {
      contexts.youtube.selection = null;
      contexts.youtube.discoveryId = null;
      contexts.youtube.progress = null;
    }
    render();
  });
}
$('youtubePaste').addEventListener('click',async () => {
  try {
    if (!navigator.clipboard?.readText) throw Error('Вставь ссылку через Ctrl + V.');
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) throw Error('Буфер обмена пуст.');
    $('targetInput').value = text;
    $('targetInput').dispatchEvent(new Event('input',{bubbles:true}));
  } catch(ex) {
    contexts.youtube.error = ex.message || 'Не удалось прочитать буфер. Используй Ctrl + V.';
    render();
  }
});
$('youtubeStop').addEventListener('click',async () => {
  const current = contexts.youtube;
  if ((!current.jobIds.length && !current.discoveryId) || current.stopping) return;
  current.stopping = true;
  render();
  try {
    await api('/api/cancel',{
      platform:'youtube',ids:current.jobIds,
      ...(current.discoveryId ? {discovery_id:current.discoveryId} : {}),
    });
    if (current.discoveryId) {
      current.discoveryId = null;
      current.finding = false;
      current.stopping = false;
      current.progress = progress('Остановлено','Поиск Shorts отменён.');
    } else {
      current.progress = progress('Останавливаем…',
        'Текущая загрузка или распознавание завершит безопасную остановку.');
    }
  } catch(ex) {
    current.stopping = false;
    current.error = ex.message;
  }
  render();
});
$('youtubeOpenFolder').addEventListener('click',async () => {
  try { await api('/api/open-folder',{platform:'youtube'}); }
  catch(ex) { contexts.youtube.error = ex.message; render(); }
});
function cmCanLeaveTaxonomyStudio() {
    if(window.ContentMachineDeepV2?.confirmLeave()===false)return false;
  if(view !== 'projects')return true;
  if(!$('projectRun').hidden &&
     window.ContentMachineTaxonomyRun?.confirmLeave()===false)return false;
  return $('projectPipeline').hidden ||
    window.ContentMachineTaxonomyStudio?.confirmLeave()!==false;
}
$('settingsNav').addEventListener('click',() => {
  if(!cmCanLeaveTaxonomyStudio())return;
  view = 'settings';
  try { localStorage.setItem('cm.lastView','settings'); } catch (_) {}
  render();
});
document.querySelectorAll('[data-platform]').forEach(button => {
  button.addEventListener('click',() => {
    if(!cmCanLeaveTaxonomyStudio())return;
    contexts[platform].target = $('targetInput').value.trim();
    platform = button.dataset.platform;
    view = 'downloader';
    settingsFeedback('');
    try {
      localStorage.setItem('cm.lastPlatform', platform);
      localStorage.setItem('cm.lastView','downloader');
    } catch (_) {}
    // The input can still have focus after switching via keyboard.
    $('targetInput').value = ctx().target;
    render();
  });
});
$('targetInput').addEventListener('input',event => {
  const current = ctx();
  current.target = event.target.value;
  if (!current.downloading) {
    current.selection = null;
    current.discoveryId = null;
    current.progress = null;
    current.error = '';
  }
  render();
});
$('findForm').addEventListener('submit',async event => {
  event.preventDefault();
  const p = platform;
  const current = contexts[p];
  const target = $('targetInput').value.trim();
  if (!target) {
    current.error = 'Вставь ссылку на профиль или видео '+config[p].source+'.';
    return render();
  }
  current.target = target;
  current.error = '';
  current.selection = null;
  current.discoveryId = null;
  current.progress = null;
  current.finding = true;
  current.stopping = false;
  render();
  try {
    const result = await api('/api/find',{
      target,platform:p,...(p === 'youtube' ? {
        cookie_browser:current.cookieBrowser,
        max_count:Number(current.maxCount),
      } : {}),
    });
    if (result.kind === 'video') {
      current.selection = result.urls;
    } else {
      current.discoveryId = result.id;
      current.progress = progress('Поиск видео…','Получаем список роликов '+config[p].source+'.');
    }
  } catch(ex) {
    current.error = ex.message;
  } finally {
    current.finding = false;
    render();
  }
});
$('downloadButton').addEventListener('click',async () => {
  const p = platform;
  const current = contexts[p];
  if (!current.selection?.length || current.downloading) return;
  current.error = '';
  current.downloading = true;
  current.progress = progress('Добавление в очередь…','');
  render();
  try {
    const result = await api('/api/download',{
      urls:current.selection,platform:p,
      ...(p === 'youtube' ? {
        cookie_browser:current.cookieBrowser,
        transcription_mode:current.transcriptionMode,
        language:current.language,
      } : {}),
    });
    current.jobIds = (result.jobs || []).map(j => j.id);
    if (!current.jobIds.length) throw Error('Загрузчик не создал заданий.');
    if (p === 'youtube') {
      current.stopping = false;
      current.ytStats = {downloaded:0,skipped:0,failed:0,cancelled:0};
      current.ytLogs = [];
      try { localStorage.setItem('cm.youtube.activeJobs',JSON.stringify(current.jobIds)); }
      catch (_) { /* Storage optional. */ }
    }
    current.progress = progress('Скачивание…','0 из '+current.jobIds.length+' завершено',0,'0 / '+current.jobIds.length);
  } catch(ex) {
    current.error = ex.message;
    current.downloading = false;
    current.progress = null;
  }
  render();
});
function updateDownload(current,jobs,source='instagram') {
  if (!current.jobIds.length) return;
  const list = current.jobIds.map(id => jobs.find(j => j.id === id)).filter(Boolean);
  if (!list.length) return;
  const completed = list.filter(j => j.status === 'complete');
  const failures = list.filter(j => j.status === 'failed');
  const cancelled = list.filter(j => j.status === 'cancelled');
  const finished = completed.length + failures.length + cancelled.length;
  const total = current.jobIds.length;
  if (source === 'youtube') {
    const skipped = completed.filter(j => j.result === 'skipped').length;
    current.ytStats = {
      downloaded:completed.length - skipped,
      skipped, failed:failures.length, cancelled:cancelled.length,
    };
    const statuses = {complete:'✓ Готово',failed:'Ошибка',cancelled:'Остановлено',
                      queued:'В очереди',processing:'Обработка'};
    current.ytLogs = list.slice(-15).reverse().map(j =>
      (j.code || 'Short') + ' · ' + (statuses[j.status] || j.status) +
      (j.result === 'skipped' ? ' (уже загружен)' : '') +
      (j.error ? ': '+j.error : ''));
  }
  if (finished === total) {
    current.error = failures.length
      ? failures.length+' из '+total+' завершились с ошибкой: '+
        failures.slice(0,2).map(j => j.error || 'Неизвестная ошибка').join(' · ')
      : '';
    current.progress = progress(cancelled.length ? 'Остановлено' :
      failures.length ? 'Завершено с ошибками' : 'Готово',
      finished+' из '+total+' заданий завершено',100,finished+' / '+total);
    current.jobIds = [];
    current.downloading = false;
    current.stopping = false;
    if (source === 'youtube') {
      try { localStorage.removeItem('cm.youtube.activeJobs'); } catch (_) {}
    }
    return;
  }
  const active = list.find(j => j.status === 'processing') || list.find(j => j.status === 'queued');
  const phase = active?.phase || 'queued';
  const share = active?.progress;
  const hasPercent = phase === 'download' && typeof share === 'number' && Number.isFinite(share);
  const pct = hasPercent ? ((finished + share/100) / total) * 100 : null;
  const names = {download:'Скачивание видео',transcribe:'Распознавание речи',
                 prepare:'Проверка видео',queued:'В очереди'};
  current.progress = current.stopping
    ? progress('Останавливаем…',finished+' из '+total+' завершено')
    : progress(names[phase] || 'Обработка',finished+' из '+total+' обработано'+
      (active?.code ? ' · '+active.code : ''),pct,
      finished+' / '+total+(hasPercent?' · '+Math.round(share)+'%':''));
}
async function tick() {
  try {
    snapshot = await api('/api/state');
    for (const p of Object.keys(contexts)) {
      const current = contexts[p];
      if (current.discoveryId) {
        const job = (snapshot.discovery || []).find(j => j.id === current.discoveryId);
        if (job?.status === 'complete') {
          current.discoveryId = null;
          current.progress = null;
          if (job.urls?.length) current.selection = job.urls;
          else current.error = 'Профиль не вернул ссылки на видео.';
        } else if (job?.status === 'failed') {
          current.discoveryId = null;
          current.progress = null;
          current.error = job.error || 'Не удалось найти видео.';
        } else if (job?.status === 'cancelled') {
          current.discoveryId = null;
          current.stopping = false;
          current.progress = progress('Остановлено','Поиск Shorts отменён.');
        } else if (job) {
          current.progress = progress('Поиск видео…',job.count
             ? 'Найдено: '+job.count : 'Получаем публикации '+config[p].source+'.');
        }
      }
      if (p === 'youtube' && current.jobIds.length &&
          !current.jobIds.some(id => (snapshot.jobs || []).some(j => j.id === id))) {
        current.jobIds = [];
        current.downloading = false;
        current.stopping = false;
        current.progress = null;
        current.error = 'Задача недоступна после перезапуска сервера. Начни поиск заново.';
        try { localStorage.removeItem('cm.youtube.activeJobs'); } catch (_) {}
      } else {
        updateDownload(current,snapshot.jobs || [],p);
      }
    }
  } catch(ex) {
    const current = ctx();
    if (current.discoveryId || current.jobIds.length || !snapshot) {
      current.error = 'Нет связи с локальным сервером. Проверь ContentMachine.';
    }
  }
  render();
}
try {
  const last = localStorage.getItem('cm.lastPlatform');
  if (last && config[last]) platform = last;
  if (localStorage.getItem('cm.lastView') === 'settings') view = 'settings';
} catch (_) { /* Optional preference. */ }
const firstUrl = new URLSearchParams(location.search).get('url');
if (firstUrl) {
  view = 'downloader';
  platform = /(?:^|\/\/)(?:www\.|m\.)?tiktok\.com\//i.test(firstUrl) ? 'tiktok' :
    /(?:^|\/\/)(?:www\.|m\.|music\.)?youtube\.com\/|youtu\.be\//i.test(firstUrl) ? 'youtube' : 'instagram';
  contexts[platform].target = firstUrl;
}
// Restore YouTube preferences and unfinished jobs after reloading the tab.
try {
  const prefs = JSON.parse(localStorage.getItem(youtubePrefsKey) || '{}');
  const options = {
    cookieBrowser:['none','chrome','edge','firefox','brave'],
    maxCount:['0','5','20','50','100'],
    language:['auto','ru','en'],
    transcriptionMode:['mai','whisper','off'],
  };
  for (const [key,allowed] of Object.entries(options)) {
    if (allowed.includes(prefs[key])) contexts.youtube[key] = prefs[key];
  }
  const ids = JSON.parse(localStorage.getItem('cm.youtube.activeJobs') || '[]');
  if (Array.isArray(ids) && ids.length && ids.length <= 500 &&
      ids.every(x => typeof x === 'string' && /^[a-f0-9]{32}$/.test(x))) {
    contexts.youtube.jobIds = ids;
    contexts.youtube.downloading = true;
    contexts.youtube.progress = progress('Восстановление прогресса…','Читаем статус из сервера.');
  }
} catch (_) { /* Continue without local storage. */ }
$('targetInput').value = ctx().target;
document.addEventListener('cm-projects-open', () => {
  view = 'projects';
  render();
});
$('qdpNav').addEventListener('click',()=>{
  if(!cmCanLeaveTaxonomyStudio())return;
  view='qdp';
  render();
  document.dispatchEvent(new Event('cm-qdp-open'));
});
$('discoveryNav').addEventListener('click', () => {
  if(!cmCanLeaveTaxonomyStudio())return;
  view = 'discovery';
  render();
  document.dispatchEvent(new Event('cm-discovery-open'));
});
// A narrow display adapter; every existing leave/paid-action guard stays in place.
window.ContentMachineApp = Object.freeze({
  currentView:()=>view,
  canLeave:cmCanLeaveTaxonomyStudio,
  openView(next){
    if(!['workspace','projects','settings','discovery','qdp','downloader','test-lab'].includes(next))return false;
    if(view!==next&&!cmCanLeaveTaxonomyStudio())return false;
    const changed=view!==next;
    view=next;render();
    if(changed&&next==='qdp')document.dispatchEvent(new Event('cm-qdp-open'));
    if(changed&&next==='discovery')document.dispatchEvent(new Event('cm-discovery-open'));
    if(changed&&next==='test-lab')document.dispatchEvent(new Event('cm-test-lab-open'));
    return true;
  },
});
render();
tick();
setInterval(tick,1200);
