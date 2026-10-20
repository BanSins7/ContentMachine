"""Mirror local project intake into PostgreSQL for future Taxonomy Builder.

Local project.json remains authoritative, PostgreSQL is an optional mirror.
Never create taxonomy categories or call an LLM here.
"""
from __future__ import annotations

from . import config


def sync_profile(record: dict) -> str:
    if not config.enabled():
        return "disabled"
    if not config.dsn():
        return "unavailable"
    import psycopg
    from projects import taxonomy_input_record
    contract = taxonomy_input_record(record)
    revision = contract["profile_revision"]
    content_hash = contract["profile_hash"]
    with psycopg.connect(config.dsn(), connect_timeout=config.connect_timeout()) as conn:
        with conn.transaction():
            available = conn.execute(
                "SELECT to_regclass('public.project_registry')"
            ).fetchone()[0]
            if available is None:
                return "migration_required"
            # A profile with the same revision must never mutate its historical snapshot.
            old = conn.execute(
                "SELECT profile_hash FROM project_profile_versions "
                "WHERE project_id=%s AND revision=%s",
                (record["id"], revision),
            ).fetchone()
            if old is not None and old[0] != content_hash:
                return "revision_conflict"
            conn.execute("""
                INSERT INTO project_registry
                    (id,name,profile_revision,profile_hash,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                    name=EXCLUDED.name,
                    profile_revision=EXCLUDED.profile_revision,
                    profile_hash=EXCLUDED.profile_hash,
                    updated_at=EXCLUDED.updated_at
                WHERE project_registry.profile_revision < EXCLUDED.profile_revision
            """, (
                record["id"], record["name"], revision, content_hash,
                record["created_at"], contract["profile_updated_at"],
            ))
            conn.execute("""
                INSERT INTO project_profile_versions
                    (project_id,revision,schema_version,profile_hash,profile)
                VALUES (%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (project_id,revision) DO NOTHING
            """, (
                record["id"], revision, contract["schema_version"], content_hash,
                __import__("json").dumps(contract, ensure_ascii=False),
            ))
    return "synced"
