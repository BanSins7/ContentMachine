"""Local TikTok photo-carousel studio: durable jobs, Bright Data + Gemini.

Data stays on the operator's PC in marketing-qa/carousel-studio. SQLite WAL
persists runs, item metadata, event logs and output paths. Every carousel has
real ordered still images, not fabricated MP4s. No arbitrary number-of-posts
cap; provider profile snapshot coverage is explicitly unknown.

The user starts one paid job by clicking Start. No silent retries of an
ambiguous Bright Data trigger or an already billed Gemini call.
"""
from __future__ import annotations

import io
import json
import re
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
from intelligence.discovery.normalize import canonical_video_url
from intelligence.market_intelligence import brightdata_provider as bd
from intelligence.market_intelligence.carousel_media import (
    download_carousel, carousel_candidate, CarouselError, MAX_SLIDES)
from intelligence.market_intelligence.metric_fields import (
    numeric_fields, metric_coverage)
from intelligence.market_intelligence.tiktok_top100 import canonical_tiktok_profile
from scripts import analyze_marketing_carousel_story as story
from scripts.prepare_market_tiktok_top100 import PROFILE_DATASET

ROOT=Path(__file__).resolve().parents[2]/"marketing-qa"/"carousel-studio"
SCHEMA="mi-tiktok-carousel-studio/v1"
ID=re.compile(r"[0-9a-f]{32}\Z")
NATIVE=re.compile(r"[0-9]{10,25}\Z")
LOCK=threading.Lock()
ACTIVE=set()
POLL_SECONDS=5
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
      state TEXT NOT NULL, phase TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
      total INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
      failed INTEGER NOT NULL DEFAULT 0, snapshot_id TEXT,
      error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      finished_at TEXT, output_dir TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS studio_items(
      run_id TEXT NOT NULL, native_id TEXT NOT NULL, canonical_url TEXT NOT NULL,
      state TEXT NOT NULL, metrics_json TEXT, analysis_json TEXT,
      asset_count INTEGER NOT NULL DEFAULT 0, error TEXT,
      PRIMARY KEY(run_id,native_id),
      FOREIGN KEY(run_id) REFERENCES studio_runs(id)
    );
    CREATE TABLE IF NOT EXISTS studio_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
      created_at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS studio_events_by_run ON studio_events(run_id,id);
    """)
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

def start(kind,value):
    kind,normalized=_input(kind,value)
    if not bd.readiness()["ready"]:
        raise ValueError("Bright Data не настроен на сервере: нужны API-токен и разрешения.")
    if not config.setting("CM_OPENROUTER_API_KEY","").strip():
        from intelligence.market_intelligence.openrouter_credentials import get_market_openrouter_key
        if not get_market_openrouter_key():
            raise ValueError("OpenRouter API-ключ недоступен в серверном окружении.")
    ident=uuid.uuid4().hex
    folder=_dir()/ident
    folder.mkdir(mode=0o700)
    with db() as c:
        c.execute("""INSERT INTO studio_runs
          (id,source_kind,source_input,state,phase,progress,created_at,updated_at,output_dir)
          VALUES(?,?,?,'queued','Подготовка',0,?,?,?)""",
          (ident,kind,normalized,utc(),utc(),str(folder)))
    _event(ident,"Задание создано. Данные сохраняются локально.")
    _thread(ident)
    return {"id":ident,"state":"queued"}

def _thread(ident):
    with LOCK:
        if ident in ACTIVE:
            return
        ACTIVE.add(ident)
    def wrapped():
        try:
            _process(ident)
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

def _wait(snapshot,ident,label):
    cycles=0
    while True:
        state=bd.progress(snapshot)
        if state=="ready":
            return
        if state=="failed":
            raise ValueError(label+": Bright Data сообщил об ошибке.")
        cycles+=1
        if cycles%6==1:
            _event(ident,label+": Bright Data "+state)
        time.sleep(POLL_SECONDS)

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
        _wait(sid,ident,"Метрики")
        for row in bd.download(sid):
            normalized=bd.normalize_record("tiktok",row)
            if normalized and normalized["url"] in batch:
                result[normalized["url"]]=(normalized,row)
    return result

def _discover(ident,kind,source):
    if kind=="post":
        with db() as c:
            sid=c.execute("SELECT snapshot_id FROM studio_runs WHERE id=?",(ident,)).fetchone()
        return [(source,None)],"exact_post"
    snapshot=_trigger_profile(ident,source)
    handle=source.rsplit("@",1)[1].casefold()
    seen={}
    inspected=0
    photos=0
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
        if platform!="tiktok" or "/photo/" not in path:
            continue
        if not path.casefold().startswith("/@"+handle+"/photo/"):
            continue
        author=raw.get("author_username") or raw.get("username")
        if isinstance(author,dict):
            author=author.get("unique_id") or author.get("username")
        if isinstance(author,str) and author.lstrip("@").casefold()!=handle:
            continue
        if raw.get("is_video") is True or str(raw.get("content_type","")).lower() in ("video","clip"):
            continue
        photos+=1
        if native not in seen:
            seen[native]=(canonical,raw)
        if inspected%500==0:
            _event(ident,"Проверено записей Bright Data: "+str(inspected)+
                   "; уникальных каруселей: "+str(len(seen)))
    _event(ident,"Bright Data вернул "+str(inspected)+" записей; отобрано "+
           str(len(seen))+" уникальных каруселей. Полнота истории аккаунта не гарантирована.")
    return list(seen.values()),"returned_profile_snapshot_only"

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

def _analyze(folder,items):
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
    response=story._request(folder,{"prompt":prompt,"slides":items,
                                    "slide_count":len(items),"native_id":"local_studio",
                                    "post_url":"local_studio",
                                    "source_selection_sha256":"local_studio"})
    return response["analysis"],response["usage"]

def _process(ident):
    with db() as c:
        row=c.execute("SELECT * FROM studio_runs WHERE id=?",(ident,)).fetchone()
    kind,source=row["source_kind"],row["source_input"]
    run_dir=_dir()/ident
    targets,scope=_discover(ident,kind,source)
    if not targets:
        _event(ident,"Провайдер не вернул публичных каруселей.",state="completed",
               phase="Завершено",progress=100,finished_at=utc())
        _package(ident)
        return
    _event(ident,"Найдено каруселей: "+str(len(targets)),
           phase="Подготовка метрик",total=len(targets),progress=17)
    # For a post we need a separate Bright Data record. Profile discovery
    # already supplies public counters, but per-post dataset can add detail.
    by_url=_trigger_metrics(ident,[url for url,_ in targets])
    _event(ident,"Метрики собраны; начинаю загрузку изображений.",
           phase="Загрузка изображений",progress=20)
    for index,(url,raw) in enumerate(targets):
        native=canonical_video_url(url)[1]
        item_dir=run_dir/"carousels"/native
        item_dir.mkdir(parents=True,exist_ok=True)
        with db() as c:
            c.execute("""INSERT OR IGNORE INTO studio_items
              (run_id,native_id,canonical_url,state) VALUES(?,?,?,'queued')""",
              (ident,native,url))
        normalized,metrics_raw=by_url.get(url,(None,None))
        metrics=_metrics(metrics_raw or raw,normalized)
        _write_json(item_dir/"metrics.json",metrics)
        with db() as c:
            c.execute("""UPDATE studio_items SET metrics_json=?,state='downloading'
                WHERE run_id=? AND native_id=?""",(json.dumps(metrics,ensure_ascii=False),ident,native))
        _event(ident,f"[{index+1}/{len(targets)}] Загрузка карусели {native}",
               phase="Загрузка каруселей",
               progress=round(20+73*(index+.05)/len(targets),2))
        try:
            manifest=download_carousel(url,item_dir)
            _write_json(item_dir/"manifest.json",manifest)
            inputs=_jpeg_inputs(item_dir,manifest)
            with db() as c:
                c.execute("""UPDATE studio_items SET state='analyzing',asset_count=?
                  WHERE run_id=? AND native_id=?""",(len(inputs),ident,native))
            _event(ident,f"[{index+1}/{len(targets)}] Gemini 3 Flash: {len(inputs)} слайдов",
                   phase="Анализ Gemini",
                   progress=round(20+73*(index+.48)/len(targets),2))
            # Persist pending marker before the potentially paid LLM call.
            _write_json(item_dir/"gemini-request.json",{
                "model":story.MODEL,"prompt_sha256":story.PROMPT_SHA,
                "slide_hashes":[x["input_jpeg_sha256"] for x in inputs],
                "state":"started_no_automatic_retry"})
            analysis,usage=_analyze(item_dir,inputs)
            _write_json(item_dir/"analysis.json",{
                "schema_version":"studio-gemini-story/v1",
                "source_url":url,"model":story.MODEL,
                "prompt_sha256":story.PROMPT_SHA,
                "narrative":analysis,"usage":usage})
            with db() as c:
                c.execute("""UPDATE studio_items SET state='completed',analysis_json=?
                    WHERE run_id=? AND native_id=?""",
                    (json.dumps(analysis,ensure_ascii=False),ident,native))
                c.execute("UPDATE studio_runs SET completed=completed+1 WHERE id=?",(ident,))
            _event(ident,f"[{index+1}/{len(targets)}] Анализ карусели {native} завершён.",
                   phase="Обработка",progress=round(20+73*(index+1)/len(targets),2))
        except Exception as exc:
            error=str(exc)[:300]
            with db() as c:
                c.execute("""UPDATE studio_items SET state='failed',error=?
                    WHERE run_id=? AND native_id=?""",(error,ident,native))
                c.execute("UPDATE studio_runs SET failed=failed+1 WHERE id=?",(ident,))
            _event(ident,f"[{index+1}/{len(targets)}] Ошибка {native}: {error}","error",
                   phase="Обработка",progress=round(20+73*(index+1)/len(targets),2))
    _event(ident,"Формирование полного архива и JSON.",phase="Сборка архива",progress=95)
    _package(ident)
    with db() as c:
        counts=c.execute("SELECT completed,failed FROM studio_runs WHERE id=?",(ident,)).fetchone()
    end="completed" if counts["failed"]==0 else "partial"
    _event(ident,"Готово. Результаты, исходные изображения и логи сохранены на ПК.",
           state=end,phase="Завершено",progress=100,finished_at=utc())

def _package(ident,partial=False):
    folder=_dir()/ident
    if not folder.is_dir():
        return
    with db() as c:
        run=c.execute("SELECT * FROM studio_runs WHERE id=?",(ident,)).fetchone()
        items=c.execute("SELECT * FROM studio_items WHERE run_id=? ORDER BY rowid",(ident,)).fetchall()
    result={
      "schema_version":SCHEMA,
      "run":{k:run[k] for k in ("id","source_kind","source_input","state",
                               "total","completed","failed","created_at","snapshot_id")},
      "profile_snapshot_complete_history_guaranteed":False,
      "unlimited_carousels_in_application":True,
      "assets_are_photo_carousel_images_not_mp4":True,
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
                or src==dest or src==temp or src.suffix in (".db",".sqlite3")):
                continue
            rel=src.relative_to(folder)
            z.write(src,arcname=rel.as_posix(),
                    compress_type=ZIP_STORED if src.suffix.lower() in (".jpg",".jpeg",".png",".webp") else ZIP_DEFLATED)
    temp.replace(dest)

def _row(row):
    return dict(row) if row else None

def list_runs():
    with db() as c:
        rows=c.execute("""SELECT id,source_kind,source_input,state,phase,progress,
                          total,completed,failed,error,created_at,updated_at
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
    return {**_row(row),"items":previews,
            "events":[_row(x) for x in reversed(events)],
            "downloads":available_downloads(ident)}

DOWNLOADS={
    "zip":"carousel-studio-results.zip",
    "json":"full-analysis.json",
    "logs":"events.jsonl",
}

def available_downloads(ident):
    folder=_dir()/ident
    return {k:(folder/name).is_file() for k,name in DOWNLOADS.items()}

def download_path(ident,format_name):
    if not ID.fullmatch(ident) or format_name not in DOWNLOADS:
        return None
    path=_dir()/ident/DOWNLOADS[format_name]
    return path if path.is_file() and not path.is_symlink() else None

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


def recover_interrupted():
    """Never automatically replay paid calls after a server restart."""
    with db() as c:
        interrupted=c.execute("""SELECT id FROM studio_runs WHERE state IN ('queued','running')""").fetchall()
        for row in interrupted:
            c.execute("""UPDATE studio_runs SET state='interrupted',
                phase='Прервано перезапуском',error='Проверь выполненные вызовы перед повтором',
                finished_at=?,updated_at=? WHERE id=?""",(utc(),utc(),row["id"]))
    return len(interrupted)
