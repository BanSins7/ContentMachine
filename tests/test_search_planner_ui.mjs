/* SP-14 Node built-in UI/transport contract QA: no jsdom/network needed. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url);
const root=path.resolve(process.cwd());
const core=require(path.join(root,'site','search-planner-core.js'));
const html=fs.readFileSync(path.join(root,'site','index.html'),'utf8');
const view=fs.readFileSync(path.join(root,'site','search-planner.js'),'utf8');
const api=fs.readFileSync(path.join(root,'intelligence','search_planner','http_api.py'),'utf8');
const projects=fs.readFileSync(path.join(root,'site','projects.js'),'utf8');
const app=fs.readFileSync(path.join(root,'app.py'),'utf8');
const validProject='a'.repeat(32);
const uuid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const secret='owner-token-of-48-random-characters-abcdef1234';

test('six panels mounted and dynamically accessible from project details',()=>{
  for(const stage of ['c9','generation','validation','planning','handoff','history']){
    assert.match(html,new RegExp('data-sp-step="'+stage+'"'));
    assert.match(html,new RegExp('data-sp-panel="'+stage+'"'));
  }
  assert.match(projects,/ContentMachineSearchPlanner/);
  assert.match(projects,/show\('searchPlanner'\)/);
  assert.match(projects,/cm-search-planner-return/);
  assert.match(projects,/cm-sp-leave/);
  assert.match(view,/cm-sp-leave/);
  assert.match(view,/window\.addEventListener\('beforeunload',lock\)/);
  assert.match(html,/id="spView"/);
  assert.match(html,/aria-live="polite"/);
});

test('local server has exact static JS/CSS whitelist and script loading',()=>{
  for(const name of ['search-planner-core.js','search-planner.js','search-planner.css']){
    assert.ok(html.includes('/'+name),name);
    assert.ok(app.includes("'/"+name+"': '"+name+"'"),name);
  }
  assert.ok(!app.includes("exec(open('site/search-planner"),'no source-code execution');
});

test('all referenced static input IDs have an actual HTML element',()=>{
  const ids=[...view.matchAll(/el\('([A-Za-z][A-Za-z0-9]*)'\)/g)].map(m=>m[1]);
  for(const id of new Set(ids))
    assert.ok(html.includes('id="'+id+'"'),'Missing UI element '+id);
  for(const key of ['spPaidKey','spPlanKey','spHeldKey'])
    assert.ok(html.includes('id="'+key+'"'),key);
});

test('no owner secrets in persistent browser storage, logs or URL',()=>{
  const combined=view+'\n'+fs.readFileSync(path.join(root,'site','search-planner-core.js'),'utf8');
  for(const secretAPI of [/localStorage\b/,/sessionStorage\b/,/indexedDB\b/,
                          /document\.cookie\b/,/console\.log\b/,/window\.name\b/])
    assert.doesNotMatch(combined,secretAPI);
  assert.match(view,/el\('spOwnerToken'\)\.value=''/);
  assert.match(view,/client\.forget\(\)/);
  assert.match(view,/window\.addEventListener\('beforeunload',lock\)/);
});

test('policy requires explicit markets/languages and strict count caps',()=>{
  const base={languages:'ru, en',markets:'BY, PL',target:4,perHyp:8,total:240,
    keyword:true,hashtag:true};
  const value=core.policy(base);
  assert.deepEqual(value,{
    languages:['ru','en'],markets:['BY','PL'],
    allowed_modes:['keyword','hashtag'],
    target_candidates_per_hypothesis:4,
    max_candidates_per_hypothesis:8,
    max_total_candidates:240,
  });
  for(const changed of [
    {languages:''},{languages:'english'}, {languages:'ru, ru'},
    {markets:'BY, BY'}, {keyword:false,hashtag:false},
    {target:9,perHyp:8},{total:481},{target:'NaN'},
  ])assert.throws(()=>core.policy({...base,...changed}));
  assert.equal(core.policy({...base,markets:''}).markets.length,0);
});

test('UUID, money, JSON and idempotency inputs strictly validated',()=>{
  assert.equal(core.uuid(uuid),uuid);
  for(const v of ['wrong',uuid.toUpperCase(),'0000',null,{}])assert.throws(()=>core.uuid(v));
  for(const v of ['0','-1','NaN','10001','0.000000001','1e100','2,5'])assert.throws(()=>core.requireUsd(v));
  assert.equal(core.requireUsd('0.50000000'),'0.50000000');
  assert.throws(()=>core.json('[]'));
  assert.throws(()=>core.json('{broken'));
  assert.deepEqual(core.json('  ',null),null);
  assert.equal(core.idempotencyKey('sp-ui-12345678'),'sp-ui-12345678');
  assert.throws(()=>core.idempotencyKey('short'));
});

test('GET makes no owner-bearing request and POST keeps secret out of URL/body',async()=>{
  const captured=[];
  const fakeFetch=async(uri,options)=>{
    captured.push({uri,options});
    return {ok:true,status:200,json:async()=>({ok:true,
      schema_version:'search-planner-http-response/v1',
      result:{schema_version:'search-planner-owner-overview/v1',configs:[]}})};
  };
  const client=core.createClient(fakeFetch,validProject);
  await client.get('/overview?limit=20');
  assert.equal(captured.length,1);
  assert.equal(captured[0].options.method,'GET');
  assert.equal(captured[0].options.headers['X-CM-SP-Owner-Token'],undefined);
  assert.ok(!captured[0].uri.includes('owner'));
  await assert.rejects(()=>client.post('/handoff/drain',{}));
  assert.equal(captured.length,1);
  client.setSecret(secret);
  await client.post('/handoff/drain',{limit:5});
  assert.equal(captured[1].options.headers['X-CM-SP-Owner-Token'],secret);
  assert.equal(captured[1].options.headers['X-ContentMachine'],'1');
  assert.equal(captured[1].options.referrerPolicy,'no-referrer');
  assert.deepEqual(JSON.parse(captured[1].options.body),{limit:5});
  assert.ok(!captured[1].uri.includes(secret));
  assert.ok(!captured[1].options.body.includes(secret));
  client.forget();
  await assert.rejects(()=>client.post('/handoff/drain',{}));
  assert.equal(captured.length,2);
});

test('client rejects malformed/cross-route API responses and invalid project scope',async()=>{
  assert.throws(()=>core.createClient(async()=>{},'../admin'));
  const bad=core.createClient(async()=>({ok:true,status:200,
    json:async()=>({ok:true,schema_version:'unexpected',result:{}})}),validProject);
  await assert.rejects(()=>bad.get('/status'));
  await assert.rejects(()=>bad.get('//admin'));
  await assert.rejects(()=>bad.get('/../admin'));
});

test('UI does not fabricate QV accepted, cost paid, Collector ACK or provider readiness',()=>{
  assert.match(view,/await post\('\/qv\/validate'/);
  assert.match(view,/await post\('\/handoff\/'\+qv\(\)\+'\/ingest'/);
  assert.match(view,/await post\('\/inbox\/'\+id\('spInboxId'\)\+'\/deliver'/);
  assert.match(view,/await get\('\/inbox\/'\+id\('spInboxId'\)\+'\/status'/);
  assert.match(view,/await get\('\/overview\?limit=30'\)/);
  assert.match(view,/window\.confirm\('Реально выполнить ОДИН платный/);
  assert.match(view,/window\.confirm\('Передать ОДИН пакет/);
  assert.match(view,/setStep\('c9'\)/);
  assert.match(view,/\.textContent=/);
  assert.doesNotMatch(view,/\.innerHTML\s*=/);
  assert.ok(api.includes('CM_SP_PAID_HTTP_ENABLED'));
  assert.ok(api.includes('one_paid_batch_with_idempotency'));
});

test('model picker accepts only pinned, supported Stage D snapshots',()=>{
  const p=core.policy({languages:'ru',markets:'BY',target:4,perHyp:8,
    total:240,keyword:true,hashtag:true});
  const model={
    selectable:true,status:'usable',model_id:'verified/model',
    catalog_version_id:18,catalog_sha256:'a'.repeat(64),
    capabilities:{supports_structured_outputs:true},
  };
  const pins={
    prompt_version:'ai-query-v1',
    strategy_version:'thinkqe-facets-mugi-sketches-jina-lexicon-v1',
    raw_schema_version:'ai-query-raw/v1',
    prompt_sha256:'b'.repeat(64),raw_schema_sha256:'c'.repeat(64),
  };
  const cfg=core.buildGenerationConfig(p,model,pins,4096);
  assert.equal(cfg.schema_version,'search-generation-config/v1');
  assert.equal(cfg.model.catalog_snapshot_id,18);
  assert.equal(cfg.model.model_catalog_sha256,'a'.repeat(64));
  assert.equal(cfg.model.strategy_version,pins.strategy_version);
  assert.equal(cfg.execution.max_concurrent_provider_calls,1);
  assert.equal(cfg.execution.automatic_paid_retries,0);
  assert.equal(cfg.execution.samples_per_batch,1);
  assert.equal(cfg.search_policy.languages[0],'ru');
  for(const bad of [
    {...model,selectable:false},
    {...model,status:'stale'},
    {...model,catalog_version_id:0},
    {...model,catalog_sha256:'bad'},
    {...model,capabilities:{supports_structured_outputs:false}},
  ])assert.throws(()=>core.buildGenerationConfig(p,bad,pins,4096));
  assert.throws(()=>core.buildGenerationConfig(p,model,
    {...pins,prompt_sha256:'bad'},4096));
  assert.throws(()=>core.buildGenerationConfig(p,model,pins,131073));
});

test('model choice comes only from actual project-scoped server catalog',()=>{
  assert.match(html,/id="spModelSelect"/);
  assert.match(html,/data-sp-action="models"/);
  assert.match(html,/data-sp-action="model-build"/);
  assert.match(view,/await get\('\/models'\)/);
  assert.match(view,/item\.selectable===true/);
  assert.match(view,/core\.buildGenerationConfig/);
  assert.match(api,/segments == \["models"\]/);
  assert.match(api,/svc\.owner_model_catalog\(pid\)/);
  assert.ok(!view.includes("https://openrouter.ai/api/v1/models"));
});

test('QV has human-readable by-hypothesis decisions without fake search proof',()=>{
  assert.match(view,/function qvStructuredSummary\(parent,data\)/);
  assert.match(view,/data\.schema_version!=='validated_queries\/v1'/);
  assert.match(view,/data\.coverage/);
  assert.match(view,/data\.queries/);
  assert.match(view,/data\.decisions/);
  assert.match(view,/\.textContent=/);
  assert.match(view,/MMR/);
  assert.match(view,/не реальная точность поиска/);
});

test('paid HTTP keys and uncertainty status survive only through SQL history',()=>{
  assert.match(html,/id="spHistoryPaid"/);
  assert.match(view,/data\.paid_http_requests/);
  assert.match(view,/r=>r\.status\+' · '\+r\.idempotency_key/);
  assert.match(view,/el\('spPaidKey'\)\.value=r\.idempotency_key/);
  assert.match(view,/el\('spPaidConfirm'\)\.checked=false/);
  assert.match(view,/await get\('\/overview\?limit=30'\)/);
});

test('owner token clears on leaving project to any other top-level screen',()=>{
  const appJS=fs.readFileSync(path.join(root,'site','app.js'),'utf8');
  assert.match(appJS,/!inProjects && !\$\('spView'\)\.hidden/);
  assert.match(appJS,/document\.dispatchEvent\(new Event\('cm-sp-leave'\)\)/);
  assert.match(projects,/section !== 'searchPlanner'/);
  assert.match(view,/document\.addEventListener\('cm-sp-leave',\(\)=>close\(\)\)/);
});

test('explicit separate idempotency keys exist for planning, paid and HELD',()=>{
  for(const name of ['spPlanKey','spPaidKey','spHeldKey']){
    assert.match(html,new RegExp('data-sp-new-key="'+name+'"'));
    assert.ok(view.includes("requestKey('"+name+"')"));
  }
  assert.match(view,/previous.*результат неизвестен|предыдущий результат неизвестен/);
  assert.match(view,/spPaidConfirm/);
  assert.match(view,/spHeldConfirm/);
  assert.match(view,/spDeliveryConfirm/);
  assert.match(view,/spConsentCheck/);
});
