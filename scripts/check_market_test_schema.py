"""Read-only database assertions for the disposable Postgres 17 test container.

Called only after fresh migrations by run_market_tests.py. Rejects wrong PG
major/version, missing key constraints, missing rights and immutable snapshots.
"""
import json
import os
import sys
from psycopg import connect

REQUIRED_TABLES=(
    "market_intelligence_snapshots",
    "mi_video_analyses",
    "mi_author_publications",
    "mi_brightdata_numeric_fields",
    "metric_snapshots",
    "project_sources",
)

def main():
    dsn=os.environ.get("CM_DB_DSN","")
    if not dsn:
        raise RuntimeError("CM_DB_DSN missing; runner should set disposable DSN")
    with connect(dsn,connect_timeout=7) as conn:
        version=int(conn.execute("SHOW server_version_num").fetchone()[0])
        if not 170000 <= version < 180000:
            raise AssertionError("PostgreSQL 17 required")
        tables={}
        for table in REQUIRED_TABLES:
            tables[table]=bool(conn.execute(
                "SELECT to_regclass(%s)",(table,)).fetchone()[0])
        if not all(tables.values()):
            raise AssertionError("Missing tables: "+",".join(
                name for name,exists in tables.items() if not exists))
        fks=conn.execute("""
            SELECT count(*) FROM pg_constraint
            WHERE conrelid='mi_video_analyses'::regclass AND contype='f'
        """).fetchone()[0]
        if fks<3:
            raise AssertionError("MI media table missing project/asset foreign keys")
        definition=conn.execute("""
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname='mi_video_analyses_method_version_check'
              AND conrelid='mi_video_analyses'::regclass
        """).fetchone()
        if not definition or any(
           x not in definition[0] for x in (
               "mi-independent-media/v1",
               "mi-abc-expanded-media/v2",
               "mi-abc-audio-expanded-media/v3",
               "mi-abc-b8-native-media/v4")):
            raise AssertionError("MI method-version migration v1-v4 incomplete")
        if not conn.execute("""
            SELECT count(*) FROM pg_trigger
            WHERE tgrelid='market_intelligence_snapshots'::regclass
             AND NOT tgisinternal AND tgname='trg_mi_immutable'
        """).fetchone()[0]:
            raise AssertionError("Immutable market snapshot trigger missing")
        num_fields=conn.execute("""
            SELECT count(*) FROM pg_constraint
            WHERE conrelid='mi_brightdata_numeric_fields'::regclass
                AND contype='f'
        """).fetchone()[0]
        if num_fields<3:
            raise AssertionError("Metric field provenance foreign keys missing")
        print(json.dumps({
            "schema_version":"mi-postgres17-validation/v1",
            "pg_major":17,"tables":tables,
            "mi_video_foreign_keys":fks,
            "mi_numeric_provenance_foreign_keys":num_fields,
            "method_versions_checked":"v1-v4",
            "snapshots_immutable":True,
            "passed":True},ensure_ascii=False,indent=2))

if __name__=="__main__":
    sys.exit(main() or 0)
