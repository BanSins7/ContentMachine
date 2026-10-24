"""Project-scoped Research File Library (PSI stage 03).

No external requests, LLM, parsing, or copyright policy gates. Immutable version
bytes on disk, immutable metadata in PostgreSQL. Old project.json untouched.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PROJECT_BYTES = 100 * 1024 * 1024  # includes all historical versions and legacy bytes
MAX_ACTIVE_FILES = 50
MAX_VERSIONS = 100
MAX_POST_BYTES = 14 * 1024 * 1024
MIN_FREE_BYTES = 100 * 1024 * 1024
LEGACY_NAMESPACE = UUID('bdc78433-d5b7-432b-91aa-4c07464a9a3c')
EXTENSIONS = {
    '.pdf': ('application/pdf','supported_next_stage'),
    '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document','supported_next_stage'),
    '.xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','supported_next_stage'),
    '.txt': ('text/plain','supported_next_stage'),
    '.md': ('text/markdown','supported_next_stage'),
    '.csv': ('text/csv','supported_next_stage'),
    '.doc': ('application/msword','deferred'),
    '.xls': ('application/vnd.ms-excel','deferred'),
    '.ppt': ('application/vnd.ms-powerpoint','deferred'),
    '.pptx': ('application/vnd.openxmlformats-officedocument.presentationml.presentation','deferred'),
    '.png': ('image/png','deferred'),
    '.jpg': ('image/jpeg','deferred'),
    '.jpeg': ('image/jpeg','deferred'),
}
ZIP_REQUIRED={'.docx':'word/document.xml','.xlsx':'xl/workbook.xml','.pptx':'ppt/presentation.xml'}
FILE_ID_RE = re.compile(r'[0-9a-f-]{36}\Z')
STORED_LEGACY_RE=re.compile(r'[0-9a-f]{32}\.[a-z0-9]{2,5}\Z')

class FileProblem(Exception):
    def __init__(self, message: str, http_status: int = 422):
        super().__init__(message)
        self.http_status = http_status


def clean_name(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 260:
        raise FileProblem('Некорректное имя файла.')
    if '/' in value or '\\' in value or '..' in value or any(ord(c)<32 for c in value):
        raise FileProblem('Имя файла не должно содержать путь или управляющие символы.')
    value=value.strip().rstrip(' .')
    if not value or len(value)>110 or any(c in '<>:"|?*' for c in value):
        raise FileProblem('Недопустимое имя файла.')
    if Path(value).suffix.lower() not in EXTENSIONS:
        raise FileProblem('Формат файла не поддерживается.')
    return value


def inspect_blob(name: str, blob: bytes) -> tuple[str,str]:
    name=clean_name(name)
    if not isinstance(blob,bytes) or not 1<=len(blob)<=MAX_FILE_BYTES:
        raise FileProblem('Файл пуст или превышает 10 МБ.')
    ext=Path(name).suffix.lower()
    mime,eligibility=EXTENSIONS[ext]
    if ext=='.pdf' and not blob.startswith(b'%PDF-'):
        raise FileProblem('PDF имеет неверную сигнатуру.')
    if ext in ('.doc','.xls','.ppt') and not blob.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        raise FileProblem('Файл Office имеет неверную сигнатуру.')
    if ext=='.png' and not blob.startswith(b'\x89PNG\r\n\x1a\n'):
        raise FileProblem('PNG имеет неверную сигнатуру.')
    if ext in ('.jpg','.jpeg') and not blob.startswith(b'\xff\xd8\xff'):
        raise FileProblem('JPEG имеет неверную сигнатуру.')
    if ext in ZIP_REQUIRED:
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                files=archive.infolist()
                if len(files)>1024 or not files:
                    raise FileProblem('Слишком много элементов в архиве Office.')
                unpacked=0
                for info in files:
                    if info.flag_bits & 1:
                        raise FileProblem('Зашифрованные Office-архивы не поддерживаются.')
                    parts=info.filename.replace('\\','/').split('/')
                    if not info.filename or info.filename.startswith('/') or '..' in parts or any(not x for x in parts[:-1]):
                        raise FileProblem('Недопустимый путь внутри Office-архива.')
                    if ((info.external_attr>>16)&0o170000)==stat.S_IFLNK:
                        raise FileProblem('Символическая ссылка внутри Office-архива.')
                    unpacked+=info.file_size
                    if unpacked>80*1024*1024 or (info.file_size>100000 and info.file_size>max(1,info.compress_size)*1000):
                        raise FileProblem('Подозрительная степень сжатия документа.')
                if ZIP_REQUIRED[ext] not in archive.namelist():
                    raise FileProblem('В архиве отсутствует обязательная часть документа.')
        except (zipfile.BadZipFile,zipfile.LargeZipFile) as exc:
            raise FileProblem('Неверная структура Office-документа.') from exc
    if ext in ('.txt','.md','.csv'):
        if b'\x00' in blob or sum(c < 9 or 13 < c < 32 for c in blob[:4096])>16:
            raise FileProblem('Текстовый файл содержит бинарные данные.')
        try:blob.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:blob.decode('cp1251')
            except UnicodeDecodeError as exc:raise FileProblem('Текстовая кодировка не поддерживается.') from exc
    return mime,eligibility


def decode_upload(payload: dict) -> tuple[str,bytes,str,str,str]:
    if not isinstance(payload,dict) or set(payload)-{'name','data_base64','uploaded_by','expected_updated_at'}:
        raise FileProblem('Недопустимые поля файла.')
    name=clean_name(payload.get('name'))
    encoded=payload.get('data_base64')
    if not isinstance(encoded,str) or len(encoded)>MAX_FILE_BYTES*4//3+8:
        raise FileProblem('Файл превышает допустимый размер.')
    try:blob=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error) as exc:raise FileProblem('Некорректный Base64.') from exc
    mime,eligibility=inspect_blob(name,blob)
    uploaded_by=payload.get('uploaded_by','local_user')
    if not isinstance(uploaded_by,str) or not 1<=len(uploaded_by.strip())<=120:
        raise FileProblem('Некорректный uploaded_by.')
    return name,blob,mime,eligibility,uploaded_by.strip()


def _project(project_id):
    # Import uses the established Registry synchronizer; it never updates project.json.
    from intelligence.project_sources.repository import _project as registry_project, RegistryProblem
    try:return registry_project(project_id)
    except RegistryProblem as exc:raise FileProblem(str(exc),exc.http_status) from exc


def _connect():
    from intelligence.project_sources.repository import _connection, RegistryProblem
    try:return _connection()
    except RegistryProblem as exc:raise FileProblem(str(exc),exc.http_status) from exc


def _id(value):
    try:return str(UUID(str(value)))
    except (ValueError,TypeError) as exc:raise FileProblem('Неверный file_id.',404) from exc


def _project_folder(pid: str) -> Path:
    from projects import storage_root
    root=storage_root()
    folder=root/pid
    if not folder.is_dir() or folder.is_symlink() or root.is_symlink():
        raise FileProblem('Хранилище проекта недоступно.',503)
    return folder


def _safe_path(folder: Path, relative: str, *, must_exist: bool=False):
    if not isinstance(relative,str) or '\\' in relative or relative.startswith('/'):
        raise FileProblem('Небезопасный путь файла.',503)
    parts=Path(relative).parts
    if not parts or any(p in ('','..','.') for p in parts) or parts[0] not in ('research_library','research'):
        raise FileProblem('Небезопасный путь файла.',503)
    target=folder.joinpath(*parts)
    cur=folder
    for part in parts:
        cur=cur/part
        if cur.is_symlink():
            raise FileProblem('Обнаружена символическая ссылка.',503)
    if must_exist and (not target.is_file() or not target.resolve().is_relative_to(folder.resolve())):
        raise FileProblem('Оригинальный файл отсутствует.',409)
    return target


def _version_ref(file_id: str, number: int, sha: str) -> str:
    return f'research_library/{file_id[:2]}/{file_id}/v{number:05d}_{sha}.bin'


def _write_durable(folder: Path, ref: str, blob: bytes) -> Path:
    target=_safe_path(folder,ref)
    parent=target.parent
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    _safe_path(folder,ref)  # Re-check symlink components after mkdir.
    if target.exists():
        raise FileProblem('Коллизия пути версии файла.',409)
    fd,tmp=tempfile.mkstemp(prefix='.incoming-',dir=parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(blob); f.flush();os.fsync(f.fileno())
        os.replace(tmp,target)
        if os.name!='nt':
            descriptor=os.open(parent,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return target


def _row(row):
    out={}
    for k,v in row.items():
        if isinstance(v,(datetime,UUID)):
            v=str(v) if isinstance(v,UUID) else v.isoformat()
        out['file_id' if k=='id' else k]=v
    return out


def _locked_project(conn,pid):
    if conn.execute('SELECT id FROM project_registry WHERE id=%s FOR UPDATE',(pid,)).fetchone() is None:
        raise FileProblem('Проект не синхронизирован с PostgreSQL.',503)


def _quotas(conn,pid,folder, blob_size, new_file):
    active=conn.execute('SELECT count(*) FROM project_research_files WHERE project_id=%s AND archived_at IS NULL',(pid,)).fetchone()[0]
    if new_file and active>=MAX_ACTIVE_FILES:
        raise FileProblem('Лимит 50 активных документов достигнут.',409)
    # Include historic attachments not yet imported; cap actual source bytes.
    old=0
    for path in (folder/'research').glob('*') if (folder/'research').is_dir() else ():
        if path.is_file() and not path.is_symlink():old+=path.stat().st_size
    existing=conn.execute("""SELECT coalesce(sum(byte_size),0) FROM project_research_file_versions
      WHERE project_id=%s AND origin='upload'""",(pid,)).fetchone()[0]
    if old+existing+blob_size>MAX_PROJECT_BYTES:
        raise FileProblem('Общий лимит 100 МБ для проекта и истории версий превышен.',409)
    if shutil.disk_usage(folder).free < blob_size+MIN_FREE_BYTES:
        raise FileProblem('Недостаточно свободного места на диске.',507)


def _save_version(conn,pid,fid,name,mime,eligibility,uploaded_by,sha,size,ref,version,origin):
    from psycopg.rows import dict_row
    src=conn.execute("""INSERT INTO project_source_versions
      (project_id,source_id,version_no,content_sha256,byte_size,media_type,fetched_or_uploaded_at,
       source_locator,immutable_asset_ref)
      VALUES (%s,%s,%s,%s,%s,%s,now(),%s::jsonb,%s) RETURNING id""",
      (pid,fid,version,sha,size,mime,json.dumps({'kind':'local_file','filename':name,'origin':origin}),ref)).fetchone()[0]
    asset=conn.execute("""INSERT INTO project_media_assets
      (project_id,source_id,source_version_id,asset_kind,content_sha256,byte_size,media_type,storage_ref)
      VALUES (%s,%s,%s,'document',%s,%s,%s,%s) RETURNING id""",
      (pid,fid,src,sha,size,mime,ref)).fetchone()[0]
    conn.execute("""INSERT INTO project_research_file_versions
      (project_id,file_id,source_version_id,version_no,content_sha256,byte_size,mime_type,
       original_name,uploaded_by,parser_eligibility,storage_ref,media_asset_id,origin)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (pid,fid,src,version,sha,size,mime,name,uploaded_by,eligibility,ref,asset,origin))
    conn.execute('UPDATE project_sources SET active_version_id=%s,updated_at=now() WHERE project_id=%s AND id=%s',(src,pid,fid))
    conn.execute('UPDATE project_research_files SET active_version_id=(SELECT id FROM project_research_file_versions WHERE project_id=%s AND file_id=%s AND version_no=%s),updated_at=clock_timestamp(),display_name=%s WHERE project_id=%s AND id=%s',
                 (pid,fid,version,name,pid,fid))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM project_research_file_versions WHERE project_id=%s AND file_id=%s AND version_no=%s',(pid,fid,version))
        return _row(cur.fetchone())


