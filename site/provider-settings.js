'use strict';
/* Main ContentMachine provider settings, compatible with Carousel Studio.
 * Secrets are sent only to the localhost API and never re-rendered into HTML.
 */
(() => {
  const $ = id => document.getElementById(id);
  const form = $('providerSettingsForm');
  if (!form) return;
  const fields = ['cmOpenrouterKey','cmRusgptKey','cmBrightdataKey'];
  const labels = [
    ['cmOpenrouterKeyState','openrouter_configured'],
    ['cmRusgptKeyState','rusgpt_configured'],
    ['cmBrightdataKeyState','brightdata_configured'],
  ];
  const modelNames = {
    openrouter:'google/gemini-3.8-flash · OpenRouter',
    rusgpt:'anthropic/claude-haiku-5.5 · Rus-GPT',
  };
  let pending = false;
  let touched = false;
  let loadSeq = 0;
  let latestStatus = null;
  form.addEventListener('input',()=>{touched = true;});
  form.addEventListener('change',()=>{touched = true;});
  const feedback = (message, error = false) => {
    $('cmProviderFeedback').textContent = message;
    $('cmProviderFeedback').classList.toggle('is-error', error);
  };
  function paintReadiness(data) {
    const chosen = $('cmProvider').value === 'rusgpt' ? 'rusgpt' : 'openrouter';
    const hasLLM = chosen === 'rusgpt' ? data.rusgpt_configured : data.openrouter_configured;
    const ready = hasLLM === true && data.brightdata_ready === true;
    const text = ready ? 'Готово к анализу'
      : !hasLLM ? 'Нужен ключ '+(chosen === 'rusgpt'?'Rus-GPT':'OpenRouter')
      : !data.brightdata_configured ? 'Нужен токен Bright Data'
      : data.brightdata_authorized !== true ? 'Нужно разрешение Bright Data'
      : 'Bright Data пока недоступен';
    $('providerSettingsStatus').textContent = text;
    $('providerSettingsStatus').classList.toggle('is-connected',ready);
    $('providerSettingsStatus').classList.toggle('is-missing',!ready);
  }
  function paint(data) {
    latestStatus = data;
    $('cmProvider').value = data.preferred_provider === 'rusgpt' ? 'rusgpt':'openrouter';
    $('cmProviderModel').textContent = 'Модель: ' + modelNames[$('cmProvider').value];
    $('cmBrightdataAuthorized').checked = data.brightdata_authorized === true;
    for (const [id, property] of labels) {
      $(id).textContent = data[property] ? 'Ключ настроен' : 'Нет ключа';
      $(id).classList.toggle('is-ready',Boolean(data[property]));
    }
    paintReadiness(data);
    for(const id of fields) $(id).value = '';
  }
  async function load() {
    if (pending) return;
    const seq = ++loadSeq;
    try {
      const response = await fetch('/api/provider-settings', {cache:'no-store'});
      const data = await response.json();
      if (!response.ok || data.error) throw Error(data.error || 'HTTP ' + response.status);
      if (seq !== loadSeq || touched) return;
      paint(data);
      feedback('');
    } catch (err) {
      if (seq !== loadSeq) return;
      feedback(err.message || 'Не удалось прочитать настройки.',true);
    }
  }
  $('cmProvider').addEventListener('change',() => {
    $('cmProviderModel').textContent = 'Модель: ' + modelNames[$('cmProvider').value];
    if (latestStatus) paintReadiness(latestStatus);
  });
  $('cmProviderRefresh').addEventListener('click',() => {touched = false;void load();});
  form.addEventListener('submit',async event => {
    event.preventDefault();
    if(pending) return;
    pending = true;
    ++loadSeq;
    $('cmProviderSave').disabled = true;
    feedback('Сохраняем настройки…');
    try {
      const payload = {
        preferred_provider: $('cmProvider').value,
        openrouter_api_key: $('cmOpenrouterKey').value,
        rusgpt_api_key: $('cmRusgptKey').value,
        brightdata_api_token: $('cmBrightdataKey').value,
        brightdata_authorized: $('cmBrightdataAuthorized').checked,
      };
      const response = await fetch('/api/provider-settings',{
        method:'POST',
        cache:'no-store',
        headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
        body:JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok || data.error) throw Error(data.error || 'HTTP ' + response.status);
      touched = false;
      paint(data);
      feedback('Настройки сохранены локально.');
    } catch(err) {
      feedback(err.message || 'Не удалось сохранить ключи.',true);
    } finally {
      pending = false;
      $('cmProviderSave').disabled = false;
    }
  });
  document.addEventListener('cm-view-change',event => {
    if (event.detail && event.detail.view === 'settings') {touched = false;void load();}
  });
  if (window.ContentMachineApp?.currentView() === 'settings') void load();
})();
