'use strict';
/* Browser-controller E2E fixture, no OpenRouter call. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../site/index.html'),'utf8');
const code=fs.readFileSync(path.join(__dirname,'../site/taxonomy-run.js'),'utf8');
const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(x=>[x[1],elem()]));
function elem(tag='div'){
 const callbacks={};
 return {tagName:tag,textContent:'',value:'',checked:false,disabled:false,
   hidden:false,className:'',children:[],dataset:{},
   append(...a){this.children.push(...a);},
   replaceChildren(...a){this.children=[...a];},
   setAttribute(k,v){this[k]=v;},
   addEventListener(k,f){(callbacks[k] ||= []).push(f);},
   emit(k){for(const f of callbacks[k]||[])f({target:this});},
   querySelector(selector){
     const queue=[...this.children];
     while(queue.length){const child=queue.shift();
       if(child.className===selector.slice(1))return child;
       queue.push(...child.children);}
     return null;
   }
 };
}
const document={
 getElementById(id){assert(elements.has(id),'missing '+id);return elements.get(id);},
 createElement:elem,dispatchEvent(e){events.push(e);}
};
const events=[],requested=[];let status='extraction_pending',spent=0;
const facts=[{id:'fact-one',kind:'function',text:'Считать калории',
 source_field:'product_functions',source_quote:'считает калории'}];
const topics=[{id:'topic-one',name:'Калории по фото',relation:'direct',origin:'explicit',
 definition:'Калории по фото',include:'Учёт',exclude:'Лекарства'},
 {id:'topic-two',name:'Проблемы подсчёта',relation:'problem_related',
 origin:'inferred',definition:'Боль пользователя',include:'Ошибки',exclude:'Лекарства'}];
let reviewFacts=0,reviewTopics=0;
function fake(data,statusCode=200){return {ok:statusCode<400,status:statusCode,
 async json(){return data;}};}
async function fetch(url,options={}){
 const body=options.body?JSON.parse(options.body):null;
 requested.push({url,method:options.method||'GET',body});
 if(url.endsWith('/runs')&&body)return fake({ok:true,run_id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  status:'extraction_pending',paid_calls:0});
 if(url.endsWith('/advance')&&body){
   if(status==='extraction_pending'){status='fact_review';spent+=0.001;}
   else if(status==='generation_pending'){status='topic_review';spent+=0.002;}
   return fake({ok:true,status,paid_calls:1});
 }
 if(url.endsWith('/facts/review')&&body){
    reviewFacts++;status='generation_pending';
    return fake({ok:true,status});}
 if(url.endsWith('/topics/publish')&&body){
    reviewTopics++;status='approved';return fake({ok:true,status});}
 if(url.endsWith('/cancel')&&body){status='cancelled';return fake({ok:true,status});}
 if(url.includes('/runs/')&&!body){
    return fake({ok:true,run_id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      status,spent_usd:String(spent),reserved_usd:'0',authorized_usd:'0.30',
      stages:[],fact_proposals:facts,topic_proposals:topics,
      approved_facts:[],critic_findings:[],
      publication:status==='approved'?{version:1,topics}:null});
 }
 throw Error('Unexpected request '+url);
}
const store=new Map(),localStorage={
 getItem(k){return store.get(k)||null;},setItem(k,v){store.set(k,v);}
};
const window={addEventListener(){}};
const ctx=vm.createContext({document,window,fetch,localStorage,console,
  confirm:()=>true,Intl,Number,Date,Math,JSON,Error,
  CustomEvent:function(type,p){return {type,detail:p.detail};}});
vm.runInContext(code,ctx,{filename:'site/taxonomy-run.js'});
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const run=window.ContentMachineTaxonomyRun;
 const p='b'.repeat(32);
 await run.start(p,{quote_id:'c'.repeat(36)},{consent_id:'d'.repeat(36)});
 await tick();await tick();
 assert.equal(status,'extraction_pending');
 assert.equal(elements.get('tbRunFacts').hidden,true);
 assert(!requested.some(x=>x.url.endsWith('/advance')),'start must never auto-spend');
 elements.get('tbRunAdvance').emit('click');
 await tick();await tick();
 assert.equal(status,'fact_review');
 assert.equal(elements.get('tbRunFacts').hidden,false);
 assert.equal(elements.get('tbRunAdvance').hidden,true);
 elements.get('tbRunReviewFacts').emit('click');
 await tick();await tick();
 assert.equal(reviewFacts,1);
 assert.equal(status,'generation_pending');
 elements.get('tbRunAdvance').emit('click');
 await tick();await tick();
 assert.equal(status,'topic_review');
 assert.equal(elements.get('tbRunTopics').hidden,false);
 elements.get('tbRunPublish').emit('click');
 await tick();await tick();
 assert.equal(reviewTopics,1);
 assert.equal(status,'approved');
 assert.equal(elements.get('tbRunPublished').hidden,false);
 assert.equal(elements.get('tbRunAdvance').hidden,true);
 assert.equal(requested.filter(x=>x.url.endsWith('/advance')).length,2);
 assert(events.some(x=>x.type==='cm-taxonomy-run-show'));
 console.log('Taxonomy Builder run UI test passed: explicit stage calls, two human gates, publication, budget display.');
})().catch(err=>{console.error(err);process.exitCode=1;});
