"""S2 unit tests: real RTB serializer fixture, fake PostgreSQL resultsets.

No database, network or paid provider required. PostgreSQL E2E is in
test_sre_taxonomy_adapter_pg.py and runs only with CM_TEST_PG_DSN.
"""
from __future__ import annotations

import copy
import unittest
from uuid import UUID, uuid5
from unittest import mock

from intelligence.relevance.taxonomy_adapter import (
    load_approved_taxonomy, load_classifier_contexts, read_authorized_taxonomy,
)
from intelligence.relevance.taxonomy_mapping import (
    expected_flat_projection, _expected_category, verify_authorized_mirror,
)
from intelligence.relevance.hypothesis_context import (
    build_classifier_contexts, summarize_by_direction,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint
from intelligence.taxonomy_builder.pipeline_contracts import sha256, PipelineError
from test_search_planner_foundation import PID, PUB, RID, snapshot as base_snapshot


def db_fixtures():
    p = base_snapshot()
    flat = expected_flat_projection(p["tree"], RID)
    flat_hash = sha256(flat)
    sre_version = 12
    row = (PID, RID, PUB, sre_version, flat_hash, "approved",
           flat_hash, copy.deepcopy(flat), "local_owner", "local_owner",
           {"severe_gap_count": 0, "duplicate_count": 0, "passed": True})
    reviews = [
        (1, copy.deepcopy(p["tree"][:4]), "local_owner"),
        (2, copy.deepcopy(p["tree"]), "local_owner"),
    ]
    categories = []
    for item in flat:
        c = _expected_category(item)
        categories.append((
            c["category_key"], c["name"], c["relation_type"], c["definition"],
            c["rules"], c["related_fact_ids"], c["review_status"],
            c["origin"], c["independent"],
        ))
    facts = [(f["id"], f["kind"], f["text"],
              f.get("source_quote") or f["text"],
              "explicit", "approved") for f in p["approved_facts"]]
    links = [(item["id"], fid) for item in flat for fid in item["fact_ids"]]
    first = (PID, RID, PUB, p["taxonomy_version"], p["tree"],
             p["approved_facts"], p["profile_snapshot"],
             "approved", "approved", [1, 2])
    return [first, row, reviews, categories, facts, links]


class FakeResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value


class FakeConnection:
    def __init__(self, values):
        self.values = copy.deepcopy(values)
        self.sql = []
        self.pointer = 0

    def execute(self, statement, parameters=None):
        self.sql.append((statement, parameters))
        if statement.startswith("SET TRANSACTION"):
            return FakeResult(None)
        if self.pointer >= len(self.values):
            raise AssertionError("Unexpected DB query")
        item = self.values[self.pointer]
        self.pointer += 1
        return FakeResult(item)

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SRETaxonomyAdapterUnit(unittest.TestCase):
    def fixture(self, mutate=None):
        raw = db_fixtures()
        if mutate:
            mutate(raw)
        return FakeConnection(raw)

    def test_approved_read_is_read_only_and_project_scoped(self):
        fake = self.fixture()
        result = load_approved_taxonomy(PID, PUB, connect=lambda: fake)
        self.assertEqual(result["project_id"], PID)
        self.assertEqual(result["taxonomy_version"], 3)
        self.assertEqual(result["sre_taxonomy_version_id"], 12)
        self.assertEqual(len(result["hypothesis_links"]), 3)
        self.assertEqual(len(fake.sql), 7)
        self.assertIn("REPEATABLE READ READ ONLY", fake.sql[0][0])
        for sql, params in fake.sql[1:]:
            self.assertTrue(sql.lstrip().upper().startswith("SELECT"))
            self.assertTrue("project_id" in sql or "l.project_id" in sql)
            self.assertEqual(params[0], PID)
        self.assertEqual(fake.sql[1][1], (PID, PUB))
        self.assertEqual(fake.sql[2][1], (PID, PUB, RID))
        self.assertEqual(fake.sql[5][1][1], [f["id"] for f in base_snapshot()["approved_facts"]])

    def test_context_has_full_11_fields_evidence_ancestry_and_deterministic_hash(self):
        fake = self.fixture()
        contexts = load_classifier_contexts(PID, PUB, connect=lambda: fake)
        self.assertEqual(len(contexts), 3)
        mapping = {c["source_node_id"]: c for c in contexts}
        c = mapping["short"]
        self.assertEqual(c["ancestry_path"], ["home", "bodyweight", "short"])
        self.assertEqual(c["branch"]["id"], "bodyweight")
        self.assertEqual(c["direction"]["id"], "home")
        self.assertEqual(c["hypothesis"]["exclude"],
                         "Неподходящие сторонние темы; без ложных обещаний")
        self.assertEqual(c["sibling_hypotheses"][0]["id"], "morning")
        self.assertEqual([f["id"] for f in c["approved_facts"]],
                         sorted([x["id"] for x in base_snapshot()["approved_facts"]]))
        self.assertEqual(c["category_key"], str(uuid5(UUID(RID), "short")))
        h = c["context_sha256"]
        clone = dict(c); clone.pop("context_sha256")
        self.assertEqual(h, fingerprint(clone))
        self.assertEqual(c["schema_version"], "sre-hypothesis-context/v1")
        self.assertIsNone(mapping["time_hyp"]["branch"])
        self.assertEqual(summarize_by_direction(contexts)["home"],
                         sorted([mapping["short"]["category_key"], mapping["morning"]["category_key"]]))
        self.assertEqual(contexts, load_classifier_contexts(PID, PUB, connect=lambda: self.fixture()))

    def test_context_generator_does_not_mutate_snapshot(self):
        snapshot = load_approved_taxonomy(PID, PUB, connect=lambda: self.fixture())
        source_copy = copy.deepcopy(snapshot)
        contexts = build_classifier_contexts(snapshot)
        contexts[0]["direction"]["name"] = "MALICIOUS"
        contexts[0]["approved_facts"][0]["text"] = "MALICIOUS"
        self.assertEqual(snapshot, source_copy)

    def test_flat_only_unknown_publication_and_cross_project_rejected(self):
        rows = db_fixtures()
        rows[0] = None
        with self.assertRaises(PipelineError):
            load_approved_taxonomy(PID, PUB, connect=lambda: FakeConnection(rows))
        with self.assertRaises(SREContractError):
            load_approved_taxonomy("bad", PUB, connect=lambda: self.fixture())
        with self.assertRaises(PipelineError):
            load_approved_taxonomy("b" * 32, PUB, connect=lambda: self.fixture())
        with self.assertRaises(PipelineError):
            load_approved_taxonomy(PID, "invalid", connect=lambda: self.fixture())

    def test_wrong_db_mirror_and_missing_human_review_are_fail_closed(self):
        mutations = [
            lambda x: x.__setitem__(1, None),
            lambda x: x.__setitem__(1, (*x[1][:5], "draft", *x[1][6:])),
            lambda x: x.__setitem__(1, (*x[1][:4], "0"*64, *x[1][5:])),
            lambda x: x.__setitem__(1, (*x[1][:6], "0"*64, *x[1][7:])),
            lambda x: x.__setitem__(1, (*x[1][:7], [], *x[1][8:])),
            lambda x: x.__setitem__(1, ("b"*32, *x[1][1:])),
            lambda x: x.__setitem__(1, (*x[1][:1], str(UUID(int=12)), *x[1][2:])),
            lambda x: x.__setitem__(2, x[2][:1]),
            lambda x: x[2].__setitem__(1, (2, [], "local_owner")),
            lambda x: x[2].__setitem__(1, (2, x[2][1][1], "")),
            lambda x: x.__setitem__(3, x[3][:-1]),
            lambda x: x.__setitem__(4, x[4][:-1]),
            lambda x: x.__setitem__(5, x[5][:-1]),
            lambda x: x.__setitem__(3, x[3] + [x[3][0]]),
            lambda x: x.__setitem__(5, x[5] + [x[5][0]]),
            lambda x: x.__setitem__(1, (*x[1][0:10], {"severe_gap_count": 1, "duplicate_count": 0})),
        ]
        for i, change in enumerate(mutations):
            with self.subTest(case=i), self.assertRaises(SREContractError):
                load_approved_taxonomy(PID, PUB, connect=lambda: self.fixture(change))

    def test_mirrored_title_rules_hierarchy_facts_and_fact_lineage_fail_closed(self):
        def mutation(category_index, field_index, value):
            def apply(rows):
                categories = rows[3]
                entry = list(categories[category_index])
                entry[field_index] = value
                categories[category_index] = tuple(entry)
            return apply
        for index, modification in enumerate((
            mutation(0, 1, "Modified name"),
            mutation(0, 2, "adjacent"),
            mutation(0, 3, "Modified definition"),
            mutation(0, 4, {"include": ["wrong"]}),
            mutation(0, 5, []),
            mutation(0, 6, "rejected"),
            mutation(0, 7, "explicit"),
            mutation(0, 8, True),
            mutation(1, 0, "11111111-1111-4111-8111-111111111111"),
            lambda rows: rows[4].__setitem__(0, (*rows[4][0][:2], "changed text", *rows[4][0][3:])),
            lambda rows: rows[4].__setitem__(0, (*rows[4][0][:5], "rejected")),
        )):
            with self.subTest(index=index), self.assertRaises(SREContractError):
                load_approved_taxonomy(PID, PUB, connect=lambda: self.fixture(modification))

    def test_immutable_snapshot_does_not_use_mutable_active_workspace(self):
        fake = self.fixture()
        read_authorized_taxonomy(fake, PID, PUB)
        for sql, _ in fake.sql:
            self.assertNotIn("active_taxonomy_id", sql)
            self.assertNotIn("UPDATE ", sql.upper())
            self.assertNotIn("INSERT ", sql.upper())


if __name__ == "__main__":
    unittest.main()
