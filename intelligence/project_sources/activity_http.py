"""Stage13 same-origin read-only project processing status."""
from __future__ import annotations
import re
from urllib.parse import parse_qs
from .activity import ActivityProblem, activity

ROUTE=re.compile(r'^/api/projects/([0-9a-f]{32})/source-activity$')

def handle_get(handler,url):
    match=ROUTE.fullmatch(url.path)
    if not match:return False
    try:
        params=parse_qs(url.query,keep_blank_values=True)
        if set(params)-{'offset','limit'} or any(len(v)!=1 for v in params.values()):
            raise ActivityProblem('Недопустимые параметры.',400,'invalid_query')
        for value in params.values():
            if not re.fullmatch('[0-9]{1,6}',value[0]):
                raise ActivityProblem('Некорректная пагинация.',400,'invalid_pagination')
        result=activity(match.group(1),
                        offset=int(params.get('offset',['0'])[0]),
                        limit=int(params.get('limit',['30'])[0]))
        handler.json_response(200,{'ok':True,**result})
    except ActivityProblem as exc:
        handler.json_response(exc.http_status,{'error':str(exc),'code':exc.code})
    except Exception:
        handler.json_response(503,{'error':'История обработки временно недоступна.'})
    return True
