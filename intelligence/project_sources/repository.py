"""PostgreSQL-backed project Reference Registry v1, offline-only.

No rights/copyright gate, external I/O, background jobs, or LLM invocation.
Each operation validates that the project exists and is represented in the DB.
PostgreSQL owns identities and deduplication; immutable source versions and
source-origin rows preserve traceability as users edit their questionnaires.
"""
from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from .normalize import LEGACY_FIELDS, KINDS, ReferenceError, normalize_url, parse_legacy

ID_RE = re.compile(r"[a-f0-9]{32}\Z")


class RegistryProblem(Exception):
    def __init__(self, message: str, http_status: int = 422):
        super().__init__(message)
        self.http_status = http_status


def _project(project_id: str):
    from projects import get_project, sync_profile_to_db
    if not isinstance(project_id, str) or not ID_RE.fullmatch(project_id):
        raise RegistryProblem("Некорректный идентификатор проекта.", 404)
    project = get_project(project_id)
    if project is None:
        raise RegistryProblem("Проект не найден.", 404)
    state = sync_profile_to_db(project)
    if state != "synced":
        raise RegistryProblem("Для Reference Registry необходим PostgreSQL: " + state, 503 if state != 'revision_conflict' else 409)
    return project


def _connection():
    from intelligence import config
    if not config.enabled() or not config.dsn():
        raise RegistryProblem("PostgreSQL для Reference Registry отключён.", 503)
    import psycopg
    try:
        return psycopg.connect(config.dsn(), connect_timeout=config.connect_timeout())
    except psycopg.OperationalError as exc:
        raise RegistryProblem("Нет соединения с PostgreSQL.", 503) from exc


def _uuid(value: str) -> str:
    try:
        value = str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RegistryProblem("Некорректный source_id.", 404) from exc
    return value


def _bounded(value, label: str, limit: int = 250):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise RegistryProblem("Некорректное поле: " + label)
    return value.strip() or None


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _public(row):
    keys = ("id", "project_id", "source_type", "canonical_url", "platform", "platform_native_id",
            "original_input", "origin_field", "label", "user_notes", "link_kind", "ingestion_mode",
            "status", "created_at", "updated_at", "tombstoned_at", "active_version_id")
    return {k if k != "id" else "source_id": _iso(row[k]) if k in ("created_at", "updated_at", "tombstoned_at") else
            str(row[k]) if k in ("id", "active_version_id") and row[k] is not None else row[k] for k in keys}


def _get(conn, project_id, source_id, include_archived=True):
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM project_sources WHERE project_id=%s AND id=%s" +
                    ("" if include_archived else " AND tombstoned_at IS NULL"), (project_id, source_id))
        row = cur.fetchone()
    if row is None:
        raise RegistryProblem("Источник не найден в этом проекте.", 404)
    return row


def _lookup_active(conn, project_id, identity):
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM project_sources WHERE project_id=%s AND identity_key=%s AND tombstoned_at IS NULL",
                    (project_id, identity))
        return cur.fetchone()


