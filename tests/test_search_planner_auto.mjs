import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {run,costCap}=require('../site/search-planner-auto.js');
const IDS={
  publication:'11111111-1111-4111-8111-111111111111',
  config:'22222222-2222-4222-8222-222222222222',
  generation:'33333333-3333-4333-8333-333333333333',
  quote:'44444444-4444-4444-8444-444444444444',
  consent:'55555555-5555-4555-8555-555555555555',
  outbox:'66666666-6666-4666-8666-666666666666',
  validation:'77777777-7777-4777-8777-777777777777',
  inbox:'88888888-8888-4888-8888-888888888888',
  querySet:'99999999-9999-4999-8999-999999999999'
};
function fixture({quote='0.30',unsafe=false,collector=true}={}){
  const calls=[],progress=[],stages={},state={paid:0,delivered:0};
  let unsafeCalls=0;
  const get=async url=>{
    calls.push(['GET',url]);
    if(url==='/models')return {
      prompt_pins:{},models:[{
        model_id:'test/snapshot',display_name:'Verified',status:'usable',
        selectable:true,catalog_version_id:3
      }]};
    if(url==='/generation/'+IDS.generation)return {
      status:state.paid?'completed':'awaiting_authorization'
    };
    if(url.endsWith('/preflight'))return {eligible:true};
    if(url.endsWith('/candidates'))return {
      schema_version:'query_candidates/v1',
      candidates:[{text:'face yoga tutorial',language:'en'}]
    };
    if(url==='/overview?limit=50')return {
      generations:[{generation_id:IDS.generation,source_outbox_id:IDS.outbox}]
    };
    if(url.endsWith('/validated'))return {
      schema_version:'validated_queries/v1',queries:[{text:'face yoga',query_mode:'keyword'}]
    };
    if(url.endsWith('/status'))return {
      chunk_count:1,status:state.delivered?'collector_accepted':'awaiting_collector',
      collector_accepted_chunks:state.delivered?[{external_query_set_id:IDS.querySet}]:[]
    };
    throw Error('Unexpected read: '+url);
  };
  const post=async (url,data)=>{
    calls.push(['POST',url,data]);
    if(url==='/contexts/preview')return {
      schema_version:'search-planner-context-preview/v1',context_count:1,
      contexts:[{hypothesis:{name:'Face Yoga'}}]
    };
    if(url==='/generation/configs')return {config_id:IDS.config};
    if(url==='/generation/plan')return {generation_id:IDS.generation,batch_count:1};
    if(url.endsWith('/quote'))return {quote_id:IDS.quote,high_usd:quote};
    if(url.endsWith('/consent'))return {consent_id:IDS.consent};
    if(url.endsWith('/execute-one')){
      if(unsafe)throw Error('Transport timeout after dispatch');
      state.paid++;return {status:'completed',result:{generation_status:'completed'}};
    }
    if(url==='/qv/validate')return {validation_run_id:IDS.validation};
    if(url.endsWith('/commit'))return {status:'held'};
    if(url.endsWith('/ingest'))return {event_id:IDS.inbox};
    if(url.endsWith('/deliver')){
      if(collector)state.delivered++;
      return {status:collector?'collector_accepted':'awaiting_collector',
        collector_available:collector};
    }
    throw Error('Unexpected write: '+url);
  };
  const opts={
    get,post,core:{buildGenerationConfig:()=>({schema_version:'search-generation-config/v1'})},
    publicationId:IDS.publication,publicationSource:'rtb_flat_v1',
    searchPolicy:{languages:['ru'],allowed_modes:['keyword']},
    modelSnapshot:'3',modelOutputTokens:'8192',budgetCap:'0.50',qvPolicy:{},
    newKey:()=>('sp-auto-'+String(calls.length).padStart(12,'0')),
    onProgress:x=>progress.push(x),
    onStage:(key,val)=>{stages[key]=val;},
    onUnsafe:()=>{unsafeCalls++;}
  };
  return {opts,calls,progress,stages,state,unsafeCalls:()=>unsafeCalls};
}
test('single run creates AI and transfers only after trusted QV and receipt',async()=>{
  const f=fixture();
  const result=await run(f.opts);
  assert.equal(result.status,'collector_accepted');
  assert.deepEqual(result.query_sets,[IDS.querySet]);
  assert.equal(result.candidates.candidates[0].text,'face yoga tutorial');
  assert.equal(result.validated.queries.length,1);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/execute-one')).length,1);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/deliver')).length,1);
  assert.equal(f.calls.find(x=>x[1].endsWith('/consent'))[2].authorized_usd,'0.50');
  assert.ok(f.progress.some(x=>x.includes('Collector')));
  assert.equal(f.unsafeCalls(),0);
});
test('quote exceeding visible click-to-run cap performs zero paid calls',async()=>{
  const f=fixture({quote:'0.70'});
  await assert.rejects(()=>run(f.opts),/превышает выбранный лимит/);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/consent')).length,0);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/execute-one')).length,0);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/deliver')).length,0);
});
test('uncertain paid outcome never auto-retries or proceeds to QV',async()=>{
  const f=fixture({unsafe:true});
  await assert.rejects(()=>run(f.opts),/Автоматический повтор запрещён/);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/execute-one')).length,1);
  assert.equal(f.calls.filter(x=>x[1]==='/qv/validate').length,0);
  assert.equal(f.unsafeCalls(),1);
});
test('no Collector receipt is not reported as successful delivery',async()=>{
  const f=fixture({collector:false});
  const result=await run(f.opts);
  assert.equal(result.status,'awaiting_collector');
  assert.deepEqual(result.query_sets,[]);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/deliver')).length,1);
});
test('continuing a completed generation does not authorize or pay again',async()=>{
  const f=fixture();
  f.state.paid=1;
  f.opts.resumeGenerationId=IDS.generation;
  const result=await run(f.opts);
  assert.equal(result.status,'collector_accepted');
  assert.equal(f.calls.filter(x=>x[1].endsWith('/execute-one')).length,0);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/consent')).length,0);
  assert.equal(f.calls.filter(x=>x[1]==='/qv/validate').length,1);
});
test('unfinished generation cannot be resumed by issuing another paid attempt',async()=>{
  const f=fixture();
  f.opts.resumeGenerationId=IDS.generation;
  await assert.rejects(()=>run(f.opts),/повторять потенциально оплаченные batch нельзя/);
  assert.equal(f.calls.filter(x=>x[1].endsWith('/execute-one')).length,0);
});
test('invalid budget input does not make any HTTP request',async()=>{
  for(const bad of ['0','-3','0.001','100.01','foo'])assert.throws(()=>costCap(bad));
  assert.equal(costCap('0.50'),'0.50');
  const f=fixture();f.opts.budgetCap='0';
  await assert.rejects(()=>run(f.opts),/лимит расходов/);
  assert.equal(f.calls.length,0);
});
