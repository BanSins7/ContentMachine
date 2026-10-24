'use strict';
/* Stage13 activity read-only UI: registered/queued/partial/ready after refresh. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../site/project-activity.js'),'utf8');
function element(tag='div'){
 const listeners={};
 return {tagName:tag.toUpperCase(),textContent:'',children:[],className:'',hidden:false,disabled:false,
  append(...a){this.children.push(...a);},replaceChildren(...a){this.children=a;},
  setAttribute(k,v){this[k]=v;},addEventListener(k,fn){(listeners[k]??=[]).push(fn);},
  emit(k){for(const cb of listeners[k]||[])cb({target:this})}};
}
let requestCount=0;const urls=[];
const sid='33333333-3333-4333-8333-333333333333';
const row=()=>({source_id:sid,source_type:'user_video_reference',label:'Fixture MP4',
  source_status:'registered',active_source_version_id:null,evidence_count:0,
  display_state:requestCount===1?'queued':'partial',jobs:[{job_id:'x',
    stage:'video_download',state:requestCount===1?'queued':'partial',
    attempts:1,max_attempts:3,error_code:null,error_message:null,charge_state:'not_applicable',
    cost_actual_usd:null,created_at:'2026-10-24T12:00:00Z'}]});
async function fetch(url,options={}){
 urls.push({url,method:options.method||'GET'});requestCount++;
 return {ok:true,async json(){return {ok:true,items:[row()],count:1,offset:0,limit:30,more:false,read_only:true}}};
}
const window={},document={createElement:element};
vm.runInNewContext(script,{window,document,fetch,console,Error});
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const parent=element();
 window.ContentMachineProjectActivity.mount(parent,'a'.repeat(32));
 for(let i=0;i<4;i++)await tick();
 const section=parent.children[0];
 assert.equal(section.children[1].textContent.includes('Зарегистрированный URL'),true);
 const notice=section.children[3],list=section.children[4];
 assert.equal(list.children.length,1);
 assert(list.children[0].children[0].textContent.includes('В очереди'), 'queued must not show ready');
 const refresh=section.children[2].children[0];refresh.emit('click');
 for(let i=0;i<4;i++)await tick();
 assert(list.children[0].children[0].textContent.includes('Частичный'), 'partial must not show ready');
 assert.equal(notice.textContent.includes('Источников: 1'),true);
 assert(urls.every(u=>u.method==='GET'&&u.url.includes('/source-activity')),
  'Activity view never starts any worker or paid stage');
 const restored=element();window.ContentMachineProjectActivity.mount(restored,'a'.repeat(32));
 for(let i=0;i<4;i++)await tick();
 assert.equal(restored.children[0].children[4].children.length,1,
   'Re-opening the project must load persisted server state');
 console.log('Stage13 activity UI PASS: persisted history, queued/partial visibility, refresh and no mutating calls.');
})().catch(e=>{console.error(e);process.exitCode=1;});
