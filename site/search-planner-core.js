'use strict';
/* SP-14 browser/Node-shared safe formatting, policy and exact HTTP transport.
 * No localStorage/sessionStorage/cookie/IndexedDB; no secrets in URLs/JSON. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.CMSearchPlannerCore = api;
})(typeof window !== 'undefined' ? window : null, function () {
  const PROJECT_RE = /^[a-f0-9]{32}$/;
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const KEY_RE = /^[A-Za-z0-9_.:-]{12,128}$/;
  const API_RESPONSE = 'search-planner-http-response/v1';
  const QV_DEFAULT = Object.freeze({
    schema_version:'query-validation-config/v1',
    min_positive_similarity:0.36,
    max_negative_similarity:0.94,
    min_positive_negative_margin:-0.12,
    semantic_duplicate_similarity:0.94,
    mmr_lambda:0.72,
    max_selected_per_hypothesis:8,
    angle_diversity_bonus:0.03,
    cross_hypothesis_dedup:true
  });
  function projectId(value) {
    if (typeof value !== 'string' || !PROJECT_RE.test(value)) throw Error('Некорректный проект');
    return value;
  }
  function uuid(value) {
    if (typeof value !== 'string' || !UUID_RE.test(value)) throw Error('Укажи канонический UUID');
    return value;
  }
  function idempotencyKey(value) {
    if (typeof value !== 'string' || !KEY_RE.test(value)) throw Error('Некорректный idempotency key');
    return value;
  }
  function newKey() {
    // Secure context 127.0.0.1 supports crypto.randomUUID; no Math.random
    // substitution for a financial or immutable approval idempotency key.
    if (typeof crypto === 'undefined' || typeof crypto.randomUUID !== 'function') {
      throw Error('Безопасный генератор ключей недоступен');
    }
    return 'sp-ui-'+crypto.randomUUID();
  }
  function json(text, emptyValue) {
    const source = String(text || '').trim();
    if (!source) return emptyValue;
    let value;
    try { value = JSON.parse(source); }
    catch (_) { throw Error('Неверный JSON. Проверь поля и формат.'); }
    if (value === null || typeof value !== 'object' || Array.isArray(value))
      throw Error('Ожидается JSON-объект');
    return value;
  }
  function csv(value, {allowEmpty=false, max=10}={}) {
    const raw=String(value || '').trim();
    if (!raw && allowEmpty) return [];
    const entries=raw.split(',').map(x=>x.trim()).filter(Boolean);
    if (!entries.length || entries.length > max || new Set(entries).size !== entries.length)
      throw Error('Неверный список языков или рынков');
    return entries;
  }
  function natural(value, max, label) {
    const n=Number(value);
    if (!Number.isSafeInteger(n) || n < 1 || n > max) throw Error('Некорректный лимит: '+label);
    return n;
  }
  function policy(data) {
    const languages=csv(data.languages);
    if (languages.some(l=>!/^[a-z]{2,3}(?:-[A-Z]{2})?$/.test(l))) throw Error('Язык должен иметь вид ru, en или pt-BR');
    const markets=csv(data.markets, {allowEmpty:true});
    if (markets.some(m=>m.length < 2 || m.length > 50)) throw Error('Некорректный рынок');
    const modes=[];
    if (data.keyword === true) modes.push('keyword');
    if (data.hashtag === true) modes.push('hashtag');
    if (!modes.length) throw Error('Выбери хотя бы один режим поиска');
    const target=natural(data.target,16,'цель');
    const perHyp=natural(data.perHyp,16,'на гипотезу');
    const total=natural(data.total,480,'общий');
    if (target>perHyp) throw Error('Цель превышает максимум на гипотезу');
    return {languages, markets,allowed_modes:modes,
      target_candidates_per_hypothesis:target,
      max_candidates_per_hypothesis:perHyp,
      max_total_candidates:total};
  }
  function requireUsd(amount) {
    const value=String(amount || '').trim();
    if (!/^\d{1,5}(?:\.\d{1,8})?$/.test(value) || Number(value)<=0 || Number(value)>10000)
      throw Error('Укажи положительный USD лимит, максимум 10000 и 8 знаков после точки');
    return value;
  }
  function createClient(fetcher, project) {
    if (typeof fetcher !== 'function') throw Error('Fetch not configured');
    const pid=projectId(project);
    const base='/api/projects/'+pid+'/search-planner';
    let secret='';
    function setSecret(value) {
      if (typeof value !== 'string' || value.length < 32 || value.length > 256)
        throw Error('Секрет владельца: 32–256 символов');
      secret=value;
      return true; // verification happens server-side, NOT here.
    }
    function forget() { secret=''; }
    async function request(method, suffix, body, signal) {
      if (typeof suffix !== 'string' || !/^\/[a-z0-9/?_=&.-]+$/.test(suffix) ||
          suffix.includes('//') || suffix.includes('..'))
        throw Error('Недопустимый маршрут');
      const headers={'Accept':'application/json'};
      const options={
        method,cache:'no-store',credentials:'same-origin',
        referrerPolicy:'no-referrer',headers,
      };
      if (signal) options.signal=signal;
      if (method === 'POST') {
        if (!secret) throw Error('Сначала введи секрет владельца');
        headers['Content-Type']='application/json';
        headers['X-ContentMachine']='1';
        headers['X-CM-SP-Owner-Token']=secret;
        options.body=JSON.stringify(body === undefined ? {} : body);
      } else if (method !== 'GET') throw Error('Метод запрещён');
      const response=await fetcher(base+suffix,options);
      let payload;
      try { payload=await response.json(); }
      catch (_) { throw Error('Ответ локального сервера не является JSON'); }
      if (!response.ok || !payload || payload.ok !== true) {
        const msg=payload && typeof payload.error === 'string'
          ? payload.error.slice(0,260) : 'HTTP '+response.status;
        throw Error(msg);
      }
      if (payload.schema_version !== API_RESPONSE) throw Error('Несовместимая версия Search Planner HTTP');
      return payload.result;
    }
    return {
      setSecret,forget,hasSecret:()=>secret.length>0,
      get:(suffix,signal)=>request('GET',suffix,undefined,signal),
      post:(suffix,body,signal)=>request('POST',suffix,body,signal)
    };
  }
  return {QV_DEFAULT, projectId, uuid, idempotencyKey, newKey,
          json, csv, policy, natural, requireUsd, createClient};
});
