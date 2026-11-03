import { useEffect, useMemo, useState } from 'react';
import { ArrowDownToLine, ChevronRight, Layers3, TrendingUp } from 'lucide-react';

export type MetricSummary = {
  post_count: number;
  observed: Record<string, number|null>;
  coverage: Record<string, number>;
  mean_views: number|null;
  median_views: number|null;
  weighted_er_by_views_pct: number|null;
  median_post_er_pct: number|null;
  mean_post_er_pct: number|null;
  er_eligible_posts: number;
};
export type RankedPost = {
  native_id: string; url: string; views: number|null; er_pct: number|null;
  slide_count: number; state: string; has_gemini: boolean;
};
export type RankedBatch = {
  batch_id: string; label: string; start_rank: number; end_rank: number;
  metrics: MetricSummary;
  relative_signals: {type:string;post_count:number;description:string}[];
  posts: RankedPost[];
};
export type SlideGroup = {
  id:string;label:string;metrics:MetricSummary;
  share_of_verified_carousels_pct:number|null;
  share_of_observed_channel_posts_pct:number|null;
  share_of_observed_channel_views_pct:number|null;
  view_impact_index:number|null;
  relative_signals:{type:string;post_count:number;description:string}[];
  batches:RankedBatch[];
};
export type CohortReport = {
  schema_version:string;source_kind:'post'|'profile';scope:string;
  profile_history_complete:boolean;
  best_relative_impact_group:string|null;
  channel:{
    all_posts:MetricSummary|null;
    ordinary_videos:MetricSummary|null;
    photo_posts:MetricSummary|null;
    known_views_coverage_pct:number|null;
  };
  carousel_classification:{
    with_verified_slide_count:number;
    without_verified_slide_count:number;
    number_of_processed_carousels:number;
    photo_posts_found_in_profile:number|null;
    coverage_of_profile_photo_posts_pct:number|null;
  };
  groups:SlideGroup[];
  interpretation_notes:string[];
};

function integer(value:number|null|undefined):string {
  return value == null ? '—' : Math.round(value).toLocaleString('ru-RU');
}
function percent(value:number|null|undefined):string {
  return value == null ? '—' : value.toLocaleString('ru-RU',{maximumFractionDigits:2})+'%';
}
function index(value:number|null|undefined):string {
  return value == null ? '—' : value.toLocaleString('ru-RU',{maximumFractionDigits:2})+'×';
}