def _create_source(conn,pid,fid,name,uploaded_by,origin):
    identity=('legacy-file:' if origin=='legacy' else 'file:')+fid
    conn.execute("""INSERT INTO project_sources
       (id,project_id,source_type,origin_field,original_input,identity_key,ingestion_mode)
       VALUES (%s,%s,'research_document','research_files',%s,%s,'metadata_only')""",
       (fid,pid,name,identity))
    conn.execute("""INSERT INTO project_research_files
       (id,project_id,source_id,display_name,uploaded_by,origin)
       VALUES (%s,%s,%s,%s,%s,%s)""",(fid,pid,fid,name,uploaded_by,origin))


def _file_row(conn,pid,fid,locked=False):
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM project_research_files WHERE project_id=%s AND id=%s'+(' FOR UPDATE' if locked else ''),(pid,fid))
        row=cur.fetchone()
    if row is None:raise FileProblem('Документ не найден в проекте.',404)
    return row


def add_file(project_id,payload):
    _project(project_id)
    name,blob,mime,eligibility,by=decode_upload(payload)
    fid=str(uuid.uuid4())
    sha=hashlib.sha256(blob).hexdigest()
    ref=_version_ref(fid,1,sha)
    folder=_project_folder(project_id)
    saved=None
    commit_started=False
    with _connect() as conn:
        try:
            _locked_project(conn,project_id)
            _quotas(conn,project_id,folder,len(blob),True)
            saved=_write_durable(folder,ref,blob)
            _create_source(conn,project_id,fid,name,by,'upload')
            version=_save_version(conn,project_id,fid,name,mime,eligibility,by,sha,len(blob),ref,1,'upload')
            commit_started=True
            conn.commit()
        except BaseException:
            try:conn.rollback()
            except Exception:pass
            # An exception *during* COMMIT may mean PostgreSQL committed but the
            # acknowledgement was lost. Never remove bytes in that uncertain case.
            if saved is not None and not commit_started:saved.unlink(missing_ok=True)
            raise
    return {'file_id':fid,'version':version}


