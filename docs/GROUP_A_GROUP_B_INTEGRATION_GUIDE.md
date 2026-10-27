# GA-MOD-19 — Group A → Group B exact consumer handshake

**Status:** IMPLEMENTED in `feature/group-a-full-module` with QA independently pending. This is a producer/outbox adapter **inside Group A only**. Group B implementation code, Group B SQL005, QDP, Unified Analytics and `main` are not modified.

**Peer code read for compatibility, not changed:** `feature/group-b-full-module` @ `0407f62eb25934c291d719282e1008b89068aef1`, specifically `intelligence/group_b/adapters/group_a.py::read_exact_a`, `intelligence/group_b/lineage.py::bind_exact_group_a`, `docs/GROUP_B_A_HANDSHAKE.md`. Integration acceptance and real cross-branch PG17 run are NOT VERIFIED until both branches/migrations are intentionally co-installed.

## Exact contract and truth

The public frozen envelope is **exactly** `group-a-to-b/v1`, as declared in Group A's existing `contracts_v1.py::group_a_to_b_v1` and independently checked by Group B's adapter. Its ONLY fields:

```text
schema_version, project_id, qdp_run_id, video_id,
qdp_input_sha256, manifest_sha256, content_kind,
analysis_state, video_a_analysis_id, carousel_a_analysis_id,
method_version, streams, evidence
```

The A v1 snapshot cannot be changed to add source/policy/CTA/failure details. Source and policy live in canonical `ga_runs` and the exact manifest/GA05 media refs. `video_a_analysis_id` and `carousel_a_analysis_id` are mutually exclusive and are the **actual A003/A004 IDs**, not a random inferred latest row. Group B binds `ga_results.id` **plus** `ga_results.result_sha256`; never a video URL or a draft analysis.

`streams` is the existing exact content-kind matrix, using `status, evidence_count, verified_absence, reason_code`. Positive completed measurements require SHA-pinned source proofs and `evidence_count > 0`; verified-zero completed measurements require an independently sourced SHA-pinned negative proof even though `evidence_count == 0`. No "zero" from a missing extractor, OCR gap, ASR absence of rows, or blind model claim.

Video/slideshow video: `technical` → genuine GA06 full-scene digest, `speech` → **GA10 ASR transcript SHA** (not GA11 speech-metrics SHA; corrected by the GA19 producer), `screen_text` → genuine GA13 OCR tracking digest or independently verified absence certificate, `audio` → GA15 source-bound event digest or certified full-stream absence, `subtitles` → GA14 measured subtitle-track or certified dense-negative review digest. Group B v1 calls subtitle proofs `screen_text` evidence kind; that is a compatibility label, **not** a claim that GA14 found visual OCR text or that Group A classified B6 subtitle style. The GA18 snapshot now stores both `speech_sha256` (GA11 metric provenance) **and** `asr_sha256` (GA10 actual transcript provenance).

Photo carousels: `slides` each uses an immutable canonical slide-position/asset/GA16-result SHA reference, `screen_text` points to genuine GA16 full still-image OCR. Verified fully empty photo OCR is a negative result with one GA16 observation proof. No inferred human swipe duration.

Mixed carousels: authoritative GA05 ordered still/video media pins and exact `video_a_analysis_id` per video item survive in A004. GA17 only observes image-wide OCR plus local video spans; it does **not** establish publication-wide full video text. Its canonical A004 `text_status` remains `partial` even when `state=completed`. The GA19 snapshot consequently has `screen_text.status=partial`, `analysis_state=partial`, and **no Group B readiness outbox event**. Group B's existing `read_exact_a` also requires native A004 `text_status=complete`; it correctly rejects this. Fully ready mixed carousel needs future **authentic full-video text coverage** from the A-side producer (and fully checked A004 integration); this blocker is documented, **not** bypassed with false data. `image_post` is explicitly unsupported by the current Group B consumer; it remains GA16 A004-only and GA19 refuses B publication.

Group B alone owns B1 hook, B2 format, B3 story, B4 visual, B5 editing, B6 subtitle style, B7 CTA classification, B8 topic registry. Group A never calls LLM for those labels, copies B code, stores B rows or creates B7 actions. A003 `cta_start_sec=NULL` and A004 `cta_slide_index=NULL` remain unchanged unless an independently confirmed compatible external read-only projection is implemented. There is no CTA→A→B cycle.

## Producer API and database boundary

```python
from intelligence.group_a import GroupAService

ga = GroupAService()
prepared = ga.prepare_group_b_handoff(
    ga05_authoritative_manifest,
    receipt={
        "ga_run_id": existing_GA_054_run_uuid, # from GA04 durable QDP intake
        "video_analysis_ids": {
            ga05_video_asset_id: ga18_a003_analysis_id,
        },
        "carousel_analysis_id": None,        # or exact GA16/GA17 A004 ID
    },
    video_snapshots=[ga18_sha_bound_snapshot],
    # carousel_observation=precomputed_ga16_or_ga17_snapshot,
    verify_current=host_live_project_qdp_source_asset_rights,
    verify_origin=host_authenticate_ga18_extractor_and_current_media_bytes,
    verify_coverage=host_check_independent_whole_video_certificates,
    # Optional verified original frame/ASR/OCR refs, never arbitrary user data:
    # extra_evidence=..., verify_evidence=...
)

# O(1) frozen wire reference + separate authenticated DB write.
receipt = ga.save_group_b_handoff(
    conn,ga05_authoritative_manifest,prepared,
    video_snapshots=[ga18_sha_bound_snapshot],
    verify_current=host_live_project_qdp_source_asset_rights,
    verify_origin=host_authenticate_ga18_extractor_and_current_media_bytes,
    verify_coverage=host_check_independent_whole_video_certificates,
)
# receipt.ga_result_id + receipt.result_sha256 are the ONLY safe B pins.
# receipt.delivery_state = "pending_outbox" only for completed A.
# partial => "blocked_partial" and NO outbox event.
```

