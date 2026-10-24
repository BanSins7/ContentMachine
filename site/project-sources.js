'use strict';
/* Stage 02 Reference Registry: project-scoped, purely local management UI. */
(() => {
  const TYPES = [
    ['product_website','Сайт продукта'],['competitor_website','Сайт конкурента'],
    ['competitor_account','Аккаунт конкурента'],['promoter_account','Промо-аккаунт'],
    ['user_video_reference','Референс видео'],['research_link','Исследование / статья'],
    ['other','Другая ссылка']
  ];
  const make=(tag,cls,text)=>{
    const el=document.createElement(tag);
    if(cls)el.className=cls;
    if(text!==undefined)el.textContent=text;
    return el;
  };
  async function api(url,method='GET',data){
    const response=await fetch(url,method==='GET'?{cache:'no-store'}:{
      method,headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:data===undefined?undefined:JSON.stringify(data)
    });
    let body;
    try{body=await response.json();}catch(_){throw Error('Некорректный ответ сервера');}
    if(!response.ok||!body.ok)throw Error(body.error||'Ошибка '+response.status);
    return body;
  }
  function labeledInput(container,label,tag='input'){
    const wrap=make('label','source-registry-label');
    wrap.append(make('span','',label));
    const input=make(tag,'source-registry-input');
    if(tag==='input')input.type='text';
    wrap.append(input);container.append(wrap);
    return input;
  }
  function render(root,project){
    const pid=project.id;
    const base='/api/projects/'+encodeURIComponent(pid)+'/sources';
    const card=make('section','settings-card source-registry');
    const header=make('h3','', 'Источники проекта');
    card.append(header);
    card.append(make('p','project-files-hint',
      'Ссылки хранятся отдельно от анкеты. Добавление не запускает скачивание и платный анализ.'));
    const notice=make('p','project-step-error');notice.hidden=true;notice.setAttribute('role','alert');card.append(notice);
    const result=make('div','source-registry-results');card.append(result);
    const form=make('form','source-registry-form');
    const selectLabel=make('label','source-registry-label');selectLabel.append(make('span','', 'Тип источника'));
    const kind=make('select','source-registry-input');
    for(const [id,title] of TYPES){const option=make('option','',title);option.value=id;kind.append(option);}
    selectLabel.append(kind);form.append(selectLabel);
    const url=labeledInput(form,'HTTPS-ссылка');url.placeholder='https://...';url.required=true;
    const label=labeledInput(form,'Подпись (необязательно)');
    const notes=labeledInput(form,'Комментарий (необязательно)');
    const add=make('button','button settings-secondary','Добавить источник');add.type='submit';form.append(add);
    card.append(form);
    const importButton=make('button','button settings-secondary','Импортировать ссылки из анкеты');importButton.type='button';card.append(importButton);
    const reload=make('button','project-text-button','Обновить список');reload.type='button';card.append(reload);
    root.append(card);
    const showError=message=>{notice.textContent=message||'';notice.hidden=!message;};
    const refresh=async()=>{
      const response=await api(base);
      result.replaceChildren();
      result.append(make('p','project-files-hint','Зарегистрировано: '+response.count));
      if(!response.sources.length) result.append(make('p','project-files-hint','Ссылок пока нет.'));
      for(const source of response.sources){
        const item=make('div','source-registry-entry');
        const heading=make('strong','',source.label||source.source_type);
        item.append(heading);
        const link=make('a','project-file-download',source.canonical_url);
        link.href=source.canonical_url;link.target='_blank';link.rel='noopener noreferrer';
        item.append(link);
        const origin=(source.origins||[]).map(x=>x.origin_field).join(', ');
        item.append(make('small','project-files-hint',source.link_kind+' · '+source.status+(origin?' · анкета: '+origin:'')));
        const edit=make('button','project-text-button','Изменить подпись');edit.type='button';
        edit.addEventListener('click',async()=>{
          const newLabel=window.prompt('Подпись источника',source.label||'');
          if(newLabel===null)return;
          try {await api(base+'/'+source.source_id,'PATCH',{
            label:newLabel,expected_updated_at:source.updated_at});showError('');await refresh();}
          catch(e){showError(e.message);}
        });
        const archive=make('button','project-text-button','Архивировать');archive.type='button';
        archive.addEventListener('click',async()=>{
          if(!window.confirm('Архивировать ссылку? Исходные версии сохранятся.'))return;
          try {await api(base+'/'+source.source_id,'DELETE',{
            expected_updated_at:source.updated_at});showError('');await refresh();}
          catch(e){showError(e.message);}
        });
        if(window.ContentMachineWebSources)window.ContentMachineWebSources.mount(item,pid,source);
        item.append(edit,archive);result.append(item);
      }
    };
    form.addEventListener('submit',async(e)=>{
      e.preventDefault();add.disabled=true;
      try{await api(base,'POST',{source_type:kind.value,url:url.value,label:label.value,user_notes:notes.value});
        url.value='';label.value='';notes.value='';showError('');await refresh();}
      catch(err){showError(err.message);}
      finally{add.disabled=false;}
    });
    importButton.addEventListener('click',async()=>{
      importButton.disabled=true;
      try{const out=await api(base+'/import-legacy','POST',{});
        showError('');await refresh();
        const info=make('p','project-files-hint',
          'Импорт: '+out.created+' новых ссылок, '+out.origins_added+' привязок, '+out.skipped+' повторов, '+out.warnings.length+' предупреждений, '+(out.stale_candidates||[]).length+' устаревших привязок.');
        result.prepend(info);
      }catch(e){showError(e.message);}finally{importButton.disabled=false;}
    });
    reload.addEventListener('click',()=>refresh().then(()=>showError('')).catch(e=>showError(e.message)));
    refresh().catch(e=>showError(e.message));
  }
  window.ContentMachineProjectSources={render};
})();
