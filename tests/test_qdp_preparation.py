"""FIX-10 independent A/B/C group-owned immutable inputs."""
import unittest
from intelligence.qualified_data.contracts import QDPError,sha256
from intelligence.qualified_data.preparation import build_preparations,build_preparation_inputs,VERSION

M={"source_id":"origin","source_policy_version_id":5,
   "metadata_id":6,"discovery_id":7}
P={"content_version_id":8,"content_sha256":"a"*64}
A={"id":9,"sha256":"b"*64,"media_kind":"video","size_bytes":128}
C={"growth_pair_ids":[10,11],"history_sha256":"c"*64,
   "growth_definition":{"name":"views","version":"v1"},
   "growth_reason_code":None}

def build(a=True,b=True,c=True,metrics=None):
    media={"A":{"state":"ready" if a else "blocked",
                "reason_code":None if a else "media_missing"},
           "B":{"state":"ready" if b else "blocked",
                "reason_code":None if b else "media_missing"},
           "asset":A if a or b else None}
    return build_preparations(M,P,media=media,metrics=metrics or C,
                               score_config_id=23 if c else None,
                               scoring_allowed=c)

class GroupPreparationTests(unittest.TestCase):
    def test_all_ready_and_unique_hashes(self):
        rows=build()
        self.assertEqual(set(rows),{"A","B","C"})
        self.assertEqual({g for g in rows if rows[g]["state"]=="ready"},{"A","B","C"})
        for group,p in rows.items():
            self.assertEqual(p["inputs"]["group"],group)
            self.assertEqual(p["inputs"]["result_schema"],VERSION)
            self.assertEqual(p["input_sha256"],sha256(p["inputs"]))
        self.assertNotIn("metric_snapshot_ids",rows["A"]["inputs"])
        self.assertNotIn("media_asset_id",rows["C"]["inputs"])
        self.assertNotIn("local_path",rows["A"]["inputs"])

    def test_only_a(self):
        self.assertEqual([g for g,x in build(b=False,c=False).items()
                          if x["state"]=="ready"],["A"])

    def test_only_b(self):
        self.assertEqual([g for g,x in build(a=False,c=False).items()
                          if x["state"]=="ready"],["B"])

    def test_only_c(self):
        self.assertEqual([g for g,x in build(a=False,b=False).items()
                          if x["state"]=="ready"],["C"])

    def test_none(self):
        self.assertTrue(all(x["state"]=="blocked" for x in build(False,False,False).values()))

    def test_missing_c_policy_never_blocks_a_b(self):
        rows=build(c=False)
        self.assertEqual(rows["C"]["reason_code"],"score_rights_missing")
        self.assertEqual(rows["A"]["state"],"ready")
        self.assertEqual(rows["B"]["state"],"ready")

    def test_missing_c_config_never_blocks_a_b(self):
        data={"A":{"state":"ready","reason_code":None},
              "B":{"state":"ready","reason_code":None},"asset":A}
        out=build_preparations(M,P,media=data,metrics=C,score_config_id=None,
                                scoring_allowed=True)
        self.assertEqual(out["C"]["reason_code"],"score_configuration_missing")
        self.assertEqual(out["A"]["state"],"ready")

    def test_metric_correction_does_not_block_a(self):
        changed=dict(C,growth_reason_code="metric_counter_correction",growth_pair_ids=[])
        self.assertEqual(build(metrics=changed)["C"]["reason_code"],
                         "metric_counter_correction")
        self.assertEqual(build(metrics=changed)["A"]["state"],"ready")

    def test_invalid_source_scope(self):
        for bad in ({"source_policy_version_id":None},
                    {"metadata_id":0},{"discovery_id":None}):
            with self.subTest(bad=bad),self.assertRaises(QDPError):
                build_preparations(dict(M,**bad),P,media={"A":{"state":"blocked","reason_code":"x"},
                   "B":{"state":"blocked","reason_code":"x"},"asset":None},
                   metrics=C,score_config_id=None,scoring_allowed=False)

    def test_invalid_metric_ids_and_hash(self):
        for bad in ({"growth_pair_ids":[10,10]},{"growth_pair_ids":[True,11]},
                    {"history_sha256":"bad"}):
            with self.subTest(bad=bad),self.assertRaises(QDPError):
                build(metrics=dict(C,**bad))

    def test_invalid_media_hash_and_group(self):
        with self.assertRaises(QDPError):
            build_preparation_inputs("D",M,media={},metrics=C,config_id=1,pinned=P)
        media={"A":{"state":"ready","reason_code":None},
               "B":{"state":"ready","reason_code":None},
               "asset":dict(A,sha256="bad")}
        with self.assertRaises(QDPError):
            build_preparations(M,P,media=media,metrics=C,score_config_id=1,
                               scoring_allowed=True)

if __name__ == "__main__":
    unittest.main()
