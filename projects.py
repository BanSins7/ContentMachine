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
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

FIELDS = (
    "niche", "goal", "product_description", "product_url",
    "competitors", "competitor_socials", "promoter_accounts",
    "video_references",
)
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
    record = {
        "id": ident,
        "name": name or ("Проект " + ident[:8]),
        "created_at": datetime.now(timezone.utc).isoformat(),
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
    # The returned project deliberately omits internal storage filenames.
    return public_project(record)


def public_project(record: dict) -> dict:
    return {**record, "files": [
        {"name": item["name"], "size": item["size"]}
        for item in record["files"]
    ]}
