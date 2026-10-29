'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {score,patterns}=require('../site/workspace-analysis.js');
const pid='a'.repeat(32);
function metrics(){return {schema_version:'group-c-http/v1',ok:true,data:{schema_version:'group-c-studio/v1',project_id:pid,video_id:'7',state:'valid_current',score:{scale:'unbounded_not_0_to_100',metric_kind:'weighted_components_not_probability',breakout_score:'120.450000000000',components:['creator','growth','engagement','freshness'].map((key,i)=>({key,value:String(i+1)+'.000000000000'}))}}};}
test('Group C keeps original Decimal strings and rejects stale, foreign, rescaled or incomplete scores',()=>{
  assert.equal(score(metrics(),pid,'7').breakout,'120.450000000000');
  for(const modify of [d=>d.data.state='stale',d=>d.data.project_id='b'.repeat(32),d=>d.data.video_id='8',d=>d.data.score.scale='0_to_100',d=>d.data.score.components.pop(),d=>d.data.score.components[0].value='NaN',d=>d.schema_version='future/v9']){
    const data=metrics();modify(data);assert.equal(score(data,pid,'7'),null);
  }
});
test('PIE only displays scoped preliminary records and never treats publishable or causal records as supported',()=>{
  const data={ok:true,schema_version:'pie-pattern-explorer/v1',project_id:pid,view:'patterns',publication_allowed:false,items:[{feature_name:'A pattern',can_publish:false,causal_claim:false,independent_holdout_confirmed:false}]};
  assert.deepEqual(patterns(data,pid),[{name:'A pattern',state:'Предварительный результат'}]);
  for(const modify of [d=>d.project_id='b'.repeat(32),d=>d.publication_allowed=true,d=>d.view='insights',d=>d.items[0].can_publish=true,d=>d.items[0].causal_claim=true,d=>d.items[0].independent_holdout_confirmed=true]){
    const other=structuredClone(data);modify(other);assert.throws(()=>patterns(other,pid));
  }
});
