'use strict';
// Browser-less behavior test of the REAL browser controller, no remote model.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../site/index.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'../site/taxonomy-deep.js'),'utf8');
const map=new Map();
function dom(tag='div'){
 const events={};
 const node={
   tagName:tag.toUpperCase(),value:'',textContent:'',name:'',checked:false,
   hidden:false,disabled:false,className:'',children:[],dataset:{},open:false,scrollTop:0,
   style:{},options:[],
   classList:{active:new Set(),toggle(k,on){if(on)this.active.add(k);else this.active.delete(k);},
     remove(k){this.active.delete(k)},add(k){this.active.add(k)}},
   append(...kids){this.children.push(...kids);if(tag==='select')this.options.push(...kids);},
   replaceChildren(...kids){this.children=[...kids];},
   setAttribute(k,v){this[k]=v;},
   addEventListener(k,fn){(events[k] ||= []).push(fn);},
   emit(k){for(const f of events[k]||[])f({target:this,preventDefault(){}});},
   closest(){return dom('details')}
 };
 return node;
}
for(const match of html.matchAll(/id="([^"]+)"/g))map.set(match[1],dom());
for(const id of ['tbDeepEntry','tbDeepWorkspace','tbDeepCardHost','tbDeepTree'])
  map.get(id).hidden=true;
const callbacks={};
const document={
 getElementById(id){assert(map.has(id),'Missing element '+id);return map.get(id);},
 createElement:dom,
 addEventListener(event,fn){(callbacks[event] ||= []).push(fn);},
 dispatchEvent(event){for(const fn of callbacks[event.type]||[])fn(event);}
};
const F1='b9f3f226-5ce2-4ccc-b8b0-8c35f5f0d001';
const F2='b9f3f226-5ce2-4ccc-b8b0-8c35f5f0d002';
const facts=[{id:F1,kind:'function',text:'Программа массажа'},
 {id:F2,kind:'problem',text:'Трудно вести ежедневный уход'}];
function node(id,name,kind,parent,relation,ids){
 return {id,name,kind,parent_id:parent,relation,source_fact_ids:ids,
 rationale:'Есть смысл изучить такой контент',definition:'Какие видео исследовать',
 include:'Подходящие ролики',exclude:'Реклама лекарств',examples:['Утренний ритуал']};
}
const tree=[node('a','Массаж лица','direction',null,'direct',[F1]),
node('b','Потребность в ритуалах','direction',null,'problem_related',[F2]),
node('ab','Утренняя рутина','branch','a','direct',[F1]),
node('leaf','Проверка формата','hypothesis','ab','direct',[F1])];
const expanded=[...tree,node('c','Beauty lifestyle','direction',null,'adjacent',[])];
let state={mode:'standard',eligible:true}, paid=0;
const req=[];
const quality={coverage_matrix:facts.map(f=>({fact_id:f.id,fact:f.text,covered:true,node_ids:['a'],
 gap_severity:null})),covered_facts:2,total_facts:2,
 severe_gap_count:0,duplicate_count:0,new_direction_count:0,duplicates:[],passed:true};
async function fetch(url,options={}){
 const action=url.split('/').pop();const method=options.method||'GET';
 req.push({method,url});
 if(method==='POST'){
   if(action==='start')state={mode:'deep',status:'pass1_pending',facts,nodes:[],
     spent_usd:'0',reserved_usd:'0',attempts:[],quality:null};
   else if(action==='advance'){
     paid++;
     if(state.status==='pass1_pending')state={...state,status:'review1',nodes:tree};
     else if(state.status==='pass2_pending')state={...state,status:'expand_pending',
        nodes:expanded,quality:{...quality,new_direction_count:1,passed:false}};
     else if(state.status==='expand_pending')state={...state,status:'review2',
        nodes:expanded,quality};
     else throw Error('Wrong sequence');
   }else if(action==='review1')state={...state,status:'pass2_pending'};
   else if(action==='review2')state={...state,status:'approved',immutable:true};
 }
 return {ok:true,status:200,async json(){return {ok:true,...state}}};
}
const local=new Map(),localStorage={setItem(k,v){local.set(k,v)},
 getItem(k){return local.get(k)||null},removeItem(k){local.delete(k)}};
const window={addEventListener(){}};
const ctx=vm.createContext({document,window,fetch,localStorage,confirm:()=>true,
 JSON,Date,Math,Set,Map,Number,Intl,Array,Error,console});
vm.runInContext(script,ctx,{filename:'site/taxonomy-deep.js'});
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 document.dispatchEvent({type:'cm-taxonomy-run-show',detail:{
   projectId:'a'.repeat(32),runId:'b'.repeat(8)+'-'+ 'b'.repeat(4)+'-'+'b'.repeat(4)+'-'+'b'.repeat(4)+'-'+'b'.repeat(12)}});
 await tick();await tick();
 assert.equal(map.get('tbDeepEntry').hidden,false);
 assert.equal(paid,0,'GET must not dispatch model');
 map.get('tbDeepStart').emit('click');
 await tick();await tick();
 assert.equal(map.get('tbDeepWorkspace').hidden,false);
 assert.equal(state.status,'pass1_pending');
 assert.equal(paid,0);
 let advance=map.get('tbDeepActions').children.find(x=>x.tagName==='BUTTON');
 advance.emit('click');await tick();await tick();
 assert.equal(state.status,'review1');assert.equal(paid,1);
 assert.equal(map.get('tbDeepReviewCommit').hidden,true);
 for(let i=0;i<tree.length;i++)map.get('tbDeepApprove').emit('click');
 assert.equal(map.get('tbDeepReviewCommit').hidden,false);
 map.get('tbDeepReviewCommit').emit('click');await tick();await tick();
 assert.equal(state.status,'pass2_pending');assert.equal(paid,1);
 advance=map.get('tbDeepActions').children.find(x=>x.tagName==='BUTTON');
 advance.emit('click');await tick();await tick();
 assert.equal(state.status,'expand_pending');assert.equal(paid,2);
 advance=map.get('tbDeepActions').children.find(x=>x.tagName==='BUTTON');
 advance.emit('click');await tick();await tick();
 assert.equal(state.status,'review2');assert.equal(paid,3);
 assert.equal(map.get('tbDeepQuality').hidden,false);
 for(let i=0;i<expanded.length;i++)map.get('tbDeepApprove').emit('click');
 assert.equal(map.get('tbDeepReviewCommit').hidden,false);
 map.get('tbDeepReviewCommit').emit('click');await tick();await tick();
 assert.equal(state.status,'approved');
 assert.equal(map.get('tbDeepTree').hidden,false);
 assert.equal(map.get('tbDeepTree').children.length>=3,true);
 assert.equal(paid,3,'Only manually clicked 3 paid calls');
 const posts=req.filter(x=>x.method==='POST');
 assert.deepEqual(posts.map(x=>x.url.split('/').pop()),
 ['start','advance','review1','advance','advance','review2']);
 console.log('Deep taxonomy UI passed: 2 human card reviews, 3 manual paid stages, quality check and final visual tree.');
})().catch(e=>{console.error(e);process.exitCode=1;});
