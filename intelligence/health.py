"""Non-sensitive diagnostic endpoint: does not leak passwords, hosts or tokens."""
from __future__ import annotations
from . import config

def status() -> dict:
    if not config.enabled():
        return {"ok": True, "enabled": False, "database": "not_configured",
                "schema_version": None, "message": "Запустите SETUP_POSTGRES_WINDOWS.bat."}
    if not config.dsn():
        return {"ok": False, "enabled": True, "database": "missing_configuration",
                "schema_version": None, "message": "Не задан CM_DB_DSN."}
    try:
        import psycopg
        with psycopg.connect(config.dsn(), connect_timeout=config.connect_timeout()) as conn:
            version = conn.execute(
                "SELECT to_regclass('public.schema_migrations')"
            ).fetchone()[0]
            if version is None:
                return {"ok": False, "enabled": True, "database": "needs_migration",
                        "schema_version": None, "message": "Запустите python -m intelligence.migrate."}
            # A schema_migrations table alone is not proof that foundations exist.
            essential = conn.execute("""SELECT
                to_regclass('public.videos'), to_regclass('public.creators'),
                to_regclass('public.metric_snapshots'), to_regclass('public.jobs')""").fetchone()
            if not all(essential):
                return {"ok": False, "enabled": True, "database": "incomplete_schema",
                        "schema_version": None, "message": "Не все таблицы созданы. Повтори миграцию."}
            version = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return {"ok": True, "enabled": True, "database": "connected",
                    "schema_version": version[0] if version else None}
    except (ImportError, Exception):
        # Avoid exposing connection strings in exception messages to the UI.
        return {"ok": False, "enabled": True, "database": "unavailable",
                "schema_version": None, "message": "Проверьте Docker Desktop и настройки PostgreSQL."}

if __name__ == "__main__":
    import json
    print(json.dumps(status(), ensure_ascii=False, indent=2))
