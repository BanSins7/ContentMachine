"""M17 ALL-LEAF C9 retrieval: full approved tree, exclusions and deterministic ties.

Rank is not a semantic decision; a low cosine score is NEVER no_match.
The only production entry loads the exact DB-approved C9 and D1 pinned video
and a SHA-verified real local ONNX M16 backend. No cloud, DB write, provider,
price, candidate drop, threshold, fabricated content or fake model substitute.
"""
from __future__ import annotations

from math import isfinite
from unicodedata import normalize

from .local_embedding import (
    LocalONNXEmbeddingBackend, MAX_TEXTS, MAX_TEXT_CHARS,
    cosine_similarity, load_local_embedding_backend, normalize_real_vectors,
)
from .v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, reject, sha256_digest,
    validate_content_input,
)
from .ports.approved_c9 import validate_c9_bundle

RANK_VERSION = "sre-all-leaf-ranking/v1"
MAX_LEAVES = 48
MAX_FEATURES = 3 * MAX_LEAVES + 1
MAX_CONTENT_CHARACTERS = MAX_TEXT_CHARS
EXCLUSION_PENALTY = 0.25


def _lines(values, path):
    if type(values) is not list or any(type(x) is not str or not x.strip() for x in values):
        reject(path, "expected explicit C9 text list")
    return list(values)


def _node_text(node, path, *, excluding=False):
    if type(node) is not dict:
        reject(path, "missing C9 hierarchy node", "snapshot_mismatch")
    if excluding:
        parts = _lines(node.get("exclude"), path + ".exclude")
    else:
        parts = []
        for k in ("name", "definition"):
            v = node.get(k)
            if type(v) is not str or not v.strip():
                reject(path + "." + k, "missing approved C9 text", "snapshot_mismatch")
            parts.append(v)
        parts.extend(_lines(node.get("include"), path + ".include"))
    return parts


def _join(parts, label):
    if any(type(p) is not str for p in parts):
        reject(label, "non-text semantic source", "snapshot_mismatch")
    rendered = normalize("NFC", "\n".join(x.strip() for x in parts if x.strip()))
    if not rendered or len(rendered) > MAX_TEXT_CHARS or "\x00" in rendered:
        # NEVER truncate RTB facts/constraints or silently replace with keywords.
        reject(label, "empty or oversized semantic input; explicit revision required",
               "local_model_blocked")
    return rendered


def prepare_c9_leaf_features(c9):
    """Pure C9 projection. Shape-valid bundle is NOT approval authority.

    Each leaf has a positive meaning text derived from the full DIRECTION ->
    optional BRANCH -> HYPOTHESIS ancestry, plus source-backed approved facts.
    Negative/exclusion texts are separate, never mixed into a positive prompt.
    No query terms, sibling shortcuts, inferred facts or semantic labels.
    """
    bundle = validate_c9_bundle(c9)
    contexts = bundle["contexts"]
    expected_keys = bundle["hypothesis_keys"]
    if not 1 <= len(contexts) <= MAX_LEAVES or len(contexts) != len(expected_keys):
        reject("c9.contexts", "missing or excessive approved leaves",
               "incomplete_coverage")
    if expected_keys != sorted(set(expected_keys)):
        reject("c9.hypothesis_keys", "unsorted or duplicate approved leaves",
               "incomplete_coverage")
    by_key = {}
    for ctx in contexts:
        key = canonical_uuid(ctx["category_key"], "c9.category_key")
        if key in by_key or ctx["project_id"] != bundle["project_id"] or ctx["publication_id"] != bundle["publication_id"]:
            reject("c9.contexts", "cross-project or duplicate leaf", "snapshot_mismatch")
        nodes = [ctx["direction"]]
        if ctx["branch"] is not None:
            nodes.append(ctx["branch"])
        nodes.append(ctx["hypothesis"])
        pos = []
        neg = []
        for i,node in enumerate(nodes):
            # Ancestors and leaf are included, not flattened into leaf name.
            pos.extend(_node_text(node, f"c9.{key}.path[{i}]"))
            neg.extend(_node_text(node, f"c9.{key}.path[{i}]", excluding=True))
        facts = ctx["approved_facts"]
        if type(facts) is not list or any(type(f) is not dict or
                type(f.get("text")) is not str or not f["text"].strip() for f in facts):
            reject("c9.approved_facts", "broken fact provenance", "snapshot_mismatch")
        pos.extend(f["text"] for f in facts)
        # Empty negatives have null similarity, NEVER use a fabricated zero
        # similarity as evidence that exclusion has been ruled out.
        by_key[key] = {
            "category_key": key,
            "source_node_id": ctx["source_node_id"],
            "direction_id": ctx["direction"]["id"],
            "branch_id": None if ctx["branch"] is None else ctx["branch"]["id"],
            "ancestry_path": list(ctx["ancestry_path"]),
            "context_sha256": ctx["context_sha256"],
            "positive_text": _join(pos,"c9.positive"),
            "exclusion_text": _join(neg,"c9.exclude") if neg else None,
            "approved_fact_ids": [f["id"] for f in facts],
        }
    if set(by_key) != set(expected_keys):
        reject("c9.contexts", "missing/extra hypothesis", "incomplete_coverage")
    return [by_key[k] for k in expected_keys]


