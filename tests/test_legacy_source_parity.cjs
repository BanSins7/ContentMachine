'use strict';
// React executes the TypeScript-compatible copies, not site/*.js.
// A hotfix to site/*.js alone must never silently leave production React stale.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const root=path.resolve(__dirname,'..');
const legacy=path.join(root,'frontend','src','legacy');
const files=fs.readdirSync(legacy).filter(name=>name.endsWith('.ts')).sort();

function stripCompatibilityHeader(source) {
  return source
    .replace(/^\/\/ @ts-nocheck\r?\n/,'')
    .replace(/^\/\/ Browser-compatible TypeScript migration source; preserves existing DOM contracts\.\r?\n/,'')
    .replace(/^\/\/ Compatibility-script TypeScript source for the main React workspace\.\r?\n/,'');
}

test('every legacy React module matches its runtime JavaScript source',()=>{
  assert.ok(files.length>=30,'unexpectedly missing legacy UI modules');
  for(const file of files){
    const base=file.slice(0,-3);
    const canonical=base==='group-a'
      ?path.join(root,'intelligence','group_a','site','group-a.js')
      :path.join(root,'site',base+'.js');
    assert.ok(fs.existsSync(canonical),file+': missing runtime counterpart');
    const js=fs.readFileSync(canonical,'utf8');
    const ts=stripCompatibilityHeader(fs.readFileSync(path.join(legacy,file),'utf8'));
    assert.equal(ts,js,
      file+': React uses this legacy TypeScript copy. Update BOTH '+canonical+' and '+file);
  }
});

test('React workspace uses mobile-safe menu and truthful read-only stage labels',()=>{
  const workspace=fs.readFileSync(path.join(legacy,'workspace.ts'),'utf8');
  assert.match(workspace,/if\(mobile\.matches\)\$\('workspaceSidebar'\)\.inert=true/);
  assert.doesNotMatch(workspace,/toolsMenu\.open|tools\.open=false/);
  assert.doesNotMatch(workspace,/API не подключён/);
});
