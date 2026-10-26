"""M06 C9 v1 authority, complete ancestry, H1/H2 and immutable exact pin QA.

Pure tests use the repository's existing S2 fake PostgreSQL snapshots.
The separate PostgreSQL integration test verifies real dual-human approvals.
No provider, network, paid execution or Collector/QDP imports.
"""
from __future__ import annotations

import copy
import subprocess
import sys
import unittest
from uuid import UUID, uuid5

from intelligence.relevance.ports.approved_c9 import (
    APPROVED_C9_VERSION, build_c9_bundle, load_approved_c9,
    validate_c9_bundle,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from test_sre_taxonomy_adapter import FakeConnection, db_fixtures, PID, PUB, RID


class ApprovedC9V1Unit(unittest.TestCase):
    def connection(self, change=None):
        data = db_fixtures()
        if change is not None:
            change(data)
        return FakeConnection(data)

    def test_one_authoritative_read_and_every_leaf_preserved(self):
        conn = self.connection()
        bundle = load_approved_c9(PID, PUB, connect=lambda: conn)
        self.assertEqual(bundle["schema_version"], APPROVED_C9_VERSION)
        self.assertEqual(bundle["project_id"], PID)
        self.assertEqual(bundle["publication_id"], PUB)
        self.assertEqual(bundle["rtb_run_id"], RID)
        self.assertEqual(bundle["sre_taxonomy_version_id"], 12)
        self.assertEqual(bundle["taxonomy_version"], 3)
        self.assertEqual(bundle, validate_c9_bundle(bundle))
        self.assertEqual(bundle["bundle_sha256"], fingerprint(
            {k: v for k, v in bundle.items() if k != "bundle_sha256"}))
        self.assertEqual(len(conn.sql), 8)  # one S2 transaction/read, not two
        self.assertIn("REPEATABLE READ READ ONLY", conn.sql[0][0])
        self.assertEqual(len(bundle["approved_snapshot"]["human_reviews"]), 2)
        leaf_nodes = [n for n in bundle["approved_snapshot"]["nodes"]
                      if n["kind"] == "hypothesis"]
        self.assertEqual(len(bundle["contexts"]), len(leaf_nodes))
        self.assertEqual(len(bundle["hypothesis_keys"]), len(leaf_nodes))
        self.assertEqual(len(bundle["node_projections"]),
                         len(bundle["approved_snapshot"]["nodes"]))
        self.assertEqual(bundle["hypothesis_keys"],
                         sorted(x["category_key"] for x in bundle["contexts"]))

        mapping = {n["source_node_id"]: n for n in bundle["node_projections"]}
        leaves = {ctx["source_node_id"]: ctx for ctx in bundle["contexts"]}
        self.assertEqual(set(leaves), {node["id"] for node in leaf_nodes})
        self.assertEqual(set(mapping), {node["id"]
                                       for node in bundle["approved_snapshot"]["nodes"]})
        for name, context in leaves.items():
            self.assertEqual(context["project_id"], PID)
            self.assertEqual(context["publication_id"], PUB)
            self.assertEqual(context["taxonomy_sha256"], bundle["taxonomy_sha256"])
            self.assertEqual(context["category_key"], str(uuid5(UUID(RID), name)))
            self.assertEqual(context["category_key"], mapping[name]["category_key"])
            self.assertTrue(context["approved_facts"])
            self.assertEqual(len(context["context_sha256"]), 64)
            self.assertEqual(context["context_sha256"],
                             fingerprint({k: v for k, v in context.items()
                                          if k != "context_sha256"}))
            self.assertEqual(context["ancestry_path"][-1], name)
            self.assertIsNone(mapping[context["ancestry_path"][0]]["parent_category_key"])
            for node_id in context["ancestry_path"][1:]:
                parent = mapping[node_id]["parent_source_node_id"]
                self.assertIn(parent, context["ancestry_path"])
                self.assertEqual(mapping[node_id]["parent_category_key"],
                                 mapping[parent]["category_key"])
            self.assertEqual(mapping[name]["kind"], "hypothesis")
            self.assertTrue(set(("id", "parent_id", "kind", "relation",
                "source_fact_ids", "name", "rationale", "definition", "include",
                "exclude", "examples")) <= set(context["hypothesis"]))
        self.assertEqual(bundle, load_approved_c9(
            PID, PUB, connect=lambda: self.connection()))

    def test_shape_only_is_not_db_authorization(self):
        authorized = load_approved_c9(PID, PUB, connect=lambda: self.connection())
        raw = copy.deepcopy(authorized["approved_snapshot"])
        self.assertEqual(build_c9_bundle(raw), authorized)
        # The pure serializer cannot attest to DB H1/H2 even with a valid hash.
        # Never expose it as a runtime authority grant (see port docstring).
        self.assertEqual(validate_c9_bundle(build_c9_bundle(raw)), authorized)

    def test_tampered_or_reordered_contexts_nodes_and_ancestors_rejected(self):
        baseline = load_approved_c9(PID, PUB, connect=lambda: self.connection())
        mutations = [
            lambda b: b["contexts"].pop(),
            lambda b: b["hypothesis_keys"].pop(),
            lambda b: b["contexts"].reverse(),
            lambda b: b["node_projections"].pop(),
            lambda b: b["node_projections"][0].update(category_key=str(UUID(int=7))),
            lambda b: b["node_projections"][1].update(parent_category_key=str(UUID(int=8))),
            lambda b: b["contexts"][0]["hypothesis"].update(include="Forged context"),
            lambda b: b["contexts"][0]["approved_facts"][0].update(text="Forged fact"),
            lambda b: b["contexts"][0].update(ancestry_path=["wrong"]),
            lambda b: b["taxonomy_reference"]["hypothesis_links"].pop(),
            lambda b: b["approved_snapshot"].update(human_reviews=[1]),
            lambda b: b.update(taxonomy_sha256="f" * 64),
            lambda b: b.update(publication_id=str(UUID(int=15))),
            lambda b: b.update(project_id="b" * 32),
            lambda b: b.update(rtb_run_id=str(UUID(int=16))),
            lambda b: b.update(taxonomy_version=True),
            lambda b: b.update(sre_taxonomy_version_id=0),
            lambda b: b.update(schema_version="sre-approved-c9/v2"),
            lambda b: b.update(injected_admin=True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(SREContractError):
                item = copy.deepcopy(baseline)
                mutate(item)
                validate_c9_bundle(item)

    def test_db_draft_foreign_tampered_review_and_flat_rejected(self):
        changes = [
            lambda x: x.__setitem__(0, None),  # flat-only/no deep publication
            lambda x: x.__setitem__(1, None),  # missing SRE projection
            lambda x: x.__setitem__(1, (*x[1][:5], "draft", *x[1][6:])),
            lambda x: x.__setitem__(2, x[2][:1]),  # absent H2
            lambda x: x[2].__setitem__(1, (2, [], "local_owner")),
            lambda x: x[2].__setitem__(1, (2, x[2][1][1], "")),
            lambda x: x.__setitem__(1, (*x[1][:4], "a"*64, *x[1][5:])),
            lambda x: x.__setitem__(3, x[3][:-1]),  # mirror leaf lost
            lambda x: x.__setitem__(4, x[4][:-1]),  # approved fact lost
            lambda x: x.__setitem__(5, x[5][:-1]),  # fact edge lost
            lambda x: x.__setitem__(6, x[6][:-1]),  # example lost
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(
                (PipelineError, SREContractError)
            ):
                load_approved_c9(PID, PUB, connect=lambda: self.connection(change))
        with self.assertRaises((PipelineError, SREContractError)):
            load_approved_c9("b" * 32, PUB, connect=lambda: self.connection())
        with self.assertRaises((PipelineError, SREContractError)):
            load_approved_c9(PID, str(UUID(int=17)), connect=lambda: self.connection())
        with self.assertRaises((PipelineError, SREContractError)):
            load_approved_c9(PID, RID, connect=lambda: self.connection())

    def test_no_mutable_pointer_writes_or_inference_in_db_loader(self):
        conn = self.connection()
        load_approved_c9(PID, PUB, connect=lambda: conn)
        for statement, parameters in conn.sql:
            upper = statement.upper()
            self.assertNotIn("ACTIVE_TAXONOMY_ID", upper)
            self.assertNotIn("UPDATE ", upper)
            self.assertNotIn("INSERT ", upper)
            self.assertNotIn("DELETE ", upper)
        self.assertEqual(conn.sql[1][1], (PID, PUB))
        self.assertEqual(conn.sql[2][1], (PID, PUB, RID))

    def test_import_is_independent_of_optional_modules(self):
        code = (
            "import sys; import intelligence.relevance.ports.approved_c9 as c;"
            "assert c.APPROVED_C9_VERSION.endswith('/v1');"
            "assert 'psycopg' not in sys.modules;"
            "assert 'intelligence.collector' not in sys.modules;"
            "assert 'intelligence.qualified_data' not in sys.modules;"
            "assert 'openrouter' not in sys.modules;"
        )
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
