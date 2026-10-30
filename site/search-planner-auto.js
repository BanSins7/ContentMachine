'use strict';
/* Search Planner one-click workflow: financial cap is approved by the Run click.
 * All external operations use existing project-scoped server/SQL guards.
 * No paid retries, unknown-result replays, dummy models, or simulated receipts.
 */
(function (root, factory) {
  const api=factory();
  if(typeof module==='object' && module.exports) module.exports=api;
  if(root) root.CMSearchPlannerAuto=api;
})(typeof window!=='undefined'?window:null,function(){
  const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const assertId=(x,where)=>{
    if(typeof x!=='string'||!UUID.test(x))throw Error('Сервер не подтвердил '+where);
    return x;
  };
  const status=(value)=>String(value||'').toLowerCase();
  const warn=(msg)=>{throw Error(msg);};
  function costCap(value) {
    const str=String(value||'').trim();
    if(!/^(?:0|[1-9]\d{0,3})(?:\.\d{1,8})?$/.test(str) ||
       Number(str)<0.01||Number(str)>100)
      throw Error('Укажи лимит расходов от $0.01 до $100.');
    return str;
  }
  async function run(options) {
    const {get,post,core,publicationId,publicationSource,searchPolicy,
      modelSnapshot,modelOutputTokens,budgetCap,qvPolicy,newKey,onProgress,
      onCandidates,onUnsafe}=options;
    if(typeof get!=='function'||typeof post!=='function'||!core ||
       typeof newKey!=='function')throw Error('Не подключён безопасный сценарий запуска');
    const cap=costCap(budgetCap);
    const stage=(text)=>{if(typeof onProgress==='function')onProgress(text);};
    const remember=(key,val)=>{
      if(typeof options.onStage==='function')options.onStage(key,val);
      return val;
    };
    let generationId=null,chosen=null;
    if(options.resumeGenerationId){
      generationId=assertId(options.resumeGenerationId,'предыдущую генерацию');
      const current=await get('/generation/'+generationId);
      if(status(current?.status)!=='completed')
        warn('Генерация не завершена: повторять потенциально оплаченные batch нельзя.');
      stage('Продолжаем передачу готового AI Query без новых платных запросов…');
    }else{
    stage('Проверяем утверждённые темы…');
    const context=await post('/contexts/preview',{
      publication_id:assertId(publicationId,'публикацию'),
      publication_source:publicationSource,search_policy:searchPolicy
    });
    if(context?.schema_version!=='search-planner-context-preview/v1' ||
       !Array.isArray(context.contexts)||!context.contexts.length ||
       context.contexts.length!==context.context_count)
      warn('Не удалось проверить полный набор утверждённых тем');
    stage('Готовим модель и план генерации…');
    const catalog=await get('/models');
    const models=(catalog?.models||[]).filter(m=>m?.selectable===true &&
      m.status==='usable' && Number.isSafeInteger(m.catalog_version_id));
    chosen=models.find(m=>m.catalog_version_id===Number(modelSnapshot))||models[0];
    if(!chosen)warn('В каталоге нет проверенной модели для генерации');
    if(typeof options.onCatalog==='function')options.onCatalog(catalog,chosen);
    const configuration=core.buildGenerationConfig(searchPolicy,chosen,
      catalog.prompt_pins,modelOutputTokens);
    const registered=await post('/generation/configs',{configuration});
    const configId=remember('config',assertId(registered?.config_id,'конфигурацию'));
    const planned=await post('/generation/plan',{
      publication_id:publicationId,config_id:configId,
      publication_source:publicationSource,
      idempotency_key:newKey('plan')
    });
    generationId=remember('generation',assertId(planned?.generation_id,'план генерации'));
    if(!Number.isSafeInteger(planned.batch_count)||planned.batch_count<0||
       planned.batch_count>480)warn('Сервер вернул недопустимое число batch');
    stage('Проверяем смету и установленный лимит…');
    const quote=await post('/generation/'+generationId+'/quote',{});
    const upper=Number(quote?.high_usd);
    if(!Number.isFinite(upper)||upper<=0)
      warn('Сервер не подтвердил стоимость генерации: запуск остановлен без расходов');
    if(upper>Number(cap)+1e-8)
      warn('Стоимость по смете $'+upper.toFixed(4)+
        ' превышает выбранный лимит $'+Number(cap).toFixed(2)+
        '. Измени лимит и создай новый запуск; платных вызовов не было.');
    const quoteId=assertId(quote.quote_id,'смету');
    const consent=await post('/generation/'+generationId+'/consent',{
      quote_id:quoteId,authorized_usd:cap
    });
    remember('consent',assertId(consent?.consent_id,'разрешённый бюджет'));
    stage('Создаём поисковые запросы в AI…');
    // A completed batch may advance the generation to "completed"; do not
    // infer completeness from a single paid HTTP wrapper status.
    let completed=false;
    for(let i=0;i<planned.batch_count+1;i++){
      const current=await get('/generation/'+generationId);
      if(status(current.status)==='completed'){completed=true;break;}
      const preflight=await get('/generation/'+generationId+'/preflight');
      if(preflight?.eligible!==true)
        warn('Генерация приостановлена: '+String(preflight?.reason||current.status)+
          '. Повторный платный запрос автоматически не запускается.');
      const key=newKey('paid');
      if(typeof options.onPaidStart==='function')options.onPaidStart(generationId,key);
      let paid;
      try {
        paid=await post('/generation/'+generationId+'/execute-one',
          {idempotency_key:key});
      }catch(error){
        if(typeof onUnsafe==='function')onUnsafe(generationId,key);
        warn('Ответ платного запроса неизвестен. Автоматический повтор запрещён: '+
          String(error?.message||error));
      }
      if(paid?.status!=='completed' || !paid?.result ||
         !['completed','running','no_eligible_batch'].includes(
           status(paid.result.generation_status||paid.result.status))){
        if(typeof onUnsafe==='function')onUnsafe(generationId,key);
        warn('Неопределённое состояние платного batch. Требуется сверка ledger; '+
             'повторная оплата не выполняется.');
      }
      if(paid.result.generation_status==='completed'){completed=true;break;}
      stage('AI: обработано batch '+(i+1)+' из '+planned.batch_count+'…');
    }
    if(!completed){
      const state=await get('/generation/'+generationId);
      if(status(state.status)!=='completed')
        warn('Генерация ещё не завершена. Продолжение автоматически не запускается.');
    }
    }
    stage('Получаем итоговые запросы…');
    const candidates=await get('/generation/'+generationId+'/candidates');
    if(candidates?.schema_version!=='query_candidates/v1' ||
       !Array.isArray(candidates.candidates))
      warn('Сервер не подтвердил итоговый AI Query');
    if(typeof onCandidates==='function')onCandidates(candidates);
    const result={generation_id:generationId,candidates,model:chosen?.display_name||chosen?.model_id||candidates.model_id};
    stage('Проверяем запросы перед передачей в Collector…');
    const overview=await get('/overview?limit=50');
    const entry=(overview?.generations||[]).find(x=>x.generation_id===generationId);
    const outbox=assertId(entry?.source_outbox_id,'SQG Outbox');
    const validation=await post('/qv/validate',{
      source_outbox_id:outbox,policy:qvPolicy
    });
    const validationId=remember('validation',
      assertId(validation?.validation_run_id,'результат проверки запросов'));
    const verified=await get('/qv/'+validationId+'/validated');
    if(verified?.schema_version!=='validated_queries/v1' ||
       !Array.isArray(verified.queries))
      warn('Сервер не подтвердил проверенные запросы');
    result.validated=verified;
    if(typeof onCandidates==='function')onCandidates(candidates,verified);
    stage('Готовим подтверждённый план для Video Collector…');
    // Commit verifies trusted SQL QV, capabilities and constraints itself.
    await post('/tasks/'+validationId+'/commit',{
      idempotency_key:newKey('held'),limits:null
    });
    const inbox=await post('/handoff/'+validationId+'/ingest',{});
    const eventId=remember('inbox',assertId(inbox?.event_id,'Inbox'));
    result.inbox_id=eventId;
    let receipt=await get('/inbox/'+eventId+'/status');
    if(status(receipt?.status)==='no_work'){
      result.status='no_work';result.query_sets=[];
      return result;
    }
    if(!Number.isSafeInteger(receipt?.chunk_count) ||
       receipt.chunk_count<1 || receipt.chunk_count>480)
      warn('Collector не подтвердил количество пакетов — автоматическая отправка остановлена.');
    for(let i=(receipt.collector_accepted_chunks||[]).length;i<receipt.chunk_count;i++){
      stage('Передаём в Video Collector: пакет '+(i+1)+' из '+receipt.chunk_count+'…');
      const delivery=await post('/inbox/'+eventId+'/deliver',{});
      if(status(delivery?.status)==='needs_attention')
        warn('Результат приёма Collector неизвестен. Повторная отправка запрещена.');
      if(delivery?.collector_available===false)break;
      receipt=await get('/inbox/'+eventId+'/status');
      if(status(receipt.status)==='collector_accepted')break;
      if(status(receipt.status)!=='awaiting_collector')
        warn('Collector не подтвердил приём пакета: '+String(receipt.status));
      if((receipt.collector_accepted_chunks||[]).length<=i)
        warn('Нет подтверждённого receipt Collector, пакет не переотправлялся.');
    }
    result.status=receipt.status;
    result.query_sets=(receipt.collector_accepted_chunks||[])
      .map(row=>row.external_query_set_id).filter(x=>typeof x==='string'&&UUID.test(x));
    return result;
  }
  return {run,costCap};
});
