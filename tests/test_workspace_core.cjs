'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {route,projectId,errorText}=require('../site/workspace-core.js');
const fs=require('node:fs');
const path=require('node:path');

test('stage routes preserve work and results deep links and reject unrecognized paths',()=>{
  assert.deepEqual(route('#stage/search/work'),{stage:'search',tab:'work'});
  assert.deepEqual(route('#stage/insights/results'),{stage:'insights',tab:'results'});
  assert.deepEqual(route('#stage/group-c'),{stage:'group-c',tab:'overview'});
  for(const path of ['', '#pipeline','#stage/unknown','#stage/search/start','#stage/search/work/more','#stage/search?token=secret','#stage/%3Cscript%3E'])
    assert.deepEqual(route(path),{stage:null,tab:'overview'});
});
test('project context requires an exact local project ID',()=>{
  assert.equal(projectId('a'.repeat(32)),true);
  for(const value of [null,undefined,{},'','A'.repeat(32),'a'.repeat(31),'a'.repeat(33),'../project','a'.repeat(32)+'?run=1'])assert.equal(projectId(value),false);
});
test('API errors support both existing envelope formats',()=>{
  assert.equal(errorText({error:'Not connected'},503),'Not connected');
  assert.equal(errorText({error:{message:'Source unavailable',code:'missing'}},409),'Source unavailable');
  assert.equal(errorText({error:{code:'db_unavailable'}},503),'db_unavailable');
  assert.equal(errorText({},500),'HTTP 500');
});

test('light questionnaire review overrides legacy dark card backgrounds and unreadable text',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../site/workspace.css'),'utf8');
  for(const selector of [
    '.cm-workspace #projectReview .tb-profile-meta',
    '.cm-workspace #projectReview .tb-review-item',
    '.cm-workspace #projectReview .tb-review-item p',
    '.cm-workspace #projectReview .tb-review-item small',
    '.cm-workspace #projectReview .tb-check-error'
  ]) assert.ok(css.includes(selector),selector);
  assert.match(css,/\.cm-workspace #projectReview \.tb-review-item p\s*\{\s*color: #0f172a !important;/);
  assert.match(css,/\.cm-workspace #projectReview \.tb-check-error\s*\{\s*background: #fef2f2 !important;/);
});

test('known run deep links are accepted; unrecognized or repeated parameters are rejected',()=>{
  const run='11111111-1111-4111-8111-111111111111';
  assert.deepEqual(route('#stage/rtb/results?run='+run),{stage:'rtb',tab:'results'});
  assert.deepEqual(route('#stage/collector/results?run=42'),{stage:'collector',tab:'results'});
  for(const hash of ['#stage/rtb/results?run='+run+'&run='+run,'#stage/rtb/results?run=../x','#stage/qdp/results?mode=deep-v2','#stage/rtb/work?run='+run])assert.equal(route(hash).stage,null);
});
test('only explicit server states qualify completion, review or failure',()=>{
  const {runState,roleState}=require('../site/workspace-core.js');
  assert.equal(runState('rtb',null).kind,'idle');
  assert.equal(runState('rtb',{status:'fact_review'}).kind,'review');
  assert.equal(runState('rtb',{status:'critic_pending',generation_reviewed:false}).kind,'review');
  assert.equal(runState('rtb',{status:'critic_pending',generation_reviewed:true}).kind,'pending');
  assert.equal(runState('rtb',{status:'extraction_running'}).kind,'running');
  assert.equal(runState('rtb',{status:'uncertain'}).kind,'error');
  assert.equal(runState('rtb',{status:'approved'}).kind,'complete');
  assert.equal(runState('collector',{state:'running',stage:'released'}).kind,'running');
  assert.equal(runState('rtb',{status:'new_unknown_state'}).kind,'unknown');
  assert.equal(roleState({stages:[]},'taxonomy_critic').kind,'idle');
});