def replace_file(project_id,file_id,payload):
    _project(project_id)
    fid=_id(file_id)
    name,blob,mime,eligibility,by=decode_upload(payload)
    expected=payload.get('expected_updated_at')
    if not isinstance(expected,str) or not expected:
        raise FileProblem('Для замены укажи expected_updated_at.')
    sha=hashlib.sha256(blob).hexdigest()
    folder=_project_folder(project_id)
    saved=None
    commit_started=False
    with _connect() as conn:
        try:
            _locked_project(conn,project_id)
            row=_file_row(conn,project_id,fid,True)
            if row['archived_at'] is not None:raise FileProblem('Сначала восстанови документ.',409)
            if row['updated_at'].isoformat()!=expected:raise FileProblem('Документ изменён в другой вкладке.',409)
            count=conn.execute('SELECT max(version_no) FROM project_research_file_versions WHERE project_id=%s AND file_id=%s',(project_id,fid)).fetchone()[0]
            if count>=MAX_VERSIONS:raise FileProblem('Лимит версий документа достигнут.',409)
            _quotas(conn,project_id,folder,len(blob),False)
            number=count+1
            ref=_version_ref(fid,number,sha)
            saved=_write_durable(folder,ref,blob)
            version=_save_version(conn,project_id,fid,name,mime,eligibility,by,sha,len(blob),ref,number,'upload')
            commit_started=True
            conn.commit()
        except BaseException:
            try:conn.rollback()
            except Exception:pass
            if saved is not None and not commit_started:saved.unlink(missing_ok=True)
            raise
    return {'file_id':fid,'version':version}


