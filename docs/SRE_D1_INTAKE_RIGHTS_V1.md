# SRE S3 — Discovery D1 Intake, Source Rights and Metadata Snapshot V1

**Status:** implemented on feature/sre-production-v1-20261023; merge gated by milestone tracker. No OpenRouter calls, no scraping, no automatic attachment of global D1 videos and no change to main.

## Explicit ownership boundary

Discovery D1 remains the ONLY canonical source for videos, discoveries, source policy versions and metadata observations. SRE makes a project-scoped reference to a *specific* trusted D1 discovery. A project is not automatically entitled to the entire global D1 catalog simply because it exists in PostgreSQL. Until S9 adds authenticated localhost routes, S3 exposes an INTERNAL service only; the caller must be the trusted local project owner. A raw remote/client project_id is never proof of authorization.

Functions:

- \`intelligence.relevance.intake.attach_discovery(conn, project_id, discovery_id)\` — explicit, transactionally serialized project association + metadata-only snapshot. Returns a validated \`sre-content-input/v1\` with \`revision\` and \`replayed\`.
- \`intelligence.relevance.intake.read_content(conn, project_id, content_version_id, external_llm=False)\` — fresh read-time D1 rights check; returns an S1 validated envelope or fail-closed \`SREContractError\`.
- \`intelligence.relevance.source_policy.verified_rights(source, policy, external_llm=False)\` — pure source/policy authorization. Parameters MUST be sourced from DB, never trusted from a user payload.
- \`intelligence.relevance.source_policy.expiry_for_intake(source,policy,observation,now=...)\` — earliest of source and policy retention and observation expiration; rejects revoked or expired observations.
- \`intelligence.relevance.content_builder.metadata_payload(observation)\` and \`build_metadata_envelope(...)\` — deterministic true source-owned metadata; no URL fetch and no fabricated hashtags, transcript, scenes or analytics.

## Rights — default deny

A D1 policy is usable by SRE only if ALL are true:
- \`sources.status='enabled'\`, \`sources.can_store_metadata=TRUE\`.
- \`source_policy_versions.is_active=TRUE\`, matching source ID, non-null \`reviewed_at\`.
- Reviewed policy JSON permission booleans \`can_store_metadata=true\` AND \`can_semantic_classify=true\` (actual booleans, not strings).
- Metadata observation belongs to the same video, source AND active policy, \`is_current\`, not deleted and not expired (when observation exists).
- Effective retention is not expired.
- The calling service has explicitly selected a D1 discovery and a registered project workspace.

\`can_external_llm\` is a SEPARATE explicit policy boolean, never implied by metadata storage or semantic classification permission. Even when true, this flag is NOT spending consent: S5 still must require its own explicit quote/consent and S6 must recheck rights immediately before network dispatch.

Existing D1 manual import policy \`permissions={"can_store_metadata":true,"can_store_metrics":false}\` does NOT grant semantics and remains blocked until an actual reviewed source permission is provided. No implicit migration or auto-approval of old sources.

Media processing is NOT implemented by S3: S3 imports only title/description metadata. Hashtags remain empty unless a future authorized D1 producer supplies source-grounded hashtag observations. Transcript/scenes are absent from persisted JSON, even though the S1 normalized return envelope contains null fields. Storage triggers intentionally treat the PRESENT key as a media-derived payload, so no null media keys are written.

## Database migration

\`intelligence/migrations/016_sre_d1_intake_rights.sql\` (additive; prior 001–015 untouched):
- Adds SHA256 content hash, immutable intake fingerprint and exact metadata observation FK to existing \`sre_content_versions\`.
- Adds \`sre_project_discoveries(project_id,video_id,discovery_id)\` with exact D1 FK and per-project video FK. This is a deliberate project selection, not a copied video table.
- Enforces unique per-project/video intake fingerprint for idempotency and indexes observation usage.
- Strengthens the existing \`sre_validate_content_rights\` SQL trigger (semantic permission, reviewed active policy, metadata observation provenance, source/policy retention, media rule).
- Replaces the weaker \`sre_readable_content_versions\` view with a live read-time policy + observation + retention + media rights gate. Revoking source/policy or deleting an observation hides old snapshots immediately; no worker process required.

Older content versions with absent new fields continue to exist; their reads are also governed by stricter policy. S3 does not silently rewrite historical metadata. The migration is checksummed, applied by existing \`intelligence.migrate\` under an advisory transaction lock.

## Snapshot semantics and idempotency

When the trusted local owner explicitly selects a discovery:

1. Lock the existing registered project workspace row (\`FOR UPDATE\`) to serialize per-project attachment and revision number allocation.
2. Read a **real** D1 discovery and current reviewed source policy; validate source-request/run/query lineage for provider requests.
3. Use latest \`source_video_metadata\` observation for THAT SAME source, policy and video, if available. A mismatched/expired observation causes denial rather than fallback to unverified metadata. No observation is allowed for URL-only imports, but global \`videos.title\` is never copied.
4. Verify effective rights and expiry before insert.
5. Link only that video and discovery to project via \`sre_project_videos\` and \`sre_project_discoveries\`.
6. Store exact JSON \`{title,description,hashtags:[]}\`. \`content_sha256\` is the canonical JSON hash of persisted data. \`intake_fingerprint\` separately binds project/video/discovery/source/policy/observation/content hash.
7. Return the previously readable revision on exact retry. A modified observed title/description or new reviewed policy/observation produces a new monotonic revision; historical rows remain unchanged.
8. An expired or revoked previously identical snapshot cannot be resurrected by repeating the old discovery: it needs a genuinely new policy or observation.

A revision's declared S1 content_version_id is the real PostgreSQL-generated ID, not the revision counter. Retention is pinned to the original insert and never extended on retry.

## Read and classification safety

All S3 reads use the strengthened \`sre_readable_content_versions\`, project-scoped by (project_id, content_version_id). The service re-evaluates policy on every read, independently validates the S1 envelope and checks SHA256 of the persisted content JSON. \`external_llm=True\` is denied unless the source policy permits third-party transmission.

WARNING: S3 implements the **read-time revocation gate**, not the final physical-delete/retention purger of all historical content, embeddings, prompts, classification evidence and downstream materializations. Before enabling any paid classification, S4/S11 must implement a comprehensive revocation and retention cleanup workflow for new downstream data; user-facing UI must never promise physical erasure from the read filter alone.

## QA

- \`tests/test_sre_intake_unit.py\`: missing/disabled policy, reviewed status, boolean grants, external gate, source/observation retention, deterministic fingerprint, no metadata guessing.
- \`tests/test_sre_intake_pg.py\`: real PostgreSQL17 metadata snapshots and foreign-project denial, replay, new revision, provider request mismatch, SQL bypass denial including forged JSON string booleans, append-only revision guard, project-discovery unlink invalidation, policy/source revocation, deleted observation, URL-only no-global-title, allowed external provider read without paid execution.
- \`.github/workflows/sre-contracts.yml\`: Linux + Windows S1/S2/S3 offline suites, real PostgreSQL17 S2+S3; existing full repository and Windows workflows unchanged.

Run:
\`\`\`sh
python -m unittest discover -s tests -p test_sre_intake_unit.py -v
CM_TEST_PG_DSN=<disposable_dsn> CM_DB_DSN=<disposable_dsn> python -m unittest discover -s tests -p test_sre_intake_pg.py -v
python -m unittest discover -s tests -v
\`\`\`

## Intentional S3 exclusions and S4 readiness

S3 does not infer video relevance, create classification runs, reserve cost, download videos, extract/transcribe media or call providers. A trusted D1 video and content snapshot are now available to S4's fake-classifier worker once the owner explicitly associates the discovery with the project. Live Collector wiring is deferred to S10. Source rights and user authorization remain prerequisites, not optional metadata.