def _insert(conn, project_id, *, source_type, url, label=None, user_notes=None,
            origin_field=None, original_input=None):
    from psycopg.rows import dict_row
    if source_type not in KINDS:
        raise RegistryProblem("Некорректный тип источника.")
    try:
        parsed = normalize_url(url, source_type)
    except ReferenceError as exc:
        raise RegistryProblem(str(exc)) from exc
    # Keep the original untouched; user-provided comments are separate.
    original = original_input if original_input is not None else url
    if not isinstance(original, str) or not original.strip() or len(original) > 12000:
        raise RegistryProblem("Некорректное исходное поле.")
    if origin_field is not None and origin_field not in LEGACY_FIELDS:
        raise RegistryProblem("Недопустимое поле происхождения.")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""INSERT INTO project_sources
          (project_id,source_type,origin_field,original_input,canonical_url,platform,
           platform_native_id,user_notes,label,identity_key,link_kind,ingestion_mode)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT DO NOTHING RETURNING *""",
          (project_id, source_type, origin_field, original, parsed.canonical_url,
           parsed.platform, parsed.native_id, _bounded(user_notes, "user_notes", 12000),
           _bounded(label, "label"), parsed.identity_key, parsed.link_kind, parsed.ingestion_mode))
        row = cur.fetchone()
        if row is not None:
            cur.execute("""INSERT INTO project_source_versions
                 (project_id,source_id,version_no,source_locator)
                 VALUES (%s,%s,1,%s::jsonb) RETURNING id""",
                 (project_id,row["id"], '{"kind":"registered_only","fetched":false}'))
            version_id = cur.fetchone()["id"]
            cur.execute("UPDATE project_sources SET active_version_id=%s WHERE id=%s",
                        (version_id,row["id"]))
            row = _get(conn, project_id, row["id"])
    return row, parsed


def list_sources(project_id: str, *, include_archived=False) -> dict:
    _project(project_id)
    from psycopg.rows import dict_row
    with _connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM project_sources WHERE project_id=%s" +
                    ("" if include_archived else " AND tombstoned_at IS NULL") +
                    " ORDER BY created_at,id", (project_id,))
        rows = cur.fetchall()
        cur.execute("SELECT source_id,origin_field,original_line,first_profile_revision FROM project_source_origins WHERE project_id=%s ORDER BY created_at,id", (project_id,))
        origins = cur.fetchall()
    out = [_public(r) for r in rows]
    for entry in out:
        entry["origins"] = [{k:row[k] for k in ("origin_field","original_line","first_profile_revision")}
                            for row in origins if str(row["source_id"]) == entry["source_id"]]
    return {"sources": out, "count": len(out), "processing_started": False}


def create_source(project_id: str, payload: dict) -> dict:
    _project(project_id)
    if not isinstance(payload, dict) or set(payload) - {"url", "source_type", "label", "user_notes"}:
        raise RegistryProblem("Недопустимые поля создания источника.")
    with _connection() as conn:
        row, parsed = _insert(conn, project_id, source_type=payload.get("source_type"),
                              url=payload.get("url"), label=payload.get("label"),
                              user_notes=payload.get("user_notes"))
        if row is None:
            existing = _lookup_active(conn, project_id, parsed.identity_key)
            if existing:
                raise RegistryProblem("Такая ссылка уже зарегистрирована в проекте.", 409)
            raise RegistryProblem("Дубликат источника.", 409)
        return {"source": _public(row)}


def update_source(project_id: str, source_id: str, payload: dict) -> dict:
    _project(project_id)
    source_id = _uuid(source_id)
    if not isinstance(payload, dict) or set(payload) - {"label", "user_notes", "expected_updated_at", "archived"}:
        raise RegistryProblem("Разрешено изменять только подпись, комментарий и архивный статус.")
    expected = payload.get("expected_updated_at")
    if not isinstance(expected, str) or not expected:
        raise RegistryProblem("Нужен expected_updated_at для защиты от конфликтов.")
    archive = payload.get("archived")
    if archive is not None and not isinstance(archive, bool):
        raise RegistryProblem("archived должен быть boolean.")
    with _connection() as conn:
        row = _get(conn, project_id, source_id)
        if row["updated_at"].isoformat() != expected:
            raise RegistryProblem("Источник изменился. Обнови страницу.", 409)
        label = _bounded(payload.get("label", row["label"]), "label")
        notes = _bounded(payload.get("user_notes", row["user_notes"]), "user_notes", 12000)
        from psycopg.rows import dict_row
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""UPDATE project_sources SET label=%s, user_notes=%s,
                  tombstoned_at=CASE WHEN %s IS NULL THEN tombstoned_at WHEN %s THEN COALESCE(tombstoned_at,now()) ELSE NULL END,
                  updated_at=GREATEST(now(),updated_at + interval '1 microsecond')
                  WHERE project_id=%s AND id=%s AND updated_at=%s::timestamptz RETURNING *""",
                  (label,notes,archive,archive,project_id,source_id,expected))
                edited = cur.fetchone()
        except Exception as exc:
            from psycopg.errors import UniqueViolation
            if isinstance(exc, UniqueViolation):
                raise RegistryProblem("Дублирующая активная ссылка в проекте.", 409) from exc
            raise
        if edited is None:
            raise RegistryProblem("Источник изменился. Обнови страницу.", 409)
        return {"source": _public(edited)}


def archive_source(project_id: str, source_id: str, expected_updated_at: str) -> dict:
    result = update_source(project_id, source_id, {"archived": True, "expected_updated_at": expected_updated_at})
    return {"source": result["source"], "archived": True}


def import_legacy(project_id: str, *, dry_run=False) -> dict:
    project = _project(project_id)
    if not isinstance(dry_run, bool):
        raise RegistryProblem("dry_run должен быть boolean.")
    proposals, warnings = parse_legacy(project)
    if dry_run:
        return {"proposals": proposals, "warnings": warnings, "proposed": len(proposals), "dry_run": True}
    inserted, linked, skipped = 0, 0, 0
    from psycopg.rows import dict_row
    with _connection() as conn:
        # All rows are one transaction. Concurrent legacy imports are serialized
        # by a project-specific row lock, then constrained by unique indexes.
        conn.execute("SELECT id FROM project_registry WHERE id=%s FOR UPDATE", (project_id,))
        for p in proposals:
            key = p["legacy_import_key"]
            found = conn.execute("SELECT 1 FROM project_source_origins WHERE project_id=%s AND import_key=%s",
                                 (project_id,key)).fetchone()
            if found:
                skipped += 1
                continue
            normalized = normalize_url(p["url"],p["source_type"])
            row = _lookup_active(conn, project_id, normalized.identity_key)
            if row is None:
                row, _ = _insert(conn, project_id, **{k:v for k,v in p.items() if k != "legacy_import_key"})
                if row is not None:
                    inserted += 1
            if row is None:
                # An archived reference or concurrent identity conflict is not
                # automatically restored by reconciliation.
                skipped += 1
                continue
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""INSERT INTO project_source_origins
                   (project_id,source_id,origin_field,original_line,import_key,first_profile_revision)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                   (project_id,row["id"],p["origin_field"],p["original_input"],key,
                    project.get("profile_revision", 1)))
                if cur.fetchone() is not None:
                    linked += 1
    return {"created": inserted, "origins_added": linked, "skipped": skipped,
            "warnings": warnings, "total_proposals": len(proposals), "dry_run": False}
