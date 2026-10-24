"""PSI Stage 06 project-scoped video API; parent Handler owns origin/CSRF guards."""
from __future__ import annotations
import json
import re
from . import video_jobs
from .video_adapters import AcquisitionProblem

PREFIX=re.compile(r'^/api/projects/([a-f0-9]{32})/sources/([a-f0-9-]{36})/video(?:/(.*))?$')
UUID=r'[a-f0-9-]{36}'


def _error(h,e):
    if isinstance(e,AcquisitionProblem):h.json_response(e.http_status,{'error':str(e),'code':e.code})
    else:h.json_response(500,{'error':'Не удалось выполнить операцию с видео.'})


def handle_get(h,url):
    m=PREFIX.fullmatch(url.path)
    if m is None:return False
    pid,sid,action=m.groups()
    try:
        if url.query:raise AcquisitionProblem('Параметры запроса не поддерживаются.','bad_query',400)
        if action in (None,'jobs'):r=video_jobs.list_jobs(pid,sid)
        elif re.fullmatch('jobs/'+UUID,action):r=video_jobs.result(pid,sid,action.split('/')[1])
        else:raise AcquisitionProblem('Маршрут не найден.','not_found',404)
        h.json_response(200,{'ok':True,**r})
    except AcquisitionProblem as e:_error(h,e)
    except Exception as e:_error(h,e)
    return True


def handle_post(h,route):
    m=PREFIX.fullmatch(route)
    if m is None:return False
    pid,sid,action=m.groups()
    try:
        try:n=int(h.headers.get('Content-Length','0'))
        except (ValueError,TypeError):raise AcquisitionProblem('Неверный размер JSON.','invalid_size',400)
        if not 2<=n<=4000:raise AcquisitionProblem('Неверный размер JSON.','invalid_size',413)
        data=json.loads(h.rfile.read(n).decode('utf-8'))
        if not isinstance(data,dict):raise AcquisitionProblem('Нужен JSON-объект.','invalid_body',400)
        if action=='start':r=video_jobs.enqueue(pid,sid,data);status=202
        elif re.fullmatch('jobs/'+UUID+'/(cancel|retry)',action or ''):
            if data:raise AcquisitionProblem('Параметры операции должны быть пустыми.','invalid_body',400)
            _,jid,kind=action.split('/')
            r=(video_jobs.cancel if kind=='cancel' else video_jobs.retry)(pid,sid,jid);status=200
        else:raise AcquisitionProblem('Маршрут не найден.','not_found',404)
        h.json_response(status,{'ok':True,**r})
    except AcquisitionProblem as e:_error(h,e)
    except (ValueError,UnicodeError,TypeError):h.json_response(400,{'error':'Некорректный JSON.'})
    except Exception as e:_error(h,e)
    return True
