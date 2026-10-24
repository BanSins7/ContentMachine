"""Stage 06 adapters: reuse existing TikTok/YouTube/Instagram exact downloaders.

No independent yt-dlp, cookie handling, or transcription implementation.
A cache hit is trusted ONLY when original worker metadata, verified video ID,
actual MP4 bytes and its SHA-256 all agree. The provenance remains project-local.
"""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_VIDEO = 2_000_000_000
SIDE_MAX = 16 * 1024 * 1024


class AcquisitionProblem(Exception):
    def __init__(self, message, code='unavailable', http_status=422):
        super().__init__(message)
        self.code=code; self.http_status=http_status


@dataclass(frozen=True)
class VideoIdentity:
    platform: str
    native_id: str
    canonical_url: str
    account: str | None = None


def _sha(path: Path, limit: int, *, allow_empty=False) -> tuple[str,int]:
    if path.is_symlink() or not path.is_file():
        raise AcquisitionProblem('Файл отсутствует или является ссылкой.', 'asset_unavailable')
    size=path.stat().st_size
    if (size == 0 and not allow_empty) or size > limit:
        raise AcquisitionProblem('Размер медиафайла вне допустимого диапазона.', 'invalid_media')
    digest=hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda:src.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest(),size


def _safe(root:Path,path:Path) -> Path:
    # Never trust paths advertised by metadata; derived paths only.
    if root.is_symlink() or path.is_symlink() or any(p.is_symlink() for p in [*path.parents] if p != root):
        raise AcquisitionProblem('Небезопасный путь к видео.','unsafe_path')
    if not path.resolve().is_relative_to(root.resolve()):
        raise AcquisitionProblem('Видео вне разрешённого архива.','unsafe_path')
    return path


def _metadata(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256*1024:
        raise AcquisitionProblem('Нет достоверных метаданных источника.','missing_metadata')
    try:
        data=json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError,UnicodeError) as exc:
        raise AcquisitionProblem('Неверные метаданные видео.','invalid_metadata') from exc
    if not isinstance(data,dict):
        raise AcquisitionProblem('Некорректная структура метаданных.','invalid_metadata')
    return data


class VideoAcquisitionAdapter:
    platform=''
    def validate_url(self,url):
        return self.resolve_identity(url)
    def resolve_identity(self,url):
        raise NotImplementedError
    def candidate_paths(self,identity):
        raise NotImplementedError
    def request_download(self,job_id,identity):
        raise NotImplementedError
    def get_job_status(self,job_id):
        from worker import local_worker as w
        with w.job_lock:
            return dict(w.jobs.get(str(job_id),{}))
    def cancel(self,job_id):
        from worker import local_worker as w
        with w.job_lock:
            if str(job_id) in w.jobs:w.jobs[str(job_id)]['cancel_requested']=True
    def collect_outputs(self,identity):
        last=None
        for root,mp4,meta in self.candidate_paths(identity):
            if not mp4.exists() or not meta.exists():continue
            try:return self.verify_asset(identity,root,mp4,meta)
            except AcquisitionProblem as exc:last=exc
        if last:raise last
        raise AcquisitionProblem('Подтверждённый MP4 не найден в архиве.','asset_unavailable')
    def verify_asset(self,identity,root,mp4,meta):
        root=Path(root);mp4=_safe(root,Path(mp4));meta=_safe(root,Path(meta))
        obj=_metadata(meta)
        actual=self.identity_from_metadata(obj)
        if actual!=identity.native_id:
            raise AcquisitionProblem('Метаданные относятся к другому видео.','wrong_id')
        try:
            returned=self.resolve_identity(str(obj.get('source_url') or ''))
        except (AcquisitionProblem,ValueError):
            raise AcquisitionProblem('Не удалось подтвердить исходную ссылку.','wrong_url') from None
        if returned.platform != identity.platform or returned.native_id!=identity.native_id:
            raise AcquisitionProblem('ID из исходной ссылки не совпадает.','wrong_id')
        digest,size=_sha(mp4,MAX_VIDEO)
        if obj.get('video_sha256')!=digest:
            raise AcquisitionProblem('SHA-256 MP4 не совпадает с метаданными.','hash_mismatch')
        # A minimal MP4 signature check; legacy downloader validates actual media tracks.
        with mp4.open('rb') as stream:
            header=stream.read(12)
        if len(header)<12 or header[4:8]!=b'ftyp':
            raise AcquisitionProblem('Файл не похож на MP4.','invalid_media')
        attachments={}
        for ext,label in (('.srt','subtitles'),('.txt','transcript')):
            p=_safe(root,mp4.with_suffix(ext))
            if p.is_file():
                h,n=_sha(p,SIDE_MAX,allow_empty=True)
                attachments[label]={'path':str(p.resolve()),'sha256':h,'byte_size':n}
        meta_hash,meta_size=_sha(meta,256*1024)
        attachments['metadata']={'path':str(meta.resolve()),'sha256':meta_hash,'byte_size':meta_size}
        return {'platform':identity.platform,'native_id':identity.native_id,
                'canonical_url':identity.canonical_url,'video_sha256':digest,
                'video_path':str(mp4.resolve()),'byte_size':size,
                'metadata':obj,'attachments':attachments,
                'availability_status':'available','acquisition_verified':True}
    def identity_from_metadata(self,obj):
        raise NotImplementedError


