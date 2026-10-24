/* QDP operator panel. Only existing local API: no TikTok, OpenRouter, media download. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let previewFor = '';
  let selectedRun = '';
  const endpoint = suffix => '/api/projects/' + encodeURIComponent($('qdpProject').value) +
    '/qualified-data/' + suffix;
  const text = (id,value) => {$(id).textContent=value;};
  async function api(url, body) {
    const response = await fetch(url, body===undefined ? {cache:'no-store'} : {
      method:'POST',
      headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(body)
    });
    const data = await response.json();
    if(!response.ok || !data.ok) {
      const err=data.error;
      throw Error(typeof err==='object' ? err.message : err || 'HTTP '+response.status);
    }
    return data;
  }
  function resetPreview() {
    previewFor='';
    $('qdpStart').disabled=true;
    text('qdpPreview','Сначала выбери проект и введи ID завершённого SRE Run.');
  }
  function config() {
    const age=$('qdpMaxAge').value.trim();
    if(age && (!/^\d+$/.test(age) || Number(age)<1 || Number(age)>365))
      throw Error('Возраст публикаций: от 1 до 365 дней или пустое поле.');
    return {max_age_days:age ? Number(age) : null,
            require_published_at:$('qdpRequireDate').checked};
  }
  function uuid(value){return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);}
  async function loadProjects() {
    const out=await api('/api/projects');
    const chooser=$('qdpProject');
    const old=chooser.value;
    chooser.replaceChildren();
    for(const item of out.projects || []) {
      if(!/^[0-9a-f]{32}$/.test(item.id)) continue;
      const option=document.createElement('option');
      option.value=item.id;
      option.textContent=item.name || item.id;
      chooser.append(option);
    }
    if(Array.from(chooser.options).some(x=>x.value===old)) chooser.value=old;
    if(!chooser.value) text('qdpPreview','Создай проект, чтобы использовать QDP.');
    await history();
  }
  async function preview() {
    const run=$('qdpSreRun').value.trim();
    if(!uuid(run)) throw Error('Нужен точный UUID завершённого SRE Run.');
    config();
    const data=await api(endpoint('preview?sre_run_id='+encodeURIComponent(run)));
    previewFor=run;
    $('qdpStart').disabled=data.totals.qualified===0;
    text('qdpPreview',
      'SRE: '+run+'\nQualified: '+data.totals.qualified+
      ' · Human Review: '+data.totals.needs_review+
      ' · Excluded: '+data.totals.excluded+
      '\n0 paid calls, 0 media downloads. Enrichment source rights are checked again by worker.');
  }
  async function create() {
    const run=$('qdpSreRun').value.trim();
    if(previewFor!==run) throw Error('Повтори предварительную проверку SRE.');
    const data=await api(endpoint('runs'),{
      sre_run_id:run,
      config:config(),
      idempotency_key:'qdp-ui-'+crypto.randomUUID()
    });
    text('qdpPreview','Run '+data.run_id+' поставлен в локальную очередь.\nQualified: '+
      data.totals.qualified+' · 0 внешних запросов.');
    selectedRun=data.run_id;
    await history();
    await details();
  }
  function clear(element){element.replaceChildren();}
  async function history() {
    const target=$('qdpRuns');
    clear(target);
    if(!$('qdpProject').value)return;
    const data=await api(endpoint('runs'));
    for(const run of data.runs || []) {
      const button=document.createElement('button');
      button.type='button';
      button.className='button';
      button.textContent=(run.status || '?')+' · ' + run.total_qualified+
        ' qualified · '+run.id.slice(0,8);
      button.addEventListener('click',async()=>{
        selectedRun=run.id;
        await safe(details);
      });
      target.append(button);
    }
  }
  async function details() {
    if(!selectedRun)return;
    const data=await api(endpoint('runs/'+encodeURIComponent(selectedRun)));
    const target=$('qdpItems');
    clear(target);
    text('qdpDetailSummary',
      'Run '+data.run.id+' · '+data.run.status+
      ' · '+data.run.total_qualified+' qualified · '+
      data.run.total_review+' review · '+data.run.total_excluded+' excluded.');
    for(const item of data.items || []) {
      const p=document.createElement('p');
      p.className='settings-description';
      const groups=Object.entries(item.groups).map(([key,value])=>
        key+':'+value.state+(value.reason_code?' ('+value.reason_code+')':'')).join(' · ');
      p.textContent='Video '+item.video_id+' · '+item.status+
        (item.reason_code?' · '+item.reason_code:'')+
        (item.metadata?.title?' · '+item.metadata.title:'')+
        (groups?' · '+groups:'');
      target.append(p);
    }
  }
  async function safe(fn) {
    try {
      text('qdpError','');
      await fn();
    } catch(e) {
      text('qdpError',String(e.message || e).slice(0,400));
    }
  }
  $('qdpProject').addEventListener('change',()=>{
    selectedRun='';
    resetPreview();
    safe(history);
  });
  for(const id of ['qdpSreRun','qdpMaxAge','qdpRequireDate']){
    $(id).addEventListener('input',resetPreview);
    $(id).addEventListener('change',resetPreview);
  }
  $('qdpPreviewButton').addEventListener('click',()=>safe(preview));
  $('qdpStart').addEventListener('click',()=>safe(create));
  $('qdpRefresh').addEventListener('click',()=>safe(async()=>{await history();await details();}));
  document.addEventListener('cm-qdp-open',()=>safe(loadProjects));
  resetPreview();
})();
