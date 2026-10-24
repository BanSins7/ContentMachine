'use strict';
/* Stage13: persisted per-source activity dashboard. Never starts jobs on mount. */
(() => {
  const el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const names={web_fetch:'Чтение сайта',document_parse:'Текст / страницы PDF-DOCX',
    video_download:'Загрузка видео',asr:'Транскрипция',ocr:'OCR',
    vision:'Vision',timeline_fusion:'Таймлайн',evidence_normalization:'Evidence'};
  const states={registered:'Источник зарегистрирован (обработка не запущена)',
    queued:'В очереди, результат ещё не готов',processing:'В работе',
    ready:'Есть сохранённое Evidence',partial:'Частичный результат',
    processed_without_evidence:'Обработка есть, проверяемое Evidence пока отсутствует',
    completed:'Этап выполнен',failed:'Ошибка задания',unavailable:'Недоступно',
    cancelled:'Отменено',needs_review:'Нужна проверка человека'};
  const errors={access_denied:'Источник ограничил доступ',
    unsupported_format:'Неподдерживаемый формат файла',no_speech:'Речь не обнаружена',
    budget_required:'Требуется предварительное разрешение бюджета',
    insufficient_rights:'Источник недоступен для выбранной операции',
    truncated_data:'Материал неполный',stale_snapshot:'Контекст устарел',
    asset_unavailable:'Локальный MP4 отсутствует',timeout:'Операция превысила время',
    source_not_found:'Источник недоступен',model_response_invalid:'Модель вернула неподходящий формат'};
  async function api(path){
    const response=await fetch(path,{cache:'no-store'});
    let data;try{data=await response.json();}catch(_){throw Error('Неверный ответ сервера.');}
    if(!response.ok||!data.ok)throw Error(data.error||'Ошибка получения статусов.');
    return data;
  }
  function mount(parent,pid){
    const section=el('section');section.className='settings-card project-activity-section';
    section.append(el('h3','Путь данных · обработка источников'));
    section.append(el('p','Текущий статус читается из PostgreSQL. Зарегистрированный URL или задание в очереди — ещё не готовый результат.'));
    const notice=el('p');notice.setAttribute('role','status');
    const list=el('div'),actions=el('div');actions.className='psi-actions';
    const refresh=el('button','Обновить статусы');refresh.type='button';refresh.className='project-text-button';
    const more=el('button','Следующие источники');more.type='button';more.className='project-text-button';more.hidden=true;
    actions.append(refresh,more);section.append(actions,notice,list);parent.append(section);
    let cursor=0,serial=0,loading=false,store=[];
    function paint(){
      list.replaceChildren();
      if(!store.length){list.append(el('p','Нет источников. Добавь URL или документ в проект.'));return;}
      for(const src of store){
        const row=el('div');row.className='psi-row';
        const title=el('strong',(src.label||src.source_type)+' · '+(states[src.display_state]||src.display_state));
        row.append(title);
        row.append(el('p','Тип: '+src.source_type+' · текущая версия: '+
          (src.active_source_version_id||'не получена')+
          ' · Evidence: '+src.evidence_count));
        if(src.jobs.length){
          const details=el('details');
          details.append(el('summary','История этапов ('+src.jobs.length+')'));
          for(const job of src.jobs){
            const item=el('div');item.className='psi-row';
            const step=names[job.stage]||job.stage;
            item.append(el('strong',step+' · '+(states[job.state]||job.state)));
            item.append(el('p','Попытка '+job.attempts+'/'+job.max_attempts+
              ' · создано '+job.created_at+
              ' · биллинг: '+job.charge_state+
              (job.cost_actual_usd===null?'':' · $'+job.cost_actual_usd)));
            if(job.error_code)item.append(el('p','Причина: '+(errors[job.error_code]||job.error_code)+
              (job.error_message?' · '+job.error_message:'')));
            details.append(item);
          }
          row.append(details);
        }
        const jump=el('a','Открыть управление источником ↓');
        jump.href='#cm-project-source-'+src.source_id;
        jump.className='project-file-download';row.append(jump);
        list.append(row);
      }
    }
    async function load(reset=false){
      if(loading)return;
      if(reset){cursor=0;store=[];}
      const run=++serial;loading=true;refresh.disabled=true;more.disabled=true;
      notice.textContent='Читаем сохранённые задания…';
      try{
        const data=await api('/api/projects/'+encodeURIComponent(pid)+
          '/source-activity?offset='+cursor+'&limit=30');
        if(run!==serial)return;
        store.push(...data.items);cursor+=data.items.length;
        more.hidden=!data.more;
        notice.textContent='Источников: '+data.count+
          ' · отображено '+store.length+
          '. Автоматический запуск и платные запросы отключены.';
        paint();
      }catch(err){notice.textContent=err.message;}
      finally{loading=false;refresh.disabled=false;more.disabled=false;}
    }
    refresh.addEventListener('click',()=>load(true));
    more.addEventListener('click',()=>load(false));
    load(true);
  }
  window.ContentMachineProjectActivity={mount};
})();