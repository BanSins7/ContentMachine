"""Bright Data on-demand public POST metrics; independent of A/B/C.

Only fixed api.brightdata.com endpoints. The Bearer token comes from server
environment CM_BRIGHTDATA_API_TOKEN, never user-facing APIs, Git, or job JSON.
Dataset IDs are documented public post-by-URL scrapers, not private tokens.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from urllib.parse import urlencode

from intelligence.discovery.normalize import canonical_video_url, CandidateError
from intelligence import config
from .contracts import MarketError
from .metric_fields import numeric_fields

ENDPOINT = "https://api.brightdata.com"
DATASETS = {
    "tiktok": "gd_lu702nij2f790tmv9h",
    "instagram": "gd_lyclm20il4r5helnj",
    "youtube": "gd_lk56epmy2i5g7lzu0k",
}
DATASET_OVERRIDE = {
    "tiktok": "CM_BRIGHTDATA_TIKTOK_POSTS_DATASET",
    "instagram": "CM_BRIGHTDATA_INSTAGRAM_REELS_DATASET",
    "youtube": "CM_BRIGHTDATA_YOUTUBE_VIDEOS_DATASET",
}
DATASET_RE = re.compile(r"gd_[a-zA-Z0-9]{8,50}\Z")
# Reject known dataset IDs which return comments rather than video metrics.
INVALID_DATASETS = {"youtube": frozenset({"gd_lk9q0ew71spt1mxywf"})}
SNAPSHOT_RE = re.compile(r"s_[a-zA-Z0-9_-]{8,100}\Z")
MAX_INPUTS = 50
MAX_RESULT_BYTES = 6_000_000
MAX_RESULTS = 500

def dataset_id(platform: str) -> str:
    if platform not in DATASETS:
        raise MarketError("brightdata_platform", "Неподдерживаемая платформа.")
    value = config.setting(DATASET_OVERRIDE[platform], DATASETS[platform]).strip()
    if not DATASET_RE.fullmatch(value) or value in INVALID_DATASETS.get(platform, ()):
        raise MarketError("brightdata_dataset", "Неверный набор данных: требуется публикация, не комментарии.", 503)
    return value

def readiness() -> dict:
    token = config.setting("CM_BRIGHTDATA_API_TOKEN", "").strip()
    enabled = config.setting("CM_BRIGHTDATA_METRICS_ENABLED", "") == "1"
    rights = config.setting("CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED", "") == "1"
    return {"configured":bool(token),"enabled":enabled,"rights_verified":rights,
            "ready":bool(token and enabled and rights),
            "source":"brightdata","secret_exposed":False}

def _token() -> str:
    state=readiness()
    if not state["ready"]:
        raise MarketError("brightdata_not_configured",
            "Bright Data требует однократной настройки токена и прав на сервере.", 503)
    return config.setting("CM_BRIGHTDATA_API_TOKEN").strip()

def _api(method: str, path: str, token: str, *, query=None, payload=None) -> object:
    if not path.startswith("/datasets/v3/") or "://" in path or "?" in path:
        raise MarketError("brightdata_invalid_path","Недопустимый путь провайдера.")
    url=ENDPOINT+path+("?" + urlencode(query) if query else "")
    raw=None if payload is None else json.dumps(payload,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(url,data=raw,method=method,headers={
        "Authorization":"Bearer "+token,
        "Accept":"application/json",
        "Content-Type":"application/json",
        "User-Agent":"ContentMachine-MarketIntelligence/1",
    })
    try:
        with urllib.request.urlopen(req,timeout=35) as result:
            if not 200<=result.status<300:
                raise MarketError("brightdata_http","Bright Data не выполнил запрос.",503)
            body=result.read(MAX_RESULT_BYTES+1)
    except urllib.error.HTTPError as exc:
        # Never leak token, provider body, request or personal metadata.
        raise MarketError("brightdata_http_"+str(exc.code),
            "Bright Data отказал в запросе (HTTP "+str(exc.code)+").",503) from None
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        raise MarketError("brightdata_network","Нет соединения с Bright Data.",503) from None
    if len(body)>MAX_RESULT_BYTES:
        raise MarketError("brightdata_too_large","Слишком большой ответ Bright Data.",413)
    try: return json.loads(body)
    except (UnicodeError,ValueError):
        raise MarketError("brightdata_bad_json","Bright Data вернул некорректные данные.",502) from None

def trigger(platform:str, urls:list[str], *, request=_api) -> str:
    if type(urls) is not list or not 1<=len(urls)<=MAX_INPUTS:
        raise MarketError("brightdata_input_limit","От 1 до 50 видео в пакете.")
    normalized=[]
    for url in urls:
        try: p,vid,canonical=canonical_video_url(url)
        except (CandidateError,ValueError):
            raise MarketError("brightdata_bad_video","Недопустимая ссылка на видео.") from None
        if p!=platform:
            raise MarketError("brightdata_cross_platform","Несовпадение платформ.")
        normalized.append({"url":canonical})
    response=request("POST","/datasets/v3/trigger",_token(),
        query={"dataset_id":dataset_id(platform),"format":"json"},
        payload=normalized)
    ident=response.get("snapshot_id") if type(response) is dict else None
    if type(ident) is not str or not SNAPSHOT_RE.fullmatch(ident):
        raise MarketError("brightdata_trigger_unknown",
            "Результат запуска неизвестен: проверь задания Bright Data перед повтором.",503)
    return ident

def progress(snapshot_id: str, *, request=_api) -> str:
    if type(snapshot_id) is not str or not SNAPSHOT_RE.fullmatch(snapshot_id):
        raise MarketError("brightdata_snapshot_id","Некорректный snapshot ID.")
    result=request("GET","/datasets/v3/progress/"+snapshot_id,_token())
    state=result.get("status") if type(result) is dict else None
    if state not in ("starting","running","ready","failed"):
        raise MarketError("brightdata_progress_unknown","Неизвестный статус провайдера.",503)
    return state

def download(snapshot_id: str, *, request=_api) -> list[dict]:
    if type(snapshot_id) is not str or not SNAPSHOT_RE.fullmatch(snapshot_id):
        raise MarketError("brightdata_snapshot_id","Некорректный snapshot ID.")
    result=request("GET","/datasets/v3/snapshot/"+snapshot_id,_token(),
                   query={"format":"json"})
    if not isinstance(result,list) or len(result)>MAX_RESULTS or any(type(x) is not dict for x in result):
        raise MarketError("brightdata_invalid_result","Провайдер вернул неподдерживаемую выборку.",502)
    return result

FIELDS = {
    "tiktok": {
        "views": ("play_count","view_count","views","video_play_count"),
        "likes": ("digg_count","like_count","likes"),
        "comments": ("comment_count","comments"),
        "shares": ("share_count","shares"),
        "saves": ("collect_count","save_count","saves"),
        "published_at": ("create_time","date_posted","post_date_created"),
        "author": ("author","author_username","username"),
    },
    "instagram": {
        "views": ("views","video_view_count","video_play_count","play_count"),
        "likes": ("likes","likes_count","like_count"),
        "comments": ("num_comments","comments_count","comment_count"),
        "shares": ("shares_count","share_count"),
        "saves": ("saves_count","save_count"),
        "published_at": ("date_posted","taken_at","create_time"),
        "author": ("user_posted","username"),
    },
    "youtube": {
        "views": ("views","view_count","viewCount"),
        "likes": ("likes","like_count","likeCount"),
        "comments": ("num_comments","comments","comment_count","commentCount"),
        "shares": (),
        "saves": (),
        "published_at": ("date_posted","published_at","upload_date"),
        "author": ("youtuber","channel_id"),
    },
}

def _get(obj, names):
    for key in names:
        val=obj.get(key)
        if val is not None: return val
    return None

def _count(obj,keys):
    value=_get(obj,keys)
    if value is None or isinstance(value,bool):return None
    if type(value) is int and 0<=value<2**63:return value
    if type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,18})",value):
        num=int(value)
        return num if num<2**63 else None
    return None

def _date(value):
    if value is None:return None
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        try: t=datetime.fromtimestamp(value,timezone.utc)
        except (OverflowError,ValueError,OSError):return None
        return t.isoformat()
    if type(value) is str:
        try:
            t=datetime.fromisoformat(value.replace("Z","+00:00"))
            return t.astimezone(timezone.utc).isoformat() if t.tzinfo else None
        except ValueError: pass
    return None

def normalize_record(platform:str, record:dict) -> dict|None:
    if type(record) is not dict or platform not in FIELDS:
        return None
    if record.get("error") or record.get("error_code"):
        return None
    raw=_get(record,("url","video_url","post_url","link"))
    if type(raw) is not str:return None
    try: p,native,canonical=canonical_video_url(raw)
    except (CandidateError,ValueError):return None
    if p!=platform:return None
    fields=FIELDS[platform]
    counts={name:_count(record,fields[name]) for name in ("views","likes","comments","shares","saves")}
    extra = numeric_fields(record)
    if all(val is None for val in counts.values()) and not extra:return None
    author=_get(record,fields["author"])
    if type(author) is dict:
        author=author.get("username") or author.get("unique_id")
    return {"platform":platform,"native_id":native,"url":canonical,
            "published_at":_date(_get(record,fields["published_at"])),
            "author":author[:120] if type(author) is str else None,
            "numeric_fields":extra,**counts}
