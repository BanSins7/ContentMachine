'use strict';
/* Display metadata only. A visited stage is never treated as a completed run. */
((root) => {
  const stages = [
    {id:'foundation',name:'Research Foundation',short:'Основа проекта',group:0,tag:'Контекст',title:'Хорошее исследование начинается с контекста.',description:'Собери всё, что нужно знать о продукте, аудитории и рынке, в одном проекте.',input:['Описание продукта','Аудитория и конкуренты','Документы, ссылки и референсы'],work:['Профиль и цели проекта','Библиотека источников','Извлечение и проверка контекста'],output:['Сохранённый профиль','Контекст с происхождением данных'],surface:'project',action:'Открыть основу проекта'},
    {id:'rtb',name:'Research Taxonomy',short:'Карта исследования',group:0,tag:'RTB',title:'Преврати контекст в карту тем.',description:'Построй дерево направлений и гипотез, проверь факты и утверди структуру исследования.',input:['Профиль и контекст проекта','Подтверждённые факты'],work:['Извлечение фактов','Построение и углубление дерева','Проверка и утверждение'],output:['Опубликованная таксономия','Категории и гипотезы для поиска'],surface:'rtb',action:'Открыть карту исследования'},
    {id:'search',name:'Search Planner',short:'План поиска',group:0,tag:'SP',title:'Найди правильные слова для поиска.',description:'Преврати утверждённые гипотезы в поисковые запросы и подготовь их передачу сборщику.',input:['Опубликованная таксономия','Языки, рынки и лимиты'],work:['Генерация и расширение запросов','Проверка покрытия и дубликатов','Публикация набора запросов'],output:['QuerySet и план поиска','Подтверждённая передача Collector'],surface:'search',action:'Открыть Search Planner'},
    {id:'collector',name:'Video Collector',short:'Сбор видео',group:1,tag:'VC',title:'Собери материал для исследования.',description:'Найди видео по опубликованным запросам, сохрани результаты и проследи их путь до отбора.',input:['Опубликованные запросы','Источники и параметры сбора'],work:['Подготовка и запуск заданий','Получение и нормализация ответов','Сохранение видео и происхождения'],output:['Каталог собранных видео','Данные для проверки релевантности'],surface:'collector',action:'Открыть Video Collector'},
    {id:'sre',name:'Semantic Relevance',short:'Отбор по смыслу',group:1,tag:'SRE',title:'Оставь то, что отвечает исследованию.',description:'Сопоставь найденные видео с гипотезами и проверь решения о релевантности.',input:['Сохранённые видео','Утверждённые категории'],work:['Подготовка LIVE-плана','Проверка смыслового соответствия','Просмотр результатов и ревью'],output:['Проверенные решения','Видео для Qualified Set'],surface:'sre',action:'Открыть SRE Studio'},
    {id:'qdp',name:'Qualified Data',short:'Подготовка данных',group:1,tag:'QDP',title:'Подготовь отобранные видео к анализу.',description:'Сформируй Qualified Set, обогати данные и подготовь независимые входы для групп анализа.',input:['Завершённый SRE LIVE run','Параметры отбора'],work:['Сборка Qualified Set','Обогащение и проверка данных','Подготовка входов A / B / C'],output:['Закреплённый manifest','Подготовленные данные для анализа'],surface:'qdp',action:'Открыть Qualified Data'},
    {id:'group-a',name:'Group A',short:'Медиаанализ',group:2,tag:'A',title:'Разбери видео на измеримые сигналы.',description:'Извлеки речь, экранный текст, кадры и аудиосигналы с привязкой к исходному медиа.',input:['Подготовленные медиа QDP','Проверенный media manifest'],work:['Технические параметры и кадры','Речь, OCR и субтитры','Аудиосигналы и снимок анализа'],output:['Измерения и наблюдения','Доказательства для Group B'],surface:null},
    {id:'group-b',name:'Group B',short:'Разбор креатива',group:2,tag:'B',title:'Пойми, как устроен креатив.',description:'Разбери хук, формат, историю, визуальный стиль, монтаж, субтитры и призыв к действию.',input:['Закреплённый результат Group A','Текстовые и визуальные доказательства'],work:['Хук, формат и повествование','Визуальный стиль и монтаж','Субтитры, CTA и темы'],output:['Структурированные признаки','Классификация с доказательствами'],surface:null},
    {id:'group-c',name:'Group C',short:'Метрики результата',group:2,tag:'C',title:'Оцени результат в правильном контексте.',description:'Сравни просмотры, рост и вовлечённость с проверенными снимками метрик и сопоставимыми группами.',input:['Снимки метрик и публикации','Утверждённые параметры и когорты'],work:['Динамика просмотров','Сглаженная вовлечённость','Свежесть и Breakout Score'],output:['Воспроизводимые C0–C7 метрики','Результаты для Unified Analytics'],surface:null},
    {id:'analytics',name:'Unified Analytics',short:'Единая аналитика',group:3,tag:'UAE',title:'Собери полную картину по каждому видео.',description:'Объедини признаки, измерения и метрики в воспроизводимую аналитическую выборку.',input:['Результаты групп A / B / C','Квалификация и происхождение данных'],work:['Проверка совместимости результатов','Сборка и ранжирование выборки','Фиксация снимка исследования'],output:['Единый аналитический набор','Закреплённая выборка для PIE'],surface:null},
    {id:'insights',name:'Patterns & Insights',short:'Паттерны и инсайты',group:3,tag:'PIE',title:'Найди закономерности, которым можно доверять.',description:'Исследуй связь креативных приёмов и результата, оцени устойчивость и сформулируй проверяемые выводы.',input:['Воспроизводимая выборка UAE','Признаки и сопоставимые результаты'],work:['Поиск и сравнение паттернов','Оценка эффекта и неопределённости','Проверка и формулировка инсайтов'],output:['Проверенные паттерны','Инсайты с границами применимости'],surface:null},
  ].map((stage,index)=>Object.freeze({...stage,index,number:String(index+1).padStart(2,'0')}));
  const groups=Object.freeze(['Исследование','Сбор и отбор','Анализ контента','Результаты']);
  function route(hash) {
    const [path,query,...extra]=(hash||'').split('?');
    const match=/^#stage\/([a-z-]+)(?:\/(work|results))?$/.exec(path);
    const fallback={stage:null,tab:'overview'};
    if(!match||!stages.some(s=>s.id===match[1])||extra.length)return fallback;
    if(query!==undefined){
      const params=new URLSearchParams(query);
      if(match[2]!=='results'||![...params].length)return fallback;
      for(const [key,value] of params){
        if(params.getAll(key).length!==1)return fallback;
        if(key==='run'){
          if(!['rtb','collector','qdp'].includes(match[1])||!(match[1]==='collector'?/^[1-9][0-9]*$/:/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/).test(value))return fallback;
        }else if(key==='mode'){if(match[1]!=='rtb'||value!=='deep-v2')return fallback;}
        else if(key==='deep'){if(match[1]!=='rtb'||value!=='1')return fallback;}
        else return fallback;
      }
    }
    return {stage:match[1],tab:match[2]||'overview'};
  }
  function projectId(value){return typeof value==='string'&&/^[0-9a-f]{32}$/.test(value);}
  function errorText(body,status) {
    const value=body?.error;
    return (typeof value==='string'?value:value?.message||value?.code)||'HTTP '+status;
  }
  // Exact server states, not inferred progress or a record of visited pages.
  function runState(stage,row) {
    if(!row)return {kind:'idle',label:'Не запускался'};
    const value=String(row.deep_status||row.status||row.state||row.stage||'');
    if(['fact_review','topic_review','review1','review2','needs_review','human_review'].includes(value)||
      (value==='critic_pending'&&row.generation_reviewed===false))return {kind:'review',label:'Ожидает проверки'};
    if(value.endsWith('_running')||['running','queued','processing','leased'].includes(value))
      return {kind:'running',label:value==='queued'?'В очереди':'Выполняется'};
    if(['failed','uncertain','paused_budget','paused_price','blocked_quality','needs_attention','error'].includes(value))
      return {kind:'error',label:value==='uncertain'?'Расход не подтверждён':value==='paused_budget'?'Нужен бюджет':value==='paused_price'?'Изменилась цена':'Ошибка / остановлен'};
    if(['cancelled','canceled'].includes(value))return {kind:'idle',label:'Отменён'};
    if((stage==='rtb'&&value==='approved'&&!row.deep_status)||['completed','succeeded','published','ready'].includes(value))
      return {kind:'complete',label:'Завершён'};
    if(value.endsWith('_pending')||['created','held','pending'].includes(value))return {kind:'pending',label:'Готов к продолжению'};
    return {kind:'unknown',label:value||'Состояние не указано'};
  }
  const roles=Object.freeze([
    {id:'fact_extraction',name:'Извлечение фактов'},
    {id:'taxonomy_generation',name:'Генерация тем'},
    {id:'taxonomy_critic',name:'Проверка качества'}
  ]);
  function roleState(run,role){
    const attempt=(run.stages||[]).find(x=>x.role===role);
    if(attempt)return runState('attempt',attempt);
    return {kind:'idle',label:'Не запускался'};
  }
  const api=Object.freeze({stages:Object.freeze(stages),groups,route,projectId,errorText,runState,roleState,roles});
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.ContentMachineWorkspaceCore=api;
})(typeof window==='object'?window:globalThis);
