'use strict';
/* PIE-MOD-34 Studio in Node vm, no browser/network/actual provider or source. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'site/pie-explorer.html'),'utf8');
const js=fs.readFileSync(path.join(root,'site/pie-explorer.js'),'utf8');
const app=fs.readFileSync(path.join(root,'app.py'),'utf8');
const home=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
const ID='a'.repeat(32);
const PATTERN={
  series_id:'b'.repeat(64),revision:2,feature_name:'duration',
  factor_id:'duration',platform:'youtube',status:'exploratory',
  status_label:'Предварительный результат',matched_pair_count:4,
  supported_bin_count:2,descriptive_raw_mean:'5/2',
  descriptive_source_unit:'ms',caveats:['Не причинный вывод.'],
  can_publish:false,causal_claim:false,independent_holdout_confirmed:false
};
const BASE={
  schema_version:'pie-pattern-explorer/v1',ok:true,project_id:ID,
  view:'overview',state:'candidate_data',items:[PATTERN],
  next_cursor:null,page_count:1,page_scope_only:true,
  publication_allowed:false,causal_claim:false,meta:{}
};
function tick(){return new Promise(resolve=>setImmediate(resolve));}
function node(tag='div'){
  const handlers={};
  return {
    tagName:tag,style:{},dataset:{},textContent:'',hidden:false,
    disabled:false,value:'',children:[],
    append(...children){this.children.push(...children);},
    replaceChildren(...children){this.children=[...children];},
    addEventListener(kind,callback){(handlers[kind]||=[]).push(callback);},
    fire(kind){for(const cb of handlers[kind]||[])cb({preventDefault(){},target:this});}
  };
}
function studio(options={}){
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)]
    .map(m=>[m[1],node()]));
  for(const name of ['pieStatus','pieView','piePlatform','pieMinPairs','pieProject'])
    nodes.get(name).tagName='input';
  nodes.get('pieView').value='overview';
  nodes.get('pieMinPairs').value='1';
  const requested=[];
  let payloads=options.payloads||[BASE];
  let index=0;
  const sandbox={
    document:{
      getElementById(id){assert(nodes.has(id),'Missing Studio node '+id);return nodes.get(id);},
      createElement(tag){return node(tag);}
    },
    location:{search:options.search||''},
    URLSearchParams,Promise,Number,String,Math,Error,
    fetch:async (url,settings)=>{
      requested.push({url,settings});
      const p=payloads[Math.min(index++,payloads.length-1)];
      return {
        ok:p.status==null||p.status===200,
        json:async()=>p.data||p,
      };
    },
  };
  vm.runInNewContext(js,sandbox,{filename:'pie-explorer.js'});
  return {nodes,requested,setPayloads(x){payloads=x;index=0;},
          get(id){return nodes.get(id);},
          async submit(){
            nodes.get('pieFilterForm').fire('submit');
            await tick();await tick();
          },
          async next(){
            nodes.get('pieNext').fire('click');
            await tick();await tick();
          },
          async back(){
            nodes.get('pieBack').fire('click');
            await tick();await tick();
          },
  };
}
async function tests(){
  assert(html.includes('id="pieProject"'));
  assert(html.includes('id="pieFilterForm"'));
  assert(html.includes('id="pieError"'));
  assert(html.includes('id="pieNext"'));
  assert(html.includes('role="alert"'));
  assert(home.includes('href="/pie-explorer.html"'));
  assert(app.includes('pie_explorer_get'));
  assert(app.includes('pie_explorer_post'));
  assert(app.includes("'/pie-explorer.js': 'pie-explorer.js'"));
  assert(!js.includes('innerHTML'));
  assert(!js.includes('eval('));
  assert(!js.includes('localStorage'));
  assert(!js.includes("method:'POST'"));

  let ui=studio();
  assert.equal(ui.requested.length,0,'No implicit data read without project');
  assert(ui.get('pieInfo').textContent.includes('доступ'));
  await ui.submit();
  assert.equal(ui.requested.length,0,'Invalid empty project blocked');
  assert.equal(ui.get('pieError').hidden,false);
  assert.equal(ui.get('pieResults').hidden,true);

  ui.get('pieProject').value=ID;
  await ui.submit();
  assert.equal(ui.requested.length,1);
  assert(ui.requested[0].url.includes('/api/projects/'+ID+'/pie/overview?'));
  assert.equal(ui.requested[0].settings.credentials,'same-origin');
  assert.equal(ui.requested[0].settings.cache,'no-store');
  assert.equal(ui.get('pieCount').textContent,'1');
  assert.equal(ui.get('piePairs').textContent,'4');
  assert.equal(ui.get('pieResults').hidden,false);
  assert.equal(ui.get('pieNext').disabled,true);
  assert.equal(ui.get('pieBack').disabled,true);
  assert(ui.get('pieCards').children[0].children.some(x=>
    String(x.textContent).includes('5/2 ms')));

  const injection={...PATTERN,feature_name:'<img src=x onerror=alert(1)>'};
  ui.setPayloads([{...BASE,items:[injection]}]);
  await ui.submit();
  assert(ui.get('pieCards').children[0].children[0].textContent.includes('<img'));
  assert.equal(ui.get('pieCards').children[0].children[0].children.length,0,
    'Source content must be text only, never an interpreted node');

  ui.setPayloads([{...BASE,next_cursor:'opaque-cursor'},BASE]);
  await ui.submit();
  assert.equal(ui.get('pieNext').disabled,false);
  await ui.next();
  assert(ui.requested.at(-1).url.includes('cursor=opaque-cursor'));
  assert.equal(ui.get('piePageIndex').textContent,'Страница 2');
  await ui.back();
  assert(!ui.requested.at(-1).url.includes('cursor='));
  assert.equal(ui.get('piePageIndex').textContent,'Страница 1');

  ui.setPayloads([{status:503,data:{
    ok:false,state:'not_ready',error:{message:'PIE права не подтверждены'}
  }}]);
  await ui.submit();
  assert.equal(ui.get('pieError').hidden,false);
  assert.equal(ui.get('pieResults').hidden,true,
    'Previously visible source must disappear on denied refresh');
  assert.equal(ui.get('pieMetrics').hidden,true);
  assert.equal(ui.get('pieCards').children.length,0);
  assert.equal(ui.get('pieConnection').dataset.state,'error');

  ui.setPayloads([{...BASE,items:[],state:'not_ready'}]);
  await ui.submit();
  assert.equal(ui.get('pieResults').hidden,false);
  assert.equal(ui.get('pieCount').textContent,'0');
  assert(ui.get('pieCards').children[0].textContent.includes('Нет доступных'));

  ui.get('pieView').value='insights';
  ui.setPayloads([{...BASE,view:'insights',items:[{
    series_id:'b'.repeat(64),revision:2,
    verification_state:'candidate_checked_for_review',
    internal_review_event_count:3,insight_text_available:false,
    can_publish:false,causal_claim:false,independent_holdout_confirmed:false,
  }]}]);
  await ui.submit();
  assert.equal(ui.get('pieResults').hidden,false);
  assert(ui.get('pieCards').children[0].children.some(x=>
    String(x.textContent).includes('Публикация не разрешена')));

  ui.get('pieView').value='overview';
  ui.setPayloads([{...BASE,items:[{...PATTERN,can_publish:true}]}]);
  await ui.submit();
  assert.equal(ui.get('pieError').hidden,false,
    'Unexpected published row cannot be displayed');
  assert.equal(ui.get('pieCards').children.length,0);

  ui.setPayloads([{...BASE,items:[{...PATTERN,causal_claim:true}]}]);
  await ui.submit();
  assert.equal(ui.get('pieResults').hidden,true,
    'Unexpected causal row must not be visualized');

  ui.setPayloads([{...BASE,items:[{...PATTERN,independent_holdout_confirmed:true}]}]);
  await ui.submit();
  assert.equal(ui.get('pieResults').hidden,true,
    'Unverified external holdout claim must not render');

  ui.get('pieStatus').value='not_an_enum';
  await ui.submit();
  assert.equal(ui.get('pieError').hidden,false);
  const before=ui.requested.length;
  assert(ui.get('pieError').textContent.includes('статус'));
  assert.equal(ui.requested.length,before);
  ui.get('pieStatus').value='';
  ui.get('piePlatform').value='youtube;drop';
  await ui.submit();
  assert.equal(ui.requested.length,before);

  const deep=studio({search:'?project='+ID});
  await tick();await tick();
  assert.equal(deep.requested.length,1);
  assert.equal(deep.get('pieProject').value,ID);
  console.log('PIE-MOD-34 Studio UI: HTML wiring, exact scoped GET, XSS text nodes, charts, pagination, revocation, unavailable/empty and unsafe filters PASS (synthetic).');
}
tests().catch(error=>{console.error(error);process.exitCode=1;});
