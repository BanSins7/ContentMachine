"""M16 fast negative/fidelity contracts; NO fake semantic backend is accepted.

These unit tests test pins, numeric/pooling helpers and strict loader failures.
Actual controlled CPU inference is in test_sre_local_embedding_real.py and
M16 DoD stays BLOCKED until that test genuinely passes with pinned model.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import patch
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from intelligence.relevance.local_embedding import (
    VERSION, BACKEND, LocalONNXEmbeddingBackend,
    validate_local_model_pins, verify_local_model_files,
    normalize_real_vectors, cosine_similarity,
    probe_local_embedding_model, load_local_embedding_backend,
)
from intelligence.relevance.v1_contracts import SREContractError

PIN = {
    "schema_version": VERSION,
    "model_name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "revision": "1" * 40,
    "backend": BACKEND,
    "model_sha256": "a" * 64,
    "tokenizer_sha256": "b" * 64,
    "output_name": "last_hidden_state",
    "pooling": "masked_mean",
    "dimensions": 384,
    "max_tokens": 128,
    "pad_token": "[PAD]",
}


def _filepins(model_bytes=b"ONNX placeholder, NOT real model",
              tokenizer_bytes=b'{"mock":true}'):
    return {
        **PIN,
        "model_sha256":hashlib.sha256(model_bytes).hexdigest(),
        "tokenizer_sha256":hashlib.sha256(tokenizer_bytes).hexdigest(),
    }


def _rejected(test,fn,*args,expected_code=None):
    with test.assertRaises(SREContractError) as ctx:
        fn(*args)
    if expected_code:
        test.assertEqual(ctx.exception.code,expected_code)


class LocalEmbeddingsM16(unittest.TestCase):
    def test_strict_real_onnx_descriptor_and_identical_stable_id(self):
        p=validate_local_model_pins(PIN)
        self.assertEqual(p.backend,"onnxruntime-cpu")
        self.assertEqual(p.model_name,PIN["model_name"])
        self.assertEqual(p.revision,"1"*40)
        self.assertEqual(p.identity_sha256(),validate_local_model_pins(dict(PIN)).identity_sha256())
        self.assertEqual(len(p.identity_sha256()),64)
        altered=dict(PIN);altered["model_sha256"]="c"*64
        self.assertNotEqual(p.identity_sha256(),validate_local_model_pins(altered).identity_sha256())
        with self.assertRaises(FrozenInstanceError):
            p.model_name="hijacked"

    def test_no_cloud_fake_latest_alias_revision_or_unpinned_fields(self):
        mutations=[
            ("backend","huggingface"),("backend","mock"),("backend","openai"),
            ("model_name","all-MiniLM-L6-v2"),("model_name","https://example.org"),
            ("revision","latest"),("revision","123abc"),
            ("model_sha256","A"*64),("tokenizer_sha256",None),
            ("pooling","CLS"),("pooling","sentence"),
            ("output_name","pooled_output"),("dimensions",True),
            ("dimensions",0),("dimensions",5000),("max_tokens",0),
            ("max_tokens",True),("max_tokens",513),("pad_token",""),
            ("schema_version","sre-local-embedding/v2"),
        ]
        for field,value in mutations:
            with self.subTest(field=field,value=str(value)):
                data=dict(PIN);data[field]=value
                _rejected(self,validate_local_model_pins,data)
        bogus=dict(PIN);bogus["access_key"]="secret"
        _rejected(self,validate_local_model_pins,bogus)
        _rejected(self,validate_local_model_pins,None)
        good=dict(PIN);good["output_name"]="sentence_embedding";good["pooling"]="sentence"
        self.assertEqual(validate_local_model_pins(good).pooling,"sentence")

    def test_on_disk_sha_checks_before_loading_and_no_remote_or_symlinks(self):
        model_data=b"static small file - never used as semantic model"
        tokenizer_data=b"static tokenizer placeholder"
        pins=_filepins(model_data,tokenizer_data)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"model.onnx").write_bytes(model_data)
            (root/"tokenizer.json").write_bytes(tokenizer_data)
            desc,path=verify_local_model_files(root,pins)
            self.assertEqual(desc.revision,pins["revision"])
            self.assertEqual(path,root)
            report=probe_local_embedding_model(root,pins,real_inference=False)
            self.assertEqual(report["status"],"blocked")
            self.assertFalse(report["real_inference_verified"])
            self.assertEqual(report["reason"],"inference_unverified")
            (root/"model.onnx").write_bytes(model_data+b"evil")
            _rejected(self,verify_local_model_files,root,pins,
                      expected_code="local_model_blocked")
            self.assertEqual(
                probe_local_embedding_model(root,pins)["status"],"blocked"
            )
            (root/"model.onnx").write_bytes(model_data)
            (root/"unexpected.onnx.data").write_bytes(b"unverified extra weights")
            _rejected(self,verify_local_model_files,root,pins)
            (root/"unexpected.onnx.data").unlink()
            # Simulate an aliased file rather than requesting Windows
            # symlink-creation privileges on a hosted CI worker.
            with patch.object(Path,"is_symlink",
                              lambda p:p.name=="tokenizer.json"):
                _rejected(self,verify_local_model_files,root,pins)
        for invalid in ("https://huggingface.co/a/b","s3://bucket",
                        "\\\\network\\share","",None,True):
            _rejected(self,verify_local_model_files,invalid,pins)

    def test_no_direct_fake_session_can_claim_real_model_inference(self):
        p=validate_local_model_pins(PIN)
        _rejected(self,LocalONNXEmbeddingBackend,p,object(),object(),object(),
                  expected_code="local_model_blocked")

    def test_no_implicit_fallback_when_runtime_or_real_model_unavailable(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"model.onnx").write_bytes(b"fake")
            (root/"tokenizer.json").write_text("{}")
            p=_filepins(b"fake",b"{}")
            outcome=probe_local_embedding_model(root,p,real_inference=False)
            self.assertEqual(outcome["status"],"blocked")
            self.assertFalse(outcome["real_inference_verified"])
            # In the CI environment without optional dependencies this must
            # remain blocked. In a configured environment the fake ONNX must
            # likewise never produce a model or lexical fallback.
            with self.assertRaises(SREContractError):
                load_local_embedding_backend(root,p)
            outcome=probe_local_embedding_model(root,p)
            self.assertEqual(outcome["status"],"blocked")
            self.assertNotIn("probe_vector_sha256",outcome)

    def test_nonfinite_zero_bool_dimension_nan_and_utf8_cosine_contracts(self):
        valid=normalize_real_vectors([[3.0,4.0],[-3.0,4.0]],dimensions=2)
        self.assertAlmostEqual(math.fsum(x*x for x in valid[0]),1.0,places=14)
        self.assertAlmostEqual(cosine_similarity([1.0,0.0],[0.0,1.0]),0.0)
        self.assertAlmostEqual(cosine_similarity([1,0],[-1,0]),-1.0)
        for row in (
            [float("nan"),0],[float("inf"),1],[True,1],
            [0.0,0.0],[0.0],[1,2,3],
        ):
            _rejected(self,normalize_real_vectors,[row],dimensions=2)
        _rejected(self,cosine_similarity,[1,2],[1])
        _rejected(self,cosine_similarity,[],[])
        _rejected(self,normalize_real_vectors,[[1,0]],dimensions=True)

    def test_no_cloud_import_at_module_import_time(self):
        script=(
            "import sys;import intelligence.relevance.local_embedding;"
            "assert 'onnxruntime' not in sys.modules;"
            "assert 'tokenizers' not in sys.modules;"
            "assert 'sentence_transformers' not in sys.modules;"
            "assert 'openai' not in sys.modules;"
            "assert 'transformers' not in sys.modules;"
        )
        proc=subprocess.run([sys.executable,"-c",script],
                            capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)


if __name__=="__main__":
    unittest.main()
