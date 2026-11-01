"""Contract: one explicitly registered post is not dropped between Marketing
batch selection, carousel acquisition, D1 analytics and Bright Data scheduling.
No provider requests, rights elevation or database writes are performed.
"""
from __future__ import annotations

import unittest

from intelligence.market_intelligence.batch import (
    CANDIDATES_SQL, _source_for_video, balanced_selection)
from intelligence.market_intelligence.service import SQL_VIDEO_EVIDENCE
from intelligence.market_intelligence.brightdata_jobs import SQL_TARGETS
from intelligence.market_intelligence.carousel_jobs import _rights


SOURCE_KINDS=("user_video_reference","research_link","other")


class SourceTypeConsistency(unittest.TestCase):
    def test_registered_video_post_types_match_across_analytics(self):
        for name,query in [
            ("batch selection",CANDIDATES_SQL),
            ("marketing evidence",SQL_VIDEO_EVIDENCE),
            ("metric scheduling",SQL_TARGETS),
        ]:
            with self.subTest(stage=name):
                for kind in SOURCE_KINDS:
                    self.assertIn("'"+kind+"'",query)
                self.assertIn("ps.link_kind='video'",query)
                self.assertIn("ps.tombstoned_at IS NULL",query)

    def test_rights_checks_rely_on_same_post_type_set(self):
        # Real SQL is inspected via a read-only cursor fake.
        class Cursor:
            def __init__(self):
                self.statement=None
            def execute(self,sql,args):
                self.statement=sql
                return self
            def fetchone(self):
                return None
        for factory in ("batch","carousel"):
            cur=Cursor()
            with self.subTest(factory=factory):
                if factory=="batch":
                    _source_for_video(cur,"a"*32,{"platform":"tiktok",
                                      "native_id":"7689988051296210209"})
                else:
                    _rights(cur,"a"*32,"tiktok","7689988051296210209")
                for kind in SOURCE_KINDS:
                    self.assertIn("'"+kind+"'",cur.statement)
                self.assertIn("tombstoned_at IS NULL",cur.statement)

    def test_selection_does_not_infer_rights_for_registered_link(self):
        # This function only selects canonical real D1 identities and must
        # never synthesize 99 missing posts or grant media rights.
        rows=[(71,"https://www.tiktok.com/@luke.swan89/photo/7689988051296210209",
               "tiktok","7689988051296210209","author")]
        chosen=balanced_selection(rows)
        self.assertEqual(len(chosen),1)
        self.assertEqual(chosen[0][3],"7689988051296210209")


if __name__=="__main__":
    unittest.main()
