"""GB-MOD-03 read-only Group B schema inspection; no implicit writes.

StoragePort persistence and worker operations belong to subsequent stages.
Schema presence alone does NOT imply QDP/A/model/Analytics readiness.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errors import DependencyUnavailable

MIGRATION_NAME = "055_group_b_runtime.sql"
LEGACY_NAME = "005_content_features_b.sql"
LEGACY_GIT_BLOB_SHA1 = "e203aab384af65ae11e9960860bee4715bb21e4d"
RUNTIME_TABLES = (
    "group_b_inbox", "group_b_runs", "group_b_block_states",
    "group_b_model_quotes", "group_b_model_approvals", "group_b_model_attempts",
    "group_b_review_events", "group_b_outbox",
)
_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@dataclass(frozen=True, slots=True)
class RuntimeSchemaStatus:
    installed: bool
    missing_tables: tuple[str, ...]
    migration_name: str
    checksum_matches: bool
    legacy_unchanged: bool


def _sha256_migration(name: str) -> str:
    return hashlib.sha256((_MIGRATIONS / name).read_bytes()).hexdigest()


def legacy_migration_unchanged() -> bool:
    data = (_MIGRATIONS / LEGACY_NAME).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    return actual == LEGACY_GIT_BLOB_SHA1


def inspect_runtime_schema(connection) -> RuntimeSchemaStatus:
    """Read-only inspection against the default public schema."""
    legacy_ok = legacy_migration_unchanged()
    row = connection.execute(
        "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
    ).fetchone()
    if row is None or row[0] is not True:
        return RuntimeSchemaStatus(False, RUNTIME_TABLES, MIGRATION_NAME, False, legacy_ok)
    installed_row = connection.execute(
        "SELECT sha256 FROM public.schema_migrations WHERE version=%s",
        (MIGRATION_NAME,)
    ).fetchone()
    table_rows = connection.execute(
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname='public' AND tablename = ANY(%s)",
        (list(RUNTIME_TABLES),)
    ).fetchall()
    present = {r[0] for r in table_rows}
    missing = tuple(t for t in RUNTIME_TABLES if t not in present)
    checksum_ok = installed_row is not None and installed_row[0] == _sha256_migration(MIGRATION_NAME)
    return RuntimeSchemaStatus(
        bool(installed_row) and not missing and checksum_ok and legacy_ok,
        missing, MIGRATION_NAME, bool(checksum_ok), legacy_ok
    )


def require_runtime_schema(connection) -> None:
    if not inspect_runtime_schema(connection).installed:
        raise DependencyUnavailable("storage", "group_b_schema_not_ready")


# GB-MOD-11 optional extension. Do not require worker tables for baseline B
# intake or the original runtime 055 schema.
WORKER_MIGRATION_NAME = "058_group_b_worker_jobs.sql"
WORKER_TABLES = ("group_b_jobs","group_b_job_events")


def require_worker_schema(connection) -> None:
    """Fail closed on absent/tampered stage11 worker schema, no DDL on reads."""
    require_runtime_schema(connection)
    found=connection.execute(
        """SELECT version,sha256 FROM schema_migrations
            WHERE version=%s""",(WORKER_MIGRATION_NAME,)
    ).fetchone()
    if found is None or found[1]!=_sha256_migration(WORKER_MIGRATION_NAME):
        raise DependencyUnavailable("storage","worker_schema_not_ready")
    missing=connection.execute(
        """SELECT r.name FROM unnest(%s::text[]) AS r(name)
            WHERE to_regclass('public.' || r.name) IS NULL""",
        (list(WORKER_TABLES),)
    ).fetchall()
    if missing:
        raise DependencyUnavailable("storage","worker_tables_missing")
