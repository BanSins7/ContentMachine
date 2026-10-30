#!/usr/bin/env python3
"""ContentMachine local dashboard: Instagram exact Reels downloader + speech-to-text.

Run on your own computer; binds only to 127.0.0.1:8765.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent
SITE = BASE / 'site'
WORKER = BASE / 'worker'
sys.path.insert(0, str(WORKER))
import local_worker as worker  # noqa: E402
import tiktok_worker as tiktok  # noqa: E402
import youtube_worker as youtube  # noqa: E402
from openrouter_transcribe import BUDGET_USD, MODEL, get_key, load_usage  # noqa: E402

HOST, PORT = '127.0.0.1', 8765
ARCHIVE = Path.home() / 'Downloads' / 'InstagramArchive'
ACCOUNT_RE = re.compile(r'[A-Za-z0-9._]{1,30}\Z')
CODE_RE = re.compile(r'[A-Za-z0-9_-]{5,80}\Z')
POST_RE = re.compile(r'/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,80})/?\Z')
EXTENSIONS = {'.mp4': 'video/mp4', '.srt': 'application/x-subrip', '.txt': 'text/plain; charset=utf-8', '.json': 'application/json; charset=utf-8'}
PROFILE_RE = re.compile(r'(?:https?://(?:www\.)?instagram\.com/)?@?([A-Za-z0-9._]{1,30})/(?:reels/)?\Z|(?:https?://(?:www\.)?instagram\.com/)?@?([A-Za-z0-9._]{1,30})\Z')
discover_pool = ThreadPoolExecutor(max_workers=1)
discover_lock = threading.Lock()
discover_jobs: dict[str, dict] = {}


def canonical_reel(value: str) -> tuple[str, str]:
    value = value.strip()
    if not value.startswith(('https://', 'http://')):
        value = 'https://' + value
    parsed = urlparse(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.instagram.com', 'instagram.com') or parsed.port or parsed.username or parsed.password:
        raise ValueError('Принимаются только HTTPS-ссылки на публикации Instagram.')
    match = POST_RE.fullmatch(parsed.path)
    if not match:
        raise ValueError('Нужна ссылка вида https://www.instagram.com/reel/SHORTCODE/')
    code = match.group(1)
    kind = 'reel' if parsed.path.startswith(('/reel/', '/reels/')) else 'p'
    return f'https://www.instagram.com/{kind}/{code}/', code


def library_items():
    rows = []
    if not ARCHIVE.is_dir():
        return rows
    for file in ARCHIVE.glob('*/*.json'):
        try:
            data = json.loads(file.read_text(encoding='utf-8'))
            account, code = str(data.get('account', '')), str(data.get('shortcode', ''))
            if not ACCOUNT_RE.fullmatch(account) or not CODE_RE.fullmatch(code):
                continue
            folder = ARCHIVE / account
            video = folder / f'{code}.mp4'
            if not video.is_file():
                continue
            rows.append({
                'account': account, 'shortcode': code,
                'source_url': data.get('source_url', ''),
                'status': data.get('subtitle_status', 'unknown'),
                'engine': data.get('subtitle_engine', ''),
                'date': data.get('saved_at_utc', ''),
                'size_mb': round(video.stat().st_size / (1024 * 1024), 1),
                'files': {ext.lstrip('.'): (folder / (code + ext)).exists() for ext in EXTENSIONS},
            })
        except (OSError, ValueError, TypeError):
            pass
    rows.sort(key=lambda row: row['date'], reverse=True)
    return rows


def update_discovery(job_id: str, **changes):
    with discover_lock:
        job = discover_jobs.get(job_id)
        if job is not None:
            if job.get('cancel_requested'):
                return True
            job.update(changes)
    return False


def platform_stats(instagram, tikvideos, youtube_videos, total_spent=None):
    # Compatibility with existing 2-platform tests and older callers.
    if total_spent is None:
        total_spent, youtube_videos = youtube_videos, []
    # Shared OpenRouter usage counter includes every platform (and past requests).
    # Attribute TikTok by per-video metadata; remaining historical usage belongs to Instagram.
    tiktok_spent = sum(float(x.get('openrouter_cost_usd') or 0) for x in tikvideos)
    youtube_spent = sum(float(x.get('openrouter_cost_usd') or 0) for x in youtube_videos)
    return {
        'instagram': {'count': len(instagram), 'spent_usd': max(0.0, round(total_spent - tiktok_spent - youtube_spent, 8))},
        'tiktok': {'count': len(tikvideos), 'spent_usd': round(tiktok_spent, 8)},
        'youtube': {'count': len(youtube_videos), 'spent_usd': round(youtube_spent, 8)},
    }


def discovery_status():
    with discover_lock:
        result = [dict(v) for v in discover_jobs.values()][-10:]
    # AutoUpdate must wait while durable offline imports are active.
    try:
        from intelligence.discovery.service import connect
        from intelligence import config
        if config.enabled():
            with connect() as conn:
                active = conn.execute("SELECT 1 FROM jobs WHERE job_type='discovery.import' AND state IN ('queued','running') LIMIT 1").fetchone()
                if active:
                    result.append({'id': 'durable-import', 'status': 'processing', 'count': 0})
    except Exception:
        pass
    return result


def app_state():
    with worker.job_lock:
        jobs = [{
            'id': job_id, 'code': Path(j.get('video_path', '')).stem,
            'status': j.get('status', 'queued'), 'error': j.get('error', ''),
            'result': j.get('result', ''), 'phase': j.get('phase', ''),
            'platform': j.get('platform', 'instagram'),
            'cancel_requested': bool(j.get('cancel_requested', False)),
            'progress': j.get('progress'), 'code': j.get('code') or Path(j.get('video_path', '')).stem,
        } for job_id, j in list(worker.jobs.items())[-1000:]]
    has_key = bool(get_key(WORKER))
    usage = load_usage(WORKER)
    instagram = library_items()
    tikvideos = tiktok.library_items()
    youtube_videos = youtube.library_items()
    return {'ok': True, 'build_revision': os.environ.get('CM_SERVING_REVISION', 'unknown')[:12],
            'jobs': jobs, 'library': instagram, 'tiktok_library': tikvideos,
            'platforms': platform_stats(instagram, tikvideos, youtube_videos, usage['spent_usd']),
            'discovery': discovery_status(),
            'configuration': {'engine': 'openrouter' if has_key else 'local',
                              'openrouter_configured': has_key,
                              'model': MODEL if has_key else worker.MODEL_NAME,
                              'language': worker.LANGUAGE or 'auto',
                              'spent_usd': usage['spent_usd'], 'budget_usd': BUDGET_USD,
                              'archive': str(ARCHIVE)}}


def discover_profile(discovery_id: str, username: str):
    """Best-effort public profile enumeration. No cookies and no bypass of Instagram limits."""
    try:
        import yt_dlp
        found: list[str] = []
        seen: set[str] = set()
        # User profiles can require login. We show a clear error, never invent entries.
        settings = {'quiet': True, 'no_warnings': True, 'extract_flat': 'in_playlist',
                    'playlistend': 500, 'socket_timeout': 20, 'retries': 1,
                    'ignoreerrors': True}
        with yt_dlp.YoutubeDL(settings) as downloader:
            payload = downloader.extract_info(f'https://www.instagram.com/{username}/', download=False)
            if not isinstance(payload, dict):
                raise ValueError('Instagram не отдал список публикаций профиля.')
            entries = payload.get('entries')
            if entries is None:
                raise ValueError('Профиль не вернул список видео. Instagram может требовать вход; вставь Reels-ссылки списком.')
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                link = str(entry.get('webpage_url') or entry.get('url') or '')
                match = POST_RE.fullmatch(urlparse(link).path) if link.startswith('http') else None
                shortcode = match.group(1) if match else str(entry.get('display_id') or '')
                if not CODE_RE.fullmatch(shortcode) or shortcode.isnumeric() or shortcode in seen:
                    continue
                seen.add(shortcode)
                found.append(f'https://www.instagram.com/reel/{shortcode}/')
                with discover_lock:
                    discover_jobs[discovery_id]['count'] = len(found)
        if not found:
            raise ValueError('Не удалось подтвердить ссылки на ролики в профиле. Попробуй вставить их списком.')
        with discover_lock:
            discover_jobs[discovery_id].update(status='complete', urls=found, count=len(found))
    except Exception as e:
        with discover_lock:
            discover_jobs[discovery_id].update(status='failed', error=str(e)[:300])


class Handler(BaseHTTPRequestHandler):
    server_version = 'ContentMachine/0.2'

    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/'):
            super().log_message(fmt, *args)

    def authorized(self, mutation=False):
        host = self.headers.get('Host', '').lower()
        if host not in (f'{HOST}:{PORT}', f'localhost:{PORT}'):
            return False
        origin = self.headers.get('Origin', '')
        if origin and origin not in (f'http://{HOST}:{PORT}', f'http://localhost:{PORT}'):
            return False
        if mutation and self.headers.get('X-ContentMachine') != '1':
            return False
        return True

    def respond(self, code, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def json_response(self, code, data):
        self.respond(code, json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')


    def _reference_mutation(self, method, route):
        """Stage 02 project-scoped registry; no download, fetch, or LLM calls."""
        m = re.fullmatch(r'/api/projects/([0-9a-f]{32})/sources(?:/([0-9a-f-]{36}|import-legacy))?', route)
        if not m:
            return False
        from intelligence.project_sources.repository import (
            RegistryProblem, create_source, update_source, archive_source, import_legacy)
        project_id, target = m.groups()
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 2 or size > 50000:
                raise ValueError('Некорректный размер запроса.')
            data = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Ожидается JSON-объект.')
            if method == 'POST' and target is None:
                result, status = create_source(project_id, data), 201
            elif method == 'POST' and target == 'import-legacy':
                if set(data) - {'dry_run'}:
                    raise RegistryProblem('Недопустимые поля импорта.')
                result, status = import_legacy(project_id, dry_run=data.get('dry_run', False)), 200
            elif method == 'PATCH' and target and target != 'import-legacy':
                result, status = update_source(project_id, target, data), 200
            elif method == 'DELETE' and target and target != 'import-legacy':
                if set(data) != {'expected_updated_at'}:
                    raise RegistryProblem('Укажи только expected_updated_at.')
                result, status = archive_source(project_id, target, data['expected_updated_at']), 200
            else:
                return self.json_response(405, {'error': 'Метод не поддерживается.'}) or True
            self.json_response(status, {'ok': True, **result})
        except RegistryProblem as exc:
            self.json_response(exc.http_status, {'error': str(exc)})
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self.json_response(400, {'error': 'Некорректный запрос источника.'})
        return True

    def do_PATCH(self):
        if not self.authorized(mutation=True):
            return self.json_response(403, {'error': 'Запрос отклонён.'})
        if not self._reference_mutation('PATCH', urlparse(self.path).path):
            return self.json_response(404, {'error': 'Не найдено.'})

    def do_DELETE(self):
        if not self.authorized(mutation=True):
            return self.json_response(403, {'error': 'Запрос отклонён.'})
        if not self._reference_mutation('DELETE', urlparse(self.path).path):
            return self.json_response(404, {'error': 'Не найдено.'})

    def do_GET(self):
        if not self.authorized():
            return self.json_response(403, {'error': 'Доступ разрешён только с локального сайта.'})
        url = urlparse(self.path)
        from intelligence.relevance.http_api import handle_get as sre_get
        if sre_get(self,url):
            return
        from intelligence.qualified_data.http_api import handle_get as qdp_get
        if qdp_get(self,url):
            return
        if url.path.startswith('/api/tools/dtk/'):
            from intelligence.connectors.http_api import handle_get as dtk_get
            if dtk_get(self, url):
                return
        from intelligence.taxonomy_builder.deep_v2_http import handle_get as rtb_v2_get
        if rtb_v2_get(self, url):
            return
        # Search Planner owns its complete HTTP surface; app.py remains a
        # two-line mount. GET cannot schedule provider or paid work.
        if '/search-planner' in url.path:
            from intelligence.search_planner.http_api import handle_get as sp_get
            if sp_get(self, url):
                return
        if url.path.startswith('/api/test-lab'):
            from test_lab import handle_get as test_lab_get
            if test_lab_get(self, url):
                return
        if url.path == '/api/state':
            return self.json_response(200, app_state())
        if url.path == '/api/projects':
            from projects import list_projects
            return self.json_response(200, {'ok': True, 'projects': list_projects()})
        from intelligence.project_sources.activity_http import handle_get as source_activity_get
        if source_activity_get(self, url):
            return
        from intelligence.project_sources.context_http import handle_get as project_context_get
        if project_context_get(self, url):
            return
        from intelligence.project_sources.evidence_http import handle_get as evidence_get
        if evidence_get(self, url):
            return
        from intelligence.project_sources.timeline_http import handle_get as fusion_get
        if fusion_get(self, url):
            return
        from intelligence.project_sources.vision_http import handle_get as vision_get
        if vision_get(self, url):
            return
        from intelligence.project_sources.transcript_http import handle_get as transcript_get
        if transcript_get(self, url):
            return
        from intelligence.project_sources.video_http import handle_get as project_video_get
        if project_video_get(self, url):
            return
        from intelligence.project_sources.web_http import handle_get as website_extract_get
        if website_extract_get(self, url):
            return
        from intelligence.project_sources.document_http import handle_get as document_extract_get
        if document_extract_get(self, url):
            return
        from intelligence.project_sources.file_http import handle_get as research_files_get
        if research_files_get(self, url):
            return
        ref_list = re.fullmatch(r'/api/projects/([0-9a-f]{32})/sources', url.path)
        if ref_list:
            from intelligence.project_sources.repository import list_sources, RegistryProblem
            archived = parse_qs(url.query).get('include_archived', ['0'])
            if len(archived) != 1 or archived[0] not in ('0', '1'):
                return self.json_response(400, {'error': 'Некорректный include_archived.'})
            try:
                return self.json_response(200, {'ok':True, **list_sources(
                    ref_list.group(1), include_archived=archived[0] == '1')})
            except RegistryProblem as exc:
                return self.json_response(exc.http_status, {'error':str(exc)})
        # In-product, read-only run history for repeatable manual model tests.
        # No provider/API calls are made by browsing completed or failed runs.
        tb_runs_list = re.fullmatch(
            r'/api/projects/([0-9a-f]{32})/taxonomy-builder/runs',url.path)
        if tb_runs_list:
            from intelligence.taxonomy_builder.run_repository import list_runs
            from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
            try:
                return self.json_response(200, {'ok':True, **list_runs(tb_runs_list.group(1))})
            except PipelineError as exc:
                return self.json_response(exc.http_status, {'error':str(exc)})
        tb_deep_read = re.fullmatch(
            r'/api/projects/([0-9a-f]{32})/taxonomy-builder/runs/([0-9a-f-]{36})/deep',url.path)
        if tb_deep_read:
            from intelligence.taxonomy_builder.deep_repository import get_deep
            from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
            try:
                return self.json_response(200, {'ok':True, **get_deep(
                    tb_deep_read.group(1),tb_deep_read.group(2))})
            except PipelineError as exc:
                return self.json_response(exc.http_status, {'error':str(exc)})
        tb_run = re.fullmatch(r'/api/projects/([0-9a-f]{32})/taxonomy-builder/runs/([0-9a-f-]{36})',url.path)
        if tb_run:
            from intelligence.taxonomy_builder.run_repository import get_run
            from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
            try:
                return self.json_response(200, {'ok':True, **get_run(tb_run.group(1),tb_run.group(2))})
            except PipelineError as exc:
                return self.json_response(exc.http_status, {'error':str(exc)})
        stage13_preflight = re.fullmatch(
            r'/api/projects/([0-9a-f]{32})/taxonomy-builder/context-preview', url.path)
        if stage13_preflight:
            from intelligence.taxonomy_builder.context_binding import ui_preflight
            from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
            args = parse_qs(url.query, keep_blank_values=True)
            try:
                if set(args) != {'context_id','token_budget'} or any(len(x)!=1 for x in args.values()):
                    raise PipelineError('Require context_id and token_budget exactly once',400)
                if not re.fullmatch(r'[0-9]+',args['token_budget'][0]):
                    raise PipelineError('Invalid context token budget',400)
                data=ui_preflight(stage13_preflight.group(1),args['context_id'][0],
                                  int(args['token_budget'][0]))
                return self.json_response(200, {'ok':True,**data})
            except PipelineError as exc:
                return self.json_response(exc.http_status, {'error':str(exc)})
        if url.path.startswith('/api/projects/') and url.path.endswith('/taxonomy-builder/cost'):
            from intelligence.taxonomy_builder.cost_repository import get_cost_state
            from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
            ident = url.path.removeprefix('/api/projects/').removesuffix('/taxonomy-builder/cost')
            try:
                return self.json_response(200, {'ok': True, **get_cost_state(ident)})
            except PipelineError as exc:
                return self.json_response(exc.http_status, {'error': str(exc)})
        if url.path.startswith('/api/projects/') and url.path.endswith('/taxonomy-builder/models'):
            from intelligence.taxonomy_builder.pipeline_repository import get_project_model_catalog
            from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
            ident = url.path.removeprefix('/api/projects/').removesuffix('/taxonomy-builder/models')
            try:
                return self.json_response(200, {'ok':True, **get_project_model_catalog(ident)})
            except PipelineError as exc:
                return self.json_response(exc.http_status, {'error':str(exc)})
        if url.path.startswith('/api/projects/') and url.path.endswith('/taxonomy-builder/pipeline'):
            from intelligence.taxonomy_builder.pipeline_repository import get_pipeline_state
            from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
            ident = url.path.removeprefix('/api/projects/').removesuffix('/taxonomy-builder/pipeline')
            try:
                return self.json_response(200, {'ok':True, **get_pipeline_state(ident)})
            except PipelineError as exc:
                return self.json_response(exc.http_status, {'error':str(exc)})
        if url.path.startswith('/api/projects/') and url.path.endswith('/taxonomy-builder/intake'):
            from intelligence.taxonomy_builder.intake import get_intake_view, IntakeProblem
            project_id = url.path.removeprefix('/api/projects/').removesuffix('/taxonomy-builder/intake')
            try:
                return self.json_response(200, {'ok': True, **get_intake_view(project_id)})
            except IntakeProblem as exc:
                return self.json_response(exc.http_status, {'error': str(exc)})
        if url.path.startswith('/api/projects/') and url.path.endswith('/taxonomy-input'):
            from projects import taxonomy_input
            project_id = url.path.removeprefix('/api/projects/').removesuffix('/taxonomy-input')
            result = taxonomy_input(project_id)
            return self.json_response(200, {'ok': True, **result}) if result else self.json_response(404, {'error': 'Проект не найден.'})
        if url.path.startswith('/api/projects/') and '/files/' in url.path:
            from projects import project_file
            from urllib.parse import quote
            project_id, index = url.path.removeprefix('/api/projects/').split('/files/', 1)
            attachment = project_file(project_id, index)
            if not attachment:
                return self.json_response(404, {'error': 'Документ не найден.'})
            path, original = attachment
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Content-Disposition',
                             'attachment; filename="research-document"; filename*=UTF-8\'\'' + quote(original, safe=''))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            with path.open('rb') as stream:
                while part := stream.read(1024 * 1024):
                    try: self.wfile.write(part)
                    except (BrokenPipeError, ConnectionAbortedError): break
            return
        if url.path.startswith('/api/projects/'):
            from projects import get_project, public_project, sync_profile_to_db
            project = get_project(url.path.removeprefix('/api/projects/'))
            if not project:
                return self.json_response(404, {'error': 'Проект не найден.'})
            result = public_project(project)
            result['profile_db_sync'] = sync_profile_to_db(project)
            return self.json_response(200, {'ok': True, 'project': result})
        if url.path.startswith('/api/intelligence/') and url.path != '/api/intelligence/health':
            from intelligence.discovery.http_api import handle_get
            if handle_get(self,url):
                return
        if url.path == '/api/intelligence/health':
            from intelligence.health import status as intelligence_status
            return self.json_response(200, intelligence_status())
        if url.path == '/api/file':
            params = parse_qs(url.query)
            account = params.get('account', [''])[0]
            code = params.get('code', [''])[0]
            ext = params.get('ext', [''])[0]
            if not ACCOUNT_RE.fullmatch(account) or not CODE_RE.fullmatch(code) or ext not in EXTENSIONS:
                return self.json_response(400, {'error': 'Некорректный файл.'})
            target = ARCHIVE / account / (code + ext)
            if not target.is_file():
                return self.json_response(404, {'error': 'Файл не найден.'})
            self.send_response(200)
            self.send_header('Content-Type', EXTENSIONS[ext])
            self.send_header('Content-Length', str(target.stat().st_size))
            self.send_header('Content-Disposition', f'attachment; filename="{code}{ext}"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            with target.open('rb') as stream:
                while part := stream.read(1024 * 1024):
                    try: self.wfile.write(part)
                    except (BrokenPipeError, ConnectionAbortedError): break
            return
        files = {'/': 'index.html', '/index.html': 'index.html', '/style.css': 'style.css', '/taxonomy-workspace.css': 'taxonomy-workspace.css', '/app.js': 'app.js', '/discovery.js': 'discovery.js', '/dtk-tool.js': 'dtk-tool.js', '/collector.js': 'collector.js', '/projects.js': 'projects.js', '/project-sources.js': 'project-sources.js', '/research-files.js': 'research-files.js', '/document-extraction.js': 'document-extraction.js', '/web-sources.js': 'web-sources.js', '/video-sources.js': 'video-sources.js', '/transcription-sources.js': 'transcription-sources.js', '/vision-sources.js': 'vision-sources.js', '/fusion-sources.js': 'fusion-sources.js', '/evidence-sources.js': 'evidence-sources.js', '/project-context.js': 'project-context.js', '/project-activity.js': 'project-activity.js', '/context-quote.js': 'context-quote.js', '/taxonomy-studio.js': 'taxonomy-studio.js', '/taxonomy-cost.js': 'taxonomy-cost.js', '/taxonomy-run.js': 'taxonomy-run.js', '/taxonomy-deep.js': 'taxonomy-deep.js', '/taxonomy-deep-v2.js': 'taxonomy-deep-v2.js', '/search-planner.css': 'search-planner.css', '/search-planner-core.js': 'search-planner-core.js', '/search-planner.js': 'search-planner.js', '/qualified-data.js': 'qualified-data.js', '/sre-studio.html': 'sre-studio.html', '/sre-studio.js': 'sre-studio.js', '/sre-studio.css': 'sre-studio.css'}
        files.update({
            '/workspace.css': 'workspace.css',
            '/workspace-core.js': 'workspace-core.js',
            '/workspace-analysis.js': 'workspace-analysis.js',
            '/workspace.js': 'workspace.js',
            '/workspace-sre.js': 'workspace-sre.js',
            '/test-lab.js': 'test-lab.js',
            '/test-lab.css': 'test-lab.css',
        })
        name = files.get(url.path)
        if not name:
            return self.json_response(404, {'error': 'Не найдено.'})
        file = SITE / name
        if not file.is_file():
            return self.json_response(404, {'error': 'Файл интерфейса отсутствует.'})
        mime = {'html': 'text/html', 'css': 'text/css', 'js': 'text/javascript'}[file.suffix.lstrip('.')]
        return self.respond(200, file.read_bytes(), mime + '; charset=utf-8')

    def do_POST(self):
        if not self.authorized(mutation=True):
            return self.json_response(403, {'error': 'Запрос отклонён.'})
        route = urlparse(self.path).path
        if route.startswith('/api/test-lab'):
            from test_lab import handle_post as test_lab_post
            if test_lab_post(self, route):
                return
        from intelligence.relevance.http_api import handle_post as sre_post
        if sre_post(self,route):
            return
        from intelligence.qualified_data.http_api import handle_post as qdp_post
        if qdp_post(self,route):
            return
        if route.startswith('/api/tools/dtk/'):
            from intelligence.connectors.http_api import handle_post as dtk_post
            if dtk_post(self, route):
                return
        from intelligence.taxonomy_builder.deep_v2_http import handle_post as rtb_v2_post
        if rtb_v2_post(self, route):
            return
        if '/search-planner' in route:
            from intelligence.search_planner.http_api import handle_post as sp_post
            if sp_post(self, route):
                return
        from intelligence.project_sources.context_http import handle_post as project_context_post
        if project_context_post(self, route):
            return
        from intelligence.project_sources.evidence_http import handle_post as evidence_post
        if evidence_post(self, route):
            return
        from intelligence.project_sources.timeline_http import handle_post as fusion_post
        if fusion_post(self, route):
            return
        from intelligence.project_sources.vision_http import handle_post as vision_post
        if vision_post(self, route):
            return
        from intelligence.project_sources.transcript_http import handle_post as transcript_post
        if transcript_post(self, route):
            return
        from intelligence.project_sources.video_http import handle_post as project_video_post
        if project_video_post(self, route):
            return
        from intelligence.project_sources.web_http import handle_post as website_extract_post
        if website_extract_post(self, route):
            return
        from intelligence.project_sources.document_http import handle_post as document_extract_post
        if document_extract_post(self, route):
            return
        from intelligence.project_sources.file_http import handle_post as research_files_post
        if research_files_post(self, route):
            return
        if self._reference_mutation('POST', route):
            return
        if route.startswith('/api/intelligence/'):
            from intelligence.discovery.http_api import handle_post
            if handle_post(self,route):
                return
        project_update = route.startswith('/api/projects/') and route.endswith('/update')
        intake_confirm = (route.startswith('/api/projects/') and
                          route.endswith('/taxonomy-builder/intake/confirm'))
        pipeline_draft = (route.startswith('/api/projects/') and
                          route.endswith('/taxonomy-builder/pipeline/draft'))
        pipeline_approve = (route.startswith('/api/projects/') and
                            route.endswith('/taxonomy-builder/pipeline/approve'))
        catalog_refresh = route == '/api/taxonomy-builder/models/refresh'
        cost_quote = route.startswith('/api/projects/') and route.endswith('/taxonomy-builder/cost/quote')
        cost_consent = route.startswith('/api/projects/') and route.endswith('/taxonomy-builder/cost/consent')
        cost_revoke = route.startswith('/api/projects/') and route.endswith('/taxonomy-builder/cost/revoke')
        deep_action = re.fullmatch(
            r'/api/projects/([0-9a-f]{32})/taxonomy-builder/runs/([0-9a-f-]{36})/deep/(start|advance|review1|review2)',route)
        builder_start = re.fullmatch(r'/api/projects/([0-9a-f]{32})/taxonomy-builder/runs',route)
        builder_action = re.fullmatch(
           r'/api/projects/([0-9a-f]{32})/taxonomy-builder/runs/([0-9a-f-]{36})/(advance|facts/review|topics/prepare|topics/publish|cancel)',route)
        if not project_update and not intake_confirm and not pipeline_draft and not pipeline_approve and not catalog_refresh and not cost_quote and not cost_consent and not cost_revoke and not deep_action and not builder_start and not builder_action and route not in {'/api/download', '/api/discover', '/api/find', '/api/open-folder', '/api/key', '/api/open-key-file', '/api/cancel', '/api/projects'}:
            return self.json_response(404, {'error': 'Не найдено.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 2 <= size <= (35000000 if route == '/api/projects' else 150000 if (project_update or deep_action) else 30000):
                raise ValueError('Некорректный размер запроса.')
            data = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Неверный формат данных.')
            if route == '/api/projects':
                from projects import create_project
                project = create_project(data)
                return self.json_response(201, {'ok': True, 'project': project})
            if deep_action:
                from intelligence.taxonomy_builder.deep_repository import (
                    start_deep,advance_deep,first_review,final_review)
                from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
                pid,rid,action=deep_action.groups()
                try:
                    if action=='start':response=start_deep(pid,rid)
                    elif action=='advance':response=advance_deep(pid,rid)
                    elif action=='review1':response=first_review(pid,rid,data.get('decisions'))
                    else:response=final_review(pid,rid,data.get('decisions'))
                    return self.json_response(200, {'ok':True, **response})
                except PipelineError as exc:
                    return self.json_response(exc.http_status,{'error':str(exc)})
            if builder_start or builder_action:
                from intelligence.taxonomy_builder.run_repository import (
                    start_run,advance_run,submit_fact_review,submit_generation_review,publish_reviewed_topics,cancel_run)
                from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
                try:
                    if builder_start:
                        result=start_run(builder_start.group(1),data.get('quote_id'),
                            data.get('consent_id'),data.get('idempotency_key'))
                        return self.json_response(201, {'ok':True,**result})
                    project_id,run_id,action=builder_action.groups()
                    if action=='advance':
                        result=advance_run(project_id,run_id)
                    elif action=='facts/review':
                        result=submit_fact_review(project_id,run_id,data.get('decisions'))
                    elif action=='topics/prepare':
                        result=submit_generation_review(project_id,run_id,data.get('decisions'))
                    elif action=='topics/publish':
                        result=publish_reviewed_topics(project_id,run_id,data.get('decisions'))
                    else:
                        result=cancel_run(project_id,run_id)
                except PipelineError as exc:
                    return self.json_response(exc.http_status,{'error':str(exc)})
                return self.json_response(200, {'ok':True, **result})
            if cost_quote or cost_consent or cost_revoke:
                from intelligence.taxonomy_builder.cost_repository import (
                    create_quote, authorize_budget, revoke_consent)
                from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
                suffix = ('/taxonomy-builder/cost/quote' if cost_quote else
                          '/taxonomy-builder/cost/consent' if cost_consent else
                          '/taxonomy-builder/cost/revoke')
                project_id = route.removeprefix('/api/projects/').removesuffix(suffix)
                try:
                    if cost_quote:
                        result=create_quote(project_id,data.get('pipeline_id'),data.get('force_new',False),
                              context_id=data.get('context_id'),token_budget=data.get('token_budget',1200))
                    elif cost_consent:
                        result=authorize_budget(
                          project_id,data.get('quote_id'),data.get('authorized_usd'),
                          data.get('acknowledge_documents_excluded',False))
                    else:
                        result=revoke_consent(project_id,data.get('consent_id'))
                except PipelineError as exc:
                    return self.json_response(exc.http_status, {'error':str(exc)})
                return self.json_response(200, {'ok':True, **result})
            if catalog_refresh:
                from intelligence.taxonomy_builder.catalog import refresh_model_catalog
                from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
                from intelligence.taxonomy_builder.pipeline_repository import _connect
                try:
                    with _connect() as conn:
                        result = refresh_model_catalog(conn)
                except PipelineError as exc:
                    return self.json_response(exc.http_status, {'error':str(exc)})
                except Exception:
                    return self.json_response(503, {'error':'Не удалось обновить каталог моделей.'})
                return self.json_response(200, {'ok':True, **result})
            if pipeline_draft or pipeline_approve:
                from intelligence.taxonomy_builder.pipeline_repository import save_draft,approve_draft
                from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
                suffix = '/taxonomy-builder/pipeline/draft' if pipeline_draft else '/taxonomy-builder/pipeline/approve'
                ident = route.removeprefix('/api/projects/').removesuffix(suffix)
                try:
                    if pipeline_draft:
                        result = save_draft(ident,data.get('config'),data.get('expected_draft_revision'))
                    else:
                        result = approve_draft(ident,data.get('expected_draft_revision'))
                except PipelineError as exc:
                    return self.json_response(exc.http_status, {'error':str(exc)})
                return self.json_response(200, {'ok':True, **result})
            if intake_confirm:
                from intelligence.taxonomy_builder.intake import confirm_intake, IntakeProblem
                project_id = route.removeprefix('/api/projects/').removesuffix('/taxonomy-builder/intake/confirm')
                try:
                    result = confirm_intake(project_id, data.get('expected_revision'),
                                            data.get('expected_hash'))
                except IntakeProblem as exc:
                    return self.json_response(exc.http_status, {'error': str(exc)})
                return self.json_response(200, {'ok': True, **result})
            if project_update:
                from projects import update_project, ProjectRevisionConflict
                project_id = route.removeprefix('/api/projects/').removesuffix('/update')
                try:
                    project = update_project(project_id, data)
                except FileNotFoundError:
                    return self.json_response(404, {'error': 'Проект не найден.'})
                except ProjectRevisionConflict as exc:
                    return self.json_response(409, {'error': str(exc)})
                return self.json_response(200, {'ok': True, 'project': project})
            if route == '/api/find':
                target = str(data.get('target') or '').strip()
                platform = str(data.get('platform') or 'instagram').strip().lower()
                if platform not in ('instagram', 'tiktok', 'youtube'):
                    raise ValueError('Неизвестная платформа.')
                if not target or len(target) > 500:
                    raise ValueError('Вставь ссылку на профиль или видео.')
                if platform == 'youtube':
                    kind, canonical, video_id = youtube.parse_link(target)
                    if kind == 'video':
                        return self.json_response(200, {'ok': True, 'kind': 'video', 'count': 1, 'urls': [canonical]})
                    browser = str(data.get('cookie_browser') or 'none')
                    youtube.cookie_opts(browser)
                    try:
                        limit = int(data.get('max_count', 20))
                    except (ValueError, TypeError):
                        raise ValueError('Некорректный лимит видео.')
                    if limit not in (0, 5, 20, 50, 100):
                        raise ValueError('Лимит роликов: все, 5, 20, 50 или 100.')
                    discovery_id = uuid.uuid4().hex
                    with discover_lock:
                        discover_jobs[discovery_id] = {'id': discovery_id, 'platform': 'youtube',
                                                      'status': 'processing', 'count': 0, 'urls': []}
                    discover_pool.submit(youtube.discover_channel, discovery_id, canonical,
                                         update_discovery, browser, limit)
                    return self.json_response(202, {'ok': True, 'kind': 'profile', 'id': discovery_id})
                if platform == 'tiktok':
                    kind, username, video_id, link = tiktok.parse_link(target)
                    if kind == 'video':
                        return self.json_response(200, {'ok': True, 'kind': 'video', 'count': 1, 'urls': [link]})
                    discovery_id = uuid.uuid4().hex
                    with discover_lock:
                        discover_jobs[discovery_id] = {'id': discovery_id, 'platform': 'tiktok', 'account': username or '',
                                                       'status': 'processing', 'count': 0, 'urls': []}
                    if kind == 'short':
                        discover_pool.submit(tiktok.resolve_short, discovery_id, link, update_discovery)
                    else:
                        discover_pool.submit(tiktok.discover_profile, discovery_id, username, update_discovery)
                    return self.json_response(202, {'ok': True, 'kind': 'profile', 'id': discovery_id})
                # Publication links are validated without a network request.
                try:
                    link, code = canonical_reel(target)
                except ValueError:
                    link = None
                if link:
                    return self.json_response(200, {'ok': True, 'kind': 'video', 'count': 1, 'urls': [link]})
                # A profile requires discovery. The UI polls /api/state while it runs.
                match = PROFILE_RE.fullmatch(target)
                if not match:
                    raise ValueError('Нужна ссылка на Instagram-профиль или публикацию.')
                username = match.group(1) or match.group(2)
                if username.lower() in ('reel', 'reels', 'p', 'tv', 'stories', 'explore'):
                    raise ValueError('Проверь ссылку на Instagram-профиль.')
                discovery_id = uuid.uuid4().hex
                with discover_lock:
                    discover_jobs[discovery_id] = {'id': discovery_id, 'account': username, 'status': 'processing', 'count': 0, 'urls': []}
                discover_pool.submit(discover_profile, discovery_id, username)
                return self.json_response(202, {'ok': True, 'kind': 'profile', 'id': discovery_id})
            if route == '/api/key':
                # This is the only setting for OpenRouter across every worker.
                # Do not echo the key, record it in logs, or store it in browser.
                raw = data.get('key')
                if not isinstance(raw, str):
                    raise ValueError('Вставь API-ключ OpenRouter.')
                key = raw.strip()
                if not (key.startswith('sk-or-') and 10 <= len(key) <= 512
                        and all(ch.isascii() and (ch.isalnum() or ch in '-_') for ch in key)):
                    raise ValueError('Неверный формат API-ключа OpenRouter.')
                target = WORKER / 'openrouter_key.txt'
                target.parent.mkdir(parents=True, exist_ok=True)
                # Atomic replace: concurrent worker reads always see a complete key.
                temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    temp.write_text(key, encoding='utf-8')
                    if os.name != 'nt':
                        temp.chmod(0o600)
                    temp.replace(target)
                finally:
                    temp.unlink(missing_ok=True)
                return self.json_response(200, {'ok': True, 'configured': True})
            if route == '/api/open-key-file':
                # Show the user's existing local file in the OS text editor.
                # Never send any key bytes back to the web UI.
                key_file = WORKER / 'openrouter_key.txt'
                if not key_file.is_file():
                    raise ValueError('На ноутбуке ещё нет сохранённого OpenRouter-ключа.')
                if sys.platform == 'win32':
                    subprocess.Popen(['notepad.exe', str(key_file)])
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', '-t', str(key_file)])
                else:
                    subprocess.Popen(['xdg-open', str(key_file)])
                return self.json_response(200, {'ok': True})
            if route == '/api/open-folder':
                platform = str(data.get('platform') or 'instagram')
                folders = {'instagram': ARCHIVE, 'tiktok': tiktok.ARCHIVE, 'youtube': youtube.ARCHIVE}
                if platform not in folders:
                    raise ValueError('Неизвестная платформа.')
                folder = folders[platform]
                folder.mkdir(parents=True, exist_ok=True)
                if sys.platform == 'win32':
                    os.startfile(str(folder))
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', str(folder)])
                else:
                    subprocess.Popen(['xdg-open', str(folder)])
                return self.json_response(200, {'ok': True})
            if route == '/api/cancel':
                if str(data.get('platform') or '') != 'youtube':
                    raise ValueError('Остановка поддерживается для YouTube Downloader.')
                ids = data.get('ids', [])
                discovery_id = str(data.get('discovery_id') or '')
                if not isinstance(ids, list) or len(ids) > 500 or not (ids or discovery_id):
                    raise ValueError('Некорректный список заданий.')
                count = 0
                with worker.job_lock:
                    for ident in set(x for x in ids if isinstance(x, str)):
                        job = worker.jobs.get(ident)
                        if job and job.get('platform') == 'youtube' and job.get('status') in ('queued', 'processing'):
                            job['cancel_requested'] = True
                            count += 1
                if discovery_id:
                    with discover_lock:
                        item = discover_jobs.get(discovery_id)
                        if item and item.get('platform') == 'youtube' and item.get('status') == 'processing':
                            item['cancel_requested'] = True
                            item['status'] = 'cancelled'
                            count += 1
                return self.json_response(200, {'ok': True, 'requested': count})
            if route == '/api/discover':
                value = str(data.get('profile') or '').strip()
                match = PROFILE_RE.fullmatch(value)
                if not match:
                    raise ValueError('Нужен username или ссылка на профиль Instagram.')
                username = match.group(1) or match.group(2)
                discovery_id = uuid.uuid4().hex
                with discover_lock:
                    discover_jobs[discovery_id] = {'id': discovery_id, 'account': username, 'status': 'processing', 'count': 0, 'urls': []}
                discover_pool.submit(discover_profile, discovery_id, username)
                return self.json_response(202, {'ok': True, 'id': discovery_id})
            urls = data.get('urls')
            if not isinstance(urls, list) or not (1 <= len(urls) <= 500):
                raise ValueError('Нужна хотя бы одна ссылка (не больше 500 за запуск).')
            platform = str(data.get('platform') or 'instagram').strip().lower()
            if platform not in ('instagram', 'tiktok', 'youtube'):
                raise ValueError('Неизвестная платформа.')
            account = str(data.get('account') or 'instagram').strip().lstrip('@') or 'instagram'
            if platform == 'instagram' and not ACCOUNT_RE.fullmatch(account):
                raise ValueError('Некорректная папка аккаунта.')
            engine = str(data.get('engine') or ('openrouter' if get_key(WORKER) else 'local'))
            language = 'auto'
            if platform == 'youtube':
                mode = str(data.get('transcription_mode') or
                           ('mai' if get_key(WORKER) else 'whisper'))
                if mode not in ('mai', 'whisper', 'off'):
                    raise ValueError('Распознавание: MAI, Whisper или выключено.')
                language = str(data.get('language') or 'auto')
                if language not in ('auto', 'ru', 'en'):
                    raise ValueError('Язык речи: авто, русский или английский.')
                engine = {'mai': 'openrouter', 'whisper': 'local', 'off': 'off'}[mode]
            if engine not in ('local', 'openrouter', 'off'):
                raise ValueError('Некорректный режим распознавания.')
            if engine == 'off' and platform != 'youtube':
                raise ValueError('Выключение распознавания доступно только YouTube.')
            if engine == 'openrouter' and not get_key(WORKER):
                raise ValueError('Для MAI нужен ключ OpenRouter; выбери локальный Whisper.')
            if platform == 'youtube':
                browser = str(data.get('cookie_browser') or 'none')
                youtube.cookie_opts(browser)
                unique = {}
                for raw in urls:
                    kind, link, video_id = youtube.parse_link(str(raw))
                    if kind != 'video':
                        raise ValueError('Для скачивания нужна ссылка на YouTube Short.')
                    unique[video_id] = link
                results = []
                for video_id, link in unique.items():
                    video = youtube.ARCHIVE / 'single' / video_id / 'video.mp4'
                    with worker.job_lock:
                        running = next((ident for ident, job in worker.jobs.items()
                                        if job.get('video_path') == str(video) and job.get('status') in ('queued', 'processing')), None)
                        if running:
                            ident = running
                        else:
                            ident = uuid.uuid4().hex
                            worker.jobs[ident] = {'job_id': ident, 'platform': 'youtube',
                                                  'video_path': str(video), 'status': 'queued',
                                                  'phase': 'queued', 'code': video_id, 'progress': None,
                                                  'cancel_requested': False}
                            worker.pool.submit(youtube.download_job, ident, {
                                'video_id': video_id, 'source_url': link, 'engine': engine,
                                'cookie_browser': browser, 'language': language
                            })
                    results.append({'code': video_id, 'id': ident})
                return self.json_response(202, {'ok': True, 'jobs': results})
            if platform == 'tiktok':
                unique = {}
                for raw in urls:
                    kind, username, video_id, link = tiktok.parse_link(str(raw))
                    if kind != 'video':
                        raise ValueError('Для скачивания нужна ссылка на TikTok-видео.')
                    unique[video_id] = (username, link)
                results = []
                for video_id, (username, link) in unique.items():
                    video = tiktok.ARCHIVE / username / f'{video_id}.mp4'
                    with worker.job_lock:
                        running = next((ident for ident, job in worker.jobs.items()
                                        if job.get('video_path') == str(video) and job.get('status') in ('queued', 'processing')), None)
                        if running:
                            ident = running
                        else:
                            ident = uuid.uuid4().hex
                            worker.jobs[ident] = {'job_id': ident, 'platform': 'tiktok', 'video_path': str(video),
                                                  'status': 'queued', 'phase': 'queued', 'code': video_id, 'progress': None}
                            worker.pool.submit(tiktok.download_job, ident, {
                                'account': username, 'shortcode': video_id, 'source_url': link, 'engine': engine
                            })
                    results.append({'code': video_id, 'id': ident})
                return self.json_response(202, {'ok': True, 'jobs': results})
            unique = {}
            for url in urls:
                link, code = canonical_reel(str(url))
                unique[code] = link
            results = []
            for code, link in unique.items():
                video = ARCHIVE / account / f'{code}.mp4'
                with worker.job_lock:
                    running = next((ident for ident, job in worker.jobs.items()
                                    if job.get('video_path') == str(video) and job.get('status') in ('queued', 'processing')), None)
                    if running:
                        ident = running
                    else:
                        ident = uuid.uuid4().hex
                        worker.jobs[ident] = {'job_id': ident, 'video_path': str(video), 'status': 'queued', 'phase': 'queued', 'code': code, 'progress': None}
                        worker.pool.submit(worker.exact_download_job, ident, {
                            'account': account, 'shortcode': code, 'source_url': link, 'engine': engine
                        })
                results.append({'code': code, 'id': ident})
            return self.json_response(202, {'ok': True, 'jobs': results})
        except (ValueError, TypeError, KeyError, UnicodeError, OSError) as e:
            return self.json_response(400, {'error': str(e)[:300]})


def main():
    print('=== CONTENTMACHINE / Instagram + TikTok + YouTube Shorts ===')
    print(f'Web app: http://{HOST}:{PORT}')
    print(f'Archive: {ARCHIVE}')
    print('Stop the previous Content Zavod worker before running this server.')
    from intelligence.discovery.service import start_worker as start_discovery_worker
    start_discovery_worker()
    from intelligence.qualified_data.worker import start_worker as start_qdp_worker
    start_qdp_worker()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    if os.environ.get('CM_NO_BROWSER') != '1':
        threading.Timer(.8, lambda: webbrowser.open(f'http://{HOST}:{PORT}')).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nContentMachine stopped.')
    finally:
        server.server_close()
        worker.pool.shutdown(wait=False, cancel_futures=True)
        discover_pool.shutdown(wait=False, cancel_futures=True)


if __name__ == '__main__':
    main()
