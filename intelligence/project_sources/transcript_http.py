"""PSI Stage 07 same-origin HTTP adapter, no inline ASR or downloads."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs
from . import transcript_jobs as jobs
from .transcript_normalizer import TranscriptProblem

PREFIX = re.compile(r'^/api/projects/([a-f0-9]{32})/sources/([a-f0-9-]{36})/transcription(?:/(.*))?$')
JOB_ID='[a-f0-9-]{36}'


def _error(h,e):
    if isinstance(e,TranscriptProblem):h.json_response(e.http_status,{'error':str(e),'code':e.code})
    else:h.json_response(500,{'error':'Не удалось обработать транскрипт.'})


def handle_get(h,url):
    m=PREFIX.fullmatch(url.path)
    if not m:return False
    pid,sid,action=m.groups()
    try:
        qs=parse_qs(url.query)
        if action in (None,'jobs'):
            if qs:raise TranscriptProblem('Лишние параметры.','invalid_query',400)
            result=jobs.list_jobs(pid,sid)
        elif action and re.fullmatch('jobs/'+JOB_ID,action):
            if set(qs)-{'offset','limit'} or any(len(x)!=1 for x in qs.values()):
                raise TranscriptProblem('Некорректная пагинация.','invalid_query',400)
            try:offset=int(qs.get('offset',['0'])[0]);limit=int(qs.get('limit',['50'])[0])
            except ValueError as exc:raise TranscriptProblem('Неверная пагинация.','invalid_query',400) from exc
            result=jobs.result(pid,sid,action.split('/')[1],offset=offset,limit=limit)
        else:raise TranscriptProblem('Неизвестный маршрут.','not_found',404)
        h.json_response(200,{'ok':True,**result})
    except TranscriptProblem as e:_error(h,e)
    except Exception:_error(h,RuntimeError())
    return True


def handle_post(h,route):
    m=PREFIX.fullmatch(route)
    if not m:return False
    pid,sid,action=m.groups()
    try:
        n=int(h.headers.get('Content-Length','0'))
        if n<2 or n>3500:raise TranscriptProblem('Размер запроса недопустим.','invalid_size',413)
        data=json.loads(h.rfile.read(n).decode('utf-8'))
        if not isinstance(data,dict):raise TranscriptProblem('Требуется JSON объект.','invalid_body',400)
        if action=='start':
            result=jobs.enqueue(pid,sid,data);code=202
        elif action and re.fullmatch('jobs/'+JOB_ID+'/retry',action):
            if data:raise TranscriptProblem('Повтор не принимает параметров.','invalid_body',400)
            result=jobs.retry(pid,sid,action.split('/')[1]);code=202
        else:raise TranscriptProblem('Неизвестный маршрут.','not_found',404)
        h.json_response(code,{'ok':True,**result})
    except TranscriptProblem as e:_error(h,e)
    except (ValueError,UnicodeError,TypeError,json.JSONDecodeError):h.json_response(400,{'error':'Неверный JSON запрос.'})
    except Exception:_error(h,RuntimeError())
    return True
