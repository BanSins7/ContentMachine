"""Small stage 04 HTTP adapter; handler already enforces localhost and mutation header."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs
from . import document_jobs as jobs

PID = r'[0-9a-f]{32}'
FID = r'[0-9a-f-]{36}'
ROOT = re.compile(rf'^/api/projects/({PID})/research-files/({FID})/(extract|extractions(?:/{FID})?(?:/retry)?)$')


def _error(handler, exc):
    if isinstance(exc,jobs.DocumentJobProblem):
        handler.json_response(exc.http_status,{'error':str(exc)})
    else:
        handler.json_response(503,{'error':'Не удалось получить доступ к очереди обработки.'})


def handle_get(handler,url):
    found=ROOT.fullmatch(url.path)
    if found is None:return False
    pid,fid,action=found.groups()
    if action=='extractions':
        if url.query:handler.json_response(400,{'error':'Недопустимые параметры.'})
        else:
            try:handler.json_response(200,{'ok':True,**jobs.list_jobs(pid,fid)})
            except jobs.DocumentJobProblem as exc:_error(handler,exc)
        return True
    found_job=re.fullmatch('extractions/('+FID+')',action)
    if found_job:
        try:
            q=parse_qs(url.query)
            if set(q)-{'offset','limit'} or any(len(x)!=1 for x in q.values()):
                raise jobs.DocumentJobProblem('Некорректная пагинация.',400)
            if any(not re.fullmatch('[0-9]{1,5}',s[0]) for s in q.values()):
                raise jobs.DocumentJobProblem('Некорректная пагинация.',400)
            result=jobs.get_result(pid,fid,found_job.group(1),offset=int(q.get('offset',['0'])[0]),
                                   limit=int(q.get('limit',['60'])[0]))
            handler.json_response(200,{'ok':True,**result})
        except jobs.DocumentJobProblem as exc:_error(handler,exc)
        return True
    handler.json_response(404,{'error':'Неизвестный маршрут.'})
    return True


def handle_post(handler,path):
    match=ROOT.fullmatch(path)
    if match is None:return False
    pid,fid,action=match.groups()
    try:
        length=int(handler.headers.get('Content-Length','0'))
        if not 2<=length<=4096:
            raise jobs.DocumentJobProblem('Недопустимый размер запроса.',413)
        payload=json.loads(handler.rfile.read(length).decode('utf-8'))
        if not isinstance(payload,dict):
            raise jobs.DocumentJobProblem('Ожидается JSON-объект.',400)
        if action=='extract':
            result=jobs.enqueue(pid,fid,payload);code=202
        elif re.fullmatch('extractions/'+FID+'/retry',action):
            if payload:
                raise jobs.DocumentJobProblem('Повторный запуск не принимает параметров.',400)
            jobid=action.split('/')[1]
            result=jobs.retry(pid,fid,jobid);code=202
        else:
            raise jobs.DocumentJobProblem('Маршрут не поддерживает POST.',405)
        handler.json_response(code,{'ok':True,**result})
    except jobs.DocumentJobProblem as exc:_error(handler,exc)
    except (ValueError,UnicodeError,TypeError,json.JSONDecodeError):
        handler.json_response(400,{'error':'Неверный формат запроса.'})
    except OSError:_error(handler,RuntimeError())
    return True
