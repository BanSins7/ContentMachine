"""Block 3 real PostgreSQL integration, no paid models or network calls."""
import os,sys,unittest,tempfile
from uuid import uuid4
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import projects
from intelligence import migrate
from intelligence.taxonomy_builder.intake import get_intake_view,confirm_intake
from intelligence.taxonomy_builder.catalog import refresh_model_catalog,list_models
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import (
    save_draft,approve_draft,get_pipeline_state,get_project_model_catalog)
from test_taxonomy_builder_pipeline import provider_model,config,PRIMARY,SECOND

PROFILE={"name":"Calories","niche":"Health & Fitness","product_type":"mobile_app",
         "product_functions":"Счётчик калорий по фото",
         "audience_problems":"Трудно считать калории"}

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"CM_TEST_PG_DSN not configured")
class PipelinePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            migrate.apply_migrations(conn)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch=mock.patch.dict(os.environ,{"CM_PROJECTS_DIR":self.temp.name,
             "CM_DB_ENABLED":"1","CM_DB_DSN":self.dsn})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.project=projects.create_project(PROFILE)
        self.other=projects.create_project(PROFILE)
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            refresh_model_catalog(conn,fetcher=lambda:[
                 provider_model(PRIMARY),
                 provider_model(SECOND,temperature=True)])
        self.assertEqual(self.project["profile_db_sync"],"synced")
        self.assertEqual(self.other["profile_db_sync"],"synced")
        self.ids=self._ids()
        self.settings=config(*self.ids)
    def tearDown(self):
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DELETE FROM project_registry WHERE id=ANY(%s)",
                         ([self.project["id"],self.other["id"]],))
    def _ids(self):
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            entries={r["model_id"]:r for r in list_models(conn)}
        self.assertTrue(entries[PRIMARY]["selectable"])
        self.assertTrue(entries[SECOND]["selectable"])
        return entries[PRIMARY]["catalog_version_id"],entries[SECOND]["catalog_version_id"]
    def _confirm(self,pid):
        state=get_intake_view(pid)
        return confirm_intake(pid,state["profile_revision"],state["profile_hash"])
    def test_twelve_owner_candidates_available(self):
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            rows=list_models(conn)
        self.assertEqual(len(rows),12)
        self.assertTrue(any(x["model_id"]==PRIMARY for x in rows))
        self.assertFalse(next(x for x in rows if x["model_id"]=="qwen/qwen3.8-27b")["selectable"])
    def test_confirm_then_draft_then_approve_then_idempotency(self):
        pid=self.project["id"]
        with self.assertRaises(PipelineError):
            save_draft(pid,self.settings,0)
        self._confirm(pid)
        self.assertEqual(get_pipeline_state(pid)["status"],"pipeline_setup")
        saved=save_draft(pid,self.settings,0)
        self.assertEqual(saved["draft_revision"],1)
        self.assertEqual(get_pipeline_state(pid)["status"],"pipeline_review")
        approved=approve_draft(pid,1)
        self.assertEqual(approved["status"],"approved")
        self.assertEqual(approved["next_step"],"cost_estimate")
        again=approve_draft(pid,1)
        self.assertEqual(again["pipeline_id"],approved["pipeline_id"])
        self.assertEqual(get_pipeline_state(pid)["status"],"pipeline_approved")
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            row=conn.execute("""SELECT config,model_snapshots FROM tb_pipeline_versions
                               WHERE id=%s""",(approved["pipeline_id"],)).fetchone()
            self.assertEqual(row[0]["stages"][1]["temperature"],0.5)
            self.assertEqual(row[1]["fact_extraction"]["model_id"],PRIMARY)
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute("UPDATE tb_pipeline_versions SET config='{}'::jsonb WHERE id=%s",
                                 (approved["pipeline_id"],))
    def test_edit_requires_new_approval_and_correct_draft_revision(self):
        pid=self.project["id"]
        self._confirm(pid)
        save_draft(pid,self.settings,0)
        approve_draft(pid,1)
        modified=config(*self.ids)
        modified["stages"][1]["temperature"]=0.8
        with self.assertRaisesRegex(PipelineError,"Draft changed"):
            save_draft(pid,modified,0)
        save_draft(pid,modified,1)
        self.assertEqual(get_pipeline_state(pid)["status"],"pipeline_review")
        version=approve_draft(pid,2)
        self.assertEqual(version["pipeline_version"],2)
    def test_stale_profile_requires_reconfirmation(self):
        pid=self.project["id"]
        self._confirm(pid)
        save_draft(pid,self.settings,0)
        projects.update_project(pid,{"expected_revision":1,
                 "audience_problems":"Изменённая проблема"})
        with self.assertRaises(PipelineError):
            approve_draft(pid,1)
        self._confirm(pid)
        state=get_pipeline_state(pid)
        self.assertEqual(state["status"],"pipeline_setup")
        fresh=save_draft(pid,self.settings,0)
        self.assertEqual(fresh["draft_revision"],1)
    def test_project_boundaries(self):
        pid=self.project["id"]
        self._confirm(pid)
        save_draft(pid,self.settings,0)
        with self.assertRaises(PipelineError):
            approve_draft(self.other["id"],1)
        self._confirm(self.other["id"])
        self.assertEqual(get_pipeline_state(self.other["id"])["status"],"pipeline_setup")
    def test_unverified_model_or_unsupported_temperature_does_not_save(self):
        pid=self.project["id"]
        self._confirm(pid)
        values=config(*self.ids)
        values["stages"][0]["temperature"]=0.9
        with self.assertRaisesRegex(PipelineError,"temperature"):
            save_draft(pid,values,0)
        values=config(*self.ids)
        values["stages"][0]["model_id"]="google/gemini-4-argon"
        with self.assertRaises(PipelineError):
            save_draft(pid,values,0)
        self.assertEqual(get_pipeline_state(pid)["status"],"pipeline_setup")
