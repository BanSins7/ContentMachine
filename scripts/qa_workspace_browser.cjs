'use strict';
/* Optional browser QA. Install Playwright separately; no runtime dependency is added.
 * node scripts/qa_workspace_browser.cjs
 * CM_QA_CHROMIUM may select a locally installed browser binary.
 * Fixtures exist only in this test. No worker or provider is started.
 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES
  ? path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'playwright'):'playwright');
const {stages}=require('../site/workspace-core.js');
const root=path.resolve(__dirname,'..');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'cm-workspace-qa-'));
const output=process.env.CM_QA_OUTPUT||path.join(temp,'screenshots');
const port=Number(process.env.CM_QA_PORT||18765);
const origin='http://127.0.0.1:'+port;
const pid='a'.repeat(32),other='b'.repeat(32);
const fixtures=[pid,other].map((id,i)=>({id,name:i?'QA second project':'QA workspace',niche:'QA',goal:'QA',files:[],file_count:0,profile_revision:1,created_at:'2026-10-29T10:00:00Z'}));
let server,browser;
const checks=[];
function passed(message){checks.push(message);console.log('PASS '+message);}
function get(route,headers={}){
  return new Promise((resolve,reject)=>{
    http.get(origin+route,{headers},r=>{let body='';r.on('data',s=>body+=s);r.on('end',()=>resolve({status:r.statusCode,headers:r.headers,body}));}).on('error',reject);
  });
}
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  server=spawn(process.env.CM_QA_PYTHON||'python',['-u','-c',
    'import app; app.PORT='+port+'; from http.server import ThreadingHTTPServer; ThreadingHTTPServer((app.HOST,app.PORT),app.Handler).serve_forever()'],
    {cwd:root,env:{...process.env,CM_PROJECTS_DIR:path.join(temp,'projects')},stdio:'ignore'});
  let ready=false;
  for(let n=0;n<60;n++){
    try{ready=(await get('/')).status===200;if(ready)break;}catch{}
    await new Promise(r=>setTimeout(r,100));
  }
  assert(ready,'Local UI server did not start; install intelligence/requirements.txt');
  for(const file of ['workspace.css','workspace-core.js','workspace-analysis.js','workspace.js','workspace-sre.js']){
    const result=await get('/'+file);assert.equal(result.status,200);assert.match(result.headers['content-type'],file.endsWith('.css')?/text\/css/:/text\/javascript/);
  }
  assert.equal((await get('/workspace.js',{Host:'external.example'})).status,403);
  assert.equal((await get('/unlisted-workspace.js')).status,404);
  passed('real host serves only the allowed assets with the correct MIME and localhost guard');
  browser=await chromium.launch({headless:true,
    ...(process.env.CM_QA_CHROMIUM?{executablePath:process.env.CM_QA_CHROMIUM}:{}),
    args:['--no-sandbox','--disable-dev-shm-usage','--no-zygote','--disable-gpu']});
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const errors=[],mutations=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()!=='GET')mutations.push(r.method()+' '+r.url());});
  await page.goto(origin,{waitUntil:'networkidle'});
  assert(await page.locator('#workspaceView').isVisible());
  assert.equal(await page.locator('.workspace-stage-link').count(),11);
  assert.equal(await page.locator('#workspaceProjectCount').innerText(),'00');
  await page.screenshot({path:path.join(output,'workspace-desktop.png')});
  for(const stage of stages){
    await page.locator('.workspace-stage-link[data-stage="'+stage.id+'"]').click();
    await page.waitForFunction(title=>document.querySelector('#workspaceStage h1')?.textContent===title,stage.title);
    assert.equal(await page.locator('#workspaceStage h1').innerText(),stage.title);
    assert.equal(await page.locator('.workspace-flow-card').count(),3);
    assert.equal(await page.locator('.workspace-stage-link[aria-current="step"]').count(),1);
    await page.locator('.workspace-stage-tabs a').nth(2).click();
    await page.getByRole('heading',{name:'Начни с проекта',exact:true}).waitFor({state:'visible'});
  }
  assert.deepEqual(mutations,[]);
  passed('all 11 stage overviews/results open; missing context is explicit; browsing causes no writes');
  await page.goto(origin+'/#stage/search',{waitUntil:'networkidle'});
  await page.screenshot({path:path.join(output,'workspace-stage.png')});
  await page.route('**/api/**',async r=>{
    const url=new URL(r.request().url());let data={ok:false,error:'QA unavailable'},status=503;
    if(url.pathname==='/api/projects'){data={ok:true,projects:fixtures};status=200;}
    else if(/^\/api\/projects\/[ab]{32}$/.test(url.pathname)){data={ok:true,project:fixtures.find(p=>p.id===url.pathname.split('/')[3])};status=200;}
    else if(url.pathname==='/api/state'){data={platforms:{},configuration:{openrouter_configured:false},jobs:[]};status=200;}
    else if(url.pathname.endsWith('/qualified-data/runs')){data={ok:true,runs:[]};status=200;}
    await r.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.reload({waitUntil:'networkidle'});
  await page.locator('#workspaceProject').selectOption(pid);
  await page.locator('.workspace-stage-tabs a').nth(1).click();
  await page.getByRole('button',{name:'Открыть Search Planner ↗',exact:true}).click();
  await page.locator('#spView').waitFor({state:'visible'});
  assert.equal(await page.locator('#workspaceProject').inputValue(),pid);
  assert.equal(await page.locator('#workspaceLocation').innerText(),'Search Planner');
  await page.screenshot({path:path.join(output,'workspace-search.png')});
  passed('Search Planner opens the selected project in the existing guarded screen');
  await page.locator('.workspace-stage-link[data-stage="qdp"]').click();
  await page.locator('.workspace-stage-tabs a').nth(1).click();
  await page.getByRole('button',{name:'Открыть Qualified Data ↗',exact:true}).click();
  await page.waitForFunction(id=>document.getElementById('qdpProject').value===id,pid);
  assert(await page.locator('#qdpView').isVisible());
  await page.locator('#workspaceContext a').last().focus();
  await page.evaluate(()=>window.ContentMachineApp.openView('qdp'));
  assert(await page.evaluate(()=>document.activeElement===[...document.querySelectorAll('#workspaceContext a')].at(-1)),'Polling must preserve focused pipeline navigation');
  await page.screenshot({path:path.join(output,'workspace-qdp.png')});
  await page.locator('#workspaceProject').selectOption(other);
  assert(await page.locator('#workspaceView').isVisible());
  await page.locator('.workspace-stage-tabs a').nth(1).click();
  await page.getByRole('button',{name:'Открыть Qualified Data ↗',exact:true}).click();
  await page.waitForFunction(id=>document.getElementById('qdpProject').value===id,other);
  passed('QDP follows project changes; another project does not reuse the previous selection');
  await page.locator('.workspace-stage-link[data-stage="collector"]').click();
  await page.locator('.workspace-stage-tabs a').nth(1).click();
  await page.getByRole('button',{name:'Открыть Video Collector ↗',exact:true}).click();
  await page.locator('#discoveryView').waitFor({state:'visible'});
  assert(await page.locator('#discoveryView').isVisible());
  assert.equal(await page.locator('#collectorV1Project').inputValue(),other);
  passed('Collector opens with the exact selected project and does not release jobs');
  await page.locator('.workspace-stage-link[data-stage="rtb"]').click();
  await page.locator('.workspace-stage-tabs a').nth(1).click();
  await page.getByRole('button',{name:'Открыть карту исследования ↗',exact:true}).click();
  await page.locator('#projectDetails').waitFor({state:'visible'});
  assert(await page.locator('#projectDetails').isVisible());
  assert(await page.locator('#tbV2Panel').getAttribute('open')!==null);
  passed('RTB opens the existing project and its real Deep V2 panel');
  // Exercise an existing dirty-editor leave refusal through the new navigation.
  await page.evaluate(()=>{
    document.getElementById('projectPipeline').hidden=false;
    window.ContentMachineTaxonomyStudio={confirmLeave:()=>false};
  });
  await page.locator('.workspace-stage-link[data-stage="insights"]').click();
  await page.waitForFunction(()=>location.hash==='#stage/rtb/work');
  assert(await page.locator('#projectsView').isVisible());
  assert.equal(new URL(page.url()).hash,'#stage/rtb/work');
  await page.locator('#workspaceProject').selectOption(pid);
  assert.equal(await page.locator('#workspaceProject').inputValue(),other);
  await page.evaluate(()=>{document.getElementById('projectPipeline').hidden=true;});
  passed('refusing a dirty-editor transition preserves the screen, route, and project');
  await page.locator('.workspace-stage-link[data-stage="insights"]').click();
  await page.locator('.workspace-stage-tabs a').nth(1).click();
  await page.getByRole('heading',{name:'От наблюдений к выводам',exact:true}).waitFor({state:'visible'});
  assert.equal(await page.locator('.workspace-signal-card').count(),4);
  await page.getByRole('button',{name:'Загрузить результаты ↻',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.workspace-analysis-status')?.textContent.startsWith('Результаты пока недоступны'));
  assert.deepEqual(await page.locator('.workspace-signal-card>strong').allTextContents(),['—','—','—','—']);
  await page.screenshot({path:path.join(output,'workspace-insights.png')});
  assert.deepEqual(mutations,[]);
  passed('analysis workspaces render their full design while unavailable results remain empty');
  await page.goto(origin+'/sre-studio.html?project='+other,{waitUntil:'networkidle'});
  assert.equal(await page.locator('#sreProject').inputValue(),other);
  assert.equal(await page.locator('#srePipelineNav a').count(),11);
  assert.equal(await page.locator('#sreCreateRun').isDisabled(),true);
  await page.screenshot({path:path.join(output,'workspace-sre.png')});
  passed('SRE shares the theme and project context while retaining its disabled write controls');
  await page.setViewportSize({width:390,height:844});
  await page.goto(origin+'/#pipeline',{waitUntil:'networkidle'});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert(await page.locator('#workspaceSidebar').evaluate(el=>el.inert));
  await page.screenshot({path:path.join(output,'workspace-mobile.png'),fullPage:true});
  await page.locator('#workspaceMenu').click();
  assert.equal(await page.locator('#workspaceMenu').getAttribute('aria-expanded'),'true');
  await page.locator('.workspace-stage-link[data-stage="group-c"]').click();
  await page.waitForFunction(()=>document.getElementById('workspaceMenu').getAttribute('aria-expanded')==='false');
  assert.equal(await page.locator('#workspaceMenu').getAttribute('aria-expanded'),'false');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.locator('.workspace-stage-tabs a').nth(1).click();
  await page.getByRole('heading',{name:'Результат в контексте',exact:true}).waitFor({state:'visible'});
  assert.equal(await page.locator('.workspace-signal-card').count(),5);
  await page.locator('#workspaceMenu').click();await page.keyboard.press('Escape');
  assert.equal(await page.locator('#workspaceMenu').getAttribute('aria-expanded'),'false');
  passed('390px mobile layout has no overflow; menu, focus, dismissal and stage navigation work');
  assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
  passed('no uncaught browser errors and no POST/PATCH/DELETE during the complete journey');
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({status:'passed',checks,uncaught_errors:errors,mutations,screenshots:fs.readdirSync(output).filter(p=>p.endsWith('.png'))},null,2)+'\n');
  console.log('Screenshots: '+output);
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();if(server)server.kill();
  // Caller-owned screenshot output is retained; all project records are test-only.
  if(process.env.CM_QA_OUTPUT)fs.rmSync(temp,{recursive:true,force:true});
});
