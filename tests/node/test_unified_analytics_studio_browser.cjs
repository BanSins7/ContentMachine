/* UA27 optional REAL Chromium rendering + screenshot smoke.
 * Requires npm package playwright and its browser installed locally.
 * Run: node --test tests/node/test_unified_analytics_studio_browser.cjs
 * Skips cleanly when optional playwright is not installed.
 */
'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
let playwright;
try{playwright=require('playwright');}catch(_){playwright=null;}
const VERSION='uae-local-http/v1',PROJECT='a'.repeat(32),TOKEN='b'.repeat(64);
test('Chromium: project Studio visible, empty/partial/blocked states and screenshot',
  {skip:!playwright?'optional Playwright not installed':false},async()=>{
    const browser=await playwright.chromium.launch({headless:true});
    try{
      const page=await browser.newPage({viewport:{width:1280,height:900},
        deviceScaleFactor:1});
      const calls=[];
      await page.route('**/api/projects/**/unified-analytics/**',async route=>{
        const request=route.request(),url=request.url();
        calls.push({url,method:request.method(),headers:request.headers()});
        if(url.includes('/snapshots?')){
          return route.fulfill({status:200,contentType:'application/json',
            body:JSON.stringify({ok:true,schema_version:VERSION,
              snapshots:[
                {id:9,video_id:101,snapshot_version:1,status:'partial',
                 created_at:'2026-10-28T10:00:00+00:00'},
                {id:8,video_id:102,snapshot_version:2,status:'blocked',
                 created_at:'2026-10-28T09:00:00+00:00'}],
              next_before:null})});
        }
        if(url.endsWith('/aggregates')){
          return route.fulfill({status:409,contentType:'application/json',
            body:JSON.stringify({ok:false,
              code:'aggregate_requires_live_qualified_pins',
              error:'UAE request rejected'})});
        }
        return route.fulfill({status:404,contentType:'application/json',
          body:JSON.stringify({ok:false,code:'not_found'})});
      });
      await page.setContent('<!doctype html><html lang="ru"><head></head><body style="background:#0b0b0d"><main id="fixture"></main></body></html>');
      await page.addStyleTag({path:path.resolve(__dirname,
        '../../site/unified-analytics-studio.css')});
      await page.addScriptTag({path:path.resolve(__dirname,
        '../../site/unified-analytics-studio.js')});
      await page.evaluate(pid=>{
        window.ContentMachineUnifiedAnalyticsStudio.mount(
          document.getElementById('fixture'),pid
        );
      },PROJECT);
      assert.equal(calls.length,0,'no fetch on initial mount');
      await page.locator('.uae-studio input[type=password]').fill(TOKEN);
      await page.getByRole('button',{name:'Подключить и обновить'}).click();
      await page.getByText('Снимок #9 · v1').waitFor();
      assert.equal(calls.length,1);
      assert.equal(calls[0].headers['x-uae-project-token'],TOKEN);
      assert.equal(calls[0].headers['x-uae-api-version'],VERSION);
      await page.getByRole('button',{name:'Проверить доступность агрегатов'}).click();
      await page.getByText('Агрегаты пока закрыты:',{exact:false}).waitFor();
      assert.equal(await page.locator('.uae-studio .uae-pill.partial').count(),1);
      assert.equal(await page.locator('.uae-studio .uae-pill.blocked').count(),1);
      assert.match(await page.locator('.uae-studio').innerText(),/не нулевой результат/);
      const screenshotDir=process.env.CM_UAE_SCREENSHOT_DIR;
      if(screenshotDir){
        fs.mkdirSync(screenshotDir,{recursive:true});
        await page.locator('.uae-studio').screenshot({
          path:path.join(screenshotDir,'ua27-studio-partial-blocked.png')
        });
      }
      await page.getByRole('button',{name:'Очистить доступ'}).click();
      assert.equal(await page.locator('.uae-studio input[type=password]').inputValue(),'');
      assert.equal(await page.getByText('Снимок #9 · v1').count(),0);
    }finally{await browser.close();}
  });
test('Chromium: responsive mobile Studio not wider than its container',
  {skip:!playwright?'optional Playwright not installed':false},async()=>{
    const browser=await playwright.chromium.launch({headless:true});
    try{
      const page=await browser.newPage({viewport:{width:390,height:844}});
      await page.setContent('<!doctype html><html><head></head><body style="margin:0;background:#0b0b0d"><main id="fixture"></main></body></html>');
      await page.addStyleTag({path:path.resolve(__dirname,
        '../../site/unified-analytics-studio.css')});
      await page.addScriptTag({path:path.resolve(__dirname,
        '../../site/unified-analytics-studio.js')});
      await page.evaluate(pid=>window.ContentMachineUnifiedAnalyticsStudio.mount(
        document.getElementById('fixture'),pid
      ),PROJECT);
      const width=await page.locator('.uae-studio').evaluate(e=>e.getBoundingClientRect().width);
      assert.ok(width<=390,'Studio must stay within mobile viewport');
      assert.equal(await page.getByRole('button',{name:'Подключить и обновить'}).count(),1);
    }finally{await browser.close();}
  });
