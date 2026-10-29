"""M41 standalone opt-in CLI, no provider on passive boot, no fake LIVE."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import contextmanager

from intelligence.relevance import cli
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
PUB="22222222-2222-4222-8222-222222222222"
DISABLED={}
WORKER={
    "CM_SRE_ENABLED":"1",
    "CM_SRE_OFFLINE_WORKER":"1",
    "CM_DB_ENABLED":"1",
    "CM_DB_DSN":"postgresql://operator:SECRET@localhost/private",
}
OK={"schema_version":"sre-cli-offline-fixture/v1",
    "project_id":PID,"status":"processed","processed":1,
    "paid_post_calls":0,"live_results_written":0}


class Connection:
    autocommit=True
    def __enter__(self):return self
    def __exit__(self,*a):return False
    @contextmanager
    def transaction(self):yield self
    def execute(self,sql,args=None):
        if sql.startswith("SET TRANSACTION"):return None
        raise AssertionError("unexpected SQL: "+sql)


class Control:
    def __init__(self,directory):
        self.directory=directory;self.claimed=False;self.closed=False
        self.stop_checks=0
    def claim(self,pid):
        self.claimed=True;self.project=pid;return {"pid":42}
    def stop_requested(self,claim):
        self.stop_checks+=1
        return self.stop_checks>=3
    def release(self,claim):
        self.closed=True


class CliTests(unittest.TestCase):
    def call(self,argv,*,env=None,connection=None,runner=None,
             controller=Control,sleep=lambda x:None):
        output=[]
        code=cli.main(argv,environ=DISABLED if env is None else env,
                      connect=(lambda:connection) if connection else None,
                      runner=runner,control_factory=controller,
                      sleep=sleep,emit=output.append)
        self.assertEqual(len(output),1)
        return code,json.loads(output[0])

    def test_module_help_and_self_check_disabled_without_db(self):
        with patch.object(cli,"_connect_explicit",
                          side_effect=AssertionError("no DB")):
            code,body=self.call(["self-check"])
        self.assertEqual(code,2 if body["status"]=="blocked_migrations" else 0)
        self.assertIn(body["status"],("disabled","blocked_migrations"))
        self.assertFalse(body["live_worker_enabled"])
        self.assertNotIn("SECRET",json.dumps(body))
        self.assertEqual(body["paid_post_calls"],0)

    def test_known_corrupt_062_fails_closed_from_source_read(self):
        assert cli._sql_blockers()==["migration_062_known_malformed_check_sql"]
        _,body=self.call(["migration-diagnosis"])
        self.assertEqual(body["status"],"blocked")
        self.assertEqual(body["known_sql_blockers"],
                         ["migration_062_known_malformed_check_sql"])
        self.assertFalse(body["database_installed_confirmed"])

    def test_worker_default_flag_denies_before_any_connect_or_claim(self):
        with patch.object(cli,"_connect_explicit",
                          side_effect=AssertionError("DB reached")):
            for argv in (
                ["worker-once","--project",PID],
                ["worker-once","--project",PID,"--allow-offline-fixture"],
                ["worker-loop","--project",PID,"--control-dir","/fake",
                 "--allow-offline-fixture"],
            ):
                with self.subTest(argv=argv):
                    code,body=self.call(argv)
                    self.assertEqual(code,2)
                    self.assertEqual(body["reason_code"],"feature_disabled")

    def test_explicit_fixture_worker_one_scoped_and_no_provider(self):
        db=Connection();recorded=[]
        def runner(conn,pid,*,lease_seconds):
            recorded.append((conn,pid,lease_seconds))
            return dict(OK)
        with patch.object(cli,"_db_preflight",return_value=True):
            code,body=self.call(["worker-once","--project",PID,
                                 "--allow-offline-fixture","--lease-seconds","12"],
                                env=WORKER,connection=db,runner=runner)
        self.assertEqual(code,0)
        self.assertEqual(recorded,[(db,PID,12)])
        self.assertEqual(body["execution_mode"],"offline_fixture")
        self.assertEqual(body["notice"],cli.M41_FIXTURE_NOTICE)
        self.assertEqual(body["paid_post_calls"],0)
        self.assertNotIn("SECRET",json.dumps(body))

    def test_scoped_runner_returning_paid_receipt_is_rejected(self):
        with self.assertRaises(SREContractError):
            cli._work_once(Connection(),PID,60,runner=lambda *a,**k:{
                **OK,"paid_post_calls":1})
        with self.assertRaises(SREContractError):
            cli._work_once(Connection(),PID,60,runner=lambda *a,**k:{
                **OK,"project_id":"b"*32})

    def test_loop_stop_and_max_cycles_cooperative_release(self):
        ctrl=[]
        def factory(directory):
            s=Control(directory);ctrl.append(s);return s
        with patch.object(cli,"_db_preflight",return_value=True):
            code,body=self.call(["worker-loop","--project",PID,
                   "--control-dir","/private","--allow-offline-fixture",
                   "--max-cycles","10"],env=WORKER,connection=Connection(),
                runner=lambda *a,**k:dict(OK),
                controller=factory)
        self.assertEqual(code,0)
        self.assertEqual(body["status"],"operator_stop_requested")
        self.assertEqual(body["cycles"],2)
        self.assertEqual(body["processed"],2)
        self.assertTrue(ctrl[0].closed)
        self.assertEqual(body["paid_post_calls"],0)

    def test_loop_release_even_on_domain_failure(self):
        ctrl=[]
        def factory(directory):
            ob=Control(directory);ctrl.append(ob);return ob
        with patch.object(cli,"_db_preflight",return_value=True):
            code,body=self.call(["worker-loop","--project",PID,
                    "--control-dir","/private","--allow-offline-fixture",
                    "--max-cycles","1"],env=WORKER,connection=Connection(),
                runner=lambda *a,**k:(_ for _ in ()).throw(
                    RuntimeError("postgresql://SECRET")),
                controller=factory)
        self.assertEqual(code,3)
        self.assertEqual(body["reason_code"],"operation_failed")
        self.assertTrue(ctrl[0].closed)
        self.assertNotIn("SECRET",json.dumps(body))

    def test_invalid_project_and_lease_rejected_before_database(self):
        for project,seconds in (("invalid",60),(PID,0),(PID,3601)):
            with self.subTest(project=project,seconds=seconds):
                code,_=self.call(["worker-once","--project",project,
                    "--allow-offline-fixture","--lease-seconds",str(seconds)],
                    env=WORKER)
                self.assertEqual(code,2)

    def test_csv_only_explicit_ascending_d1_bigint(self):
        self.assertEqual(cli._csv("1,2,3"),[1,2,3])
        for bad in ("", "2,1","1,1","0","0001",
                    "1;2","1, 2","9223372036854775808",
                    "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,"
                    "21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,"
                    "38,39,40,41,42,43,44,45,46,47,48,49,50,51"):
            with self.subTest(bad=bad):
                with self.assertRaises(SREContractError):
                    cli._csv(bad)

    def test_preview_current_c9_d1_and_read_only(self):
        db=Connection()
        with patch.object(cli,"_db_preflight",return_value=True):
            with patch("intelligence.relevance.dataset_preview.read_dataset_preview",
                       return_value={"schema_version":"sre-dataset-preview/v1",
                                     "execution_allowed":False}) as original:
                code,body=self.call(["preview","--project",PID,
                    "--publication",PUB,"--content-ids","1,2"],
                    env=WORKER,connection=db)
                original.assert_called_once_with(db,PID,PUB,[1,2])
        self.assertEqual(code,0)
        self.assertFalse(body["execution_allowed"])
        self.assertEqual(body["paid_post_calls"],0)

    def test_migration_diag_no_db_when_not_explicit(self):
        with patch.object(cli,"_connect_explicit",
                          side_effect=AssertionError("not passive")):
            _,body=self.call(["migration-diagnosis"])
        self.assertEqual(body["command"],"migration-diagnosis")
        self.assertIn("known_sql_blockers",body)


if __name__=="__main__":
    unittest.main()
