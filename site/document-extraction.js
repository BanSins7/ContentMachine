'use strict';
/* Stage 04: explicit local extraction enqueue + bounded preview, no LLM calls. */
(() => {
  const URL = (pid,fid) => '/api/projects/'+encodeURIComponent(pid)+'/research-files/'+encodeURIComponent(fid);
  async function api(path,payload) {
    const options=payload===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(payload)
    };
    const response=await fetch(path,options);
    const body=await response.json();
    if(!response.ok||!body.ok)throw Error(body.error||'Ошибка '+response.status);
    return body;
  }
  function linkButton(label,action) {
    const b=document.createElement('button');b.type='button';b.className='project-text-button';
    b.textContent=label;b.addEventListener('click',action);return b;
  }
  function mount(menu,pid,file,feedback) {
    const base=URL(pid,file.file_id);
    const box=document.createElement('div');box.className='project-files-hint';
    box.setAttribute('aria-label','Извлечение текста документа');
    const state=document.createElement('p');state.textContent='Извлечение документа ещё не запускалось.';
    const actions=document.createElement('div');actions.className='project-wizard-actions';
    const preview=document.createElement('div');preview.className='project-upload-list';
    let busy=false;
    const ocr=document.createElement('input');ocr.type='checkbox';ocr.id='cm-ocr-'+file.file_id;
    const ocrLabel=document.createElement('label');ocrLabel.setAttribute('for',ocr.id);
    ocrLabel.textContent='Локальный OCR для сканов (если установлен Tesseract)';
    ocrLabel.prepend(ocr);
    const enqueue=linkButton('Извлечь текст',async()=>{
      if(busy)return;busy=true;enqueue.disabled=true;
      try {
        const result=await api(base+'/extract',{config:{ocr:ocr.checked}});
        state.textContent='Задание '+result.job_id+' · '+result.state+
          '. Для выполнения нужен локальный document worker.';
        await reload();
      }catch(error){state.textContent=error.message||'Не удалось поставить задание.';}
      finally{busy=false;enqueue.disabled=false;}
    });
    if(!file.archived_at)actions.append(ocrLabel,enqueue);
    const refresh=linkButton('Статус извлечения',()=>reload());actions.append(refresh);
    box.append(actions,state,preview);menu.append(box);
    async function display(jobId,offset=0){
      try {
        const data=await api(base+'/extractions/'+encodeURIComponent(jobId)+'?offset='+offset+'&limit=20');
        preview.replaceChildren();
        const output=data.result;
        if(!output){state.textContent='Статус: '+data.state+(data.error_code?' · '+data.error_code:'');return;}
        state.textContent='Версия '+data.file_version+' · '+data.state+' · '+
          (output.segment_count||0)+' фрагментов · '+output.tables.length+' таблиц · '+
          'engine: '+output.extraction_engine;
        if(output.warnings.length){
          const warn=document.createElement('p');warn.textContent='Покрытие: '+output.warnings.join(', ');preview.append(warn);
        }
        let quotes=[];
        if(file.source_id){
          try{
            const response=await api('/api/projects/'+encodeURIComponent(pid)+
              '/sources/'+encodeURIComponent(file.source_id)+'/evidence/items?limit=100');
            quotes=(response.items||[]).filter(x=>x.human_decision==='approved'&&
              x.is_current_version&&typeof x.exact_quote==='string'&&x.exact_quote.length>=3);
          }catch(error){preview.append(Object.assign(document.createElement('small'),{
            textContent:'Выделение цитат недоступно: '+error.message}));}
        }
        for(const seg of output.segments){
          const row=document.createElement('div');row.className='project-files-hint psi-doc-segment';
          const loc=seg.locator||{};
          const location=document.createElement('small');
          location.textContent='Страница '+(loc.page_number??loc.page??'?')+
            (loc.table_index===undefined?'':' · таблица '+loc.table_index)+
            ' · '+(loc.kind||loc.type||'текст');
          row.append(location);
          const text=String(seg.text||'').slice(0,3000);
          const matches=quotes.filter(q=>text.includes(q.exact_quote))
            .sort((a,b)=>b.exact_quote.length-a.exact_quote.length);
          const content=document.createElement('p');
          let cursor=0;
          while(cursor<text.length){
            const matched=matches.map(q=>({q,at:text.indexOf(q.exact_quote,cursor)}))
              .filter(x=>x.at>=0).sort((a,b)=>a.at-b.at)[0];
            if(!matched){content.append(document.createTextNode(text.slice(cursor)));break;}
            if(matched.at>cursor)content.append(document.createTextNode(text.slice(cursor,matched.at)));
            const highlighted=document.createElement('mark');
            highlighted.textContent=matched.q.exact_quote;
            highlighted.title='Подтверждённая цитата: '+matched.q.citation;
            content.append(highlighted);cursor=matched.at+matched.q.exact_quote.length;
          }
          row.append(content);preview.append(row);
        }
        if(output.tables?.length){
          const tables=document.createElement('details');
          const title=document.createElement('summary');
          title.textContent='Извлечённые таблицы ('+output.tables.length+')';
          tables.append(title);
          for(const table of output.tables.slice(0,12)){
            const rows=document.createElement('pre');rows.className='psi-output';
            rows.textContent=JSON.stringify(table,null,2).slice(0,8000);
            tables.append(rows);
          }
          if(output.tables.length>12)tables.append(Object.assign(document.createElement('p'),{
            textContent:'Показаны первые 12 таблиц; полный результат сохраняется в источнике.'}));
          preview.append(tables);
        }
        if(output.has_more){preview.append(linkButton('Ещё фрагменты',()=>display(jobId,offset+20)));}
      }catch(error){state.textContent=error.message||'Ошибка получения результата.';}
    }
    async function reload(){
      try {
        const result=await api(base+'/extractions');
        const latest=result.jobs[0];
        if(!latest){state.textContent='Обработка ещё не запускалась.';return;}
        state.textContent='Версия '+latest.file_version+' · '+latest.state+
          (latest.error_code?' · '+latest.error_code:'');
        preview.replaceChildren();
        if(['completed','partial','unavailable'].includes(latest.state)){
          preview.append(linkButton('Показать извлечённое',()=>display(latest.job_id)));
        }
        if(['failed','unavailable'].includes(latest.state) && latest.attempts<3){
          preview.append(linkButton('Повторить',async()=>{
            try{await api(base+'/extractions/'+latest.job_id+'/retry',{});await reload();}
            catch(error){state.textContent=error.message;}
          }));
        }
      }catch(error){state.textContent='Извлечение недоступно: '+error.message;}
    }
    reload();
  }
  window.ContentMachineDocumentExtraction={mount};
})();