def list_files(project_id,include_archived=False):
    _project(project_id)
    from psycopg.rows import dict_row
    with _connect() as conn,conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT f.*,v.version_no,v.mime_type,v.byte_size,v.content_sha256,
          v.parser_eligibility,v.storage_ref,v.source_version_id,
          coalesce(j.state,sv.initial_extraction_status) AS processing_status
          FROM project_research_files f
          JOIN project_research_file_versions v ON v.project_id=f.project_id AND v.id=f.active_version_id
          JOIN project_source_versions sv ON sv.project_id=v.project_id AND sv.id=v.source_version_id
          LEFT JOIN LATERAL (
            SELECT state FROM source_extraction_jobs e
            WHERE e.project_id=v.project_id AND e.source_version_id=v.source_version_id
              AND e.stage='document_parse' ORDER BY e.created_at DESC,e.id DESC LIMIT 1
          ) j ON TRUE
          WHERE f.project_id=%s"""+('' if include_archived else ' AND f.archived_at IS NULL')+" ORDER BY f.created_at DESC,f.id",(project_id,))
        rows=[_row(x) for x in cur.fetchall()]
    folder=_project_folder(project_id)
    for x in rows:
        try:
            p=_safe_path(folder,x['storage_ref'],must_exist=True)
            x['storage_status']='ready' if hashlib.sha256(p.read_bytes()).hexdigest()==x['content_sha256'] else 'corrupt'
        except (FileProblem,OSError):x['storage_status']='missing'
    return {'files':rows,'count':len(rows),'limits':{'max_file_bytes':MAX_FILE_BYTES,'max_active_files':MAX_ACTIVE_FILES,'max_project_bytes':MAX_PROJECT_BYTES}}


def versions(project_id,file_id):
    _project(project_id)
    fid=_id(file_id)
    from psycopg.rows import dict_row
    with _connect() as conn,conn.cursor(row_factory=dict_row) as cur:
        _file_row(conn,project_id,fid)
        cur.execute("""SELECT v.*,coalesce(j.state,sv.initial_extraction_status) AS processing_status
          FROM project_research_file_versions v
          JOIN project_source_versions sv ON sv.project_id=v.project_id AND sv.id=v.source_version_id
          LEFT JOIN LATERAL (
            SELECT state FROM source_extraction_jobs e
            WHERE e.project_id=v.project_id AND e.source_version_id=v.source_version_id
              AND e.stage='document_parse' ORDER BY e.created_at DESC,e.id DESC LIMIT 1
          ) j ON TRUE WHERE v.project_id=%s AND v.file_id=%s ORDER BY v.version_no DESC""",(project_id,fid))
        rows=[_row(v) for v in cur.fetchall()]
    return {'file_id':fid,'versions':rows}


def file_download(project_id,file_id,version=None):
    _project(project_id)
    fid=_id(file_id)
    from psycopg.rows import dict_row
    with _connect() as conn,conn.cursor(row_factory=dict_row) as cur:
        _file_row(conn,project_id,fid)
        if version is None:
            cur.execute("""SELECT v.* FROM project_research_file_versions v
              JOIN project_research_files f ON f.project_id=v.project_id AND f.active_version_id=v.id
              WHERE f.project_id=%s AND f.id=%s""",(project_id,fid))
        else:
            if not isinstance(version,int) or not 1<=version<=MAX_VERSIONS:
                raise FileProblem('Неверный номер версии.')
            cur.execute('SELECT * FROM project_research_file_versions WHERE project_id=%s AND file_id=%s AND version_no=%s',(project_id,fid,version))
        row=cur.fetchone()
    if row is None:raise FileProblem('Версия документа не найдена.',404)
    folder=_project_folder(project_id)
    target=_safe_path(folder,row['storage_ref'],must_exist=True)
    data=target.read_bytes()
    if len(data)!=row['byte_size'] or hashlib.sha256(data).hexdigest()!=row['content_sha256']:
        raise FileProblem('Повреждён файл версии: SHA-256 не совпадает.',409)
    return data,row['original_name'],row['mime_type']


def _status(project_id,file_id,expected,archive):
    _project(project_id)
    fid=_id(file_id)
    if not isinstance(expected,str) or not expected:raise FileProblem('Нужен expected_updated_at.')
    with _connect() as conn:
        _locked_project(conn,project_id)
        row=_file_row(conn,project_id,fid,True)
        if row['updated_at'].isoformat()!=expected:raise FileProblem('Документ изменён в другой вкладке.',409)
        if (row['archived_at'] is not None)==archive:raise FileProblem('Статус уже изменён.',409)
        if not archive:
            n=conn.execute('SELECT count(*) FROM project_research_files WHERE project_id=%s AND archived_at IS NULL',(project_id,)).fetchone()[0]
            if n>=MAX_ACTIVE_FILES:raise FileProblem('Лимит активных документов.',409)
        conn.execute('UPDATE project_research_files SET archived_at='+('clock_timestamp()' if archive else 'NULL')+',updated_at=clock_timestamp() WHERE project_id=%s AND id=%s',(project_id,fid))
        conn.execute('UPDATE project_sources SET tombstoned_at='+('clock_timestamp()' if archive else 'NULL')+',updated_at=clock_timestamp() WHERE project_id=%s AND id=%s',(project_id,fid))
        conn.commit()
    return {'file_id':fid,'archived':archive}


def archive_file(project_id,file_id,expected):return _status(project_id,file_id,expected,True)
def restore_file(project_id,file_id,expected):return _status(project_id,file_id,expected,False)


def import_legacy(project_id,dry_run=False):
    if type(dry_run) is not bool:raise FileProblem('dry_run должен быть boolean.')
    project=_project(project_id)
    folder=_project_folder(project_id)
    candidates=[]
    for item in project.get('files',[]):
        if not isinstance(item,dict) or not isinstance(item.get('stored_name'),str) or not STORED_LEGACY_RE.fullmatch(item['stored_name']):
            continue
        file_id=str(uuid5(LEGACY_NAMESPACE,project_id+':'+item['stored_name']))
        file_path=_safe_path(folder,'research/'+item['stored_name'],must_exist=True)
        name=clean_name(item['name'])
        blob=file_path.read_bytes()
        mime,eligibility=inspect_blob(name,blob)
        if len(blob)!=item.get('size'):raise FileProblem('Старый документ имеет неверный размер.',409)
        candidates.append((file_id,name,blob,mime,eligibility,'research/'+item['stored_name']))
    if dry_run:return {'candidates':len(candidates),'created':0,'dry_run':True}
    created=0
    with _connect() as conn:
        _locked_project(conn,project_id)
        for fid,name,blob,mime,eligibility,ref in candidates:
            if conn.execute('SELECT 1 FROM project_research_files WHERE project_id=%s AND id=%s',(project_id,fid)).fetchone():continue
            _create_source(conn,project_id,fid,name,'legacy_import','legacy')
            _save_version(conn,project_id,fid,name,mime,eligibility,'legacy_import',hashlib.sha256(blob).hexdigest(),len(blob),ref,1,'legacy')
            created+=1
        conn.commit()
    return {'candidates':len(candidates),'created':created,'dry_run':False}


def repair_scan(project_id, quarantine=False, age_seconds=600):
    """Identify orphaned uploaded byte blobs; opt-in quarantine, never delete snapshots.

    Call only for a quiescent project after a crash. Fresh in-flight blobs are ignored.
    """
    _project(project_id)
    folder=_project_folder(project_id)
    with _connect() as conn:
        refs={x[0] for x in conn.execute("SELECT storage_ref FROM project_research_file_versions WHERE project_id=%s AND origin='upload'",(project_id,)).fetchall()}
    import time
    orphaned=[]
    lib=folder/'research_library'
    if lib.exists():
        if lib.is_symlink():raise FileProblem('Символическая ссылка в библиотеке.',503)
        for f in lib.rglob('*'):
            if f.is_symlink():raise FileProblem('Символическая ссылка в библиотеке.',503)
            if not f.is_file() or f.name.startswith('.incoming-'):
                continue
            rel=f.relative_to(folder).as_posix()
            if rel not in refs and time.time()-f.stat().st_mtime>=age_seconds:
                orphaned.append(rel)
                if quarantine:
                    target=folder/'research_quarantine'/uuid.uuid4().hex
                    target.parent.mkdir(mode=0o700,exist_ok=True)
                    os.replace(f,target)
    return {'orphaned':orphaned,'quarantined':len(orphaned) if quarantine else 0}
