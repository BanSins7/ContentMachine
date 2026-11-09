import { useCallback, useEffect, useState } from 'react';
import {
  ArrowDownToLine, ArrowUpRight, ChevronRight, Database, Images,
  LoaderCircle, RefreshCw, Search, UsersRound,
} from 'lucide-react';

const api='/api/studio/accounts';

export type SavedAccount = {
  id:string;
  username:string;
  profile_url:string;
  run_count:number;
  completed_carousels:number;
  unique_attempted_carousels:number;
  latest_run_id:string;
  latest_run_state:string;
  latest_run_created_at:string;
  latest_snapshot_posts:number|null;
};

type MasterSummary = {
  account:{id:string;username:string;profile_url:string};
  cumulative:{
    run_count:number;
    completed_carousels:number;
    incomplete_carousels:number;
    unique_attempted_carousels:number;
    source_run_ids:string[];
  };
  runs:Array<{
    id:string;created_at:string;state:string;
    completed_in_run:number;failed_in_run:number;
    skipped_existing_in_run:number;provider:string;model:string;
  }>;
  latest_profile_snapshot:{
    run_id:string;created_at:string;post_count:number;snapshot_id:string|null;
  }|null;
  group_analytics:{
    groups:Array<{
      id:string;label:string;
      metrics:{post_count:number;median_views:number|null;weighted_er_by_views_pct:number|null};
    }>;
  };
};

async function read<T>(url:string):Promise<T>{
  const result=await fetch(url,{cache:'no-store'});
  const json:unknown=await result.json();
  if(!result.ok) throw new Error((json as {error?:string}).error||'Не удалось получить аккаунты.');
  return json as T;
}

function count(value:number|null|undefined):string {
  return typeof value==='number' ? value.toLocaleString('ru-RU') : '—';
}

function date(value:string):string{
  return new Date(value).toLocaleString('ru-RU',{day:'2-digit',month:'short',year:'numeric'});
}

