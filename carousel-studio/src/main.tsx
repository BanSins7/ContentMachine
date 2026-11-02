import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowDownToLine, ArrowLeft, ArrowUpRight, Check, ChevronRight, CircleHelp,
  Database, FileArchive, FileJson, FileText, Images, LoaderCircle, Play, RefreshCw, Search,
  ShieldCheck, Sparkles, X } from 'lucide-react';
import './style.css';

type Mode = 'post' | 'profile';
type Status = 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'interrupted';
type RunSummary = {
  id: string; source_kind: Mode; source_input: string; state: Status;
  phase: string; progress: number; total: number; completed: number;
  failed: number; error?: string; created_at: string;
};
type Log = {created_at: string; level: string; message: string};
type ResultItem = {
  native_id: string; canonical_url: string; state: Status | 'downloading' | 'analyzing';
  asset_count: number; error: string | null;
};
type RunDetail = RunSummary & {
  items: ResultItem[]; events: Log[]; downloads: Record<string,boolean>
};
type Readiness = {brightdata: boolean; gemini: boolean; model: string;
  storage: string; profile_scope: string; platform: string};

const api = '/api/studio';
const initialRun = new URLSearchParams(window.location.search).get('run') || '';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const result = await fetch(url, { cache: 'no-store', ...init });
  const json: unknown = await result.json();
  if (!result.ok) {
    const error = json as {error?: string};
    throw new Error(error.error || `HTTP ${result.status}`);
  }
  return json as T;
}

