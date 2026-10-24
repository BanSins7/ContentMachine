'use strict';
/* Research File Library, explicit user-triggered local file operations only. */
(() => {
  const MAX_FILE_BYTES = 10 * 1024 * 1024;
  const MIME_ACCEPT = '.pdf,.doc,.docx,.txt,.md,.csv,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg';
  const fileKey = id => '/api/projects/' + encodeURIComponent(id) + '/research-files';
  async function api(url, payload) {
    const opts = payload === undefined ? {cache:'no-store'} : {
      method:'POST', headers:{'Content-Type':'application/json','X-ContentMachine':'1'},body:JSON.stringify(payload)
    };
    const response=await fetch(url,opts);
    const body=await response.json();
    if(!response.ok || !body.ok) throw Error(body.error || 'Ошибка '+response.status);
    return body;
  }
  const span=(label,cls='')=>{const e=document.createElement('span');e.textContent=label;if(cls)e.className=cls;return e;};
  const button=(label,fn)=>{const e=document.createElement('button');e.type='button';e.className='project-text-button';e.textContent=label;e.addEventListener('click',fn);return e;};
  function chooser({title,accept=MIME_ACCEPT,onFile}) {
    const picker=document.createElement('input');picker.type='file';picker.accept=accept;picker.hidden=true;
    picker.addEventListener('change',async()=>{const file=picker.files?.[0];picker.value='';if(file) await onFile(file);});
    const trigger=button(title,()=>picker.click());return {picker,trigger};
  }
  async function encodeFile(file,expected_updated_at) {
    if(!file.size || file.size>MAX_FILE_BYTES)throw Error('Файл должен быть не больше 10 МБ.');
    const base64=await new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onerror=()=>reject(Error('Не удалось прочитать документ.'));
      reader.onload=()=>resolve(String(reader.result).split(',')[1]||'');
      reader.readAsDataURL(file);
    });
    return {name:file.name,data_base64:base64,uploaded_by:'local_user',...(expected_updated_at?{expected_updated_at}:{})};
  }
  function render(parent,project){
    const pid=project.id;
    const block=document.createElement('section');block.className='project-detail-field';
    const title=document.createElement('h3');title.textContent='Библиотека исследований';
    const subtitle=document.createElement('p');
    subtitle.textContent='Новые файлы сохраняются с историей версий. Анализ ИИ не запускается автоматически.';
    const feedback=document.createElement('p');feedback.setAttribute('role','status');feedback.className='project-files-hint';
    const actions=document.createElement('div');actions.className='project-wizard-actions';
    const list=document.createElement('div');list.className='project-upload-list';
    block.append(title,subtitle,actions,feedback,list);parent.append(block);
    let busy=false;
    async function perform(work){
      if(busy)return;busy=true;feedback.textContent='Выполняется операция…';
      try{await work();feedback.textContent='Данные сохранены.';await refresh();}
      catch(error){feedback.textContent=error.message||'Ошибка сохранения файла.';}
      finally{busy=false;}
    }
    const add=chooser({title:'+ Добавить файл',onFile:file=>perform(async()=>api(fileKey(pid),await encodeFile(file)))});
    actions.append(add.trigger,add.picker);
    actions.append(button('Импорт прежних вложений',()=>perform(()=>api(fileKey(pid)+'/import-legacy',{}))));
    async function refresh(){
      try {
        const result=await api(fileKey(pid)+'?include_archived=1');
        list.replaceChildren();
        if(!result.files.length){list.append(span('Библиотека пуста. Можно добавить PDF или DOCX.'));return;}
        for(const file of result.files){
          const row=document.createElement('div');row.className='project-upload-row';
          const name=document.createElement('strong');name.textContent=file.display_name;
          row.append(name,span(' · версия '+file.version_no+' · '+(file.byte_size/1024).toFixed(1)+' КБ · '+file.storage_status));
          if(file.archived_at)row.append(span(' · архив'));
          const menu=document.createElement('div');menu.className='project-wizard-actions';
          const down=document.createElement('a');down.className='project-file-download';
          down.textContent='Скачать';down.href=fileKey(pid)+'/'+encodeURIComponent(file.file_id)+'/download';menu.append(down);
          const replace=chooser({title:'Заменить',onFile:f=>perform(async()=>api(fileKey(pid)+'/'+encodeURIComponent(file.file_id)+'/replace',await encodeFile(f,file.updated_at)))});
          if(!file.archived_at)menu.append(replace.trigger,replace.picker);
          menu.append(button('Версии',async()=>{
            const previous=row.querySelector('[data-file-versions]');if(previous){previous.remove();return;}
            try{
              const res=await api(fileKey(pid)+'/'+encodeURIComponent(file.file_id)+'/versions');
              const versions=document.createElement('div');versions.dataset.fileVersions='1';
              for(const item of res.versions){
                const itemLink=document.createElement('a');itemLink.className='project-file-download';
                itemLink.href=fileKey(pid)+'/'+encodeURIComponent(file.file_id)+'/download?version='+item.version_no;
                itemLink.textContent='Версия '+item.version_no+' · '+item.content_sha256.slice(0,12)+' · '+item.original_name;
                versions.append(itemLink,document.createTextNode(' '));
              }
              row.append(versions);
            }catch(error){feedback.textContent=error.message;}
          }));
          menu.append(button(file.archived_at?'Восстановить':'Архивировать',()=>perform(()=>
            api(fileKey(pid)+'/'+encodeURIComponent(file.file_id)+'/'+(file.archived_at?'restore':'archive'),{expected_updated_at:file.updated_at}))));
          row.append(menu);list.append(row);
        }
      }catch(error){list.replaceChildren(span('Библиотека недоступна: '+error.message));}
    }
    refresh();
  }
  window.ContentMachineResearchFiles={render};
})();
