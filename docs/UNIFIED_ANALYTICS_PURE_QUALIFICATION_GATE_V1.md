# UA-MOD-03 — Pure Qualification Gate (standalone UAE)

**Scope:** deterministic, stdlib-only decision code in intelligence/unified_analytics/qualification.py; test regression in tests/test_unified_analytics_qualification.py. This is NOT a live cross-module join, API, database migration, Group C scorer or finished read-side pipeline.

**Owner:** feature/unified-analytics-full-module. **Historical read-only references:** PR #23 intelligence/analytics/contracts.py and tests/test_analytics_engine.py (at legacy SHA 12921968ef3d5c6f5e706fd8e625c54733405f3b).

## 1. Public API and trust model

| Public symbol | Purpose | Invariant |
|---|---|---|
| validate_request | Port legacy 32-lowerhex project, positive non-bool video, 8–180-char immutable key | Validation ONLY: no queue or dedup write |
| canonical_gate_hash | Legacy-compatible Decimal, UTC-aware datetime, date, UUID, JSON serialization | Reject NaN, Infinity, arbitrary types, non-string keys and naive times; SHA is not signature |
| QualificationObservation | Typed, immutable claim of an adapter-checked current row + exact EvidenceRef + payload fingerprint | A caller can fake checked=True; NEVER expose as untrusted HTTP/user input |
| QualificationInput | Exact InputScope, UTC as_of and observation map | No implicit clock and no DB read |
| evaluate_qualification | Pure conditional decision with statuses, blockers, per-port readiness and input/decision hashes | Output qualified is valid ONLY CONDITIONAL on verified trustworthy upstream observations |

All actual DB connections and rights checks are future UA-MOD-05..13 work; at stage 03 there are no imported peer implementations, providers, rescoring, SQL or background jobs.

## 2. Seven mandatory sources

- **source_policy:** Source enabled, same exact source/policy version, both source and policy explicitly allow can_store_metadata/can_analyze_media/can_store_metrics/can_derive_scores, current valid retention, not revoked and optional expiry strictly later than as_of. Source rights not known is never granted.
- **d1:** Exact ContentMachine project ID, canonical videos.id and source/content kind, confirmed project membership and content SHA; metric persisted, matching video/source with quality=ok, valid retention, nonempty source metric definition/version.
- **qdp:** Verified actual qualified membership with matching project/video/source/source-policy-version/QDP run/preparation SHA. A handoff ACK, ready flag or request to analyze is NOT a completed item.
- **sre:** LIVE completed SRE run, effective approved current positive match, no supersession, run UUID, explicit taxonomy version and digest/content version, D1 content hash and matching source policy version. Historical offline_fixture results are never positive.
- **group_a:** Completed pinned analysis ID/project-scope facts, matching QDP preparation digest, media manifest+snapshot digests and verified media; video duration/scenes/speech/screen-text/subtitle truth or carousel slides/text coverage.
- **group_b:** Completed reviewed (needs_review=False) B analysis, exact A analysis ID, A snapshot hash, media manifest hash, QDP digest and content type; B1/B2 inferred classifications, B3 story reviewed, B4 visual bool truth, B5 editing cut count and B6 subtitle type for videos, B7 CTA coverage, B8 reviewed topic and matching taxonomy version.
- **group_c:** Persisted calculated metric ID from Group C only, COMPLETE score_status and finite raw numeric score (ZERO and scores >100 are legal); exact D1 metric/snapshot identity and version plus explicitly approved config/cohort assertions. UAE never computes C1–C7.

The image_post kind is rejected until Group B publishes a supported B2 contract; photo_carousel/mixed_carousel use the carousel coverage checks. No null/not_run/unknown becomes False or 0.

## 3. Evidence state and hard-block precedence

ObservationState is complete / partial / missing / unavailable / revoked. For complete/partial records a port-matching EvidenceRef, version and facts digest are mandatory; missing/unavailable/revoked records have only a typed reason and no reference or checked/current claim.

A complete/checked/current record is an **adapter ASSERTION**, NOT proof of authenticity or production readiness. All future real adapters must construct assertions from source-authoritative DB reads, rechecking policy and provenance in the transaction. An EvidenceRead.located from UA-MOD-02 does NOT grant this privilege.

Status precedence:
1. **blocked:** unknown/revoked/stale/unavailable source-policy, D1 project membership, QDP actual membership; expired/denied rights; source/metric identity or retention failure; a non-LIVE/superseded/unapproved SRE decision if SRE facts are present.
2. **partial:** any remaining unavailable/incomplete A/B/C or missing SRE; partial is diagnostics only, never eligible for ranking or Patterns.
3. **qualified:** all seven current/checked/complete observations available and every semantic condition passes. This outcome is **conditional on future real adapters having genuinely verified the inputs**.

readiness is an explicit string per port: ready / missing / unavailable / revoked / unverified / incomplete. Empty facts in an apparently complete checked record do not qualify. A syntactically complete record with a semantic blocker cannot have readiness=ready.

## 4. Lineage and deterministic fingerprints

- scope_sha256 includes project/video/source/policy/content kind/QDP run/preparation.
- inputs_sha256 includes explicit as_of UTC, all required port states, checked/current flags, reason codes and the full lineage_sha256 of every evidence reference (which includes producer schema/version and its SHA256 facts fingerprint).
- decision_sha256 includes schema version, status, blockers, detailed readiness, scope and inputs SHA. Two identical snapshots yield identical decisions; different C score values/sources produce different fingerprints even if both states remain qualified.
- No hash constitutes a cryptographic producer signature, current rights check or proof that any remote module actually ran.

## 5. Legacy parity and changes

Historical positive licensed-source video from PR #23 is reused as a fixture, with explicit new QDP, SRE, A/B manifest/parent, C persistence/approval, D1 content/media and policy version/expiry pins added. Mutations test legacy rights revocation, disabled source policy, incomplete A scene coverage, B taxonomy/A parent mismatch, unresolved B7 CTA, unapproved relevance and noncomplete C score.

**Preserved:** three statuses, compatible positive A/B/C result, source rights blocked, A/B/C incomplete partial, strict request validation, deterministic canonical hashing and raw C score with NO normalization.

**Intentional security corrections:** old gate did not check QDP membership, SRE LIVE origin/current supersession, A/B/C version lineage, current rights/retention and explicit source approval. The new gate checks these. Old readiness booleans are replaced by stateful strings; old image_post special-case becomes unsupported InputScope. Old canonical serializer stringified arbitrary JSON keys and any datetime offset; new serializer rejects non-strings and normalizes timezone-aware timestamps to UTC. canonical_gate_hash is separate from the existing strict JSON-only contracts.canonical_hash.

**Remaining limitations:** peer B/C UAE output wire versions not yet frozen, no authoritative DB reader/human-approval verification, no multi-source transactional join, no PG17 E2E, no user API. A fixture with checked=True may make the pure gate conditionally qualified; that is NOT a production qualification result.

## 6. QA command and next stage

Run locally or in GitHub Actions:
- python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
- python -m compileall -q intelligence/unified_analytics

Tests include positive video/carousel, legacy vector mutations, null and empty evidence, revoked/expired current rights, QDP/SRE spoofed provenance, wrong A/B/C versions, C finite zero and raw >100, malformed B stories and digest uniqueness.

**Next:** UA-MOD-04 UAE-owned additive SQL migrations, after collision-free fresh audit of peer numbers/names. Do NOT copy legacy analytics migration numbers 016/017/018 or Group C scoring/approval ownership.
