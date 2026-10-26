'use strict';
/* Browserless test of real RTB B9 Deep V2 UI; NO provider requests. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../site/index.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'../site/taxonomy-deep-v2.js'),'utf8');
assert.match(html,/id="tbV2Panel"/);
assert.ok(html.indexOf('id="tbV2Panel"')>html.indexOf('id="projectDetailsFields"'),'B9 entry should be in project details');
assert.match(html,/src="\/taxonomy-deep-v2.js"/);
assert.doesNotMatch(script,/\.innerHTML\s*=/);
const nodes=new Map(),callbacks={};
function dom(tag='div'){
  const listeners={};
  const item={tagName:tag.toUpperCase(),value:'',textContent:'',
    hidden:false,disabled:false,checked:false,dataset:{},children:[],
    className:'',style:{},type:'',onclick:null,
    append(...kids){this.children.push(...kids)},
    replaceChildren(...kids){this.children=[...kids]},
    addEventListener(ev,fn){(listeners[ev]||=[]).push(fn)},
    emit(ev){if(ev==='click'&&typeof this.onclick==='function')this.onclick();
      for(const fn of listeners[ev]||[])fn({target:this,preventDefault(){}})},
    querySelector(selector){
      const className=selector.replace(/^\./,'');
      return this.children.find(x=>x.className===className)||null;
    },
    setAttribute(k,v){this[k]=v},
  };
  return item;
}
for(const m of html.matchAll(/id="([^"]+)"/g))nodes.set(m[1],dom());
const document={
  getElementById(id){assert(nodes.has(id),'Missing browser ID '+id);return nodes.get(id)},
  createElement:dom,
  addEventListener(ev,fn){(callbacks[ev]||=[]).push(fn)},
  dispatchEvent(event){for(const fn of callbacks[event.type]||[])fn(event)}
};
const storage=new Map(),localStorage={
 getItem(k){return storage.get(k)||null},
 setItem(k,v){storage.set(k,v)}
};
const PID='a'.repeat(32);
const RUN='11111111-1111-4111-8111-111111111111';
const SNAP='33333333-3333-4333-8333-333333333333';
const BIND='44444444-4444-4444-8444-444444444444';
const QUOTE='55555555-5555-4555-8555-555555555555';
const CONSENT='66666666-6666-4666-8666-666666666666';
const PUB='77777777-7777-4777-8777-777777777777';
const node=(id,kind,parent)=>({id,kind,parent_id:parent,
  source_fact_ids:[],relation:'adjacent',name:'Sample <img src=x onerror=alert(1)>',
  rationale:'Evidence',definition:'Detailed research direction',
  include:'Include',exclude:'Exclude',examples:['Example'],
  source_observation_ids:[],search_signals:kind==='hypothesis'?['real videos']:[],
  validation_focus:kind==='hypothesis'?['test formats']:[]});
const tree=[node('direction_one','direction',null),
  node('direction_two','direction',null),
  node('hypothesis_one','hypothesis','direction_one'),
  node('hypothesis_two','hypothesis','direction_two')];
let state={status:'pass1_pending',spent_usd:'0',reserved_usd:'0',authorized_usd:'0.200000',
 expansion_count:0,attempt:{output:{nodes:tree}}};
let paid=0,allowConfirm=true,expansionMode=false,expansionNo=0;
const expansionQuoteIDs=['88888888-8888-4888-8888-888888888881','88888888-8888-4888-8888-888888888882'];
const expansionConsentIDs=['99999999-9999-4999-8999-999999999991','99999999-9999-4999-8999-999999999992'];
const expansionHashes=['1'.repeat(64),'2'.repeat(64)];
const issuedQuotes=[],issuedConsents=[];
const req=[];
async function fetch(url,opts={}){
 const method=opts.method||'GET';
 const body=opts.body?JSON.parse(opts.body):null;
 req.push({method,url,body});
 let result={ok:true};
 if(method==='POST'){
   if(url.endsWith('bindings/pin'))result={...result,binding_id:BIND,research_input_sha256:'a'.repeat(64)};
   else if(url.endsWith('/quotes'))result={...result,quote_id:QUOTE,required_usd:'0.20',
     quote_sha256:'b'.repeat(64),pass1_exact_reservation_usd:'0.08',
     pass2_bounded_reservation_usd:'0.12'};
   else if(url.endsWith('/consents'))result={...result,consent_id:CONSENT};
   else if(url.endsWith('/runs/start')){
     assert.equal(body.idempotency_key.length>=12,true);
     state.status='pass1_pending';result={...result,run_id:RUN};
   }else if(url.endsWith('advance-pass1')){
     paid++;state.status='review1';result={...result,status:state.status};
   }else if(url.endsWith('/review1')){
     assert.equal(body.decisions.length,tree.length);
     assert.equal(body.decisions[0].action,'edit');
     assert.equal(body.decisions[0].node.id,tree[0].id);
     assert.equal(Object.keys(body.decisions[0].node).length,14,
       'H1 edits must preserve all 14 signed V2 fields');
     assert.equal(body.decisions[0].node.name,'Edited B9-grounded direction');
     state.status='pass2_pending';result={...result,status:state.status};
   }else if(url.endsWith('advance-pass2')){
     paid++;state.status=expansionMode?'expand_pending':'review2';
     result={...result,status:state.status};
   }else if(url.endsWith('expansion-quote')){
     assert.equal(state.status,'expand_pending');
     assert.ok(expansionNo<2,'A third expansion quote must not exist');
     issuedQuotes.push(expansionQuoteIDs[expansionNo]);
     result={...result,quote_id:expansionQuoteIDs[expansionNo],
       required_usd:'0.031',quote_sha256:expansionHashes[expansionNo],
       expansion_no:expansionNo+1,explicit_consent_required:true,provider_calls:0};
   }else if(url.endsWith('expansion-consent')){
     assert.equal(state.status,'expand_pending');
     assert.equal(body.quote_id,expansionQuoteIDs[expansionNo]);
     assert.equal(body.authorized_usd,'0.031');
     assert.equal(body.acknowledged_quote_sha256,expansionHashes[expansionNo]);
     issuedConsents.push(expansionConsentIDs[expansionNo]);
     result={...result,consent_id:expansionConsentIDs[expansionNo],
       expansion_no:expansionNo+1,consent_recorded:true,provider_calls:0};
   }else if(url.endsWith('advance-expansion')){
     assert.equal(state.status,'expand_pending');
     assert.equal(body.consent_id,expansionConsentIDs[expansionNo],
       'Paid expansion must use the exact separately consented grant');
     assert.equal(body.authorize_paid_call,true);
     paid++;expansionNo++;state.expansion_count=expansionNo;
     state.status=expansionNo<2?'expand_pending':'blocked_quality';
     result={...result,status:state.status,expansion_count:expansionNo};
   }else if(url.endsWith('/review2')){
     assert.equal(body.decisions.length,tree.length);
     state.status='approved';result={...result,status:state.status};
   }else if(url.endsWith('/publish')){
     state.status='published';result={...result,publication_id:PUB};
   }else throw Error('Unmocked paid or mutation '+url);
 } else if(url.endsWith('/status'))result={...result,...state};
 else if(url.endsWith('/review2-preview'))result={...result,candidate_nodes:tree,
   readiness:{passed:true,blocking_errors:[]}};
 else if(url.endsWith('/publication'))result={...result,publication_id:PUB};
 else if(url.includes('/exports/'))result={...result,
   schema_version:url.includes('v2')?'rtb-publication-export/v2':'rtb-publication-export/v1',
   nodes:tree,publication_id:PUB};
 else throw Error('Unmocked GET '+url);
 return {ok:true,status:200,async json(){return result}};
}
const window={confirm(){return allowConfirm}};
const context=vm.createContext({document,window,fetch,localStorage,
  JSON,Math,Date,Array,Object,String,Number,Error,Promise,encodeURIComponent,console});
vm.runInContext(script,context,{filename:'site/taxonomy-deep-v2.js'});
const $=id=>document.getElementById(id);
const tick=async()=>{for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r))};
(async()=>{
  document.dispatchEvent({type:'cm-taxonomy-deep-v2-project-open',detail:{projectId:PID}});
  assert.equal(paid,0);
  assert.equal(req.length,0,'No network on panel open');
  $('tbV2Bind').emit('click');await tick();
  assert.equal(req.length,0,'Invalid snapshot UUID cannot initiate request');
  $('tbV2Snapshot').value=SNAP;$('tbV2Bind').emit('click');await tick();
  assert.equal($('tbV2Quote').disabled,false);
  $('tbV2Quote').emit('click');await tick();
  assert.equal($('tbV2Consent').disabled,true);
  $('tbV2ConsentAck').checked=true;$('tbV2ConsentAck').emit('change');
  assert.equal($('tbV2Consent').disabled,false);
  $('tbV2Consent').emit('click');await tick();
  assert.equal($('tbV2Start').disabled,false);
  $('tbV2Start').emit('click');await tick();
  assert.equal(state.status,'pass1_pending');
  assert.equal(paid,0,'No model before paid action');
  assert.equal($('tbV2Actions').children.length,1);
  allowConfirm=false;
  $('tbV2Actions').children[0].emit('click');await tick();
  assert.equal(paid,0,'User rejected paid confirmation');
  allowConfirm=true;
  $('tbV2Actions').children[0].emit('click');await tick();
  assert.equal(paid,1);
  assert.equal(state.status,'review1');
  const rows=$('tbV2ReviewRows').children.filter(n=>n.dataset.id);
  assert.equal(rows.length,tree.length);
  assert.equal($('tbV2Review').hidden,false);
  for(const row of rows)row.querySelector('.tb-v2-decision').value='approve';
  rows[0].querySelector('.tb-v2-decision').value='edit';
  rows[0].querySelector('.tb-v2-field-name').value='Edited B9-grounded direction';
  assert.equal(rows[0].querySelector('.tb-v2-field-parent_id').value,'');
  rows[0].querySelector('.tb-v2-field-source_fact_ids').value='{"forged":"fact"}';
  const beforeInvalidReview=req.length;
  $('tbV2ReviewSubmit').emit('click');await tick();
  assert.equal(req.length,beforeInvalidReview,
    'Invalid B9 fact array must be refused before POST');
  assert.match($('tbV2Error').textContent,/(JSON-массив|только строки)/);
  assert.equal(state.status,'review1');
  rows[0].querySelector('.tb-v2-field-source_fact_ids').value='[]';
  $('tbV2ReviewSubmit').emit('click');await tick();
  assert.equal(state.status,'pass2_pending');
  assert.equal(paid,1,'Human approval is not paid');
  $('tbV2Actions').children[0].emit('click');await tick();
  assert.equal(state.status,'review2');
  assert.equal(paid,2);
  const h2=$('tbV2ReviewRows').children.filter(n=>n.dataset.id);
  for(const row of h2)row.querySelector('.tb-v2-decision').value='approve';
  $('tbV2ReviewSubmit').emit('click');await tick();
  assert.equal(state.status,'approved');
  $('tbV2Actions').children[0].emit('click');await tick();
  assert.equal(state.status,'published');
  $('tbV2Actions').children[0].emit('click');await tick();
  assert.equal($('tbV2Tree').hidden,false);
  assert.match($('tbV2Tree').textContent,/rtb-publication-export\/v2/);
  assert.equal(paid,2,'GET/export/publish never dispatch paid provider');
  assert.ok(req.every(x=>!x.body||!Object.keys(x.body).includes('reviewer_id')));
  assert.ok(req.filter(x=>x.url.endsWith('advance-pass1')||
                             x.url.endsWith('advance-pass2'))
      .every(x=>x.body.authorize_paid_call===true));
  assert.equal(storage.get('cm.rtb.v2.last.'+PID),RUN);
  // Separate recovery path: an already billed Pass2 left an explicit quality
  // gap. Every expansion must obtain a NEW quote and NEW consent before paid.
  const OTHER='b'.repeat(32);
  expansionMode=true;expansionNo=0;state.status='expand_pending';
  document.dispatchEvent({type:'cm-taxonomy-deep-v2-project-open',
    detail:{projectId:OTHER}});
  $('tbV2RunId').value=RUN;
  $('tbV2Resume').emit('click');await tick();
  assert.equal($('tbV2Actions').children.length,1);
  for(let number=1;number<=2;number++){
    const before=paid;
    $('tbV2Actions').children[0].emit('click');await tick();
    assert.equal(paid,before,'An expansion quote is not a paid request');
    const confirm=$('tbV2Actions').children.find(b=>
      b.textContent.startsWith('Подтвердить новую сумму'));
    assert(confirm,'Expansion quote must show a separate USD consent');
    confirm.emit('click');await tick();
    assert.equal(paid,before,'Expansion authorization is not a paid request');
    const execute=$('tbV2Actions').children.find(b=>
      b.textContent.startsWith('Оплатить Expansion'));
    assert(execute,'Only a separately consented expansion can execute');
    execute.emit('click');await tick();
    assert.equal(paid,before+1);
    assert.equal(state.status,number===1?'expand_pending':'blocked_quality');
  }
  assert.equal(expansionNo,2);
  assert.deepEqual(issuedQuotes,expansionQuoteIDs,
    'Each expansion must get its own quote ID');
  assert.deepEqual(issuedConsents,expansionConsentIDs,
    'Each expansion must get a distinct consent ID');
  assert.equal(new Set(issuedQuotes).size,2);
  assert.equal(new Set(issuedConsents).size,2);
  assert.equal(req.filter(x=>x.url.endsWith('advance-expansion')).length,2);
  assert.equal($('tbV2Actions').children.length,0,
    'Third expansion must never become a UI action');
  // Simulate a stale or inconsistent server transition. A client receiving
  // expand_pending + count=2 MUST NOT offer another quote/consent/paid action.
  state.status='expand_pending';state.expansion_count=2;
  $('tbV2Resume').emit('click');await tick();
  assert.equal($('tbV2Actions').children.length,0,
    'No third quote button even if status is incorrectly expand_pending');
  assert.match($('tbV2Error').textContent,/максимум/);
  assert.equal(paid,4,'No third OpenRouter request');
  process.stdout.write('RTB Deep V2 B9 UI mock: H1/H2 + 2 separate expansions OK\n');
})().catch(err=>{console.error(err);process.exitCode=1});
