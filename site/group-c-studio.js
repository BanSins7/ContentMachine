'use strict';
/* GC-MOD-27. A read-only, authorized Studio. No C7 arithmetic or fake data. */
(function (root) {
  const STUDIO_VERSION = 'group-c-studio/v1';
  const PROJECT = /^[0-9a-f]{32}$/;
  const VIDEO = /^[1-9][0-9]*$/;
  const DECIMAL = /^[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$/;
  const API_SCHEMA = 'group-c-http/v1';
  const BASE = '/api/group-c/v1/projects/';
  const PARTS = Object.freeze(['creator', 'growth', 'engagement', 'freshness']);
  const VALID = 'valid_current';

  function validIds(projectId, videoId) {
    if (typeof projectId !== 'string' || !PROJECT.test(projectId)) throw new Error('Укажите корректный ID проекта (32 строчных hex-символа).');
    if (typeof videoId !== 'string' || !VIDEO.test(videoId) || videoId.length > 19) throw new Error('Укажите корректный videos.id.');
    const n = BigInt(videoId);
    if (n >= 9223372036854775808n) throw new Error('ID публикации вне допустимого диапазона.');
    return {projectId, videoId};
  }

  function formatDecimal(raw) {
    if (typeof raw !== 'string' || !DECIMAL.test(raw) || raw.length > 110) return '—';
    const [whole, fraction] = raw.replace(/^\+/, '').split('.');
    const sign = whole.startsWith('-') ? '−' : '';
    const integer = (sign ? whole.slice(1) : whole).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    const suffix = fraction ? ',' + fraction.replace(/0+$/, '') : '';
    return sign + integer + (suffix === ',' ? '' : suffix);
  }

  function formatCount(raw) {
    const s = String(raw);
    if (!/^[0-9]{1,19}$/.test(s)) return '—';
    return s.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  function formatUTC(raw) {
    if (typeof raw !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:/.test(raw)) return '—';
    const d = new Date(raw);
    if (!Number.isFinite(d.getTime())) return '—';
    return new Intl.DateTimeFormat('ru-RU',{
      timeZone:'UTC',year:'numeric',month:'short',day:'2-digit',
      hour:'2-digit',minute:'2-digit'
    }).format(d) + ' UTC';
  }

  function checkedStudio(input, projectId, videoId) {
    if (!input || input.schema_version !== STUDIO_VERSION ||
        input.project_id !== projectId || String(input.video_id) !== videoId ||
        !Array.isArray(input.timeline) || !Array.isArray(input.cohorts) ||
        !Array.isArray(input.limitations)) {
      throw new Error('Ответ Studio не соответствует ожидаемой версии или проекту.');
    }
    if (input.state !== VALID) {
      // Never render stale numeric values, even if a malicious/mismatched
      // producer happens to include them in an unavailable response.
      return {...input, score:null, timeline:[], cohorts:[],
        configuration:null, approval:null};
    }
    const s=input.score;
    if (!s || s.scale !== 'unbounded_not_0_to_100' ||
        s.metric_kind !== 'weighted_components_not_probability' ||
        typeof s.breakout_score !== 'string' || !DECIMAL.test(s.breakout_score) ||
        !Array.isArray(s.components) || s.components.length !== 4 ||
        !PARTS.every((name,i) => s.components[i].key === name &&
          typeof s.components[i].value === 'string' && DECIMAL.test(s.components[i].value))) {
      throw new Error('Неполное подтверждение исходного C7: числовой результат скрыт.');
    }
    const ids = input.timeline.map(p => String(p.snapshot_id));
    if (new Set(ids).size !== ids.length || input.timeline.length > 48 ||
        input.timeline.some(p => typeof p.observed_at !== 'string' ||
          typeof p.views !== 'number' || !Number.isSafeInteger(p.views) ||
          p.views < 0 || p.quality_status !== 'ok')) {
      throw new Error('Некорректные наблюдения временного ряда.');
    }
    return input;
  }

  function chartGeometry(timeline, width=760, height=280) {
    const points = timeline.map(p => ({
      value: Number(p.views),date:Date.parse(p.observed_at),raw:p
    })).filter(p => Number.isSafeInteger(p.value) && p.value>=0 &&
       Number.isFinite(p.date));
    if (points.length < 2) return null;
    if (points.some((p,i) => i>0 && p.date <= points[i-1].date)) return null;
    const lo=points[0].date,hi=points[points.length-1].date;
    const max=Math.max(1,...points.map(p=>p.value));
    const left=62,right=18,top=17,bottom=34;
    const spanW=width-left-right,spanH=height-top-bottom;
    const coords=points.map(p=>({
      x:left+(p.date-lo)/(hi-lo)*spanW,
      y:top+(1-p.value/max)*spanH,
      raw:p.raw,
    }));
    if (coords.some(p=>!Number.isFinite(p.x)||!Number.isFinite(p.y))) return null;
    return {coords,left,top,spanH,spanW,bottom,max,
            labels:[max,max*.75,max*.5,max*.25,0]};
  }

  function componentWidths(components) {
    const nums=components.map(p=>Number(p.value));
    if (nums.some(v=>!Number.isFinite(v))) return [];
    const max=Math.max(...nums.map(Math.abs),0);
    if (max===0) return components.map(()=>0);
    return nums.map(v=>Math.min(100,Math.max(0,Math.abs(v)/max*100)));
  }

  const deps=(typeof document!=='undefined' && typeof window!=='undefined')
    ? {document,window,fetch:root.fetch&&root.fetch.bind(root)} : null;

  function init() {
    if (!deps) return;
    const doc=deps.document, pick=id=>doc.getElementById(id);
    const connect=pick('gcConnectForm'),dash=pick('gcDashboard');
    if (!connect || !dash) return;
    const refs={
      project:pick('gcProject'),video:pick('gcVideo'),token:pick('gcBearer'),
      load:pick('gcLoad'),message:pick('gcMessage'),score:pick('gcScore'),
      flag:pick('gcScoreFlag'),metric:pick('gcMetricId'),
      videoId:pick('gcVideoId'),sourceState:pick('gcSourceState'),
      sourceVersion:pick('gcSourceVersion'),qualityState:pick('gcQualityState'),
      chart:pick('gcChart'),chartEmpty:pick('gcChartEmpty'),chartWrap:pick('gcChartWrap'),
      chartFirst:pick('gcChartFirst'),chartLast:pick('gcChartLast'),
      points:pick('gcPoints'),parts:pick('gcComponents'),cohorts:pick('gcCohorts'),
      config:pick('gcConfig'),approval:pick('gcApproval'),reviewFlag:pick('gcApprovalFlag'),
      quality:pick('gcQuality'),limitations:pick('gcLimitations')
    };
    const qs=new URLSearchParams(deps.window.location.search);
    if (qs.has('project')&&PROJECT.test(qs.get('project'))) refs.project.value=qs.get('project');
    if (qs.has('video')&&VIDEO.test(qs.get('video'))) refs.video.value=qs.get('video');
    // Do not accept bearer_token or csrf from URL query, fragments, storage,
    // cookies or postMessage. Only explicit password control in this tab.
    let generation=0,abort=null;
    function status(message,level='info') {
      refs.message.textContent=message;
      refs.message.dataset.level=level;
    }
    function text(id,value) {refs[id].textContent=value==null?'—':String(value);}
    function add(node,tag,content,className) {
      const el=doc.createElement(tag);
      if (className) el.className=className;
      if (content!==undefined&&content!==null) el.textContent=String(content);
      node.appendChild(el);
      return el;
    }
    function clear(el){while(el.firstChild)el.removeChild(el.firstChild);}
    function description(el,rows) {
      clear(el);
      for(const [label,value] of rows) {
        add(el,'dt',label);
        add(el,'dd',value==null?'—':String(value));
      }
    }
    function renderGraph(timeline) {
      clear(refs.chart);clear(refs.points);
      const g=chartGeometry(timeline);
      refs.chartWrap.hidden=!g;refs.chartEmpty.hidden=!!g;
      if(!g)return;
      const NS='http://www.w3.org/2000/svg';
      function svg(tag,attributes,textValue) {
        const n=doc.createElementNS(NS,tag);
        Object.entries(attributes).forEach(([k,v])=>n.setAttribute(k,String(v)));
        if(textValue!==undefined) n.textContent=textValue;
        refs.chart.appendChild(n);return n;
      }
      g.labels.forEach((v,i)=>{
        const y=g.top+i*g.spanH/4;
        svg('line',{x1:g.left,y1:y,x2:g.left+g.spanW,y2:y,class:'gc-gridline'});
        svg('text',{x:4,y:y+4},formatCount(Math.round(v)));
      });
      svg('line',{x1:g.left,y1:g.top+g.spanH,x2:g.left+g.spanW,y2:g.top+g.spanH,class:'gc-axis'});
      const coords=g.coords.map(p=>p.x.toFixed(2)+','+p.y.toFixed(2)).join(' ');
      const [first,last]=[g.coords[0],g.coords[g.coords.length-1]];
      svg('polygon',{points:coords+' '+last.x.toFixed(2)+','+(g.top+g.spanH)+' '+
        first.x.toFixed(2)+','+(g.top+g.spanH),class:'gc-area'});
      svg('polyline',{points:coords,class:'gc-path'});
      g.coords.forEach(p=>{
        const circle=svg('circle',{cx:p.x,cy:p.y,r:4.6,class:'gc-dot'});
        const title=doc.createElementNS(NS,'title');
        title.textContent=formatUTC(p.raw.observed_at)+' · '+formatCount(p.raw.views)+' views';
        circle.appendChild(title);
      });
      text('chartFirst',formatUTC(first.raw.observed_at));
      text('chartLast',formatUTC(last.raw.observed_at));
      g.coords.forEach((p,i)=>{
        const item=add(refs.points,'div',null,'gc-point');
        add(item,'span',formatUTC(p.raw.observed_at)+' · ');
        add(item,'strong',formatCount(p.raw.views));
        if(p.raw.is_current)add(item,'span',' · актуальный');
      });
    }
    function renderParts(parts){
      clear(refs.parts);
      if(!parts) {
        add(refs.parts,'div','Числовой результат недоступен.','gc-empty');
        return;
      }
      const widths=componentWidths(parts);
      parts.forEach((p,i)=>{
        const item=add(refs.parts,'div',null,'gc-part');
        const label=add(item,'div',null,'gc-part-label');
        add(label,'span',p.label||p.key);
        add(label,'strong',formatDecimal(p.value));
        const track=add(item,'div',null,'gc-track');
        const fill=add(track,'div',null,'gc-fill');
        fill.dataset.index=String(i);
        fill.style.width=(widths[i]||0).toFixed(2)+'%';
        fill.setAttribute('aria-label',String(p.label)+': '+formatDecimal(p.value));
      });
    }
    function renderCohorts(cohorts) {
      clear(refs.cohorts);
      if(!cohorts.length){
        add(refs.cohorts,'div','Нет доступных подтверждённых когорт.','gc-empty');return;
      }
      cohorts.forEach(c=>{
        const row=add(refs.cohorts,'div',null,'gc-row');
        const a=add(row,'div');
        add(a,'strong',c.label||c.purpose);
        add(a,'small','Freeze #'+c.id+' · версия метрики '+c.source_metric_version);
        const v=add(row,'div',null,'gc-row-value');
        add(v,'div',formatCount(c.member_count)+' публикаций');
        if(c.median_views!==null&&c.median_views!==undefined)
          add(v,'small','Медиана '+formatDecimal(c.median_views));
      });
    }
    function render(d) {
      dash.hidden=false;
      const valid=d.state===VALID&&d.score;
      const score=valid?d.score:null;
      text('videoId',formatCount(d.video_id));
      text('score',score?formatDecimal(score.breakout_score):'—');
      refs.flag.textContent=valid?'Проверен':'Недоступен';
      refs.flag.dataset.kind=valid?'good':'bad';
      text('metric',score?'metric #'+score.calculated_metric_id:'Нет доступного результата');
      text('sourceState',valid?'Проверен':'Ограничен');
      text('sourceVersion',score?'Политика #'+score.source_policy_version_id+' · '+score.metric_version:
        'Источник или расчёт не подтверждён');
      text('qualityState',d.quality&&d.quality.status?'Качество: '+d.quality.status:'Нет подтверждения качества');
      renderGraph(valid?d.timeline:[]);
      renderParts(score?score.components:null);
      renderCohorts(valid?d.cohorts:[]);
      const c=valid?d.configuration:null;
      description(refs.config,[
        ['Идентификатор',c?'#'+c.id:null],
        ['Версия формулы',c&&c.calculation_version],
        ['α (сглаживание)',c&&formatDecimal(c.alpha)],
        ['Байесовский k',c&&formatDecimal(c.bayesian_k)],
        ['Half-life (часов)',c&&formatDecimal(c.freshness_half_life_h)],
        ['Активна сейчас',c?(c.is_active?'Да':'Историческая версия'):null],
      ]);
      const a=valid?d.approval:null;
      refs.reviewFlag.textContent=a&&a.decision==='approved'?'Утверждено':'Не проверено';
      description(refs.approval,[
        ['Решение',a&&a.decision==='approved'?'Утверждено человеком':null],
        ['Рецензент',a&&a.reviewer_id],
        ['Источник личности',a&&a.identity_provider],
        ['ID решения',a&&a.review_decision_id],
        ['Дата решения',a&&formatUTC(a.decided_at)],
        ['Активация',a&&a.activation_event_id],
      ]);
      description(refs.quality,[
        ['Статус',d.quality&&d.quality.status],
        ['Причина',d.reason_code||(d.quality&&d.quality.reason_code)||'Не выявлена'],
        ['Запуск',d.run_id],
        ['Версия схемы',d.schema_version],
        ['Проверенный снимок',score&&score.snapshot_id],
      ]);
      clear(refs.limitations);
      d.limitations.forEach(item=>add(refs.limitations,'li',item));
      if(valid)status('Данные получены. Просмотры и компоненты C7 проверены по текущим правам источника и когорт.','success');
      else status('Сохранённый результат недоступен: '+(d.reason_code||d.state)+'. Числовые поля скрыты.','error');
    }
    connect.addEventListener('submit',async event=>{
      event.preventDefault();
      const my=++generation;
      if(abort)abort.abort();
      abort=new AbortController();
      dash.hidden=true;
      refs.load.disabled=true;
      status('Проверяю доступ и загружаю Group C…');
      try {
        if(!deps.fetch)throw new Error('HTTP-клиент недоступен.');
        const ids=validIds(refs.project.value.trim(),refs.video.value.trim());
        const token=refs.token.value;
        if(!/^[A-Za-z0-9._~+/-]{24,4096}$/.test(token))
          throw new Error('Введите действующий токен доступа без префикса Bearer.');
        const url=BASE+ids.projectId+'/videos/'+ids.videoId+'/studio';
        const response=await deps.fetch(url,{
          method:'GET',
          cache:'no-store',
          credentials:'omit',
          redirect:'error',
          headers:{Authorization:'Bearer '+token,Accept:'application/json'},
          signal:abort.signal,
        });
        const contentType=response.headers.get('Content-Type')||'';
        if(!contentType.startsWith('application/json'))
          throw new Error('Сервер вернул неожиданный формат ответа.');
        const payload=await response.json();
        if(my!==generation)return;
        if(!response.ok){
          const code=payload&&payload.error&&payload.error.code;
          throw new Error('API отклонил запрос ('+response.status+'): '+(code||'request_rejected'));
        }
        if(!payload||payload.schema_version!==API_SCHEMA)
          throw new Error('Версия HTTP API несовместима со Studio.');
        render(checkedStudio(payload.data,ids.projectId,ids.videoId));
      }catch(error){
        if(my!==generation)return;
        if(error&&error.name==='AbortError')return;
        status(error&&error.message||'Ошибка чтения данных Group C.','error');
      }finally{
        if(my===generation)refs.load.disabled=false;
      }
    });
  }

  const pure={validIds,formatDecimal,formatCount,formatUTC,chartGeometry,
              componentWidths,checkedStudio,STUDIO_VERSION};
  if(typeof module!=='undefined'&&module.exports)module.exports=pure;
  if(deps){
    if(deps.document.readyState==='loading')
      deps.document.addEventListener('DOMContentLoaded',init,{once:true});
    else init();
  }
})(typeof globalThis!=='undefined'?globalThis:this);
