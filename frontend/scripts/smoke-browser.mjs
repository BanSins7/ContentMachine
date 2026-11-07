/**
 * Real Chromium smoke for React production assets; completely offline API stub.
 * Only validates UI boot and asset wiring, not business backend behavior.
 */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const repo = resolve(fileURLToPath(new URL('../../', import.meta.url)));
const site = resolve(repo, 'site');
const dist = resolve(site, 'react-dist');
const port = 18776;
const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
};
const api = (pathname) => {
  if (pathname === '/api/state') return { ok: true, platforms: {}, configuration: { openrouter_configured: false }, jobs: [], library: [], tiktok_library: [], discovery: [] };
  if (pathname === '/api/projects') return { ok: true, projects: [] };
  return { ok: false, error: 'Offline smoke: API unavailable' };
};
const server = createServer((req, res) => {
  const pathname = new URL(req.url || '/', 'http://127.0.0.1:' + port).pathname;
  if (pathname.startsWith('/api/')) {
    const data = api(pathname);
    res.writeHead(data.ok ? 200 : 503, {'Content-Type': 'application/json'});
    res.end(JSON.stringify(data));
    return;
  }
  let pathnameOnDisk;
  if (pathname === '/' || ['index.html','sre-studio.html','group-a.html'].some(n => pathname === '/' + n)) {
    pathnameOnDisk = resolve(dist,'index.html');
  } else if (/^\/assets\/[a-z0-9_.-]+$/i.test(pathname)) {
    pathnameOnDisk = resolve(dist, pathname.slice(1));
  } else if (/^\/react-legacy\/[a-z0-9-]+\.js$/i.test(pathname)) {
    pathnameOnDisk = resolve(dist, 'legacy', pathname.replace('/react-legacy/', ''));
  } else if (/^\/[a-z0-9-]+\.css$/.test(pathname)) {
    pathnameOnDisk = pathname === '/group-a.css'
      ? resolve(repo, 'intelligence/group_a/site/group-a.css')
      : resolve(site, pathname.slice(1));
  }
  if (!pathnameOnDisk || !existsSync(pathnameOnDisk)) {
    res.writeHead(404);
    res.end('Not found');
    return;
  }
  res.writeHead(200, {
    'Content-Type': types[extname(pathnameOnDisk)] || 'application/octet-stream',
    'Cache-Control':'no-store',
    'Content-Security-Policy':"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
  });
  res.end(readFileSync(pathnameOnDisk));
});

await new Promise(resolveReady => server.listen(port, '127.0.0.1', resolveReady));
const browser = await chromium.launch({headless: true});
const issues = [];
try {
  const page = await browser.newPage();
  // Offline mock: verify UI wiring without contacting paid providers.
  let providerState = {
    ok:true,preferred_provider:'openrouter',active_model:'google/gemini-3.8-flash',
    openrouter_configured:true,rusgpt_configured:false,brightdata_configured:false,
    brightdata_authorized:false,brightdata_ready:false,no_keys_returned:true,
  };
  await page.route('**/api/provider-settings',async route => {
    if (route.request().method() === 'POST') {
      const payload = route.request().postDataJSON();
      assert.equal(payload.preferred_provider,'rusgpt');
      assert.equal(payload.rusgpt_api_key,'fixture-rusgpt');
      assert.equal(payload.brightdata_api_token,'fixture-brightdata');
      providerState = {...providerState,preferred_provider:'rusgpt',
        active_model:'anthropic/claude-haiku-5.5',rusgpt_configured:true,
        brightdata_configured:true,brightdata_authorized:true,brightdata_ready:true};
    }
    await route.fulfill({status:200,contentType:'application/json',
      body:JSON.stringify(providerState)});
  });
  page.on('pageerror', (error) => issues.push(error.message));
  page.on('response', (response) => {
    if (response.url().includes('/react-legacy/') && response.status() !== 200)
      issues.push('Browser module unavailable: ' + response.url());
  });

  await page.goto('http://127.0.0.1:' + port + '/', {waitUntil:'networkidle'});
  await page.waitForFunction(() => Boolean(window.ContentMachineApp && window.ContentMachineWorkspace), null, {timeout: 15000});
  assert.equal(await page.locator('#workspaceTitle').count(),1);
  assert.equal(await page.locator('#workspaceStageNav').count(),1);
  const toolsMenu = page.locator('#workspaceTools');
  assert.equal(await toolsMenu.evaluate((el) => el.open),false);
  assert.equal(await page.locator('#workspaceSidebar .brand').count(),0);
  assert.equal(await page.locator('.workspace-header-brand').count(),1);
  await toolsMenu.locator('summary').click();
  assert.equal(await toolsMenu.evaluate((el) => el.open),true);
  assert.equal(await page.locator('#workspaceStageNav .workspace-stage-link').count(),11);
  await page.locator('#settingsNav').click();
  assert.equal(await page.locator('#settingsView').isVisible(),true);
  assert.equal(await toolsMenu.evaluate((el) => el.open),false);
  await toolsMenu.locator('summary').click();
  await page.locator('#workspaceLibraryNav').click();
  await page.locator('#projectsView').waitFor({state:'visible',timeout:10000});
  assert.equal(await page.evaluate(() => window.ContentMachineApp.currentView()),'projects');
  assert.equal(await toolsMenu.evaluate((el) => el.open),false);


  await page.locator('#workspaceTools summary').click();
  await page.locator('#settingsNav').click();
  await page.locator('#settingsView').waitFor({state:'visible',timeout:10000});
  await page.waitForFunction(() => document.getElementById('providerSettingsStatus').textContent !== 'Проверяем…');
  await page.locator('#cmProvider').selectOption('rusgpt');
  await page.locator('#cmRusgptKey').fill('fixture-rusgpt');
  await page.locator('#cmBrightdataKey').fill('fixture-brightdata');
  await page.locator('#cmBrightdataAuthorized').check();
  await page.locator('#cmProviderSave').click();
  await page.waitForFunction(() =>
    document.getElementById('cmProviderFeedback').textContent.includes('сохранены'));
  assert.equal(await page.locator('#cmRusgptKey').inputValue(),'');
  assert.equal(await page.locator('#cmBrightdataKey').inputValue(),'');
  assert.match(await page.locator('#cmBrightdataKeyState').textContent(),/настроен/);
  assert.match(await page.locator('#cmRusgptKeyState').textContent(),/настроен/);

  await page.goto('http://127.0.0.1:' + port + '/sre-studio.html', {waitUntil:'networkidle'});
  assert.equal(await page.locator('#sreTitle').count(),1);
  assert.equal(await page.locator('#sreConnect').count(),1);

  await page.goto('http://127.0.0.1:' + port + '/group-a.html', {waitUntil:'networkidle'});
  assert.equal(await page.locator('#project').count(),1);
  assert.equal(await page.locator('#run').count(),1);
  assert.deepEqual(issues,[]);
  console.log('React production Chromium boot: main, SRE, Group A OK');
} finally {
  await browser.close();
  await new Promise(resolveClose => server.close(resolveClose));
}
