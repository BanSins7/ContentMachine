"""FIX-10-D static + fake catalog PG17 migration proof, no live database."""
from __future__ import annotations
from contextlib import contextmanager
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path
import hashlib
import json
import re
import unittest

from intelligence.taxonomy_builder import migration_audit as audit


class RowCursor:
    def __init__(self, rows):
        self.rows=rows
    def fetchone(self):
        return self.rows[0]
    def fetchall(self):
        return list(self.rows)


class FakeReadOnlyCatalog:
    def __init__(self, *, wrong_version=False, missing_guard=None,
                 changed_migration=None, missing_table=None):
        source=audit.inspect_files()
        self.installed={**source["migration_sha256"],
                        **{name:"a"*64 for name in audit.PREDECESSORS}}
        if changed_migration:
            self.installed[changed_migration]="0"*64
        self.tables=set()
        self.guards=set()
        for filename,(tables,triggers) in audit.SPECS.items():
            self.tables.update(tables)
            sql=(audit.MIGRATIONS/filename).read_text(encoding="utf-8")
            # Extract security guard ownership directly from migration SQL.
            owned={
                trig:table for trig,table in re.findall(
                    r"CREATE\s+TRIGGER\s+(\w+)\s+.*?\bON\s+(\w+)\b",
                    sql,re.I|re.S)
            }
            for trig in triggers:
                self.guards.add((owned[trig],trig))
        if missing_guard:
            self.guards={x for x in self.guards if x[1]!=missing_guard}
        if missing_table:
            self.tables.discard(missing_table)
        self.version="160009" if wrong_version else "170006"
        self.executed=[]
    @contextmanager
    def transaction(self):
        yield self
    def execute(self,query):
        self.executed.append(query)
        if query=="SET TRANSACTION READ ONLY":
            return RowCursor([])
        if query=="SHOW server_version_num":
            return RowCursor([(self.version,)])
        if query.startswith("SELECT version,sha256 FROM schema_migrations"):
            return RowCursor(sorted(self.installed.items()))
        if "FROM pg_class c JOIN pg_namespace n" in query:
            return RowCursor([(x,) for x in sorted(self.tables)])
        if "FROM pg_trigger t" in query:
            return RowCursor(sorted(self.guards))
        raise AssertionError("Unexpected mutable/unknown statement "+query)


class RTBDeepV2MigrationAuditTest(unittest.TestCase):
    def test_current_six_migrations_are_additive_and_owned(self):
        report=audit.inspect_files()
        self.assertTrue(report["static_valid"],report["static_issues"])
        self.assertEqual(len(report["migration_files"]),6)
        self.assertEqual(len(report["migration_sha256"]),6)
        self.assertGreater(report["expected_trigger_count"],20)
        self.assertGreater(report["expected_table_count"],10)
        self.assertFalse(report["postgres_checked"])

    def test_tampered_migration_removes_required_security_guard(self):
        name="054_tb_deep_v2_research_binding.sql"
        original=(audit.MIGRATIONS/name).read_text(encoding="utf-8")
        changed=original.replace(
            "CREATE TRIGGER tb_deep_v2_binding_immutable",
            "-- CREATE TRIGGER tb_deep_v2_binding_immutable")
        report=audit.inspect_files(override={name:changed})
        self.assertFalse(report["static_valid"])
        self.assertIn("missing_triggers:"+name,report["static_issues"])

    def test_new_foreign_table_creation_and_drop_table_rejected(self):
        name="059_tb_deep_v2_publication_sre.sql"
        original=(audit.MIGRATIONS/name).read_text(encoding="utf-8")
        report=audit.inspect_files(override={
            name: original+"\nCREATE TABLE tb_runs_bad(x int); DROP TABLE tb_runs;\n"})
        self.assertIn("foreign_table_creation:"+name,report["static_issues"])
        self.assertIn("destructive_schema_change:"+name,report["static_issues"])

    def test_read_only_postgres_verifies_all_shas_tables_and_enabled_triggers(self):
        fake=FakeReadOnlyCatalog()
        report=audit.verify_postgres(fake)
        self.assertTrue(report["postgres_valid"],report["postgres_issues"])
        self.assertTrue(report["postgres_checked"])
        self.assertTrue(all(x=="SET TRANSACTION READ ONLY" or
            x=="SHOW server_version_num" or x.lstrip().startswith("SELECT")
            for x in fake.executed))

    def test_postgres_rejects_disabled_security_trigger_or_hash_drift(self):
        name="059_tb_deep_v2_publication_sre.sql"
        for fake in (
            FakeReadOnlyCatalog(missing_guard="tb_deep_v2_sre_link_immutable"),
            FakeReadOnlyCatalog(changed_migration=name),
            FakeReadOnlyCatalog(missing_table="tb_deep_v2_publications"),
            FakeReadOnlyCatalog(wrong_version=True),
        ):
            with self.subTest(case=fake.__dict__):
                report=audit.verify_postgres(fake)
                self.assertFalse(report["postgres_valid"])
                self.assertTrue(report["postgres_issues"])

    def test_security_trigger_bound_to_wrong_sibling_table_is_rejected(self):
        # A trigger with a valid name attached to a DIFFERENT RTB table
        # cannot masquerade as protection for the original SQL target.
        fake=FakeReadOnlyCatalog()
        target=("tb_deep_v2_sre_links","tb_deep_v2_sre_link_immutable")
        self.assertIn(target,fake.guards)
        fake.guards.remove(target)
        fake.guards.add(("tb_deep_v2_publications",target[1]))
        report=audit.verify_postgres(fake)
        self.assertFalse(report["postgres_valid"])
        self.assertTrue(any(
            item.startswith("missing_disabled_or_misbound_guard:"+target[1])
            for item in report["postgres_issues"]
        ))

    def test_declared_trigger_on_foreign_table_fails_static_source_check(self):
        name="059_tb_deep_v2_publication_sre.sql"
        sql=(audit.MIGRATIONS/name).read_text(encoding="utf-8")
        before="CREATE TRIGGER tb_deep_v2_sre_link_immutable"
        self.assertIn(before,sql)
        # The recorded name exists, but this SQL source now changes the
        # trigger's ON target, even though the table itself is not created.
        changed=sql.replace(
            "ON tb_deep_v2_sre_links\n",
            "ON knowledge_approved_snapshots\n",1,
        )
        self.assertNotEqual(changed,sql)
        report=audit.inspect_files(override={name:changed})
        self.assertFalse(report["static_valid"])
        self.assertIn("trigger_on_foreign_table:"+name,report["static_issues"])

    def test_cli_only_checks_sources_no_database(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(audit.main(["--check"]),0)

if __name__=="__main__":
    unittest.main()
