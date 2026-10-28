"""M42 genuine M20->M27 fake->M21 evidence->shadow journal regression.

No actual provider, authenticated bill, C9/D1 SQL reader, paid M28 receipt,
or legitimate M36 LIVE result. M20 source fixtures originate in existing
M23 tests; this suite tests real protocol logic without synthetic privilege.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
import socket
import unittest
from unittest.mock import patch

from intelligence.relevance.m42_fake_opus_shadow import verify_and_journal_fake_opus
from intelligence.relevance.m42_fixture_isolation import M42FixtureBlocked, PROVENANCE
from intelligence.relevance.v1_contracts import SREContractError

from test_sre_m42_fixture_isolation import FakePG, BASE_ENV
from test_sre_opus_transport import M27OpusTransport, RUN
from test_sre_opus_evidence_verifier import make_reply


class M42RealFakeProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        M27OpusTransport.setUpClass()
        cls.plan = M27OpusTransport.plan

    def setUp(self):
        self.conn = FakePG()
        self.envelopes = [
            M27OpusTransport().envelope(i)
            for i in range(self.plan["request_count"])
        ]
        self.scripts = {
            r["request_sha256"]: json.dumps(
                make_reply(r, "uncertain" if i == 2 else "match",
                           evidence=i != 2), ensure_ascii=False,
            )
            for i, r in enumerate(self.plan["requests"])
        }

    def execute(self, *, run_id=RUN, plan=None, envelopes=None, scripts=None,
                flags=BASE_ENV):
        with patch.dict(os.environ, flags, clear=True):
            return verify_and_journal_fake_opus(
                self.conn, run_id,
                self.plan if plan is None else plan,
                self.envelopes if envelopes is None else envelopes,
                self.scripts if scripts is None else scripts,
            )

    def test_three_leaf_complete_fake_evidence_and_atomic_shadow_replay(self):
        with patch.object(socket.socket, "connect",
                          side_effect=AssertionError("NETWORK CALL")):
            first = self.execute()
            replay = self.execute()
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["validated_categories"], 3)
        self.assertEqual(first["validated_requests"], 3)
        self.assertEqual(first["verification_sha256"], replay["verification_sha256"])
        self.assertEqual(first["provenance"], PROVENANCE)
        self.assertFalse(first["provider_receipts_authenticated"])
        self.assertFalse(first["financial_settlement_authorized"])
        self.assertFalse(first["exportable_to_live_consumers"])
        self.assertTrue(first["decisions_are_untrusted"])
        self.assertEqual(first["paid_calls"], 0)
        self.assertEqual(len(self.conn.rows), 2)
        self.assertEqual({k[2] for k in self.conn.rows}, {
            "fake_opus_response", "simulated_verified_pairs",
        })
        self.assertTrue(all(
            v[0]["exportable_to_live_consumers"] is False
            and v[0]["provider_authenticated"] is False
            for v in self.conn.rows.values()
        ))

    def test_missing_script_refuses_entire_run_before_sql(self):
        scripts = dict(self.scripts)
        scripts.pop(next(iter(scripts)))
        with self.assertRaises(M42FixtureBlocked):
            self.execute(scripts=scripts)
        self.assertEqual(self.conn.transaction_count, 0)

    def test_wrong_envelope_identity_fails_before_mutation(self):
        bad = deepcopy(self.envelopes)
        bad[1]["project_id"] = "f" * 32
        with self.assertRaises(M42FixtureBlocked):
            self.execute(envelopes=bad)
        self.assertEqual(self.conn.transaction_count, 0)

    def test_fake_reply_bad_evidence_fails_before_mutation(self):
        altered = dict(self.scripts)
        key = next(iter(altered))
        altered[key] = "{}"
        with self.assertRaises(SREContractError):
            self.execute(scripts=altered)
        self.assertEqual(self.conn.transaction_count, 0)

    def test_conflicting_source_script_replay_cannot_change_run(self):
        self.execute()
        altered = dict(self.scripts)
        key = next(iter(altered))
        obj = json.loads(altered[key])
        obj["decision"] = "uncertain"
        obj["evidence"] = []
        altered[key] = json.dumps(obj, ensure_ascii=False)
        with self.assertRaisesRegex(M42FixtureBlocked, "replay mismatch"):
            self.execute(scripts=altered)
        self.assertEqual(len(self.conn.rows), 2)

    def test_no_test_flags_no_provider_or_database_mutation(self):
        with self.assertRaises(M42FixtureBlocked):
            self.execute(flags={})
        self.assertEqual(self.conn.queries, [])

    def test_wrong_m20_project_bound_to_envelopes_rejected(self):
        plan = deepcopy(self.plan)
        plan["project_id"] = "f" * 32
        with self.assertRaises((M42FixtureBlocked, SREContractError)):
            self.execute(plan=plan)
        self.assertEqual(self.conn.transaction_count, 0)


if __name__ == "__main__":
    unittest.main()
