'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {route,projectId,errorText}=require('../site/workspace-core.js');

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
