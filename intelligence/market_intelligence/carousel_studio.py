"""Local TikTok photo-carousel studio: durable jobs, Bright Data + Gemini.

Data stays on the operator's PC in marketing-qa/carousel-studio. SQLite WAL
persists runs, item metadata, event logs and output paths. Every carousel has
real ordered still images, not fabricated MP4s. A user-chosen per-run cap
(default 30) applies only after provider profile discovery; provider profile
snapshot coverage is explicitly unknown.

The user starts one paid job by clicking Start. No silent retries of an
ambiguous Bright Data trigger or an already billed Gemini call.
"""
from __future__ import annotations

import io
import json
import re
from statistics import mean,median
from decimal import Decimal
import sqlite3
import threading
import time
import uuid
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse, urlencode
import urllib.request
import urllib.error
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

from PIL import Image, ImageOps

from intelligence import config
from intelligence.market_intelligence import studio_credentials
from intelligence.market_intelligence.openrouter_credentials import get_market_openrouter_key
from intelligence.discovery.normalize import canonical_video_url
from intelligence.market_intelligence import brightdata_provider as bd
from intelligence.market_intelligence import carousel_studio_analytics as cohort
from intelligence.market_intelligence.carousel_media import (
    download_carousel, carousel_candidate, CarouselError, MAX_SLIDES)
from intelligence.market_intelligence.metric_fields import (
    numeric_fields, metric_coverage)
from intelligence.market_intelligence.tiktok_top100 import canonical_tiktok_profile, _kind
from scripts import analyze_marketing_carousel_story as story
from scripts.prepare_market_tiktok_top100 import PROFILE_DATASET

ROOT=Path(__file__).resolve().parents[2]/"marketing-qa"/"carousel-studio"
SCHEMA="mi-tiktok-carousel-studio/v1"
# Isolated Studio upgrade: do not change the archived neutral-v3 benchmark model.
STUDIO_MODEL="google/gemini-3.8-flash"
STUDIO_PRICE={
    "route":"OpenRouter Standard",
    "input_usd_per_million_tokens":0.75,
    "output_usd_per_million_tokens":3.75,
    "quote_date":"2026-11-03",
    "reference_url":"https://openrouter.ai/google/gemini-3.8-flash",
    "promotional_discount_may_expire":True,
}
RUSGPT_MODEL="anthropic/claude-haiku-5.5"
RUSGPT_PRICE={
    "route":"Rus-GPT",
    "input_rub_per_million_tokens":60.0,
    "output_rub_per_million_tokens":300.0,
    "quote_date":"2026-11-03",
    "reference_url":"https://rus-gpt.com/models",
    "may_change":True,
}
MODEL_BY_PROVIDER={"openrouter":STUDIO_MODEL,"rusgpt":RUSGPT_MODEL}

def _priced_usage(usage,provider="openrouter"):
    """Price estimate is informational; OpenRouter's reported cost is billed truth."""
    if provider not in MODEL_BY_PROVIDER:
        raise ValueError("studio_unknown_provider")
    usage=dict(usage)
    inp,out=usage.get("prompt_tokens"),usage.get("completion_tokens")
    estimate=None
    if (type(inp) is int and inp>=0 and type(out) is int and out>=0):
        cost_input,cost_output=(("60","300") if provider=="rusgpt" else ("0.75","3.75"))
        estimate=float((Decimal(inp)*Decimal(cost_input)+Decimal(out)*Decimal(cost_output))/
                       Decimal(1000000))
        estimate=round(estimate,8)
    return {**usage,
            "estimated_standard_cost_usd":estimate if provider=="openrouter" else None,
            "estimated_cost_rub":estimate if provider=="rusgpt" else None,
            "standard_reference_pricing":dict(RUSGPT_PRICE if provider=="rusgpt" else STUDIO_PRICE),
            "not_a_spend_cap":True}

ID=re.compile(r"[0-9a-f]{32}\Z")
NATIVE=re.compile(r"[0-9]{10,25}\Z")
LOCK=threading.RLock()
ACTIVE=set()
POLL_SECONDS=5
# Detail metrics are OPTIONAL; their slow provider queue must not block LLM.
DETAIL_METRICS_WAIT_SECONDS=240
# Explicit retry only: never attempt successfully analyzed or ambiguous paid items.
RETRYABLE_MODEL_ERRORS=frozenset({
    "story_model_invalid_json","story_model_top_level_fields_invalid"})
RETRYABLE_MEDIA_ERRORS=frozenset({"carousel_slide_count_unavailable"})
RETRYABLE_ERRORS=RETRYABLE_MODEL_ERRORS | RETRYABLE_MEDIA_ERRORS
RETRY_MARKER_STATE="approved_for_explicit_retry"
PROFILE_MAX_LINE=3_000_000  # per-record safety limit, NOT a limit on publications

def utc():
    return datetime.now(timezone.utc).isoformat()

def _dir():
    ROOT.mkdir(parents=True,exist_ok=True)
    return ROOT