class TikTokAdapter(VideoAcquisitionAdapter):
    platform='tiktok'
    def resolve_identity(self,url):
        from worker import tiktok_worker as t
        try:kind,account,vid,canonical=t.parse_link(url)
        except ValueError as exc:raise AcquisitionProblem(str(exc),'invalid_url') from exc
        if kind!='video' or not vid or not account:
            raise AcquisitionProblem('Для загрузки TikTok нужен URL с точным video ID; короткая ссылка сначала должна быть разрешена.','identity_unresolved')
        return VideoIdentity('tiktok',vid,canonical,account)
    def candidate_paths(self,identity):
        from worker import tiktok_worker as t
        root=t.ARCHIVE
        folder=root/identity.account
        return [(root,folder/(identity.native_id+'.mp4'),folder/(identity.native_id+'.json'))]
    def identity_from_metadata(self,obj):return str(obj.get('shortcode') or '')
    def request_download(self,job_id,identity):
        from worker import tiktok_worker as t
        t.download_job(str(job_id),{'source_url':identity.canonical_url,'account':identity.account,
                                    'shortcode':identity.native_id,'engine':'local'})


class YouTubeAdapter(VideoAcquisitionAdapter):
    platform='youtube'
    def resolve_identity(self,url):
        from worker import youtube_worker as y
        try:kind,canonical,vid=y.parse_link(url)
        except ValueError as exc:raise AcquisitionProblem(str(exc),'invalid_url') from exc
        if kind!='video' or not vid:raise AcquisitionProblem('Требуется конкретное YouTube-видео.','identity_unresolved')
        return VideoIdentity('youtube',vid,canonical)
    def candidate_paths(self,identity):
        from worker import youtube_worker as y
        root=y.ARCHIVE
        folder=root/'single'/identity.native_id
        return [(root,folder/'video.mp4',folder/'metadata.json')]
    def identity_from_metadata(self,obj):return str(obj.get('id') or '')
    def request_download(self,job_id,identity):
        from worker import youtube_worker as y
        y.download_job(str(job_id),{'source_url':identity.canonical_url,'video_id':identity.native_id,
                                     'cookie_browser':'none','language':'auto','engine':'local'})


class InstagramAdapter(VideoAcquisitionAdapter):
    platform='instagram'
    def resolve_identity(self,url):
        from worker import local_worker as w
        raw=str(url)
        parts=urlsplit(raw)
        match=re.fullmatch(r'/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,80})/?',parts.path)
        if parts.scheme!='https' or parts.hostname not in ('instagram.com','www.instagram.com') or parts.port or not match:
            raise AcquisitionProblem('Требуется точная HTTPS-ссылка на Instagram Reel/post.','invalid_url')
        code=match.group(1)
        canonical=f'https://www.instagram.com/reel/{code}/' if parts.path.startswith(('/reel/','/reels/')) else f'https://www.instagram.com/p/{code}/'
        return VideoIdentity('instagram',code,canonical,'instagram')
    def candidate_paths(self,identity):
        root=Path.home()/'Downloads'/'InstagramArchive'
        result=[]
        if root.is_dir() and not root.is_symlink():
            for folder in sorted(root.iterdir()):
                if folder.is_dir() and not folder.is_symlink() and re.fullmatch('[A-Za-z0-9._]{1,30}',folder.name):
                    result.append((root,folder/(identity.native_id+'.mp4'),folder/(identity.native_id+'.json')))
        return result
    def identity_from_metadata(self,obj):return str(obj.get('verified_shortcode') or obj.get('shortcode') or '')
    def request_download(self,job_id,identity):
        from worker import local_worker as w
        w.exact_download_job(str(job_id),{'source_url':identity.canonical_url,
            'account':'instagram','shortcode':identity.native_id,'engine':'local'})


ADAPTERS={'tiktok':TikTokAdapter(),'youtube':YouTubeAdapter(),'instagram':InstagramAdapter()}


def adapter_for(platform):
    a=ADAPTERS.get(platform)
    if a is None:raise AcquisitionProblem('Платформа загрузки не поддерживается.','unsupported_platform')
    return a
