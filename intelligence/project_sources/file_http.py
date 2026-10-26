"""HTTP adapter for stage 03. Called only by app.Handler after same-origin checks."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs,quote
from . import file_library as lib

PREFIX=re.compile(r'^/api/projects/([0-9a-f]{32})/research-files(?:/(.+))?$')
FILE_ID=r'([0-9a-f-]{36})'


def _error(handler, exc):
    if isinstance(exc,lib.FileProblem):handler.json_response(exc.http_status,{'error':str(exc)})
    else:handler.json_response(500,{'error':'Не удалось выполнить файловую операцию.'})


def handle_get(handler,url):
    m=PREFIX.fullmatch(url.path)
    if m is None:return False
    pid,rest=m.groups()
    try:
        if not rest:
            qs=parse_qs(url.query)
            if set(qs)-{'include_archived'} or qs.get('include_archived',['0']) not in (['0'],['1']):
                raise lib.FileProblem('Некорректные параметры.',400)
            result=lib.list_files(pid,include_archived=qs.get('include_archived',['0'])==['1'])
            handler.json_response(200,{'ok':True,**result})
        elif re.fullmatch(FILE_ID+r'/versions',rest):
            fid=rest.rsplit('/',1)[0]
            handler.json_response(200,{'ok':True,**lib.versions(pid,fid)})
        elif re.fullmatch(FILE_ID+r'/download',rest):
            qs=parse_qs(url.query)
            if set(qs)-{'version'} or len(qs.get('version',[]))>1:
                raise lib.FileProblem('Некорректная версия.',400)
            version_raw=qs.get('version',[None])[0]
            version=None
            if version_raw is not None:
                if not re.fullmatch('[1-9][0-9]{0,2}',version_raw):
                    raise lib.FileProblem('Некорректная версия.',400)
                version=int(version_raw)
            data,name,mime=lib.file_download(pid,rest.rsplit('/',1)[0],version)
            handler.send_response(200)
            handler.send_header('Content-Type','application/octet-stream')
            handler.send_header('Content-Length',str(len(data)))
            handler.send_header('Content-Disposition',"attachment; filename=\"research-file\"; filename*=UTF-8''"+quote(name,safe=''))
            handler.send_header('Cache-Control','no-store')
            handler.send_header('X-Content-Type-Options','nosniff')
            handler.send_header('Referrer-Policy','no-referrer')
            handler.end_headers()
            try:handler.wfile.write(data)
            except (BrokenPipeError,ConnectionAbortedError):pass
        else:handler.json_response(404,{'error':'Неизвестный файл.'})
    except lib.FileProblem as exc:_error(handler,exc)
    except (OSError,ValueError):_error(handler,RuntimeError())
    return True


def handle_post(handler,route):
    m=PREFIX.fullmatch(route)
    if m is None:return False
    pid,rest=m.groups()
    try:
        size=int(handler.headers.get('Content-Length','0'))
        if not 2<=size<=lib.MAX_POST_BYTES:
            raise lib.FileProblem('Некорректный размер запроса.',413)
        payload=json.loads(handler.rfile.read(size).decode('utf-8'))
        if not isinstance(payload,dict):raise lib.FileProblem('Ожидается JSON-объект.',400)
        if not rest:
            result=lib.add_file(pid,payload);code=201
        elif rest=='import-legacy':
            if set(payload)-{'dry_run'}:raise lib.FileProblem('Недопустимые параметры.',400)
            result=lib.import_legacy(pid,dry_run=payload.get('dry_run',False));code=200
        elif re.fullmatch(FILE_ID+r'/replace',rest):
            result=lib.replace_file(pid,rest.split('/')[0],payload);code=201
        elif re.fullmatch(FILE_ID+r'/(archive|restore)',rest):
            if set(payload)!={'expected_updated_at'}:raise lib.FileProblem('Требуется expected_updated_at.',400)
            fid,op=rest.split('/')
            result=(lib.archive_file if op=='archive' else lib.restore_file)(pid,fid,payload['expected_updated_at']);code=200
        else:raise lib.FileProblem('Неизвестная операция.',404)
        handler.json_response(code,{'ok':True,**result})
    except lib.FileProblem as exc:_error(handler,exc)
    except (ValueError,UnicodeError,TypeError,json.JSONDecodeError):handler.json_response(400,{'error':'Некорректный запрос.'})
    except OSError:_error(handler,RuntimeError())
    return True
