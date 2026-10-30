"""Taxonomy Builder Block 2: project questionnaire confirmation and handoff.

Projects and document files remain local. Confirmations live in PostgreSQL,
bound to an exact immutable profile revision and SHA256.
No model selection, quotes, charges or external network requests occur here.
"""
from __future__ import annotations

import re
from uuid import UUID

from intelligence import config

PROJECT_ID = re.compile(r"[0-9a-f]{32}\Z")
ALLOWED_NICHES = frozenset({
    "health & fitness", "health and fitness", "health_fitness",
    "здоровье и фитнес",
})
PRODUCT_TYPES = frozenset({
    "mobile_app", "digital_service", "physical_product",
    "service", "course", "no_product", "other",
})

class IntakeProblem(Exception):
    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.http_status = http_status

def _project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise IntakeProblem("Некорректный идентификатор проекта.", 404)
    return project_id

def _supported_niche(value: object) -> bool:
    """Allow an explicitly declared Health & Fitness category in a rich niche.

    Preserve the owner's original questionnaire text and immutable profile
    hash. Do not infer taxonomy scope from incidental mentions in prose:
    only an exact niche or a first-line 'Основная категория:' list qualifies.
    """
    if not isinstance(value, str):
        return False
    if value.casefold().strip() in ALLOWED_NICHES:
        return True
    first_line = value.strip().split("\n", 1)[0].strip()
    declaration = re.fullmatch(
        r"основная\s+категория\s*:\s*(.+)", first_line,
        flags=re.IGNORECASE,
    )
    if declaration is None:
        return False
    return any(
        token.casefold().strip(" \t.;:") in ALLOWED_NICHES
        for token in declaration.group(1).split("/")
    )


def blockers_for(source: dict) -> list[dict]:
    """Explicit missing-information check; no LLM inference from descriptions."""
    profile = source.get("profile", {})
    errors = []
    def missing(field: str, message: str):
        errors.append({"field":field,"message":message})
    if not _supported_niche(profile.get("niche")):
        missing("niche","Для Taxonomy Builder v1 выбери нишу Health & Fitness.")
    product_type = profile.get("product_type","")
    if product_type not in PRODUCT_TYPES:
        missing("product_type","Укажи тип продукта из списка.")
    if not isinstance(profile.get("audience_problems"),str) or not profile["audience_problems"].strip():
        missing("audience_problems","Опиши реальные проблемы аудитории.")
    if product_type != "no_product" and (not isinstance(profile.get("product_functions"),str)
                                        or not profile["product_functions"].strip()):
        missing("product_functions","Опиши реальные функции продукта (или выбери «Нет продукта»).")
    if not source.get("has_source_material"):
        missing("source_material","Недостаточно исходной информации о проекте.")
    return errors

def _db_reason(sync: str) -> str | None:
    if sync == "synced":
        return None
    return {
        "disabled":"Подключи PostgreSQL: синхронизация проектной БД отключена.",
        "unavailable":"PostgreSQL недоступна. Анкета сохранена локально.",
        "migration_required":"Для подтверждения анкеты нужно применить миграции PostgreSQL.",
        "revision_conflict":"История профиля в PostgreSQL не совпадает с локальной ревизией.",
    }.get(sync,"Анкета ещё не синхронизирована с PostgreSQL.")

def _dsn() -> str:
    if not config.enabled() or not config.dsn():
        raise IntakeProblem("Подключи PostgreSQL, чтобы подтвердить анкету.", 503)
    return config.dsn()

def _connection():
    import psycopg
    try:
        return psycopg.connect(_dsn(),connect_timeout=config.connect_timeout())
    except psycopg.Error as exc:
        raise IntakeProblem("Не удалось связаться с PostgreSQL.", 503) from exc

def _profile_source(project_id: str) -> dict:
    from projects import taxonomy_input
    source=taxonomy_input(_project_id(project_id))
    if source is None:
        raise IntakeProblem("Проект не найден.",404)
    return source

