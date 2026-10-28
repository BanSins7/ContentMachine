"""SP-06 offline model attestation, no fixture escape, no network or GPU.

A fake module models only the SentenceTransformer constructor contract. These
tests do NOT count as validation of true multilingual retrieval quality; that
requires operator-provisioned reviewed weights and a representative dataset.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from intelligence.search_planner.query_validation.embedding import (
    SentenceTransformerEmbeddingProvider, HashingTestEmbeddings, encode_checked,
)
from intelligence.search_planner.query_validation.model_manifest import (
    MANIFEST_FILENAME, build_model_manifest, verify_model_manifest,
)
from intelligence.search_planner.query_validation.provision_model import main
from intelligence.search_planner.query_validation.service import validate_generation_outbox
from intelligence.search_planner.query_validation.contracts import DEFAULT_POLICY
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32
EVENT = "55555555-5555-4555-8555-555555555555"
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REV = "a" * 40


class _Dense:
    def __init__(self, data):
        self.data = data
    def tolist(self):
        return self.data


class DummySentenceTransformer:
    instances = []

    def __init__(self, location, *, local_files_only=False, trust_remote_code=True):
        self.location = location
        self.local_files_only = local_files_only
        self.trust_remote_code = trust_remote_code
        self.__class__.instances.append(self)

    def get_sentence_embedding_dimension(self):
        return 384

    def encode(self, texts, *, batch_size, convert_to_numpy,
               normalize_embeddings, show_progress_bar):
        assert batch_size == 32 and convert_to_numpy
        assert normalize_embeddings and not show_progress_bar
        return _Dense([[1.0] + [0.0] * 383 for _ in texts])


class EmbeddingManifestStage06(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "modules.json").write_text("[]", encoding="utf-8")
        sub = self.root / "0_Transformer"
        sub.mkdir()
        (sub / "config.json").write_text('{"hidden_size":384}', encoding="utf-8")
        (sub / "model.safetensors").write_bytes(b"test-only-mock-weights")
        self.prepare()

    def prepare(self):
        manifest = build_model_manifest(
            self.root, model_id=MODEL_ID, revision=REV, dimensions=384)
        (self.root / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8")
        return manifest

    def fake_library(self):
        return mock.patch.dict(sys.modules, {
            "sentence_transformers": SimpleNamespace(
                SentenceTransformer=DummySentenceTransformer),
        })

    def test_manifest_is_deterministic_and_key_derived_from_sha(self):
        p = verify_model_manifest(self.root)
        self.assertEqual(p, verify_model_manifest(self.root))
        self.assertTrue(p["model_key"].startswith("st-local-sha256:"))
        self.assertEqual(len(p["manifest"]["files"]), 3)
        self.assertEqual(p["manifest"]["dimensions"], 384)
        self.assertEqual(p["manifest"]["revision"], REV)

    def test_model_loaded_only_from_local_path_and_offline_only(self):
        DummySentenceTransformer.instances.clear()
        pin = verify_model_manifest(self.root)["model_key"]
        with self.fake_library():
            provider = SentenceTransformerEmbeddingProvider(self.root, model_key=pin)
        self.assertEqual(provider.mode, "semantic_local")
        self.assertEqual(provider.model_key, pin)
        self.assertEqual(len(DummySentenceTransformer.instances), 1)
        loaded = DummySentenceTransformer.instances[0]
        self.assertEqual(loaded.location, str(self.root))
        self.assertTrue(loaded.local_files_only)
        self.assertFalse(loaded.trust_remote_code)
        self.assertEqual(len(encode_checked(provider, ["проверка", "exercise"])), 2)

    def test_wrong_operator_model_key_never_calls_loader(self):
        DummySentenceTransformer.instances.clear()
        with self.fake_library(), self.assertRaises(PipelineError):
            SentenceTransformerEmbeddingProvider(self.root, model_key="unverified-model-v1")
        self.assertEqual(DummySentenceTransformer.instances, [])

    def test_provisioned_model_fails_if_bytes_mutated_after_loading(self):
        with self.fake_library():
            provider = SentenceTransformerEmbeddingProvider(self.root)
        (self.root / "0_Transformer" / "model.safetensors").write_bytes(b"different")
        with self.assertRaisesRegex(PipelineError, "checksum"):
            provider.verify_runtime_pin()

    def test_missing_extra_or_modified_weights_rejected_before_loading(self):
        cases = ("missing", "added", "changed")
        for case in cases:
            with self.subTest(case=case):
                original = (self.root / "0_Transformer" / "model.safetensors").read_bytes()
                if case == "missing":
                    (self.root / "0_Transformer" / "model.safetensors").unlink()
                elif case == "added":
                    (self.root / "new.bin").write_bytes(b"unreviewed")
                else:
                    (self.root / "0_Transformer" / "model.safetensors").write_bytes(b"mutated")
                with self.assertRaises(PipelineError):
                    verify_model_manifest(self.root)
                (self.root / "0_Transformer" / "model.safetensors").write_bytes(original)
                (self.root / "new.bin").unlink(missing_ok=True)
                self.assertEqual(verify_model_manifest(self.root)["manifest"]["revision"], REV)

    def test_symlinked_weights_are_rejected(self):
        link = self.root / "shortcut.safetensors"
        try:
            link.symlink_to(self.root / "0_Transformer" / "model.safetensors")
        except (OSError, NotImplementedError):
            self.skipTest("Filesystem or Windows runner prohibits symlinks")
        with self.assertRaisesRegex(PipelineError, "symlink"):
            verify_model_manifest(self.root)

    def test_no_manifest_no_production_provider(self):
        (self.root / MANIFEST_FILENAME).unlink()
        with self.fake_library(), self.assertRaises(PipelineError):
            SentenceTransformerEmbeddingProvider(self.root)

    def test_manifest_provenance_revision_and_dimension_strict(self):
        for field, value in (
            ("revision", "main"), ("revision", "A" * 40),
            ("model_id", "malicious-id"), ("dimensions", True),
            ("dimensions", 5000), ("schema_version", "v2"),
        ):
            original = self.prepare()
            original[field] = value
            (self.root / MANIFEST_FILENAME).write_text(
                json.dumps(original), encoding="utf-8")
            with self.subTest(field=field, value=value), self.assertRaises(PipelineError):
                verify_model_manifest(self.root)
        self.prepare()

    def test_loaded_embedding_dimension_mismatch_blocks(self):
        class WrongDim(DummySentenceTransformer):
            def get_sentence_embedding_dimension(self):
                return 768
        with mock.patch.dict(sys.modules, {
            "sentence_transformers": SimpleNamespace(SentenceTransformer=WrongDim),
        }), self.assertRaisesRegex(PipelineError, "dimension"):
            SentenceTransformerEmbeddingProvider(self.root)

    def test_non_offline_model_loader_fails_closed(self):
        class OlderSentenceTransformer:
            def __init__(self, path):
                pass
        with mock.patch.dict(sys.modules, {
            "sentence_transformers": SimpleNamespace(
                SentenceTransformer=OlderSentenceTransformer),
        }), self.assertRaisesRegex(PipelineError, "failed to load safely"):
            SentenceTransformerEmbeddingProvider(self.root)

    def test_provisioner_write_and_verify_offline_without_download(self):
        (self.root / MANIFEST_FILENAME).unlink()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([
                "--model-dir", str(self.root), "--write",
                "--model-id", MODEL_ID, "--revision", REV,
                "--dimensions", "384",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["model_key"],
                         verify_model_manifest(self.root)["model_key"])
        # No silent overwrite of an attestation.
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["--model-dir", str(self.root), "--write",
                      "--model-id", MODEL_ID, "--revision", REV, "--dimensions", "384"])

    def test_provider_claiming_semantic_local_cannot_publish_to_qv(self):
        class Spoofed:
            mode = "semantic_local"
            model_key = "st-local-sha256:" + "f" * 64
            def encode(self, texts):
                return [[1.0] * 64 for _ in texts]
            def verify_runtime_pin(self):
                return self.model_key
        with self.assertRaisesRegex(PipelineError, "verified local SentenceTransformer"):
            validate_generation_outbox(
                PID, EVENT, embedding_provider=Spoofed(), policy=DEFAULT_POLICY)

    def test_test_fixture_must_be_real_fixture_and_explicitly_opted_in(self):
        fixture = HashingTestEmbeddings()
        with self.assertRaises(PipelineError):
            validate_generation_outbox(PID, EVENT, embedding_provider=fixture,
                                       policy=DEFAULT_POLICY)
        class FakeFixture:
            mode = "test_fixture"
            model_key = "qa-hashing-fixture-v1"
        with self.assertRaises(PipelineError):
            validate_generation_outbox(
                PID, EVENT, embedding_provider=FakeFixture(),
                policy=DEFAULT_POLICY, allow_test_embeddings=True)

    def test_embedding_batch_still_rejects_zero_and_nonfinite(self):
        class Malformed:
            def encode(self, _):
                return [[float("nan")]]
        with self.assertRaises(PipelineError):
            encode_checked(Malformed(), ["abc"])


if __name__ == "__main__":
    unittest.main()
