"""Local same-origin HTTP adapter for manually approved website AI jobs."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs
from . import web_jobs as jobs
from .web_extractor import WebProblem

PREFIX=re.compile(r'^/api/projects/([a-f0-9]{32})/sources/([a-f0-9-]{36})/web(?:/(.*))?$')
UUID=r'[a-f0-9-]{36}'


def _error(h,exc):
    if isinstance(exc,WebProblem):h.json_response(exc.http_status,{'error':str(exc),'code':exc.code})
    else:h.json_response(500,{'error':'Не удалось выполнить веб-операцию.'})


def handle_get(h,url):
    m=PREFIX.fullmatch(url.path)
    if not m:return False
    pid,sid,action=m.groups()
    try:
        if url.query:
            raise WebProblem('invalid_query','Параметры запроса не поддерживаются.',400)
        if action in (None,'jobs'):
            data=jobs.list_runs(pid,sid)
        elif re.fullmatch('jobs/'+UUID,action):
            data=jobs.result(pid,sid,action.split('/')[1])
        else:raise WebProblem('not_found','Маршрут не найден.',404)
        h.json_response(200,{'ok':True,**data})
    except WebProblem as exc:_error(h,exc)
    except Exception:_error(h,RuntimeError())
    return True


def handle_post(h,route):
    m=PREFIX.fullmatch(route)
    if not m:return False
    pid,sid,action=m.groups()
    try:
        n=int(h.headers.get('Content-Length','0'))
        if n<2 or n>8000:raise WebProblem('invalid_size','Некорректный размер запроса.',413)
        data=json.loads(h.rfile.read(n).decode('utf-8'))
        if not isinstance(data,dict):raise WebProblem('invalid_body','Ожидается JSON-объект.',400)
        if action=='quote':
            if data:raise WebProblem('invalid_body','Для сметы нужен пустой JSON объект.',400)
            result=jobs.cost_quote(pid,sid);status=201
        elif action=='start':
            result=jobs.enqueue(pid,sid,data);status=202
        else:raise WebProblem('not_found','Маршрут не найден.',404)
        h.json_response(status,{'ok':True,**result})
    except WebProblem as exc:_error(h,exc)
    except (ValueError,UnicodeError,TypeError):h.json_response(400,{'error':'Некорректный JSON запрос.'})
    except Exception:_error(h,RuntimeError())
    return True