export function CohortExplorer({
  report,runId,onBatch,onOpen
}:{
  report:CohortReport;runId:string;
  onBatch:(ids:string[]|null)=>void;
  onOpen:(native:string)=>void;
}) {
  const [groupId,setGroupId]=useState('6-8');
  const [batchIndex,setBatchIndex]=useState(0);
  const group=useMemo(()=>report.groups.find(x=>x.id===groupId)||report.groups[0],
    [report,groupId]);
  const batch=group?.batches?.[batchIndex]||null;
  useEffect(()=>{ setBatchIndex(0); },[groupId,report]);
  useEffect(()=>{
    onBatch(batch ? batch.posts.map(x=>x.native_id) : []);
  },[batch,onBatch]);
  const account=report.channel.all_posts;
  return <section className="panel cohort-panel" aria-label="Аналитика групп каруселей">
    <div className="panel-heading cohort-head">
      <div><span className="section-index">АНАЛИТИКА АККАУНТА</span><h2>Какая длина каруселей работает лучше?</h2></div>
      <a className="download" href={`/api/studio/runs/${runId}/download/groups`} download>
        JSON <ArrowDownToLine size={15}/>
      </a>
    </div>
    {account ? <div className="account-stats">
      <div><small>Публикаций в снимке</small><strong>{integer(account.post_count)}</strong></div>
      <div><small>Просмотров в снимке</small><strong>{integer(account.observed.views)}</strong></div>
      <div><small>Обычных видео</small><strong>{integer(report.channel.ordinary_videos?.post_count)}</strong></div>
      <div><small>Взвешенный ER</small><strong>{percent(account.weighted_er_by_views_pct)}</strong></div>
    </div> : <p className="cohort-caveat">
      Анализ отдельной публикации: сравнение со всем аккаунтом появится при запуске по профилю.
    </p>}
    <div className="cohort-cards" role="group" aria-label="Выбор группы по числу слайдов">
      {report.groups.map(g=><button
        key={g.id} type="button" aria-pressed={group?.id===g.id}
        className={`cohort-card ${group?.id===g.id?'cohort-card-active':''}`}
        onClick={()=>{setGroupId(g.id);setBatchIndex(0);}}>
        <span className="cohort-name">{g.label}</span>
        <strong>{integer(g.metrics.post_count)} <small>каруселей</small></strong>
        <span className="cohort-divider"/>
        <span>Доля каруселей <b>{percent(g.share_of_verified_carousels_pct)}</b></span>
        <span>Доля просмотров аккаунта <b>{percent(g.share_of_observed_channel_views_pct)}</b></span>
        <span>Индекс вклада <b>{index(g.view_impact_index)}</b></span>
      </button>)}
    </div>
    {report.best_relative_impact_group && <p className="cohort-finding">
      <TrendingUp size={16}/> Наибольший относительный вклад:
      <strong>{report.groups.find(g=>g.id===report.best_relative_impact_group)?.label}</strong>
      <span>по доступной статистике</span>
    </p>}
    {group && <div className="cohort-drill">
      <div className="cohort-drill-header">
        <div><span className="section-index">ВЫБРАННАЯ ГРУППА</span>
          <h3>{group.label}</h3></div>
        <span className="cohort-neutral-label"><Layers3 size={16}/> {group.batches.length} батч.</span>
      </div>
      <div className="cohort-metrics">
        <span>Медиана просмотров <strong>{integer(group.metrics.median_views)}</strong></span>
        <span>ER по просмотрам <strong>{percent(group.metrics.weighted_er_by_views_pct)}</strong></span>
        <span>Медианный ER <strong>{percent(group.metrics.median_post_er_pct)}</strong></span>
        <span>Доля публикаций аккаунта <strong>{percent(group.share_of_observed_channel_posts_pct)}</strong></span>
      </div>
      {group.batches.length ? <>
        <div className="batch-scroll" role="group" aria-label="Батчи по убыванию просмотров">
          {group.batches.map((item,i)=><button
            type="button" key={item.batch_id}
            className={`batch-tab ${batchIndex===i?'batch-active':''}`}
            aria-pressed={batchIndex===i}
            onClick={()=>setBatchIndex(i)}>
            {item.label}
          </button>)}
        </div>
        {batch && <>
          <div className="batch-summary">
            <strong>{batch.label}</strong>
            <span>Просмотры: {integer(batch.metrics.observed.views)}</span>
            <span>ER: {percent(batch.metrics.weighted_er_by_views_pct)}</span>
          </div>
          <div className="ranked-posts">
            {batch.posts.map((p,i)=><button key={p.native_id} className="ranked-post"
              type="button" onClick={()=>onOpen(p.native_id)}>
              <span className="rank-number">{batch.start_rank+i}</span>
              <span className="rank-id">№{p.native_id} <small>{p.slide_count} слайдов</small></span>
              <span className="rank-value">{integer(p.views)} <small>просм.</small></span>
              <span className="rank-er">ER {percent(p.er_pct)}</span>
              <ChevronRight size={15}/>
            </button>)}
          </div>
          {batch.relative_signals.length>0 && <p className="cohort-caveat">
            Для проверки: {batch.relative_signals.map(x=>x.description.toLowerCase()+
            ' ('+x.post_count+')').join('; ')}. Это не установленные причины.
          </p>}
        </>}
      </> : <p className="cohort-caveat">В группе пока нет каруселей с подтверждённым числом слайдов.</p>}
    </div>}
    <div className="cohort-notes">
      <p>Доли аккаунта рассчитаны по публикациям, вернувшимся в одном снимке Bright Data.
        TikTok может не предоставить всю историю. ER = (лайки + комментарии + репосты +
        сохранения) / просмотры × 100. Пропущенные метрики не считаются нулями.</p>
      {report.carousel_classification.without_verified_slide_count>0 && <p>
        Без проверенного числа слайдов: {report.carousel_classification.without_verified_slide_count}.
        Они исключены из процентного распределения по длине, но остаются в общем списке.</p>}
    </div>
  </section>;
}
