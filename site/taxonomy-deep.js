'use strict';
/* Optional two-pass Opus mode. All paid calls are explicit; never retry. */
(() => {
  const $=id=>document.getElementById(id);
  const label={direction:'Направление',branch:'Поднаправление',
    hypothesis:'Исследовательская гипотеза',direct:'Функция продукта',
    problem_related:'Проблема аудитории',adjacent:'Смежная тема'};
  const statusText={
    pass1_pending:'Первый проход готов: Opus построит направления, ветки и гипотезы.',
    pass1_running:'Opus создаёт первое дерево. Не обновляй страницу.',
    review1:'Первое человеческое утверждение: оставь только направления, которые стоит развивать.',
    pass2_pending:'Твои решения сохранены. Теперь Opus углубит разрешённые ветки и проверит пробелы.',
    pass2_running:'Opus углубляет темы и ищет пропущенные направления.',
    expand_pending:'Проверка нашла новые направления, пробелы или дубли. Нужен дополнительный осознанный проход Opus.',
    expand_running:'Opus выполняет дополнительную проверку полноты.',
    review2:'Финальное утверждение: пройди всё дерево и реши, что публиковать.',
    approved:'Дерево утверждено и опубликовано в SRE.',
    blocked_quality:'Лимит проверочных проходов достигнут, а существенные проблемы остались. Публикация заблокирована.',
    uncertain:'Ответ провайдера неизвестен. Повторный платный вызов заблокирован.',
    failed:'Opus вернул некорректную структуру. Этот оплаченный запрос нельзя повторить.',
    paused_budget:'Разрешённого бюджета недостаточно для следующего запроса.',
    paused_price:'Тариф или модель поменялись. Нужна новая утверждённая конфигурация.'
  };
  const validReview=new Set(['review1','review2']);
  let pid=null,rid=null,state=null,loading=false,epoch=0,cursor=0;
  let votes={},additions=[],edited={},busy=false;
  const endpoint=()=>'/api/projects/'+encodeURIComponent(pid)+
    '/taxonomy-builder/runs/'+encodeURIComponent(rid)+'/deep';
  const el=(tag,text='',cls='')=>{
    const e=document.createElement(tag);e.textContent=text;
    if(cls)e.className=cls;return e;};
  async function api(path='',body){
    const r=await fetch(endpoint()+path,body===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(body)});
    let data;
    try{data=await r.json();}catch(_){throw Error('Сервер вернул неожиданный ответ.');}
    if(!r.ok||!data.ok)throw Error(data.error||'Ошибка '+r.status);
    return data;
  }
  function notify(msg=''){
    $('tbDeepError').hidden=!msg;$('tbDeepError').textContent=msg;
  }
  const draftKey=()=>rid&&state?'cm.deep.review.'+rid+'.'+state.status:'';
  function saveDraft(){
    if(!draftKey())return;
    try{localStorage.setItem(draftKey(),JSON.stringify({votes,additions,edited,cursor}));}catch(_){}
  }
  function loadDraft(){
    votes={};additions=[];edited={};cursor=0;
    let source;
    try{source=JSON.parse(localStorage.getItem(draftKey())||'null');}catch(_){return;}
    if(!source||typeof source!=='object')return;
    const ids=new Set((state.nodes||[]).map(x=>x.id));
    if(source.votes&&typeof source.votes==='object'){
      for(const [id,v] of Object.entries(source.votes)){
        if(ids.has(id)&&v&&['approve','reject','edit','merge'].includes(v.action))
          votes[id]=v;
      }
    }
    if(Array.isArray(source.additions))additions=source.additions.slice(0,20);
    if(source.edited&&typeof source.edited==='object')edited=source.edited;
    cursor=Number.isInteger(source.cursor)?Math.max(0,source.cursor):0;
  }
  function nodeData(row){return {...row,...(edited[row.id]||{})};}
  function nodes(){return state?.nodes||[];}
  function reviewed(){return Object.keys(votes).length;}
  function selectedFacts(select,chosen=[]){
    select.replaceChildren();
    for(const f of state.facts||[]){
      const op=el('option',f.text.length>95?f.text.slice(0,95)+'…':f.text);
      op.value=f.id;op.selected=chosen.includes(f.id);select.append(op);
    }
  }
  function selectControl(labelText,values,selected,change){
    const wrap=el('label','','tb-deep-field');wrap.append(el('span',labelText));
    const select=document.createElement('select');
    for(const [value,text] of values){
      const option=el('option',text);option.value=value;select.append(option);
    }
    select.value=selected===null?'':selected;
    select.addEventListener('change',()=>change(select.value));
    wrap.append(select);return wrap;
  }
  function setNodeEdit(row,delta){
    const updated={...nodeData(row),...delta};
    edited[row.id]=updated;
    if(votes[row.id]&&['approve','edit'].includes(votes[row.id].action))
      votes[row.id]={action:'edit',id:row.id,node:updated};
    saveDraft();
  }
  function editNode(parent,row){
    const current=nodeData(row);
    const details=document.createElement('details');details.className='tb-deep-edit';
    details.append(el('summary','Изменить название, границы и связь с фактами'));
    const fields=[
      ['name','Название',1,160],['rationale','Почему эта тема важна',3,700],
      ['definition','Что охватывает',3,700],['include','Включать ролики',3,600],
      ['exclude','Исключать ролики',3,600],
      ['examples','Примеры видео — по одному в строке',3,1000]
    ];
    for(const [key,label,lines,max] of fields){
      const wrapper=el('label','','tb-deep-field');wrapper.append(el('span',label));
      const field=document.createElement(lines===1?'input':'textarea');
      if(lines===1)field.type='text';else field.rows=lines;
      field.maxLength=max;
      field.value=key==='examples'?(current.examples||[]).join('\n'):current[key]||'';
      field.addEventListener('input',()=>{
        const updated={...nodeData(row)};
        updated[key]=key==='examples'?field.value.split('\n').map(x=>x.trim()).filter(Boolean).slice(0,4):field.value;
        edited[row.id]=updated;
        if(votes[row.id]?.action==='approve'||votes[row.id]?.action==='edit')
          votes[row.id]={action:'edit',id:row.id,node:updated};
        saveDraft();
      });
      wrapper.append(field);details.append(wrapper);
    }
    const parentOptions=[['','Нет · корневое направление'],
      ...[...nodes(),...additions].filter(n=>n.id!==row.id)
        .map(n=>[n.id,(label[n.kind]||n.kind)+' · '+n.name])];
    details.append(selectControl('Ветка-родитель',parentOptions,current.parent_id||'',
      value=>{setNodeEdit(row,{parent_id:value||null});}));
    details.append(selectControl('Уровень',[['direction','Направление'],['branch','Поднаправление'],
      ['hypothesis','Гипотеза']],current.kind,
      value=>{setNodeEdit(row,{kind:value});}));
    details.append(selectControl('Связь с продуктом',[['direct','Функция продукта'],
      ['problem_related','Проблема аудитории'],['adjacent','Смежная тема']],current.relation,
      value=>{setNodeEdit(row,{relation:value});}));
    const factsWrap=el('label','','tb-deep-field');
    factsWrap.append(el('span','Факты из анкеты · можно выбрать несколько'));
    const factSelect=document.createElement('select');factSelect.multiple=true;factSelect.size=4;
    selectedFacts(factSelect,current.source_fact_ids);
    factSelect.addEventListener('change',()=>{
      setNodeEdit(row,{source_fact_ids:[...factSelect.selectedOptions].map(x=>x.value)});
    });
    factsWrap.append(factSelect);details.append(factsWrap);
    parent.append(details);
  }
  function simple(parent,heading,content){
    const wrap=el('div','','tb-deep-section');wrap.append(el('span',heading,'tb-review-label'));
    wrap.append(el('p',content||'Не указано','tb-review-paragraph'));parent.append(wrap);
  }
  function currentCard(){
    const all=nodes();
    $('tbDeepCardHost').hidden=!validReview.has(state.status);
    if(!validReview.has(state.status)||!all.length)return;
    cursor=Math.max(0,Math.min(all.length-1,cursor));
    const row=all[cursor],node=nodeData(row);
    $('tbDeepCard').replaceChildren();
    $('tbDeepCardType').textContent=label[node.kind]||'Тема';
    $('tbDeepCardCount').textContent=(cursor+1)+' из '+all.length+' · решено '+reviewed();
    $('tbDeepCardProgress').style.width=(100*reviewed()/all.length)+'%';
    const card=$('tbDeepCard');
    card.append(el('span',(label[node.relation]||'Связь с продуктом'),'tb-review-kicker'));
    card.append(el('h3',node.name,'tb-review-title'));
    simple(card,'Зачем это направление',node.rationale);
    simple(card,'О чём искать видео',node.definition);
    const cols=el('div','','tb-review-columns');
    simple(cols,'Включать',node.include);simple(cols,'Не включать',node.exclude);
    card.append(cols);
    if((node.examples||[]).length)simple(card,'Примеры видео',node.examples.join('\n• '));
    const linked=(state.facts||[]).filter(x=>(node.source_fact_ids||[]).includes(x.id));
    const evidence=document.createElement('details');
    evidence.className='tb-review-evidence';
    evidence.append(el('summary','Основано на фактах · '+linked.length));
    if(linked.length)for(const fact of linked)evidence.append(el('p',fact.text,'tb-review-evidence-text'));
    else evidence.append(el('p','Смежное направление без прямой ссылки на функцию продукта.','tb-review-evidence-text'));
    card.append(evidence);
    const source=[...nodes(),...additions].find(n=>n.id===node.parent_id);
    if(source)simple(card,'Ветка внутри',source.name);
    editNode(card,row);
    const merger=document.createElement('details');
    merger.className='tb-deep-merge';merger.append(el('summary','Объединить с другим направлением'));
    const choices=nodes().filter(n=>n.id!==row.id&&n.kind===row.kind&&n.parent_id===row.parent_id);
    if(choices.length){
      const combo=document.createElement('select');
      for(const other of choices){const o=el('option',other.name);o.value=other.id;combo.append(o);}
      const merge=el('button','Объединить с выбранным','tb-quiet-button');merge.type='button';
      merge.addEventListener('click',()=>{
        votes[row.id]={id:row.id,action:'merge',target_id:combo.value};
        moveAfterVote();
      });merger.append(combo,merge);
    }else merger.append(el('p','На этом уровне нет подходящего направления для объединения.'));
    card.append(merger);
    const choice=votes[row.id];
    $('tbDeepApprove').textContent=choice&&['approve','edit'].includes(choice.action)?'✓ Одобрено':'Одобрить';
    $('tbDeepReject').textContent=choice?.action==='reject'?'✓ Отклонено':'Отклонить';
    $('tbDeepPrev').disabled=cursor===0;
    $('tbDeepNext').disabled=cursor===all.length-1;
    $('tbDeepReviewHint').textContent=reviewed()+' из '+all.length+
      ' рассмотрено. Изменения сохраняются только как черновик до отдельного подтверждения.';
    $('tbDeepReviewCommit').hidden=reviewed()!==all.length;
    $('tbDeepReviewCommit').textContent=state.status==='review1'?
      'Утвердить направления и перейти ко второму проходу →':
      'Финально утвердить дерево и опубликовать →';
  }
  function moveAfterVote(){
    saveDraft();
    if(cursor<nodes().length-1)cursor++;
    currentCard();
    $('tbDeepCard').scrollTop=0;
  }
  function vote(action){
    if(!validReview.has(state?.status)||busy)return;
    const row=nodes()[cursor];
    const value=edited[row.id];
    votes[row.id]=action==='reject'?{id:row.id,action:'reject'}:
      value?{id:row.id,action:'edit',node:value}:{id:row.id,action:'approve'};
    moveAfterVote();
  }
  function navigate(d){
    if(!validReview.has(state?.status)||busy)return;
    cursor=Math.max(0,Math.min(nodes().length-1,cursor+d));
    saveDraft();currentCard();$('tbDeepCard').scrollTop=0;
  }
  function buildTree(container,tree){
    container.replaceChildren();
    const byParent=new Map();
    for(const n of tree){
      const key=n.parent_id||'root';
      if(!byParent.has(key))byParent.set(key,[]);
      byParent.get(key).push(n);
    }
    const roots=byParent.get('root')||[];
    const grid=el('div','','tb-deep-tree-grid');
    const draw=(n,seen=new Set())=>{
      if(seen.has(n.id))return el('p','Неверная ссылка в дереве');
      const item=document.createElement('details');
      item.className='tb-deep-tree-node tb-deep-tree-'+n.kind;
      item.open=n.kind==='direction';
      const header=el('summary','','');
      header.append(el('span',label[n.kind]||'Тема','tb-deep-kind'));
      header.append(el('strong',n.name));
      item.append(header);
      const inner=el('div','','tb-deep-tree-body');
      inner.append(el('p',n.definition,'tb-deep-tree-definition'));
      const extra=document.createElement('details');extra.className='tb-deep-tree-extra';
      extra.append(el('summary','Обоснование и правила'));
      simple(extra,'Почему эта ветка появилась',n.rationale);
      simple(extra,'Что включать',n.include);
      simple(extra,'Что исключать',n.exclude);
      const links=(state.facts||[]).filter(f=>(n.source_fact_ids||[]).includes(f.id));
      if(links.length)simple(extra,'Факты анкеты',links.map(f=>f.text).join('\n• '));
      if(n.examples?.length)simple(extra,'Примеры видео',n.examples.join('\n• '));
      inner.append(extra);
      const children=byParent.get(n.id)||[];
      if(children.length){
        const kids=el('div','','tb-deep-tree-children');
        for(const child of children)kids.append(draw(child,new Set([...seen,n.id])));
        inner.append(kids);
      }
      item.append(inner);return item;
    };
    for(const root of roots)grid.append(draw(root));
    container.append(el('h3','Утверждённое дерево направлений'));
    container.append(el('p','Раскрой ветки, чтобы увидеть гипотезы, правила отбора роликов и связь с фактами.','tb-review-hint'));
    container.append(grid);
    container.hidden=false;
  }
  function renderQuality(){
    const q=state.quality;const host=$('tbDeepQuality');
    host.replaceChildren();
    host.hidden=!q;
    if(!q)return;
    const top=el('div','','tb-deep-quality-summary');
    for(const [title,value] of [
      ['Факты покрыты',q.covered_facts+' / '+q.total_facts],
      ['Серьёзные пробелы',String(q.severe_gap_count)],
      ['Сходные темы',String(q.duplicate_count)],
      ['Новые направления',String(q.new_direction_count)]
    ]){const item=el('div','','tb-deep-quality-stat');item.append(el('strong',value));item.append(el('span',title));top.append(item);}
    host.append(top);
    if(!q.passed)host.append(el('p','Проверка не завершена: система не считает результат достаточно полным относительно утверждённых фактов.','tb-deep-quality-note'));
    const matrix=$('tbDeepCoverage');matrix.replaceChildren();
    matrix.append(el('h4','Матрица покрытия анкеты'));
    for(const row of q.coverage_matrix||[]){
      const elRow=el('div','','tb-deep-matrix-row');
      elRow.append(el('span',row.covered?'●':'○','tb-deep-matrix-icon'));
      elRow.append(el('p',row.fact));
      elRow.append(el('small',row.covered?'Покрыт '+row.node_ids.length+' темами':
        row.gap_severity==='severe'?'Существенный пробел':'Требует проверки'));
      matrix.append(elRow);
    }
    for(const gap of q.branch_gaps||[])
      matrix.append(el('p','Нет конкретной исследовательской гипотезы: '+gap.name,
        'tb-review-footnote'));
    const names=new Map((state.nodes||[]).map(n=>[n.id,n.name]));
    for(const gap of q.duplicates||[])
      matrix.append(el('p','Возможно дублирование: «'+(names.get(gap.a)||gap.a)+
        '» и «'+(names.get(gap.b)||gap.b)+'». '+gap.reason,'tb-review-footnote'));
  }
  function renderAttempts(){
    const host=$('tbDeepAttempts');host.replaceChildren();
    for(const a of state.attempts||[]){
      const cost=a.charged_usd===null?'Стоимость неизвестна':
        '$'+Number(a.charged_usd).toFixed(5);
      host.append(el('p',a.stage+' · '+a.status+' · '+cost+
        (a.error?' · '+a.error:''),'tb-run-stage-line'));
    }
  }
  function render(){
    if(!state)return;
    const deep=state.mode==='deep';
    $('tbDeepEntry').hidden=deep||!state.eligible;
    const preview=state.cost_preview;
    $('tbDeepCostPreview').textContent=preview
      ?'Оценка двух основных запросов: '+Number(preview.two_pass_rough_reservation_usd).toFixed(3)+
       ' USD. Доступно по бюджету: '+Number(preview.remaining_authorized_usd).toFixed(3)+
       ' USD. Дополнительные запросы и второй большой промпт могут стоить дороже.'
      :'Каждый Opus-запрос проверяет отдельный резерв бюджета.';
    $('projectRun').classList.toggle('tb-deep-active',deep);
    if(!deep)return;
    $('tbDeepStatus').textContent=statusText[state.status]||'Обрабатываем исследование';
    $('tbDeepTitle').textContent=state.status==='approved'?'Готовое дерево исследований':
       state.status==='review1'?'Выбери перспективные направления':
       state.status==='review2'?'Утверди итоговую структуру':
       state.status.startsWith('expand')?'Проверяем полноту':'Углублённая таксономия';
    for(let i=1;i<=4;i++){
      const active=i===(state.status==='review1'?2:
        ['pass2_pending','pass2_running','expand_pending','expand_running'].includes(state.status)?3:
        ['review2','approved'].includes(state.status)?4:1);
      $('tbDeepStep'+i).className=active?'tb-deep-step-active':'';
    }
    $('tbRunSpent').textContent='$'+Number(state.spent_usd).toFixed(4);
    $('tbRunReserved').textContent='$'+Number(state.reserved_usd).toFixed(4);
    renderQuality();renderAttempts();
    $('tbDeepTree').hidden=true;
    $('tbDeepActions').replaceChildren();
    $('tbDeepCardHost').hidden=true;
    if(validReview.has(state.status))currentCard();
    if(state.status==='approved')buildTree($('tbDeepTree'),state.nodes||[]);
    if(['pass1_pending','pass2_pending','expand_pending'].includes(state.status)){
      const note=el('p','Следующая кнопка отправляет ровно один платный запрос Opus 5.5. Стоимость проверяется по оставшемуся бюджету перед отправкой. Повторов нет.','tb-review-hint');
      const btn=el('button',state.status==='pass1_pending'?'Запустить первое построение':
        state.status==='pass2_pending'?'Углубить утверждённые ветки':
        'Исправить пробелы и проверить насыщение','button primary');
      btn.disabled=busy;
      btn.addEventListener('click',()=>execute('/advance',
        'Отправить один платный запрос Opus 5.5? Ответ может потребовать дополнительной проверки.'));
      $('tbDeepActions').append(note,btn);
    }
    if(['uncertain','failed','paused_budget','paused_price','blocked_quality'].includes(state.status)){
      $('tbDeepActions').append(el('p','Автоматическое продолжение запрещено. Результаты и ошибки сохранены; для новой попытки понадобится новый запуск с новым бюджетом.','tb-review-hint'));
    }
  }
  async function refresh(){
    if(!pid||!rid)return;
    const index=++epoch;loading=true;
    try{
      const data=await api();
      if(index!==epoch)return;
      const prev=state?.status;
      state=data;
      if(data.mode==='deep'&&prev!==data.status)loadDraft();
      render();
    }catch(error){if(index===epoch)notify(error.message);}
    finally{loading=false;}
  }
  async function execute(path,question,body={}){
    if(busy||!pid||!rid||!confirm(question))return;
    busy=true;notify();$('tbDeepActions').replaceChildren(el('p','Работаем… не закрывай страницу.','tb-review-hint'));
    try{
      await api(path,body);await refresh();
    }catch(error){notify(error.message);await refresh();}
    finally{busy=false;render();}
  }
  function formNew(event){
    event.preventDefault();
    if(!validReview.has(state?.status)||busy)return;
    const form=event.target;
    const values=Object.fromEntries(new FormData(form).entries());
    const facts=$('tbDeepAddFacts');
    const source=[...facts.selectedOptions].map(x=>x.value);
    const ident='manual_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,7);
    const n={id:ident,parent_id:values.parent_id||null,kind:values.kind,
      relation:values.relation,source_fact_ids:source,name:values.name.trim(),
      rationale:values.rationale.trim(),definition:values.definition.trim(),
      include:values.include.trim(),exclude:values.exclude.trim(),
      examples:values.examples.split('\n').map(x=>x.trim()).filter(Boolean).slice(0,4)};
    if(n.kind==='direction'&&n.parent_id){notify('Корневое направление не может иметь родителя.');return;}
    if(n.kind!=='direction'&&!n.parent_id){notify('Ветке или гипотезе нужно выбрать родительское направление.');return;}
    if(n.relation!=='adjacent'&&!source.length){notify('Выбери подтверждающие факты из анкеты.');return;}
    additions.push(n);saveDraft();form.reset();notify();
    $('tbDeepReviewHint').textContent='Добавлена новая ветка: '+n.name+'. Она будет проверена сервером перед сохранением.';
  }
  async function commit(){
    if(!state||!validReview.has(state.status)||busy)return;
    if(reviewed()!==nodes().length){notify('Прими решение по каждой карточке.');return;}
    const all=[...nodes().map(n=>votes[n.id]),...additions.map(node=>({action:'add',node}))];
    const yes=all.filter(x=>x.action==='approve'||x.action==='edit'||x.action==='add').length;
    if(yes<2){notify('Сохрани как минимум два направления.');return;}
    const isFinal=state.status==='review2';
    const completedDraftKey=draftKey();
    await execute(isFinal?'/review2':'/review1',
      isFinal?'Окончательно утвердить дерево и опубликовать его в SRE?':
        'Утвердить только выбранные ветки для следующего платного прохода Opus?',{decisions:all});
    if(!validReview.has(state?.status))try{localStorage.removeItem(completedDraftKey);}catch(_){}
  }
  function configureAdd(){
    const parent=$('tbDeepAddParent');parent.replaceChildren();
    const root=el('option','Нет · корневое направление');root.value='';parent.append(root);
    for(const n of [...nodes(),...additions]){
      if(n.kind==='hypothesis')continue;
      const o=el('option',(label[n.kind]||'Ветка')+' · '+n.name);o.value=n.id;parent.append(o);
    }
    selectedFacts($('tbDeepAddFacts'));
  }
  document.addEventListener('cm-taxonomy-run-show',event=>{
    pid=event.detail?.projectId||null;rid=event.detail?.runId||null;
    state=null;epoch++;votes={};additions=[];edited={};cursor=0;
    $('tbDeepEntry').hidden=true;$('tbDeepWorkspace').hidden=true;
    $('projectRun').classList.remove('tb-deep-active');notify();
    if(pid&&rid)refresh();
  });
  $('tbDeepStart').addEventListener('click',()=>execute('/start',
    'Включить двухпроходный режим? Он заменит плоскую генерацию, потребует два отдельных платных запроса Opus 5.5 и две ручные проверки. Дополнительные проверки насыщения тоже платные. Сумма ограничена бюджетом текущего запуска.'));
  $('tbDeepApprove').addEventListener('click',()=>vote('approve'));
  $('tbDeepReject').addEventListener('click',()=>vote('reject'));
  $('tbDeepPrev').addEventListener('click',()=>navigate(-1));
  $('tbDeepNext').addEventListener('click',()=>navigate(1));
  $('tbDeepReviewCommit').addEventListener('click',commit);
  $('tbDeepAddForm').addEventListener('submit',formNew);
  // Do not reset an in-progress add-node form when the user clicks/selects.
  // Populate only when its disclosure first opens or the reviewed tree changes.
  $('tbDeepAddForm').closest('details')?.addEventListener('toggle',e=>{
    if(e.target.open)configureAdd();
  });
  document.addEventListener('keydown',e=>{
    if(!state||!validReview.has(state.status)||busy||
      e.ctrlKey||e.metaKey||e.altKey)return;
    if(['INPUT','SELECT','TEXTAREA'].includes(e.target?.tagName))return;
    if(e.key==='ArrowLeft')navigate(-1);
    else if(e.key==='ArrowRight')navigate(1);
    else if(e.key.toLowerCase()==='a')vote('approve');
    else if(e.key.toLowerCase()==='r')vote('reject');
  });
  window.addEventListener('beforeunload',e=>{
    if(busy){e.preventDefault();e.returnValue='';}
  });
  window.ContentMachineDeepTaxonomy={refresh};
})();
+
      Number(preview.two_pass_rough_reservation_usd).toFixed(3)+
      ' · доступно по бюджету 
    $('projectRun').classList.toggle('tb-deep-active',deep);
    if(!deep)return;
    $('tbDeepStatus').textContent=statusText[state.status]||'Обрабатываем исследование';
    $('tbDeepTitle').textContent=state.status==='approved'?'Готовое дерево исследований':
       state.status==='review1'?'Выбери перспективные направления':
       state.status==='review2'?'Утверди итоговую структуру':
       state.status.startsWith('expand')?'Проверяем полноту':'Углублённая таксономия';
    for(let i=1;i<=4;i++){
      const active=i===(state.status==='review1'?2:
        ['pass2_pending','pass2_running','expand_pending','expand_running'].includes(state.status)?3:
        ['review2','approved'].includes(state.status)?4:1);
      $('tbDeepStep'+i).className=active?'tb-deep-step-active':'';
    }
    $('tbRunSpent').textContent='$'+Number(state.spent_usd).toFixed(4);
    $('tbRunReserved').textContent='$'+Number(state.reserved_usd).toFixed(4);
    renderQuality();renderAttempts();
    $('tbDeepTree').hidden=true;
    $('tbDeepActions').replaceChildren();
    $('tbDeepCardHost').hidden=true;
    if(validReview.has(state.status))currentCard();
    if(state.status==='approved')buildTree($('tbDeepTree'),state.nodes||[]);
    if(['pass1_pending','pass2_pending','expand_pending'].includes(state.status)){
      const note=el('p','Следующая кнопка отправляет ровно один платный запрос Opus 5.5. Стоимость проверяется по оставшемуся бюджету перед отправкой. Повторов нет.','tb-review-hint');
      const btn=el('button',state.status==='pass1_pending'?'Запустить первое построение':
        state.status==='pass2_pending'?'Углубить утверждённые ветки':
        'Исправить пробелы и проверить насыщение','button primary');
      btn.disabled=busy;
      btn.addEventListener('click',()=>execute('/advance',
        'Отправить один платный запрос Opus 5.5? Ответ может потребовать дополнительной проверки.'));
      $('tbDeepActions').append(note,btn);
    }
    if(['uncertain','failed','paused_budget','paused_price','blocked_quality'].includes(state.status)){
      $('tbDeepActions').append(el('p','Автоматическое продолжение запрещено. Результаты и ошибки сохранены; для новой попытки понадобится новый запуск с новым бюджетом.','tb-review-hint'));
    }
  }
  async function refresh(){
    if(!pid||!rid)return;
    const index=++epoch;loading=true;
    try{
      const data=await api();
      if(index!==epoch)return;
      const prev=state?.status;
      state=data;
      if(data.mode==='deep'&&prev!==data.status)loadDraft();
      render();
    }catch(error){if(index===epoch)notify(error.message);}
    finally{loading=false;}
  }
  async function execute(path,question,body={}){
    if(busy||!pid||!rid||!confirm(question))return;
    busy=true;notify();$('tbDeepActions').replaceChildren(el('p','Работаем… не закрывай страницу.','tb-review-hint'));
    try{
      await api(path,body);await refresh();
    }catch(error){notify(error.message);await refresh();}
    finally{busy=false;render();}
  }
  function formNew(event){
    event.preventDefault();
    if(!validReview.has(state?.status)||busy)return;
    const form=event.target;
    const values=Object.fromEntries(new FormData(form).entries());
    const facts=$('tbDeepAddFacts');
    const source=[...facts.selectedOptions].map(x=>x.value);
    const ident='manual_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,7);
    const n={id:ident,parent_id:values.parent_id||null,kind:values.kind,
      relation:values.relation,source_fact_ids:source,name:values.name.trim(),
      rationale:values.rationale.trim(),definition:values.definition.trim(),
      include:values.include.trim(),exclude:values.exclude.trim(),
      examples:values.examples.split('\n').map(x=>x.trim()).filter(Boolean).slice(0,4)};
    if(n.kind==='direction'&&n.parent_id){notify('Корневое направление не может иметь родителя.');return;}
    if(n.kind!=='direction'&&!n.parent_id){notify('Ветке или гипотезе нужно выбрать родительское направление.');return;}
    if(n.relation!=='adjacent'&&!source.length){notify('Выбери подтверждающие факты из анкеты.');return;}
    additions.push(n);saveDraft();form.reset();notify();
    $('tbDeepReviewHint').textContent='Добавлена новая ветка: '+n.name+'. Она будет проверена сервером перед сохранением.';
  }
  async function commit(){
    if(!state||!validReview.has(state.status)||busy)return;
    if(reviewed()!==nodes().length){notify('Прими решение по каждой карточке.');return;}
    const all=[...nodes().map(n=>votes[n.id]),...additions.map(node=>({action:'add',node}))];
    const yes=all.filter(x=>x.action==='approve'||x.action==='edit'||x.action==='add').length;
    if(yes<2){notify('Сохрани как минимум два направления.');return;}
    const isFinal=state.status==='review2';
    const completedDraftKey=draftKey();
    await execute(isFinal?'/review2':'/review1',
      isFinal?'Окончательно утвердить дерево и опубликовать его в SRE?':
        'Утвердить только выбранные ветки для следующего платного прохода Opus?',{decisions:all});
    if(!validReview.has(state?.status))try{localStorage.removeItem(completedDraftKey);}catch(_){}
  }
  function configureAdd(){
    const parent=$('tbDeepAddParent');parent.replaceChildren();
    const root=el('option','Нет · корневое направление');root.value='';parent.append(root);
    for(const n of [...nodes(),...additions]){
      if(n.kind==='hypothesis')continue;
      const o=el('option',(label[n.kind]||'Ветка')+' · '+n.name);o.value=n.id;parent.append(o);
    }
    selectedFacts($('tbDeepAddFacts'));
  }
  document.addEventListener('cm-taxonomy-run-show',event=>{
    pid=event.detail?.projectId||null;rid=event.detail?.runId||null;
    state=null;epoch++;votes={};additions=[];edited={};cursor=0;
    $('tbDeepEntry').hidden=true;$('tbDeepWorkspace').hidden=true;
    $('projectRun').classList.remove('tb-deep-active');notify();
    if(pid&&rid)refresh();
  });
  $('tbDeepStart').addEventListener('click',()=>execute('/start',
    'Включить двухпроходный режим? Он заменит плоскую генерацию, потребует два отдельных платных запроса Opus 5.5 и две ручные проверки. Дополнительные проверки насыщения тоже платные. Сумма ограничена бюджетом текущего запуска.'));
  $('tbDeepApprove').addEventListener('click',()=>vote('approve'));
  $('tbDeepReject').addEventListener('click',()=>vote('reject'));
  $('tbDeepPrev').addEventListener('click',()=>navigate(-1));
  $('tbDeepNext').addEventListener('click',()=>navigate(1));
  $('tbDeepReviewCommit').addEventListener('click',commit);
  $('tbDeepAddForm').addEventListener('submit',formNew);
  // Do not reset an in-progress add-node form when the user clicks/selects.
  // Populate only when its disclosure first opens or the reviewed tree changes.
  $('tbDeepAddForm').closest('details')?.addEventListener('toggle',e=>{
    if(e.target.open)configureAdd();
  });
  document.addEventListener('keydown',e=>{
    if(!state||!validReview.has(state.status)||busy||
      e.ctrlKey||e.metaKey||e.altKey)return;
    if(['INPUT','SELECT','TEXTAREA'].includes(e.target?.tagName))return;
    if(e.key==='ArrowLeft')navigate(-1);
    else if(e.key==='ArrowRight')navigate(1);
    else if(e.key.toLowerCase()==='a')vote('approve');
    else if(e.key.toLowerCase()==='r')vote('reject');
  });
  window.addEventListener('beforeunload',e=>{
    if(busy){e.preventDefault();e.returnValue='';}
  });
  window.ContentMachineDeepTaxonomy={refresh};
})();
+Number(preview.remaining_authorized_usd).toFixed(3)+
      '. Фактическая стоимость второго прохода и дополнительных проверок может быть выше.':
      'Перед каждым запросом система отдельно проверит оставшийся бюджет.';
    $('tbDeepWorkspace').hidden=!deep;
    $('projectRun').classList.toggle('tb-deep-active',deep);
    if(!deep)return;
    $('tbDeepStatus').textContent=statusText[state.status]||'Обрабатываем исследование';
    $('tbDeepTitle').textContent=state.status==='approved'?'Готовое дерево исследований':
       state.status==='review1'?'Выбери перспективные направления':
       state.status==='review2'?'Утверди итоговую структуру':
       state.status.startsWith('expand')?'Проверяем полноту':'Углублённая таксономия';
    for(let i=1;i<=4;i++){
      const active=i===(state.status==='review1'?2:
        ['pass2_pending','pass2_running','expand_pending','expand_running'].includes(state.status)?3:
        ['review2','approved'].includes(state.status)?4:1);
      $('tbDeepStep'+i).className=active?'tb-deep-step-active':'';
    }
    $('tbRunSpent').textContent='$'+Number(state.spent_usd).toFixed(4);
    $('tbRunReserved').textContent='$'+Number(state.reserved_usd).toFixed(4);
    renderQuality();renderAttempts();
    $('tbDeepTree').hidden=true;
    $('tbDeepActions').replaceChildren();
    $('tbDeepCardHost').hidden=true;
    if(validReview.has(state.status))currentCard();
    if(state.status==='approved')buildTree($('tbDeepTree'),state.nodes||[]);
    if(['pass1_pending','pass2_pending','expand_pending'].includes(state.status)){
      const note=el('p','Следующая кнопка отправляет ровно один платный запрос Opus 5.5. Стоимость проверяется по оставшемуся бюджету перед отправкой. Повторов нет.','tb-review-hint');
      const btn=el('button',state.status==='pass1_pending'?'Запустить первое построение':
        state.status==='pass2_pending'?'Углубить утверждённые ветки':
        'Исправить пробелы и проверить насыщение','button primary');
      btn.disabled=busy;
      btn.addEventListener('click',()=>execute('/advance',
        'Отправить один платный запрос Opus 5.5? Ответ может потребовать дополнительной проверки.'));
      $('tbDeepActions').append(note,btn);
    }
    if(['uncertain','failed','paused_budget','paused_price','blocked_quality'].includes(state.status)){
      $('tbDeepActions').append(el('p','Автоматическое продолжение запрещено. Результаты и ошибки сохранены; для новой попытки понадобится новый запуск с новым бюджетом.','tb-review-hint'));
    }
  }
  async function refresh(){
    if(!pid||!rid)return;
    const index=++epoch;loading=true;
    try{
      const data=await api();
      if(index!==epoch)return;
      const prev=state?.status;
      state=data;
      if(data.mode==='deep'&&prev!==data.status)loadDraft();
      render();
    }catch(error){if(index===epoch)notify(error.message);}
    finally{loading=false;}
  }
  async function execute(path,question,body={}){
    if(busy||!pid||!rid||!confirm(question))return;
    busy=true;notify();$('tbDeepActions').replaceChildren(el('p','Работаем… не закрывай страницу.','tb-review-hint'));
    try{
      await api(path,body);await refresh();
    }catch(error){notify(error.message);await refresh();}
    finally{busy=false;render();}
  }
  function formNew(event){
    event.preventDefault();
    if(!validReview.has(state?.status)||busy)return;
    const form=event.target;
    const values=Object.fromEntries(new FormData(form).entries());
    const facts=$('tbDeepAddFacts');
    const source=[...facts.selectedOptions].map(x=>x.value);
    const ident='manual_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,7);
    const n={id:ident,parent_id:values.parent_id||null,kind:values.kind,
      relation:values.relation,source_fact_ids:source,name:values.name.trim(),
      rationale:values.rationale.trim(),definition:values.definition.trim(),
      include:values.include.trim(),exclude:values.exclude.trim(),
      examples:values.examples.split('\n').map(x=>x.trim()).filter(Boolean).slice(0,4)};
    if(n.kind==='direction'&&n.parent_id){notify('Корневое направление не может иметь родителя.');return;}
    if(n.kind!=='direction'&&!n.parent_id){notify('Ветке или гипотезе нужно выбрать родительское направление.');return;}
    if(n.relation!=='adjacent'&&!source.length){notify('Выбери подтверждающие факты из анкеты.');return;}
    additions.push(n);saveDraft();form.reset();notify();
    $('tbDeepReviewHint').textContent='Добавлена новая ветка: '+n.name+'. Она будет проверена сервером перед сохранением.';
  }
  async function commit(){
    if(!state||!validReview.has(state.status)||busy)return;
    if(reviewed()!==nodes().length){notify('Прими решение по каждой карточке.');return;}
    const all=[...nodes().map(n=>votes[n.id]),...additions.map(node=>({action:'add',node}))];
    const yes=all.filter(x=>x.action==='approve'||x.action==='edit'||x.action==='add').length;
    if(yes<2){notify('Сохрани как минимум два направления.');return;}
    const isFinal=state.status==='review2';
    const completedDraftKey=draftKey();
    await execute(isFinal?'/review2':'/review1',
      isFinal?'Окончательно утвердить дерево и опубликовать его в SRE?':
        'Утвердить только выбранные ветки для следующего платного прохода Opus?',{decisions:all});
    if(!validReview.has(state?.status))try{localStorage.removeItem(completedDraftKey);}catch(_){}
  }
  function configureAdd(){
    const parent=$('tbDeepAddParent');parent.replaceChildren();
    const root=el('option','Нет · корневое направление');root.value='';parent.append(root);
    for(const n of [...nodes(),...additions]){
      if(n.kind==='hypothesis')continue;
      const o=el('option',(label[n.kind]||'Ветка')+' · '+n.name);o.value=n.id;parent.append(o);
    }
    selectedFacts($('tbDeepAddFacts'));
  }
  document.addEventListener('cm-taxonomy-run-show',event=>{
    pid=event.detail?.projectId||null;rid=event.detail?.runId||null;
    state=null;epoch++;votes={};additions=[];edited={};cursor=0;
    $('tbDeepEntry').hidden=true;$('tbDeepWorkspace').hidden=true;
    $('projectRun').classList.remove('tb-deep-active');notify();
    if(pid&&rid)refresh();
  });
  $('tbDeepStart').addEventListener('click',()=>execute('/start',
    'Включить двухпроходный режим? Он заменит плоскую генерацию, потребует два отдельных платных запроса Opus 5.5 и две ручные проверки. Дополнительные проверки насыщения тоже платные. Сумма ограничена бюджетом текущего запуска.'));
  $('tbDeepApprove').addEventListener('click',()=>vote('approve'));
  $('tbDeepReject').addEventListener('click',()=>vote('reject'));
  $('tbDeepPrev').addEventListener('click',()=>navigate(-1));
  $('tbDeepNext').addEventListener('click',()=>navigate(1));
  $('tbDeepReviewCommit').addEventListener('click',commit);
  $('tbDeepAddForm').addEventListener('submit',formNew);
  // Do not reset an in-progress add-node form when the user clicks/selects.
  // Populate only when its disclosure first opens or the reviewed tree changes.
  $('tbDeepAddForm').closest('details')?.addEventListener('toggle',e=>{
    if(e.target.open)configureAdd();
  });
  document.addEventListener('keydown',e=>{
    if(!state||!validReview.has(state.status)||busy||
      e.ctrlKey||e.metaKey||e.altKey)return;
    if(['INPUT','SELECT','TEXTAREA'].includes(e.target?.tagName))return;
    if(e.key==='ArrowLeft')navigate(-1);
    else if(e.key==='ArrowRight')navigate(1);
    else if(e.key.toLowerCase()==='a')vote('approve');
    else if(e.key.toLowerCase()==='r')vote('reject');
  });
  window.addEventListener('beforeunload',e=>{
    if(busy){e.preventDefault();e.returnValue='';}
  });
  window.ContentMachineDeepTaxonomy={refresh};
})();
