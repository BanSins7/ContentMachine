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
};
const contexts = Object.fromEntries(Object.keys(config).map(key => [key, {
  target: '', selection: null, discoveryId: null, jobIds: [], finding: false,
  downloading: false, error: '', progress: null,
}]));
let platform = 'instagram';
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
  $('toolTitle').textContent = details.title;
  document.title = details.title+' — ContentMachine';
  document.querySelectorAll('[data-platform]').forEach(button => {
    const selected = button.dataset.platform === platform;
    button.classList.toggle('selected', selected);
    if (selected) button.setAttribute('aria-current','page');
    else button.removeAttribute('aria-current');
  });
  if (document.activeElement !== $('targetInput') && $('targetInput').value !== current.target) {
    $('targetInput').value = current.target;
  }
  $('targetInput').placeholder = details.placeholder;
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
document.querySelectorAll('[data-platform]').forEach(button => {
  button.addEventListener('click',() => {
    contexts[platform].target = $('targetInput').value.trim();
    platform = button.dataset.platform;
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
  render();
  try {
    const result = await api('/api/find',{target,platform:p});
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
    const result = await api('/api/download',{urls:current.selection,platform:p});
    current.jobIds = (result.jobs || []).map(j => j.id);
    if (!current.jobIds.length) throw Error('Загрузчик не создал заданий.');
    current.progress = progress('Скачивание…','0 из '+current.jobIds.length+' завершено',0,'0 / '+current.jobIds.length);
  } catch(ex) {
    current.error = ex.message;
    current.downloading = false;
    current.progress = null;
  }
  render();
});
function updateDownload(current,jobs) {
  if (!current.jobIds.length) return;
  const list = current.jobIds.map(id => jobs.find(j => j.id === id)).filter(Boolean);
  if (!list.length) return;
  const completed = list.filter(j => j.status === 'complete').length;
  const failures = list.filter(j => j.status === 'failed');
  const finished = completed + failures.length;
  const total = current.jobIds.length;
  if (finished === total) {
    current.error = failures.length
      ? failures.length+' из '+total+' не скачались: '+failures.slice(0,2).map(j => j.error || 'Неизвестная ошибка').join(' · ')
      : '';
    current.progress = progress(failures.length ? 'Завершено с ошибками' : 'Готово',
      completed+' из '+total+' видео готовы',100,finished+' / '+total);
    current.jobIds = [];
    current.downloading = false;
    return;
  }
  const active = list.find(j => j.status === 'processing') || list.find(j => j.status === 'queued');
  const phase = active?.phase || 'queued';
  const share = active?.progress;
  const hasPercent = phase === 'download' && typeof share === 'number' && Number.isFinite(share);
  const pct = hasPercent ? ((finished + share/100) / total) * 100 : null;
  const names = {download:'Скачивание видео',transcribe:'Распознавание речи',
                 prepare:'Проверка видео',queued:'В очереди'};
  current.progress = progress(names[phase] || 'Обработка',finished+' из '+total+' обработано'+
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
        } else if (job) {
          current.progress = progress('Поиск видео…',job.count
             ? 'Найдено: '+job.count : 'Получаем публикации '+config[p].source+'.');
        }
      }
      updateDownload(current,snapshot.jobs || []);
    }
  } catch(ex) {
    const current = ctx();
    if (current.discoveryId || current.jobIds.length || !snapshot) {
      current.error = 'Нет связи с локальным сервером. Проверь ContentMachine.';
    }
  }
  render();
}
const firstUrl = new URLSearchParams(location.search).get('url');
if (firstUrl) {
  platform = /(?:^|\/\/)(?:www\.|m\.)?tiktok\.com\//i.test(firstUrl) ? 'tiktok' : 'instagram';
  contexts[platform].target = firstUrl;
}
$('targetInput').value = ctx().target;
render();
tick();
setInterval(tick,1200);
