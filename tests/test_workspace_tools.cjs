'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'site/workspace.css'),'utf8');
const js=fs.readFileSync(path.join(root,'site/workspace.js'),'utf8');

test('only Carousel Analysis stays in sidebar; remaining tools appear after main white pipeline',()=>{
  const sidebar=html.split('<aside class="sidebar"')[1].split('</aside>')[0];
  const topbar=html.split('<header class="workspace-topbar">')[1].split('</header>')[0];
  const overview=html.split('<div id="workspaceOverview">')[1].split('<div id="workspaceStage"')[0];
  const directory=html.split('<section class="workspace-tools workspace-tools-unified"')[1].split('</section>')[0];
  const content=html.split('<div class="content">')[1].split('</main>')[0];
  assert.ok(sidebar.includes('id="carouselAnalysisNav"'));
  assert.ok(sidebar.includes('href="/carousel-studio/"'));
  assert.equal((sidebar.match(/<a\b/g)||[]).length,1);
  assert.equal((sidebar.match(/<button\b/g)||[]).length,0);
  assert.equal((sidebar.match(/<details\b/g)||[]).length,0);
  assert.ok(!sidebar.includes('id="workspaceTools"'),'sidebar should show Carousel Analysis only');
  assert.ok(!topbar.includes('id="workspaceTools"'),'tools must not be hidden in the topbar');
  assert.ok(!overview.includes('id="workspaceTools"'),'tool directory must not disappear when overview is hidden');
  assert.ok(directory.includes('id="workspaceTools"'),'tool directory must be persistent');
  assert.ok(content.includes('id="workspaceTools"'));
  assert.ok(html.indexOf('id="workspacePipelineRows"')<html.indexOf('id="workspaceTools"'),'tools must be below the pipeline');
  assert.ok(html.indexOf('id="settingsView"')<html.indexOf('id="workspaceTools"'),'tools must remain accessible after every view');
  assert.ok(directory.includes('Остальные инструменты ContentMachine'));
  assert.ok(directory.includes('11 модулей pipeline'));
  for(const id of ['workspaceLibraryNav','workspaceHistoryNav','testLabNav',
    'workspaceStageNav','settingsNav','discoveryNav','qdpNav']){
    assert.ok(directory.includes('id="'+id+'"'),id+' belongs under the pipeline');
    assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1,'duplicate '+id);
  }
  for(const dest of ['/market-intelligence.html','/sre-studio.html']){
    assert.ok(directory.includes('href="'+dest+'"'),dest+' must be accessible');
  }
  for(const platform of ['instagram','tiktok','youtube']){
    assert.ok(directory.includes('data-platform="'+platform+'"'));
  }
  assert.ok(html.includes('id="projectsNav"'),'legacy project handler still exists');
  assert.ok(html.includes('class="brand workspace-header-brand"'));
  assert.match(css,/\.carousel-analysis-sidebar-button\s*\{/);
  assert.match(css,/\.workspace-stage-disclosure:not\(\[open\]\) #workspaceStageNav \{display:none;\}/);
  assert.match(css,/\.cm-workspace \.main \{background:#fff!important;\}/);
  assert.match(js,/toolsMenu\.addEventListener\('click'/);
  assert.match(js,/if\(mobile\.matches\)\$\('workspaceSidebar'\)\.inert=true;/);
  assert.match(css,/UI audit \\(2026-11\\): preserve the/);
});
