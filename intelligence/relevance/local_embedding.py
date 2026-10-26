"""M16: genuine LOCAL semantic embedding, strictly pinned ONNX CPU inference.

No cloud, HTTP, HuggingFace hub resolution, model downloads or fake fallback.
A real model ONNX and tokenizer.json are REQUIRED in a local directory with
caller-pinned exact SHA-256 and immutable full upstream revision. Test doubles
are used ONLY in separate pure contract tests, never as production backend.

M17 will use the vectors for all-leaf ranking. M18 will introduce
tenant-scoped caching/revocation. The embeddings are NOT match/no_match facts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import math
from pathlib import Path
import re
import unicodedata

from .v1_contracts import fingerprint, positive, record, reject, sha256_digest

VERSION = "sre-local-embedding/v1"
BACKEND = "onnxruntime-cpu"
MAX_TEXTS = 32
MAX_TEXT_CHARS = 8192
MAX_DIMENSIONS = 4096
MODEL_MAX_BYTES = 2 * 1024**3
TOKENIZER_MAX_BYTES = 32 * 1024**2
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}/[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_OUTPUTS = frozenset(("last_hidden_state", "token_embeddings", "sentence_embedding"))
_VERIFIED_SESSION_TOKEN = object()  # constructor may only be used after artifact SHA check


@dataclass(frozen=True, slots=True)
class LocalModelPins:
    schema_version: str
    model_name: str
    revision: str
    backend: str
    model_sha256: str
    tokenizer_sha256: str
    output_name: str
    pooling: str
    dimensions: int
    max_tokens: int
    pad_token: str
    def identity_sha256(self):
        return fingerprint({
            "schema_version": self.schema_version, "model_name": self.model_name,
            "revision": self.revision, "backend": self.backend,
            "model_sha256": self.model_sha256,
            "tokenizer_sha256": self.tokenizer_sha256,
            "output_name": self.output_name, "pooling": self.pooling,
            "dimensions": self.dimensions, "max_tokens": self.max_tokens,
            "pad_token": self.pad_token,
        })


def validate_local_model_pins(value):
    """Full descriptor must come from TRUSTED deployment config, not a client.

    Caller is responsible for selecting a trusted model/revision/checksum from
    its own audited deployment manifest; a self-signed SHA proves nothing.
    """
    fields=("schema_version","model_name","revision","backend",
            "model_sha256","tokenizer_sha256","output_name","pooling",
            "dimensions","max_tokens","pad_token")
    record(value,"local_model",required=fields)
    if value["schema_version"] != VERSION:
        reject("local_model.schema_version", "unsupported embedding contract",
               "unsupported_schema_version")
    if value["backend"] != BACKEND:
        reject("local_model.backend", "no cloud/fake/model-substitute backend",
               "local_model_blocked")
    if type(value["model_name"]) is not str or not _MODEL.fullmatch(value["model_name"]):
        reject("local_model.model_name", "expected pinned repository identifier")
    if type(value["revision"]) is not str or not _REVISION.fullmatch(value["revision"]):
        reject("local_model.revision", "expected exact full 40-hex commit")
    for name in ("model_sha256","tokenizer_sha256"):
        sha256_digest(value[name], "local_model."+name)
    if type(value["output_name"]) is not str or value["output_name"] not in _OUTPUTS:
        reject("local_model.output_name", "unexpected ONNX output ABI")
    if value["pooling"] not in ("masked_mean","sentence"):
        reject("local_model.pooling", "unsupported sentence pooling")
    if (value["pooling"]=="sentence") != (value["output_name"]=="sentence_embedding"):
        reject("local_model.pooling", "pooling and declared output incompatible")
    positive(value["dimensions"],"local_model.dimensions",maximum=MAX_DIMENSIONS)
    positive(value["max_tokens"],"local_model.max_tokens",maximum=512)
    if (type(value["pad_token"]) is not str or
            not 1<=len(value["pad_token"])<=40 or
            any(ord(c)<32 for c in value["pad_token"])):
        reject("local_model.pad_token", "invalid explicit tokenizer pad token")
    return LocalModelPins(**value)


def _sha256_file(path, max_bytes):
    digest=hashlib.sha256()
    read=0
    try:
        with path.open("rb") as fd:
            for chunk in iter(lambda:fd.read(1024*1024), b""):
                read+=len(chunk)
                if read>max_bytes:
                    reject("local_model.files","local model file too large",
                           "local_model_blocked")
                digest.update(chunk)
    except (OSError, ValueError) as exc:
        reject("local_model.files", "local model file unreadable",
               "local_model_blocked")
    if read==0:
        reject("local_model.files","empty model artifact","local_model_blocked")
    return digest.hexdigest()


def verify_local_model_files(directory, pins):
    """Fail BEFORE import or model inference if artifacts cannot be trusted."""
    if type(directory) not in (str, Path):
        reject("local_model.directory","expected local filesystem path",
               "local_model_blocked")
    desc=validate_local_model_pins(pins)
    raw=str(directory)
    if (not raw or "://" in raw or raw.startswith("\\\\") or
            "\x00" in raw or "\n" in raw):
        reject("local_model.directory","URL/remote paths are forbidden",
               "local_model_blocked")
    base=Path(raw).expanduser()
    # No symlink on root or any parent: never inadvertently load a remotely
    # mounted or substituted model via a path alias.
    if any(part.is_symlink() for part in (base,*base.parents)) or not base.is_dir():
        reject("local_model.directory","missing/untrusted local model directory",
               "local_model_blocked")
    files=(("model.onnx",desc.model_sha256,MODEL_MAX_BYTES),
           ("tokenizer.json",desc.tokenizer_sha256,TOKENIZER_MAX_BYTES))
    # No extra ONNX external-data files may be discovered by a runtime:
    # unsupported sidecars require a new, fully checksummed contract version.
    try:
        names={p.name for p in base.iterdir()}
    except OSError:
        reject("local_model.files","model directory unreadable",
               "local_model_blocked")
    if names!={"model.onnx","tokenizer.json"}:
        reject("local_model.files","unexpected/unpinned local model artifacts",
               "local_model_blocked")
    for name,expected,limit in files:
        path=base/name
        if not path.is_file() or path.is_symlink():
            reject("local_model.files","required pinned artifact missing/aliased",
                   "local_model_blocked")
        if _sha256_file(path,limit)!=expected:
            reject("local_model.files","local model SHA-256 mismatch",
                   "local_model_blocked")
    return desc,base


def _texts(value):
    if type(value) not in (list,tuple) or not 1<=len(value)<=MAX_TEXTS:
        reject("embedding.texts","expected 1..32 explicit texts")
    out=[]
    for i,text in enumerate(value):
        if (type(text) is not str or not text.strip() or
                len(text)>MAX_TEXT_CHARS or "\x00" in text):
            reject(f"embedding.texts[{i}]","missing/oversized text")
        out.append(unicodedata.normalize("NFC",text))
    return out


def normalize_real_vectors(values, *, dimensions):
    """Numeric-only ONNX output normalizer; NOT a mock semantic model."""
    positive(dimensions,"embedding.dimensions",maximum=MAX_DIMENSIONS)
    if type(values) not in (list,tuple) or not values:
        reject("embedding.vectors","no inference output","local_model_blocked")
    result=[]
    for i,row in enumerate(values):
        if type(row) not in (list,tuple) or len(row)!=dimensions:
            reject(f"embedding.vectors[{i}]","embedding dimension mismatch",
                   "local_model_blocked")
        if any(type(n) not in (float,int) or
               not math.isfinite(n) for n in row):
            reject(f"embedding.vectors[{i}]","NaN/Inf/bool embedding",
                   "local_model_blocked")
        square=math.fsum(float(n)*float(n) for n in row)
        if square<=1e-20 or not math.isfinite(square):
            reject(f"embedding.vectors[{i}]","zero/invalid embedding",
                   "local_model_blocked")
        scale=math.sqrt(square)
        result.append(tuple(float(n)/scale for n in row))
    return tuple(result)


def cosine_similarity(left,right):
    if (type(left) not in (tuple,list) or type(right) not in (tuple,list)
            or not left or len(left)!=len(right)):
        reject("embedding.cosine","vectors must have equal nonzero dimensions")
    dims=len(left)
    x,y=normalize_real_vectors([left,right],dimensions=dims)
    score=math.fsum(a*b for a,b in zip(x,y))
    if not math.isfinite(score):
        reject("embedding.cosine","invalid cosine","local_model_blocked")
    return max(-1.0,min(1.0,score))


class LocalONNXEmbeddingBackend:
    """A loaded real ONNX session, impossible to initialize without its files."""
    __slots__=("_pins","_tokenizer","_session","_np","_inputs","_identity")

    def __init__(self,desc,tokenizer,session,np,*,_verified_token=None):
        if _verified_token is not _VERIFIED_SESSION_TOKEN:
            reject("local_model.onnx","unverified/mock session is not production backend",
                   "local_model_blocked")
        self._pins=desc
        self._tokenizer=tokenizer
        self._session=session
        self._np=np
        self._identity=desc.identity_sha256()
        names=[entry.name for entry in session.get_inputs()]
        if (len(names)!=len(set(names)) or "input_ids" not in names or
                "attention_mask" not in names or
                set(names)-{"input_ids","attention_mask","token_type_ids"}):
            reject("local_model.onnx","unsupported ONNX input ABI",
                   "local_model_blocked")
        self._inputs=names
        outputs=[entry.name for entry in session.get_outputs()]
        if desc.output_name not in outputs:
            reject("local_model.onnx","expected ONNX output is missing",
                   "local_model_blocked")

    @property
    def model_identity_sha256(self):
        return self._identity

    @property
    def pins(self):
        return self._pins

    def embed(self,texts):
        """Actual CPU ONNX inference + masked mean pooling, never fake fallback."""
        raw=_texts(texts)
        self._tokenizer.enable_truncation(max_length=self._pins.max_tokens)
        self._tokenizer.enable_padding(
            pad_id=self._tokenizer.token_to_id(self._pins.pad_token),
            pad_token=self._pins.pad_token,
        )
        encoded=self._tokenizer.encode_batch(raw,add_special_tokens=True)
        if len(encoded)!=len(raw):
            reject("embedding.tokenizer","tokenizer changed batch size",
                   "local_model_blocked")
        try:
            ids=self._np.asarray([e.ids for e in encoded],dtype=self._np.int64)
            mask=self._np.asarray([e.attention_mask for e in encoded],
                                  dtype=self._np.int64)
            if (ids.ndim!=2 or mask.shape!=ids.shape or
                    ids.shape[1]>self._pins.max_tokens or
                    not self._np.all(mask.sum(axis=1)>0)):
                reject("embedding.tokenizer","invalid token/mask tensor",
                       "local_model_blocked")
            feeds={"input_ids":ids,"attention_mask":mask}
            if "token_type_ids" in self._inputs:
                feeds["token_type_ids"]=self._np.zeros_like(ids)
            raw_output=self._session.run([self._pins.output_name],feeds)[0]
            values=self._np.asarray(raw_output)
            if self._pins.pooling=="sentence":
                if values.shape!=(len(raw),self._pins.dimensions):
                    reject("embedding.onnx","sentence output shape mismatch",
                           "local_model_blocked")
            else:
                if values.shape!=(*ids.shape,self._pins.dimensions):
                    reject("embedding.onnx","token embeddings shape mismatch",
                           "local_model_blocked")
                weights=mask.astype(self._np.float64)[:,:,None]
                values=(values.astype(self._np.float64)*weights).sum(axis=1)/weights.sum(axis=1)
            vectors=normalize_real_vectors(values.tolist(),
                                           dimensions=self._pins.dimensions)
        except Exception as exc:
            # Do not silently substitute lexical/rule-based numbers.
            from .v1_contracts import SREContractError
            if isinstance(exc,SREContractError):
                raise
            reject("embedding.onnx","genuine local inference failed",
                   "local_model_blocked")
        return {
            "schema_version": VERSION,"backend":BACKEND,
            "model_name":self._pins.model_name,"revision":self._pins.revision,
            "model_identity_sha256":self._identity,
            "input_sha256":fingerprint(raw),
            "dimensions":self._pins.dimensions,
            "vectors":vectors,
            "vector_sha256":fingerprint([list(v) for v in vectors]),
            "real_model":True,  # only reachable through verified ONNX loader
            "source":"local_cpu_onnx",
        }


def load_local_embedding_backend(directory,pins):
    """Strict production entrypoint. NO fake injection or remote resolution."""
    desc,root=verify_local_model_files(directory,pins)
    try:
        ort=importlib.import_module("onnxruntime")
        tokenizers=importlib.import_module("tokenizers")
        np=importlib.import_module("numpy")
    except ImportError:
        reject("embedding.dependencies","local ONNX CPU packages not installed",
               "local_model_blocked")
    try:
        options=ort.SessionOptions()
        options.intra_op_num_threads=1
        options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        tokenizer=tokenizers.Tokenizer.from_file(str(root/"tokenizer.json"))
        pad_id=tokenizer.token_to_id(desc.pad_token)
        if type(pad_id) is not int or pad_id<0:
            reject("embedding.tokenizer","pad token absent from pinned vocabulary",
                   "local_model_blocked")
        session=ort.InferenceSession(
            str(root/"model.onnx"),sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        if session.get_providers()!=["CPUExecutionProvider"]:
            reject("embedding.onnx","non-CPU provider disabled",
                   "local_model_blocked")
        return LocalONNXEmbeddingBackend(
            desc,tokenizer,session,np,_verified_token=_VERIFIED_SESSION_TOKEN,
        )
    except Exception as exc:
        from .v1_contracts import SREContractError
        if isinstance(exc,SREContractError):
            raise
        reject("embedding.onnx","pinned local model cannot be loaded",
               "local_model_blocked")


def probe_local_embedding_model(directory,pins,*,real_inference=True):
    """Fail-closed health. A descriptor checksum is never proof of inference."""
    from .v1_contracts import SREContractError
    try:
        desc,root=verify_local_model_files(directory,pins)
        if not real_inference:
            return {
                "status":"blocked","reason":"inference_unverified",
                "real_inference_verified":False,
                "model_identity_sha256":desc.identity_sha256(),
            }
        model=load_local_embedding_backend(root,pins)
        sample=("A person is running outdoors.","Eine Person läuft im Freien.")
        first=model.embed(sample)
        second=model.embed(sample)
        if any(abs(a-b)>1e-6 for x,y in zip(first["vectors"],second["vectors"])
               for a,b in zip(x,y)):
            reject("embedding.preflight","CPU repeated inference diverged",
                   "local_model_blocked")
        return {
            "status":"ready","reason":None,
            "real_inference_verified":True,
            "model_identity_sha256":desc.identity_sha256(),
            "probe_vector_sha256":first["vector_sha256"],
            "dimensions":desc.dimensions,
        }
    except SREContractError as exc:
        return {
            "status":"blocked","reason":exc.code,
            "real_inference_verified":False,"model_identity_sha256":None,
        }


__all__=(
    "VERSION","BACKEND","LocalModelPins","LocalONNXEmbeddingBackend",
    "validate_local_model_pins","verify_local_model_files",
    "normalize_real_vectors","cosine_similarity",
    "load_local_embedding_backend","probe_local_embedding_model",
)
