"""Saved, project-scoped pipeline test scenarios.

A scenario is a bookmark and a test boundary, NOT a pipeline execution. Keeping
the original project ID preserves its RTB snapshots, files and PostgreSQL links.
No provider calls are made by these endpoints.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from projects import PROJECT_ID, get_project, list_projects, storage_root

SCENARIO_ID = re.compile(r"[0-9a-f]{32}\Z")
STAGES = (
    "foundation", "rtb", "search", "collector", "sre", "qdp",
    "group-a", "group-b", "group-c", "analytics", "insights",
)
_LOCK = threading.RLock()


class RevisionConflict(ValueError):
    pass


def _root() -> Path:
    return storage_root() / "_test_lab"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and SCENARIO_ID.fullmatch(value) is not None


def _file(scenario_id: str) -> Path:
    if not _valid_id(scenario_id):
        raise ValueError("Некорректный ID теста.")
    return _root() / (scenario_id + ".json")


def _write(record: dict) -> None:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    dest = _file(record["id"])
    temp = root / ("." + record["id"] + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def _decorate(record: dict) -> dict:
    project = get_project(record["project_id"])
    return {
        **record,
        "project_name": project.get("name", "Без названия") if project else "Проект недоступен",
        "project_available": project is not None,
    }


def get_scenario(scenario_id: str) -> dict | None:
    if not _valid_id(scenario_id):
        return None
    try:
        record = json.loads(_file(scenario_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(record, dict) or record.get("id") != scenario_id:
        return None
    return _decorate(record)


def list_scenarios() -> list[dict]:
    root = _root()
    if not root.is_dir():
        return []
    records = []
    for path in root.glob("*.json"):
        if not _valid_id(path.stem):
            continue
        record = get_scenario(path.stem)
        if record:
            records.append(record)
    return sorted(records, key=lambda item: (item.get("updated_at", ""), item["id"]), reverse=True)


def create_scenario(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - {"name", "project_id", "target_stage"}:
        raise ValueError("Некорректные поля теста.")
    project_id = data.get("project_id")
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise ValueError("Выбери проект из библиотеки.")
    project = get_project(project_id)
    if project is None:
        raise FileNotFoundError("Исходный проект не найден.")
    name = data.get("name", "")
    if not isinstance(name, str) or len(name) > 120:
        raise ValueError("Название теста не должно превышать 120 символов.")
    name = name.strip() or (project["name"] + " · Тест pipeline")[:120]
    target = data.get("target_stage", "search")
    if target not in STAGES or not isinstance(target, str):
        raise ValueError("Неизвестный этап pipeline.")
    now = _now()
    record = {
        "id": uuid.uuid4().hex, "project_id": project_id, "name": name,
        "target_stage": target, "last_opened_stage": None,
        "last_opened_at": None, "revision": 1,
        "created_at": now, "updated_at": now,
    }
    with _LOCK:
        _write(record)
    return _decorate(record)


def update_scenario(scenario_id: str, data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - {"expected_revision", "target_stage", "last_opened_stage"}:
        raise ValueError("Некорректные поля обновления теста.")
    revision = data.get("expected_revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("Укажи текущую версию сценария.")
    with _LOCK:
        record = get_scenario(scenario_id)
        if record is None:
            raise FileNotFoundError("Тест не найден.")
        if record["revision"] != revision:
            raise RevisionConflict("Тест изменён в другой вкладке. Обнови данные.")
        if not record["project_available"]:
            raise FileNotFoundError("Исходный проект недоступен.")
        target = data.get("target_stage", record["target_stage"])
        if not isinstance(target, str) or target not in STAGES:
            raise ValueError("Неизвестный этап pipeline.")
        opened = data.get("last_opened_stage", record["last_opened_stage"])
        if opened is not None and (not isinstance(opened, str) or opened not in STAGES):
            raise ValueError("Неизвестный открываемый этап.")
        if opened and STAGES.index(opened) > STAGES.index(target):
            if "last_opened_stage" in data:
                raise ValueError("Этап выходит за пределы теста.")
            opened = None
        changed = target != record["target_stage"] or opened != record["last_opened_stage"]
        if not changed:
            return record
        record["target_stage"] = target
        if opened != record["last_opened_stage"]:
            record["last_opened_stage"] = opened
            record["last_opened_at"] = _now() if opened else None
        elif "last_opened_stage" in data and opened:
            record["last_opened_at"] = _now()
        record["updated_at"] = _now()
        record["revision"] += 1
        persisted = {key: value for key, value in record.items() if key not in ("project_name", "project_available")}
        _write(persisted)
    return _decorate(persisted)


def _read_data(handler) -> dict:
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        if not 2 <= size <= 4096:
            raise ValueError("Некорректный размер запроса.")
        data = json.loads(handler.rfile.read(size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Неверный JSON.") from None
    if not isinstance(data, dict):
        raise ValueError("Нужен JSON-объект.")
    return data


def handle_get(handler, url) -> bool:
    if url.path == "/api/test-lab":
        handler.json_response(200, {"ok": True, "projects": list_projects(), "scenarios": list_scenarios()})
        return True
    match = re.fullmatch(r"/api/test-lab/([0-9a-f]{32})", url.path)
    if match:
        record = get_scenario(match.group(1))
        handler.json_response(200 if record else 404, {"ok": True, "scenario": record} if record else {"error": "Тест не найден."})
        return True
    if url.path.startswith("/api/test-lab"):
        handler.json_response(404, {"error": "Не найдено."})
        return True
    return False


def handle_post(handler, route: str) -> bool:
    if not route.startswith("/api/test-lab"):
        return False
    match = re.fullmatch(r"/api/test-lab/([0-9a-f]{32})", route)
    if route != "/api/test-lab" and not match:
        handler.json_response(404, {"error": "Не найдено."})
        return True
    try:
        data = _read_data(handler)
        record = create_scenario(data) if route == "/api/test-lab" else update_scenario(match.group(1), data)
        handler.json_response(201 if route == "/api/test-lab" else 200, {"ok": True, "scenario": record})
    except RevisionConflict as exc:
        handler.json_response(409, {"error": str(exc)})
    except FileNotFoundError as exc:
        handler.json_response(404, {"error": str(exc)})
    except ValueError as exc:
        handler.json_response(400, {"error": str(exc)})
    return True
