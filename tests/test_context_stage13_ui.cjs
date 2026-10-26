'use strict';
/* Stage13 true browser-controller test: explicit approvals, stale rejection, no paid call. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../site/index.html'),'utf8');
const cost=fs.readFileSync(path.join(__dirname,'../site/taxonomy-cost.js'),'utf8');
const quoted=fs.readFileSync(path.join(__dirname,'../site/context-quote.js'),'utf8');
const ui=fs.readFileSync(path.join(__dirname,'../site/project-context.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function el(tag='div'){
  const handlers={};return {tagName:tag.toUpperCase(),children:[],
    hidden:false,disabled:false,checked:false,value:'',textContent:'',className:'',
    dataset:{},type:'',href:'',target:'',rel:'',
    append(...items){this.children.push(...items);},prepend(...items){this.children.unshift(...items);},
    replaceChildren(...items){this.children=[...items];},
    addEventListener(name,cb){(handlers[name] ||= []).push(cb);},
    setAttribute(k,v){this[k]=v;},remove(){this.removed=true;},
    emit(name){for(const handler of handlers[name]||[])handler({target:this});},
    querySelector(selector){
      const queue=[...this.children];
      while(queue.length){const n=queue.shift();
        if(selector==='pre'&&n.tagName==='PRE')return n;
        if(selector.startsWith('.')&&n.className===selector.slice(1))return n;
        queue.push(...n.children);
      }
      return null;
    }
  };
}
const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],el()]));
elements.get('tbCostInputMode').value='legacy_questionnaire_v1';
elements.get('tbContextTokenBudget').value='1200';
const events=[],requests=[];
const document={getElementById(id){assert(elements.has(id),'Missing UI element '+id);return elements.get(id);},
  createElement:el,dispatchEvent(v){events.push(v);}};
const project='a'.repeat(32), ctx='11111111-1111-4111-8111-111111111111';
const other='22222222-2222-4222-8222-222222222222';
const pipeline='33333333-3333-4333-8333-333333333333';
const evidenceId='44444444-4444-4444-8444-444444444444';
const newest='66666666-6666-4666-8666-666666666666';
let contextRevision='c'.repeat(64),quote=null,consent=null,quoteCalls=0,consentCalls=0,preflights=0;
let serverError=false,alterSecond=false;
function binding(){
 return {mode:'approved_project_context_v1',context_id:ctx,context_sha256:contextRevision,
   approval_id:'55555555-5555-4555-8555-555555555555',
   approval_decision:'approved_partial',
   selection_sha256:(alterSecond&&preflights>=2?'e':'d').repeat(64),
   selector_version:'rtb-evidence-selector/1',token_budget:1200,
   selected_evidence_ids:[evidenceId],excluded_evidence_count:2,
   excluded_source_ids:[other]};
}
const preview=()=>({ok:true,input_mode:'approved_project_context_v1',context_binding:binding(),
  selected_count:1,eligible_fact_count:1,profile_revision:1,profile_hash:'f'.repeat(64),
  no_provider_call:true,no_quote_created:true,
  evidence:[{evidence_id:evidenceId,source_type:'questionnaire_field',
    citation:'profile:product_functions:0-32',trust_label:'direct_user_statement',
    evidence_kind:'user_claim',knowledge_scope:'operational_product',
    allowed_fact_kinds:['function','problem'],text:'Tracks steps throughout the day.'}]});
const snapshots=()=>({ok:true,snapshots:[
  {context_id:ctx,context_version:2,context_sha256:contextRevision,decision:'approved_partial',
   profile_revision:1,coverage:{evidence_total:1,sources_incomplete:1}},
  {context_id:other,context_version:1,context_sha256:'a'.repeat(64),
   decision:'rejected',profile_revision:1,coverage:{evidence_total:1,sources_incomplete:0}}]});
const respond=(data,status=200)=>({ok:status<400,status,async json(){return data;}});
async function fetch(url,options={}){
 const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
 requests.push({url,method,body});
 if(url.includes('/context/snapshots?')){
   const found=/[?&]limit=(\d+)/.exec(url);
   if(!found||Number(found[1])>80)
     return respond({ok:false,error:'Stage11 maximum page size is 80'},400);
   return respond(snapshots());
 }
 if(url.includes('/context/draft'))return respond({ok:true,
   context_sha256:contextRevision,context:{schema_version:'project-context/v1',
     selection_policy:'evidence-on-demand/v1',profile:{revision:1},
     coverage:{sources_total:1,sources_incomplete:1,analysis_outputs:0,evidence_total:1,
       reviewed_approved:1,review_pending:0,reviewed_rejected:0},warnings:[],
     source_manifest:[],evidence_manifest:[],unavailable_or_excluded_sources:[]}});
 if(url.endsWith('/context/build')&&method==='POST')
   return respond({ok:true,context_version:3,context_id:newest,created:true});
 if(url.includes('/context/snapshots/'+newest)&&!url.endsWith('/decide')){
   return respond({ok:true,context_id:newest,context_version:3,context_sha256:contextRevision,
     approval:null,context:{schema_version:'project-context/v1',
       selection_policy:'evidence-on-demand/v1',profile:{revision:1},
       coverage:{sources_total:1,sources_incomplete:0,analysis_outputs:0,evidence_total:0,
         reviewed_approved:0,review_pending:0,reviewed_rejected:0},
       warnings:[],source_manifest:[],evidence_manifest:[],
       unavailable_or_excluded_sources:[]}});
 }
 if(url.includes('/context/snapshots/'+ctx)&&!url.endsWith('/decide')){
   return respond({ok:true,context_id:ctx,context_version:2,context_sha256:contextRevision,
     approval:{decision:'approved_partial'},context:{
       schema_version:'project-context/v1',selection_policy:'evidence-on-demand/v1',
       profile:{revision:1},coverage:{sources_total:1,sources_incomplete:1,analysis_outputs:0,
         evidence_total:1,reviewed_approved:1,review_pending:0,reviewed_rejected:0},
       warnings:['source_coverage_incomplete'],source_manifest:[],evidence_manifest:[],
       unavailable_or_excluded_sources:[{source_id:other,reason:'unavailable'}]}});
 }
 if(url.includes('/taxonomy-builder/context-preview')){
   preflights++;
   if(serverError)return respond({ok:false,error:'Context has changed'},409);
   return respond(preview());
 }
 if(url.endsWith('/taxonomy-builder/cost')&&method==='GET')
   return respond({ok:true,pipeline_id:pipeline,
     status:quote?'quote_ready':'quote_required',quote,consent});
 if(url.endsWith('/taxonomy-builder/cost/quote')&&method==='POST'){
   quoteCalls++;
   const selectedBinding=body.context_id?binding():null;
   quote={quote_id:'q'+quoteCalls,high_usd:'0.020000',low_usd:'0.001000',
     expected_usd:'0.009000',expires_at:'2027-01-01T00:00:00Z',
     details:{input_mode:body.context_id?'approved_project_context_v1':'legacy_questionnaire_v1',
       context_binding:selectedBinding,stages:[],assumptions:{excluded_documents_count:0}}};
   consent=null;
   return respond({ok:true,quote,reused:false});
 }
 if(url.endsWith('/taxonomy-builder/cost/consent')&&method==='POST'){
   consentCalls++;
   // Mirror the production _consent payload: no embedded quote_id.
   consent={consent_id:'consent'+consentCalls,
     authorized_usd:body.authorized_usd,revoked:false};
   return respond({ok:true,consent,status:'spending_authorized'});
 }
 if(url.endsWith('/taxonomy-builder/cost/revoke')&&method==='POST'){
   consent.revoked=true;return respond({ok:true,revoked:true});
 }
 throw Error('Unexpected request '+method+' '+url);
}
const window={confirm:()=>true};
const context=vm.createContext({document,window,fetch,console,Intl,Number,Date,Math,
  CustomEvent:function(type,params){return {type,detail:params.detail}},
  confirm:()=>true,Error});
vm.runInContext(cost,context,{filename:'site/taxonomy-cost.js'});
vm.runInContext(quoted,context,{filename:'site/context-quote.js'});
vm.runInContext(ui,context,{filename:'site/project-context.js'});
(async()=>{
  assert(html.includes('/context-quote.js'));
  window.ContentMachineContextQuote.open(project,ctx,1200);
  for(let i=0;i<8;i++)await tick();
  assert.equal(elements.get('tbContextSelect').value,ctx);
  assert(requests.some(x=>x.url.includes('/context/snapshots?limit=80')),
    'Stage13 must respect the real Stage11 MAX_LIST=80 API contract');
  assert.equal(quoteCalls,0,'opening Context must never create a quote');
  assert.equal(preflights,0,'preflight only after explicit action');
  assert(elements.get('tbContextOptions').hidden===false);
  elements.get('tbContextCheck').emit('click');
  for(let i=0;i<8;i++)await tick();
  assert.equal(preflights,1);
  assert(elements.get('tbContextStatus').textContent.includes(contextRevision));
  assert.equal(elements.get('tbContextEvidence').children.length,1);
  assert.equal(elements.get('tbContextCreateQuote').disabled,true);
  elements.get('tbContextAckSelection').checked=true;
  elements.get('tbContextAckSelection').emit('change');
  assert.equal(elements.get('tbContextCreateQuote').disabled,true,
    'partial Context requires a separate acknowledgement');
  elements.get('tbContextAckPartial').checked=true;
  elements.get('tbContextAckPartial').emit('change');
  assert.equal(elements.get('tbContextCreateQuote').disabled,false);
  alterSecond=true;
  elements.get('tbContextCreateQuote').emit('click');
  for(let i=0;i<8;i++)await tick();
  assert.equal(quoteCalls,0,'changed hash must block quote creation');
  assert(elements.get('tbContextStatus').textContent.includes('изменились'));
  alterSecond=false;preflights=0;
  elements.get('tbContextCheck').emit('click');
  for(let i=0;i<8;i++)await tick();
  elements.get('tbContextAckSelection').checked=true;
  elements.get('tbContextAckSelection').emit('change');
  elements.get('tbContextAckPartial').checked=true;
  elements.get('tbContextAckPartial').emit('change');
  elements.get('tbContextCreateQuote').emit('click');
  for(let i=0;i<8;i++)await tick();
  assert.equal(quoteCalls,1,'explicit click creates exactly one Context quote');
  assert.equal(elements.get('tbCostSummary').hidden,false);
  assert(elements.get('tbCostScope').textContent.includes('1 закреплённых Evidence'));
  assert.equal(elements.get('tbCostAuthorize').disabled,true,'consent explicit, not automatic');
  elements.get('tbCostAckUncertainty').checked=true;
  elements.get('tbCostAckUncertainty').emit('change');
  assert.equal(elements.get('tbCostAuthorize').disabled,false);
  elements.get('tbCostAuthorize').emit('click');
  for(let i=0;i<8;i++)await tick();
  assert.equal(consentCalls,1);
  assert.equal(elements.get('tbCostAuthorized').hidden,false);
  // Stage13 refresh/reopen: existing Context quote/consent must be restored
  // from server after explicit exact preflight, NEVER replaced automatically.
  const savedQuoteCalls=quoteCalls,savedConsentCalls=consentCalls;
  window.ContentMachineTaxonomyCost.open(project);
  for(let i=0;i<10;i++)await tick();
  assert.equal(quoteCalls,savedQuoteCalls,'reopen must not silently create legacy quote');
  assert.equal(elements.get('tbCostInputMode').value,'approved_project_context_v1');
  assert.equal(elements.get('tbCostSummary').hidden,true,
    'Previous approval hidden until current Evidence selection is revalidated');
  elements.get('tbContextCheck').emit('click');
  for(let i=0;i<10;i++)await tick();
  assert.equal(elements.get('tbCostSummary').hidden,false,'same quote restored after rechecking');
  assert.equal(elements.get('tbCostAuthorized').hidden,false,'same consent restored from PostgreSQL state');
  assert.equal(consentCalls,savedConsentCalls,'restoration never reauthorizes budget');
  assert.equal(quoteCalls,savedQuoteCalls);
  // A failed revalidation must instantly remove a formerly valid quote
  // from the UI, without revoking/changing the immutable server consent.
  serverError=true;
  elements.get('tbContextCheck').emit('click');
  for(let i=0;i<8;i++)await tick();
  assert.equal(elements.get('tbCostSummary').hidden,true,
    'Failed or stale Evidence preflight must hide previously authorized quote');
  assert.equal(elements.get('tbCostAuthorized').hidden,true);
  assert.equal(quoteCalls,savedQuoteCalls);
  assert.equal(consentCalls,savedConsentCalls);
  serverError=false;
  elements.get('tbContextTokenBudget').value='1400';
  elements.get('tbContextTokenBudget').emit('input');
  assert.equal(elements.get('tbCostSummary').hidden,true,'changed budget invalidates quote');
  assert.equal(elements.get('tbCostAuthorize').disabled,true);
  assert(!requests.some(x=>x.url.endsWith('/advance')||x.url.endsWith('/runs')),
    'Stage13 must not dispatch billable requests');
  const otherProject='b'.repeat(32);
  window.ContentMachineTaxonomyCost.open(otherProject);
  for(let i=0;i<8;i++)await tick();
  const beforeSwitch=quoteCalls;
  // Returning to legacy from an existing Context quote must be an EXPLICIT
  // opt-in. It is allowed to calculate a new quote, but not spend money.
  elements.get('tbCostInputMode').value='legacy_questionnaire_v1';
  elements.get('tbCostInputMode').emit('change');
  for(let i=0;i<8;i++)await tick();
  assert.equal(quoteCalls,beforeSwitch+1);
  const legacyQuotes=quoteCalls;
  elements.get('tbCostInputMode').value='approved_project_context_v1';
  elements.get('tbCostInputMode').emit('change');
  for(let i=0;i<8;i++)await tick();
  assert(requests.some(x=>x.url.includes('/api/projects/'+otherProject+'/context/snapshots')),
    'Context selector entered from Pipeline Studio must use the active project, not stale previous project');
  assert.equal(quoteCalls,legacyQuotes,
    'Switching to Context mode must not auto-create a quote');
  // Stage13 snapshot switching: building a new version while an older one
  // is being inspected must display the newly built version, not keep the old.
  const detailsRoot=el();
  window.ContentMachineProjectContext.mount(detailsRoot,project);
  for(let i=0;i<8;i++)await tick();
  const panel=detailsRoot.children[0];
  const history=panel.children[6],inspector=panel.children[7],actions=panel.children[2];
  history.children[0].children[2].emit('click');
  for(let i=0;i<8;i++)await tick();
  assert(inspector.children[0].textContent.includes('Context v2'));
  actions.children[0].emit('click');
  for(let i=0;i<8;i++)await tick();
  actions.children[1].emit('click');
  for(let i=0;i<8;i++)await tick();
  assert(inspector.children[0].textContent.includes('Context v3'),
    'New Context must be inspectable immediately even while a different snapshot was open');
  assert(requests.some(x=>x.url.endsWith('/context/build')&&x.method==='POST'),
    'Snapshot build must require its own POST separate from read-only inspection');
  console.log('Stage13 Context UI passed: exact source preview, partial approval, stale prevention, explicit quote+consent, budget invalidation, zero paid calls.');
})().catch(error=>{console.error(error);process.exitCode=1;});
