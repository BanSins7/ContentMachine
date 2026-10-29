'use strict';
/* GC27 standalone Studio browser tests. No external npm dependencies. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..');
const studio=require('../site/group-c-studio.js');

const project='a'.repeat(32);
const validScore={
  breakout_score:'196.078000000000',
  metric_kind:'weighted_components_not_probability',
  scale:'unbounded_not_0_to_100',
  components:[
    {key:'creator',label:'Автор',value:'9.000000000000'},
    {key:'growth',label:'Рост',value:'150.000000000000'},
    {key:'engagement',label:'Вовлечённость',value:'36.000000000000'},
    {key:'freshness',label:'Свежесть',value:'1.078000000000'}
  ]
};
function example(){
  return {
    schema_version:'group-c-studio/v1',project_id:project,video_id:17,
    state:'valid_current',score:validScore,
    timeline:[
      {snapshot_id:1,observed_at:'2026-10-21T00:00:00+00:00',views:100,quality_status:'ok'},
      {snapshot_id:2,observed_at:'2026-10-22T00:00:00+00:00',views:200,quality_status:'ok'}
    ],
    cohorts:[{id:3,purpose:'creator',member_count:2}],
    configuration:{id:5},approval:{decision:'approved'},
    limitations:['Breakout Score не вероятность и не 0–100']
  };
}

test('canonical project + video route without accepting JS unsafe / adversarial ID',()=>{
  assert.deepEqual(studio.validIds(project,'17'),{projectId:project,videoId:'17'});
  for (const id of ['0','-1','+1','01','1.5','1/../2','9223372036854775808'])
    assert.throws(()=>studio.validIds(project,id));
  for (const proj of ['a'.repeat(31),'A'.repeat(32),'a'.repeat(31)+'/',"a".repeat(30)+'..'])
    assert.throws(()=>studio.validIds(proj,'17'));
});

test('Decimal values shown as exact strings, not normalized or floatified',()=>{
  assert.equal(studio.formatDecimal('1234567.340000000000'),'1 234 567,34');
  assert.equal(studio.formatDecimal('0.000000000000'),'0');
  assert.equal(studio.formatDecimal('-14.002300000000'),'−14,0023');
  assert.equal(studio.formatDecimal('NaN'),'—');
  assert.equal(studio.formatDecimal('5e70'),'—');
  assert.equal(studio.formatCount(1234567),'1 234 567');
});

test('unbounded score is preserved and not transformed into probability',()=>{
  const data=studio.checkedStudio(example(),project,'17');
  assert.equal(data.score.breakout_score,'196.078000000000');
  assert.equal(data.score.scale,'unbounded_not_0_to_100');
  assert.equal(studio.componentWidths(data.score.components).length,4);
  assert.equal(Math.max(...studio.componentWidths(data.score.components)),100);
  assert.ok(studio.componentWidths(data.score.components)[0]<100);
});

test('denied results always hide any malicious stale score, graph and approvals',()=>{
  const bad={...example(),state:'unavailable',reason_code:'source_policy_expired'};
  const result=studio.checkedStudio(bad,project,'17');
  assert.equal(result.score,null);
  assert.deepEqual(result.timeline,[]);
  assert.deepEqual(result.cohorts,[]);
  assert.equal(result.approval,null);
  assert.equal(result.configuration,null);
});

test('unknown version/project/score shape never becomes a numeric dashboard',()=>{
  const good=example();
  assert.throws(()=>studio.checkedStudio({...good,schema_version:'group-c-studio/v999'},project,'17'));
  assert.throws(()=>studio.checkedStudio({...good,project_id:'b'.repeat(32)},project,'17'));
  assert.throws(()=>studio.checkedStudio({...good,score:{...validScore,scale:'0_to_100'}},project,'17'));
  assert.throws(()=>studio.checkedStudio({...good,score:{...validScore,breakout_score:80}},project,'17'));
  assert.throws(()=>studio.checkedStudio({...good,score:{...validScore,components:[]}},project,'17'));
});

test('raw points must be exact verified timestamped integers, unique and bounded',()=>{
  const good=example();
  assert.ok(studio.chartGeometry(good.timeline));
  assert.equal(studio.chartGeometry([good.timeline[0]]),null);
  const doubled={...good,timeline:[good.timeline[0],good.timeline[0]]};
  assert.throws(()=>studio.checkedStudio(doubled,project,'17'));
  const bad={...good,timeline:[{...good.timeline[0],views:1.2},good.timeline[1]]};
  assert.throws(()=>studio.checkedStudio(bad,project,'17'));
  const retracted={...good,timeline:[{...good.timeline[0],quality_status:'retracted'},good.timeline[1]]};
  assert.throws(()=>studio.checkedStudio(retracted,project,'17'));
});

test('timeline preserves observed timestamps with no fabricated chart at tied dates',()=>{
  const examplePoints=example().timeline;
  assert.equal(studio.chartGeometry(examplePoints).coords.length,2);
  assert.equal(studio.chartGeometry([
    {...examplePoints[0]}, {...examplePoints[1],observed_at:examplePoints[0].observed_at}
  ]),null);
  assert.equal(studio.formatUTC('not a timestamp'),'—');
});

test('static page does not persist Bearer or inject dynamic HTML',()=>{
  const html=fs.readFileSync(path.join(root,'site/group-c-studio.html'),'utf8');
  const js=fs.readFileSync(path.join(root,'site/group-c-studio.js'),'utf8');
  const css=fs.readFileSync(path.join(root,'site/group-c-studio.css'),'utf8');
  assert.match(html,/id="gcConnectForm"/);
  assert.match(html,/id="gcChart"/);
  assert.match(html,/Content-Security-Policy/);
  assert.match(html,/id="gcApproval"/);
  assert.match(html,/id="gcQuality"/);
  assert.match(css,/@media\(max-width:680px\)/);
  assert.doesNotMatch(js,/localStorage|sessionStorage|\.innerHTML\s*=|document\.write|eval\(/);
  assert.match(js,/credentials:'omit'/);
  assert.match(js,/redirect:'error'/);
  assert.match(js,/textContent/);
  assert.match(js,/checkedStudio\(payload.data,ids.projectId,ids.videoId\)/);
});

test('no local Studio implementation of C7 arithmetic exists',()=>{
  const js=fs.readFileSync(path.join(root,'site/group-c-studio.js'),'utf8');
  assert.doesNotMatch(js,/compute_breakout|compute_c_metrics|calculate_c7|0\.45\s*\*/);
  assert.equal(studio.STUDIO_VERSION,'group-c-studio/v1');
});
