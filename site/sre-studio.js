'use strict';
/* M40 SRE Studio. No third-party modules, storage, model, worker or paid calls.
 * The host MUST inject a genuine session-bound per-action CSRF token port.
 * A client-created token, actor header or "project id" NEVER proves access.
 * Browser source is untrusted: authoritative checks remain in M38/M39.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof document !== 'undefined' && document.getElementById('sreTitle'))
    api.createStudio({document, fetch: root.fetch.bind(root),
      bridge: root.ContentMachineSREHost || null,
      crypto: root.crypto || null, URL: root.URL, Blob: root.Blob});
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const PREFIX = '/api/sre/v1';
  const MODEL = 'anthropic/claude-opus-5.5';
  const PID_RE = /^[0-9a-f]{32}$/;
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const SHA_RE = /^[0-9a-f]{64}$/;
  const KEY_RE = /^[A-Za-z0-9._-]{12,120}$/;
  const INT_RE = /^[1-9][0-9]{0,15}$/;
  const USD_RE = /^(?:0|[1-9][0-9]{0,11})\.[0-9]{9}$/;
  const STATES = new Set(['planned','pending_consent','queued','running',
    'waiting_source','paused_budget','paused_rights','unknown','partial',
    'completed','failed','cancelled']);

  function ids(raw, max) {
    if (typeof raw !== 'string') throw Error('Неверный формат списка IDs.');
    const parts = raw.split(/[\s,;]+/).filter(Boolean);
    if (parts.length < 1 || parts.length > max)
      throw Error('Нужно выбрать от 1 до ' + max + ' ID.');
    const values = parts.map(s => {
      if (!INT_RE.test(s)) throw Error('ID должен быть положительным десятичным BIGINT.');
      const n = Number(s);
      if (!Number.isSafeInteger(n) || n < 1)
        throw Error('BIGINT вне точного диапазона JavaScript; используйте серверный выбор.');
      return n;
    });
    if (new Set(values).size !== values.length)
      throw Error('Повторяющиеся идентификаторы недопустимы.');
    return values.sort((a,b) => a-b);
  }
  function uuid(s) { return typeof s === 'string' && UUID_RE.test(s); }
  function project(s) { return typeof s === 'string' && PID_RE.test(s); }
  function key(s) { return typeof s === 'string' && KEY_RE.test(s); }
  function positive(s) {
    return typeof s === 'string' && INT_RE.test(s) &&
      Number.isSafeInteger(Number(s)) && Number(s)>0;
  }
  function money(s) { return typeof s === 'string' && USD_RE.test(s); }
  function quoteConfirmation(q) {
    if (!q || !SHA_RE.test(q.quote_sha256) || !money(q.conditional_max_total_usd))
      throw Error('Нет подтверждённого SHA/USD котировки.');
    return {action:'authorize_sre_spending',
      stage:'sre_live_opus_paid_execution',
      quote_sha256:q.quote_sha256,max_usd:q.conditional_max_total_usd};
  }
  function reviewConfirmation(pid,rid,pair,decision,reason) {
    if (!project(pid) || !uuid(rid) || !pair ||
        !positive(String(pair.item_id)) || !uuid(pair.category_key) ||
        !uuid(pair.result_id) || !SHA_RE.test(pair.quote_sha256) ||
        !SHA_RE.test(pair.catalog_sha256) ||
        !['match','no_match','uncertain'].includes(pair.original_model_decision) ||
        !['match','no_match','uncertain'].includes(decision) ||
        !/^[a-z][a-z0-9_]{2,79}$/.test(reason))
      throw Error('Оригинальный M30 proof/SHA или структурированная причина недоступны.');
    return {action:'sre-human-review-confirm/v1',project_id:pid,
      run_id:rid,item_id:pair.item_id,category_key:pair.category_key,
      base_result_id:pair.result_id,quote_sha256:pair.quote_sha256,
      catalog_sha256:pair.catalog_sha256,
      original_model_decision:pair.original_model_decision,
      human_decision:decision,reason_code:reason,
      expected_prior_review_id:pair.review_id || null};
  }
  function createStudio(opts) {
    const doc = opts.document, transport = opts.fetch;
    const bridge = opts.bridge, crypto = opts.crypto;
    if (!doc || typeof transport !== 'function') throw Error('Studio needs DOM and fetch');
    const el = id => doc.getElementById(id);
    const hostReady = !!(bridge && typeof bridge.csrfForAction === 'function');
    const state = {project:null, connected:false, apiReady:false,
      epoch:0, busy:false, publication:null, taxonomy:null, verified:[],
      run:null, quote:null, review:null, pages:{}, pending:new Map(),
      currentResults:[]};
    const writes = ['sreAttach','sreCreateRun','sreCancelRun',
      'sreIssueQuote','sreGrantConsent','sreRevokeConsent','sreSubmitReview'];
    function setText(id,value) {
      const e=el(id);if(e)e.textContent=String(value === null || value === undefined ? '—':value);
    }
    function feedback(msg,error=false) {
      const e=el('sreFeedback');
      if(e){e.textContent=String(msg);e.dataset.level=error?'error':'success';}
    }
    function setStatus(msg,level='info') {
      setText('sreStatus',msg);
      const e=el('sreStatus');if(e)e.dataset.level=level;
    }
    function clearRows(id) {
      const node=el(id);
      while(node && node.firstChild) node.removeChild(node.firstChild);
    }
    function row(id,title,sub,buttonLabel,click) {
      const box=doc.createElement('div');box.className='sre-row';
      const main=doc.createElement('div');main.className='sre-row-main';
      const strong=doc.createElement('div');strong.className='sre-row-title';
      strong.textContent=String(title);
      const muted=doc.createElement('div');muted.className='sre-row-sub';
      muted.textContent=String(sub || '');
      main.appendChild(strong);main.appendChild(muted);box.appendChild(main);
      if(buttonLabel && click) {
        const b=doc.createElement('button');b.type='button';
        b.className='sre-button secondary';b.textContent=buttonLabel;
        b.addEventListener('click',() => task(() => click()));
        box.appendChild(b);
      }
      el(id).appendChild(box);
    }
    function updateButtons() {
      const enabled=state.connected && state.apiReady && hostReady && !state.busy;
      writes.forEach(id=>{if(el(id))el(id).disabled=!enabled;});
      setText('sreWriteState', !hostReady ?
        'Отключено: нет доверенного session/CSRF bridge хоста' :
        !state.connected?'Отключено до проверки прав проекта':
        !state.apiReady?'Отключено: схема LIVE не готова':
        'CSRF-хост подключён; сервер дополнительно проверяет права и согласие');
    }
    function resetProject() {
      state.epoch+=1;state.project=null;state.connected=false;state.apiReady=false;
      state.publication=null;state.taxonomy=null;state.verified=[];
      state.run=null;state.quote=null;state.review=null;
      state.pages={};state.currentResults=[];state.pending.clear();
      ['sreTaxonomyRows','sreContentRows','sreRunsRows','sreQuoteRows',
        'sreResultRows','sreQueueRows','sreHistoryRows','sreRankRows'].forEach(clearRows);
      ['sreTaxonomyMore','sreRunsMore','sreQuotesMore','sreResultsMore',
        'sreQueueMore','sreHistoryMore'].forEach(id=>{el(id).hidden=true;});
      el('sreConsentCheck').checked=false;el('sreReviewCheck').checked=false;
      el('sreProgress').setAttribute('aria-valuenow','0');
      el('sreProgressFill').style.width='0%';
      setText('sreVersion','—');setText('sreMode','Не проверена');
      setText('sreRunStatus','Запуск ещё не выбран.');
      setText('sreTaxonomy','Укажите C9 publication UUID и подтвердите права.');
      setText('sreContentStatus','D1 ещё не проверены.');
      setText('sreQuoteSelected','Котировка не выбрана.');
      setText('sreReviewSelected','Выберите оригинальный M30 результат.');
      setText('sreRankNotice','Rank-all недоступен: подтверждённый ranking API не подключён. Оценки не имитируются.');
      updateButtons();
    }
    async function request(path,method='GET',payload=null,mutationKey=null,csrf=null) {
      if (!path.startsWith(PREFIX+'/') || path.startsWith('//') ||
          path.includes('://') || path.includes('..'))
        throw Error('Запрос должен идти только в API SRE.');
      const headers={Accept:'application/json'};
      if(method==='POST') {
        if(!mutationKey||!key(mutationKey)||!csrf)
          throw Error('Нет idempotency/CSRF подтверждения хоста.');
        headers['Content-Type']='application/json';
        headers['Idempotency-Key']=mutationKey;
        headers['X-CSRF-Token']=csrf;
      }
      const response=await transport(path,{method,cache:'no-store',
        credentials:'same-origin',redirect:'error',headers,
        ...(method==='POST'?{body:JSON.stringify(payload)}:{})});
      if (!response || typeof response.json!=='function')
        throw Error('Нет JSON-ответа SRE.');
      const data=await response.json();
      if (!data || typeof data!=='object' || Array.isArray(data))
        throw Error('API вернул неверный JSON.');
      if(!response.ok) {
        const type = typeof data.error==='string' ? data.error : 'access_denied';
        if (response.status===409) throw Error('Конфликт/неопределённая операция ('+type+'). Нельзя повторять с новым ключом без сверки.');
        if(response.status===403) throw Error('Доступ не подтверждён или права D1 отозваны.');
        if(response.status===429) throw Error('Превышен лимит действий. Операция не выполнялась.');
        throw Error('SRE API недоступен ('+response.status+').');
      }
      return data;
    }
    function base() {
      if(!state.connected || !project(state.project))
        throw Error('Сначала проверьте права проекта.');
      return PREFIX+'/projects/'+state.project;
    }
    function assertEpoch(epoch) {
      if(epoch!==state.epoch)throw Error('Проект изменился — старый ответ отклонён.');
    }
    async function mutation(operation,body,providedKey=null) {
      if(!hostReady || !state.connected || !state.apiReady)
        throw Error('Изменения отключены: нет проверенного host CSRF/доступа.');
      // task() serializes UI actions and already holds the busy flag.
      const endpoint=base()+'/mutations/'+operation;
      const seal=JSON.stringify([state.project,operation,body]);
      let id=state.pending.get(seal);
      if (!id) {
        if (providedKey!==null) id=providedKey;
        else if(crypto && typeof crypto.randomUUID==='function') id=crypto.randomUUID();
        else throw Error('Для безопасного ключа операции требуется crypto.randomUUID.');
        if(!key(id))throw Error('Ключ идемпотентности должен быть 12–120 ASCII символов.');
        state.pending.set(seal,id);
      }
      let token;
      try {token=await bridge.csrfForAction(operation);}
      catch(_) {throw Error('Сервер не выдал session-bound CSRF для этого действия.');}
      if(typeof token!=='string' || token.length<8)
        throw Error('Серверный CSRF отсутствует.');
      const epoch=state.epoch;
      const result=await request(endpoint,'POST',body,id,token);
      assertEpoch(epoch);
      state.pending.delete(seal);
      if (result.state==='mutation_in_doubt')
        throw Error('Операция в неопределённом состоянии: проверьте журнал.');
      return result;
    }
    async function task(fn) {
      if(state.busy)return;
      state.busy=true;updateButtons();
      try{await fn();}
      catch(error){
        feedback(error instanceof Error ? error.message:'Операция отклонена.',true);
        if(error && /отозваны|Проект изменился/.test(String(error.message))) {
          state.verified=[];state.quote=null;state.review=null;
          ['sreResultRows','sreQueueRows','sreQuoteRows','sreContentRows'].forEach(clearRows);
        }
      } finally {state.busy=false;updateButtons();}
    }
    function on(id,callback) {
      const control=el(id);
      if(control)control.addEventListener('click',() => task(callback));
    }
    function inputValue(id) {return String(el(id).value||'').trim();}
    function validRun() {
      const rid=inputValue('sreRunId');
      if (!uuid(rid))throw Error('Укажите канонический UUID LIVE-запуска.');
      return rid;
    }
    function pageUrl(path,cursor) {
      const q=new URLSearchParams({limit:'30'});
      if(cursor)q.set('after',cursor);
      return path+'?'+q.toString();
    }
    async function connect() {
      const pid=inputValue('sreProject');
      if(!project(pid))throw Error('ID проекта должен содержать ровно 32 строчные hex-цифры.');
      resetProject();const epoch=state.epoch;
      setStatus('Проверяем право доступа к проекту…');
      const m=await request(PREFIX+'/manifest');
      if(m.schema_version!=='sre-localhost-read-api/v1' || m.read_only!==true)
        throw Error('Неизвестная или старая версия manifest SRE.');
      const h=await request(PREFIX+'/projects/'+pid+'/health');
      assertEpoch(epoch);
      if(h.schema_version!=='sre-read-api/v1' || h.project_id!==pid)
        throw Error('Ответ принадлежит другой версии/проекту.');
      state.project=pid;state.connected=true;state.apiReady=
        h.status==='schema_available' && h.live_tables_present===true;
      setText('sreVersion',m.schema_version);
      setText('sreMode',h.status+' · LIVE QA не подтверждена');
      setStatus(state.apiReady ?
        'Доступ SRE подтверждён. Операции записи требуют прав хоста и CSRF.' :
        'SRE доступен, но схема неполна. Запись отключена.',
        state.apiReady?'info':'warning');
      feedback('Проект авторизован для чтения. Это не разрешение на платный запуск.');
    }
    async function taxonomy(more=false) {
      const pub=inputValue('srePublication');
      if(!uuid(pub))throw Error('Укажите канонический C9 publication UUID.');
      const epoch=state.epoch;
      if(!more) {state.taxonomy=null;state.publication=null;clearRows('sreTaxonomyRows');}
      const cursor=more?state.pages.taxonomy:null;
      if(more && !cursor)return;
      const data=await request(pageUrl(base()+'/taxonomy/'+pub,cursor));
      assertEpoch(epoch);
      if(data.publication_id!==pub || data.human_approved_c9!==true ||
          !Array.isArray(data.rows))throw Error('Нет проверенного C9 publication.');
      state.publication=pub;state.taxonomy={publication_id:pub,
        taxonomy_sha256:data.taxonomy_sha256,count:data.approved_hypothesis_count};
      for(const v of data.rows){
        if(!uuid(v.category_key))throw Error('Недействительный C9 leaf идентификатор.');
        row('sreTaxonomyRows',v.kind+' · '+v.source_node_id,
          v.category_key+' · родитель: '+(v.parent_category_key||'корень'));
      }
      state.pages.taxonomy=data.next_cursor||null;
      el('sreTaxonomyMore').hidden=!data.has_more;
      setText('sreTaxonomy','C9 с одобрением людей; гипотез: '+
        data.approved_hypothesis_count+'; SHA: '+data.taxonomy_sha256);
      feedback('C9 подтверждена PostgreSQL-источником.');
    }
    async function verifyContent() {
      const content=ids(inputValue('sreContentIds'),50);
      state.verified=[];clearRows('sreContentRows');
      const epoch=state.epoch;
      const checked=[];
      for(const cid of content) {
        const data=await request(base()+'/content/'+cid+'/preview');
        assertEpoch(epoch);
        if(data.content_version_id!==cid || data.rights_rechecked!==true ||
          !SHA_RE.test(data.content_sha256)||!positive(String(data.video_id)))
          throw Error('D1 не прошёл авторизацию SHA/прав.');
        checked.push(cid);
        row('sreContentRows','#'+cid+' · video '+data.video_id,
          (data.title_preview||'без названия')+' · '+data.completeness+
          ' · права проверены сейчас');
      }
      state.verified=checked;
      setText('sreContentStatus','Проверено версий: '+checked.length+
        '. Права будут повторно проверены сервером при создании LIVE-плана.');
      feedback('D1 проверены; источники не отправлены провайдеру.');
    }
    async function attach() {
      const selected=ids(inputValue('sreDiscoveryIds'),50);
      const result=await mutation('attach',{discovery_ids:selected});
      if(!result.receipt || !Array.isArray(result.receipt.ids))
        throw Error('Не получены подтверждённые content version IDs.');
      const contentIds=result.receipt.ids;
      if(!contentIds.every(n=>Number.isSafeInteger(n)&&n>0))
        throw Error('Версии D1 вне точного числового диапазона.');
      el('sreContentIds').value=contentIds.sort((a,b)=>a-b).join(', ');
      state.verified=[];clearRows('sreContentRows');
      feedback('Выбранные D1 присоединены; теперь проверьте права версий.');
    }
    async function createRun() {
      if(!state.taxonomy || state.publication!==inputValue('srePublication'))
        throw Error('Сначала проверьте утверждённую C9.');
      const selected=ids(inputValue('sreContentIds'),50);
      if(JSON.stringify(selected)!==JSON.stringify(state.verified))
        throw Error('Сначала проверьте все выбранные D1 версии и права.');
      const config=inputValue('srePipelineConfig'),idKey=inputValue('sreRunKey');
      if(!positive(config)||!key(idKey))
        throw Error('Нужны реальная конфигурация и ключ идемпотентности 12–120 символов.');
      const value=await mutation('create_run',{
        publication_id:state.publication,content_version_ids:selected,
        pipeline_config_id:Number(config),idempotency_key:idKey},idKey);
      const rid=value.receipt&&value.receipt.run_id;
      if(!uuid(rid))throw Error('План не вернул авторитетный LIVE UUID.');
      el('sreRunId').value=rid;
      feedback('Создан только LIVE-план '+rid+'; списаний и dispatch нет.');
      await loadRun();
    }
    function showProgress(run) {
      if(!STATES.has(run.state))throw Error('Неизвестное состояние LIVE.');
      const total=run.expected_items;
      const done=run.finished_items;
      if(!Number.isSafeInteger(total)||total<0||
         !Number.isSafeInteger(done)||done<0||done>total)
        throw Error('Недостоверные счётчики прогресса LIVE.');
      const pct=total?Math.round(done*100/total):0;
      el('sreProgress').setAttribute('aria-valuenow',String(pct));
      el('sreProgressFill').style.width=pct+'%';
      setText('sreRunStatus','Состояние: '+run.state+
        ' · завершено '+done+'/'+total+' · '+pct+'%'+
        (run.cancel_requested?' · отмена запрошена':''));
    }
    async function loadRun() {
      const rid=validRun(),epoch=state.epoch;
      const run=await request(base()+'/runs/'+rid);
      assertEpoch(epoch);
      if(run.run_id!==rid || run.schema_version!=='sre-read-api/v1')
        throw Error('Запрос вернул чужой/неподтверждённый LIVE-запуск.');
      if(state.run!==rid){
        state.quote=null;state.review=null;state.currentResults=[];
        ['sreQuoteRows','sreResultRows','sreQueueRows','sreHistoryRows'].forEach(clearRows);
        el('sreConsentCheck').checked=false;el('sreReviewCheck').checked=false;
      }
      state.run=rid;showProgress(run);
      feedback('Прогресс LIVE считан без запуска воркера.');
    }
    async function runs(more=false) {
      if(!more)clearRows('sreRunsRows');
      const data=await request(pageUrl(base()+'/runs',more?state.pages.runs:null));
      if(!Array.isArray(data.rows))throw Error('Некорректная страница LIVE-запусков.');
      for(const v of data.rows) {
        if(!uuid(v.run_id))throw Error('Неизвестный ID запуска.');
        row('sreRunsRows',v.run_id,v.state+' · '+v.finished_items+'/'+v.expected_items,
          'Выбрать',()=>{el('sreRunId').value=v.run_id;return loadRun();});
      }
      state.pages.runs=data.next_cursor||null;el('sreRunsMore').hidden=!data.has_more;
      feedback('Загружены только LIVE-запуски текущего проекта.');
    }
    async function cancel() {
      const rid=validRun();
      if(!state.run || state.run!==rid)throw Error('Сначала загрузите LIVE-запуск.');
      const result=await mutation('cancel_run',{run_id:rid});
      feedback('Запрошена безопасная отмена: '+(result.receipt?.status||'выполнена')+
        '. UNKNOWN никогда не перезапускается автоматически.');
      await loadRun();
    }
    function selectQuote(q) {
      if(!q || !positive(String(q.item_id)) || !SHA_RE.test(q.quote_sha256) ||
         !SHA_RE.test(q.catalog_sha256) || !money(q.conditional_max_total_usd) ||
         !(Date.parse(q.expires_at)>Date.now()))
        throw Error('Котировка недействительна/истекла. Нужна новая.');
      state.quote=q;el('sreItemId').value=String(q.item_id);
      el('sreConsentAmount').value='';el('sreConsentCheck').checked=false;
      setText('sreQuoteSelected','Item #'+q.item_id+' · SHA: '+q.quote_sha256+
        ' · условный предел $'+q.conditional_max_total_usd+
        ' · до '+q.expires_at);
    }
    async function quotes(more=false) {
      const rid=validRun();
      if(!more){clearRows('sreQuoteRows');state.quote=null;}
      const data=await request(pageUrl(base()+'/runs/'+rid+'/quotes',
        more?state.pages.quotes:null));
      if(!Array.isArray(data.rows))throw Error('Нет авторизованных котировок.');
      for(const q of data.rows) {
        if(!SHA_RE.test(q.quote_sha256)||!money(q.conditional_max_total_usd))continue;
        row('sreQuoteRows','Item '+q.item_id+' · максимум $'+q.conditional_max_total_usd,
          q.quote_sha256+' · истекает '+q.expires_at,'Выбрать',()=>selectQuote(q));
      }
      state.pages.quotes=data.next_cursor||null;
      el('sreQuotesMore').hidden=!data.has_more;
      feedback('Показаны только актуальные котировки с проверенными правами.');
    }
    async function issueQuote() {
      const rid=validRun(),item=inputValue('sreItemId');
      if(!positive(item))throw Error('Требуется идентификатор LIVE item BIGINT.');
      const val=await mutation('issue_quote',{run_id:rid,item_id:Number(item)});
      feedback('Котировка выпущена без согласия/резерва. Проверьте её TTL и USD.');
      await quotes();
      return val;
    }
    async function grantConsent() {
      const q=state.quote,rid=validRun();
      if(!q || !el('sreConsentCheck').checked)throw Error('Выберите котировку и подтвердите согласие.');
      const exact=inputValue('sreConsentAmount');
      if(exact!==q.conditional_max_total_usd || !money(exact))
        throw Error('Нужно повторить точную сумму USD из котировки.');
      const confirmation=quoteConfirmation(q);
      const result=await mutation('grant_consent',{run_id:rid,
        item_id:q.item_id,quote_sha256:q.quote_sha256,
        explicit_confirmation:confirmation});
      el('sreConsentCheck').checked=false;
      feedback('Согласие сохранено: '+(result.receipt?.consent_id||'подтверждено')+
        '. Деньги не резервируются и оплачиваемый POST не выполняется.');
    }
    async function revokeConsent() {
      const q=state.quote,rid=validRun();
      if(!q)throw Error('Сначала выберите исходную котировку.');
      await mutation('revoke_consent',{run_id:rid,
        item_id:q.item_id,quote_sha256:q.quote_sha256,
        reason:'user_revoked'});
      state.quote=null;el('sreConsentCheck').checked=false;
      setText('sreQuoteSelected','Согласие отозвано. Повторное подтверждение этой котировки невозможно.');
      await quotes();feedback('Согласие отозвано без запуска модели.');
    }
    async function collection(type,more=false) {
      const paths={results:'results',queue:'review-queue',history:'history'};
      const containers={results:'sreResultRows',queue:'sreQueueRows',history:'sreHistoryRows'};
      const buttons={results:'sreResultsMore',queue:'sreQueueMore',history:'sreHistoryMore'};
      const rid=validRun(),epoch=state.epoch;
      if(!more){clearRows(containers[type]);state.pages[type]=null;}
      const data=await request(pageUrl(base()+'/runs/'+rid+'/'+paths[type],
        more?state.pages[type]:null));
      assertEpoch(epoch);
      if(!Array.isArray(data.rows)||data.schema_version!=='sre-read-api/v1')
        throw Error('Нет корректных результатов M30/M32.');
      for(const v of data.rows) {
        if(type==='history'){
          row(containers[type],v.category_key+' · ревизия '+v.revision,
            v.human_decision+' · '+v.reviewed_at+' · '+v.action);
          continue;
        }
        if(!uuid(v.result_id)||!uuid(v.category_key)||
            !['match','no_match','uncertain'].includes(v.original_model_decision))
          throw Error('M30 proof/решение недостоверны.');
        row(containers[type],'Item '+v.item_id+' · категория '+v.category_key,
          'Модель: '+v.original_model_decision+
          ' · итог: '+v.effective_decision+
          (v.human_reviewed?' · подтверждено человеком':' · ждёт проверки'),
          type==='queue'?'Проверить':null,
          type==='queue'?()=>selectReview(v):null);
      }
      state.pages[type]=data.next_cursor||null;el(buttons[type]).hidden=!data.has_more;
      if(type==='results' && !more)state.currentResults=data.rows;
      else if(type==='results')state.currentResults.push(...data.rows);
      feedback('Проверены действующие права и исходные доказательства M30/M32.');
    }
    function selectReview(pair) {
      if(!uuid(pair.result_id)||!SHA_RE.test(pair.quote_sha256)||
         !SHA_RE.test(pair.catalog_sha256)||pair.human_reviewed)
        throw Error('Для ревью требуется настоящий оригинальный результат и SHA M26.');
      state.review=pair;
      el('sreReviewCheck').checked=false;
      el('sreHumanDecision').value=pair.original_model_decision;
      el('sreReviewReason').value='verified_model_decision';
      setText('sreReviewSelected','Item '+pair.item_id+' · '+pair.category_key+
        ' · исходно '+pair.original_model_decision+' · M30 '+pair.result_id);
    }
    async function submitReview() {
      if(!state.review||!el('sreReviewCheck').checked)
        throw Error('Выберите подтверждённую M30 пару и поставьте галочку человеческой проверки.');
      const rid=validRun(),decision=inputValue('sreHumanDecision'),
        reason=inputValue('sreReviewReason');
      const confirmation=reviewConfirmation(state.project,rid,state.review,decision,reason);
      await mutation('human_review',{
        run_id:rid,item_id:state.review.item_id,
        category_key:state.review.category_key,human_decision:decision,
        reason_code:reason,expected_prior_review_id:state.review.review_id||null,
        explicit_confirmation:confirmation});
      state.review=null;el('sreReviewCheck').checked=false;
      await collection('queue');feedback('Человеческое ревью сохранено; исходный M30 вывод неизменяем.');
    }
    async function exportResults() {
      const rid=validRun(),epoch=state.epoch;
      // Fresh rights recheck; NEVER export cached results from a previous
      // page after the source may have been revoked in the meantime.
      const latest=await request(base()+'/runs/'+rid+'/results?limit=100');
      assertEpoch(epoch);
      if(!Array.isArray(latest.rows)||latest.schema_version!=='sre-read-api/v1')
        throw Error('Нет актуальных авторизованных данных для экспорта.');
      const safe={schema_version:'sre-studio-export/v1',
        project_id:state.project,run_id:rid,
        exported_page_only:true,has_more:latest.has_more===true,
        next_cursor:latest.next_cursor||null,source_rights_checked_at_export:true,
        rows:latest.rows};
      const text=JSON.stringify(safe,null,2);
      if(typeof opts.onExport==='function'){opts.onExport(text);return;}
      if(!opts.Blob || !opts.URL || typeof opts.URL.createObjectURL!=='function')
        throw Error('Локальная выгрузка недоступна в этом браузере.');
      const file=new opts.Blob([text],{type:'application/json;charset=utf-8'});
      const url=opts.URL.createObjectURL(file);
      try{
        const a=doc.createElement('a');
        a.href=url;a.download='sre-'+rid+'-verified-page.json';
        a.rel='noopener';a.click();
      } finally {opts.URL.revokeObjectURL(url);}
      feedback('Экспортирована лишь авторизованная страница до 100 записей; не полный архив.');
    }
    on('sreConnect',connect);on('sreRefresh',connect);
    on('sreTaxonomyLoad',()=>taxonomy());
    on('sreTaxonomyMore',()=>taxonomy(true));
    on('sreVerifyContent',verifyContent);on('sreAttach',attach);
    on('sreCreateRun',createRun);on('sreLoadRuns',()=>runs());
    on('sreRunsMore',()=>runs(true));on('sreLoadRun',loadRun);
    on('sreCancelRun',cancel);on('sreIssueQuote',issueQuote);
    on('sreLoadQuotes',()=>quotes());on('sreQuotesMore',()=>quotes(true));
    on('sreGrantConsent',grantConsent);on('sreRevokeConsent',revokeConsent);
    on('sreLoadResults',()=>collection('results'));
    on('sreResultsMore',()=>collection('results',true));
    on('sreLoadQueue',()=>collection('queue'));
    on('sreQueueMore',()=>collection('queue',true));
    on('sreLoadHistory',()=>collection('history'));
    on('sreHistoryMore',()=>collection('history',true));
    on('sreSubmitReview',submitReview);on('sreExport',exportResults);
    el('sreProject').addEventListener('input',() => {
      if(state.connected||state.project!==null) {
        resetProject();setStatus('Проект изменён. Повторно подтвердите доступ.','warning');
      }
    });
    el('srePublication').addEventListener('input',()=>{
      state.taxonomy=null;state.publication=null;
      clearRows('sreTaxonomyRows');setText('sreTaxonomy','C9 изменилась: нужна повторная проверка.');
    });
    el('sreContentIds').addEventListener('input',()=>{
      state.verified=[];clearRows('sreContentRows');
      setText('sreContentStatus','Список D1 изменён: нужна повторная проверка прав.');
    });
    el('sreRunId').addEventListener('input',()=>{
      state.run=null;state.quote=null;state.review=null;state.currentResults=[];
      ['sreResultRows','sreQuoteRows','sreQueueRows','sreHistoryRows'].forEach(clearRows);
    });
    resetProject();
    setStatus('SRE не подключён. Укажите проект и нажмите «Проверить доступ».');
    return {state,actions:{connect,taxonomy,verifyContent,attach,createRun,
      loadRun,runs,cancel,quotes,issueQuote,grantConsent,revokeConsent,
      collection,submitReview,exportResults},parseIds:ids,resetProject};
  }
  return {PREFIX,MODEL,ids,uuid,project,key,positive,money,
    quoteConfirmation,reviewConfirmation,createStudio};
});
