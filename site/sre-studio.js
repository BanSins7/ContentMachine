'use strict';
// Read-only SRE bootstrap. No POST, no worker, no OpenRouter or local storage.
(() => {
  const el = id => document.getElementById(id);
  const validStatus = new Set(['disabled', 'available', 'blocked_config',
    'blocked_dependency', 'blocked_policy']);
  const labels = {
    disabled: 'SRE отключён — включение выполняется администратором.',
    available: 'SRE прошёл проверку offline-готовности.',
    blocked_config: 'SRE пока не готов: требуется настройка или проверка БД.',
    blocked_dependency: 'SRE пока не готов: отсутствует необходимая зависимость.',
    blocked_policy: 'SRE пока не готов: права проекта не подтверждены.',
  };
  async function readJson(path) {
    const response = await fetch(path, {method: 'GET', cache: 'no-store',
      credentials: 'same-origin'});
    const data = await response.json();
    if (!data || typeof data !== 'object' || Array.isArray(data))
      throw Error('invalid response');
    return {response, data};
  }
  async function refresh() {
    const status = el('sreStatus');
    status.textContent = 'Проверяем доступность локального API…';
    el('sreRefresh').disabled = true;
    try {
      const manifest = await readJson('/api/sre/v1/manifest');
      if (!manifest.response.ok || !manifest.data.ok ||
          manifest.data.schema_version !== 'sre-module/v1')
        throw Error('manifest unavailable');
      el('sreVersion').textContent = manifest.data.schema_version;
      el('sreMode').textContent =
        manifest.data.deployment_readiness === 'disabled'
          ? 'Выключен по умолчанию' : 'Требует проверки конфигурации';
      const health = await readJson('/api/sre/v1/health');
      const key = validStatus.has(health.data.status) ? health.data.status : '';
      status.textContent = labels[key] || 'Проверка состояния недоступна.';
    } catch (_) {
      status.textContent = 'SRE недоступен в текущей конфигурации.';
    } finally {
      el('sreRefresh').disabled = false;
    }
  }
  el('sreRefresh').addEventListener('click', refresh);
  refresh();
})();