- `adapters/group_b.py`: pure `prepare_group_b_result` + `validate_prepared_handoff`. Current-rights checks on every asset before and after, independent GA18 coverage reauthorization, original GA16/17 slide observation digest, SHA-only evidence manifest (never raw PCM, video, private paths or transcripts), explicit GA54 run ID and A003/A004 PK receipts. Strict JSON canonical SHA of both the frozen B snapshot and the enclosing GA19 preparation, portable string-keyed asset-ID maps. Extra frame/transcript/OCR/audio refs require explicit `verify_evidence` callback bound to the current project/source/asset.
- `adapters/group_b_writer.py`: explicit psycopg transaction against **pre-existing GA054** `ga_runs`/`ga_media_refs` (must come from durable GA04 QDP intake) and immutable A003/A004. `FOR UPDATE` exact run lock; `FOR SHARE` active D1 source/policy/media pins and canonical A003/A004 state/method/SHA/policy/slide ordering. GA18 source-media SHA, snapshot SHA and A003 stream statuses reread live. Rebuilds and compares exact prepared result before and after every SQL mutation. Inserts idempotent, append-only GA054 `ga_results` with `snapshot`, `evidence`, exact result SHA and canonical A003/A004 FK; updates eligible GA run state only after committed result is present.
- For **completed only**, also inserts an immutable `ga_outbox` `group_a.result_ready/v1` payload with exact GA result ID, frozen SHA, policy and manifest. This means `pending`, **NOT delivered or accepted**; GA20 must implement retries/ACK/current-result invalidation and actual Group B transport. Partial results may persist to `ga_results` for audit but have **no** outbox event and never make B eligible.
- Does **not** rename or edit any existing GA 054/055/056 migration. Existing migration filename overlap between GA's `055_group_a_handoff_inbox.sql` and B's `055_group_b_runtime.sql` must be reviewed when both branches join; sorted filenames are distinct, but integration migration ordering and checksums need real PG17 qualification.
- No auto-creation or guessing of project membership, QDP receipts, active GA runs, missing A analyses, human certificates, transcript/slide refs. If GA 054 run or all ordered `ga_media_refs` are absent, writer fails closed. Only the host may supply fresh real verifier callbacks, which must never be the always-true fakes used in tests.

## B consumer contract match

The currently inspected B adapter requires:
- `ga_runs.stage_version == group-a-assembly/v1`, `ga_runs.state=completed` and `ga_results.state=completed`;
- exact `group-a-to-b/v1` shape and `ga_results.result_sha256 == sha256(canonical_json(snapshot))`;
- `ga_results.evidence` = `snapshot.evidence`, exact `method_version`, stream types and complete evidence proofs (including a verified absence);
- original QDP project/run/video/source/preparation SHA, original policy version, current unexpired D1 assets in precise GA05 slide order;
- A003 `media_asset_id`/source/policy/complete native stream states + known subtitles for video;
- A004 exact content kind, ordered slide media refs and `slide_status/text_status=complete` for photo or mixed carousel.

These are producer admission constraints, **not evidence that the other branch has passed a joint runtime test**. The B project/analysis selection remains B-owned. Group B's existing `read_exact_a(..., ga_result_id,expected_sha256)` and `bind_exact_group_a(...)` own the consumer-side transaction and must be independently executed after deliberate merge/reconciliation.

## QA checklist and limitations

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_b_handoff.py
python -m unittest discover -s tests -p 'test_group_a_b_handoff.py' -v
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
# Disposable PG17 DB only, never production:
CM_TEST_PG_DSN='postgresql://.../ga19_test' \
 python -m unittest discover -s tests -p 'test_group_a_b_handoff.py' -v
```

Committed regression tests: exact frozen v1 wire/hash, completed/partial A, false verified absence, real GA10 transcript SHA, unsupported future version/foreign source or policy and revoked rights, validated still-image OCR and photo carousel, mixed-carousel partial text, image_post rejection, unverified frame refs rejected, direct CTA classification absent, optional strict JSON Schema and real disposable PG17 GA054 `ga_results/ga_outbox` pending-only completion, unique replay, rights revocation/missing media SHA, rollback after mutation. Fake independent cert and rights callbacks in these tests are **NOT** production provenance.

**NOT VERIFIED:** Python execution, actual QDP+GA+B shared PostgreSQL 17 migration, real A→B transaction against `feature/group-b-full-module`, Group B B1–B8 model readiness, live provider receipts, Windows/Linux E2E, real independently signed full-video coverage, and real pending outbox dispatch/ACK. No claim of CI PASS until logs are observed.

**Next GA-MOD-20:** worker/runtime DAG and leases; point-of-use revoked source/QDP/media invalidation, current-versus-historical result filtering; pending outbox actual delivery/ACK and idempotent retries. GA21–24: API, benchmarks, joint CI matrix and merge handshake.
