'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const state = {reference: null, dataUrl: '', activeId: '', polling: null, busy: false, items: []};
  const labels = {queued: 'В очереди', running: 'Генерация…', succeeded: 'Готово', failed: 'Ошибка'};
  const validTypes = ['image/png', 'image/jpeg', 'image/webp'];
  const MAX_IMAGE = 10 * 1024 * 1024;

  async function api(path, payload) {
    let response;
    try {
      response = await fetch(path, payload === undefined ? {cache: 'no-store'} : {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-ContentMachine': '1'},
        body: JSON.stringify(payload)
      });
    } catch (_error) {
      throw {code: 'network_error', message: 'Нет связи с локальным сервером. Проверь запуск ContentMachine.'};
    }
    let body;
    try { body = await response.json(); }
    catch (_) { throw {code: 'bad_response', message: 'Сервер вернул ответ в неверном формате. Обнови страницу.'}; }
    if (!response.ok || !body.ok) {
      throw body.error || {code: 'server_error', message: 'Не удалось выполнить запрос.'};
    }
    return body;
  }

  function setBadge(status) {
    const el = $('resultState');
    el.textContent = labels[status] || 'Нет результата';
    el.className = 'status-chip' +
      (status === 'succeeded' ? ' success' : status === 'failed' ? ' failure' :
        (status === 'queued' || status === 'running') ? ' active' : '');
  }
  function showError(error) {
    const e = typeof error === 'object' && error ? error : {message: String(error)};
    $('alertBox').hidden = false;
    $('alertTitle').textContent = {
      key_missing: 'OpenAI API не настроен',
      network_error: 'Ошибка соединения',
      provider_timeout: 'Истекло время ожидания',
      invalid_reference: 'Ошибка референса',
      provider_unreachable: 'OpenAI недоступен',
      worker_unavailable: 'Генератор недоступен',
      insufficient_quota: 'Лимит API исчерпан',
      rate_limit_exceeded: 'Лимит запросов'
    }[e.code] || 'Не удалось выполнить генерацию';
    $('alertText').textContent = e.message || 'Неизвестная ошибка. Попробуй ещё раз вручную.';
    $('alertRequestId').hidden = !e.request_id;
    $('alertRequestId').textContent = e.request_id ? 'OpenAI Request ID: ' + e.request_id : '';
  }
  function hideError() { $('alertBox').hidden = true; }
  function setBusy(busy) {
    state.busy = busy;
    $('generateButton').disabled = busy;
    $('referenceInput').disabled = busy;
    $('generateLabel').textContent = busy ? 'Генерация выполняется…' : 'Сгенерировать фотографию';
    $('buttonArrow').textContent = busy ? '⌛' : '↗';
  }
  function showEmpty() {
    $('resultEmpty').hidden = false;
    $('resultWorking').hidden = true;
    $('resultImage').hidden = true;
    $('resultActions').hidden = true;
    setBadge('');
  }
  function showWorking(job) {
    hideError();
    $('resultEmpty').hidden = true;
    $('resultImage').hidden = true;
    $('resultWorking').hidden = false;
    $('resultActions').hidden = true;
    $('workingTitle').textContent = job.status === 'queued' ? 'Подготовка генерации…' : 'Создаём фотографию…';
    $('workingHint').textContent = 'Запрос выполняется на сервере. Результат сохранится в истории автоматически.';
    setBadge(job.status);
  }
  function showResult(job) {
    $('resultEmpty').hidden = true;
    $('resultWorking').hidden = true;
    $('resultImage').hidden = false;
    $('resultImage').src = job.image_url + '?v=' + encodeURIComponent(job.updated_at || '');
    $('resultImage').onerror = () => {
      $('resultImage').hidden = true;
      showError({code: 'file_missing', message: 'Не удалось загрузить сохранённое изображение.'});
    };
    $('resultActions').hidden = false;
    $('downloadResult').href = job.image_url;
    $('downloadResult').download = 'avatar-' + job.id.slice(0, 8) + '.png';
    const cost = job.estimated_cost_usd;
    $('resultMeta').textContent = job.size + ' · ' + job.quality.toUpperCase() +
      (typeof cost === 'number' ? ' · ≈ $' + cost.toFixed(4) : '');
    setBadge('succeeded');
  }
  function stopPolling() {
    if (state.polling) clearTimeout(state.polling);
    state.polling = null;
  }
  async function watch(job) {
    if (job.id !== state.activeId) return;
    if (job.status === 'succeeded') {
      stopPolling();
      setBusy(false);
      hideError();
      showResult(job);
      await loadHistory();
    } else if (job.status === 'failed') {
      stopPolling();
      setBusy(false);
      $('resultWorking').hidden = true;
      $('resultEmpty').hidden = false;
      $('resultImage').hidden = true;
      $('resultActions').hidden = true;
      setBadge('failed');
      showError(job.error || {message: 'Генерация завершилась с ошибкой.'});
      await loadHistory();
    } else {
      setBusy(true);
      showWorking(job);
      stopPolling();
      state.polling = setTimeout(async () => {
        if (state.activeId !== job.id) return;
        try {
          const next = await api('/api/avatar-studio/jobs/' + job.id);
          await watch(next.job);
        } catch (error) {
          setBusy(false);
          showError({code: 'network_error',
            message: 'Не удалось проверить состояние генерации. Запрос может продолжаться на сервере. Обнови историю перед повтором.'});
          await loadHistory().catch(() => {});
        }
      }, 2500);
    }
  }
  async function loadFile(file) {
    if (!file) return;
    if (!validTypes.includes(file.type)) {
      showError({code: 'invalid_reference', message: 'Выбери PNG, JPEG или WebP.'});
      return;
    }
    if (file.size <= 0 || file.size > MAX_IMAGE) {
      showError({code: 'reference_too_large', message: 'Размер файла должен быть от 1 байта до 10 МБ.'});
      return;
    }
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      state.reference = file;
      state.dataUrl = dataUrl;
      $('referencePreview').src = dataUrl;
      $('referencePreview').hidden = false;
      $('referenceName').textContent = file.name;
      $('referenceName').hidden = false;
      $('emptyReference').hidden = true;
      $('clearReference').hidden = false;
      hideError();
    } catch (_) {
      showError({code: 'read_failed', message: 'Не удалось прочитать изображение. Выбери другой файл.'});
    }
  }
  function clearReference() {
    state.reference = null;
    state.dataUrl = '';
    $('referenceInput').value = '';
    $('referencePreview').hidden = true;
    $('referencePreview').removeAttribute('src');
    $('referenceName').hidden = true;
    $('emptyReference').hidden = false;
    $('clearReference').hidden = true;
  }
  async function generate(event) {
    event.preventDefault();
    if (state.busy) return;
    hideError();
    const prompt = $('promptInput').value.trim();
    if (!state.dataUrl) return showError({code: 'invalid_reference', message: 'Сначала загрузи фотографию-референс.'});
    if (prompt.length < 20 || prompt.length > 15000) {
      return showError({code: 'invalid_prompt', message: 'Промпт должен содержать от 20 до 15 000 символов.'});
    }
    stopPolling();
    setBusy(true);
    $('resultWorking').hidden = false;
    $('resultEmpty').hidden = true;
    $('resultImage').hidden = true;
    $('resultActions').hidden = true;
    $('workingTitle').textContent = 'Отправляем запрос…';
    $('workingHint').textContent = 'Референс отправляется в OpenAI для генерации фотографии.';
    setBadge('queued');
    try {
      const res = await api('/api/avatar-studio/generate', {
        reference: state.dataUrl, prompt, quality: $('qualityInput').value, size: $('sizeInput').value
      });
      state.activeId = res.job.id;
      await watch(res.job);
      await loadHistory();
    } catch (error) {
      setBusy(false);
      $('resultWorking').hidden = true;
      $('resultEmpty').hidden = false;
      setBadge('failed');
      showError(error);
    }
  }
  function timeLabel(value) {
    try { return new Intl.DateTimeFormat('ru-RU', {day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit'}).format(new Date(value)); }
    catch (_) { return ''; }
  }
  function historyCard(job) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'history-item' + (state.activeId === job.id ? ' selected' : '');
    const thumb = document.createElement('img');
    thumb.className = 'history-thumb';
    thumb.alt = '';
    thumb.loading = 'lazy';
    thumb.src = job.image_url || job.reference_url || '';
    const details = document.createElement('span');
    details.className = 'history-details';
    const prompt = document.createElement('span');
    prompt.className = 'history-prompt';
    prompt.textContent = job.prompt || 'Без описания';
    prompt.title = job.prompt || '';
    const time = document.createElement('span');
    time.className = 'history-date' + (job.status === 'failed' ? ' history-failed' : '');
    time.textContent = timeLabel(job.created_at) + ' · ' + (labels[job.status] || job.status);
    details.append(prompt, time);
    button.append(thumb, details);
    button.addEventListener('click', () => selectHistory(job));
    return button;
  }
  async function selectHistory(job) {
    stopPolling();
    state.activeId = job.id;
    $('promptInput').value = job.prompt || '';
    $('qualityInput').value = job.quality || 'high';
    $('sizeInput').value = job.size || '1024x1536';
    updateCounter();
    try {
      if (job.reference_url) {
        const response = await fetch(job.reference_url, {cache: 'no-store'});
        if (!response.ok) throw new Error('file unavailable');
        const blob = await response.blob();
        await loadFile(new File([blob], 'reference.' + (blob.type.split('/')[1] || 'png'),
          {type: blob.type}));
      }
    } catch (_) {
      clearReference();
      showError({code: 'reference_missing', message: 'Не удалось восстановить референс из истории.'});
    }
    if (job.status === 'succeeded') {
      setBusy(false);
      hideError();
      showResult(job);
    } else if (job.status === 'failed') {
      setBusy(false);
      $('resultWorking').hidden = true;
      $('resultImage').hidden = true;
      $('resultEmpty').hidden = false;
      $('resultActions').hidden = true;
      setBadge('failed');
      showError(job.error || {message: 'Неудачная генерация.'});
    } else {
      await watch(job);
    }
    renderHistory();
  }
  function renderHistory() {
    const list = $('historyList');
    list.replaceChildren();
    if (!state.items.length) {
      const empty = document.createElement('p');
      empty.className = 'history-placeholder';
      empty.textContent = 'История пока пуста.';
      list.appendChild(empty);
    } else {
      for (const job of state.items.slice(0, 12)) list.appendChild(historyCard(job));
    }
  }
  async function loadHistory() {
    const data = await api('/api/avatar-studio/history');
    state.items = data.jobs || [];
    renderHistory();
  }
  function updateCounter() {
    const value = $('promptInput').value.length;
    $('promptCounter').textContent = value.toLocaleString('ru-RU') + ' / 15 000';
  }

  $('generateForm').addEventListener('submit', generate);
  $('promptInput').addEventListener('input', updateCounter);
  $('referenceInput').addEventListener('change', e => loadFile(e.target.files[0]));
  $('clearReference').addEventListener('click', clearReference);
  const zone = $('dropZone');
  for (const type of ['dragenter', 'dragover']) zone.addEventListener(type, e => {
    e.preventDefault(); zone.classList.add('dragging');
  });
  for (const type of ['dragleave', 'drop']) zone.addEventListener(type, e => {
    e.preventDefault(); zone.classList.remove('dragging');
  });
  zone.addEventListener('drop', e => { if (!state.busy) loadFile(e.dataTransfer.files[0]); });
  $('refreshHistory').addEventListener('click', () => loadHistory().catch(showError));
  showEmpty();
  Promise.all([
    api('/api/avatar-studio/status').then(data => { $('keyNotice').hidden = data.configured; })
      .catch(showError),
    loadHistory().then(() => {
      const active = state.items.find(j => j.status === 'queued' || j.status === 'running');
      if (active) { state.activeId = active.id; watch(active); }
    }).catch(showError)
  ]);
})();
