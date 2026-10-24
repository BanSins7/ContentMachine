"""PSI Stage 11 project-scoped same-origin context endpoints; synchronous PG only."""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs
from . import context_service as svc
from .context_builder import ContextProblem

PREFIX=re.compile(r'^/api/projects/([0-9a-f]{32})/context(?:/(.*))?$')
UUID=r'[0-9a-f-]{36}'

def _error(h,exc):
    if isinstance(exc,ContextProblem):h.json_response(exc.http_status,{'error':str(exc),'code':exc.code})
    else:h.json_response(500,{'error':'Не удалось обработать Project Context.'})

def _qs(url,allowed):
    q=parse_qs(url.query,keep_blank_values=True)
    if set(q)-set(allowed) or any(len(x)!=1 for x in q.values()):
        raise ContextProblem('Некорректные параметры запроса.','invalid_query',400)
    return {k:v[0] for k,v in q.items()}

def handle_get(h,url):
    m=PREFIX.fullmatch(url.path)
    if not m:return False
    pid,action=m.groups()
    try:
        if action in ('draft',None):
            if url.query:raise ContextProblem('Параметры не допускаются.','invalid_query',400)
            data=svc.preview(pid)
        elif action=='snapshots':
            q=_qs(url,('offset','limit'))
            try:offset=int(q.get('offset','0'));limit=int(q.get('limit','20'))
            except ValueError as e:raise ContextProblem('Неверная пагинация.','invalid_query',400) from e
            data=svc.list_snapshots(pid,offset=offset,limit=limit)
        elif action=='approved':
            if url.query:raise ContextProblem('Неверные параметры.','invalid_query',400)
            data=svc.approved(pid)
        elif action=='compare':
            q=_qs(url,('left','right'))
            if set(q)!={'left','right'}:raise ContextProblem('Нужны left и right.','invalid_query',400)
            data=svc.compare(pid,q['left'],q['right'])
        elif action and re.fullmatch('snapshots/'+UUID,action):
            if url.query:raise ContextProblem('Неверные параметры.','invalid_query',400)
            data=svc.get_snapshot(pid,action.split('/')[1])
        elif action and re.fullmatch('snapshots/'+UUID+'/manifest',action):
            if url.query:raise ContextProblem('Неверные параметры.','invalid_query',400)
            data=svc.get_snapshot(pid,action.split('/')[1],manifest_only=True)
        elif action and re.fullmatch('snapshots/'+UUID+'/selection',action):
            q=_qs(url,('token_budget',))
            try:budget=int(q.get('token_budget','1200'))
            except ValueError as e:raise ContextProblem('Некорректный token budget.','invalid_budget',400) from e
            data=svc.selection(pid,action.split('/')[1],token_budget=budget)
        else:raise ContextProblem('Маршрут не найден.','not_found',404)
        h.json_response(200,{'ok':True,**data})
    except ContextProblem as e:_error(h,e)
    except Exception:_error(h,RuntimeError())
    return True

def handle_post(h,route):
    m=PREFIX.fullmatch(route)
    if not m:return False
    pid,action=m.groups()
    try:
        try:n=int(h.headers.get('Content-Length','0'))
        except ValueError as e:raise ContextProblem('Некорректный Content-Length.','invalid_size',400) from e
        if not 2<=n<=12000:raise ContextProblem('Размер JSON не разрешён.','invalid_size',413)
        data=json.loads(h.rfile.read(n).decode('utf-8'))
        if not isinstance(data,dict):raise ContextProblem('Ожидается JSON-объект.','invalid_body',400)
        if action=='build':result=svc.create_snapshot(pid,data);code=201 if result['created'] else 200
        elif action and re.fullmatch('snapshots/'+UUID+'/decide',action):
            result=svc.decide(pid,action.split('/')[1],data);code=201
        else:raise ContextProblem('Маршрут не найден.','not_found',404)
        h.json_response(code,{'ok':True,**result})
    except ContextProblem as e:_error(h,e)
    except (ValueError,UnicodeError,TypeError,json.JSONDecodeError):h.json_response(400,{'error':'Некорректный JSON.'})
    except Exception:_error(h,RuntimeError())
    return True
