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
        if action and re.fullmatch('jobs/'+UUID+'/asset',action):
            _send_preview(h,pid,sid,action.split('/')[1])
            return True
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


def _send_preview(h,pid,sid,jid):
    """Byte-range streaming for the verified local MP4, bounded per response."""
    path,size,_=video_jobs.preview_media(pid,sid,jid)
    import re
    header=h.headers.get('Range','')
    if header:
        match=re.fullmatch(r'bytes=(\d+)-(\d*)',header)
        if not match:
            raise AcquisitionProblem('Неверный диапазон видео.','invalid_range',416)
        start=int(match.group(1))
        requested_end=int(match.group(2)) if match.group(2) else size-1
        if start>=size or requested_end<start:
            raise AcquisitionProblem('Диапазон вне MP4.','invalid_range',416)
    else:
        start=0;requested_end=size-1
    end=min(size-1,requested_end,start+4*1024*1024-1)
    length=end-start+1
    h.send_response(206)
    h.send_header('Content-Type','video/mp4')
    h.send_header('Accept-Ranges','bytes')
    h.send_header('Content-Range',f'bytes {start}-{end}/{size}')
    h.send_header('Content-Length',str(length))
    h.send_header('Cache-Control','private, no-store')
    h.send_header('X-Content-Type-Options','nosniff')
    h.send_header('Referrer-Policy','no-referrer')
    h.send_header('Content-Disposition','inline; filename="content-machine-preview.mp4"')
    h.end_headers()
    try:
        with path.open('rb') as stream:
            stream.seek(start)
            remaining=length
            while remaining>0:
                part=stream.read(min(256*1024,remaining))
                if not part:break
                h.wfile.write(part);remaining-=len(part)
    except (BrokenPipeError,ConnectionAbortedError,OSError):
        pass
