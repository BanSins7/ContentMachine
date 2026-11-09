"use strict";
/* GA21: render remote fields with textContent only; no dynamic HTML,
   browser persistence, third-party scripts or raw media paths. */
(() => {
  const get = id => document.getElementById(id);
  const params = new URLSearchParams(window.location.search);
  const validProject = x => /^[a-f0-9]{32}$/.test(x);
  const validRun = x => /^[a-f0-9-]{36}$/.test(x);
  const state = {project:"", run:"", stepsCursor:null, eventsCursor:null};
  const setText = (id,value) => { get(id).textContent = String(value ?? "—"); };
  const el = (name,text,cls) => {
    const node = document.createElement(name);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };
  const endpoint = suffix => "/api/projects/" + state.project + "/group-a/v1/" + suffix;
  const announce = text => setText("notice",text);
  const time = value => {
    if (value === null || value === undefined || value === "") return "—";
    const n = Number(value);
    return Number.isFinite(n) && n >= 0 ? n.toFixed(3) + "с" : "—";
  };
  async function api(path,body) {
    const opts = {method:body===undefined?"GET":"POST",cache:"no-store",
      credentials:"same-origin",headers:{"Accept":"application/json"}};
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-ContentMachine"] = "1";
      opts.body = JSON.stringify(body);
    }
    const response = await fetch(endpoint(path),opts);
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(String(data.error?.code || "ga_unavailable"));
    }
    return data;
  }
  function disableActions(disabled) {
    for (const id of ["start","pause","resume","cancel"]) get(id).disabled = disabled;
  }
  function clearSections() {
    for (const id of ["steps","events","summary","timeline","slides"]) get(id).replaceChildren();
    setText("run-state","—");setText("step-count","—");
    setText("delivery-state","—");setText("coverage-state","—");
    setText("measurement-type","Нет опубликованного снимка");
    state.stepsCursor = null; state.eventsCursor = null;
    get("more-steps").hidden = true;get("more-events").hidden = true;
    disableActions(true);
  }
  function brief(item,details="",status="") {
    const li = el("li");
    const box=el("div");
    box.append(el("strong",item),el("small",details));
    li.append(box,el("span",status,"status"));
    return li;
  }
  function field(name,value) {
    const div=el("div",null,"field");
    div.append(el("span",name),el("strong",value === null || value === undefined ? "Не измерено" : value));
    return div;
  }
  function line(host,start,end,label) {
    const box=el("div",null,"line");
    box.append(el("span",time(start)+" – "+time(end)),el("div",label));
    host.append(box);
  }
  async function loadRuns() {
    const project=get("project").value.trim();
    if (!validProject(project)) {
      announce("Укажите корректный 32-символьный ID проекта.");
      return;
    }
    state.project = project;
    state.run="";
    clearSections();
    announce("Проверяем доступ и актуальность QDP...");
    const select=get("run");
    select.replaceChildren(el("option","— Выберите запуск —"));
    select.firstChild.value="";
    const items=await api("runs?limit=25");
    for (const r of items.items) {
      const o=el("option",(r.run_id.slice(0,8)+" · "+r.state+" · "+r.content_kind));
      o.value=r.run_id;
      select.append(o);
    }
    const ready=await api("capabilities");
    setText("readiness",ready.api_state === "host_mounted"?"API подключён":"API не готов");
    announce(items.items.length ? "Выберите запуск или откройте первый." : "Для проекта нет доступных запусков.");
    if (items.items.length) { select.value=items.items[0].run_id; await chooseRun(); }
  }
  async function chooseRun() {
    state.run=get("run").value;
    clearSections();
    if (!validRun(state.run)) return;
    await Promise.all([overview(),steps(false),events(false),measurements()]);
  }
  async function overview() {
    const data=await api("runs/"+state.run);
    setText("run-state",data.run.state);
    const n=Object.values(data.steps || {}).reduce((a,b)=>a+Number(b||0),0);
    const completed=Number(data.steps?.completed||0);
    setText("step-count",completed+" / "+n);
    setText("delivery-state",Object.entries(data.delivery||{}).map(([k,v])=>k+":"+v).join(" · ")||"Нет доставки");
    disableActions(false);
    get("start").disabled=!["queued","partial"].includes(data.run.state);
    get("pause").disabled=!["queued","running","partial"].includes(data.run.state);
    get("resume").disabled=get("pause").disabled;
    get("cancel").disabled=!["queued","running","partial"].includes(data.run.state);
    announce("Запуск: "+data.run.run_id.slice(0,8)+" · "+data.run.state);
  }
  async function steps(more) {
    const route="runs/"+state.run+"/steps?limit=20"+
      (more&&state.stepsCursor?"&cursor="+encodeURIComponent(state.stepsCursor):"");
    const data=await api(route);
    if(!more)get("steps").replaceChildren();
    for (const row of data.items) {
      const details="Попыток "+row.attempts+" / "+row.max_attempts+
        " · CPU "+(row.cpu_slots??"—")+" / GPU "+(row.gpu_slots??"—")+
        " · резерв "+(row.reserved_cpu_slot_sec??"—")+" слот·с";
      const li=brief(row.step_key,details,row.state);
      if(row.state==="failed"){
        const btn=el("button","Повторить","ghost");
        btn.type="button";btn.addEventListener("click",()=>perform("retry",{step_key:row.step_key}));
        li.append(btn);
      }
      get("steps").append(li);
    }
    state.stepsCursor=data.next_cursor;
    get("more-steps").hidden=!state.stepsCursor;
  }
  async function events(more) {
    const route="runs/"+state.run+"/events?limit=20"+
      (more&&state.eventsCursor?"&cursor="+encodeURIComponent(state.eventsCursor):"");
    const data=await api(route);
    if(!more)get("events").replaceChildren();
    for(const e of data.items){
      get("events").append(brief(e.kind,e.created_at,"#"+e.event_id));
    }
    state.eventsCursor=data.next_cursor;
    get("more-events").hidden=!state.eventsCursor;
  }
  async function measurements() {
    const data=await api("runs/"+state.run+"/measurements?limit=25");
    const sum=get("summary"),timeline=get("timeline"),slides=get("slides");
    sum.replaceChildren();timeline.replaceChildren();slides.replaceChildren();
    if(!data.available){
      setText("coverage-state","Не подтверждено");
      setText("measurement-type","Нет текущего завершённого A003/A004");
      timeline.append(el("p","Измерения ожидают подтверждения источника.","empty"));
      return;
    }
    setText("measurement-type",data.analysis_type+" · #"+data.ga_result_id);
    const summary=data.summary||{};
    for(const [key,value] of Object.entries(summary)) sum.append(field(key,value));
    const statuses=["speech_status","screen_text_status","audio_status","slide_status","text_status"]
      .filter(k=>k in summary).map(k=>summary[k]);
    setText("coverage-state",statuses.every(s=>s==="complete")?"Полное":"Частичное / неизвестно");
    for(const [kind,label] of [["scenes","Сцена"],["speech","Речь"],["screen_text","Экранный текст"],["audio_events","Аудиособытие"]]){
      for(const part of (data[kind]||[])){
        line(timeline,part.start_sec,part.end_sec,
          label+(part.scene_index?" №"+part.scene_index:"")+(part.event_type?" · "+part.event_type:""));
      }
    }
    if(!timeline.childNodes.length)timeline.append(el("p","Нет подтверждённых временных окон.","empty"));
    for(const slide of (data.slides||[])){
      const li=el("div",null,"line");
      li.append(el("span","Слайд "+slide.slide_index),
                el("div",slide.kind+" · "+(slide.text_status||"неизвестно")+
                   " · слов "+(slide.screen_word_count??"не измерено")+
                   " · длительность "+time(slide.duration_sec)));
      slides.append(li);
    }
  }
  async function perform(action,body={}) {
    if(!state.run)return;
    if(action==="cancel"&&!window.confirm("Отменить выполнение? Завершённые результаты останутся в журнале."))return;
    try {
      disableActions(true);
      const response=await api("runs/"+state.run+"/"+action,body);
      announce("Действие: "+response.state+"; данные обновляются.");
      await chooseRun();
    }catch(e){
      announce("Действие отклонено: "+e.message);
      await overview().catch(()=>disableActions(true));
    }
  }
  async function safe(action) {try {await action();}catch(err){announce("Нет доступа или данные недоступны: "+err.message);}}
  get("load").addEventListener("click",()=>safe(loadRuns));
  get("refresh").addEventListener("click",()=>safe(()=>state.run?chooseRun():loadRuns()));
  get("run").addEventListener("change",()=>safe(chooseRun));
  get("start").addEventListener("click",()=>perform("start"));
  get("pause").addEventListener("click",()=>perform("pause"));
  get("resume").addEventListener("click",()=>perform("resume"));
  get("cancel").addEventListener("click",()=>perform("cancel"));
  get("more-steps").addEventListener("click",()=>safe(()=>steps(true)));
  get("more-events").addEventListener("click",()=>safe(()=>events(true)));
  const initial=params.get("project_id");
  if(initial&&validProject(initial)){get("project").value=initial;safe(loadRuns);}
})();
