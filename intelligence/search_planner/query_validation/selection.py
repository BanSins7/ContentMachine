"""Pure Query Validation: grounded relevance, semantic duplicates, MMR.

Scores are vector-space proxies, not empirically verified video recall.
Every source candidate is selected or rejected with a reason.
"""
from __future__ import annotations
from collections import Counter
from unicodedata import normalize as unicode_normalize
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.search_planner.contracts import validate_search_policy
from .contracts import validate_policy
from .embedding import encode_checked


def normalized(text):
    return " ".join(unicode_normalize("NFKC", text).casefold().split()).lstrip("#")


def cosine(a, b):
    return max(-1., min(1., sum(x*y for x,y in zip(a,b))))


def references(ctx):
    hyp=ctx["hypothesis"]
    positive=[(hyp["name"],1.0),
              ((hyp["name"]+" "+hyp["definition"])[:3000],.94),
              ((hyp["name"]+" "+hyp["include"])[:3000],.90)]
    positive += [(x,.80) for x in hyp["examples"][:10] if x.strip()]
    negative=[n["exclude"][:3000] for n in
              (ctx["direction"],ctx["branch"],hyp) if n and n["exclude"].strip()]
    negative += [n["name"] for n in ctx["neighbor_hypotheses"]]
    return positive,list(dict.fromkeys(negative))


