'use strict';
/* Test Lab: saved project references and navigation bookmarks, never a paid run. */
(() => {
  const $ = id => document.getElementById(id);
  const stages = window.ContentMachineWorkspaceCore.stages;
  const validId = window.ContentMachineWorkspaceCore.projectId;
  let projects = [], scenarios = [], selectedId = '', loading = 0, busy = false;

  const node = (tag, cls, value) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (value !== undefined) el.textContent = value;
    return el;
  };
  const current = () => scenarios.find(item => item.id === selectedId);
  const label = stageId => stages.find(stage => stage.id === stageId)?.name || stageId;
  function message(value, isError = false) {
    const el = $('testLabMessage');
    el.textContent = value || '';
    el.hidden = !value;
    el.classList.toggle('is-error', isError);
  }
  async function api(path, data) {
    const options = data === undefined ? {cache:'no-store'} : {
      method:'POST',
      headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(data),
    };
    const response = await fetch(path, options);
    let result;
    try { result = await response.json(); }
    catch { throw Error('Сервер вернул некорректный ответ.'); }
    if (!response.ok || !result.ok) throw Error(result.error || 'HTTP ' + response.status);
    return result;
  }
  function stageOptions(select) {
    select.replaceChildren();
    for (const stage of stages) {
      const option = node('option', '', stage.number + ' · ' + stage.name);
      option.value = stage.id;
      select.append(option);
    }
  }
  function renderProjects() {
    const select = $('testLabSource');
    const saved = select.value;
    select.replaceChildren();
    const initial = node('option', '', 'Выбери существующий проект');
    initial.value = '';
    select.append(initial);
    for (const project of projects) {
      const option = node('option', '', project.name);
      option.value = project.id;
      select.append(option);
    }
    const suggestion = projects.find(p => /от[её]к|otek|edema/i.test(p.name))?.id;
    select.value = projects.some(p => p.id === saved) ? saved : (suggestion || '');
    $('testLabCreate').disabled = !select.value || busy;
    $('testLabNoProjects').hidden = projects.length > 0;
  }
  function renderScenarios() {
    const host = $('testLabSavedList');
    host.replaceChildren();
    $('testLabEmpty').hidden = scenarios.length > 0;
    for (const scenario of scenarios) {
      const button = node('button', 'testlab-scenario' + (scenario.id === selectedId ? ' is-active' : ''));
      button.type = 'button';
      button.setAttribute('aria-pressed', String(scenario.id === selectedId));
      button.append(node('strong', '', scenario.name));
      button.append(node('span', '', scenario.project_name + ' · До ' + label(scenario.target_stage)));
      button.addEventListener('click', () => {
        selectedId = scenario.id; renderScenarios(); renderDetails();
      });
      host.append(button);
    }
  }
  function renderDetails() {
    const scenario = current(), panel = $('testLabDetails');
    panel.hidden = !scenario;
    if (!scenario) return;
    $('testLabScenarioTitle').textContent = scenario.name;
    $('testLabSourceName').textContent = 'Источник: ' + scenario.project_name;
    $('testLabTarget').value = scenario.target_stage;
    $('testLabTarget').disabled = busy || !scenario.project_available;
    $('testLabResume').disabled = busy || !scenario.project_available;
    $('testLabLastOpened').textContent = scenario.last_opened_stage
      ? 'Последний открытый экран: ' + label(scenario.last_opened_stage) +
        ' · ' + new Date(scenario.last_opened_at).toLocaleString('ru-RU')
      : 'Этапы ещё не открывались в этом сценарии.';
    const host = $('testLabStageList'); host.replaceChildren();
    const limit = stages.findIndex(stage => stage.id === scenario.target_stage);
    for (const stage of stages) {
      const enabled = stage.index <= limit;
      const button = node('button', 'testlab-stage' +
        (stage.id === scenario.target_stage ? ' is-target' : '') +
        (stage.id === scenario.last_opened_stage ? ' is-recent' : ''));
      button.type = 'button';
      button.disabled = !enabled || !scenario.project_available || busy;
      const number = node('span', 'testlab-stage-number', stage.number);
      const name = node('span', 'testlab-stage-name');
      name.append(node('strong', '', stage.name));
      name.append(node('small', '', stage.short));
      const status = node('span', 'testlab-stage-action', !enabled ? 'Вне предела' :
        stage.id === scenario.last_opened_stage ? 'Открыть снова →' :
        (stage.surface ? 'Открыть модуль →' : 'Открыть обзор →'));
      button.append(number, name, status);
      button.addEventListener('click', () => openStage(stage.id));
      host.append(button);
    }
    $('testLabAvailability').hidden = scenario.project_available;
  }
  function render() {
    renderProjects(); renderScenarios(); renderDetails();
  }
  async function load(prefer) {
    const ticket = ++loading;
    message('');
    try {
      const response = await api('/api/test-lab');
      if (ticket !== loading) return;
      projects = (response.projects || []).filter(p => validId(p.id));
      scenarios = (response.scenarios || []).filter(p => validId(p.id));
      selectedId = scenarios.some(p => p.id === (prefer || selectedId))
        ? (prefer || selectedId) : (scenarios[0]?.id || '');
      render();
    } catch (error) {
      if (ticket === loading) message('Не удалось загрузить библиотеку: ' + error.message, true);
    }
  }
  async function createScenario(event) {
    event.preventDefault();
    if (busy) return;
    const projectId = $('testLabSource').value;
    if (!projects.some(p => p.id === projectId)) {
      message('Сначала выбери исходный проект.', true); return;
    }
    busy = true; $('testLabCreate').disabled = true; message('');
    try {
      const result = await api('/api/test-lab', {
        project_id: projectId, name: $('testLabName').value.trim(),
        target_stage: $('testLabInitialTarget').value,
      });
      $('testLabName').value = '';
      await load(result.scenario.id);
      message('Тестовый сценарий сохранён. Исходный проект и его результаты не изменены.');
    } catch (error) {
      message(error.message, true);
    } finally { busy = false; render(); }
  }
  async function updateTarget() {
    const scenario = current();
    if (!scenario || busy) return;
    const target = $('testLabTarget').value;
    if (target === scenario.target_stage) return;
    busy = true; renderDetails();
    try {
      const result = await api('/api/test-lab/' + scenario.id, {
        expected_revision: scenario.revision, target_stage: target,
      });
      scenarios = scenarios.map(s => s.id === scenario.id ? result.scenario : s);
      message('Предел тестирования сохранён. Все результаты исходного проекта остались на месте.');
    } catch (error) {
      message(error.message + ' Обнови список перед повтором.', true);
      await load(scenario.id);
    } finally { busy = false; renderDetails(); renderScenarios(); }
  }
  async function openStage(stageId) {
    const scenario = current();
    if (!scenario || busy || !scenario.project_available) return;
    const targetIdx = stages.findIndex(stage => stage.id === scenario.target_stage);
    const step = stages.find(stage => stage.id === stageId);
    if (!step || step.index > targetIdx) return;
    busy = true; renderDetails(); message('');
    try {
      const result = await api('/api/test-lab/' + scenario.id, {
        expected_revision: scenario.revision, last_opened_stage: stageId,
      });
      scenarios = scenarios.map(s => s.id === scenario.id ? result.scenario : s);
      if (!window.ContentMachineWorkspace.openStage(scenario.project_id, stageId))
        throw Error('Не удалось открыть этап. Проверь, что проект выбран и доступен.');
    } catch (error) {
      message(error.message, true);
      if (/другой вкладке/.test(error.message)) await load(scenario.id);
    } finally { busy = false; renderDetails(); renderScenarios(); }
  }
  $('testLabCreateForm').addEventListener('submit', createScenario);
  $('testLabSource').addEventListener('change', renderProjects);
  $('testLabTarget').addEventListener('change', updateTarget);
  $('testLabResume').addEventListener('click', () => {
    const scenario = current();
    if (scenario) openStage(scenario.last_opened_stage || scenario.target_stage);
  });
  $('testLabRefresh').addEventListener('click', () => load());
  $('testLabNewProject').addEventListener('click', () => window.ContentMachineProjects.create());
  $('testLabNav').addEventListener('click', () => {
    if (location.hash === '#tools/test-lab')
      window.ContentMachineApp.openView('test-lab');
    else location.hash = '#tools/test-lab';
  });
  document.addEventListener('cm-test-lab-open', () => { void load(); });
  stageOptions($('testLabInitialTarget'));
  stageOptions($('testLabTarget'));
  $('testLabInitialTarget').value = 'search';
  if (window.ContentMachineApp.currentView() === 'test-lab') void load();
})();
