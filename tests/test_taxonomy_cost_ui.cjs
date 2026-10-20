'use strict';
/* Deterministic DOM/fetch UI test for the real Block 5 cost controller. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../site/index.html'),'utf8');
const code=fs.readFileSync(path.join(__dirname,'../site/taxonomy-cost.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function element(tag='div'){
  const listeners={};
  return {tagName:tag,hidden:false,disabled:false,value:'',checked:false,textContent:'',
    children:[],className:'',
    replaceChildren(){this.children=[];},
    append(...x){this.children.push(...x);},
    addEventListener(type,fn){(listeners[type] ||= []).push(fn);},
    emit(type,patch={}){Object.assign(this,patch);for(const fn of listeners[type]||[])fn({target:this});}
  };
}
const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(x=>[x[1],element()]));
const events=[];
const document={
 getElementById(id){assert(elements.has(id),'Missing UI id: '+id);return elements.get(id);},
 createElement:element,
 dispatchEvent(e){events.push(e);},
};
const config="a".repeat(64);
const fakeQuote={
 quote_id:'e'.repeat(32),pipeline_id:'c'.repeat(32),
 low_usd:'0.001200',expected_usd:'0.007300',high_usd:'0.020000',
 expires_at:new Date(Date.now()+600000).toISOString(),currency:'USD',
 details:{
   pipeline_version:1,profile_revision:1,config_sha256:config,
   stages:[
     {role:'fact_extraction',model_id:'openai/gpt-6.1-sol',temperature:null,
      reasoning:{mode:'effort',effort:'medium'},pricing_verified_at:'2026-10-20T10:00:00Z',
      scenarios:{low:{estimated_usd:'0.000200'},expected:{estimated_usd:'0.001500'},high:{estimated_usd:'0.004000'}}},
     {role:'taxonomy_generation',model_id:'anthropic/claude-sonnet-5.5',
      temperature:0.4,reasoning:{mode:'default'},pricing_verified_at:'2026-10-20T10:00:00Z',
      scenarios:{low:{estimated_usd:'0.001000'},expected:{estimated_usd:'0.005800'},high:{estimated_usd:'0.016000'}}}
   ],
   assumptions:{excluded_documents_count:1,documents_included:false}
 }
};
let quote=null,consent=null,postCalls=[],consentNumber=0,quoteNumber=0;
function response(data,status=200){return {ok:status<400,status,async json(){return data;}};}
async function fetch(url,options){
 const method=options?.method||'GET',body=options?.body?JSON.parse(options.body):null;
 if(method==='POST')postCalls.push({url,body});
 if(url.endsWith('/cost')&&method==='GET'){
   const currentConsent=consent?.quote_id===quote?.quote_id?consent:null;
   return response({ok:true,status:
    !quote?'quote_required':currentConsent&&!currentConsent.revoked?'spending_authorized':'quote_ready',
    pipeline_id:fakeQuote.pipeline_id,quote,consent:currentConsent});
 }
 if(url.endsWith('/cost/quote')&&method==='POST'){
   quoteNumber++;
   quote={...fakeQuote,quote_id:'q'+quoteNumber,pipeline_id:fakeQuote.pipeline_id};
   return response({ok:true,reused:false,quote});
 }
 if(url.endsWith('/cost/consent')&&method==='POST'){
   if(!body.acknowledge_documents_excluded)return response({ok:false,error:'docs excluded'},422);
   if(Number(body.authorized_usd)<Number(quote.high_usd))
     return response({ok:false,error:'budget below high'},422);
   consent={consent_id:'x'+(++consentNumber),quote_id:body.quote_id,
     authorized_usd:body.authorized_usd,revoked:false};
   return response({ok:true,consent,status:'spending_authorized',launch_available:false});
 }
 if(url.endsWith('/cost/revoke')&&method==='POST'){
   consent.revoked=true;
   return response({ok:true,revoked:true,launch_available:false});
 }
 throw Error('Unexpected API '+method+' '+url);
}
const window={};
const context=vm.createContext({document,window,fetch,CustomEvent:function(type,params){
 return {type,detail:params.detail};},confirm:()=>true,console,Date,Intl,Number,Error});
vm.runInContext(code,context,{filename:'site/taxonomy-cost.js'});
(async()=>{
 assert(elements.has('tbCostRequote'));
 window.ContentMachineTaxonomyCost.open('a'.repeat(32));
 await tick();await tick();await tick();
 assert.equal(quoteNumber,1);
 assert.equal(elements.get('tbCostSummary').hidden,false);
 assert.equal(elements.get('tbCostBreakdown').children.length,2);
 assert.equal(elements.get('tbCostConsent').hidden,false);
 assert.equal(elements.get('tbCostDocsWrap').hidden,false);
 assert.equal(elements.get('tbCostAuthorize').disabled,true,'explicit consent required');
 elements.get('tbCostAckUncertainty').emit('change',{checked:true});
 assert.equal(elements.get('tbCostAuthorize').disabled,true,'docs need explicit acknowledgement');
 elements.get('tbCostAckDocuments').emit('change',{checked:true});
 assert.equal(elements.get('tbCostAuthorize').disabled,false);
 elements.get('tbCostAuthorize').emit('click');
 await tick();await tick();
 assert.equal(elements.get('tbCostAuthorized').hidden,false);
 assert.equal(elements.get('tbCostAuthorize').disabled,true);
 assert.equal(consentNumber,1);
 elements.get('tbCostRevoke').emit('click');
 await tick();await tick();
 assert.equal(consent.revoked,true);
 elements.get('tbCostRequote').emit('click');
 await tick();await tick();await tick();
 assert.equal(quoteNumber,2,'revocation requires new quote');
 assert.equal(elements.get('tbCostConsent').hidden,false,'new quote allows fresh consent');
 elements.get('tbCostBack').emit('click');
 assert(events.some(x=>x.type==='cm-taxonomy-cost-return'));
 assert(!postCalls.some(x=>/completion|\/runs|\/generate/.test(x.url)),
   'Block 5 must not call paid/generative APIs');
 const projects=fs.readFileSync(path.join(__dirname,'../site/projects.js'),'utf8');
 assert(projects.includes('cm-taxonomy-pipeline-open'),
   'Project handoff to the real Studio must exist');
 assert(projects.includes('cm-taxonomy-cost-show'),'Cost handoff must exist');
 console.log('Taxonomy quote UI interaction passed: price breakdown, consent, document acknowledgement, revocation, new quote, return, no paid calls.');
})().catch(err=>{console.error(err);process.exitCode=1;});
