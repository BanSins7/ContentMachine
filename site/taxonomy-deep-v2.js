'use strict';
/* RTB FIX-09-B: separate B9-native Deep V2, never mixes legacy RTB endpoints.
   GET/preview never spends; every paid stage is an explicit browser POST. */
(() => {
  const $ = id => document.getElementById(id);
  let pid = null, run = null, binding = null, quote = null, consent = null;
  let expansionQuote = null, expansionConsent = null, state = null;
  let busy = false, epoch = 0, reviewerPhase = '';
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const base = () => '/api/projects/' + encodeURIComponent(pid) +
    '/taxonomy-builder/deep-v2';
  const runUrl = action => base() + '/runs/' + encodeURIComponent(run) +
    (action ? '/' + action : '');
  const el = (tag, text) => {
    const node = document.createElement(tag);
    node.textContent = text == null ? '' : String(text);
    return node;
  };
  function message(error='', notice='') {
    $('tbV2Error').textContent = error; $('tbV2Error').hidden = !error;
    $('tbV2Notice').textContent = notice; $('tbV2Notice').hidden = !notice;
  }
  function freeze(value) {
    busy = value;
    for (const id of ['tbV2Bind','tbV2Quote','tbV2Consent','tbV2Start',
      'tbV2Revoke','tbV2Resume','tbV2ReviewSubmit'])
      $(id).disabled = value;
  }
  async function api(path, body) {
    const options = body === undefined ? {cache:'no-store'} : {
      method:'POST',
      headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(body)
    };
    const response = await fetch(path, options);
    let data;
    try { data = await response.json(); }
    catch (_) { throw Error('Сервер не вернул JSON.'); }
    if (!response.ok || data.ok !== true)
      throw Error(data.error || 'HTTP ' + response.status);
    return data;
  }
  async function call(action, fn) {
    if (!pid || busy) return;
    const t=epoch;
    freeze(true); message();
    try {
      const result = await fn();
      if (t === epoch) {
        message('', action + ' выполнено.');
        updateButtons();
        return result;
      }
    } catch (err) {
      if (t === epoch) message(err.message || String(err));
    } finally {
      if (t === epoch) { freeze(false); updateButtons(); }
    }
    return null;
  }
  const remember = () => {
    try {localStorage.setItem('cm.rtb.v2.last.' + pid, run);}
    catch (_) {}
  };
  function updateButtons() {
    if (busy) return;
    for(const button of $('tbV2Actions').children) button.disabled=false;
    $('tbV2Quote').disabled = !binding;
    $('tbV2Consent').disabled = !quote || !$('tbV2ConsentAck').checked || !!consent;
    $('tbV2Start').disabled = !consent;
    $('tbV2Revoke').disabled = !consent;
  }
  function action(label, fn) {
    const b=el('button',label);b.type='button';
    b.className='tb-quiet-button tb-action-outline';
    b.disabled=busy;
    b.addEventListener('click',fn);
    $('tbV2Actions').append(b);
  }
  function explicitCharge(stage, endpoint, extra={}) {
    return async () => {
      if (!window.confirm('Отдельный платный запрос Opus 5.5: ' + stage +
          '. Возможен фактический расход; автоматических повторов нет. Выполнить?')) return;
      await call(stage,async () => {
        const res = await api(endpoint,{...extra,authorize_paid_call:true});
        await refresh();
        return res;
      });
    };
  }
  async function refresh() {
    if (!run || !pid) return;
    const t=epoch;
    const next=await api(runUrl('status'));
    if (t!==epoch) return;
    state=next;
    $('tbV2Status').textContent = 'Deep V2 · '+next.status+' · '+run;
    $('tbV2Budget').textContent = 'Расход $'+(next.spent_usd||'0')+
       ' · Резерв $'+(next.reserved_usd||'0')+
       ' · Разрешено $'+(next.authorized_usd||'0');
    $('tbV2Actions').replaceChildren();
    $('tbV2Review').hidden=true;
    $('tbV2Tree').hidden=true;
    const status=next.status;
    if (status==='pass1_pending') {
      action('Оплатить Pass 1',explicitCharge('Pass 1',runUrl('advance-pass1')));
    } else if (status==='review1') {
      const nodes=next.attempt?.output?.nodes;
      if (!Array.isArray(nodes) || !nodes.length) throw Error('Нет подтверждённого результата Pass 1.');
      renderReview(nodes,1);
    } else if (status==='pass2_pending') {
      action('Оплатить Pass 2',explicitCharge('Pass 2',runUrl('advance-pass2')));
    } else if (status==='expand_pending') {
      if (!Number.isInteger(next.expansion_count)) {
        message('Нет подтверждённого счётчика Expansion. Платное действие заблокировано.');
        return;
      }
      if (next.expansion_count>=2) {
        message('Достигнут максимум: два Expansion. Третий запрос запрещён.');
        return;
      }
      action('Получить отдельную смету Expansion',async () => {
        const q=await call('Смета Expansion',()=>api(runUrl('expansion-quote'),{}));
        if (!q) return;
        expansionQuote=q;expansionConsent=null;
        message('', 'Expansion #'+q.expansion_no+' · отдельная цена $'+q.required_usd+
          ' · модель не запускалась.');
        action('Подтвердить новую сумму $'+q.required_usd,async () => {
          if (!window.confirm('Разрешить ДОПОЛНИТЕЛЬНЫЙ бюджет $'+q.required_usd+
              ' на Expansion #'+q.expansion_no+'?')) return;
          const c=await call('Отдельное согласие Expansion',()=>api(
            runUrl('expansion-consent'),{
              quote_id:q.quote_id,authorized_usd:q.required_usd,
              acknowledged_quote_sha256:q.quote_sha256}));
          if (c) {
            expansionConsent=c;
            action('Оплатить Expansion #'+q.expansion_no,explicitCharge(
              'Expansion #'+q.expansion_no,runUrl('advance-expansion'),
              {consent_id:c.consent_id}));
          }
        });
      });
    } else if (status==='review2') {
      const preview=await api(runUrl('review2-preview'));
      if (t!==epoch) return;
      renderReview(preview.candidate_nodes,2,preview.readiness);
    } else if (status==='approved') {
      action('Опубликовать проверенную таксономию',async () => {
        if (!window.confirm('Human Review 2 утверждён. Опубликовать неизменяемую версию RTB и SRE?')) return;
        await call('Публикация',async () => {
          const res=await api(runUrl('publish'),{confirm_publication:true});
          await refresh();return res;
        });
      });
    } else if (status==='published') {
      action('Показать экспорт V2 (14 полей)',()=>loadExport('rtb-publication-export/v2'));
      action('Показать экспорт V1 (11 полей)',()=>loadExport('rtb-publication-export/v1'));
    } else {
      message('', 'Состояние '+status+'. Повторное платное действие автоматически не предлагается.');
    }
  }
  async function loadExport(version) {
    await call('Read-only экспорт',async () => {
      const pub=await api(runUrl('publication'));
      const dto=await api(base()+'/exports/'+encodeURIComponent(pub.publication_id)+
        '?version='+encodeURIComponent(version));
      $('tbV2Tree').textContent=JSON.stringify(dto,null,2);
      $('tbV2Tree').hidden=false;
    });
  }
  // Exactly 14 pinned Deep V2 fields. The server is still the authority
  // for B9 facts, A8 citations, protected H1 nodes and parent graph.
  const V2_ARRAY_FIELDS=new Set([
    'source_fact_ids','examples','source_observation_ids',
    'search_signals','validation_focus'
  ]);
  const V2_EDIT_FIELDS=[
    ['parent_id','Родительское направление ID (пусто для корня)'],
    ['kind','Тип узла: direction / branch / hypothesis'],
    ['relation','Отношение: direct / problem_related / adjacent'],
    ['source_fact_ids','Утверждённые факты B9 — JSON массив UUID'],
    ['name','Название'],
    ['rationale','Обоснование'],
    ['definition','Точное определение'],
    ['include','Что включать'],
    ['exclude','Что исключать'],
    ['examples','Примеры — JSON массив строк'],
    ['source_observation_ids','Оригинальные A8 observation ID — JSON массив'],
    ['search_signals','Поисковые сигналы — JSON массив'],
    ['validation_focus','Что проверять — JSON массив']
  ];
  function renderV2Fields(box,n) {
    for (const [key,label] of V2_EDIT_FIELDS) {
      const caption=el('label',label);
      const field=document.createElement(
        key==='parent_id'||key==='kind'||key==='relation'||key==='name'
          ? 'input' : 'textarea');
      field.className='tb-v2-field-'+key;
      field.setAttribute('aria-label',label);
      if (field.tagName==='TEXTAREA') field.rows=2;
      field.value=V2_ARRAY_FIELDS.has(key)?JSON.stringify(n[key]):(n[key]??'');
      box.append(caption,field);
    }
  }
  function readV2Fields(row) {
    const id=row.dataset.id;
    const edited={id};
    for (const [key] of V2_EDIT_FIELDS) {
      const control=row.querySelector('.tb-v2-field-'+key);
      if (!control) throw Error('Отсутствует поле '+key);
      const value=control.value;
      if (V2_ARRAY_FIELDS.has(key)) {
        let parsed;
        try {parsed=JSON.parse(value);}
        catch (_) {throw Error('Поле '+key+' должно быть корректным JSON-массивом');}
        if (!Array.isArray(parsed)||parsed.some(v=>typeof v!=='string'))
          throw Error('Поле '+key+' должно содержать только строки');
        edited[key]=parsed;
      } else if (key==='parent_id') {
        edited.parent_id=value.trim()||null;
        if (edited.parent_id===id) throw Error('Нельзя сделать узел родителем самого себя');
      } else {
        if (!value.trim()) throw Error('Поле '+key+' не должно быть пустым');
        edited[key]=value;
      }
    }
    if (Object.keys(edited).length!==14)
      throw Error('Для Review нужны все 14 полей Deep V2');
    return edited;
  }
  function renderReview(nodes,number,readiness) {
    reviewerPhase=String(number);
    $('tbV2Review').hidden=false;
    $('tbV2ReviewTitle').textContent='Human Review #'+number+
      ' · решение по каждому узлу ('+nodes.length+')';
    $('tbV2ReviewRows').replaceChildren();
    $('tbV2AddJson').value='[]';
    if (readiness) {
      const p=el('p','Search Readiness: '+(readiness.passed?'предварительно готово':
         'есть блокирующие вопросы')+' · '+(readiness.blocking_errors||[])
            .map(x=>x.code).join(', '));
      $('tbV2ReviewRows').append(p);
    }
    for (const n of nodes) {
      const box=el('div');box.className='tb-v2-review-node';box.dataset.id=n.id;
      const header=el('strong',n.kind+' · '+n.name+' · '+n.id);
      const details=el('pre',JSON.stringify(n,null,2));
      const select=document.createElement('select');
      select.className='tb-v2-decision';
      for(const [value,label] of [
        ['','Выбрать решение'],['approve','Утвердить'],['reject','Отклонить'],
        ['edit','Редактировать 14 полей ниже'],['merge','Объединить с ID ниже']
      ]) {
        const opt=el('option',label);opt.value=value;select.append(opt);
      }
      const target=document.createElement('input');
      target.className='tb-v2-merge';target.type='text';
      target.placeholder='ID утверждаемого узла для merge';
      box.append(header,details,select,target);
      renderV2Fields(box,n);
      $('tbV2ReviewRows').append(box);
    }
    $('tbV2ReviewSubmit').onclick=async () => {
      if (busy || !pid || !run) return;
      let choices;
      try {
        choices=Array.from($('tbV2ReviewRows').children)
          .filter(x=>x.dataset?.id).map(row => {
            const type=row.querySelector('.tb-v2-decision').value;
            const id=row.dataset.id;
            if (!type) throw Error('Не выбрано решение для '+id);
            if (type==='edit') return {action:'edit',id,
              node:readV2Fields(row)};
            if (type==='merge') {
              const target_id=row.querySelector('.tb-v2-merge').value.trim();
              if (!target_id||target_id===id)
                throw Error('Укажите ID другого утверждённого узла для объединения');
              return {action:'merge',id,target_id};
            }
            return {action:type,id};
          });
        const additional=JSON.parse($('tbV2AddJson').value||'[]');
        if (!Array.isArray(additional)) throw Error('Добавленные узлы должны быть массивом JSON.');
        additional.forEach(node=>choices.push({action:'add',node}));
      } catch (err) {message(err.message);return;}
      if (!window.confirm('Сохранить неизменяемый Human Review #'+number+
          '? Отменить решения после записи будет нельзя.')) return;
      await call('Human Review #'+number,async () => {
        const outcome=await api(runUrl('review'+number),{decisions:choices});
        await refresh();return outcome;
      });
    };
  }
  function openProject(projectId) {
    if (!/^[0-9a-f]{32}$/.test(projectId||'')) return;
    if (pid!==projectId) {
      pid=projectId;run=null;binding=null;quote=null;consent=null;
      expansionQuote=null;expansionConsent=null;state=null;epoch++;
      $('tbV2QuoteInfo').textContent='';
      $('tbV2Tree').hidden=true;$('tbV2Review').hidden=true;
      $('tbV2Actions').replaceChildren();
      $('tbV2Status').textContent='Укажите UUID утверждённого B9 Snapshot или откройте запуск.';
      try {$('tbV2RunId').value=localStorage.getItem('cm.rtb.v2.last.'+pid)||'';}
      catch (_) {$('tbV2RunId').value='';}
      updateButtons();message();
    }
  }
  $('tbV2Bind').addEventListener('click',async () => {
    const snapshot=$('tbV2Snapshot').value.trim().toLowerCase();
    if (!uuid.test(snapshot)) {message('Введите корректный UUID утверждённого B9 Snapshot.');return;}
    const res=await call('B9/A8 pin',()=>api(base()+'/bindings/pin',{snapshot_id:snapshot}));
    if (res) {binding=res.binding_id;quote=null;consent=null;updateButtons();
      $('tbV2QuoteInfo').textContent='Замороженный B9/A8 source SHA: '+res.research_input_sha256;}
  });
  $('tbV2Quote').addEventListener('click',async () => {
    if (!binding)return;
    const q=await call('Смета',()=>api(base()+'/quotes',{binding_id:binding}));
    if (q) {quote=q;consent=null;$('tbV2ConsentAck').checked=false;
      $('tbV2QuoteInfo').textContent='Opus 5.5 · два прохода до $'+q.required_usd+
        ' · Pass1 резерв $'+q.pass1_exact_reservation_usd+
        ' · Pass2 резерв $'+q.pass2_bounded_reservation_usd+
        ' · дополнительные Expansion не включены.';
      updateButtons();}
  });
  $('tbV2ConsentAck').addEventListener('change',updateButtons);
  $('tbV2Consent').addEventListener('click',async () => {
    if (!quote || !$('tbV2ConsentAck').checked)return;
    if (!window.confirm('Выделить бюджет $'+quote.required_usd+
        ' только для первых двух проходов Opus 5.5?')) return;
    const c=await call('Согласие',()=>api(base()+'/consents',{
      quote_id:quote.quote_id,authorized_usd:quote.required_usd,
      acknowledged_quote_sha256:quote.quote_sha256}));
    if (c) {consent=c;updateButtons();}
  });
  $('tbV2Start').addEventListener('click',async () => {
    if(!quote||!consent)return;
    const key='cm.rtb.v2.idempotency.'+pid+'.'+quote.quote_id;
    let idem;
    try {idem=localStorage.getItem(key);}catch (_) {}
    if (!idem) {idem='ui-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
      try {localStorage.setItem(key,idem);}catch (_) {}}
    const res=await call('Начальный запуск без оплаты',()=>api(base()+'/runs/start',{
      quote_id:quote.quote_id,consent_id:consent.consent_id,
      idempotency_key:idem}));
    if (res) {run=res.run_id;$('tbV2RunId').value=run;remember();
      try {await refresh();}catch(err){message(err.message);}}
  });
  $('tbV2Revoke').addEventListener('click',async () => {
    if (!consent || !window.confirm('Отозвать согласие на будущие запросы?'))return;
    const res=await call('Отзыв согласия',()=>api(base()+'/consents/revoke',{
      consent_id:consent.consent_id}));
    if(res){consent=null;updateButtons();}
  });
  $('tbV2Resume').addEventListener('click',async () => {
    const id=$('tbV2RunId').value.trim().toLowerCase();
    if(!uuid.test(id)){message('Неверный UUID Deep V2 run.');return;}
    run=id;remember();
    await call('Чтение состояния',refresh);
  });
  document.addEventListener('cm-taxonomy-deep-v2-project-open',event=>{
    openProject(event.detail?.projectId);
  });
  document.addEventListener('cm-taxonomy-pipeline-open',event=>{
    openProject(event.detail?.projectId);
  });
  // Standalone RTB V2 is not a legacy deep-run; opening legacy history
  // does not implicitly trigger or attach a paid V2 execution.
})();