def prepare_d1_video_text(verified_content):
    """Use observed D1 text fields only; media segments require actual rights."""
    content = validate_content_input(verified_content)
    data = content["data"]
    parts = []
    for name in ("title", "description"):
        if data[name]:
            parts.append(data[name])
    parts.extend(data["hashtags"])
    for name in ("transcript", "scenes"):
        if data[name]:
            parts.extend(seg["text"] for seg in data[name])
    # Fail closed on oversized D1 media instead of silently ignoring segments
    # and manufacturing an incomplete content SHA meaning.
    return _join(parts,"d1.semantic_text")


def rank_all_leaf_vectors(features, video_vector, positive_vectors,
                          exclusion_vectors, *, expected_hypothesis_keys,
                          model_identity_sha256, content_sha256):
    """Pure mathematical scoring boundary. NOT proof of real inference.

    All features must be the exact approved leaf set; callers never supply
    thresholds, top_k, preselected candidate subset, or fake match labels.
    The production service alone supplies actual M16 vectors.
    """
    sha256_digest(model_identity_sha256, "model_identity_sha256")
    sha256_digest(content_sha256, "content_sha256")
    if (type(features) is not list or not 1<=len(features)<=MAX_LEAVES or
            type(positive_vectors) is not dict or
            type(exclusion_vectors) is not dict):
        reject("ranking.matrix", "invalid all-leaf vector matrix", "incomplete_coverage")
    keys=[]
    for feat in features:
        if type(feat) is not dict:
            reject("ranking.features", "invalid C9 leaf")
        key=canonical_uuid(feat.get("category_key"),"category_key")
        keys.append(key)
        if type(feat.get("direction_id")) is not str or type(feat.get("ancestry_path")) is not list:
            reject("ranking.contexts", "missing full RTB ancestry", "snapshot_mismatch")
        sha256_digest(feat.get("context_sha256"),"context_sha256")
    if len(set(keys))!=len(keys) or keys!=sorted(keys):
        reject("ranking.features","expected exactly sorted, distinct C9 leaves",
               "incomplete_coverage")
    # The production caller MUST supply the full independently verified C9
    # leaf set; dropping a feature cannot be concealed by a self-consistent
    # but smaller set of embedding vectors.
    if (type(expected_hypothesis_keys) is not list or
            expected_hypothesis_keys!=sorted(set(expected_hypothesis_keys)) or
            keys!=expected_hypothesis_keys):
        reject("ranking.coverage","C9 leaf incomplete/extra/mutated",
               "incomplete_coverage")
    negative_keys={x["category_key"] for x in features if x["exclusion_text"] is not None}
    if set(positive_vectors)!=set(keys) or set(exclusion_vectors)!=negative_keys:
        reject("ranking.matrix", "missing or extra positive/exclusion vectors",
               "incomplete_coverage")
    if type(video_vector) not in (tuple,list) or not video_vector:
        reject("ranking.video_vector","missing genuine model vector")
    dims=len(video_vector)
    normalized=normalize_real_vectors(
        [video_vector]+[positive_vectors[k] for k in keys]+
        [exclusion_vectors[k] for k in sorted(negative_keys)],
        dimensions=dims,
    )
    v=normalized[0]
    pos={key:normalized[1+i] for i,key in enumerate(keys)}
    neg={key:normalized[1+len(keys)+i] for i,key in enumerate(sorted(negative_keys))}
    scores=[]
    for feat in features:
        key=feat["category_key"]
        good=cosine_similarity(v,pos[key])
        bad=cosine_similarity(v,neg[key]) if key in neg else None
        # Exclusions are an independent signal, not absolute disqualifiers:
        # only positive *evidence of exclusion* applies penalty.
        penalty=EXCLUSION_PENALTY*max(0.0,bad) if bad is not None else 0.0
        net=good-penalty
        if not isfinite(net):
            reject("ranking.scores","non-finite rank","local_model_blocked")
        scores.append({
            "category_key":key,
            "source_node_id":feat["source_node_id"],
            "direction_id":feat["direction_id"],
            "branch_id":feat["branch_id"],
            "ancestry_path":list(feat["ancestry_path"]),
            "context_sha256":feat["context_sha256"],
            "positive_similarity":good,
            "exclusion_similarity":bad,
            "exclusion_penalty":penalty,
            "rank_score":net,
            "is_candidate":True,
            "classification_state":"not_evaluated",
        })
    scores.sort(key=lambda row:(-row["rank_score"],row["category_key"]))
    for rank,item in enumerate(scores,1):
        item["rank"]=rank
    if len(scores)!=len(features) or {x["category_key"] for x in scores}!=set(keys):
        reject("ranking.coverage","lost approved leaf","incomplete_coverage")
    payload={
        "schema_version":RANK_VERSION,
        "model_identity_sha256":model_identity_sha256,
        "content_sha256":content_sha256,
        "expected_leaf_count":len(keys),
        "candidate_count":len(scores),
        "dropped_count":0,
        "ranking_only":True,
        "candidates":scores,
    }
    payload["ranking_sha256"]=fingerprint(payload)
    return payload


