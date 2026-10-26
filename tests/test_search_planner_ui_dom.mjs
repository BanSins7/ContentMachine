/* SP-14 bounded mocked UI journey: no browser extensions, jsdom or network. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url);
const BASE=path.resolve(process.cwd());
const html=fs.readFileSync(path.join(BASE,'site/index.html'),'utf8');
const core=require(path.join(BASE,'site/search-planner-core.js'));
const script=fs.readFileSync(path.join(BASE,'site/search-planner.js'),'utf8');
const PID='a'.repeat(32);
const PUB='22222222-2222-4222-8222-222222222222';
const CONF='33333333-3333-4333-8333-333333333333';
const GEN='44444444-4444-4444-8444-444444444444';
const QUOTE='55555555-5555-4555-8555-555555555555';
const CONSENT='66666666-6666-4666-8666-666666666666';
const SQG_OUT='77777777-7777-4777-8777-777777777777';
const QV='88888888-8888-4888-8888-888888888888';
const INBOX='99999999-9999-4999-8999-999999999999';
const SECRET='test-local-owner-token-with-at-least-32-random-characters';

class Element {
  constructor(id='',dataset={}){
    this.id=id;this.dataset=dataset;this.value='';this.checked=false;
    this.hidden=false;this.disabled=false;this.children=[];this.listeners={};
    this.textContent='';this.className='';
    this.classList={toggle(){},add(){},remove(){}};
  }
  setAttribute(){}
  removeAttribute(){}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  replaceChildren(...nodes){this.children=[...nodes];}
  append(...nodes){this.children.push(...nodes);}
  remove(){}
  get options(){return this.children;}
  click(){for(const fn of this.listeners.click||[])fn({preventDefault(){}});}
  querySelectorAll(){return [];}
}
function makeFixture(){
  const ids={};
  for(const m of html.matchAll(/id="(sp[A-Za-z0-9]+)"/g))
    ids[m[1]]=new Element(m[1]);
  for(const [id,val] of Object.entries({
    spTarget:'4',spMaxHyp:'8',spMaxTotal:'240',spModelOutputTokens:'4096',
    spReconcileIndex:'0',spLanguages:'ru',spMarkets:'BY',
  }))ids[id].value=val;
  ids.spKeyword.checked=true;ids.spHashtag.checked=true;
  const buttons={
    step:[...html.matchAll(/<button[^>]*data-sp-step="([^"]+)"/g)].map(m=>new Element('',{spStep:m[1]})),
    action:[...html.matchAll(/<button[^>]*data-sp-action="([^"]+)"/g)].map(m=>new Element('',{spAction:m[1]})),
    key:[...html.matchAll(/<button[^>]*data-sp-new-key="([^"]+)"/g)].map(m=>new Element('',{spNewKey:m[1]})),
    panel:[...html.matchAll(/<section[^>]*data-sp-panel="([^"]+)"/g)].map(m=>new Element('',{spPanel:m[1]})),
  };
  const root=ids.spView;
  root.querySelectorAll=(selector)=>({
    '[data-sp-step]':buttons.step,
    '[data-sp-action]':buttons.action,
    '[data-sp-new-key]':buttons.key,
    '[data-sp-panel]':buttons.panel,
  }[selector]||[]);
  const events={},requested=[];
  const document={
    getElementById:(id)=>ids[id]||null,
    createElement:(tag)=>new Element(tag),
    addEventListener:(name,fn)=>((events[name]??=[]).push(fn)),
    dispatchEvent:(event)=>{for(const fn of events[event.type]||[])fn(event);},
    body:{append(){}},
  };
  const models={
    schema_version:'search-planner-model-picker/v1',
    available_count:1,
    models:[{model_id:'test/verified',display_name:'Verified',
      catalog_version_id:18,catalog_sha256:'a'.repeat(64),
      selectable:true,status:'usable',
      capabilities:{supports_structured_outputs:true}}],
    prompt_pins:{prompt_version:'ai-query-v1',raw_schema_version:'ai-query-raw/v1',
      strategy_version:'thinkqe-facets-mugi-sketches-jina-lexicon-v1',
      prompt_sha256:'b'.repeat(64),raw_schema_sha256:'c'.repeat(64)}
  };
  let uncertainPaid=false;
  const window={
    CMSearchPlannerCore:core,
    addEventListener(){},
    confirm:()=>true,
    scrollTo(){},
    setTimeout(fn){fn();},
    fetch:async(uri,options)=>{
      requested.push({uri,method:options.method,body:options.body?JSON.parse(options.body):null,
        ownerHeader:options.headers['X-CM-SP-Owner-Token']||null});
      const response=(result)=>({ok:true,status:200,
        json:async()=>({ok:true,schema_version:'search-planner-http-response/v1',result})});
      let data={schema_version:'search-planner-stage14-qa/v1',status:'ok'};
      if(uri.endsWith('/publications?limit=50'))data={publications:[
        {taxonomy_publication_id:PUB,taxonomy_version:1,approved_hypothesis_count:2}]};
      else if(uri.endsWith('/models'))data=models;
      else if(uri.endsWith('/generation/configs'))data={config_id:CONF};
      else if(uri.endsWith('/generation/plan'))data={generation_id:GEN,status:'awaiting_authorization'};
      else if(uri.endsWith('/generation/'+GEN+'/quote'))data={quote_id:QUOTE,high_usd:'0.50000000'};
      else if(uri.endsWith('/generation/'+GEN+'/consent'))data={consent_id:CONSENT,status:'authorized'};
      else if(uri.endsWith('/generation/'+GEN+'/execute-one')){
        uncertainPaid=true;data={status:'needs_attention',reason:'unknown_billing_no_retry'};
      }else if(uri.endsWith('/qv/validate'))data={validation_run_id:QV,status:'completed'};
      else if(uri.endsWith('/qv/'+QV+'/validated'))data={
        schema_version:'validated_queries/v1',
        coverage:[{hypothesis_id:'h1',source_count:1,selected_count:1,outcome:'selected'}],
        queries:[{hypothesis_id:'h1',text:'fitness video',candidate_id:GEN,
          query_mode:'keyword',language:'ru',relevance_score:.7,mmr_score:.6}],
        decisions:[{candidate_id:GEN,decision:'selected',reason_code:'selected'}]
      };
      else if(uri.endsWith('/handoff/'+QV+'/ingest'))data={event_id:INBOX,status:'awaiting_collector'};
      else if(uri.endsWith('/inbox/'+INBOX+'/status'))data={
        event_id:INBOX,status:'awaiting_collector',collector_accepted_chunks:[]};
      else if(uri.endsWith('/overview?limit=30'))data={
        configs:[{config_id:CONF,revision:1,model:{model_id:'test/verified'}}],
        generations:[{generation_id:GEN,config_id:CONF,publication_id:PUB,
          status:'completed',source_outbox_id:SQG_OUT}],
        validations:[{validation_run_id:QV,generation_id:GEN,
          embedding_mode:'semantic_local',selected_count:1,candidate_count:1}],
        inbox:[{event_id:INBOX,validation_run_id:QV,selected_count:1,
          chunk_count:1,status:'awaiting_collector'}],
        paid_http_requests:[{generation_id:GEN,idempotency_key:'sp-paid-previous-key-0001',
          status:'uncertain'}],
      };
      return response(data);
    },
  };
  vm.runInNewContext(script,{
    window,document,Option:class{constructor(label,value){this.label=label;this.value=value;}},
    AbortController,Blob,URL,Event:class{constructor(type){this.type=type;}},
    CustomEvent:class{constructor(type,extra){this.type=type;this.detail=extra?.detail;}},
  },{filename:'site/search-planner.js'});
  const button=(action)=>buttons.action.find(b=>b.dataset.spAction===action);
  const step=(name)=>buttons.step.find(b=>b.dataset.spStep===name);
  const run=async(action)=>{
    assert.ok(button(action),'UI action '+action);
    button(action).click();
    await new Promise(resolve=>setImmediate(resolve));
  };
  return {ids,window,document,requested,step,run,
    state:()=>({uncertainPaid})};
}

test('entire UI journey with mock HTTP: no automatic spending or fake Collector acceptance',async()=>{
  const x=makeFixture();
  x.window.ContentMachineSearchPlanner.open(PID,'QA project');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(x.requested.length,1);
  assert.equal(x.requested[0].method,'GET');
  assert.equal(x.ids.spPublication.options.length,2);
  x.ids.spPublication.value=PUB;
  x.ids.spOwnerToken.value=SECRET;x.ids.spUnlock.click();
  assert.equal(x.ids.spOwnerToken.value,'');
  assert.match(x.ids.spAuthState.textContent,/Секрет подготовлен/);
  await x.run('contexts');
  await x.run('models');
  x.ids.spModelSelect.value='18';
  await x.run('model-build');
  const config=JSON.parse(x.ids.spConfigJson.value);
  assert.equal(config.model.catalog_snapshot_id,18);
  assert.equal(config.execution.automatic_paid_retries,0);
  await x.run('config');
  assert.equal(x.ids.spConfigId.value,CONF);
  await x.run('plan');
  assert.equal(x.ids.spGenerationId.value,GEN);
  await x.run('quote');
  assert.equal(x.ids.spQuoteId.value,QUOTE);
  x.ids.spAuthorizedUsd.value='0.25000000';
  x.ids.spConsentCheck.checked=true;
  await x.run('consent');
  assert.equal(x.ids.spConsentId.value,CONSENT);
  x.ids.spPaidConfirm.checked=true;
  await x.run('execute');
  assert.ok(x.state().uncertainPaid);
  assert.match(x.ids.spNotice.textContent,/needs_attention/);
  assert.ok(x.ids.spNotice.classList);
  x.ids.spSourceOutbox.value=SQG_OUT;
  await x.run('validate');
  assert.equal(x.ids.spValidationId.value,QV);
  await x.run('validated');
  assert.ok(x.ids.spValidationResult.children.length>=2);
  await x.run('tasks-preview');
  x.ids.spHeldConfirm.checked=true;
  await x.run('tasks-commit');
  await x.run('handoff-preview');
  await x.run('handoff-ingest');
  assert.equal(x.ids.spInboxId.value,INBOX);
  await x.run('inbox-status');
  assert.match(x.ids.spNotice.textContent,/Collector/);
  x.step('history').click();
  await x.run('history');
  assert.ok(x.ids.spHistoryPaid.children.length>1);
  assert.equal(x.requested.some(r=>r.uri.endsWith('/inbox/'+INBOX+'/deliver')),false);
  const post=x.requested.filter(r=>r.method==='POST');
  assert.ok(post.length>5);
  assert.ok(post.every(r=>r.ownerHeader===SECRET));
  assert.ok(x.requested.every(r=>!r.uri.includes(SECRET)&&!JSON.stringify(r.body).includes(SECRET)));
  assert.equal(x.requested.filter(r=>r.uri.endsWith('/generation/'+GEN+'/execute-one')).length,1);
  x.document.dispatchEvent({type:'cm-sp-leave'});
  assert.equal(x.window.ContentMachineSearchPlanner.open!==undefined,true);
  assert.equal(x.ids.spAuthState.textContent.includes('Только чтение'),true);
});

test('no secret means any attempted UI POST is blocked before fetch',async()=>{
  const x=makeFixture();
  x.window.ContentMachineSearchPlanner.open(PID,'QA');
  await new Promise(resolve=>setImmediate(resolve));
  x.ids.spPublication.value=PUB;
  await x.run('contexts');
  assert.equal(x.requested.length,1);
  assert.match(x.ids.spNotice.textContent,/секрет локального владельца/);
  assert.equal(x.ids.spContextsResult.children.length,0);
});
