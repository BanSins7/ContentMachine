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
        for(const seg of output.segments){
          const row=document.createElement('p');row.className='project-files-hint';
          row.textContent=JSON.stringify(seg.locator)+' — '+seg.text.slice(0,400);
          preview.append(row);
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