def _embed_one_or_more(backend, texts):
    """Chunk genuine M16 inference, validate each response's identity and SHA."""
    if type(backend) is not LocalONNXEmbeddingBackend:
        reject("embedding.backend","only verified local M16 ONNX backend is allowed",
               "local_model_blocked")
    outputs=[]
    for at in range(0,len(texts),MAX_TEXTS):
        batch=texts[at:at+MAX_TEXTS]
        out=backend.embed(batch)
        if (type(out) is not dict or out.get("real_model") is not True or
                out.get("source")!="local_cpu_onnx" or
                out.get("model_identity_sha256")!=backend.model_identity_sha256 or
                out.get("input_sha256")!=fingerprint(batch) or
                type(out.get("vectors")) not in (list,tuple) or
                len(out["vectors"])!=len(batch)):
            reject("embedding.output","unverified or incomplete real ONNX inference",
                   "local_model_blocked")
        if out.get("vector_sha256")!=fingerprint([list(v) for v in out["vectors"]]):
            reject("embedding.output","vector SHA does not match inference",
                   "local_model_blocked")
        outputs.extend(out["vectors"])
    if len(outputs)!=len(texts):
        reject("embedding.output","missing inference vectors","incomplete_coverage")
    return outputs


def _rank_inputs(c9, content):
    features=prepare_c9_leaf_features(c9)
    text=prepare_d1_video_text(content)
    inputs=[text]+[f["positive_text"] for f in features]
    inputs.extend(f["exclusion_text"] for f in features if f["exclusion_text"] is not None)
    return features,inputs


