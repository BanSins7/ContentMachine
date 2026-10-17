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
from openrouter_transcribe import BUDGET_USD, MODEL, get_key, load_usage  # noqa: E402

HOST, PORT = '127.0.0.1', 8765
ARCHIVE = Path.home() / 'Downloads' / 'InstagramArchive'
ACCOUNT_RE = re.compile(r'[A-Za-z0-9._]{1,30}\Z')
CODE_RE = re.compile(r'[A-Za-z0-9_-]{5,80}\Z')
POST_RE = re.compile(r'/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,80})/?\Z')
EXTENSIONS = {'.mp4': 'video/mp4', '.srt': 'application/x-subrip', '.txt': 'text/plain; charset=utf-8', '.json': 'application/json; charset=utf-8'}
PROFILE_RE = re.compile(r'(?:https?://(?:www\.)?instagram\.com/)?@?([A-Za-z0-9._]{1,30})/?\Z')
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
    return rows[:800]


def discovery_status():
    with discover_lock:
        return [dict(v) for v in discover_jobs.values()][-10:]


def app_state():
    with worker.job_lock:
        jobs = [{
            'id': job_id, 'code': Path(j.get('video_path', '')).stem,
            'status': j.get('status', 'queued'), 'error': j.get('error', ''),
            'result': j.get('result', ''),
        } for job_id, j in list(worker.jobs.items())[-200:]]
    has_key = bool(get_key(WORKER))
    usage = load_usage(WORKER)
    return {'ok': True, 'jobs': jobs, 'library': library_items(), 'discovery': discovery_status(),
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
        if not found:
            raise ValueError('Не удалось подтвердить ссылки на ролики в профиле. Попробуй вставить их списком.')
        with discover_lock:
            discover_jobs[discovery_id].update(status='complete', urls=found, count=len(found))
    except Exception as e:
        with discover_lock:
            discover_jobs[discovery_id].update(status='failed', error=str(e)[:300])


class Handler(BaseHTTPRequestHandler):
    server_version = 'ContentMachine/0.1'

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

    def do_GET(self):
        if not self.authorized():
            return self.json_response(403, {'error': 'Доступ разрешён только с локального сайта.'})
        url = urlparse(self.path)
        if url.path == '/api/state':
            return self.json_response(200, app_state())
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
        files = {'/': 'index.html', '/index.html': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js'}
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
        if route not in {'/api/download', '/api/discover', '/api/open-folder', '/api/key'}:
            return self.json_response(404, {'error': 'Не найдено.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 2 <= size <= 30000:
                raise ValueError('Некорректный размер запроса.')
            data = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Неверный формат данных.')
            if route == '/api/key':
                key = str(data.get('key') or '').strip()
                if key and (not key.startswith('sk-or-') or len(key) > 512 or '\n' in key):
                    raise ValueError('Неверный формат ключа OpenRouter.')
                target = WORKER / 'openrouter_key.txt'
                if key:
                    target.write_text(key, encoding='utf-8')
                    if os.name != 'nt': os.chmod(target, 0o600)
                else:
                    target.unlink(missing_ok=True)
                return self.json_response(200, {'ok': True, 'configured': bool(key)})
            if route == '/api/open-folder':
                ARCHIVE.mkdir(parents=True, exist_ok=True)
                if sys.platform == 'win32':
                    os.startfile(str(ARCHIVE))
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', str(ARCHIVE)])
                else:
                    subprocess.Popen(['xdg-open', str(ARCHIVE)])
                return self.json_response(200, {'ok': True})
            if route == '/api/discover':
                value = str(data.get('profile') or '').strip()
                match = PROFILE_RE.fullmatch(value)
                if not match:
                    raise ValueError('Нужен username или ссылка на профиль Instagram.')
                username = match.group(1)
                discovery_id = uuid.uuid4().hex
                with discover_lock:
                    discover_jobs[discovery_id] = {'id': discovery_id, 'account': username, 'status': 'processing', 'count': 0, 'urls': []}
                discover_pool.submit(discover_profile, discovery_id, username)
                return self.json_response(202, {'ok': True, 'id': discovery_id})
            urls = data.get('urls')
            if not isinstance(urls, list) or not (1 <= len(urls) <= 75):
                raise ValueError('Вставь от 1 до 75 ссылок на Reels.')
            account = str(data.get('account') or 'instagram').strip().lstrip('@') or 'instagram'
            if not ACCOUNT_RE.fullmatch(account):
                raise ValueError('Некорректная папка аккаунта.')
            engine = str(data.get('engine') or 'openrouter')
            if engine not in ('local', 'openrouter'):
                raise ValueError('Некорректный режим распознавания.')
            if engine == 'openrouter' and not get_key(WORKER):
                raise ValueError('Добавь OpenRouter API-ключ в настройках или выбери Whisper.')
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
                        worker.jobs[ident] = {'job_id': ident, 'video_path': str(video), 'status': 'queued'}
                        worker.pool.submit(worker.exact_download_job, ident, {
                            'account': account, 'shortcode': code, 'source_url': link, 'engine': engine
                        })
                results.append({'code': code, 'id': ident})
            return self.json_response(202, {'ok': True, 'jobs': results})
        except (ValueError, TypeError, KeyError, UnicodeError, OSError) as e:
            return self.json_response(400, {'error': str(e)[:300]})


def main():
    print('=== CONTENTMACHINE / Instagram Downloader ===')
    print(f'Web app: http://{HOST}:{PORT}')
    print(f'Archive: {ARCHIVE}')
    print('Stop the previous Content Zavod worker before running this server.')
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
