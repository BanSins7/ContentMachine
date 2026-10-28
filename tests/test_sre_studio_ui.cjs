'use strict';
// M40 real Node22 interaction tests; no jsdom/browser/npm/network dependencies.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ui=require('../site/sre-studio.js');

const PID='a'.repeat(32);
const PUB='22222222-2222-4222-8222-222222222222';
const RID='11111111-1111-4111-8111-111111111111';
const CATEGORY='33333333-3333-4333-8333-333333333333';
const RESULT='44444444-4444-4444-8444-444444444444';
const SHA='f'.repeat(64);
const CATALOG='e'.repeat(64);
const BASE='/api/sre/v1';
const PROJ=BASE+'/projects/'+PID;

class Node {
  constructor(tag='div') {
    this.tagName=tag;this.children=[];this.handlers={};this.attributes={};
    this.style={};this.dataset={};this.value='';this.hidden=false;
    this.disabled=false;this.checked=false;this.className='';this.type='';
    this._textContent='';
  }
  set textContent(s){this._textContent=String(s);this.children=[];}
  get textContent(){return this._textContent||this.children.map(c=>c.textContent).join('');}
  get firstChild(){return this.children[0]||null;}
  appendChild(x){this.children.push(x);return x;}
  removeChild(x){const i=this.children.indexOf(x);if(i>=0)this.children.splice(i,1);}
  addEventListener(name,callback){(this.handlers[name]||=[]).push(callback);}
  async event(name='click'){
    for(const cb of (this.handlers[name]||[]))await cb({target:this});
  }
  async click(){return this.event('click');}
  setAttribute(k,v){this.attributes[k]=String(v);}
}
class Document {
  constructor(){this.nodes=new Map();this.created=[];}
  getElementById(id){if(!this.nodes.has(id))this.nodes.set(id,new Node());return this.nodes.get(id);}
  createElement(name){const n=new Node(name);this.created.push(n);return n;}
  get(id){return this.getElementById(id);}
}
function ok(data,status=200) {
  return {ok:status>=200&&status<300,status,
    json:async()=>data};
}
function sampleRun() {
  return {schema_version:'sre-read-api/v1',run_id:RID,
    state:'pending_consent',cancel_requested:false,expected_items:3,
    finished_items:1,blocked_items:0};
}
function sampleQuote() {
  return {quote_id:23,item_id:5,quote_sha256:SHA,catalog_sha256:CATALOG,
    conditional_max_total_usd:'0.750000000',
    issued_at:'2026-10-28T08:30:00Z',expires_at:'2999-10-28T08:34:00Z'};
}
function samplePair() {
  return {item_id:5,category_key:CATEGORY,
    original_model_decision:'match',effective_decision:'match',
    result_id:RESULT,receipt_id:PUB,quote_sha256:SHA,
    catalog_sha256:CATALOG,review_id:null,review_revision:0,
    human_reviewed:false,rights_rechecked:true};
}
function setup({host=true,health='schema_available',handler=null}={}){
  const document=new Document();
  const requests=[],exports=[],csrfActions=[];
  const bridge=host?{csrfForAction:async action=>{
    csrfActions.push(action);return 'verified-session-token-'+action;
  }}:null;
  const crypto={randomUUID:()=> '550e8400-e29b-41d4-a716-446655440000'};
  const fetch=async(url,opts)=>{
    requests.push({url,opts});
    if(handler){const response=await handler(url,opts);if(response)return response;}
    if(url===BASE+'/manifest')return ok({
      schema_version:'sre-localhost-read-api/v1',read_only:true});
    if(url===PROJ+'/health')return ok({
      schema_version:'sre-read-api/v1',project_id:PID,
      status:health,live_tables_present:health==='schema_available'});
    if(url.startsWith(PROJ+'/taxonomy/'+PUB))return ok({
      schema_version:'sre-read-api/v1',publication_id:PUB,
      human_approved_c9:true,taxonomy_sha256:SHA,
      approved_hypothesis_count:1,
      rows:[{category_key:CATEGORY,kind:'hypothesis',
             source_node_id:'H1',parent_category_key:null}],
      has_more:false,next_cursor:null});
    if(url===PROJ+'/content/51/preview')return ok({
      schema_version:'sre-read-api/v1',content_version_id:51,
      rights_rechecked:true,video_id:16,content_sha256:SHA,
      completeness:'metadata_only',title_preview:'Видео',description_preview:'Описание'});
    if(url===PROJ+'/runs/'+RID+'/rankings/5')return ok({
      schema_version:'sre-read-api/v1',
      ranking_version:'sre-all-leaf-ranking/v1',
      run_id:RID,item_id:5,local_cpu_only:true,paid_post_calls:0,
      source_rights_rechecked:true,model_identity_sha256:SHA,
      ranking_sha256:CATALOG,expected_leaf_count:1,scored_leaf_count:1,
      rows:[{rank:1,category_key:CATEGORY,
             rank_score:0.8123,classification_state:'not_evaluated'}]});
    if(url===PROJ+'/runs/'+RID)return ok(sampleRun());
    if(url.startsWith(PROJ+'/runs/'+RID+'/quotes'))return ok({
      schema_version:'sre-read-api/v1',rows:[sampleQuote()],
      has_more:false,next_cursor:null});
    if(url.startsWith(PROJ+'/runs/'+RID+'/results'))return ok({
      schema_version:'sre-read-api/v1',rows:[samplePair()],
      has_more:false,next_cursor:null});
    if(url.startsWith(PROJ+'/runs/'+RID+'/review-queue'))return ok({
      schema_version:'sre-read-api/v1',rows:[samplePair()],
      has_more:false,next_cursor:null});
    if(url.startsWith(PROJ+'/runs/'+RID+'/history'))return ok({
      schema_version:'sre-read-api/v1',rows:[],has_more:false,next_cursor:null});
    if(url.startsWith(PROJ+'/runs?'))return ok({
      schema_version:'sre-read-api/v1',rows:[sampleRun()],
      has_more:false,next_cursor:null});
    if(opts.method==='POST' && url===PROJ+'/mutations/create_run')return ok({
      state:'committed',receipt:{run_id:RID,status:'pending_consent'}});
    if(opts.method==='POST' && url===PROJ+'/mutations/grant_consent')return ok({
      state:'committed',receipt:{consent_id:RESULT,dispatch_allowed:false}});
    if(opts.method==='POST' && url===PROJ+'/mutations/human_review')return ok({
      state:'committed',receipt:{review_id:RESULT,revision:1}});
    if(opts.method==='POST' && url===PROJ+'/mutations/issue_quote')return ok({
      state:'committed',receipt:{quote_sha256:SHA,max_total_usd:'0.750000000'}});
    if(opts.method==='POST' && url===PROJ+'/mutations/attach')return ok({
      state:'committed',receipt:{count:1,ids:[51]}});
    if(opts.method==='POST' && url===PROJ+'/mutations/cancel_run')return ok({
      state:'committed',receipt:{run_id:RID,status:'cancelled'}});
    if(opts.method==='POST' && url===PROJ+'/mutations/revoke_consent')return ok({
      state:'committed',receipt:{status:'revoked'}});
    return ok({error:'not_found'},404);
  };
  const studio=ui.createStudio({document,fetch,bridge,crypto,
    onExport:body=>exports.push(body)});
  return {studio,doc:document,requests,exports,csrfActions,
    node:id=>document.get(id),click:async id=>document.get(id).click()};
}
async function connected(s) {
  s.node('sreProject').value=PID;
  await s.click('sreConnect');
  assert.equal(s.studio.state.connected,true,s.node('sreFeedback').textContent);
}
function posts(s){return s.requests.filter(x=>x.opts.method==='POST');}

