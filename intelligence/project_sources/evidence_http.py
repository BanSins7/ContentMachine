"""PSI Stage10 same-origin evidence API. No network/model work inline."""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs
from . import evidence_jobs as jobs
from .evidence_normalizer import EvidenceProblem

PROJECT=re.compile(r'^/api/projects/([0-9a-f]{32})/evidence(?:/(.*))?$')
SOURCE=re.compile(r'^/api/projects/([0-9a-f]{32})/sources/([0-9a-f-]{36})/evidence(?:/(.*))?$')
UUID='[0-9a-f-]{36}'


def _error(h,error):
    if isinstance(error,EvidenceProblem):
        h.json_response(error.http_status,{'error':str(error),'code':error.code})
    else:h.json_response(500,{'error':'Не удалось выполнить проверку Evidence.'})


def _pagination(url):
    q=parse_qs(url.query,keep_blank_values=True)
    if set(q)-{'offset','limit'} or any(len(v)!=1 for v in q.values()):
        raise EvidenceProblem('Некорректные параметры запроса.','invalid_pagination',400)
    try:return int(q.get('offset',['0'])[0]),int(q.get('limit',['50'])[0])
    except ValueError as e:raise EvidenceProblem('Некорректная пагинация.','invalid_pagination',400) from e


def handle_get(h,url):
    s=SOURCE.fullmatch(url.path);p=PROJECT.fullmatch(url.path)
    if not s and not p:return False
    try:
        if s:
            pid,sid,action=s.groups()
            if action in (None,'jobs'):
                if url.query:raise EvidenceProblem('Параметры не поддерживаются.','invalid_query',400)
                value=jobs.list_jobs(pid,sid)
            elif action=='items':
                offset,limit=_pagination(url)
                value=jobs.list_evidence(pid,sid=sid,offset=offset,limit=limit)
            else:raise EvidenceProblem('Неизвестный маршрут.','not_found',404)
        else:
            pid,action=p.groups()
            if action in (None,'items'):
                offset,limit=_pagination(url)
                value=jobs.list_evidence(pid,offset=offset,limit=limit)
            elif action and re.fullmatch(UUID+'/verify',action):
                if url.query:raise EvidenceProblem('Параметры не поддерживаются.','invalid_query',400)
                value=jobs.verify(pid,action.split('/')[0])
            else:raise EvidenceProblem('Неизвестный маршрут.','not_found',404)
        h.json_response(200,{'ok':True,**value})
    except EvidenceProblem as error:_error(h,error)
    except Exception:_error(h,RuntimeError())
    return True


def handle_post(h,path):
    s=SOURCE.fullmatch(path);p=PROJECT.fullmatch(path)
    if not s and not p:return False
    try:
        n=int(h.headers.get('Content-Length','0'))
        if not 2<=n<=4000:raise EvidenceProblem('Неверный размер запроса.','invalid_size',413)
        data=json.loads(h.rfile.read(n).decode('utf-8'))
        if not isinstance(data,dict):raise EvidenceProblem('Ожидается JSON объект.','invalid_body',400)
        if s:
            pid,sid,action=s.groups()
            if action=='enqueue':value=jobs.enqueue(pid,sid,data);code=202
            elif action and re.fullmatch('jobs/'+UUID+'/retry',action):
                if data:raise EvidenceProblem('Нужен пустой JSON.','invalid_body',400)
                value=jobs.retry(pid,sid,action.split('/')[1]);code=202
            else:raise EvidenceProblem('Маршрут не найден.','not_found',404)
        else:
            pid,action=p.groups()
            if action=='profile/import':value=jobs.import_profile(pid,data);code=200 if data.get('dry_run') else 202
            elif action and re.fullmatch(UUID+'/review',action):
                value=jobs.review(pid,action.split('/')[0],data);code=201
            else:raise EvidenceProblem('Маршрут не найден.','not_found',404)
        h.json_response(code,{'ok':True,**value})
    except EvidenceProblem as e:_error(h,e)
    except (ValueError,TypeError,UnicodeError,json.JSONDecodeError):
        h.json_response(400,{'error':'Некорректный JSON запрос.'})
    except Exception:_error(h,RuntimeError())
    return True
