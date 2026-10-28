'use strict';
/* Node-only GB26 UI check: no browser, server, secrets or paid requests. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.resolve(__dirname,'../site/group-b.js'),'utf8');
const shell=fs.readFileSync(path.resolve(__dirname,'../site/index.html'),'utf8');
const server=fs.readFileSync(path.resolve(__dirname,'../app.py'),'utf8');
const projects=fs.readFileSync(path.resolve(__dirname,'../site/projects.js'),'utf8');
const PID='f'.repeat(32);
const RUN='00000000-0000-0000-0000-000000000001';

class Element {
  constructor(tag='div'){
    this.tagName=tag;this.children=[];this.handlers={};this.textContent='';
    this.value='';this.type='';this.className='';this.attributes={};
    this.disabled=false;this.hidden=false;this.parent=null;
  }
  append(...nodes){
    for(const n of nodes){n.parent=this;this.children.push(n);}
  }
  setAttribute(k,v){this.attributes[k]=v;}
  addEventListener(name,callback){this.handlers[name]=callback;}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  querySelector(q){
    if(q.startsWith('.')){
      const cls=q.slice(1);
      if(this.className.split(/\s+/).includes(cls))return this;
    }
    for(const child of this.children){
      const found=child.querySelector?.(q);
      if(found)return found;
    }
    return null;
  }
  remove(){
    if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);
  }
  flatten(){
    return [this,...this.children.flatMap(c=>c.flatten())];
  }
  click(){
    return this.handlers.click?.({preventDefault(){}});
  }
}

test('Group B assets explicitly mounted via existing local server and project detail',()=>{
  assert.match(shell,/src="\/group-b\.js"/);
  assert.match(shell,/href="\/group-b\.css"/);
  assert.match(server,/\x27\/group-b\.js\x27:\s*\x27group-b\.js\x27/);
  assert.match(server,/\x27\/group-b\.css\x27:\s*\x27group-b\.css\x27/);
  assert.match(projects,/ContentMachineGroupB\.mount\(root,project\.id\)/);
  assert.doesNotMatch(source,/\.innerHTML\s*=/);
  assert.doesNotMatch(source,/localStorage/);
});

test('Project-scoped UI shows all B1-B8 and never assumes qualified from raw completed',async()=>{
  const calls=[];
  const response=async uri=>{
    calls.push(uri);
    let payload={ok:true};
    if(uri.endsWith('/runs?offset=0&limit=10'))
      payload={ok:true,items:[{run_id:RUN,video_id:15,state:'partial',updated_at:'now'}],
               total:1};
    else if(uri.endsWith('/runs/'+RUN))
      payload={ok:true,run:{run_id:RUN,video_id:15,content_type:'video',
        state:'partial',result_analysis_id:null,
        media_manifest_sha256:null,a_snapshot_sha256:null},
        review_revision:0,correction_count:0,invalidated:false,
        blocks:Array.from({length:8},(_,i)=>({
          block:'B'+(i+1),status:'not_run',error_code:null}))};
    return {ok:true,status:200,json:async()=>payload};
  };
  const sandbox={
    window:{confirm:()=>false},
    document:{createElement:t=>new Element(t)},
    fetch:response,console,
  };
  vm.runInNewContext(source,sandbox);
  const root=new Element();
  sandbox.window.ContentMachineGroupB.mount(root,PID);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls[0],'/api/projects/'+PID+'/group-b/runs?offset=0&limit=10');
  const btn=root.flatten().find(x=>x.textContent.startsWith('#15'));
  assert.ok(btn);
  await btn.click();
  await new Promise(resolve=>setImmediate(resolve));
  const allText=root.flatten().map(x=>x.textContent).filter(Boolean).join(' ');
  for(let i=1;i<=8;i++)assert.match(allText,new RegExp('B'+i+' ·'));
  assert.match(allText,/квалификация: не проверена/);
  assert.match(allText,/Результат|История/iu);
  assert.equal(calls.length,2);
});

test('Untrusted project identifier cannot mount a cross-project API',()=>{
  const calls=[];
  const sandbox={window:{},document:{createElement:t=>new Element(t)},
    fetch:async p=>{calls.push(p);return {json:async()=>({ok:true,items:[]})}}};
  vm.runInNewContext(source,sandbox);
  const node=new Element();
  sandbox.window.ContentMachineGroupB.mount(node,'../another-project');
  assert.equal(node.children.length,0);
  assert.equal(calls.length,0);
});