def get_intake_view(project_id: str) -> dict:
    """Read server-authoritative state; an old approval is not inherited."""
    source=_profile_source(project_id)
    missing=blockers_for(source)
    reason=_db_reason(source.get("profile_db_sync","unavailable"))
    confirmed=False
    if reason is None:
        try:
            with _connection() as conn:
                exists=conn.execute("""SELECT 1 FROM tb_intake_confirmations
                  WHERE project_id=%s AND profile_revision=%s AND profile_hash=%s""",
                  (source["project_id"],source["profile_revision"],source["profile_hash"])).fetchone()
                confirmed=bool(exists)
        except IntakeProblem:
            reason="PostgreSQL недоступна. Подтверждение сейчас не проверено."
        except Exception as exc:
            # Migration not installed or temporary backend fault: fail closed.
            reason="Не удалось прочитать подтверждение: проверь миграции PostgreSQL."
    return {
        "project_id":source["project_id"],
        "project_name":source["profile"].get("name",""),
        "profile_revision":source["profile_revision"],
        "profile_hash":source["profile_hash"],
        "profile":source["profile"],
        "research_documents":source["research_documents"],
        "profile_db_sync":source.get("profile_db_sync"),
        "blockers":missing,
        "database_blocker":reason,
        "confirmed":confirmed,
        "can_confirm":not missing and reason is None,
        "next_step":"pipeline_studio" if confirmed and not missing and reason is None
                    else "questionnaire_review",
        "pipeline_studio_available":confirmed and not missing and reason is None,
        "pipeline_configuration_available":confirmed and not missing and reason is None,
    }

def confirm_intake(project_id: str, expected_revision: int,
                   expected_hash: str, confirmed_by: str = "local_owner") -> dict:
    """Transactional confirmation with protection against concurrent local edits.

    Same-app local writes use projects._PROFILE_WRITE_LOCK. PostgreSQL locks
    project_registry row so approval cannot race an external registry update.
    """
    if type(expected_revision) is not int or expected_revision<1:
        raise IntakeProblem("Передай корректную ревизию анкеты.", 400)
    if not isinstance(expected_hash,str) or not re.fullmatch("[0-9a-f]{64}",expected_hash):
        raise IntakeProblem("Передай корректный хеш анкеты.", 400)
    from projects import _PROFILE_WRITE_LOCK
    with _PROFILE_WRITE_LOCK:
        source=_profile_source(project_id)
        if source["profile_revision"] != expected_revision or source["profile_hash"] != expected_hash:
            raise IntakeProblem("Анкета изменилась. Проверь актуальную версию.",409)
        missing=blockers_for(source)
        if missing:
            raise IntakeProblem("Заполни обязательные данные Taxonomy Builder перед подтверждением.",422)
        reason=_db_reason(source.get("profile_db_sync","unavailable"))
        if reason:
            raise IntakeProblem(reason,503 if source.get("profile_db_sync")!="revision_conflict" else 409)
        try:
            with _connection() as conn:
                with conn.transaction():
                    row=conn.execute("""SELECT profile_revision,profile_hash FROM project_registry
                              WHERE id=%s FOR UPDATE""",(project_id,)).fetchone()
                    if row!=(expected_revision,expected_hash):
                        raise IntakeProblem("Версия профиля в PostgreSQL отличается от анкеты.",409)
                    exists=conn.execute("""SELECT 1 FROM project_profile_versions
                         WHERE project_id=%s AND revision=%s AND profile_hash=%s""",
                         (project_id,expected_revision,expected_hash)).fetchone()
                    if not exists:
                        raise IntakeProblem("Подтверждаемый снимок анкеты не найден.",409)
                    conn.execute("""INSERT INTO tb_intake_confirmations
                        (project_id,profile_revision,profile_hash,confirmed_by)
                        VALUES (%s,%s,%s,%s) ON CONFLICT (project_id,profile_revision) DO NOTHING""",
                        (project_id,expected_revision,expected_hash,confirmed_by))
                    confirmed=conn.execute("""SELECT profile_hash FROM tb_intake_confirmations
                        WHERE project_id=%s AND profile_revision=%s""",
                        (project_id,expected_revision)).fetchone()
                    if confirmed is None or confirmed[0]!=expected_hash:
                        raise IntakeProblem("Подтверждена другая версия профиля.",409)
        except IntakeProblem:
            raise
        except Exception as exc:
            # No model request is started; local project data is never rolled back.
            raise IntakeProblem("Не удалось сохранить подтверждение в PostgreSQL.",503) from exc
    return get_intake_view(project_id)
