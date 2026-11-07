'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'site/workspace.css'),'utf8');
const js=fs.readFileSync(path.join(root,'site/workspace.js'),'utf8');

test('sidebar contains only the collapsed Tools disclosure and hidden legacy project bindings',()=>{
  const sidebar=html.split('<aside class="sidebar"')[1].split('</aside>')[0];
  const tools=sidebar.split('<details class="workspace-tools workspace-tools-unified"')[1].split('</details>')[0];
  assert.ok(tools.includes('id="workspaceTools"'));
  for(const id of ['workspaceLibraryNav','workspaceHistoryNav','testLabNav','workspaceStageNav','settingsNav','discoveryNav','qdpNav']){
    assert.ok(tools.includes('id="'+id+'"'),id+' must live inside Tools');
    assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1,'duplicate '+id);
  }
  assert.ok(!sidebar.includes('class="brand"'),'brand is not inside the rail');
  assert.ok(html.includes('class="brand workspace-header-brand"'),'brand is in the topbar');
  assert.match(css,/\.workspace-tools-unified\s*\{/);
  assert.match(js,/toolsMenu\.addEventListener\('click'/);
});
