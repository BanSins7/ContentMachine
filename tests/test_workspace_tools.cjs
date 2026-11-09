'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'site/workspace.css'),'utf8');
const js=fs.readFileSync(path.join(root,'site/workspace.js'),'utf8');

test('sidebar exposes only Carousel Analysis; Tools remains in topbar',()=>{
  const sidebar=html.split('<aside class="sidebar"')[1].split('</aside>')[0];
  const topbar=html.split('<header class="workspace-topbar">')[1].split('</header>')[0];
  assert.ok(sidebar.includes('id="carouselAnalysisNav"'));
  assert.ok(sidebar.includes('href="/carousel-studio/"'));
  assert.equal((sidebar.match(/<a\b/g)||[]).length,1);
  assert.equal((sidebar.match(/<button\b/g)||[]).length,0);
  assert.equal((sidebar.match(/<details\b/g)||[]).length,0);
  assert.ok(!sidebar.includes('id="workspaceTools"'),'Tools must not live in the rail');
  assert.ok(topbar.includes('id="workspaceTools"'));
  for(const id of ['workspaceLibraryNav','workspaceHistoryNav','testLabNav',
    'workspaceStageNav','settingsNav','discoveryNav','qdpNav']){
    assert.ok(topbar.includes('id="'+id+'"'),id+' in the header Tools menu');
    assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1,'duplicate '+id);
  }
  assert.ok(html.includes('id="projectsNav"'),'legacy project handler still exists');
  assert.ok(html.includes('class="brand workspace-header-brand"'));
  assert.match(css,/\.carousel-analysis-sidebar-button\s*\{/);
  assert.match(css,/\.workspace-topbar-actions \.workspace-tools-unified\s*\{/);
  assert.match(js,/toolsMenu\.addEventListener\('click'/);
});
