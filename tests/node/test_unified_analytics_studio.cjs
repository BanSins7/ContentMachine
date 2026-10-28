/* UA-MOD-27 DOM smoke without npm dependencies. Run with:
 * node --test tests/node/test_unified_analytics_studio.cjs
 * This test never contacts a real server/PIE/Group C and uses no paid models.
 */
'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const SCRIPT=fs.readFileSync(path.resolve(
  __dirname,'../../site/unified-analytics-studio.js'),'utf8');
const PROJECT='a'.repeat(32), TOKEN='b'.repeat(64), VERSION='uae-local-http/v1';
const REQUEST='00000000-0000-0000-0000-000000000013';
const JOB='00000000-0000-0000-0000-000000000014';

class Element{
  constructor(tag){
    this.tag=String(tag).toLowerCase();
    this._text='';this.nodes=[];this.listeners={};
    this.value='';this.className='';this.dataset={};this.attrs={};
    this.hidden=false;this.disabled=false;this.isConnected=true;
  }
  get children(){return this.nodes;}
  get textContent(){return this._text+this.nodes.map(x=>x.textContent||'').join('');}
  set textContent(value){this._text=String(value);this.nodes=[];}
  append(...nodes){this.nodes.push(...nodes);return undefined;}
  replaceChildren(...nodes){this.nodes=[...nodes];this._text='';}
  setAttribute(name,value){this.attrs[name]=value;}
  addEventListener(name,callback){
    (this.listeners[name]??=[]).push(callback);
  }
  async event(name='click'){
    for(const callback of this.listeners[name]||[]){
      await callback({type:name,target:this});
    }
  }
}
function walk(node,predicate){
  if(predicate(node))return node;
  for(const child of node.children||[]){
    const found=walk(child,predicate);if(found)return found;
  }
  return undefined;
}
function all(node,predicate){
  let result=[];
  if(predicate(node))result.push(node);
  for(const child of node.children||[])result.push(...all(child,predicate));
  return result;
}
function findButton(root,text){
  const b=walk(root,n=>n.tag==='button'&&n.textContent.includes(text));
  assert.ok(b,'Missing button '+text);return b;
}
function field(root,text){
  const label=walk(root,n=>n.tag==='label'&&
    n.children[0]?.textContent===text);
  assert.ok(label,'Missing field '+text);
  return walk(label,n=>n.tag==='input');
}
function createFixture(handler=async()=>({ok:true,schema_version:VERSION,snapshots:[],next_before:null})){
  const requests=[], confirms=[];
  let confirmValue=true;
  const dom={createElement:tag=>new Element(tag)};
  const browser={confirm:text=>{confirms.push(text);return confirmValue;}};
  const fetch=async(url,options)=>{
    requests.push({url,options});
    const res=await handler(url,options);
    if('status' in res&&res.status>=400)return {
      ok:false,status:res.status,json:async()=>res
    };
    return {ok:true,status:200,json:async()=>res};
  };
  const context={document:dom,window:browser,fetch,console:{
    error(){throw Error('Studio should not log sensitive data');}
  }};
  vm.runInNewContext(SCRIPT,context,{filename:'unified-analytics-studio.js'});
  const parent=new Element('main');
  context.window.ContentMachineUnifiedAnalyticsStudio.mount(parent,PROJECT);
  return {
    root:parent,requests,confirms,
    setConfirm(value){confirmValue=value;},
    token(){return walk(parent,n=>n.tag==='input'&&n.type==='password');}
  };
}
function timeline(id,status='partial'){
  return {id,video_id:id+100,snapshot_version:1,status,
    created_at:'2026-10-28T09:00:00+00:00',
    request_id:REQUEST};
}

