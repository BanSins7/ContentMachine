# Group B — GB-MOD-25 Analytics Adapter & Contract Handoff

**Scope:** read-only Group B producer-side exact-version handoff to Unified Analytics Engine (UAE) and compatibility audit. **Implementation on `feature/group-b-full-module` ONLY**. No edits to UAE, Group A, Group C, `main`, SQL005 or other branches. No paid model calls, Analytics score fabrication, or Analytics DB writes.

## Real peer interfaces inspected

Read-only comparison against:
- `feature/unified-analytics-full-module`, `intelligence/unified_analytics/group_b_adapter.py`, actual peer `GroupBExactReader` using `PORT_SCHEMAS["group_b"]="uae-group-b-reader/v1"`, local `B_READER_WIRE="uae-group-b-exact/v1"`, `GroupBExactPins`, `EvidenceRead`, and `read_completed_analysis(scope)`. Its reader validates same project/video/source/policy; current QDP B preparation and event receipt; exact A ID/SHA/media manifest; every applicable SQL005 component; B1–B8 state; provenance, timestamp, taxonomy and original rights; video B5/B6 applicable and carousel B5/B6 explicitly nonapplicable. **A located EvidenceRead is still NOT UAE qualification**.
- `feature/unified-analytics-full-module`, `intelligence/unified_analytics/qualification.py`: separate seven-port `source_policy,d1,qdp,sre,group_a,group_b,group_c` authoritative qualification, never just B. Group C cannot be fabricated by Group B.
- `feature/analytics-engine-qualified-pipeline-20261023`, `intelligence/analytics/service.py::_b_payload`: existing typed B projection keys `hook,hook_parts,format,story,visual,editing,subtitles,cta_summary,ctas,topic,analysis_version,taxonomy_version`; legacy `_a_and_b` independently joins latest completed B but lacks modern Group B invalidation check. Treat this as a **legacy compatibility reference only**, not a verified current eligibility source.

## Actual GB25 implementation

`intelligence/group_b/integration/analytics_contract.py` defines:
- `UAECompatibility` explicit immutable consumer declaration: expected adapter and reader schemas; support for GB25, stale invalidation, human review, authentic QDP receipts and explicit carousel nonapplicability. A hash is for integrity, **not a signature**. `verify_uae_consumer_capability` on the external trusted host must authenticate the deployed UAE consumer before any payload is exposed.
- `validate_snapshot` pure structural check: exact 8 B block states (video all B1–B8 `complete`; carousel B5/B6 only `not_run`/`unavailable` with no invented digest or claim), nonunknown B1/B2/B7/B8, original B3 roles/slots, video B5/B6 measured facts, source-lineage/proof/per-class quality and tenant/policy/QDP mismatches. A pure valid result is **`candidate` only**, not a runtime attestation.
- `HandoffDecision`: `blocked` or `candidate`, reason codes, exact analysis ID, optional SHA-backed payload. `uae_qualified=False` always. No method here can mark Group C/Unified Analytics `qualified`.

`intelligence/group_b/integration/analytics.py` defines:
- `probe_uae_relations` for SQL005 + Group B runtime 055/056 relation availability, without creating any peer tables.
- `read_uae_handoff(conn,project_id,run_id,current,compatibility,verifier)` under a shared/run-scoped transaction. Reuses GB24 current QDP/GA/media/source-policy/review attestation, checks original canonical `videos.content_kind`, actual video vs carousel A foreign key (same A version in `group_b_runs` AND `content_b_analyses`), exact source and policy and four current rights, QDP accepted inbox + immutable `group_b_inbox_receipts`, unique original signed event, full analysis state and exact version/method metadata, no correction/previous invalidation. Current installed peer UAE-reader v1 blocks any review event, so even gold-only annotation prevents an old-wire handoff pending explicit peer compatibility update.
- Every applicable B1–B8 SQL005 result is reread: hooks and ordered parts; video vs carousel format; original B3 story roles and sampling; B4 visual fields; B5 deterministic editing; B6 subtitles; complete B7 `content_b_cta_summary` and ordered CTA rows (zero means false **only after** the actual full scan); B8 exact approved taxonomy category/topic/optional subtopic with no merge or invalid parent. Carousel forbids video-only rows. Model text/labels are not exposed to an unapproved consumer.
- `verify_uae_exact_source` rechecks authentic per-component/source/GA/media/receipt proof and caller permissions; `verify_uae_quality_gate` verifies independent actual per-class gold quality; both callbacks are mandatory and must return the exact boolean `True`. Missing capabilities or any partial/review/changed/expired proof => **blocked with no sensitive payload**.
- On success, returns a deterministic SHA-256-bound `features_b` projection compatible in keys with legacy `_b_payload` plus project/run/analysis/Group A/QDP/config/current-rights lineage. **Group A original-media manifest SHA and Group B observation manifest SHA are separate fields** and are not equated; the trusted host must verify both exact source originals. A relation-presence probe checks actual SQL005 + Group B 055/056 tables/views before a live read. It explicitly sets `uae_qualification_status="not_evaluated"`, `group_c_qualification_status="not_evaluated"`; consumer must still evaluate C, SRE, D1, QDP, policy, stored snapshots and causal-safe aggregation in its **own** pipeline. No delivery or snapshot publication happens in this branch.

