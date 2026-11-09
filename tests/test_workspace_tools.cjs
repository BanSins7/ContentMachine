'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'site/workspace.css'),'utf8');
const js=fs.readFileSync(path.join(root,'site/workspace.js'),'utf8');

test('Carousel Analysis stays first; full tools and pipeline are visible below it',()=>{
  const sidebar=html.split('<aside class="sidebar"')[1].split('</aside>')[0];
  const topbar=html.split('<header class="workspace-topbar">')[1].split('</header>')[0];
  assert.ok(sidebar.includes('id="carouselAnalysisNav"'));
  assert.ok(sidebar.includes('href="/carousel-studio/"'));
  assert.equal((sidebar.match(/<a id="carouselAnalysisNav"/g)||[]).length,1);
  assert.ok(sidebar.includes('id="workspaceTools"'));
  assert.ok(!topbar.includes('id="workspaceTools"'),'no hidden topbar tool menu');
  assert.ok(sidebar.indexOf('id="carouselAnalysisNav"')<sidebar.indexOf('id="workspaceTools"'));
  assert.ok(sidebar.includes('11 модулей pipeline'));
  assert.ok(sidebar.includes('Остальные инструменты'));
  for(const id of ['workspaceLibraryNav','workspaceHistoryNav','testLabNav',
    'workspaceStageNav','settingsNav','discoveryNav','qdpNav']){
    assert.ok(sidebar.includes('id="'+id+'"'),id+' belongs in the sidebar');
    assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1,'duplicate '+id);
  }
  for(const dest of ['/carousel-studio/','/market-intelligence.html','/sre-studio.html']){
    assert.ok(sidebar.includes('href="'+dest+'"'),dest+' must be accessible');
  }
  for(const platform of ['instagram','tiktok','youtube']){
    assert.ok(sidebar.includes('data-platform="'+platform+'"'));
  }
  assert.ok(html.includes('id="projectsNav"'),'legacy project handler still exists');
  assert.ok(html.includes('class="brand workspace-header-brand"'));
  assert.ok(html.includes('id="workspacePipelineRows"'),'main white pipeline is retained');
  assert.match(css,/\.carousel-analysis-sidebar-button\s*\{/);
  assert.match(css,/\.workspace-stage-disclosure:not\(\[open\]\) #workspaceStageNav \{display:none;\}/);
  assert.match(css,/\.cm-workspace \.main \{background:#fff!important;\}/);
  assert.match(js,/toolsMenu\.addEventListener\('click'/);
  assert.match(js,/if\(mobile\.matches\)\$\('workspaceSidebar'\)\.inert=true;/);
});
