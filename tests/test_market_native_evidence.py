"""QDP-free Marketing media provenance and block reference verification."""
import copy
import unittest
from intelligence.market_intelligence.native_evidence import make_receipt,verify_receipt

PID="b"*32
SHA="a"*64
FREF="ga-frame:f001-"+SHA

def _fixture():
    blocks={"B"+str(n):{"state":"unavailable","evidence_frame_refs":[]}
            for n in range(1,9)}
    blocks["B1"]["evidence_frame_refs"]=[FREF]
    return {
        "project_id":PID,"video_id":17,"source_id":"source-id",
        "source_version_id":"version-id","media_asset_id":"asset-id",
        "media_sha256":SHA,"duration_sec":"5.000",
        "visual":{"source_media_sha256":SHA,
                  "frames":[{"ref":FREF,"png_sha256":SHA,
                             "observed_sec":"0.125"}],
                  "speech":{"observation_sha256":None}},
        "group_b":{"blocks":blocks},
        "method":"mi-abc-expanded-media/v2",
    }

class NativeEvidenceTests(unittest.TestCase):
    def test_receipt_has_source_sha_and_no_qdp_contract(self):
        receipt=make_receipt(**_fixture())
        self.assertEqual(receipt["video_id"],17)
        self.assertTrue(verify_receipt(receipt,media_sha256=SHA))
        self.assertNotIn("qdp_run_id",receipt)
        self.assertFalse(receipt["full_group_b_certified"])

    def test_tampered_sha_or_ref_rejected(self):
        proof=make_receipt(**_fixture())
        bad=copy.deepcopy(proof)
        bad["frames"][0]["observed_sec"]="2.000"
        self.assertFalse(verify_receipt(bad))
        bad2=_fixture()
        bad2["group_b"]["blocks"]["B1"]["evidence_frame_refs"]=["invented"]
        with self.assertRaises(ValueError):
            make_receipt(**bad2)

    def test_frame_proof_must_match_hash_and_video_duration(self):
        invalid=_fixture()
        invalid["visual"]["frames"][0]["observed_sec"]="6.0"
        with self.assertRaises(ValueError):
            make_receipt(**invalid)
        invalid=_fixture()
        invalid["visual"]["frames"][0]["png_sha256"]="c"*64
        with self.assertRaises(ValueError):
            make_receipt(**invalid)

    def test_receipt_is_deterministic(self):
        left=make_receipt(**_fixture())
        right=make_receipt(**_fixture())
        self.assertEqual(left["receipt_sha256"],right["receipt_sha256"])


if __name__=="__main__":
    unittest.main()
