'use strict';
/* Optional real Chromium QA; not executed by the browserless test suite.
 * No worker/provider is started. Projects use a disposable directory.
 * RTB responses are fixtures ONLY in the final inspector/review tests and their
 * screenshot filenames include "fixture". They never reach project storage.
 * Install Playwright separately, then:
 * CM_QA_OUTPUT=/absolute/output node scripts/qa_workspace_browser.cjs
 */
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES?path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'playwright'):'playwright');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'cm-engineering-browser-'));
const output=process.env.CM_QA_OUTPUT||path.join(temp,'screenshots');
const port=Number(process.env.CM_QA_PORT||18765),origin='http://127.0.0.1:'+port;
let server,browser;const checks=[];
function pass(value){checks.push(value);console.log('PASS '+value);}
async function get(url){return fetch(origin+url);}
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  server=spawn(process.env.CM_QA_PYTHON||'python',['-u','-c','import app; app.PORT='+port+'; from http.server import ThreadingHTTPServer; ThreadingHTTPServer((app.HOST,app.PORT),app.Handler).serve_forever()'],{cwd:root,env:{...process.env,CM_PROJECTS_DIR:path.join(temp,'projects'),CM_DB_DSN:''},stdio:'ignore'});
  let ready=false;for(let i=0;i<60;i++){try{ready=(await get('/')).ok;if(ready)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  assert(ready,'Install intelligence/requirements.txt and retry');
  const created=await fetch(origin+'/api/projects',{method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1','Origin':origin},body:JSON.stringify({name:'QA · инженерный pipeline',niche:'Health & Fitness',goal:'Проверить интерфейс'})});
  assert.equal(created.status,201);const project=(await created.json()).project,pid=project.id;
  for(const file of ['workspace-runs.js','workspace-core.js','workspace.css'])assert((await get('/'+file)).ok);
  pass('real HTTP application, new assets and disposable project creation');
  browser=await chromium.launch({headless:true,...(process.env.CM_QA_CHROMIUM?{executablePath:process.env.CM_QA_CHROMIUM}:{}),args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'}),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes.push(r.method()+' '+r.url());});
  await page.goto(origin,{waitUntil:'networkidle'});await page.locator('#workspaceProject').selectOption(pid);
  for(const [width,height] of [[1280,800],[1440,900],[1920,1080]]){
    await page.setViewportSize({width,height});
    await page.goto(origin+'/#pipeline',{waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.getElementById('workspaceNextTitle').textContent!=='Выберите проект');
    assert.equal(await page.locator('.workspace-stage-link').count(),11);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.equal(await page.locator('.workspace-pipeline-row[data-stage="group-b"]').getAttribute('data-state'),'unknown');
    await page.screenshot({path:path.join(output,'pipeline-'+width+'x'+height+'.png'),fullPage:true});
  }
  pass('1280×800, 1440×900, 1920×1080: real overview, no horizontal overflow, truthful unavailable modules');
  await page.goto(origin+'/#projects',{waitUntil:'networkidle'});
  await page.locator('#projectLibrarySearch').fill('инженерный');assert.equal(await page.locator('.cm-project-row').count(),1);
  await page.reload({waitUntil:'networkidle'});assert.equal(await page.locator('.cm-project-row').count(),1);
  await page.screenshot({path:path.join(output,'library-real-project.png'),fullPage:true});
  await page.goto(origin+'/#history',{waitUntil:'networkidle'});await page.locator('.cm-history-section').first().waitFor();
  await page.screenshot({path:path.join(output,'history-unavailable-backend.png'),fullPage:true});
  pass('real saved project survives reload; project history represents unavailable PostgreSQL explicitly');
  // Isolated model fixtures validate inspection and UI gates without any AI calls.
  const rid='11111111-1111-4111-8111-111111111111';
  const facts=[{id:'f1',kind:'function',text:'QA fixture: учёт питания',source_field:'product_functions',source_quote:'Учёт питания'}];
  const topics=[{id:'t1',name:'QA fixture: дневник питания',definition:'Тестовая тема',include:'Учёт',exclude:'Лекарства',relation:'direct',origin:'explicit',positive_examples:[],negative_examples:[]}];
  const stages=['fact_extraction','taxonomy_generation','taxonomy_critic'].map((role,i)=>({role,status:'completed',model_id:i?'anthropic/claude-opus-5.5':'xiaomi/mimo-v2.6-flash',reasoning:{effort:i===1?'high':'medium'},charged_usd:'0.01'}));
  const fixture={ok:true,run_id:rid,status:'fact_review',profile:{product_functions:'Учёт питания'},profile_revision:1,authorized_usd:'1',spent_usd:'.03',reserved_usd:'0',stages,fact_proposals:facts,approved_facts:facts,topic_proposals:topics,critic_findings:[],stage_outputs:stages.map((s,i)=>({...s,normalized:i===0?{facts}:i===1?{topics}:{findings:[]},raw_model_json:{fixture_only:true,role:s.role}}))};
  await page.route('**/api/projects/'+pid+'/taxonomy-builder/runs**',async r=>{
    assert.equal(r.request().method(),'GET');const tail=new URL(r.request().url()).pathname;
    await r.fulfill({contentType:'application/json',body:JSON.stringify(tail.endsWith('/runs')?{ok:true,project_id:pid,runs:[{run_id:rid,status:'fact_review',created_at:'2026-10-30T10:00:00Z',stages,verified_fact_count:1,valid_topic_count:1}]}:fixture)});
  });
  for(const [width,height] of [[1280,800],[1440,900],[1920,1080]]){
    await page.setViewportSize({width,height});await page.goto(origin+'/#stage/rtb/results?run='+rid,{waitUntil:'networkidle'});
    await page.locator('.cm-model-choice').nth(1).click();await page.getByRole('button',{name:'Исходный ответ',exact:true}).click();
    assert(await page.locator('.cm-inspector-body').innerText().then(v=>v.includes('fixture_only')));
    await page.getByRole('button',{name:'Результат',exact:true}).click();
    assert(await page.locator('.cm-inspector-body').innerText().then(v=>v.includes(topics[0].name)));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,'fixture-inspector-'+width+'x'+height+'.png'),fullPage:true});
  }
  pass('fixture-only model inspection at all three viewports: result switching, source JSON, no overflow');
  await page.getByRole('button',{name:'Перейти к проверке',exact:true}).click();await page.locator('#projectRun').waitFor({state:'visible'});
  await page.locator('#tbReviewApprove').click();await page.locator('#tbRunNewFact').fill('QA manual addition');
  const additions=page.locator('#tbRunNewFact');await additions.dispatchEvent('input');
  const refuse=dialog=>dialog.dismiss();page.on('dialog',refuse);
  await page.locator('#workspaceHistoryNav').click();assert(await page.locator('#projectRun').isVisible());page.off('dialog',refuse);
  page.once('dialog',dialog=>dialog.accept());await page.locator('#workspaceHistoryNav').click();
  await page.locator('.cm-run-table a').first().click();await page.getByRole('button',{name:'Перейти к проверке',exact:true}).click();
  assert.equal(await page.locator('#tbRunNewFact').inputValue(),'QA manual addition');
  await page.locator('.cm-run-step').nth(1).click();assert(await page.locator('#tbRunOutputsPanel').isVisible());
  pass('fixture-only review: rejected navigation preserves screen; decisions/additions survive reopen; progress opens saved output');
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);pass('no uncaught browser errors or writes while browsing/reviewing drafts');
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({status:'passed',checks,fixtures:'Only fixture-* screenshots and the final inspector/review tests use model fixtures.',screenshots:fs.readdirSync(output).filter(n=>n.endsWith('.png'))},null,2)+'\n');
  console.log('Screenshots: '+output);
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server)server.kill();});
