'use strict';
const $ = id => document.getElementById(id);
const api = async (path, payload) => {
  const options = payload === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(payload)};
  const response = await fetch(path, options);
  let body;
  try { body = await response.json(); } catch { throw Error('Не удалось прочитать ответ сервера.'); }
  if (!response.ok || body.error) throw Error(body.error || `Ошибка ${response.status}`);
  return body;
};
const form = $('findForm');
const input = $('targetInput');
const findBtn = $('findButton');
const downloadBtn = $('downloadButton');
let selection = null;
let discoveryId = null;
let jobIds = [];
let currentMode = 'idle';
const error = message => { $('errorBox').hidden = !message; $('errorBox').textContent = message || ''; };
const showResult = urls => {
  selection = urls;
  $('foundCount').textContent = urls.length.toLocaleString('ru-RU');
  $('resultBox').hidden = false;
  downloadBtn.disabled = false;
};
const setProgress = (title, detail, percent = null, value = '') => {
  $('progressBox').hidden = false;
  $('progressTitle').textContent = title;
  $('progressDetail').textContent = detail || '';
  $('progressValue').textContent = value;
  const bar = $('progressTrack');
  bar.classList.toggle('indeterminate',percent === null);
  if (percent !== null) {
    const p = Math.max(0,Math.min(100,percent));
    $('progressFill').style.width = `${p}%`;
    bar.setAttribute('aria-valuenow',String(Math.round(p)));
  } else {
    bar.removeAttribute('aria-valuenow');
    $('progressFill').style.width = '';
  }
};
const formatMoney = value => '$' + Number(value || 0).toFixed(4);
function resetSearch() {
  selection = null;
  discoveryId = null;
  jobIds = [];
  $('resultBox').hidden = true;
  $('progressBox').hidden = true;
  error('');
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  const target = input.value.trim();
  if (!target) {error('Вставь ссылку на Instagram-профиль или Reels.'); return;}
  resetSearch();
  currentMode = 'finding';
  findBtn.disabled = true;
  findBtn.textContent = 'Поиск…';
  try {
    const result = await api('/api/find',{target});
    if (result.kind === 'video') {
      showResult(result.urls);
      currentMode = 'ready';
    } else {
      discoveryId = result.id;
      setProgress('Поиск видео…','Получаем список Reels из профиля Instagram.');
    }
  } catch (e) {
    currentMode = 'idle';
    error(e.message);
  } finally {
    findBtn.disabled = false;
    findBtn.textContent = 'Найти →';
  }
});
input.addEventListener('input',() => { if (currentMode !== 'downloading') resetSearch(); });
downloadBtn.addEventListener('click',async () => {
  if (!selection || !selection.length) return;
  error('');
  downloadBtn.disabled = true;
  currentMode = 'downloading';
  setProgress('Добавление в очередь…','');
  try {
    const account = findAccount(input.value);
    const result = await api('/api/download',{urls:selection,account});
    jobIds = result.jobs.map(j => j.id);
    setProgress('Скачивание…',`0 из ${jobIds.length} завершено`,0,`0 / ${jobIds.length}`);
  } catch (e) {
    error(e.message);
    $('progressBox').hidden = true;
    currentMode = 'ready';
    downloadBtn.disabled = false;
  }
});
function findAccount(target) {
  const match = target.match(/^(?:https?:\/\/)?(?:www\.)?instagram\.com\/(?!reel\/|reels\/|p\/|tv\/)([A-Za-z0-9._]{1,30})(?:\/reels)?\/?(?:\?.*)?$/i);
  if (match) return match[1];
  if (/^@?[A-Za-z0-9._]{1,30}$/.test(target)) return target.replace(/^@/,'');
  return 'instagram';
}
function updateDownload(jobs) {
  if (!jobIds.length) return;
  const list = jobIds.map(id => jobs.find(j => j.id===id)).filter(Boolean);
  if (!list.length) return;
  const completed = list.filter(j => j.status==='complete').length;
  const failed = list.filter(j => j.status==='failed');
  const ended = completed + failed.length;
  const active = list.find(j => j.status==='processing') || list.find(j => j.status==='queued');
  if (ended === jobIds.length) {
    if (failed.length) {
      error(`${failed.length} из ${jobIds.length} не скачались: ${failed.slice(0,2).map(j => j.error || 'Неизвестная ошибка').join(' · ')}`);
      setProgress('Завершено с ошибками',`${completed} из ${jobIds.length} готово`,ended/jobIds.length*100,`${ended} / ${jobIds.length}`);
    } else {
      setProgress('Готово',`${completed} видео сохранено вместе с субтитрами`,100,`${completed} / ${jobIds.length}`);
    }
    jobIds = [];
    currentMode = 'ready';
    downloadBtn.disabled = false;
    return;
  }
  const phase = active?.phase || (active?.status==='queued'?'В очереди':'Обработка');
  const part = active?.progress;
  const hasPercent = typeof part === 'number' && Number.isFinite(part) && phase==='download';
  const percent = hasPercent ? (ended + part/100)/jobIds.length*100 : null;
  const stage = ({download:'Скачивание видео',transcribe:'Распознавание речи',prepare:'Проверка файла',queued:'Ожидание'})[phase] || 'Обработка';
  setProgress(stage,`${ended} из ${jobIds.length} обработано${active?.code?' · '+active.code:''}`,percent,`${ended} / ${jobIds.length}${hasPercent?' · '+Math.round(part)+'%':''}`);
}
async function tick() {
  try {
    const state = await api('/api/state');
    $('videoCount').textContent = state.library.length.toLocaleString('ru-RU');
    $('spentAmount').textContent = formatMoney(state.configuration.spent_usd);
    if (discoveryId) {
      const item = (state.discovery || []).find(j => j.id === discoveryId);
      if (item?.status === 'complete') {
        discoveryId = null;
        $('progressBox').hidden = true;
        if (!item.urls?.length) {error('Instagram не вернул ссылки на видео этого профиля.');currentMode='idle';}
        else {showResult(item.urls);currentMode='ready';}
      } else if (item?.status === 'failed') {
        discoveryId = null;
        currentMode = 'idle';
        $('progressBox').hidden = true;
        error(item.error || 'Не удалось получить список видео из Instagram.');
      } else if (item) {
        setProgress('Поиск видео…',item.count ? `Найдено: ${item.count}` : 'Получаем публикации профиля Instagram.');
      }
    }
    updateDownload(state.jobs || []);
  } catch (e) {
    if (discoveryId || jobIds.length) error('Нет связи с локальным сервером. Проверь окно START_CONTENTMACHINE.bat.');
    else if ($('videoCount').textContent==='—') error('Локальный сервер не отвечает. Запусти START_CONTENTMACHINE.bat.');
  }
}
const startUrl = new URLSearchParams(location.search).get('url');
if (startUrl) input.value = startUrl;
tick();
setInterval(tick,1200);
