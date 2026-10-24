"""PSI stage 08 local same-origin HTTP routes; never performs inline media/AI."""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs
from . import vision_jobs as jobs
from .vision_engine import VisionProblem

PREFIX=re.compile(r'^/api/projects/([a-f0-9]{32})/sources/([a-f0-9-]{36})/vision(?:/(.*))?$')
UUID='[a-f0-9-]{36}'

def _reply_error(h,e):
    if isinstance(e,VisionProblem):h.json_response(e.http_status,{'error':str(e),'code':e.code})
    else:h.json_response(500,{'error':'Не удалось выполнить Vision-операцию.'})

def handle_get(h,url):
    m=PREFIX.fullmatch(url.path)
    if not m:return False
    pid,sid,action=m.groups()
    try:
        qs=parse_qs(url.query)
        if action in (None,'jobs'):
            if qs:raise VisionProblem('Параметры не поддерживаются.','invalid_query',400)
            data=jobs.list_jobs(pid,sid)
        elif action and re.fullmatch('jobs/'+UUID,action):
            if set(qs)-{'offset','limit'} or any(len(v)!=1 for v in qs.values()):
                raise VisionProblem('Неверная пагинация.','invalid_query',400)
            try:offset=int(qs.get('offset',['0'])[0]);limit=int(qs.get('limit',['20'])[0])
            except ValueError as e:raise VisionProblem('Неверная пагинация.','invalid_query',400) from e
            data=jobs.result(pid,sid,action.split('/')[1],offset=offset,limit=limit)
        else:raise VisionProblem('Маршрут отсутствует.','not_found',404)
        h.json_response(200,{'ok':True,**data})
    except VisionProblem as exc:_reply_error(h,exc)
    except Exception:_reply_error(h,RuntimeError())
    return True

def handle_post(h,route):
    m=PREFIX.fullmatch(route)
    if not m:return False
    pid,sid,action=m.groups()
    try:
        n=int(h.headers.get('Content-Length','0'))
        if not 2<=n<=4000:raise VisionProblem('Неверный размер запроса.','invalid_size',413)
        body=json.loads(h.rfile.read(n).decode('utf-8'))
        if not isinstance(body,dict):raise VisionProblem('Ожидается JSON-объект.','invalid_body',400)
        if action=='prepare':data=jobs.enqueue_prepare(pid,sid,body)
        elif action=='quote':data=jobs.quote(pid,sid,body)
        elif action=='analyze':data=jobs.enqueue_analysis(pid,sid,body)
        elif action and re.fullmatch('jobs/'+UUID+'/retry',action):
            if body:raise VisionProblem('Для повтора нужен пустой JSON.','invalid_body',400)
            data=jobs.retry_prepare(pid,sid,action.split('/')[1])
        else:raise VisionProblem('Маршрут отсутствует.','not_found',404)
        h.json_response(201 if action=='quote' else 202,{'ok':True,**data})
    except VisionProblem as exc:_reply_error(h,exc)
    except (ValueError,UnicodeError,TypeError,json.JSONDecodeError):h.json_response(400,{'error':'Неверный JSON запрос.'})
    except Exception:_reply_error(h,RuntimeError())
    return True