def select_queries(bundle,contexts,policy,embeddings):
    cfg=validate_policy(policy)
    if not isinstance(bundle,dict) or bundle.get("schema_version")!="query_candidates/v1" or bundle.get("status")!="completed":
        raise PipelineError("Only complete query_candidates/v1 can be validated",409)
    candidates=bundle.get("candidates")
    coverage=bundle.get("hypothesis_coverage")
    if not isinstance(candidates,list) or len(candidates)>480 or not isinstance(coverage,list):
        raise PipelineError("Invalid source candidates/coverage",409)
    lookup={c["hypothesis"]["id"]:c for c in contexts}
    if not lookup or len(lookup)!=len(contexts):
        raise PipelineError("Duplicate/missing RTB context",409)
    source_counts=Counter(x["hypothesis_id"] for x in candidates)
    if len(set(x["candidate_id"] for x in candidates))!=len(candidates):
        raise PipelineError("Duplicate source candidate UUID",409)
    if set(x["hypothesis_id"] for x in coverage)!=set(lookup) or len(coverage)!=len(lookup):
        raise PipelineError("Incomplete source hypothesis coverage",409)
    for c in coverage:
        n=source_counts[c["hypothesis_id"]]
        if n!=c["candidate_count"] or ((n==0)!=(c["state"]=="no_viable_queries")):
            raise PipelineError("Contradictory source hypothesis coverage",409)
    for c in candidates:
        ctx=lookup.get(c["hypothesis_id"])
        if ctx is None or c["direction_id"]!=ctx["direction"]["id"] or (
            c["branch_id"]!=(ctx["branch"]["id"] if ctx["branch"] else None)):
            raise PipelineError("Cross-RTB or mismatched candidate lineage",409)
        p=validate_search_policy(ctx["search_policy"])
        if c["language"] not in p["languages"] or c["query_mode"] not in p["allowed_modes"]:
            raise PipelineError("Candidate violates approved search policy",409)
        if not set(c["source_fact_ids"]).issubset({f["id"] for f in ctx["approved_facts"]}):
            raise PipelineError("Unapproved source fact ID",409)
        if not isinstance(c["text"],str) or not c["text"].strip() or len(c["text"])>120:
            raise PipelineError("Invalid source query text",409)
    # Embed unique references in a single bounded call; no implicit fallback.
    texts=[];indices={}
    def index(text):
        if text not in indices:
            indices[text]=len(texts);texts.append(text)
        return indices[text]
    for c in candidates:index(c["text"])
    refs={}
    for hid,ctx in sorted(lookup.items()):
        pos,neg=references(ctx)
        refs[hid]=([(index(t),w) for t,w in pos], [index(t) for t in neg])
    vectors=encode_checked(embeddings,texts) if texts else []
    decisions={}
    for c in sorted(candidates,key=lambda x:x["candidate_id"]):
        vector=vectors[indices[c["text"]]]
        pos,neg=refs[c["hypothesis_id"]]
        sim=max(cosine(vector,vectors[i])*w for i,w in pos)
        bad=max((cosine(vector,vectors[i]) for i in neg),default=-1.)
        margin=sim-bad
        ctx=lookup[c["hypothesis_id"]]
        exclusions=[normalized(n["exclude"]) for n in
            (ctx["direction"],ctx["branch"],ctx["hypothesis"]) if n]
        forbidden=any(len(x)>=8 and x in normalized(c["text"]) for x in exclusions)
        reason=None
        if forbidden:reason="explicit_exclusion"
        elif sim<cfg["min_positive_similarity"]:reason="low_relevance"
        elif margin<cfg["min_positive_negative_margin"] or (
                bad>cfg["max_negative_similarity"] and bad>sim):
            reason="negative_scope"
        decisions[c["candidate_id"]]={
            "candidate_id":c["candidate_id"],"hypothesis_id":c["hypothesis_id"],
            "decision":"rejected" if reason else "eligible",
            "reason_code":reason,"positive_similarity":round(sim,8),
            "negative_similarity":round(bad,8),"score_margin":round(margin,8),
            "mmr_score":None,"selected_rank":None,"duplicate_of":None,
        }
    # High-confidence global dedup across equal locale/mode, stable tie-break.
    eligible=[c for c in candidates if decisions[c["candidate_id"]]["decision"]=="eligible"]
    eligible.sort(key=lambda c:(-decisions[c["candidate_id"]]["positive_similarity"],c["candidate_id"]))
    survivors=[]
    for c in eligible:
        duplicate=None;reason=None
        for other in survivors:
            if (c["language"],c["query_mode"])!=(other["language"],other["query_mode"]):
                continue
            # Local dedup is always safe before per-hypothesis MMR.
            # Cross-hypothesis dedup must wait until AFTER MMR, otherwise
            # a winner later rejected by quota can suppress a valid query
            # for a completely different hypothesis.
            if c["hypothesis_id"]!=other["hypothesis_id"]:
                continue
            if normalized(c["text"])==normalized(other["text"]):
                duplicate,reason=other,"exact_duplicate";break
            if cosine(vectors[indices[c["text"]]],vectors[indices[other["text"]]])>=cfg["semantic_duplicate_similarity"]:
                duplicate,reason=other,"semantic_duplicate";break
        if duplicate is None:
            survivors.append(c)
        else:
            decisions[c["candidate_id"]].update(decision="rejected",reason_code=reason,
                                                 duplicate_of=duplicate["candidate_id"])
    selected=[]
    for hid,ctx in sorted(lookup.items()):
        pool=[c for c in survivors if c["hypothesis_id"]==hid]
        accepted=[];angles=set()
        limit=min(cfg["max_selected_per_hypothesis"],
                  ctx["search_policy"]["target_candidates_per_hypothesis"])
        def mmr(c):
            overlap=max((cosine(vectors[indices[c["text"]]],vectors[indices[x["text"]]])
                         for x in accepted),default=0.)
            return (cfg["mmr_lambda"]*decisions[c["candidate_id"]]["positive_similarity"]
                    -(1-cfg["mmr_lambda"])*overlap
                    +(cfg["angle_diversity_bonus"] if c["angle"] not in angles else 0.))
        while pool and len(accepted)<limit:
            best=min(pool,key=lambda c:(-mmr(c),c["candidate_id"]))
            decision=decisions[best["candidate_id"]]
            decision.update(decision="selected",reason_code="selected",
                            selected_rank=len(accepted)+1,mmr_score=round(mmr(best),8))
            selected.append(best["candidate_id"]);accepted.append(best)
            angles.add(best["angle"]);pool.remove(best)
        for c in pool:
            decisions[c["candidate_id"]].update(decision="rejected",reason_code="quota")
    if cfg["cross_hypothesis_dedup"] and selected:
        # Only physically merge queries that were ACTUALLY selected for their
        # hypothesis. The winner is guaranteed to remain selected. Rejected
        # equivalents preserve original candidate UUID + hypothesis_id in the
        # immutable qv_decisions table, enabling SP-10 task lineage fan-out.
        ordered_selected = sorted(
            (c for c in candidates if decisions[c["candidate_id"]]["decision"]=="selected"),
            key=lambda c:(-decisions[c["candidate_id"]]["positive_similarity"],c["candidate_id"]),
        )
        physical=[]
        for c in ordered_selected:
            alias_of=None
            reason=None
            for first in physical:
                if c["hypothesis_id"]==first["hypothesis_id"]:
                    continue
                if (c["language"],c["query_mode"])!=(first["language"],first["query_mode"]):
                    continue
                if normalized(c["text"])==normalized(first["text"]):
                    alias_of,reason=first,"exact_duplicate"
                    break
                if cosine(vectors[indices[c["text"]]],vectors[indices[first["text"]]])>=cfg["semantic_duplicate_similarity"]:
                    alias_of,reason=first,"semantic_duplicate"
                    break
            if alias_of is None:
                physical.append(c)
            else:
                decisions[c["candidate_id"]].update(
                    decision="rejected", reason_code=reason,
                    duplicate_of=alias_of["candidate_id"],
                    selected_rank=None, mmr_score=None,
                )
        selected = [
            cid for cid in selected if decisions[cid]["decision"]=="selected"
        ]
        # Re-rank after cross-hypothesis merges to preserve contiguous
        # selected ranks and stable QV SQL UNIQUE(run,hypothesis,rank).
        for hid in sorted(lookup):
            kept = [cid for cid in selected if decisions[cid]["hypothesis_id"]==hid]
            for rank, cid in enumerate(kept, 1):
                decisions[cid]["selected_rank"] = rank
    if any(d["decision"]=="eligible" for d in decisions.values()):
        raise AssertionError("Undecided query")
    out_coverage=[]
    for item in sorted(coverage,key=lambda x:x["hypothesis_id"]):
        hid=item["hypothesis_id"]
        keep=sum(decisions[x["candidate_id"]]["decision"]=="selected"
                 for x in candidates if x["hypothesis_id"]==hid)
        if keep:state,reason="selected",None
        elif item["candidate_count"]==0:state,reason="no_source_candidates",item["reason"]
        else:
            shared = any(
                d["hypothesis_id"] == hid and
                d["reason_code"] in ("exact_duplicate","semantic_duplicate") and
                d["duplicate_of"] is not None and
                decisions[d["duplicate_of"]]["decision"] == "selected" and
                decisions[d["duplicate_of"]]["hypothesis_id"] != hid
                for d in decisions.values()
            )
            state,reason="all_rejected",(
                "Equivalent query selected under another hypothesis; source candidate link retained"
                if shared else "No in-scope, unique query passed Query Validation"
            )
        out_coverage.append({"hypothesis_id":hid,"source_count":item["candidate_count"],
                             "selected_count":keep,"outcome":state,"reason":reason})
    if sum(x["selected_count"] for x in out_coverage)!=len(selected):
        raise AssertionError("Inconsistent selected count")
    # Sidecar is informational only. Persistence remains the existing
    # immutable per-candidate QV decisions + duplicate_of foreign key; SP-07
    # does not modify validated_queries/v1 or historical PostgreSQL schema.
    by_id = {c["candidate_id"]:c for c in candidates}
    groups = {
        cid: [{
            "candidate_id": cid, "hypothesis_id": by_id[cid]["hypothesis_id"],
            "source_fact_ids": list(by_id[cid]["source_fact_ids"]),
        }] for cid in selected
    }
    for did in sorted(decisions):
        d = decisions[did]
        if d["duplicate_of"] in groups and d["reason_code"] in (
            "exact_duplicate","semantic_duplicate"
        ):
            source = by_id[did]
            groups[d["duplicate_of"]].append({
                "candidate_id": did,
                "hypothesis_id": source["hypothesis_id"],
                "source_fact_ids": list(source["source_fact_ids"]),
            })
    shared_links = [
        {"selected_candidate_id": cid,
         "linked_sources": sorted(groups[cid], key=lambda x:x["candidate_id"])}
        for cid in sorted(groups)
    ]
    ordered=[decisions[k] for k in sorted(decisions)]
    return {"decisions":ordered,"coverage":out_coverage,"selected_candidate_ids":selected,
            "selected_count":len(selected),"source_candidate_count":len(candidates),
            "reason_counts":dict(sorted(Counter(x["reason_code"] for x in ordered).items())),
            "evaluation_mode":embeddings.mode,"embedding_model_key":embeddings.model_key,
            "shared_query_links":shared_links}
