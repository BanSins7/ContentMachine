"""Versioned transactional PostgreSQL migrations. Run: python -m intelligence.migrate."""
from __future__ import annotations
import hashlib
from pathlib import Path
from .config import dsn

MIGRATIONS = Path(__file__).with_name("migrations")
LOCK_ID = 82931674013

def apply_migrations(connection) -> list[str]:
    installed = []
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        for path in sorted(MIGRATIONS.glob("[0-9]*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            row = connection.execute(
                "SELECT sha256 FROM schema_migrations WHERE version = %s", (path.name,)
            ).fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError("Migration modified after deployment: " + path.name)
                continue
            try:
                connection.execute(sql)
            except Exception as exc:
                # Preserve original PostgreSQL error and identify the actual
                # migration file. The outer transaction still rolls back all
                # changes; do not mark this version as applied.
                raise RuntimeError("Migration SQL failed: " + path.name) from exc
            connection.execute(
                "INSERT INTO schema_migrations(version, sha256) VALUES (%s, %s)",
                (path.name, checksum)
            )
            installed.append(path.name)
    return installed

def main() -> None:
    if not dsn():
        raise SystemExit("CM_DB_DSN is not set. Run SETUP_POSTGRES_WINDOWS.bat first.")
    import psycopg
    with psycopg.connect(dsn(), connect_timeout=5) as conn:
        applied = apply_migrations(conn)
        existing = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    print("PostgreSQL schema ready:", ", ".join(x[0] for x in existing))
    if applied:
        print("Applied:", ", ".join(applied))
    else:
        print("No pending migrations.")

if __name__ == "__main__":
    main()