def _rank_vectors(c9,content,features,input_texts,vectors,model_identity_sha256):
    """Pure scores over complete vectors; not proof of a real semantic model.

    Production M17 gets vectors only from SHA-verified M16; M18 may reuse
    previously validated, immutable cached model vectors after fresh D1 gates.
    """
    neg_keys=[f["category_key"] for f in features if f["exclusion_text"] is not None]
    if type(vectors) not in (tuple,list) or len(vectors)!=1+len(features)+len(neg_keys):
        reject("embedding.output","missing exclusion/positive/video vectors",
               "incomplete_coverage")
    positives={f["category_key"]:vectors[i+1] for i,f in enumerate(features)}
    negatives={key:vectors[1+len(features)+i] for i,key in enumerate(neg_keys)}
    ranking=rank_all_leaf_vectors(
        features,vectors[0],positives,negatives,
        expected_hypothesis_keys=c9["hypothesis_keys"],
        model_identity_sha256=model_identity_sha256,
        content_sha256=content["content_sha256"],
    )
    ranking.update({
        "project_id":c9["project_id"],
        "publication_id":c9["publication_id"],
        "c9_bundle_sha256":c9["bundle_sha256"],
        "contexts_sha256":fingerprint(c9["contexts"]),
        "video_id":content["video_id"],
        "content_version_id":content["content_version_id"],
        "observed_completeness":content["completeness"],
        "embedding_input_sha256":fingerprint(input_texts),
        "dispatch_allowed":False,
        "paid_calls":0,
    })
    ranking["ranking_sha256"]=fingerprint({
        k:v for k,v in ranking.items() if k!="ranking_sha256"
    })
    return ranking


def _rank_verified(backend, c9, content):
    """No public injection of a numerical mock as an ONNX runtime."""
    features,inputs=_rank_inputs(c9,content)
    vectors=_embed_one_or_more(backend,inputs)
    return _rank_vectors(c9,content,features,inputs,vectors,
                         backend.model_identity_sha256)

