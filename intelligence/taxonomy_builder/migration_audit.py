"""RTB FIX-10-D: offline/PG17 read-only audit of owned migrations 054..059.

--check uses only Python stdlib. --postgres requires a disposable test
database: verifies schema_migrations checksums and ENABLED security triggers.
It never makes writes, runs migrations, or dispatches paid OpenRouter calls.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from intelligence.migrate import MIGRATIONS

SPECS={
"054_tb_deep_v2_research_binding.sql":(
 ("tb_deep_v2_research_bindings",),
 ("tb_deep_v2_binding_source_guard","tb_deep_v2_binding_immutable")),
"055_tb_deep_v2_budget_ledger.sql":(
 ("tb_deep_v2_quotes","tb_deep_v2_consents","tb_deep_v2_revocations","tb_deep_v2_runs","tb_deep_v2_attempts"),
 ("tb_deep_v2_quote_guard","tb_deep_v2_quote_immutable",
  "tb_deep_v2_consent_guard","tb_deep_v2_consent_immutable",
  "tb_deep_v2_revocation_immutable","tb_deep_v2_run_insert_guard",
  "tb_deep_v2_attempt_insert_guard","tb_deep_v2_run_update_guard",
  "tb_deep_v2_run_no_delete","tb_deep_v2_attempt_update_guard","tb_deep_v2_attempt_no_delete")),
"056_tb_deep_v2_human_reviews.sql":(
 ("tb_deep_v2_reviews",),
 ("tb_deep_v2_review_insert_guard","tb_deep_v2_review_immutable")),
"057_tb_deep_v2_expansion_consent.sql":(
 ("tb_deep_v2_expansion_quotes","tb_deep_v2_expansion_consents","tb_deep_v2_expansion_revocations"),
 ("tb_deep_v2_expansion_quote_check","tb_deep_v2_expansion_consent_check",
  "tb_deep_v2_expansion_grant_after_insert",
  "tb_deep_v2_expansion_quote_immutable","tb_deep_v2_expansion_consent_immutable",
  "tb_deep_v2_expansion_revocation_immutable")),
"058_tb_deep_v2_search_readiness.sql":(
 ("tb_deep_v2_readiness_reports",),
 ("tb_deep_v2_readiness_insert_guard","tb_deep_v2_readiness_immutable")),
"059_tb_deep_v2_publication_sre.sql":(
 ("tb_deep_v2_publications","tb_deep_v2_sre_links"),
 ("tb_deep_v2_publication_insert_guard","tb_deep_v2_sre_link_insert_guard",
  "tb_deep_v2_publication_immutable","tb_deep_v2_sre_link_immutable"))
}
PREDECESSORS=("052_ke_knowledge_snapshot.sql","053_ke_c_qualified_execution.sql")
TABLES=re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",re.I)
TRIGGERS=re.compile(r"\bCREATE\s+TRIGGER\s+(\w+)",re.I)
DROP=re.compile(r"\b(?:DROP|TRUNCATE)\s+TABLE\b",re.I)
ALTER=re.compile(r"\bALTER\s+TABLE\s+(\w+)",re.I)

def _no_comments(sql):
    sql=re.sub(r"/\*.*?\*/","",sql,flags=re.S)
    return re.sub(r"--[^\n]*","",sql)

def inspect_files(*,root=MIGRATIONS,override=None):
    issues=[]
    if list(SPECS)!=sorted(SPECS):
        issues.append("migration_sequence_unsorted")
    if override is None:
        for name in PREDECESSORS:
            if not (root/name).is_file():
                issues.append("missing_predecessor:"+name)
    hashes={}
    for name,(tables,triggers) in SPECS.items():
        path=root/name
        if override is not None and name in override:
            sql=override[name]
        elif path.is_file():
            sql=path.read_text(encoding="utf-8")
        else:
            issues.append("missing_migration:"+name)
            continue
        if not isinstance(sql,str) or not sql.strip():
            issues.append("empty_migration:"+name)
            continue
        hashes[name]=hashlib.sha256(sql.encode("utf-8")).hexdigest()
        statement=_no_comments(sql)
        definitions=set(TABLES.findall(statement))
        guard_defs=set(TRIGGERS.findall(statement))
        if not set(tables).issubset(definitions):
            issues.append("missing_tables:"+name)
        if not set(triggers).issubset(guard_defs):
            issues.append("missing_triggers:"+name)
        if any(not t.startswith("tb_deep_v2_") for t in definitions):
            issues.append("foreign_table_creation:"+name)
        if DROP.search(statement):
            issues.append("destructive_schema_change:"+name)
        if any(not t.startswith("tb_deep_v2_") for t in ALTER.findall(statement)):
            issues.append("foreign_table_alter:"+name)
        if re.search(r"\bREFERENCES\s+(?:tb_runs|tb_deep_runs)\s*\(",statement,re.I):
            issues.append("legacy_run_foreign_key:"+name)
        if not re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b",statement,re.I):
            issues.append("missing_guard_function:"+name)
    tables=[t for v in SPECS.values() for t in v[0]]
    triggers=[t for v in SPECS.values() for t in v[1]]
    if len(set(tables))!=len(tables):
        issues.append("duplicate_table_ownership")
    if len(set(triggers))!=len(triggers):
        issues.append("duplicate_trigger_ownership")
    return {
        "schema_version":"rtb-deep-v2-migration-audit/v1",
        "migration_files":list(SPECS),
        "expected_table_count":len(tables),
        "expected_trigger_count":len(triggers),
        "migration_sha256":hashes,
        "static_issues":issues,
        "static_valid":not issues,
        "postgres_checked":False,
        "postgres_valid":False,
    }

def verify_postgres(conn,*,root=MIGRATIONS):
    """Verify installed hashes AND enabled per-table trigger ownership, read-only."""
    report=inspect_files(root=root)
    issues=list(report["static_issues"])
    if issues:
        report["postgres_issues"]=issues
        return report
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        version=int(conn.execute("SHOW server_version_num").fetchone()[0])
        if version//10000!=17:
            issues.append("wrong_postgres_version:"+str(version))
        installed=dict(conn.execute(
            "SELECT version,sha256 FROM schema_migrations").fetchall())
        for name,sha in report["migration_sha256"].items():
            if installed.get(name)!=sha:
                issues.append("migration_sha_mismatch:"+name)
        if any(name not in installed for name in PREDECESSORS):
            issues.append("missing_b9_or_flat_predecessor")
        tables=set(row[0] for row in conn.execute(
            """SELECT c.relname FROM pg_class c JOIN pg_namespace n
               ON n.oid=c.relnamespace
               WHERE n.nspname=current_schema()
               AND c.relkind IN ('r','p') AND c.relname LIKE 'tb_deep_v2_%'"""
        ).fetchall())
        # Pair the trigger with its table: another table cannot spoof a guard.
        guards=set(tuple(row) for row in conn.execute(
            """SELECT c.relname,t.tgname FROM pg_trigger t
               JOIN pg_class c ON c.oid=t.tgrelid
               JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=current_schema()
                 AND t.tgisinternal=false AND t.tgenabled IN ('O','A')
                 AND c.relname LIKE 'tb_deep_v2_%'"""
        ).fetchall())
        for name,(expected_tables,expected_triggers) in SPECS.items():
            for tab in expected_tables:
                if tab not in tables:
                    issues.append("missing_db_table:"+tab)
            for trig in expected_triggers:
                if not any((tab,trig) in guards for tab in expected_tables):
                    issues.append("missing_or_disabled_guard:"+trig)
    report["postgres_checked"]=True
    report["postgres_issues"]=issues
    report["postgres_valid"]=not issues
    return report

def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("--check",action="store_true")
    ap.add_argument("--postgres",action="store_true")
    ap.add_argument("--json-out",type=Path)
    args=ap.parse_args(argv)
    if args.postgres:
        if not os.environ.get("CM_TEST_PG_DSN"):
            raise SystemExit("CM_TEST_PG_DSN required: test DB only, never production")
        import psycopg
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"]) as conn:
            report=verify_postgres(conn)
    else:
        report=inspect_files()
    if args.json_out:
        args.json_out.parent.mkdir(parents=True,exist_ok=True)
        args.json_out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",
                                 encoding="utf-8")
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if (report["postgres_valid"] if args.postgres else
                 report["static_valid"]) else 1

if __name__=="__main__":
    raise SystemExit(main())
