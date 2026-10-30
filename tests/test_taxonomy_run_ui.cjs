'use strict';
/* Browser-controller E2E fixture, no OpenRouter call. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../site/index.html'),'utf8');
const code=fs.readFileSync(path.join(__dirname,'../site/taxonomy-run.js'),'utf8');
const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(x=>[x[1],elem()]));
// Lightweight DOM fixture must preserve [hidden] attributes from HTML.
for(const id of ['tbRunHistoryPanel','tbRunDetailsPanel','tbRunReviewDeck'])
  elements.get(id).hidden=true;
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
const events=[],requested=[];let status='extraction_pending',spent=0,inputMode='legacy_questionnaire_v1';
const facts=[{id:'fact-one',kind:'function',text:'Считать калории',
 source_field:'product_functions',source_quote:'считает калории'}];
const topics=[{id:'topic-one',name:'Калории по фото',relation:'direct',origin:'explicit',
 definition:'Калории по фото',include:'Учёт',exclude:'Лекарства',
 positive_examples:['Фото дневник питания'],negative_examples:['Обзор лекарств']},
 {id:'topic-two',name:'Проблемы подсчёта',relation:'problem_related',
 origin:'inferred',definition:'Боль пользователя',include:'Ошибки',exclude:'Лекарства',
 positive_examples:['Учёт порций'],negative_examples:['Обзор гоночных машин']}];
let reviewFacts=0,reviewTopics=0;
function fake(data,statusCode=200){return {ok:statusCode<400,status:statusCode,
 async json(){return data;}};}
async function fetch(url,options={}){
 const body=options.body?JSON.parse(options.body):null;
 requested.push({url,method:options.method||'GET',body});
 if(url.endsWith('/runs')&&body)return fake({ok:true,run_id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  status:'extraction_pending',paid_calls:0});
 if(url.endsWith('/runs')&&!body)return fake({ok:true,runs:[{
   run_id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
   status,spent_usd:String(spent),reserved_usd:'0',
   created_at:'2026-10-20T10:00:00Z',
   stages:[{role:'fact_extraction',model_id:'xiaomi/mimo-v2.6-flash',
            max_output_tokens:5600},
           {role:'taxonomy_generation',model_id:'anthropic/claude-opus-5.5',
            max_output_tokens:10000},
           {role:'taxonomy_critic',model_id:'anthropic/claude-opus-5.5',
            max_output_tokens:8000}]
 }]});
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
      status,input_mode:inputMode,spent_usd:String(spent),reserved_usd:'0',authorized_usd:'0.30',
      stages:[],fact_proposals:facts,topic_proposals:topics,
      approved_facts:[],critic_findings:[{topic_name:'Калории по фото',
        issue:'Здесь есть риск обещания результата без доказательств',
        suggestion:'Уточнить правила отбора и не обещать лечение',severity:'medium'}],
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
 assert.equal(run.confirmLeave(),true,'No in-flight request should block normal navigation');
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
 const factChoices=elements.get('tbRunFactRows').children;
 assert.equal(factChoices[0].querySelector('.tb-run-keep').checked,false,
  'User must explicitly approve every fact');
 assert.equal(elements.get('tbRunReviewDeck').hidden,false);
 assert.equal(elements.get('tbRunReviewCommit').hidden,true,
   'No publication before the owner reviews each card');
 elements.get('tbReviewApprove').emit('click');
 assert.equal(factChoices[0].querySelector('.tb-run-keep').checked,true);
 await run.open(p,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
 assert.equal(elements.get('tbRunFactRows').children[0].querySelector('.tb-run-keep').checked,true,
   'Explicit decisions must survive refresh and a re-opened project');
 assert.equal(elements.get('tbRunReviewCommit').hidden,false);
 // Owner can navigate without losing votes. No backend call until final commit.
 elements.get('tbReviewPrev').emit('click');
 assert.equal(elements.get('tbRunReviewCommit').hidden,false);
 elements.get('tbReviewNext').emit('click');
 elements.get('tbRunReviewCommit').emit('click');
 await tick();await tick();
 assert.equal(reviewFacts,1);
 assert.equal(status,'generation_pending');
 elements.get('tbRunAdvance').emit('click');
 await tick();await tick();
 assert.equal(status,'topic_review');
 assert.equal(elements.get('tbRunTopics').hidden,false);
 elements.get('tbRunTopicAddName').value='Смежные привычки';
 elements.get('tbRunTopicAddDefinition').value='Общие привычки';
 elements.get('tbRunTopicAddInclude').value='Здоровые привычки';
 elements.get('tbRunTopicAddExclude').value='Советы по лекарствам';
 elements.get('tbRunTopicAddPositive').value='Пример привычки';
 elements.get('tbRunTopicAddNegative').value='Обзор автомобиля';
 const topicChoices=elements.get('tbRunTopicRows').children;
 assert.equal(topicChoices[0].querySelector('.tb-run-keep').checked,false,
  'User must explicitly approve every category');
 assert.equal(elements.get('tbReviewPhase').textContent,'Независимая проверка тем');
 assert.equal(elements.get('tbRunReviewCommit').hidden,true,
   'Critic suggestions must never auto-publish');
 elements.get('tbReviewApprove').emit('click');
 assert.equal(elements.get('tbReviewPhase').textContent,'Выбор тем');

 assert.equal(elements.get('tbRunReviewCommit').hidden,true);
 elements.get('tbReviewApprove').emit('click');
 assert.equal(topicChoices[0].querySelector('.tb-run-keep').checked,true);
 assert.equal(elements.get('tbRunReviewCommit').hidden,true,
   'Second topic still unreviewed');
 elements.get('tbReviewReject').emit('click');
 assert.equal(topicChoices[1].querySelector('.tb-run-keep').checked,false);
 elements.get('tbReviewPrev').emit('click');
 assert.equal(elements.get('tbReviewCount').textContent.startsWith('1 из 2'),true);
 elements.get('tbReviewNext').emit('click');
 elements.get('tbReviewApprove').emit('click');
 assert.equal(topicChoices[1].querySelector('.tb-run-keep').checked,true);
 assert.equal(elements.get('tbRunReviewCommit').hidden,false);
 elements.get('tbRunReviewCommit').emit('click');
 await tick();await tick();
 assert.equal(reviewTopics,1);
 const added=requested.find(x=>x.url.endsWith('/topics/publish')).body.decisions.find(x=>x.action==='add');
 assert.equal(added.name,'Смежные привычки');
 assert.equal(added.positive_examples[0],'Пример привычки');
 assert.equal(status,'approved');
 assert.equal(elements.get('tbRunPublished').hidden,false);
 assert.equal(elements.get('tbRunAdvance').hidden,true);
 assert.equal(requested.filter(x=>x.url.endsWith('/advance')).length,2);
 assert(events.some(x=>x.type==='cm-taxonomy-run-show'));
 assert.equal(elements.get('tbRunHistoryList').children.length,1,
   'PostgreSQL run history must be visible in product');
 // A subsequent failed Critic must NOT erase/hide expensive generated topics.
 status='failed';
 await run.open(p,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
 assert.equal(elements.get('tbRunTopics').hidden,true,
   'Inactive edit forms must remain hidden after the Critic fails');
 assert.equal(elements.get('tbRunAdvance').hidden,true,
   'Failed attempts must not expose an automatic paid retry');
 assert.match(elements.get('tbRunNextHint').textContent,/Новое исследование/,
   'Stopped run must explain how to start a fresh, separately approved quote');
 assert.equal(elements.get('tbRunReviewDeck').hidden,false,
   'Produced topics remain inspectable in a read-only card after a later failure');
 const frozen=elements.get('tbRunTopicRows').children[0];
 assert.equal(elements.get('tbReviewCard').children.length>0,true);
 assert.equal(elements.get('tbReviewApprove').hidden,true);
 elements.get('tbRunOpenHistory').emit('click');
 assert.equal(elements.get('tbRunHistoryPanel').hidden,false);
 elements.get('tbRunCloseHistory').emit('click');
 assert.equal(elements.get('tbRunHistoryPanel').hidden,true);
 elements.get('tbRunOpenDetails').emit('click');
 assert.equal(elements.get('tbRunDetailsPanel').hidden,false);
 elements.get('tbRunCloseDetails').emit('click');
 assert.equal(elements.get('tbRunDetailsPanel').hidden,true);
 assert.equal(frozen.querySelector('.tb-run-keep').disabled,true);
 assert.equal(frozen.querySelector('.tb-run-topic-name').readOnly,true);
 assert.equal(frozen.querySelector('.tb-run-topic-definition').readOnly,true);
 assert.equal(elements.get('tbRunPublish').disabled,true);
 assert.equal(elements.get('tbRunExport').disabled,false);
 assert.equal(requested.filter(x=>x.url.endsWith('/advance')).length,2,
   'Reading failed results/history must never trigger a new billable call');
 // Stage12: the existing human Fact Review respects immutable contextual Evidence.
 status='fact_review';inputMode='approved_project_context_v1';
 Object.assign(facts[0],{
   source_field:undefined,source_evidence_id:'e0000000-0000-4000-8000-000000000001',
   source_type:'research_document',citation:'doc:page1:characters-4-22',
   trust_label:'extracted_document_unverified',evidence_kind:'external_claim'
 });
 store.clear();
 await run.open(p,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
 const contextFact=elements.get('tbRunFactRows').children[0];
 assert.equal(contextFact.querySelector('.tb-run-edit').readOnly,true,
   'Source-grounded Context facts must not be edited without replacing Evidence');
 assert.equal(elements.get('tbRunNewFact').disabled,true,
   'Ungrounded human-added Context facts must be disabled');
 assert.equal(elements.get('tbRunNewFactType').disabled,true);
 const gatherText=node=>[node.textContent,...node.children.map(gatherText)].join(' ');
 assert(gatherText(elements.get('tbReviewCard')).includes('doc:page1:characters-4-22'),
   'Context fact review must show immutable citation in existing review screen');
 console.log('Taxonomy UX UI passed: legacy reviews plus approved-context citations and read-only fact evidence.');
})().catch(err=>{console.error(err);process.exitCode=1;});