def _authorize_live_rank_inputs(conn, project, run_id, item_id):
    """Real DB S2/C9 + S3/D1 authorization; caller owns transaction."""
    from .taxonomy_adapter import read_authorized_taxonomy
    from .ports.approved_c9 import build_c9_bundle
    from .live_content_gate import read_live_content_gate

    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    row=conn.execute(
        """SELECT r.state,r.cancel_requested,r.manifest,r.manifest_sha256,
                  r.publication_id::text,r.taxonomy_version_id,r.taxonomy_sha256,
                  r.pipeline_config_id,r.pipeline_sha256,r.execution_mode,
                  i.video_id,i.content_version_id,i.content_sha256,
                  i.source_id,i.source_policy_version_id,i.discovery_id,
                  i.metadata_observation_id,i.contexts_sha256,
                  i.expected_hypothesis_keys,i.manifest_sha256,i.state
           FROM sre_live_runs r JOIN sre_live_items i
             ON i.run_id=r.run_id AND i.project_id=r.project_id
           WHERE r.project_id=%s AND r.run_id=%s::uuid AND i.id=%s""",
        (pid,rid,iid),
    ).fetchone()
    if row is None or row[9]!="live":
        reject("run_item","LIVE item not available to project","rights_denied")
    (state,cancelled,manifest,manifest_sha,pub,tax_version,tax_sha,
     pipeline_id,pipeline_sha,_,video_id,content_id,content_sha,
     source_id,policy_id,discovery_id,observation_id,contexts_sha,
     expected_keys,item_manifest_sha,item_state)=row
    # Local retrieval is allowed before paid consent, but never during a
    # rights/budget hold, cancellation, UNKNOWN or terminal settlement.
    # A paused run needs explicit audited re-authorization before resuming.
    if (cancelled or state not in ("planned","pending_consent","queued","running") or
            item_state not in ("not_evaluated","queued","running")):
        reject("run_item","inactive, paused, uncertain or terminal LIVE work",
               "invalid_transition")
    if (type(manifest) is not dict or manifest.get("schema_version")!="sre-live-plan/v1"
            or fingerprint(manifest)!=manifest_sha or
            item_manifest_sha!=manifest_sha or
            manifest.get("project_id")!=pid or manifest.get("publication_id")!=pub or
            manifest.get("taxonomy_version_id")!=tax_version or
            manifest.get("taxonomy_sha256")!=tax_sha or
            manifest.get("pipeline_config_id")!=pipeline_id or
            manifest.get("pipeline_sha256")!=pipeline_sha or
            manifest.get("contexts_sha256")!=contexts_sha or
            type(expected_keys) is not list or
            expected_keys!=manifest.get("expected_hypothesis_keys")):
        reject("run_item","SRE run/item input pins drifted","snapshot_mismatch")
    approved=build_c9_bundle(read_authorized_taxonomy(conn,pid,pub))
    if (approved["taxonomy_sha256"]!=tax_sha or
            approved["sre_taxonomy_version_id"]!=tax_version or
            approved["bundle_sha256"]!=manifest.get("c9_bundle_sha256") or
            fingerprint(approved["contexts"])!=contexts_sha or
            approved["hypothesis_keys"]!=expected_keys):
        reject("approved_c9","live C9 publication context drift","snapshot_mismatch")
    gate=read_live_content_gate(
        conn,pid,content_id,phase="before_offline_compute",
        pins={
            "video_id":video_id,"content_version_id":content_id,
            "content_sha256":content_sha,"source_id":source_id,
            "source_policy_version_id":policy_id,"discovery_id":discovery_id,
            "metadata_observation_id":observation_id,
        },external_llm=False,
    )
    content=gate["content"]
    pinned_items=manifest.get("items")
    if (type(pinned_items) is not list or
            content["video_id"]!=video_id or
            content["content_sha256"]!=content_sha or
            not any(type(entry) is dict and
                    entry.get("video_id")==video_id and
                    entry.get("content_version_id")==content_id and
                    entry.get("content_sha256")==content_sha and
                    entry.get("contexts_sha256")==contexts_sha and
                    entry.get("source_id")==source_id and
                    entry.get("source_policy_version_id")==policy_id and
                    entry.get("discovery_id")==discovery_id and
                    entry.get("metadata_observation_id")==observation_id
                    for entry in pinned_items)):
        reject("run_item","current D1 media does not match sealed version",
               "snapshot_mismatch")
    return approved,content,gate,rid,iid


def rank_live_run_item(conn, project, run_id, item_id, *,
                       model_directory, model_pins):
    """Trusted C9/D1 read-first, then genuine local ONNX; never paid."""
    approved,content,gate,rid,iid=_authorize_live_rank_inputs(
        conn,project,run_id,item_id,
    )
    # Do not load model until all relevant DB source-rights/C9 checks passed.
    backend=load_local_embedding_backend(model_directory,model_pins)
    result=_rank_verified(backend,approved,content)
    result.update({"run_id":rid,"item_id":iid,"version_pin_sha256":gate["version_pin_sha256"]})
    result["ranking_sha256"]=fingerprint({k:v for k,v in result.items() if k!="ranking_sha256"})
    return result


__all__=(
    "RANK_VERSION","EXCLUSION_PENALTY",
    "prepare_c9_leaf_features","prepare_d1_video_text",
    "rank_all_leaf_vectors","rank_live_run_item",
)
