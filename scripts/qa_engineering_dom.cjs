'use strict';
/* Browserless integration QA, not visual/browser QA. All AI data below are test
 * fixtures; they are never part of the application, project storage or screenshots.
 * npm install --prefix /tmp/cm-ux-qa --no-audit --no-fund jsdom@26
 * NODE_PATH=/tmp/cm-ux-qa/node_modules node scripts/qa_engineering_dom.cjs
 */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..');
const PID='a'.repeat(32),OTHER='b'.repeat(32),RID='11111111-1111-4111-8111-111111111111';
const facts=[{id:'fact-one',kind:'function',text:'Тест: учёт питания',source_field:'product_functions',source_quote:'Учёт питания'}];
const topics=[{id:'topic-one',name:'Тест: привычки питания',relation:'direct',origin:'explicit',definition:'Определение тестовой темы',include:'Дневник питания',exclude:'Лекарства',positive_examples:[],negative_examples:[]}];
const findings=[{topic_name:topics[0].name,issue:'Проверить формулировку',suggestion:'Уточнить критерий',severity:'medium'}];
const models=['xiaomi/mimo-v2.6-flash','anthropic/claude-opus-5.5','anthropic/claude-opus-5.5'];
const roles=['fact_extraction','taxonomy_generation','taxonomy_critic'];
const stages=roles.map((role,i)=>({role,model_id:models[i],status:'completed',reasoning:{effort:i===1?'high':'medium'},usage:{prompt_tokens:120,completion_tokens:80},charged_usd:'0.01'}));
function run(status='fact_review'){
  return {ok:true,run_id:RID,status,profile:{name:'QA project',product_functions:'Учёт питания'},profile_revision:2,authorized_usd:'1',spent_usd:'.03',reserved_usd:'0',generation_reviewed:false,
    stages,stage_outputs:stages.map((s,i)=>({...s,normalized:[{facts},{topics},{findings}][i],raw_model_json:{fixture_only:true,...[{facts},{topics},{findings}][i]}})),fact_proposals:facts,approved_facts:facts,topic_proposals:topics,critic_findings:findings};
}
const projects=[{id:PID,name:'QA project',niche:'Health & Fitness',goal:'Test',files:[],profile_revision:2,created_at:'2026-10-30T10:00:00Z'},{id:OTHER,name:'Second project',niche:'Design',files:[],profile_revision:1,created_at:'2026-10-29T10:00:00Z'}];
const flush=async()=>{for(let i=0;i<4;i++)await new Promise(r=>setTimeout(r,0));};
const passed=[];
function pass(name){passed.push(name);console.log('PASS '+name);}
async function boot(hash='#pipeline',storage={}){
  const errors=[],calls=[],downloads=[];
  const vc=new VirtualConsole();vc.on('jsdomError',e=>{if(!e.message.includes('navigation'))errors.push(e.message);});
  const dom=new JSDOM(fs.readFileSync(path.join(root,'site/index.html'),'utf8'),{url:'http://localhost:8765/'+hash,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  const w=dom.window;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.matchMedia=()=>({matches:false,addEventListener(){}});w.setInterval=()=>0;
  w.confirm=()=>true;w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};
  const anchorClick=w.HTMLAnchorElement.prototype.click;
  w.HTMLAnchorElement.prototype.click=function(){if(this.download)downloads.push(this.download);else anchorClick.call(this);};
  let clipboard='';Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async text=>{clipboard=text;}}});
  for(const [key,value] of Object.entries({'cm.workspace.project':PID,...storage}))w.localStorage.setItem(key,value);
  let status='fact_review',foreign=false,failHistory=false,delayed=null;
  w.fetch=async(url,options={})=>{
    calls.push({url,method:options.method||'GET'});
    let data={ok:false,error:'Unavailable in QA'},code=503;
    if(url==='/api/projects'){data={ok:true,projects};code=200;}
    else if(url==='/api/state'){data={platforms:{},configuration:{openrouter_configured:false},jobs:[]};code=200;}
    else if(/^\/api\/projects\/[ab]{32}$/.test(url)){data={ok:true,project:projects.find(p=>url.includes(p.id))};code=200;}
    else if(url.endsWith('/taxonomy-builder/runs')){
      if(delayed)await delayed;
      if(!failHistory){data={ok:true,project_id:foreign?OTHER:url.split('/')[3],runs:url.includes(OTHER)?[]:[{run_id:RID,status,created_at:'2026-10-30T12:00:00Z',verified_fact_count:1,valid_topic_count:1,stages}]};code=200;}
    }else if(url.endsWith('/taxonomy-builder/runs/'+RID)){data=run(status);code=200;}
    else if(url.includes('qualified-data/runs')){data={ok:true,runs:[]};code=200;}
    else if(url.includes('collector/runs?')){data={ok:true,runs:[]};code=200;}
    return {ok:code<400,status:code,json:async()=>data};
  };
  for(const file of ['app.js','projects.js','taxonomy-run.js','taxonomy-deep-v2.js','workspace-core.js','workspace-analysis.js','workspace-runs.js','workspace.js'])w.eval(fs.readFileSync(path.join(root,'site',file),'utf8'));
  await flush();
  return {w,d:w.document,dom,calls,errors,downloads,get clipboard(){return clipboard;},setStatus:v=>status=v,setForeign:v=>foreign=v,setFail:v=>failHistory=v,setDelay:v=>delayed=v};
}
async function go(app,hash){app.w.location.hash=hash;await flush();}
function clickText(root,selector,text){const item=[...root.querySelectorAll(selector)].find(n=>n.textContent===text);assert(item,'Missing control '+text);item.click();return item;}
(async()=>{
  const historyBoot=await boot('#history');
  assert.equal(historyBoot.d.getElementById('workspaceHistoryProject').textContent,'QA project');
  assert.equal(historyBoot.d.querySelectorAll('.cm-history-section').length,3);historyBoot.dom.window.close();
  const app=await boot();const {w,d}=app;
  assert.equal(d.getElementById('workspaceTitle').textContent,'QA project');
  assert.equal(d.querySelectorAll('.workspace-stage-link').length,11);
  assert.equal(d.querySelector('.workspace-pipeline-row[data-stage=rtb]').dataset.state,'review');
  assert.equal(d.querySelector('.workspace-pipeline-row[data-stage=group-a]').dataset.state,'unknown');
  assert(!app.calls.some(c=>c.method!=='GET'));pass('project-scoped overview, all 11 modules, server-derived states, no automatic writes');
  await go(app,'#stage/rtb/results?run='+RID);
  assert.equal(d.querySelector('.cm-pane-title').textContent,'Извлечение фактов');
  assert(d.querySelector('.cm-inspector-body').textContent.includes(facts[0].text));
  d.querySelectorAll('.cm-model-choice')[1].click();
  assert(d.querySelector('.cm-inspector-body').textContent.includes(topics[0].name));
  clickText(d,'.cm-inspector-tabs button','Исходный ответ');
  assert(d.querySelector('.cm-code').textContent.includes('fixture_only'));
  d.querySelectorAll('.cm-model-choice')[2].click();
  clickText(d,'.cm-inspector-tabs button','Результат');
  assert(d.querySelector('.cm-inspector-body').textContent.includes(findings[0].issue));
  clickText(d,'.cm-inspector-tabs button','Вход');
  assert(d.querySelector('.cm-inspector-body').textContent.includes(facts[0].text));
  clickText(d,'.cm-artifact-tools button','Копировать JSON');await flush();assert(app.clipboard.includes('Учёт питания'));
  clickText(d,'.cm-artifact-tools button','Скачать JSON');assert.equal(app.downloads.length,1);
  assert(!app.calls.some(c=>c.method!=='GET'));pass('all three model outputs, original JSON, saved inputs, clipboard and local export without paid calls');
  app.setStatus('failed');await go(app,'#pipeline');await go(app,'#stage/rtb/results?run='+RID);
  assert(d.querySelector('.cm-inspector-heading .cm-state').classList.contains('cm-state-error'));
  d.querySelectorAll('.cm-model-choice')[1].click();assert(d.querySelector('.cm-inspector-body').textContent.includes(topics[0].name));
  pass('a later failed stage preserves access to every earlier saved model output');
  await go(app,'#history');
  assert.equal(d.querySelectorAll('.cm-history-section').length,3);
  const select=d.querySelector('.cm-filter select');select.value='rtb';select.dispatchEvent(new w.Event('change'));
  assert.equal(d.querySelectorAll('.cm-history-section').length,1);
  app.setFail(true);clickText(d,'.cm-list-toolbar button','Обновить');await flush();
  assert(d.querySelector('.cm-history').textContent.includes('История недоступна'));
  app.setFail(false);pass('project history, module filtering and partial backend failures are explicit');
  await go(app,'#projects');
  assert.equal(d.querySelectorAll('.cm-project-row').length,2);
  const search=d.getElementById('projectLibrarySearch');search.value='Second';search.dispatchEvent(new w.Event('input'));
  assert.equal(d.querySelectorAll('.cm-project-row').length,1);
  d.querySelector('.cm-project-row .workspace-button').click();await flush();
  assert.equal(w.localStorage.getItem('cm.workspace.project'),OTHER);assert.equal(d.getElementById('workspaceTitle').textContent,'Second project');
  pass('library search and project selection preserve exact project identity');
  // Restore original project, open real controller and preserve unsubmitted votes.
  w.ContentMachineWorkspace.openProject(PID);await flush();app.setStatus('fact_review');
  await w.ContentMachineProjects.run(PID,RID);await flush();
  assert.equal(d.getElementById('projectRun').hidden,false);
  assert.equal(w.location.hash,'#stage/rtb/work');
  assert.equal(d.getElementById('workspaceLocation').textContent,'Research Taxonomy');
  d.getElementById('tbReviewApprove').click();
  const input=d.getElementById('tbRunNewFact');input.value='Manually added fixture';input.dispatchEvent(new w.Event('input'));
  w.confirm=()=>false;await go(app,'#history');
  assert.equal(d.getElementById('projectRun').hidden,false);
  assert.notEqual(w.location.hash,'#history');
  w.confirm=()=>true;await go(app,'#history');
  await w.ContentMachineProjects.run(PID,RID);await flush();
  assert.equal(d.getElementById('tbRunNewFact').value,'Manually added fixture');
  assert.equal(d.querySelector('#tbRunFactRows .tb-run-keep').checked,true);
  const writesBefore=app.calls.filter(c=>c.method!=='GET').length;
  d.querySelectorAll('.cm-run-step')[1].click();assert.equal(d.getElementById('tbRunOutputsPanel').hidden,false);
  assert(d.getElementById('tbRunOutputsPanel').textContent.includes(topics[0].name));
  assert.equal(app.calls.filter(c=>c.method!=='GET').length,writesBefore);
  pass('review refusal preserves route and project; decisions and manual additions survive reopen; progress opens model results');
  await go(app,'#projects');d.getElementById('projectOverviewCreate').click();await flush();
  assert.equal(w.localStorage.getItem('cm.workspace.project'),'','New draft must not retain another saved project as its context');
  const name=d.getElementById('project-input-name');name.value='Unfinished QA draft';name.dispatchEvent(new w.Event('input'));
  await go(app,'#pipeline');await go(app,'#projects');
  assert.equal(d.getElementById('projectDraftHint').hidden,false);d.getElementById('projectDraftResume').click();
  assert.equal(d.getElementById('project-input-name').value,'Unfinished QA draft');
  const saved={};for(let i=0;i<w.localStorage.length;i++){const key=w.localStorage.key(i);saved[key]=w.localStorage.getItem(key);}
  const reopened=await boot('#projects',saved);reopened.d.getElementById('projectDraftResume').click();
  assert.equal(reopened.d.getElementById('project-input-name').value,'Unfinished QA draft');reopened.dom.window.close();
  pass('questionnaire text survives navigation and a fresh document session');
  await go(app,'#pipeline');app.setForeign(true);await w.ContentMachineWorkspaceRuns.load(PID).then(data=>assert(data.find(s=>s.id==='rtb').error));app.setForeign(false);
  let release;const delay=new Promise(r=>release=r);app.setDelay(delay);
  d.getElementById('workspaceRefresh').click();w.ContentMachineWorkspace.openProject(OTHER);await flush();release();await flush();
  assert.equal(d.getElementById('workspaceTitle').textContent,'Second project');
  assert.equal(d.querySelector('.workspace-pipeline-row[data-stage=rtb] .cm-state').textContent,'Не запускался');
  pass('foreign-project responses and late results cannot leak into the newly selected project');
  assert.deepEqual(app.errors,[]);assert.equal(app.calls.filter(c=>c.method!=='GET').length,0);
  app.dom.window.close();pass('no uncaught DOM errors and no POST/PATCH/DELETE during the tested journey');
  console.log(JSON.stringify({status:'passed',type:'browserless DOM integration',groups:passed.length,checks:passed},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
