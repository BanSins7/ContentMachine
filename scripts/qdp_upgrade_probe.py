#!/usr/bin/env python3
"""FIX-18 QA-112: seeded main -> QDP upgrade probe, disposable PG17 only.

Two separate invocations:
  seed   : after migrating pinned main, insert immutable pre-upgrade fixture.
  verify : after QDP migrations, verify exact fixture and idempotent reapply.
Neither mode may run without explicit dual opt-in and a test DSN.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED_PROJECT_ID = "a" * 32
SEED_HASH = "b" * 64
SEED_NAME = "qdp-fix18-upgrade-preservation"


def run(mode: str) -> None:
    if (os.getenv("QDP_DISPOSABLE_TEST_DB") != "YES" or
            os.getenv("QDP_UPGRADE_FIX18") != "YES" or
            not os.getenv("CM_TEST_PG_DSN")):
        raise RuntimeError("requires explicit disposable PG17 QA opt-in and CM_TEST_PG_DSN")
    import psycopg
    with psycopg.connect(os.environ["CM_TEST_PG_DSN"], connect_timeout=5) as db:
        version = int(db.execute("SHOW server_version_num").fetchone()[0])
        if not 170000 <= version < 180000:
            raise RuntimeError("upgrade acceptance requires PostgreSQL 17")
        if mode == "seed":
            db.execute(
                """INSERT INTO project_registry
                   (id,name,profile_revision,profile_hash,created_at,updated_at)
                   VALUES (%s,%s,1,%s,now(),now())""",
                (SEED_PROJECT_ID, SEED_NAME, SEED_HASH))
            db.commit()
            print("QDP_UPGRADE_SEED=OK")
            return
        row = db.execute(
            "SELECT name,profile_revision,profile_hash FROM project_registry WHERE id=%s",
            (SEED_PROJECT_ID,)).fetchone()
        if row != (SEED_NAME, 1, SEED_HASH):
            raise RuntimeError("pre-upgrade main data was lost or changed")
        from intelligence.migrate import apply_migrations
        if apply_migrations(db) != []:
            raise RuntimeError("repeated QDP migrations changed installed schema")
        from intelligence.qualified_data.schema import require_qdp_schema
        report = require_qdp_schema(db)
        if not report.get("required"):
            raise RuntimeError("QDP schema not present after main upgrade")
        row2 = db.execute(
            "SELECT name,profile_revision,profile_hash FROM project_registry WHERE id=%s",
            (SEED_PROJECT_ID,)).fetchone()
        if row2 != row:
            raise RuntimeError("migration replay modified main fixture")
        print("QDP_UPGRADE_MAIN_TO_FEATURE=PASS (seed retained; repeated migrations noop)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("seed", "verify"))
    args = p.parse_args()
    run(args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
