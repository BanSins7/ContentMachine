"""FIX-07 verified SRE D1 metadata identity; no latest-observation fallback."""
from __future__ import annotations

import unittest

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.provenance import (
    pinned_source_identity, read_exact_metadata,
)


def pinned(**changes):
    data = {
        "content_version_id": 12,
        "content_sha256": "a" * 64,
        "source_ids": ["s1"],
        "source_policy_version_ids": [8],
        "metadata_observation_ids": [200],
        "discovery_ids": [31],
    }
    data.update(changes)
    return data


class DB:
    def __init__(self, *, schema=True, content=None, discovery=True,
                 metadata=None):
        self.schema = schema
        self.content = content if content is not None else {
            "id":12,"source_id":"s1","source_policy_version_id":8,
            "metadata_observation_id":200,"content_sha256":"a"*64,
            "provenance":{"discovery_id":31},
            "content":{"title":"real title","description":None},
        }
        self.discovery = discovery
        self.metadata = metadata if metadata is not None else {
            "metadata_id":200,"video_id":1,
            "source_id":"s1","source_policy_version_id":8,
            "title":"real title","description":None,"published_at":None,
            "fetched_at":"from-source","expires_at":None,
        }
        self.queries = []

    def execute(self, sql, args=None):
        self.queries.append((sql, args))
        if "FROM pg_attribute" in sql:
            answer = {"observation_column":self.schema,
                      "discovery_table":self.schema}
        elif "FROM sre_content_versions c" in sql:
            answer = self.content
        elif "FROM sre_project_discoveries pd" in sql:
            answer = {"id":31} if self.discovery else None
        elif "FROM source_video_metadata m" in sql:
            answer = self.metadata
        else:
            raise AssertionError("Unrecognized metadata SQL " + sql)
        class Cursor:
            def fetchone(self_inner):
                return answer
        return Cursor()


class ExactSourceTests(unittest.TestCase):
    def test_valid_pinned_identity_no_query(self):
        self.assertEqual(pinned_source_identity(pinned()), {
            "content_version_id":12,"source_id":"s1",
            "source_policy_version_id":8,
            "metadata_observation_id":200,"discovery_id":31,
            "content_sha256":"a"*64,
        })

    def test_missing_source_observation_discovery_are_blocked(self):
        bad = (
            {"source_ids":[]}, {"source_ids":["s1","s2"]},
            {"source_policy_version_ids":[]},
            {"metadata_observation_ids":[]}, {"metadata_observation_ids":[200,201]},
            {"discovery_ids":[]}, {"content_version_id":None},
        )
        for variant in bad:
            with self.subTest(variant=variant):
                db=DB()
                got,reason=read_exact_metadata(db,"a"*32,1,pinned(**variant))
                self.assertIsNone(got)
                self.assertEqual(reason,"sre_source_unpinned")
                self.assertFalse(db.queries)

    def test_without_external_sre_s3_schema_block(self):
        db=DB(schema=False)
        got,reason=read_exact_metadata(db,"a"*32,1,pinned())
        self.assertIsNone(got)
        self.assertEqual(reason,"sre_metadata_contract_unavailable")
        self.assertEqual(len(db.queries),1)

    def test_pinned_exact_observation_and_null_publication(self):
        db=DB()
        result, reason=read_exact_metadata(db,"a"*32,1,pinned())
        self.assertIsNone(reason)
        self.assertEqual(result["metadata_id"],200)
        self.assertEqual(result["discovery_id"],31)
        self.assertIsNone(result["published_at"])
        self.assertEqual(result["provenance_schema"],"qdp-exact-metadata/v1")
        sql,args=db.queries[-1]
        self.assertIn("m.id=%s",sql)
        self.assertNotIn("ORDER BY m.fetched_at DESC",sql)
        self.assertEqual(args,(200,1,"s1",8))

    def test_source_replacement_or_sha_mismatch_blocks(self):
        for change in (
            {"source_id":"s2"}, {"source_policy_version_id":999},
            {"metadata_observation_id":202},
            {"content_sha256":"b"*64},
            {"provenance":{"discovery_id":99}},
        ):
            with self.subTest(change=change):
                altered={**DB().content, **change}
                db=DB(content=altered)
                result, reason=read_exact_metadata(db,"a"*32,1,pinned())
                self.assertIsNone(result)
                self.assertEqual(reason,"sre_metadata_lineage_mismatch")

    def test_revoked_source_content_or_project_discovery_blocks(self):
        cases=((DB(content={}),"sre_metadata_lineage_mismatch"),
               (DB(discovery=False),"sre_discovery_lineage_missing"),
               (DB(metadata={}),"pinned_metadata_unavailable_or_revoked"))
        for db,expected in cases:
            with self.subTest(expected=expected):
                result,reason=read_exact_metadata(db,"a"*32,1,pinned())
                self.assertIsNone(result)
                self.assertEqual(reason,expected)

    def test_inplace_title_rewrite_is_detected(self):
        db=DB(metadata={**DB().metadata,"title":"newer global metadata"})
        result,reason=read_exact_metadata(db,"a"*32,1,pinned())
        self.assertIsNone(result)
        self.assertEqual(reason,"pinned_metadata_content_changed")


if __name__ == "__main__":
    unittest.main()