export function AccountLibrary({
  onChoose, onOpenRun
}:{
  onChoose:(profileUrl:string)=>void;
  onOpenRun:(runId:string)=>void;
}) {
  const [accounts,setAccounts]=useState<SavedAccount[]>([]);
  const [selected,setSelected]=useState('');
  const [report,setReport]=useState<MasterSummary|null>(null);
  const [detailLoading,setDetailLoading]=useState(false);
  const [filter,setFilter]=useState('');
  const [busy,setBusy]=useState(true);
  const [error,setError]=useState('');

  const reload=useCallback(async()=>{
    setBusy(true);setError('');
    try{
      const data=await read<{accounts:SavedAccount[]}>(api);
      setAccounts(data.accounts);
      setSelected(old=>data.accounts.some(x=>x.id===old)?old:(data.accounts[0]?.id||''));
    }catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  },[]);

  useEffect(()=>{void reload();},[reload]);
  useEffect(()=>{
    let live=true;
    setReport(null);
    setError('');
    setDetailLoading(Boolean(selected));
    if(selected){
      void read<MasterSummary>(api+'/'+encodeURIComponent(selected))
        .then(x=>{if(live)setReport(x);})
        .catch(e=>{if(live)setError((e as Error).message);})
        .finally(()=>{if(live)setDetailLoading(false);});
    }
    return ()=>{live=false;};
  },[selected]);

  const visible=accounts.filter(x=>x.username.toLowerCase().includes(filter.toLowerCase()));
  return <section className="accounts-page" aria-labelledby="accounts-heading">
    <div className="eyebrow"><span className="eyebrow-line"/> СОХРАНЕНО ЛОКАЛЬНО</div>
    <h1 id="accounts-heading">Аккаунты<span className="accent-dot">.</span></h1>
    <p className="accounts-description">Выбирай ранее собранные TikTok-профили, просматривай накопленную
      аналитику и продолжай исследование. Открытие аккаунта не расходует токены или кредиты.</p>
    <div className="accounts-grid">
      <section className="panel accounts-index" aria-label="Сохранённые аккаунты">
        <div className="panel-heading">
          <div><span className="section-index">01 / БИБЛИОТЕКА</span><h2>Мои профили</h2></div>
          <button className="icon-button" type="button" title="Обновить" onClick={()=>{void reload();}}>
            <RefreshCw size={16}/>
          </button>
        </div>
        <div className="input-wrap accounts-search"><Search size={17} className="input-icon"/>
          <input value={filter} onChange={e=>setFilter(e.target.value)}
            placeholder="Найти TikTok-аккаунт" aria-label="Найти TikTok-аккаунт"/>
        </div>
        {busy?<div className="empty compact"><LoaderCircle className="spin" size={20}/>Загрузка...</div>:
          visible.length ? <div className="accounts-list">
            {visible.map(x=><button type="button" key={x.id}
              className={'accounts-entry '+(selected===x.id?'active':'')} onClick={()=>setSelected(x.id)}>
              <span className="accounts-avatar"><UsersRound size={18}/></span>
              <span className="accounts-entry-text">
                <strong>{x.username}</strong>
                <small>{x.completed_carousels} каруселей · {x.run_count} запусков</small>
              </span><ChevronRight size={16}/>
            </button>)}
          </div>:<div className="empty compact">
            <Images size={24}/>
            {accounts.length?'Ничего не найдено':'Аккаунтов пока нет. Запусти первый анализ профиля.'}
          </div>}
      </section>

      <section className="panel accounts-detail" aria-label="Накопленная аналитика аккаунта">
        {report && report.account.id===selected ? <>
          <div className="panel-heading">
            <div><span className="section-index">02 / ОБЩИЙ РЕЗУЛЬТАТ</span>
              <h2>{report.account.username}</h2></div>
            <Database size={19} className="quiet-icon"/>
          </div>
          <div className="account-stats">
            <div><small>Успешно проанализировано</small>
              <strong>{count(report.cumulative.completed_carousels)}</strong></div>
            <div><small>Всего уникальных каруселей</small>
              <strong>{count(report.cumulative.unique_attempted_carousels)}</strong></div>
            <div><small>Запусков</small><strong>{count(report.cumulative.run_count)}</strong></div>
            <div><small>Публикаций в последнем снимке</small>
              <strong>{count(report.latest_profile_snapshot?.post_count)}</strong></div>
          </div>
          <p className="accounts-caveat">Общая аналитика объединяет карусели без повторных TikTok ID.
            Просмотры аккаунта сравниваются только с последним сохранённым снимком Bright Data,
            без смешивания статистики разных дат.</p>
          <div className="accounts-downloads">
            <a className="download" href={api+'/'+selected+'/download/json'} download>
              <ArrowDownToLine size={15}/> Скачать единый JSON
            </a>
            <a className="download" href={api+'/'+selected+'/download/groups'} download>
              <ArrowDownToLine size={15}/> Группы и батчи JSON
            </a>
          </div>
          <button className="start-button account-continue" type="button"
            onClick={()=>onChoose(report.account.profile_url)}>
            <Images size={17}/> Выбрать аккаунт для нового анализа
          </button>

          <div className="accounts-subheading">Распределение по слайдам</div>
          <div className="accounts-groups">
            {report.group_analytics.groups.map(group=><div className="accounts-group" key={group.id}>
              <span>{group.label}</span>
              <strong>{group.metrics.post_count} постов</strong>
              <small>Медиана просмотров: {count(group.metrics.median_views)}</small>
            </div>)}
          </div>
          <div className="accounts-subheading">История запусков</div>
          <div className="accounts-runs">
            {report.runs.slice().reverse().map(run=><button type="button"
              key={run.id} onClick={()=>onOpenRun(run.id)}>
              <span><b>{date(run.created_at)}</b>
                <small>{run.completed_in_run} завершено · {run.provider} · {run.state}</small>
              </span><ArrowUpRight size={15}/>
            </button>)}
          </div>
        </>:detailLoading ? <div className="empty" role="status">
          <LoaderCircle size={24} className="spin"/><strong>Загружаем аналитику аккаунта…</strong>
          <span>Читаем сохранённые данные, без повторных платных запросов.</span>
        </div>:<div className="empty">
          <Database size={24}/>
          <strong>{selected?'Данные выбранного аккаунта недоступны':'Выбери сохранённый аккаунт'}</strong>
          <span>{selected?'Проверь подключение к локальному серверу и повтори обновление.':
            'Единый JSON будет доступен без повторного сбора.'}</span>
        </div>}
      </section>
    </div>
    {error&&<p className="notice" role="alert">{error}</p>}
  </section>;
}
