'use strict';
/* No DOM library: real UI logic is exercised with a minimal event/element harness.
   No provider calls, and no browser installation required in GitHub Actions. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
const code=fs.readFileSync(path.join(root,'site/taxonomy-studio.js'),'utf8');
function tick(){return new Promise(resolve=>setImmediate(resolve));}
function element(tag='div'){
  const listeners={};
  const node={
    tagName:tag,hidden:false,disabled:false,value:'',checked:false,textContent:'',
    className:'',children:[],style:{},options:[],attributes:{},
    append(...children){
      this.children.push(...children);
      if(this.tagName==='select')this.options.push(...children.filter(x=>x.tagName==='option'));
    },
    replaceChildren(...children){this.children=children;this.options=this.tagName==='select'?children:[];},
    addEventListener(name,handler){(listeners[name] ||= []).push(handler);},
    emit(name,changes={}){Object.assign(this,changes);for(const handler of listeners[name]||[])handler({target:this});},
    setAttribute(key,value){this.attributes[key]=value;},
    getAttribute(key){return this.attributes[key];}
  };
  return node;
}
const ids=[...html.matchAll(/id="([^"]+)"/g)].map(x=>x[1]);
const nodes=new Map(ids.map(id=>[id,element(
  /<(select)[^>]*id="([^"]+)"/.test('')?'select':'div'
)]));
for(const id of ['tbModelSelect','tbReasoningMode','tbReasoningEffort'])
  nodes.set(id,element('select'));
const docListeners={};
const document={
  getElementById(id){assert(nodes.has(id),'Missing HTML element '+id);return nodes.get(id);},
  createElement:element,
  addEventListener(name,handler){(docListeners[name] ||= []).push(handler);},
  dispatchEvent(event){for(const fn of docListeners[event.type]||[])fn(event);}
};
const a={
  model_id:'openai/gpt-6.1-sol',display_name:'OpenAI GPT-6.1 Sol',status:'usable',
  selectable:true,catalog_version_id:11,
  capabilities:{text_output:true,supports_structured_outputs:true,
    supports_temperature:false,reasoning_efforts:['low','medium','high'],
    max_output_tokens:8000}
};
const b={
  model_id:'anthropic/claude-sonnet-5.5',display_name:'Claude Sonnet 5.5',status:'usable',
  selectable:true,catalog_version_id:22,
  capabilities:{text_output:true,supports_structured_outputs:true,
    supports_temperature:true,reasoning_efforts:['low','medium','high'],
    max_output_tokens:16000}
};
const missing={
  model_id:'qwen/qwen3.8-27b',display_name:'Qwen',status:'unverified',
  selectable:false,catalog_version_id:null,capabilities:null
};
let draft=null,draftRevision=0,approval=null,requests=[];
function response(body,status=200){
  return {ok:status<400,status,async json(){return body;}};
}
async function fetch(url,options){
  const method=options?.method||'GET';
  let data=method==='POST'?JSON.parse(options.body):null;
  requests.push({url,method,data});
  if(url.endsWith('/models')&&method==='GET')return response({ok:true,models:[a,b,missing]});
  if(url.endsWith('/pipeline')&&method==='GET'){
    return response({ok:true,profile_revision:1,profile_hash:'f'.repeat(64),
      draft:draft?{draft_revision:draftRevision,config:draft}:null,
      approved_versions:approval?[approval]:[],
      status:approval&&approval.source_draft_revision===draftRevision?
        'pipeline_approved':draft?'pipeline_review':'pipeline_setup'});
  }
  if(url.endsWith('/pipeline/draft')&&method==='POST'){
    if(data.expected_draft_revision!==draftRevision)return response({ok:false,error:'Draft changed'},409);
    draft=data.config;draftRevision++;
    return response({ok:true,status:'pipeline_review',draft_revision:draftRevision,config:draft});
  }
  if(url.endsWith('/pipeline/approve')&&method==='POST'){
    if(!draft||data.expected_draft_revision!==draftRevision)
      return response({ok:false,error:'Wrong draft'},409);
    approval={pipeline_version:1,config:JSON.parse(JSON.stringify(draft)),
      source_draft_revision:draftRevision,
      approved_at:new Date().toISOString(),config_sha256:'a'.repeat(64)};
    return response({ok:true,status:'approved',pipeline_version:1,
      pipeline_id:'example',next_step:'cost_estimate'});
  }
  if(url.endsWith('/models/refresh')&&method==='POST')
    return response({ok:true,verified:2,total:12});
  throw Error('Unexpected route '+method+' '+url);
}
const window={scrollTo(){},ContentMachineTaxonomyStudio:null};
const context=vm.createContext({document,window,fetch,confirm:()=>true,
  console,Number,Array,JSON,Date,Set,String,Math,Error});
vm.runInContext(code,context,{filename:'site/taxonomy-studio.js'});

(async()=>{
  for(const name of ['tbStudioLayout','tbModelSelect','tbSaveDraft','tbApprove',
    'tbRefreshCatalog','tbStageList','tbHistoryList','tbReasoningMode','tbTemperatureEnable'])
    assert(nodes.has(name),name+' should exist in HTML');
  document.dispatchEvent({type:'cm-taxonomy-pipeline-open',detail:{projectId:'c'.repeat(32)}});
  await tick();await tick();
  assert.equal(nodes.get('tbStudioLayout').hidden,false);
  assert.equal(nodes.get('tbSaveDraft').disabled,true,'empty draft is not saveable');
  assert.equal(nodes.get('tbApprove').disabled,true);
  nodes.get('tbModelSelect').emit('change',{value:a.model_id});
  assert.equal(nodes.get('tbTemperatureEnable').disabled,true,'model without temperature must block it');
  assert.equal(nodes.get('tbSaveDraft').disabled,true,'second stage missing');
  let rail=nodes.get('tbStageList').children;
  rail[2].emit('click');  // fact, human checkpoint, taxonomy
  nodes.get('tbModelSelect').emit('change',{value:b.model_id});
  assert.equal(nodes.get('tbSaveDraft').disabled,false);
  nodes.get('tbTemperatureEnable').emit('change',{checked:true});
  assert.equal(nodes.get('tbTemperature').disabled,false);
  nodes.get('tbTemperature').emit('change',{value:'0.5'});
  assert.equal(nodes.get('tbSaveDraft').disabled,false);
  assert.equal(nodes.get('tbApprove').disabled,true,'cannot approve unsaved modifications');
  nodes.get('tbSaveDraft').emit('click');
  await tick();await tick();
  assert.equal(draftRevision,1);
  assert.equal(draft.stages[0].model_id,a.model_id);
  assert.equal(draft.stages[1].temperature,0.5);
  assert.equal(nodes.get('tbApprove').disabled,false);
  nodes.get('tbApprove').emit('click');
  await tick();await tick();
  assert.equal(approval.config.stages[1].temperature,0.5);
  assert.equal(nodes.get('tbApprovedNext').hidden,false);
  assert.equal(nodes.get('tbApprove').disabled,true,'approved settings not auto-sent to provider');
  assert(!requests.some(x=>/completion|\/runs|\/quotes|\/consents/.test(x.url)),
    'Block 4 must not call paid APIs');
  assert.equal(nodes.get('tbHistoryList').children.length,1);
  nodes.get('tbHistoryList').children[0].emit('click');
  assert.equal(nodes.get('tbApprove').disabled,true,'history copy must be saved first');
  assert.equal(window.ContentMachineTaxonomyStudio.confirmLeave(),true);
  console.log('Taxonomy Studio interaction test passed: models, temperature, save, approve, history, budget gate.');
})().catch(err=>{console.error(err);process.exitCode=1;});
