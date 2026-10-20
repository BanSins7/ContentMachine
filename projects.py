"""Local-only project onboarding storage.

The nine-field project wizard is an inert intake feature: persisting a project
never starts scraping, transcription, discovery, or external API calls.
Projects and real uploaded documents live outside the Git checkout.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import threading
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

FIELDS = (
    "niche", "goal", "product_description", "product_type", "product_functions",
    "audience_problems", "product_url",
    "competitors", "competitor_socials", "promoter_accounts",
    "video_references",
)
PRODUCT_TYPES = frozenset({
    "", "mobile_app", "digital_service", "physical_product", "service",
    "course", "no_product", "other",
})
PROFILE_SCHEMA_VERSION = 1
_PROFILE_WRITE_LOCK = threading.RLock()
TEXT_LIMIT = 12000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_FILE_COUNT = 10
MAX_REQUEST_BYTES = 35 * 1024 * 1024
EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".xls", ".xlsx",
    ".ppt", ".pptx", ".png", ".jpg", ".jpeg",
}
PROJECT_ID = re.compile(r"[a-f0-9]{32}\Z")


def storage_root() -> Path:
    custom = os.environ.get("CM_PROJECTS_DIR")
    if custom:
        return Path(custom).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ContentMachineData" / "projects"
    return Path.home() / ".contentmachine" / "projects"


def _read_record(directory: Path) -> dict:
    return json.loads((directory / "project.json").read_text(encoding="utf-8"))


def list_projects() -> list[dict]:
    root = storage_root()
    if not root.is_dir():
        return []
    records = []
    for folder in root.iterdir():
        if not folder.is_dir() or not PROJECT_ID.fullmatch(folder.name):
            continue
        try:
            record = _read_record(folder)
            records.append({
                "id": record["id"],
                "name": record["name"],
                "niche": record["niche"],
                "created_at": record["created_at"],
                "file_count": len(record["files"]),
            })
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(records, key=lambda row: (row["created_at"], row["id"]), reverse=True)


def get_project(project_id: str) -> dict | None:
    if not PROJECT_ID.fullmatch(project_id):
        return None
    file = storage_root() / project_id / "project.json"
    if not file.is_file():
        return None
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe_filename(raw: str) -> str:
    # Remove paths from Unix/Windows names and characters forbidden by Windows.
    name = str(raw).replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name)[:110].rstrip(" .")
    if not name or name in (".", "..") or Path(name).suffix.lower() not in EXTENSIONS:
        raise ValueError("Недопустимое расширение документа. Используй PDF, Office, TXT, CSV или изображение.")
    return name


def create_project(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Некорректная анкета проекта.")
    name = payload.get("name", "")
    if not isinstance(name, str) or len(name) > 120:
        raise ValueError("Название проекта слишком длинное.")
    name = name.strip()
    fields = {}
    for key in FIELDS:
        raw = payload.get(key, "")
        if not isinstance(raw, str) or len(raw) > TEXT_LIMIT:
            raise ValueError("Слишком длинное поле анкеты: " + key)
        fields[key] = raw.strip()
    if fields["product_type"] not in PRODUCT_TYPES:
        raise ValueError("Недопустимый тип продукта.")
    files = payload.get("research_files", [])
    if not isinstance(files, list) or len(files) > MAX_FILE_COUNT:
        raise ValueError("Можно прикрепить не больше 10 файлов.")
    prepared = []
    total = 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("data_base64"), str):
            raise ValueError("Некорректное вложение.")
        filename = _safe_filename(item.get("name", ""))
        encoded = item["data_base64"]
        if len(encoded) > (MAX_FILE_BYTES * 4 // 3) + 8:
            raise ValueError("Файл больше 10 МБ: " + filename)
        try:
            blob = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Не удалось прочитать файл: " + filename) from None
        if not blob or len(blob) > MAX_FILE_BYTES:
            raise ValueError("Файл пуст или больше 10 МБ: " + filename)
        total += len(blob)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Общий размер файлов не должен превышать 24 МБ.")
        prepared.append((filename, blob))

    ident = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    record = {
        "id": ident,
        "name": name or ("Проект " + ident[:8]),
        "created_at": created_at,
        "updated_at": created_at,
        "profile_revision": 1,
        **fields,
        "files": [],
        "status": "draft",
    }
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".pending-", dir=root))
    try:
        docs = temp / "research"
        for filename, blob in prepared:
            docs.mkdir(exist_ok=True)
            stored = uuid.uuid4().hex + Path(filename).suffix.lower()
            (docs / stored).write_bytes(blob)
            record["files"].append({"name": filename, "size": len(blob), "stored_name": stored})
        (temp / "project.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.rename(root / ident)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    # Local persistence never depends on PostgreSQL availability.
    result = public_project(record)
    result["profile_db_sync"] = sync_profile_to_db(record)
    return result



class ProjectRevisionConflict(ValueError):
    """Client edited a stale project revision."""


def sync_profile_to_db(record: dict) -> str:
    """Best-effort mirror into local PostgreSQL; local project.json stays authoritative."""
    try:
        from intelligence.project_registry import sync_profile
        return sync_profile(record)
    except Exception:
        # Missing Docker, credentials or a pending migration must not destroy intake data.
        return "unavailable"


def _profile_fields(record: dict) -> dict:
    return {
        "name": record.get("name", ""),
        **{key: record.get(key, "") for key in FIELDS},
    }


def taxonomy_input_record(record: dict) -> dict:
    """Stable, side-effect-free v1 contract for the future Taxonomy Builder."""
    import hashlib
    profile = _profile_fields(record)
    documents = [
        {"index": i, "name": item["name"], "size": item["size"],
         "download_url": "/api/projects/" + record["id"] + "/files/" + str(i)}
        for i, item in enumerate(record.get("files", []))
    ]
    revision = record.get("profile_revision", 1)
    payload = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "project_id": record["id"],
        "profile_revision": revision,
        "profile_updated_at": record.get("updated_at", record["created_at"]),
        "profile": profile,
        "research_documents": documents,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    payload["profile_hash"] = hashlib.sha256(encoded).hexdigest()
    payload["missing_structured_fields"] = [
        name for name in ("product_type", "product_functions", "audience_problems")
        if not profile[name].strip()
    ]
    payload["has_source_material"] = bool(
        any(profile[name].strip() for name in (
            "product_description", "product_functions", "audience_problems",
            "product_url", "competitors", "niche"
        )) or documents
    )
    return payload


def taxonomy_input(project_id: str) -> dict | None:
    record = get_project(project_id)
    if record is None:
        return None
    result = taxonomy_input_record(record)
    result["profile_db_sync"] = sync_profile_to_db(record)
    return result


def update_project(project_id: str, patch: dict) -> dict:
    """Optimistic, atomic local edit; no modification to existing research attachments."""
    if not isinstance(patch, dict):
        raise ValueError("Некорректные изменения проекта.")
    expected = patch.get("expected_revision")
    if type(expected) is not int or expected < 1:
        raise ValueError("Укажи корректную expected_revision.")
    allowed = set(FIELDS) | {"name", "expected_revision"}
    if set(patch) - allowed:
        raise ValueError("Неподдерживаемые поля изменения проекта.")
    with _PROFILE_WRITE_LOCK:
        record = get_project(project_id)
        if record is None:
            raise FileNotFoundError("Проект не найден.")
        current_revision = record.get("profile_revision", 1)
        if current_revision != expected:
            raise ProjectRevisionConflict("Проект изменён в другой вкладке. Обнови страницу.")
        updated = dict(record)
        for key, value in patch.items():
            if key == "expected_revision":
                continue
            limit = 120 if key == "name" else TEXT_LIMIT
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError("Некорректное значение поля: " + key)
            clean = value.strip()
            if key == "product_type" and clean not in PRODUCT_TYPES:
                raise ValueError("Недопустимый тип продукта.")
            if key == "name" and not clean:
                raise ValueError("Название проекта не может быть пустым.")
            updated[key] = clean
        if _profile_fields(updated) == _profile_fields(record):
            result = public_project(record)
            result["profile_db_sync"] = sync_profile_to_db(record)
            return result
        updated["profile_revision"] = current_revision + 1
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
        folder = storage_root() / project_id
        temp = folder / (".profile-" + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, folder / "project.json")
        finally:
            temp.unlink(missing_ok=True)
    result = public_project(updated)
    result["profile_db_sync"] = sync_profile_to_db(updated)
    return result

def public_project(record: dict) -> dict:
    return {**record, "files": [
        {"name": item["name"], "size": item["size"]}
        for item in record["files"]
    ]}


def project_file(project_id: str, file_index: str) -> tuple[Path, str] | None:
    """Resolve only attachments recorded in a valid, local project manifest."""
    if not PROJECT_ID.fullmatch(project_id) or not re.fullmatch(r"(?:0|[1-9][0-9]?)", file_index):
        return None
    record = get_project(project_id)
    if record is None:
        return None
    index = int(file_index)
    files = record.get("files")
    if not isinstance(files, list) or index >= len(files):
        return None
    item = files[index]
    stored = item.get("stored_name", "")
    if not isinstance(stored, str) or not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]+", stored):
        return None
    path = storage_root() / project_id / "research" / stored
    if not path.is_file():
        return None
    return path, item["name"]
