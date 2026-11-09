"""Offline regression: stuck optional Bright Data batches cannot block Haiku.

Tests never invoke a paid provider. Recovery uses only local SQLite snapshot.
"""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from intelligence.market_intelligence import carousel_studio as st

PROFILE="https://www.tiktok.com/@example"
NATIVE="7660142485791968544"
URL=PROFILE+"/photo/"+NATIVE
RUN="f"*32

class TestSlowMetrics(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        mock=patch.object(st,"ROOT",self.root)
        mock.start()
        self.addCleanup(mock.stop)

    def test_wait_timeout_only_applies_to_optional_metadata(self):
        # Simulated monotonic clock: no sleep, no network.
        with patch.object(st.bd,"progress",return_value="running"), patch.object(
            st.time,"monotonic",side_effect=[0,301]), patch.object(
            st.time,"sleep",side_effect=AssertionError("should time out")):
            with patch.object(st,"_event"):
                with self.assertRaisesRegex(TimeoutError,"brightdata_detail"):
                    st._wait("sd_dummy","f"*32,"Метрики",max_seconds=240)
        self.assertEqual(st.DETAIL_METRICS_WAIT_SECONDS,240)

    def test_first_stuck_batch_does_not_create_second_paid_batch(self):
        links=[PROFILE+"/photo/"+str(7000000000000000000+i) for i in range(100)]
        with patch.object(st.bd,"trigger",return_value="sd_test12345678901234") as trigger, patch.object(
            st,"_wait",side_effect=TimeoutError("brightdata_detail_metrics_wait_limit")) as wait, patch.object(
            st.bd,"download",side_effect=AssertionError("no ready data")) as download, patch.object(
            st,"_event") as events:
            result=st._trigger_metrics(RUN,links)
        self.assertEqual(result,{})
        trigger.assert_called_once()
        wait.assert_called_once_with("sd_test12345678901234",RUN,"Метрики",
                                     max_seconds=240)
        download.assert_not_called()
        self.assertTrue(any("сохранёнными метриками профиля" in args[1]
                            for args,_ in (x for x in
                              ((call.args,call.kwargs) for call in events.call_args_list))))

    def test_recovery_from_stuck_metadata_keeps_original_profile_counters(self):
        folder=self.root/RUN
        folder.mkdir()
        counts={"views":901,"likes":90,"comments":5,"shares":3,"saves":7}
        with st.db() as con:
            con.execute("""INSERT INTO studio_runs
                (id,source_kind,source_input,state,phase,progress,total,snapshot_id,
                 created_at,updated_at,output_dir)
                VALUES(?,'profile',?,'interrupted','Прервано',17,1,?,?,?,?)""",
                (RUN,PROFILE,"snapshot-original","2026-11-03T16:03:31+00:00",
                 st.utc(),str(folder)))
            con.execute("""INSERT INTO studio_targets
                (run_id,native_id,ordinal,canonical_url) VALUES(?,?,0,?)""",
                (RUN,NATIVE,URL))
            con.execute("""INSERT INTO studio_items
                (run_id,native_id,canonical_url,state)
                VALUES(?,?,?,'queued')""",(RUN,NATIVE,URL))
            con.execute("""INSERT INTO studio_channel_posts
                (run_id,native_id,kind,canonical_url,counts_json)
                VALUES(?,?,'photo_carousel',?,?)""",
                (RUN,NATIVE,URL,json.dumps({"observed_counters":counts})))
        # Keep the job at the downloaded stage without calling providers.
        def fail_after_recovery(url,path):
            raise RuntimeError("test_stopped_before_model")
        with patch.object(st,"_discover",side_effect=AssertionError("no profile trigger")), patch.object(
            st,"_trigger_metrics",side_effect=AssertionError("no post metrics trigger")), patch.object(
            st,"download_carousel",side_effect=fail_after_recovery), patch.object(
            st,"_analyze",side_effect=AssertionError("no LLM")):
            st._process(RUN,resume=True)
        row=st.get_run(RUN)["items"][0]
        self.assertEqual(row["state"],"failed")
        with st.db() as con:
            record=con.execute("SELECT metrics_json FROM studio_items WHERE run_id=?",(RUN,)).fetchone()
        data=json.loads(record["metrics_json"])
        self.assertEqual(data["observed_counters"],counts)
        self.assertEqual(data["source_record"],"profile_snapshot")
        self.assertEqual(data["provider_snapshot_id"],"snapshot-original")
        self.assertEqual(data["collected_at_utc"],"2026-11-03T16:03:31+00:00")
        self.assertEqual(data["derived_public_ratios"]["engagement_per_view_pct"],
                         round(100*sum([90,5,3,7])/901,5))

if __name__=="__main__":
    unittest.main()