def db():
    conn=sqlite3.connect(_dir()/"studio.sqlite3",timeout=30)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS studio_runs(
      id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source_input TEXT NOT NULL,
      max_carousels INTEGER,
      provider TEXT NOT NULL DEFAULT 'openrouter',
      model TEXT NOT NULL DEFAULT 'google/gemini-3.8-flash',
      state TEXT NOT NULL, phase TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
      total INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
      failed INTEGER NOT NULL DEFAULT 0, skipped_existing INTEGER NOT NULL DEFAULT 0,
      snapshot_id TEXT,
      error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      finished_at TEXT, output_dir TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS studio_items(
      run_id TEXT NOT NULL, native_id TEXT NOT NULL, canonical_url TEXT NOT NULL,
      state TEXT NOT NULL, metrics_json TEXT, analysis_json TEXT,
      manifest_json TEXT, asset_count INTEGER NOT NULL DEFAULT 0, error TEXT,
      PRIMARY KEY(run_id,native_id),
      FOREIGN KEY(run_id) REFERENCES studio_runs(id)
    );
    CREATE TABLE IF NOT EXISTS studio_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
      created_at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS studio_events_by_run ON studio_events(run_id,id);
    CREATE TABLE IF NOT EXISTS studio_channel_posts(
      run_id TEXT NOT NULL, native_id TEXT NOT NULL,
      kind TEXT NOT NULL, canonical_url TEXT NOT NULL,
      counts_json TEXT NOT NULL,
      PRIMARY KEY(run_id,native_id),
      FOREIGN KEY(run_id) REFERENCES studio_runs(id)
    );
    CREATE INDEX IF NOT EXISTS studio_channel_posts_by_run
      ON studio_channel_posts(run_id,kind);
    CREATE TABLE IF NOT EXISTS studio_group_analytics(
      run_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,
      FOREIGN KEY(run_id) REFERENCES studio_runs(id)
    );
    CREATE TABLE IF NOT EXISTS studio_targets(
      run_id TEXT NOT NULL,native_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
      canonical_url TEXT NOT NULL,
      PRIMARY KEY(run_id,native_id),
      UNIQUE(run_id,ordinal),
      FOREIGN KEY(run_id) REFERENCES studio_runs(id)
    );
    CREATE INDEX IF NOT EXISTS studio_targets_by_run ON studio_targets(run_id,ordinal);
    """)
    run_cols={row[1] for row in conn.execute("PRAGMA table_info(studio_runs)")}
    if "max_carousels" not in run_cols:
        conn.execute("ALTER TABLE studio_runs ADD COLUMN max_carousels INTEGER")
    if "skipped_existing" not in run_cols:
        conn.execute("ALTER TABLE studio_runs ADD COLUMN skipped_existing INTEGER NOT NULL DEFAULT 0")
    if "provider" not in run_cols:
        conn.execute("ALTER TABLE studio_runs ADD COLUMN provider TEXT NOT NULL DEFAULT 'openrouter'")
    if "model" not in run_cols:
        conn.execute("ALTER TABLE studio_runs ADD COLUMN model TEXT NOT NULL DEFAULT 'google/gemini-3.8-flash'")
    conn.commit()
    cols={row[1] for row in conn.execute("PRAGMA table_info(studio_items)")}
    if "manifest_json" not in cols:
        conn.execute("ALTER TABLE studio_items ADD COLUMN manifest_json TEXT")
        conn.commit()
    return conn

def _write_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    staged=path.with_suffix(path.suffix+".tmp")
    staged.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    staged.replace(path)

def _event(run_id,message,level="info",**updates):
    with db() as c:
        c.execute("INSERT INTO studio_events(run_id,created_at,level,message) VALUES(?,?,?,?)",
                  (run_id,utc(),level,message[:900]))
        if updates:
            cols=",".join(f"{k}=?" for k in updates)
            c.execute(f"UPDATE studio_runs SET {cols},updated_at=? WHERE id=?",
                      [*updates.values(),utc(),run_id])
    folder=_dir()/run_id
    folder.mkdir(exist_ok=True)
    with (folder/"events.jsonl").open("a",encoding="utf-8") as fp:
        fp.write(json.dumps({"at":utc(),"level":level,"message":message},
                            ensure_ascii=False)+"\n")

def _input(kind,value):
    if kind=="post":
        if type(value) is not str or len(value)>400:
            raise ValueError("Укажите ссылку на TikTok-карусель.")
        platform,native,url=canonical_video_url(value.strip())
        if platform!="tiktok" or not carousel_candidate(url) or not NATIVE.fullmatch(native):
            raise ValueError("Нужна публичная ссылка TikTok формата /@user/photo/ID.")
        return kind,url
    if kind=="profile":
        handle,url=canonical_tiktok_profile(value)
        return kind,url
    raise ValueError("Источник должен быть ссылкой или TikTok-аккаунтом.")

def start(kind,value,max_carousels=30):
    kind,normalized=_input(kind,value)
    if kind=="profile":
        if (type(max_carousels) is not int
            or not 1<=max_carousels<=5000):
            raise ValueError("Количество каруселей должно быть от 1 до 5000.")
    else:
        max_carousels=1
    if not bd.readiness()["ready"]:
        raise ValueError("Bright Data не настроен на сервере: нужны API-токен и разрешения.")
    provider=studio_credentials.provider()
    selected_model=MODEL_BY_PROVIDER[provider]
    if provider=="rusgpt":
        if not studio_credentials.token("rusgpt_api_key"):
            raise ValueError("В настройках Studio отсутствует API-ключ Rus-GPT.")
    elif not get_market_openrouter_key():
        raise ValueError("OpenRouter API-ключ недоступен в серверном окружении.")
    with LOCK:
        with db() as c:
            # Same account with unfinished durable work: point to the existing
            # run instead of billing a second Bright Data profile collection.
            existing=c.execute("""SELECT id,state FROM studio_runs
                WHERE source_kind=? AND source_input=?
                AND state IN ('paused','running','queued','interrupted')
                ORDER BY created_at DESC LIMIT 1""",(kind,normalized)).fetchone()
            if existing:
                return {"id":existing["id"],"state":existing["state"],
                        "existing_run":True,
                        "resume_available":existing["state"] in ("paused","interrupted")}
            ident=uuid.uuid4().hex
            folder=_dir()/ident
            folder.mkdir(mode=0o700)
            c.execute("""INSERT INTO studio_runs
              (id,source_kind,source_input,max_carousels,provider,model,state,phase,progress,
               created_at,updated_at,output_dir)
              VALUES(?,?,?,?,?,?,'queued','Подготовка',0,?,?,?)""",
              (ident,kind,normalized,max_carousels,provider,selected_model,utc(),utc(),str(folder)))
        _event(ident,"Задание создано. Данные сохраняются локально.")
        _thread(ident)
    return {"id":ident,"state":"queued","existing_run":False}

def _thread(ident, *, resume=False):
    with LOCK:
        if ident in ACTIVE:
            return
        ACTIVE.add(ident)
    def wrapped():
        try:
            _process(ident,resume=resume)
        except Exception as exc:
            # No secrets or provider bodies are allowed in logs.
            error=type(exc).__name__ if isinstance(exc,(urllib.error.URLError,urllib.error.HTTPError)) else str(exc)
            _event(ident,"Задание остановлено: "+error,"error",
                   state="failed",phase="Ошибка",error=error,finished_at=utc())
            _package(ident,partial=True)
        finally:
            with LOCK:
                ACTIVE.discard(ident)
    threading.Thread(target=wrapped,daemon=True,name="studio-"+ident[:8]).start()

def _bd_stream(snapshot):
    """Stream NDJSON: no in-app post limit and no 500-record JSON cap.

    Callers do not claim profile completeness: it reflects provider coverage.
    """
    if not bd.SNAPSHOT_RE.fullmatch(snapshot):
        raise ValueError("Некорректный Bright Data snapshot_id.")
    url=bd.ENDPOINT+"/datasets/v3/snapshot/"+snapshot+"?"+urlencode({"format":"ndjson"})
    req=urllib.request.Request(url,headers={
        "Authorization":"Bearer "+bd._read_only_token(),
        "Accept":"application/x-ndjson",
        "User-Agent":"ContentMachine-CarouselStudio/1",
    })
    with urllib.request.urlopen(req,timeout=180) as response:
        for line in response:
            if len(line)>PROFILE_MAX_LINE:
                raise ValueError("Одна запись Bright Data превышает безопасный размер.")
            if not line.strip():
                continue
            obj=json.loads(line)
            if type(obj) is dict:
                yield obj

def _wait(snapshot,ident,label,*,max_seconds=None):
    cycles=0
    deadline=time.monotonic()+max_seconds if max_seconds is not None else None
    while True:
        state=bd.progress(snapshot)
        if state=="ready":
            return
        if state=="failed":
            raise ValueError(label+": Bright Data сообщил об ошибке.")
        cycles+=1
        if cycles%6==1:
            _event(ident,label+": Bright Data "+state)
        if deadline is not None and time.monotonic()>=deadline:
            raise TimeoutError("brightdata_detail_metrics_wait_limit")
        time.sleep(min(POLL_SECONDS,max(0,deadline-time.monotonic()))
                   if deadline is not None else POLL_SECONDS)

def _trigger_profile(ident,profile_url):
    dataset=config.setting("CM_MARKET_TIKTOK_PROFILE_POSTS_DATASET",PROFILE_DATASET).strip()
    if not bd.DATASET_RE.fullmatch(dataset):
        raise ValueError("Некорректный ID набора данных Bright Data.")
    _event(ident,"Запуск сбора TikTok-аккаунта через Bright Data.",
           state="running",phase="Сбор аккаунта",progress=3)
    # Persist the uncertain state BEFORE the potentially billable POST.
    _event(ident,"Создание снимка: автоматический повтор при сетевой ошибке запрещён.",
           phase="Bright Data: создание снимка")
    result=bd._api("POST","/datasets/v3/trigger",bd._token(),
        query={"dataset_id":dataset,"format":"json"},
        payload=[{"url":profile_url,"start_date":""}])
    snapshot=result.get("snapshot_id") if isinstance(result,dict) else None
    if not isinstance(snapshot,str) or not bd.SNAPSHOT_RE.fullmatch(snapshot):
        raise ValueError("Неизвестный результат запуска Bright Data; автоматический повтор запрещён.")
    _event(ident,"Снимок аккаунта создан; ожидаем готовности.",
           snapshot_id=snapshot,phase="Bright Data: ожидание",progress=8)
    _wait(snapshot,ident,"Аккаунт")
    return snapshot

def _trigger_metrics(ident,urls):
    if not urls:
        return {}
    result={}
    # Bright Data allows 50 source URLs per trigger. This is an API batch size,
    # not a cap on the account's carousel count.
    for start_at in range(0,len(urls),bd.MAX_INPUTS):
        batch=urls[start_at:start_at+bd.MAX_INPUTS]
        _event(ident,"Уточнение метрик Bright Data: публикации "+
               str(start_at+1)+"–"+str(start_at+len(batch)))
        sid=bd.trigger("tiktok",batch)
        _event(ident,"Bright Data: получен снимок метрик "+sid)
        try:
            _wait(sid,ident,"Метрики",max_seconds=DETAIL_METRICS_WAIT_SECONDS)
        except TimeoutError:
            _event(ident,"Подробные метрики Bright Data не готовы за "+
                   str(DETAIL_METRICS_WAIT_SECONDS)+" секунд (снимок "+sid+
                   "). Пропускаю дополнительные запросы, продолжаю с уже "+
                   "сохранёнными метриками профиля. Повторная платная задача не создаётся.",
                   "error")
            break
        expected_ids={canonical_video_url(url)[1]:url for url in batch}
        for row in bd.download(sid):
            normalized=bd.normalize_record("tiktok",row)
            if not normalized:
                continue
            expected=expected_ids.get(normalized["native_id"])
            if expected:
                # Some providers use /video/ even for a TikTok Photo Mode
                # post. The exact native ID is the stable match key.
                normalized["url"]=expected
                result[expected]=(normalized,row,sid)
    return result

def _discover(ident,kind,source):
    if kind=="post":
        with db() as c:
            sid=c.execute("SELECT snapshot_id FROM studio_runs WHERE id=?",(ident,)).fetchone()
        return [(source,None)],"exact_post"
    snapshot=_trigger_profile(ident,source)
    handle=source.rsplit("@",1)[1].casefold()
    seen={}
    all_posts={}
    ambiguous=set()
    inspected=0
    _event(ident,"Потоковое чтение результатов аккаунта.",phase="Отбор каруселей",progress=12)
    for raw in _bd_stream(snapshot):
        inspected+=1
        url=next((raw.get(k) for k in ("url","post_url","video_url","link")
                  if isinstance(raw.get(k),str)),None)
        if not url:
            continue
        try:
            platform,native,canonical=canonical_video_url(url)
        except (ValueError,TypeError):
            continue
        path=urlparse(canonical).path
        if platform!="tiktok" or not path.casefold().startswith("/@"+handle+"/"):
            continue
        author=raw.get("author_username") or raw.get("username")
        if isinstance(author,dict):
            author=author.get("unique_id") or author.get("username")
        if isinstance(author,str) and author.lstrip("@").casefold()!=handle:
            continue
        path_kind="photo" if "/photo/" in path else "video"
        kind=_kind(raw,path_kind)
        if kind not in ("photo","video"):
            continue
        canonical=f"https://www.tiktok.com/@{handle}/"+(
            "photo" if kind=="photo" else "video")+"/"+native
        metric_keys=bd.FIELDS["tiktok"]
        counts={key:bd._count(raw,metric_keys[key])
                for key in ("views","likes","comments","shares","saves")}
        observed={
            "native_id":native,
            "kind":"photo_carousel" if kind=="photo" else "video",
            "canonical_url":canonical,
            "counts":counts,
        }
        if native in ambiguous:
            continue
        if native in all_posts:
            if observed!=all_posts[native]:
                # Conflicting provider rows must not inflate denominators.
                ambiguous.add(native)
                all_posts.pop(native,None)
                seen.pop(native,None)
            continue
        all_posts[native]=observed
        if kind=="photo":
            seen[native]=(canonical,raw)
        if inspected%500==0:
            _event(ident,"Проверено записей Bright Data: "+str(inspected)+
                   "; уникальных каруселей: "+str(len(seen)))
    # Persist ALL observed channel publications, including regular videos,
    # without downloading or submitting those videos to Gemini.
    with db() as c:
        c.executemany("""INSERT OR REPLACE INTO studio_channel_posts
            (run_id,native_id,kind,canonical_url,counts_json)
            VALUES(?,?,?,?,?)""",[
                (ident,p["native_id"],p["kind"],p["canonical_url"],
                 json.dumps({"observed_counters":p["counts"]},ensure_ascii=False))
                for p in all_posts.values()])
    _event(ident,"Bright Data вернул "+str(inspected)+" записей: "+
           str(len(seen))+" каруселей и "+
           str(sum(p["kind"]=="video" for p in all_posts.values()))+
           " обычных видео. История аккаунта может быть неполной.",
           phase="Отбор каруселей")
    return list(seen.values()),"returned_profile_snapshot_only"

def _select_profile_carousels(targets,limit):
    """Pick up to limit with the latest reliable provider dates first.

    Unknown dates keep provider order and go after dated publications.
    Do not use TikTok IDs as fabricated timestamps. Account denominator is
    kept intact by _discover; only detail queries/media/LLM are capped.
    """
    if type(limit) is not int or not 1<=limit<=5000:
        raise ValueError("studio_invalid_carousel_limit")
    def time_of(raw):
        if type(raw) is not dict:
            return None
        for key in ("create_time","date_posted","post_date_created"):
            value=raw.get(key)
            if value is None:
                continue
            iso=bd._date(value)
            if iso:
                try:
                    return datetime.fromisoformat(iso).timestamp()
                except ValueError:
                    pass
        return None
    dated=[(i,pair,time_of(pair[1])) for i,pair in enumerate(targets)]
    dated.sort(key=lambda row:(row[2] is None,
                               -(row[2] if row[2] is not None else 0),
                               row[0]))
    return [pair for _,pair,_ in dated[:limit]]


def _metrics(record,normalized=None):
    # Zero denominators and absent fields are NOT silently invented metrics.
    facts=normalized or (bd.normalize_record("tiktok",record) if record else None)
    facts=facts or {}
    nums=numeric_fields(record) if record else {}
    counts={k:facts.get(k) for k in ("views","likes","comments","shares","saves")}
    views=counts["views"]
    rates={}
    for k in ("likes","comments","shares","saves"):
        val=counts[k]
        rates[k+"_per_view_pct"]=round(100*val/views,5) if isinstance(views,int) and views>0 and isinstance(val,int) else None
    engagement=[counts[k] for k in ("likes","comments","shares","saves")]
    rates["engagement_per_view_pct"]=(round(100*sum(engagement)/views,5)
        if isinstance(views,int) and views>0 and all(isinstance(x,int) for x in engagement)
        else None)
    return {
        "schema_version":"studio-tiktok-metrics/v1",
        "source":"Bright Data public TikTok post dataset" if record else "not_available",
        "post_url":facts.get("url"),
        "published_at":facts.get("published_at"),
        "public_author":facts.get("author"),
        "observed_counters":counts,
        "observed_numeric_fields":nums,
        "observed_field_coverage":metric_coverage(nums),
        "derived_public_ratios":rates,
        "unavailable_not_zero":{"retention":None,"average_watch_time":None,
                               "per_slide_dwell_time":None},
        "caveat":"Нет доступа к внутренней аналитике TikTok; метрики удержания не выводятся из лайков или просмотров."
    }

def _jpeg_inputs(folder,manifest):
    native=manifest["post_native_id"]
    original_dir=folder/("carousel-tiktok-"+native)
    out_dir=folder/"gemini-input"
    out_dir.mkdir(exist_ok=True)
    results=[]
    for slide in manifest["slides"]:
        src=original_dir/slide["file_name"]
        if src.is_symlink():
            raise ValueError("Небезопасный путь изображения.")
        if sha256(src.read_bytes()).hexdigest()!=slide["source_image_sha256"]:
            raise ValueError("Исходный слайд изменился.")
        with Image.open(src) as original:
            image=ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((1536,1536),Image.Resampling.LANCZOS)
            output=io.BytesIO()
            image.save(output,format="JPEG",quality=90,optimize=True)
        data=output.getvalue()
        rel=f"gemini-input/{slide['slide_index']:02d}.jpg"
        (folder/rel).write_bytes(data)
        results.append({"slide_index":slide["slide_index"],"relative_image":rel,
                        "input_jpeg_sha256":sha256(data).hexdigest()})
    return results

def _analyze(folder,items,*,provider="openrouter",model=None):
    prompt={
        "task":"Infer the complete slide-by-slide semantic narrative from the images.",
        "carousel_id":"local_studio",
        "slide_count":len(items),
        "required_json_contract":story.OUTPUT_CONTRACT,
        "rules":{"only_ordered_images_are_evidence":True,
                 "no_prior_model_labels_or_descriptions":True,
                 "do_not_assume_hook_cta_product_or_their_positions":True,
                 "one_primary_role_per_slide_secondary_roles_optional":True,
                 "phase_segments_cover_entire_carousel_in_order":True,
                 "separate_direct_observation_from_interpretation":True}
    }
    if model is None:
        model=MODEL_BY_PROVIDER[provider]
    if model!=MODEL_BY_PROVIDER.get(provider):
        raise ValueError("studio_model_provider_mismatch")
    response=story._request(folder,{"prompt":prompt,"slides":items,
                                    "slide_count":len(items),"native_id":"local_studio",
                                    "post_url":"local_studio",
                                    "source_selection_sha256":"local_studio"},
                            model=model,provider=provider)
    return response["analysis"],_priced_usage(response["usage"],provider=provider)

def _saved_targets(ident):
    """Durable ordered worklist. Never repeat Bright Data on resume."""
    with db() as c:
        rows=c.execute("""SELECT native_id,canonical_url FROM studio_targets
            WHERE run_id=? ORDER BY ordinal""",(ident,)).fetchall()
    return [(r["canonical_url"],None) for r in rows]


def _complete_item(ident,native,analysis):
    """Commit success exactly once, even after a crash following JSON output."""
    with db() as c:
        cursor=c.execute("""UPDATE studio_items
            SET state='completed',analysis_json=?,error=NULL
            WHERE run_id=? AND native_id=? AND state!='completed'""",
            (json.dumps(analysis,ensure_ascii=False),ident,native))
        if cursor.rowcount:
            c.execute("UPDATE studio_runs SET completed=completed+1 WHERE id=?",(ident,))


def _complete_from_saved_json(folder,url,*,provider="openrouter",model=STUDIO_MODEL):
    """Reconcile an already-saved model response without an API call."""
    path=folder/"analysis.json"
    if not path.is_file() or path.is_symlink():
        return None
    payload=json.loads(path.read_text("utf-8"))
    if (payload.get("source_url")!=url or
        payload.get("model")!=model or
        payload.get("provider","openrouter")!=provider or
        payload.get("prompt_sha256")!=story.PROMPT_SHA or
        not isinstance(payload.get("narrative"),dict)):
        raise ValueError("studio_existing_analysis_provenance_mismatch")
    return payload["narrative"]


def resume(ident):
    """Explicit user-controlled continuation, no profile or metric re-trigger."""
    if type(ident) is not str or not ID.fullmatch(ident):
        raise ValueError("Некорректный ID задания.")
    with LOCK:
        with db() as c:
            run=c.execute("SELECT state,provider,model FROM studio_runs WHERE id=?",(ident,)).fetchone()
            if run is None:
                raise ValueError("Задание не найдено.")
            if run["state"] not in ("paused","interrupted"):
                raise ValueError("Продолжить можно только приостановленное задание.")
            targets=c.execute("SELECT COUNT(*) FROM studio_targets WHERE run_id=?",
                              (ident,)).fetchone()[0]
            if targets<=0:
                raise ValueError("Нет сохранённого списка публикаций для безопасного продолжения.")
            if ident in ACTIVE:
                raise ValueError("Задание уже выполняется.")
            # An ambiguous possibly billed request must NEVER be automatically
            # replayed. Isolate that one item so other not-yet-started items
            # can continue without another Bright Data / LLM charge for it.
            pending=c.execute("""SELECT native_id,state,canonical_url FROM studio_items
                WHERE run_id=? AND state IN ('analyzing','awaiting_credits')""",
                (ident,)).fetchall()
            needs_review=[]
            for item in pending:
                folder=_dir()/ident/"carousels"/item["native_id"]
                receipt=folder/"analysis.json"
                valid_receipt=False
                if receipt.is_file() and not receipt.is_symlink():
                    try:
                        valid_receipt=(_complete_from_saved_json(
                            folder,item["canonical_url"],
                            provider=run["provider"],model=run["model"]) is not None)
                    except (ValueError,TypeError,KeyError,OSError,UnicodeError):
                        valid_receipt=False
                # Valid on-disk LLM output can be reconciled without any call.
                if valid_receipt:
                    continue
                marker=folder/"gemini-request.json"
                rejected=False
                if marker.is_file() and not marker.is_symlink():
                    try:
                        stored=json.loads(marker.read_text("utf-8"))
                        rejected=(stored.get("state")=="rejected_insufficient_credits"
                            and stored.get("provider","openrouter")==run["provider"]
                            and stored.get("model")==run["model"]
                            and stored.get("prompt_sha256")==story.PROMPT_SHA)
                    except (ValueError,TypeError,OSError,UnicodeError,AttributeError):
                        rejected=False
                if rejected:
                    continue  # Definite HTTP 402: explicit resume is safe.
                c.execute("""UPDATE studio_items
                    SET state='needs_review',
                        error='Возможен уже оплаченный LLM-вызов: автоматический повтор запрещён'
                    WHERE run_id=? AND native_id=? AND state IN ('analyzing','awaiting_credits')""",
                    (ident,item["native_id"]))
                needs_review.append(item["native_id"])
            c.execute("""UPDATE studio_runs SET state='queued',phase='Возобновление',
                error=NULL,finished_at=NULL,updated_at=? WHERE id=?""",(utc(),ident))
        if needs_review:
            _event(ident,"Безопасное продолжение: "+str(len(needs_review))+
                   " неопределённых платных вызовов оставлено для ручной проверки: "+
                   ", ".join(needs_review)+". Они НЕ будут отправлены повторно.",
                   "error")
        _event(ident,"Продолжение из сохранённого списка без повторного запуска Bright Data.")
        _thread(ident,resume=True)
    return {"id":ident,"state":"queued","resumed":True,
            "manual_review_held":len(needs_review)}


def retry_failed(ident, *, confirm_paid_retries=False):
    """User-approved retry of known failed items in the SAME run only.

    Successful analyses and needs_review items never change. No Bright Data
    triggers. Failed model attempts can already have been billed; the user
    must explicitly consent to new requests, and old request evidence stays
    archived. Restarting after a new request began does not auto-retry it.
    """
    if type(ident) is not str or not ID.fullmatch(ident):
        raise ValueError("Некорректный ID задания.")
    if type(confirm_paid_retries) is not bool:
        raise ValueError("Подтверждение стоимости должно быть логическим значением.")
    with LOCK:
        if ident in ACTIVE:
            raise ValueError("Задание выполняется, дождитесь завершения.")
        with db() as c:
            run=c.execute("SELECT * FROM studio_runs WHERE id=?",(ident,)).fetchone()
            if run is None:
                raise ValueError("Задание не найдено.")
            if run["state"] not in ("partial","failed","completed"):
                raise ValueError("Повторить ошибки можно только после окончания запуска.")
            provider,model=run["provider"],run["model"]
            if model!=MODEL_BY_PROVIDER.get(provider):
                raise ValueError("Модель исходного запуска не поддерживается.")
            failures=c.execute("""SELECT native_id,canonical_url,error
                FROM studio_items WHERE run_id=? AND state='failed'
                ORDER BY rowid""",(ident,)).fetchall()
            selected=[x for x in failures if x["error"] in RETRYABLE_ERRORS]
            payable=[x for x in selected if x["error"] in RETRYABLE_MODEL_ERRORS]
            if not selected:
                raise ValueError("Нет ошибок, разрешённых для безопасного повтора.")
            if payable and confirm_paid_retries is not True:
                raise ValueError("Подтвердите повторную оплату запросов LLM.")
            # Validate all local evidence before changing statuses. Completed
            # or unverified payment attempts are excluded at SQL level.
            planned=[]
            for item in selected:
                native=item["native_id"]
                folder=_dir()/ident/"carousels"/native
                marker=folder/"gemini-request.json"
                if (folder/"analysis.json").is_file():
                    raise ValueError("Найден сохранённый ответ модели. Нужна проверка перед повтором.")
                if item["error"] in RETRYABLE_MODEL_ERRORS:
                    if not (folder/"manifest.json").is_file():
                        raise ValueError("Отсутствует сохранённая галерея для "+native)
                    if marker.is_symlink():
                        raise ValueError("Небезопасный путь контрольной записи.")
                    original=json.loads(marker.read_text("utf-8")) if marker.is_file() else {}
                    if (original and (original.get("provider","openrouter")!=provider
                        or original.get("model")!=model
                        or original.get("prompt_sha256")!=story.PROMPT_SHA)):
                        raise ValueError("Контрольная запись LLM не совпадает с исходным запуском.")
                    planned.append((item,folder,marker,original))
                else:
                    if marker.is_file() or marker.is_symlink():
                        raise ValueError("Найдена запись платного запроса для ошибки загрузки. Нужна проверка.")
                    planned.append((item,folder,marker,None))
            # Approval is recorded BEFORE allowing queued failed requests.
            # Preserve originals with distinct names for each retry attempt.
            for item,folder,marker,old in planned:
                if old is None:
                    continue
                archive_index=1
                while (folder/f"gemini-request-before-retry-{archive_index:03d}.json").exists():
                    archive_index+=1
                if marker.exists():
                    archived=folder/f"gemini-request-before-retry-{archive_index:03d}.json"
                    _write_json(archived,old)
                _write_json(marker,{
                    "provider":provider,"model":model,
                    "prompt_sha256":story.PROMPT_SHA,
                    "slide_hashes":old.get("slide_hashes"),
                    "state":RETRY_MARKER_STATE,
                    "approved_at_utc":utc(),
                    "approval_kind":"explicit_retry_failed_only",
                })
            ids=[item["native_id"] for item in selected]
            for native in ids:
                c.execute("""UPDATE studio_items SET state='queued',error=NULL
                    WHERE run_id=? AND native_id=? AND state='failed'""",(ident,native))
            c.execute("""UPDATE studio_runs SET state='queued',phase='Повтор ошибок',
                progress=20,failed=failed-?,error=NULL,finished_at=NULL,
                updated_at=? WHERE id=?""",(len(ids),utc(),ident))
        _event(ident,"Повтор только ошибочных каруселей: "+str(len(ids))+
               " (LLM: "+str(len(payable))+", изображения: "+
               str(len(ids)-len(payable))+"). Успешные и спорные не затронуты.")
        _thread(ident,resume=True)
    return {"id":ident,"state":"queued","retry_failed_only":True,
            "selected":len(ids),"new_paid_llm_calls_possible":len(payable)}


def _process(ident, *, resume=False):
    with db() as c:
        row=c.execute("SELECT * FROM studio_runs WHERE id=?",(ident,)).fetchone()
    if row is None:
        raise ValueError("studio_run_missing")
    kind,source=row["source_kind"],row["source_input"]
    provider,model=row["provider"],row["model"]
    if model!=MODEL_BY_PROVIDER.get(provider):
        raise ValueError("studio_run_provider_model_invalid")
    run_dir=_dir()/ident
    if resume:
        targets=_saved_targets(ident)
        if not targets:
            raise ValueError("studio_resume_targets_missing")
        # Recover source profile metrics from the already-paid saved snapshot
        # if the Python server restarted while polling optional detail data.
        # This step performs no Bright Data / LLM calls and never overwrites
        # an existing detailed metrics JSON.
        if kind=="profile":
            with db() as c:
                missing=c.execute("""SELECT i.native_id,i.canonical_url,p.counts_json
                    FROM studio_items i LEFT JOIN studio_channel_posts p
                    ON p.run_id=i.run_id AND p.native_id=i.native_id
                    WHERE i.run_id=? AND i.metrics_json IS NULL""",
                    (ident,)).fetchall()
            restored=0
            for entry in missing:
                if not entry["counts_json"]:
                    continue
                observed=(json.loads(entry["counts_json"]).get("observed_counters") or {})
                normalized={"url":entry["canonical_url"]}
                for key in ("views","likes","comments","shares","saves"):
                    value=observed.get(key)
                    normalized[key]=value if type(value) is int and value>=0 else None
                metric=_metrics(None,normalized)
                metric["source"]="Bright Data TikTok profile snapshot"
                metric["source_record"]="profile_snapshot"
                metric["provider_snapshot_id"]=row["snapshot_id"]
                metric["collected_at_utc"]=row["created_at"]
                folder=run_dir/"carousels"/entry["native_id"]
                _write_json(folder/"metrics.json",metric)
                with db() as c:
                    c.execute("""UPDATE studio_items SET metrics_json=?
                        WHERE run_id=? AND native_id=? AND metrics_json IS NULL""",
                        (json.dumps(metric,ensure_ascii=False),ident,entry["native_id"]))
                restored+=1
            if restored:
                _event(ident,"Восстановлены исходные метрики из сохранённого "+
                       f"снимка профиля: {restored} публикаций. Без новых платных запросов.")
        _event(ident,f"Восстановлено публикаций: {len(targets)}.",
               state="running",phase="Продолжение",error=None)
    else:
        targets,scope=_discover(ident,kind,source)
        old_count=0
        if kind=="profile":
            # Deduplicate successfully analyzed TikTok IDs across ALL earlier
            # Studio runs (including previous single-post runs).
            with db() as c:
                previous={r[0] for r in c.execute(
                    "SELECT DISTINCT native_id FROM studio_items WHERE state='completed'")}
            old_count=sum(canonical_video_url(url)[1] in previous for url,_ in targets)
            targets=[(url,raw) for url,raw in targets
                     if canonical_video_url(url)[1] not in previous]
            if row["max_carousels"] is not None:
                new_count=len(targets)
                targets=_select_profile_carousels(targets,row["max_carousels"])
                _event(ident,
                    f"Новых каруселей: {new_count}; ранее завершено и пропущено: {old_count}; "
                    f"выбрано для нового анализа: {len(targets)} (лимит {row['max_carousels']}).")
        # Freeze the complete target list BEFORE any paid per-post triggers.
        with db() as c:
            for order,(url,raw) in enumerate(targets):
                native=canonical_video_url(url)[1]
                c.execute("""INSERT INTO studio_targets
                   (run_id,native_id,ordinal,canonical_url) VALUES(?,?,?,?)""",
                   (ident,native,order,url))
                c.execute("""INSERT OR IGNORE INTO studio_items
                   (run_id,native_id,canonical_url,state)
                   VALUES(?,?,?,'queued')""",(ident,native,url))
            c.execute("UPDATE studio_runs SET skipped_existing=? WHERE id=?",
                      (old_count,ident))
        if not targets:
            _event(ident,"Новых каруселей для анализа не найдено. Старые не оплачиваются повторно.",
                   state="completed",phase="Завершено",progress=100,
                   total=0,finished_at=utc())
            _package(ident)
            return
        _event(ident,f"Выбрано каруселей: {len(targets)}.",
               state="running",phase="Подготовка метрик",total=len(targets),progress=17)
        # This paid metadata step is done only on a new run. Checkpoint all
        # results before starting Gemini, so a resume cannot double-bill it.
        try:
            by_url=_trigger_metrics(ident,[url for url,_ in targets])
            _event(ident,"Подробные метрики Bright Data получены.",
                   phase="Загрузка изображений",progress=20)
        except Exception as exc:
            by_url={}
            _event(ident,"Подробные метрики недоступны: "+
                   type(exc).__name__+
                   ". Продолжаю с сохранёнными данными профиля без повтора платного запроса.",
                   "error",phase="Загрузка изображений",progress=20)
        for url,raw in targets:
            native=canonical_video_url(url)[1]
            item_dir=run_dir/"carousels"/native
            item_dir.mkdir(parents=True,exist_ok=True)
            normalized,metrics_raw,metric_snapshot=by_url.get(url,(None,None,None))
            try:
                metrics=_metrics(metrics_raw or raw,normalized)
            except (ValueError,TypeError):
                metrics=_metrics(None)
                metrics["metric_parse_error"]="source_field_invalid"
            metrics["provider_snapshot_id"]=metric_snapshot
            metrics["collected_at_utc"]=utc()
            metrics["source_record"]=(
                "post_detail" if metrics_raw else
                "profile_snapshot" if raw else "unavailable")
            _write_json(item_dir/"metrics.json",metrics)
            with db() as c:
                c.execute("""UPDATE studio_items SET metrics_json=?
                    WHERE run_id=? AND native_id=?""",
                    (json.dumps(metrics,ensure_ascii=False),ident,native))

    # Both new and resumed runs use the frozen list. Completed rows and saved
    # results are never resent to the LLM.
    for index,(url,_raw) in enumerate(targets):
        native=canonical_video_url(url)[1]
        item_dir=run_dir/"carousels"/native
        item_dir.mkdir(parents=True,exist_ok=True)
        with db() as c:
            item=c.execute("""SELECT * FROM studio_items
                 WHERE run_id=? AND native_id=?""",(ident,native)).fetchone()
        if item is None:
            raise ValueError("studio_checkpoint_item_missing")
        if item["state"]=="completed":
            continue
        if item["state"] in ("failed","needs_review"):
            continue  # Do not re-bill known failures or ambiguous paid calls.
        request_marker=None
        try:
            restored=_complete_from_saved_json(item_dir,url,provider=provider,model=model)
            if restored is not None:
                _complete_item(ident,native,restored)
                _event(ident,f"Сохранённый Gemini JSON {native} восстановлен без нового запроса.")
                continue
            marker_file=item_dir/"gemini-request.json"
            if resume and marker_file.is_file():
                marker=json.loads(marker_file.read_text("utf-8"))
                if (marker.get("state") not in
                    ("rejected_insufficient_credits",RETRY_MARKER_STATE) or
                    marker.get("model")!=model or
                    marker.get("provider","openrouter")!=provider or
                    marker.get("prompt_sha256")!=story.PROMPT_SHA):
                    raise ValueError("studio_uncertain_paid_call_requires_manual_review")
            elif resume and item["state"]=="analyzing":
                raise ValueError("studio_missing_paid_call_checkpoint")
            if (item_dir/"manifest.json").is_file():
                manifest=json.loads((item_dir/"manifest.json").read_text("utf-8"))
            else:
                with db() as c:
                    c.execute("""UPDATE studio_items SET state='downloading'
                        WHERE run_id=? AND native_id=?""",(ident,native))
                _event(ident,f"[{index+1}/{len(targets)}] Загрузка карусели {native}",
                       phase="Загрузка каруселей",
                       progress=round(20+73*(index+.05)/len(targets),2))
                manifest=download_carousel(url,item_dir)
                _write_json(item_dir/"manifest.json",manifest)
            inputs=_jpeg_inputs(item_dir,manifest)
            with db() as c:
                c.execute("""UPDATE studio_items SET state='analyzing',
                    asset_count=?,manifest_json=?,error=NULL
                    WHERE run_id=? AND native_id=?""",
                    (len(inputs),json.dumps(manifest,ensure_ascii=False),ident,native))
            _event(ident,f"[{index+1}/{len(targets)}] {model} ({provider}): {len(inputs)} слайдов",
                   phase="Анализ Gemini",
                   progress=round(20+73*(index+.48)/len(targets),2))
            # Record exact fingerprints prior to potentially billable call.
            slide_hashes=[x["input_jpeg_sha256"] for x in inputs]
            if (resume and marker_file.is_file() and
                marker.get("slide_hashes") is not None and
                marker.get("slide_hashes")!=slide_hashes):
                raise ValueError("studio_resume_slide_inputs_changed")
            request_marker={
                "provider":provider,"model":model,"prompt_sha256":story.PROMPT_SHA,
                "slide_hashes":slide_hashes,
                "state":"started_no_automatic_retry"}
            _write_json(marker_file,request_marker)
            analysis,usage=_analyze(item_dir,inputs,provider=provider,model=model)
            _write_json(item_dir/"analysis.json",{
                "schema_version":"studio-gemini-story/v1",
                "source_url":url,"provider":provider,"model":model,
                "prompt_sha256":story.PROMPT_SHA,
                "narrative":analysis,"usage":usage})
            _complete_item(ident,native,analysis)
            _event(ident,f"[{index+1}/{len(targets)}] Анализ карусели {native} завершён.",
                   phase="Обработка",
                   progress=round(20+73*(index+1)/len(targets),2))
        except urllib.error.HTTPError as exc:
            if exc.code!=402 or request_marker is None:
                # Other HTTP failures are not silently assumed to be balance.
                error="HTTP "+str(exc.code)
                with db() as c:
                    c.execute("""UPDATE studio_items SET state='failed',error=?
                        WHERE run_id=? AND native_id=?""",(error,ident,native))
                    c.execute("UPDATE studio_runs SET failed=failed+1 WHERE id=?",(ident,))
                _event(ident,f"Ошибка поставщика на {native}: {error}",
                       "error",phase="Обработка")
                continue
            # 402 is a definitive rejected request: safe to retry after top-up.
            _write_json(marker_file,{**request_marker,
                "state":"rejected_insufficient_credits","http_status":402})
            with db() as c:
                c.execute("""UPDATE studio_items SET state='awaiting_credits',
                    error='Недостаточно средств провайдера LLM'
                    WHERE run_id=? AND native_id=?""",(ident,native))
            provider_name="Rus-GPT" if provider=="rusgpt" else "OpenRouter"
            _event(ident,provider_name+" сообщил о нехватке средств (HTTP 402). "+
                   "Сохранённые результаты и фото на месте. Пополните баланс и нажмите «Продолжить».",
                   "error",state="paused",phase="Ожидание пополнения "+provider_name,
                   error="Недостаточно средств "+provider_name,finished_at=utc())
            try:
                _package(ident,partial=True)
            except Exception:
                # Export failure must not turn a safely paused run into failed.
                _event(ident,"Не удалось обновить ZIP; файлы и SQLite не удалены.",
                       "error")
            return
        except Exception as exc:
            # Ambiguous post-request errors must not be retried automatically.
            error=(type(exc).__name__ if isinstance(exc,(urllib.error.URLError,TimeoutError))
                   else str(exc)[:200])
            with db() as c:
                c.execute("""UPDATE studio_items SET state='failed',error=?
                    WHERE run_id=? AND native_id=?""",(error,ident,native))
                c.execute("UPDATE studio_runs SET failed=failed+1 WHERE id=?",(ident,))
            _event(ident,f"[{index+1}/{len(targets)}] Ошибка {native}: {error}",
                   "error",phase="Обработка",
                   progress=round(20+73*(index+1)/len(targets),2))
    with db() as c:
        counts=c.execute("SELECT completed,failed FROM studio_runs WHERE id=?",
                         (ident,)).fetchone()
        held=c.execute("""SELECT COUNT(*) FROM studio_items
            WHERE run_id=? AND state='needs_review'""",(ident,)).fetchone()[0]
    end="completed" if counts["failed"]==0 and held==0 else "partial"
    _event(ident,"Сбор и анализ закончены. Фиксируем итоговый JSON и архив.",
           state=end,phase="Сборка архива",progress=100,finished_at=utc())
    _package(ident)


def _aggregate_metrics(items):
    """Only grounded public counters; missing values do not become zeros."""
    counters=("views","likes","comments","shares","saves")
    fields={key:[] for key in counters}
    engagement_rates=[]
    top=[]
    for item in items:
        raw=item["metrics_json"]
        if not raw:
            continue
        m=json.loads(raw)
        obs=m.get("observed_counters") or {}
        for key in counters:
            value=obs.get(key)
            if type(value) is int and value>=0:
                fields[key].append(value)
        rate=(m.get("derived_public_ratios") or {}).get("engagement_per_view_pct")
        if type(rate) in (int,float):
            engagement_rates.append(rate)
        if type(obs.get("views")) is int:
            top.append((obs["views"],item["native_id"]))
    summary={
        "schema_version":"studio-account-public-metrics/v1",
        "number_of_carousels":len(items),
        "public_count_coverage":{key:len(vals) for key,vals in fields.items()},
        "total_observed":{key:sum(vals) if vals else None
                          for key,vals in fields.items()},
        "mean_views":round(mean(fields["views"]),2) if fields["views"] else None,
        "median_views":median(fields["views"]) if fields["views"] else None,
        "mean_available_engagement_per_view_pct":(
            round(mean(engagement_rates),5) if engagement_rates else None),
        "top_10_by_public_views":[{"native_id":ident,"views":v}
                                  for v,ident in sorted(top,reverse=True)[:10]],
        "note":"Totals only sum observed fields. Missing counters and inaccessible historical posts are excluded; never 0-filled.",
    }
    return summary



def _gemini_cost_summary(folder,items):
    """Summarize actual provider receipts independently from reference estimates.

    Missing cost or usage means UNKNOWN, never USD 0. Older model pricing
    is not silently rebased to the current Gemini 3.8 Flash quote.
    """
    known_provider=[]
    known_estimates=[]
    known_provider_rub=[]
    known_estimates_rub=[]
    complete=0
    receipt_count=0
    models=set()
    for item in items:
        if item["state"]!="completed":
            continue
        complete+=1
        native=item["native_id"]
        if not NATIVE.fullmatch(native):
            continue
        src=folder/"carousels"/native/"analysis.json"
        if src.is_symlink() or not src.is_file():
            continue
        try:
            payload=json.loads(src.read_text("utf-8"))
            usage=payload.get("usage") or {}
            model=payload.get("model")
        except (ValueError,TypeError,OSError):
            continue
        if not isinstance(usage,dict):
            continue
        receipt_count+=1
        if isinstance(model,str):
            models.add(model)
        actual=usage.get("reported_cost_usd")
        if (model==STUDIO_MODEL and type(actual) in (int,float)
            and 0<=actual<1e9):
            known_provider.append(actual)
        estimated=usage.get("estimated_standard_cost_usd")
        if (model==STUDIO_MODEL and type(estimated) in (int,float)
            and 0<=estimated<1e9):
            known_estimates.append(estimated)
        actual_rub=usage.get("reported_cost_rub")
        if (model==RUSGPT_MODEL and type(actual_rub) in (int,float)
            and 0<=actual_rub<1e9):
            known_provider_rub.append(actual_rub)
        estimated_rub=usage.get("estimated_cost_rub")
        if (model==RUSGPT_MODEL and type(estimated_rub) in (int,float)
            and 0<=estimated_rub<1e9):
            known_estimates_rub.append(estimated_rub)
    return {
        "completed_carousels":complete,
        "usage_receipts":receipt_count,
        "models_in_results":sorted(models),
        "provider_cost_coverage":len(known_provider),
        "sum_known_provider_cost_usd":(
            round(sum(known_provider),8) if known_provider else None),
        "standard_quote_estimate_coverage":len(known_estimates),
        "sum_known_standard_quote_estimate_usd":(
            round(sum(known_estimates),8) if known_estimates else None),
        "rub_provider_cost_coverage":len(known_provider_rub),
        "sum_known_provider_cost_rub":(
            round(sum(known_provider_rub),8) if known_provider_rub else None),
        "rub_estimate_coverage":len(known_estimates_rub),
        "sum_known_rub_estimate":(
            round(sum(known_estimates_rub),8) if known_estimates_rub else None),
        "standard_quote":dict(STUDIO_PRICE),
        "rusgpt_quote":dict(RUSGPT_PRICE),
        "note":"Provider costs are separated by currency; incomplete costs are not zero. Quotes can change.",
    }


def _package(ident,partial=False):
    folder=_dir()/ident
    if not folder.is_dir():
        return
    with db() as c:
        run=c.execute("SELECT * FROM studio_runs WHERE id=?",(ident,)).fetchone()
        items=c.execute("SELECT * FROM studio_items WHERE run_id=? ORDER BY rowid",(ident,)).fetchall()
        channel=c.execute("""SELECT native_id,kind,canonical_url,counts_json
                             FROM studio_channel_posts WHERE run_id=?
                             ORDER BY native_id""",(ident,)).fetchall()
    groups=cohort.build([dict(x) for x in items],
                        [dict(x) for x in channel],source_kind=run["source_kind"])
    channel_evidence={
        "schema_version":"studio-public-channel-snapshot/v1",
        "source":"Bright Data TikTok profile snapshot" if run["source_kind"]=="profile"
                 else "not_collected_for_single_post",
        "snapshot_id":run["snapshot_id"],
        "scope":"only_provider_returned_publications",
        "posts":[{
            "native_id":p["native_id"],
            "kind":p["kind"],
            "url":p["canonical_url"],
            "observed_counters":json.loads(p["counts_json"]).get("observed_counters"),
        } for p in channel],
    }
    _write_json(folder/"channel-snapshot-public-metrics.json",channel_evidence)
    _write_json(folder/"group-analytics.json",groups)
    with db() as c:
        c.execute("""INSERT INTO studio_group_analytics(run_id,payload_json,created_at)
                     VALUES(?,?,?)
                     ON CONFLICT(run_id) DO UPDATE SET payload_json=excluded.payload_json,
                                                        created_at=excluded.created_at""",
                  (ident,json.dumps(groups,ensure_ascii=False),utc()))
    result={
      "schema_version":SCHEMA,
      "run":{k:run[k] for k in ("id","source_kind","source_input","provider","model","state",
                               "total","completed","failed","skipped_existing","phase","error",
                               "created_at","snapshot_id","max_carousels")},
      "profile_snapshot_complete_history_guaranteed":False,
      "profile_processing_limit_applied_after_discovery":(
          run["source_kind"]=="profile" and run["max_carousels"] is not None),
      "unlimited_carousels_in_application":True,
      "assets_are_photo_carousel_images_not_mp4":True,
      "aggregate_public_metrics":_aggregate_metrics(items),
      "gemini_cost_summary":_gemini_cost_summary(folder,items),
      "group_analytics":groups,
      "channel_snapshot_public_metrics":channel_evidence,
      "items":[{
        "native_id":x["native_id"],"url":x["canonical_url"],"state":x["state"],
        "assets":x["asset_count"],"error":x["error"],
        "metrics":json.loads(x["metrics_json"]) if x["metrics_json"] else None,
        "gemini":json.loads(x["analysis_json"]) if x["analysis_json"] else None
      } for x in items],
    }
    _write_json(folder/"full-analysis.json",result)
    # Native source photos and all analytical artifacts, without sqlite DB.
    dest=folder/"carousel-studio-results.zip"
    temp=folder/"carousel-studio-results.pending.zip"
    with ZipFile(temp,"w",compression=ZIP_DEFLATED,compresslevel=5) as z:
        for src in folder.rglob("*"):
            if (not src.is_file() or src.is_symlink()
                or src==dest or src==temp or src.suffix.lower() in (
                    ".db",".sqlite3",".mp4",".mov",".webm",".gif",".exe",".dll")):
                continue
            rel=src.relative_to(folder)
            z.write(src,arcname=rel.as_posix(),
                    compress_type=ZIP_STORED if src.suffix.lower() in (".jpg",".jpeg",".png",".webp") else ZIP_DEFLATED)
    temp.replace(dest)
    if run["source_kind"]=="profile":
        # Cumulative account report is derived entirely from saved local
        # evidence. Never trigger paid provider/model calls while rebuilding.
        try:
            from intelligence.market_intelligence import carousel_studio_accounts
            carousel_studio_accounts.refresh_master(run["source_input"])
        except Exception as exc:
            _event(ident,"Не удалось обновить общий отчёт аккаунта: "+
                   type(exc).__name__+". Исходный отчёт запуска сохранён.",
                   "error")

def _row(row):
    return dict(row) if row else None

def list_runs():
    with db() as c:
        rows=c.execute("""SELECT id,source_kind,source_input,max_carousels,provider,model,state,phase,progress,
                          total,completed,failed,skipped_existing,error,created_at,updated_at
                          FROM studio_runs ORDER BY created_at DESC LIMIT 50""").fetchall()
    return [_row(x) for x in rows]

def get_run(ident):
    if not ID.fullmatch(ident):
        return None
    with db() as c:
        row=c.execute("SELECT * FROM studio_runs WHERE id=?",(ident,)).fetchone()
        if not row:return None
        items=c.execute("""SELECT native_id,canonical_url,state,asset_count,error,
                           metrics_json,analysis_json
                           FROM studio_items WHERE run_id=? ORDER BY rowid""",(ident,)).fetchall()
        events=c.execute("""SELECT created_at,level,message FROM studio_events
                            WHERE run_id=? ORDER BY id DESC LIMIT 250""",(ident,)).fetchall()
    previews=[]
    for x in items:
        record={k:x[k] for k in ("native_id","canonical_url","state","asset_count","error")}
        metrics=json.loads(x["metrics_json"]) if x["metrics_json"] else {}
        analysis=json.loads(x["analysis_json"]) if x["analysis_json"] else {}
        record["counters"]=metrics.get("observed_counters")
        record["story_summary"]=analysis.get("overall_story_summary")
        record["hook"]=analysis.get("global_hook")
        record["cta"]=analysis.get("global_cta")
        record["product"]=analysis.get("product_integration")
        previews.append(record)
    failures=[x for x in previews if x["state"]=="failed"]
    return {**_row(row),"items":previews,
            "retryable_failed":sum(x["error"] in RETRYABLE_ERRORS for x in failures),
            "retryable_paid_failed":sum(x["error"] in RETRYABLE_MODEL_ERRORS for x in failures),
            "retryable_media_failed":sum(x["error"] in RETRYABLE_MEDIA_ERRORS for x in failures),
            "manual_review":sum(x["state"]=="needs_review" for x in previews),
            "events":[_row(x) for x in reversed(events)],
            "downloads":available_downloads(ident)}

DOWNLOADS={
    "zip":"carousel-studio-results.zip",
    "json":"full-analysis.json",
    "logs":"events.jsonl",
    "groups":"group-analytics.json",
}

def available_downloads(ident):
    folder=_dir()/ident
    return {k:(folder/name).is_file() for k,name in DOWNLOADS.items()}

def download_path(ident,format_name):
    if not ID.fullmatch(ident) or format_name not in DOWNLOADS:
        return None
    path=_dir()/ident/DOWNLOADS[format_name]
    return path if path.is_file() and not path.is_symlink() else None

def read_group_analytics(ident):
    """Return persisted cohort report, independent of browser session."""
    if not ID.fullmatch(ident):
        return None
    with db() as c:
        row=c.execute("SELECT payload_json FROM studio_group_analytics WHERE run_id=?",
                      (ident,)).fetchone()
    return json.loads(row[0]) if row else None


def item_download_path(ident,native,kind):
    """Only exact per-run per-post safe JSON artifacts."""
    files={"metrics":"metrics.json","analysis":"analysis.json",
           "manifest":"manifest.json"}
    if (not ID.fullmatch(ident) or not NATIVE.fullmatch(native)
        or kind not in files):
        return None
    with db() as c:
        row=c.execute("SELECT 1 FROM studio_items WHERE run_id=? AND native_id=?",
                      (ident,native)).fetchone()
    if not row:return None
    file=_dir()/ident/"carousels"/native/files[kind]
    return file if file.is_file() and not file.is_symlink() else None


def item_slide_path(ident,native,index):
    """Safe immutable original still from a DB-owned post and manifest."""
    if (not ID.fullmatch(ident) or not NATIVE.fullmatch(native)
        or type(index) is not int or not 1<=index<=MAX_SLIDES):
        return None
    with db() as c:
        row=c.execute("SELECT asset_count FROM studio_items WHERE run_id=? AND native_id=?",
                      (ident,native)).fetchone()
    if not row or row["asset_count"]<index:
        return None
    folder=_dir()/ident/"carousels"/native
    manifest_file=folder/"manifest.json"
    if not manifest_file.is_file() or manifest_file.is_symlink():
        return None
    try:
        manifest=json.loads(manifest_file.read_text("utf-8"))
        slide=manifest["slides"][index-1]
        if slide["slide_index"]!=index:
            return None
        name=slide["file_name"]
        if not re.fullmatch(r"[0-9]{3}\.(?:jpg|jpeg|png|webp)",name,re.I):
            return None
        path=folder/("carousel-tiktok-"+native)/name
        if path.is_symlink() or not path.is_file():
            return None
        # The original hash is checked again before being displayed.
        if sha256(path.read_bytes()).hexdigest()!=slide["source_image_sha256"]:
            return None
        return path
    except (KeyError,IndexError,TypeError,ValueError,OSError):
        return None


def recover_interrupted():
    """Never automatically replay paid calls after a server restart."""
    with db() as c:
        interrupted=c.execute("""SELECT id FROM studio_runs WHERE state IN ('queued','running')""").fetchall()
        for row in interrupted:
            c.execute("""UPDATE studio_runs SET state='interrupted',
                phase='Прервано перезапуском',error='Проверь выполненные вызовы перед повтором',
                finished_at=?,updated_at=? WHERE id=?""",(utc(),utc(),row["id"]))
    return len(interrupted)