function readableDate(value?: string): string {
  if (!value) return '—';
  return new Date(value).toLocaleString('ru-RU', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' });
}

function short(value: string, n = 40): string {
  return value.length > n ? value.slice(0, n - 3) + '...' : value;
}

function formatStatus(state: string): string {
  const map: Record<string,string> = {
    queued:'В очереди', running:'Выполняется',completed:'Готово',
    partial:'Есть ошибки',failed:'Ошибка',interrupted:'Прервано',
    downloading:'Загрузка',analyzing:'Анализ Gemini'
  };
  return map[state] || state;
}

function Download({runId,type,ready,children}: {
  runId: string; type: string; ready: boolean; children: React.ReactNode
}) {
  return (
    <a className={`download ${ready ? '' : 'disabled'}`}
       aria-disabled={!ready}
       href={ready ? `${api}/runs/${runId}/download/${type}` : undefined}
       download={ready ? '' : undefined}>
      {children}<ArrowDownToLine size={15} strokeWidth={1.8}/>
    </a>
  );
}

function App() {
  const [mode,setMode] = useState<Mode>('post');
  const [source,setSource] = useState('');
  const [activeId,setActiveId] = useState(initialRun);
  const [runs,setRuns] = useState<RunSummary[]>([]);
  const [detail,setDetail] = useState<RunDetail | null>(null);
  const [readiness,setReadiness] = useState<Readiness | null>(null);
  const [pending,setPending] = useState(false);
  const [error,setError] = useState('');
  const [expanded,setExpanded] = useState<string>('');

  const refresh = useCallback(async () => {
    try {
      const payload = await request<{runs:RunSummary[]}>(`${api}/runs`);
      setRuns(payload.runs);
      if (activeId) {
        const found = await request<RunDetail>(`${api}/runs/${activeId}`);
        setDetail(found);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  },[activeId]);

  useEffect(() => {
    request<Readiness>(`${api}/readiness`).then(setReadiness).catch(()=>{});
  },[]);
  useEffect(() => {
    void refresh();
    const handle = window.setInterval(() => { void refresh(); }, 1700);
    return () => window.clearInterval(handle);
  },[refresh]);

  function selectRun(id: string) {
    setActiveId(id);
    setExpanded('');
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('run',id);
    else url.searchParams.delete('run');
    window.history.replaceState(null,'',url);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setPending(true);
    try {
      const response = await request<{id:string}>(`${api}/runs`,{
        method:'POST',
        headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
        body:JSON.stringify({kind:mode,value:source.trim()})
      });
      selectRun(response.id);
      setSource('');
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  const running = detail && (detail.state==='queued' || detail.state==='running');
  const progress = detail ? Math.min(Math.max(detail.progress,0),100) : 0;
  const completed = detail && (detail.state==='completed' || detail.state==='partial');
  const current = runs.find(r=>r.id===activeId);
  const statusTitle = detail?.phase || current?.phase || 'Нет активных запусков';
  const logs = detail?.events || [];

  return (
    <div className="shell">
      <aside className="side">
        <a className="brand" href="/" aria-label="Content Machine главная">
          <span className="brand-symbol">C<span>·</span>M</span>
          <span className="brand-text">Content<span>Machine</span></span>
        </a>
        <div className="side-links">
          <a className="side-link" href="/"><ArrowLeft size={17}/> На платформу</a>
          <span className="side-link selected"><Images size={17}/> TikTok Studio</span>
        </div>
        <div className="side-bottom">
          <div className="platform"><span className="online-dot"/> Только TikTok</div>
          <div>Сохранение на этом компьютере</div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <span className="breadcrumb">Workspace <ChevronRight size={14}/> Studio</span>
          <span className="top-version">GEMINI 3 FLASH</span>
        </header>

        <section className="intro">
          <div className="eyebrow"><span className="eyebrow-line"/> RESEARCH WORKSPACE</div>
          <h1>TikTok <em>Carousel</em> Studio<span className="accent-dot">.</span></h1>
          <p>Собирайте карусели, изучайте их структуру и сохраняйте полный анализ в одном месте.</p>
        </section>

        <section className="workspace-grid">
          <div className="work-left">
            <section className="panel create-panel" aria-labelledby="create-title">
              <div className="panel-heading">
                <div><span className="section-index">01 / ИСТОЧНИК</span><h2 id="create-title">Новый анализ</h2></div>
                <Sparkles size={19} className="quiet-icon"/>
              </div>
              <div className="toggle" role="group" aria-label="Тип источника">
                <button type="button" className={mode==='post'?'active':''}
                  aria-pressed={mode==='post'} onClick={()=>setMode('post')}>Ссылка на карусель</button>
                <button type="button" className={mode==='profile'?'active':''}
                  aria-pressed={mode==='profile'} onClick={()=>setMode('profile')}>TikTok-аккаунт</button>
              </div>
              <form onSubmit={submit} className="start-form">
                <label htmlFor="source">{mode==='post'?'Ссылка на публикацию':'Имя пользователя или URL профиля'}</label>
                <div className="input-wrap"><Search size={19} className="input-icon"/>
                  <input id="source" required value={source} onChange={e=>setSource(e.target.value)}
                    autoComplete="off" spellCheck={false}
                    placeholder={mode==='post'?'https://www.tiktok.com/@username/photo/...':'@username'}/></div>
                <button className="start-button" type="submit" disabled={pending || !source.trim()}>
                  {pending?<LoaderCircle size={18} className="spin"/>:<Play size={17} fill="currentColor"/>}
                  {pending?'Создаю задание':'Запустить анализ'}
                  <ArrowUpRight size={17}/>
                </button>
              </form>
              <div className="footnote"><ShieldCheck size={15}/>
                Только публичные карусели. Без искусственного лимита на количество публикаций.
              </div>
              {readiness && (!readiness.brightdata || !readiness.gemini) &&
                <p className="notice" role="status">Для запуска потребуется настроенный серверный доступ к {[
                  !readiness.brightdata&&'Bright Data',!readiness.gemini&&'OpenRouter'].filter(Boolean).join(' и ')}.</p>}
            </section>

            <section className="panel progress-panel" aria-labelledby="progress-title">
              <div className="panel-heading">
                <div><span className="section-index">02 / ВЫПОЛНЕНИЕ</span><h2 id="progress-title">Ход работы</h2></div>
                {running?<span className="status-pill"><LoaderCircle size={13} className="spin"/> В процессе</span>:
                 completed?<span className="status-pill"><Check size={13}/> Завершено</span>:
                 <Activity size={19} className="quiet-icon"/>}
              </div>
              {detail ? <>
                <div className="progress-title"><span>{statusTitle}</span><strong>{Math.round(progress)}%</strong></div>
                <div className="track" role="progressbar" aria-valuemin={0} aria-valuemax={100}
                     aria-valuenow={progress} aria-label="Прогресс анализа"><span style={{width:progress+'%'}}/></div>
                <div className="progress-stats">
                  <div><small>Найдено</small><strong>{detail.total}</strong></div>
                  <div><small>Обработано</small><strong>{detail.completed}</strong></div>
                  <div><small>С ошибками</small><strong>{detail.failed}</strong></div>
                </div>
                {detail.error && <p className="notice" role="alert">{detail.error}</p>}
              </> : <div className="empty compact"><Activity size={24}/> Выберите источник и запустите анализ.</div>}
            </section>

            <section className="panel logs-panel" aria-labelledby="logs-title">
              <div className="panel-heading">
                <div><span className="section-index">03 / СОБЫТИЯ</span><h2 id="logs-title">Логи процесса</h2></div>
                {detail && <Download runId={detail.id} type="logs" ready={!!detail.downloads?.logs}>
                  <FileText size={15}/> Скачать
                </Download>}
              </div>
              <div className="log-list" role="log" aria-label="События выполнения">
                {logs.length ? logs.slice(-15).reverse().map((l,index)=>
                  <div className="log-row" key={index}>
                    <time>{new Date(l.created_at).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}</time>
                    <span className={l.level==='error'?'error-text':''}>{l.message}</span>
                  </div>) : <div className="empty compact">Пока нет событий</div>}
              </div>
            </section>
          </div>

          <div className="work-right">
            <section className="panel results-panel" aria-labelledby="results-title">
              <div className="panel-heading">
                <div><span className="section-index">04 / РЕЗУЛЬТАТЫ</span><h2 id="results-title">Файлы и аналитика</h2></div>
                <Database size={19} className="quiet-icon"/>
              </div>
              <div className="result-downloads">
                <div className="result-type"><FileArchive size={20}/>
                  <div><strong>Полный архив</strong><span>Исходники, аналитика, метрики</span></div>
                  {detail && <Download runId={detail.id} type="zip" ready={!!detail.downloads?.zip}>ZIP</Download>}
                </div>
                <div className="result-type"><FileJson size={20}/>
                  <div><strong>Полный анализ</strong><span>Gemini + Bright Data + показатели</span></div>
                  {detail && <Download runId={detail.id} type="json" ready={!!detail.downloads?.json}>JSON</Download>}
                </div>
                <div className="result-type"><FileText size={20}/>
                  <div><strong>История выполнения</strong><span>События и ошибки</span></div>
                  {detail && <Download runId={detail.id} type="logs" ready={!!detail.downloads?.logs}>LOG</Download>}
                </div>
              </div>
              <div className="result-divider"/>
              <div className="result-subhead"><span>Публикации</span><span>{detail?.items?.length||0}</span></div>
              <div className="items">
                {detail?.items?.length ? detail.items.map(item=>
                  <div key={item.native_id} className="item">
                    <button type="button" className="item-summary"
                      aria-expanded={expanded===item.native_id}
                      onClick={()=>setExpanded(expanded===item.native_id?'':item.native_id)}>
                      <Images size={18}/><span className="item-id">{item.native_id}</span>
                      <small>{item.asset_count} слайд.</small>
                      <span className="item-state">{formatStatus(item.state)}</span>
                      <ChevronRight size={15} className={expanded===item.native_id?'rotate':''}/>
                    </button>
                    {expanded===item.native_id && <div className="item-details">
                      <a href={item.canonical_url} target="_blank" rel="noopener noreferrer">
                        Оригинал в TikTok <ArrowUpRight size={14}/></a>
                      {item.error && <p role="alert">{item.error}</p>}
                      <span>Файлы и метрики доступны в общем ZIP и JSON.</span>
                    </div>}
                  </div>)
                : <div className="empty"><Images size={26}/>
                    <strong>Результатов пока нет</strong>
                    <span>После запуска здесь появятся найденные карусели.</span></div>}
              </div>
            </section>

            <section className="panel history-panel" aria-labelledby="history-title">
              <div className="panel-heading">
                <div><span className="section-index">СОХРАНЕНО ЛОКАЛЬНО</span><h2 id="history-title">История запусков</h2></div>
                <button className="icon-button" type="button" onClick={()=>void refresh()} title="Обновить">
                  <RefreshCw size={17}/></button>
              </div>
              {runs.length?runs.slice(0,12).map(run=>
                <button key={run.id} className={`history-row ${run.id===activeId?'current':''}`}
                  onClick={()=>selectRun(run.id)}>
                  <span className="history-icon"><Images size={17}/></span>
                  <span className="history-main"><strong>{short(run.source_input,36)}</strong>
                    <small>{readableDate(run.created_at)} · {formatStatus(run.state)}</small></span>
                  <ChevronRight size={17}/>
                </button>)
              :<div className="empty compact">История запусков появится здесь.</div>}
            </section>
          </div>
        </section>

        <footer className="footer">
          <span><span className="online-dot"/> LOCAL-FIRST · SQLite + файлы на ПК</span>
          <span><CircleHelp size={14}/> Охват аккаунта зависит от Bright Data</span>
        </footer>
        {error && <div className="toast" role="alert"><span>{error}</span><button onClick={()=>setError('')} aria-label="Закрыть"><X size={17}/></button></div>}
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