test('mount: no automatic fetch, manual token only, never any fake breakout values',()=>{
  const f=createFixture();assert.equal(f.requests.length,0);
  assert.match(f.root.textContent,/Unified Analytics Studio/);
  assert.match(f.root.textContent,/Breakout Score и компоненты не опубликованы/);
  assert.match(f.root.textContent,/не нулевой результат/);
  assert.ok(!f.root.textContent.includes('Breakout 0–100'));
  assert.equal(f.token().autocomplete,'off');
});
test('missing token: 403 without fetch or private data',async()=>{
  const f=createFixture();
  await findButton(f.root,'Подключить и обновить').event();
  assert.equal(f.requests.length,0);
  assert.match(f.root.textContent,/Доступ к этому проекту отклонён/);
});
test('successful explicit GET: version+token headers, status cards and no secret in URL',async()=>{
  const f=createFixture(async()=>({
    ok:true,schema_version:VERSION,snapshots:[timeline(9),timeline(8,'blocked')],
    next_before:null
  }));
  f.token().value=TOKEN;
  await findButton(f.root,'Подключить и обновить').event();
  assert.equal(f.requests.length,1);
  const a=f.requests[0];
  assert.equal(a.options.headers['X-UAE-Project-Token'],TOKEN);
  assert.equal(a.options.headers['X-UAE-Api-Version'],VERSION);
  assert.match(a.url,/\/api\/projects\/[a-f0-9]{32}\/unified-analytics\/snapshots\?limit=10$/);
  assert.ok(!a.url.includes(TOKEN));
  assert.match(f.root.textContent,/Частичных: 1/);
  assert.match(f.root.textContent,/Заблокированных: 1/);
  assert.match(f.root.textContent,/Снимок #9/);
  assert.ok(!f.root.textContent.includes('Квалифицировано: 2'));
});
test('snapshot detail: exact ID URL and no raw media or invented score',async()=>{
  const f=createFixture(async(url)=>{
    if(url.includes('/snapshots/9'))return {
      ok:true,schema_version:VERSION,
      snapshot:{id:9,video_id:1009,snapshot_version:1,
        status:'partial',gate_schema:'uae-qualification/v1',
        input_schema:'unified-analytics-input/v1',
        created_at:'2026-10-28T09:00:00+00:00'}
    };
    return {ok:true,schema_version:VERSION,snapshots:[timeline(9)],
      next_before:null};
  });
  f.token().value=TOKEN;
  await findButton(f.root,'Подключить').event();
  await findButton(f.root,'Открыть сведения').event();
  assert.ok(f.requests[1].url.endsWith('/snapshots/9'));
  assert.match(f.root.textContent,/Неизвестно \/ недоступно/);
  assert.match(f.root.textContent,/Подробные приватные причины не опубликованы/);
});
test('GET 409 aggregate not converted to visible A/B/C scores',async()=>{
  const f=createFixture(async(url)=>url.endsWith('/aggregates')
    ?{ok:false,status:409,code:'aggregate_requires_live_qualified_pins'}
    :{ok:true,schema_version:VERSION,snapshots:[],next_before:null});
  f.token().value=TOKEN;
  await findButton(f.root,'Подключить').event();
  await findButton(f.root,'Проверить доступность агрегатов').event();
  assert.match(f.root.textContent,/Агрегаты пока закрыты/);
  assert.match(f.root.textContent,/Breakout Score и компоненты не опубликованы/);
});
test('bad token/API 403 cannot keep old timeline visible',async()=>{
  let fail=false;
  const f=createFixture(async()=>{
    return fail?{ok:false,status:403,code:'project_forbidden'}:
      {ok:true,schema_version:VERSION,snapshots:[timeline(9)],
        next_before:null};
  });
  f.token().value=TOKEN;await findButton(f.root,'Подключить').event();
  assert.match(f.root.textContent,/Снимок #9/);
  fail=true;await findButton(f.root,'Обновить список').event();
  assert.ok(!f.root.textContent.includes('Снимок #9'));
  assert.equal(f.token().value,'');
});
test('job UUID must be valid before fetch',async()=>{
  const f=createFixture();f.token().value=TOKEN;
  field(f.root,'UUID задания').value='bad';
  await findButton(f.root,'Проверить статус').event();
  assert.equal(f.requests.length,0);
});
test('job status is not qualification and cancel requires confirmation/CSRF',async()=>{
  let cancelled=false;
  const f=createFixture(async(url,opts)=>{
    if(url.includes('/cancel')){
      cancelled=true;return {ok:true,schema_version:VERSION,cancelled:true};
    }
    if(url.includes('/jobs/'))return {
      ok:true,schema_version:VERSION,
      job:{job_id:JOB,request_id:REQUEST,state:'pending',attempts:0,
        max_attempts:4,snapshot_id:null,created_at:'2026-10-28T09:00:00Z',
        updated_at:'2026-10-28T09:00:00Z',last_error_code:null}
    };
    throw Error('unknown request');
  });
  f.token().value=TOKEN;field(f.root,'UUID задания').value=JOB;
  await findButton(f.root,'Проверить статус').event();
  assert.match(f.root.textContent,/Квалификация.*Не доказана статусом очереди/s);
  f.setConfirm(false);
  await findButton(f.root,'Отменить незавершённое').event();
  assert.equal(cancelled,false);
  f.setConfirm(true);
  await findButton(f.root,'Отменить незавершённое').event();
  assert.equal(cancelled,true);
  assert.equal(f.requests[1].options.headers['X-ContentMachine'],'1');
  assert.deepEqual(JSON.parse(f.requests[1].options.body),{});
});
test('manual enqueue has required fields, confirm and no auto start',async()=>{
  let POST=0;
  const f=createFixture(async(url,opts)=>{
    POST++;return {ok:true,schema_version:VERSION,
      job_id:JOB,result:'created',qualified:false};
  });
  f.token().value=TOKEN;
  field(f.root,'UUID запроса').value=REQUEST;
  field(f.root,'request_sha256').value='c'.repeat(64);
  field(f.root,'idempotency_key').value='ua27.queue.one';
  field(f.root,'batch_key').value='batch.1';
  assert.equal(POST,0);
  f.setConfirm(false);
  await findButton(f.root,'Подтвердить постановку').event();
  assert.equal(POST,0);
  f.setConfirm(true);
  await findButton(f.root,'Подтвердить постановку').event();
  assert.equal(POST,1);
  assert.equal(f.requests[0].options.method,'POST');
  assert.equal(f.requests[0].options.headers['X-ContentMachine'],'1');
  const body=JSON.parse(f.requests[0].options.body);
  assert.equal(body.request_id,REQUEST);
  assert.equal(body.request_sha256,'c'.repeat(64));
  assert.match(f.root.textContent,/Квалификация не подтверждена/);
});
test('token change clears loaded snapshot UI and disables refresh',async()=>{
  const f=createFixture(async()=>({ok:true,schema_version:VERSION,
    snapshots:[timeline(9)],next_before:null}));
  f.token().value=TOKEN;await findButton(f.root,'Подключить').event();
  assert.match(f.root.textContent,/Снимок #9/);
  f.token().value='d'.repeat(64);
  await f.token().event('input');
  assert.ok(!f.root.textContent.includes('Снимок #9'));
  assert.equal(findButton(f.root,'Обновить список').disabled,true);
});
test('changing project token clears already inspected job, request and snapshot',async()=>{
  const f=createFixture(async(url)=>{
    if(url.includes('/snapshots/9'))return {ok:true,schema_version:VERSION,
      snapshot:{...timeline(9),gate_schema:'uae-qualification/v1',
        input_schema:'unified-analytics-input/v1'}};
    if(url.includes('/jobs/'))return {ok:true,schema_version:VERSION,
      job:{job_id:JOB,request_id:REQUEST,state:'pending',attempts:0,max_attempts:4,
        snapshot_id:null,created_at:'2026-10-28T09:00:00Z',
        updated_at:'2026-10-28T09:00:00Z',last_error_code:null}};
    return {ok:true,schema_version:VERSION,snapshots:[timeline(9)],
      next_before:null};
  });
  f.token().value=TOKEN;
  await findButton(f.root,'Подключить').event();
  await findButton(f.root,'Открыть сведения').event();
  field(f.root,'UUID задания').value=JOB;
  await findButton(f.root,'Проверить статус').event();
  assert.match(f.root.textContent,/Не доказана статусом очереди/);
  f.token().value='d'.repeat(64);
  await f.token().event('input');
  assert.ok(!f.root.textContent.includes('Не доказана статусом очереди'));
  assert.ok(!f.root.textContent.includes('Снимок #9'));
  assert.ok(!f.root.textContent.includes('uae-qualification/v1'));
  assert.equal(findButton(f.root,'Отменить незавершённое').hidden,true);
});
test('disconnect clears token and all project-specific values',async()=>{
  const f=createFixture(async()=>({ok:true,schema_version:VERSION,
    snapshots:[timeline(9)],next_before:null}));
  f.token().value=TOKEN;await findButton(f.root,'Подключить').event();
  await findButton(f.root,'Очистить доступ').event();
  assert.equal(f.token().value,'');
  assert.ok(!f.root.textContent.includes('Снимок #9'));
});
test('untrusted content must never be inserted as HTML',async()=>{
  const attack='<img src=x onerror=alert(1)>';
  const f=createFixture(async()=>({ok:true,schema_version:VERSION,
    snapshots:[{...timeline(9),created_at:attack}],next_before:null}));
  f.token().value=TOKEN;await findButton(f.root,'Подключить').event();
  const dangerous=all(f.root,n=>n.tag==='img'||n.tag==='script');
  assert.equal(dangerous.length,0);
  assert.ok(!SCRIPT.includes('.innerHTML'));
  assert.ok(!SCRIPT.includes('localStorage'));
  assert.ok(!SCRIPT.includes('sessionStorage'));
});
test('no credentials embedded in URL, cookies, storage or timers',()=>{
  assert.ok(!SCRIPT.includes('document.cookie'));
  assert.ok(!SCRIPT.includes('setInterval('));
  assert.ok(!SCRIPT.includes('setTimeout('));
  assert.ok(!SCRIPT.includes('localStorage'));
  assert.ok(!SCRIPT.includes('sessionStorage'));
  assert.ok(!SCRIPT.includes('console.log('));
});