## Required changes in OTHER branches BEFORE end-to-end UAE integration

**These are identified; no other branch was edited. They require your authorization or a separate UAE-owner task.**

1. **UAE** `intelligence/unified_analytics/group_b_adapter.py` currently demands `group_b_inbox.event_key == GroupBExactPins.event_id` while 055 permits a non-UUID event key. A true authenticated QDP receipt already stores a separate UUID `group_b_inbox_receipts.event_id`. Harmonize the consumer contract to use the actual receipt's event UUID, not an arbitrary event key; reverify producer `event_id,event_sha256,preparation_sha256`. GB25 deliberately blocks a handoff if these IDs differ.
2. **UAE** currently treats *every* `group_b_review_events` row as unresolved, including `kind=gold_annotation`. GB23 intentionally separates quality-only gold annotations from label-changing `correction`. Peer should distinguish them with a verified immutable chain, while corrections always invalidate the old version.
3. **UAE** reader must itself honor durable `group_b.invalidated.v1` outbox state as well as `run.state` and current source rights, to prevent an old SQL005 `completed` from being accepted after future reconfiguration. The GB25 producer reader does this, but the separately deployed UAE consumer currently only checks run state and review rows. Integrate the explicit source-current gate through the UAE owner.
4. **UAE** reader requires carousel B5/B6 `not_run` or `unavailable` (correct), but GB21/22 currently conservatively block carousel `completed` pending a signed **not applicable** completion protocol. Align these semantics in both module owners before claiming full carousel handoff; do not create fake `subtitle_type=none` or `editing_type=single_take` on images.
5. **Legacy Analytics** `_a_and_b` currently selects the newest completed SQL005 B parent, not the `group_b_runs` currently qualified result and its 055 invalidation/review outbox. It should use an authoritative current-version adapter (or route to UAE) and never accept stale, rights-revoked B rows.
6. **Cross-branch real integration** requires the authentic signed QDP/Group A producer and rights/current-media-byte proof, actual full B1–B8 accepted and PG17-completed source records, a real independent approved gold-set quality gate, and a UAE consumer matching the exact declared version; Group C/UA qualification stays **strictly external**.

## Tests and QA

`tests/test_group_b_analytics.py`: synthetic positive **candidate-only** video/carousel constraints, no absent B5/B6, unknown/partial B1/B2/B6/B8, no fabricated CTA negative, changed A/QDP/policy, review/invalidation, incompatible consumer, missing Group C qualified truth. `tests/test_group_b_analytics_db.py`: optional disposable PG17 tests ensure partial/fake source or cross-project scenarios never leak a candidate, never mark UAE-qualified and never write Analytics data. Included in Linux/Windows/PG17 workflow.

```bash
python -m unittest discover -s tests -p 'test_group_b_analytics.py' -v
CM_TEST_PG_DSN='postgresql://.../cm_group_b_test' python -m unittest discover -s tests -p 'test_group_b_analytics_db.py' -v
```

**Implemented != passed:** all tests committed, **not locally executed**, no green Actions/PG17 observed as of this stage. No authentic seven-port UAE+Group C E2E success is claimed. This stage is **integration-prepared with explicitly blocked/identified peer interface differences**, not production activation.