test('M40 assets have no CDN, provider request, external dependencies or model selector',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../site/sre-studio.html'),'utf8');
  const script=fs.readFileSync(path.join(__dirname,'../site/sre-studio.js'),'utf8');
  assert.match(html,/id="sreGrantConsent"/);
  assert.match(html,/id="sreReviewCheck"/);
  assert.match(html,/id="sreRankNotice"/);
  assert.match(html,/id="sreExport"/);
  assert.doesNotMatch(html,/<script[^>]+https?:/);
  assert.doesNotMatch(html,/<iframe|<form[^>]*action="https?:/);
  assert.doesNotMatch(script,/localStorage|sessionStorage|eval\(|Function\(/);
  assert.doesNotMatch(script,/openrouter\.ai|api\.anthropic\.com|api_key:/);
  assert.equal(ui.MODEL,'anthropic/claude-opus-5.5');
});
test('no fetch, provider dispatch, write or auto-run on initial page',()=>{
  const s=setup();
  assert.equal(s.requests.length,0);
  assert.equal(s.node('sreCreateRun').disabled,true);
  assert.match(s.node('sreStatus').textContent,/не подключён/);
});
test('invalid D1 and C9 identity rejected, duplicate IDs do not pass',()=>{
  assert.deepEqual(ui.ids('2, 1, 3',50),[1,2,3]);
  for(const raw of ['1,1','01','-2','0','2.5','9007199254740992','', 'x', '1,2,3']){
    assert.throws(()=>ui.ids(raw,raw==='1,2,3'?2:50));
  }
  assert.equal(ui.uuid(PUB),true);
  assert.equal(ui.uuid(PUB.toUpperCase()),false);
  assert.equal(ui.project(PID),true);
  assert.equal(ui.project(PID.toUpperCase()),false);
});
test('without trusted host bridge GET works but every write stays disabled',async()=>{
  const s=setup({host:false});await connected(s);
  assert.equal(s.node('sreCreateRun').disabled,true);
  assert.match(s.node('sreWriteState').textContent,/CSRF bridge/);
  await s.click('sreAttach');assert.equal(posts(s).length,0);
});
test('read-only health incomplete never enables writes',async()=>{
  const s=setup({health:'schema_incomplete'});await connected(s);
  assert.equal(s.node('sreIssueQuote').disabled,true);
  assert.match(s.node('sreMode').textContent,/schema_incomplete/);
});
test('approved C9 and authorized D1 previews precede run creation',async()=>{
  const s=setup();await connected(s);
  s.node('srePublication').value=PUB;
  await s.click('sreTaxonomyLoad');
  assert.equal(s.studio.state.publication,PUB);
  assert.match(s.node('sreTaxonomy').textContent,/гипотез: 1/);
  s.node('sreContentIds').value='51';
  await s.click('sreVerifyContent');
  assert.deepEqual(s.studio.state.verified,[51]);
  assert.equal(posts(s).length,0);
  const paths=s.requests.map(x=>x.url);
  assert(paths.some(x=>x.includes('/taxonomy/')));
  assert(paths.some(x=>x.includes('/content/51/preview')));
});
test('selected D1 attach requires POST and returns real SRE content IDs',async()=>{
  const s=setup();await connected(s);
  s.node('sreDiscoveryIds').value='31';
  await s.click('sreAttach');
  const p=posts(s);
  assert.equal(p.length,1);
  assert.equal(p[0].url,PROJ+'/mutations/attach');
  assert.deepEqual(JSON.parse(p[0].opts.body),{discovery_ids:[31]});
  assert.equal(s.node('sreContentIds').value,'51');
  assert.deepEqual(s.studio.state.verified,[]);
});
test('run create uses the exact host CSRF, real selection and matching idempotency key',async()=>{
  const s=setup();await connected(s);
  s.node('srePublication').value=PUB;await s.click('sreTaxonomyLoad');
  s.node('sreContentIds').value='51';await s.click('sreVerifyContent');
  s.node('srePipelineConfig').value='2';s.node('sreRunKey').value='sre-run-2026-10-28-01';
  await s.click('sreCreateRun');
  const p=posts(s);assert.equal(p.length,1,s.node('sreFeedback').textContent);
  assert.equal(p[0].url,PROJ+'/mutations/create_run');
  assert.equal(p[0].opts.headers['Idempotency-Key'],'sre-run-2026-10-28-01');
  assert.deepEqual(JSON.parse(p[0].opts.body),{
    publication_id:PUB,content_version_ids:[51],pipeline_config_id:2,
    idempotency_key:'sre-run-2026-10-28-01'});
  assert.equal(s.node('sreRunId').value,RID);
  assert.equal(s.node('sreProgress').attributes['aria-valuenow'],'33');
  assert(s.csrfActions.includes('create_run'));
});
test('rank-all is never auto-run, one explicit authorized GET displays genuine ONNX scores',async()=>{
  const s=setup();await connected(s);
  assert.equal(s.requests.filter(x=>x.url.includes('/rankings/')).length,0);
  s.node('sreRunId').value=RID;s.node('sreRankItemId').value='5';
  await s.click('sreLoadRank');
  assert.equal(posts(s).length,0);
  assert.equal(s.requests.filter(x=>x.url.includes('/rankings/')).length,1);
  assert.match(s.node('sreRankRows').textContent,/0\.81230/);
  assert.match(s.node('sreRankNotice').textContent,/1\/1 C9/);
});
test('rank endpoint incomplete/forged scores blocked, no guess or stale data',async()=>{
  const s=setup({handler:url=>{
    if(url.includes('/rankings/'))return ok({
      schema_version:'sre-read-api/v1',
      ranking_version:'sre-all-leaf-ranking/v1',
      run_id:RID,item_id:5,local_cpu_only:true,paid_post_calls:1,
      source_rights_rechecked:false,model_identity_sha256:SHA,
      ranking_sha256:CATALOG,expected_leaf_count:2,scored_leaf_count:1,
      rows:[{rank:1,category_key:CATEGORY,rank_score:0.99,
             classification_state:'not_evaluated'}]});
    return null;
  }});
  await connected(s);
  s.node('sreRunId').value=RID;s.node('sreRankItemId').value='5';
  await s.click('sreLoadRank');
  assert.equal(s.node('sreRankRows').children.length,0);
  assert.match(s.node('sreFeedback').textContent,/не подтвердил полный rank-all/);
});
test('never allow consent on missing amount/check; exact fixed USD/stage confirmation',async()=>{
  const s=setup();await connected(s);
  s.node('sreRunId').value=RID;await s.click('sreLoadQuotes');
  const choice=s.node('sreQuoteRows').children[0].children[1];
  await choice.click();
  assert.equal(s.studio.state.quote.quote_sha256,SHA);
  await s.click('sreGrantConsent');assert.equal(posts(s).length,0);
  s.node('sreConsentCheck').checked=true;
  s.node('sreConsentAmount').value='0.75';
  await s.click('sreGrantConsent');assert.equal(posts(s).length,0);
  s.node('sreConsentAmount').value='0.750000000';
  await s.click('sreGrantConsent');
  const p=posts(s);assert.equal(p.length,1,s.node('sreFeedback').textContent);
  assert.equal(p[0].url,PROJ+'/mutations/grant_consent');
  const b=JSON.parse(p[0].opts.body);
  assert.deepEqual(b.explicit_confirmation,{
    action:'authorize_sre_spending',stage:'sre_live_opus_paid_execution',
    quote_sha256:SHA,max_usd:'0.750000000'});
  assert.equal(b.item_id,5);
  assert.equal(s.node('sreConsentCheck').checked,false);
  assert(!p[0].opts.body.includes('api_key'));
  assert.equal(s.csrfActions.at(-1),'grant_consent');
});
test('review demands M30 quote+catalog SHA and real human explicit confirmation',async()=>{
  const s=setup();await connected(s);
  s.node('sreRunId').value=RID;await s.click('sreLoadQueue');
  const choice=s.node('sreQueueRows').children[0].children[1];
  await choice.click();
  assert.equal(s.studio.state.review.result_id,RESULT);
  s.node('sreReviewCheck').checked=true;
  s.node('sreHumanDecision').value='no_match';
  s.node('sreReviewReason').value='incorrect_positive';
  await s.click('sreSubmitReview');
  const p=posts(s);assert.equal(p.length,1,s.node('sreFeedback').textContent);
  assert.equal(p[0].url,PROJ+'/mutations/human_review');
  const body=JSON.parse(p[0].opts.body);
  assert.equal(body.human_decision,'no_match');
  assert.deepEqual(body.explicit_confirmation,{
    action:'sre-human-review-confirm/v1',project_id:PID,
    run_id:RID,item_id:5,category_key:CATEGORY,base_result_id:RESULT,
    quote_sha256:SHA,catalog_sha256:CATALOG,
    original_model_decision:'match',human_decision:'no_match',
    reason_code:'incorrect_positive',expected_prior_review_id:null});
  assert.equal(s.node('sreReviewCheck').checked,false);
  assert.equal(s.studio.state.review,null);
});
test('quote visibility or reviewer proof revoked -> deny and never POST',async()=>{
  const s=setup({handler:(url)=>{
    if(url.startsWith(PROJ+'/runs/'+RID+'/quotes'))return ok({error:'rights_denied'},403);
    if(url.startsWith(PROJ+'/runs/'+RID+'/review-queue'))return ok({error:'rights_denied'},403);
    return null;
  }});
  await connected(s);
  s.node('sreRunId').value=RID;
  await s.click('sreLoadQuotes');
  assert.match(s.node('sreFeedback').textContent,/отозваны/);
  await s.click('sreLoadQueue');
  assert.equal(s.studio.state.quote,null);
  assert.equal(s.studio.state.review,null);
  assert.equal(posts(s).length,0);
});
test('export gets freshly authorized page and marks partial data honestly',async()=>{
  const s=setup();await connected(s);
  s.node('sreRunId').value=RID;
  await s.click('sreExport');
  assert.equal(s.exports.length,1);
  const body=JSON.parse(s.exports[0]);
  assert.equal(body.exported_page_only,true);
  assert.equal(body.rows[0].result_id,RESULT);
  assert.equal(body.source_rights_checked_at_export,true);
  assert.equal(posts(s).length,0);
});
test('changing project clears all cached privileged data and disables writes',async()=>{
  const s=setup();await connected(s);
  s.node('sreContentIds').value='51';
  await s.click('sreVerifyContent');
  assert.deepEqual(s.studio.state.verified,[51]);
  s.node('sreProject').value='b'.repeat(32);
  await s.node('sreProject').event('input');
  assert.equal(s.studio.state.connected,false);
  assert.deepEqual(s.studio.state.verified,[]);
  assert.equal(s.node('sreCreateRun').disabled,true);
  assert.equal(s.node('sreContentRows').children.length,0);
});
test('rendering server-provided XSS payload uses textContent, never innerHTML',async()=>{
  const poison='<img src=x onerror=alert(1)>';
  const s=setup({handler:url=>{
    if(url===PROJ+'/content/51/preview')return ok({
      schema_version:'sre-read-api/v1',content_version_id:51,
      rights_rechecked:true,video_id:16,content_sha256:SHA,
      title_preview:poison,completeness:'metadata_only'});
    return null;
  }});
  await connected(s);
  s.node('sreContentIds').value='51';await s.click('sreVerifyContent');
  assert.match(s.node('sreContentRows').textContent,/<img/);
  assert.equal(s.node('sreContentRows').children[0].children.length,1);
});
