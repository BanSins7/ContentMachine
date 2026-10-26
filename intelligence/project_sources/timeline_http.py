"""Local same-origin PSI Stage09 API: explicit queue, inspect, export, retry."""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs
from . import timeline_jobs as jobs
from .timeline_fusion import FusionProblem

PREFIX=re.compile(r'^/api/projects/([a-f0-9]{32})/sources/([a-f0-9-]{36})/fusion(?:/(.*))?$')
UUID='[a-f0-9-]{36}'


def _error(h,exc):
    if isinstance(exc,FusionProblem):h.json_response(exc.http_status,{'error':str(exc),'code':exc.code})
    else:h.json_response(500,{'error':'Не удалось собрать таймлайн.'})


def handle_get(h,url):
    m=PREFIX.fullmatch(url.path)
    if not m:return False
    pid,sid,action=m.groups()
    try:
        qs=parse_qs(url.query)
        if action in (None,'jobs'):
            if qs:raise FusionProblem('Лишние параметры.','invalid_query',400)
            data=jobs.list_jobs(pid,sid)
        elif action and re.fullmatch('jobs/'+UUID+r'(?:/export)?',action):
            export=action.endswith('/export')
            if set(qs)-({'offset','limit'} if not export else set()) or any(len(v)!=1 for v in qs.values()):
                raise FusionProblem('Недопустимая пагинация.','invalid_query',400)
            try:offset=int(qs.get('offset',['0'])[0]);limit=int(qs.get('limit',['30'])[0])
            except ValueError as exc:raise FusionProblem('Неверная пагинация.','invalid_query',400) from exc
            data=jobs.result(pid,sid,action.split('/')[1],offset=offset,limit=limit,export=export)
        else:raise FusionProblem('Маршрут отсутствует.','not_found',404)
        h.json_response(200,{'ok':True,**data})
    except FusionProblem as exc:_error(h,exc)
    except Exception:_error(h,RuntimeError())
    return True


def handle_post(h,route):
    m=PREFIX.fullmatch(route)
    if not m:return False
    pid,sid,action=m.groups()
    try:
        n=int(h.headers.get('Content-Length','0'))
        if not 2<=n<=3000:raise FusionProblem('Неверный размер запроса.','invalid_size',413)
        body=json.loads(h.rfile.read(n).decode('utf-8'))
        if not isinstance(body,dict):raise FusionProblem('Нужен JSON объект.','invalid_body',400)
        if action in (None,'start'):data=jobs.enqueue(pid,sid,body)
        elif action and re.fullmatch('jobs/'+UUID+'/retry',action):
            if body:raise FusionProblem('Повтор без аргументов.','invalid_body',400)
            data=jobs.retry(pid,sid,action.split('/')[1])
        else:raise FusionProblem('Маршрут отсутствует.','not_found',404)
        h.json_response(202,{'ok':True,**data})
    except FusionProblem as exc:_error(h,exc)
    except (ValueError,UnicodeError,TypeError,json.JSONDecodeError):h.json_response(400,{'error':'Некорректный JSON.'})
    except Exception:_error(h,RuntimeError())
    return True
