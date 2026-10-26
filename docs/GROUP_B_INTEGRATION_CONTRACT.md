# Group B — Integration Boundary / Freeze v0
**Owner:** `intelligence.group_b`. **Status:** bootstrap; boundary definitions only. No Group B consumer/provider implemented yet.

## External dependencies, NOT copied
- **QDP → B**: `qdp-handoff/v1` pointer on delivery after QDP durable outbox. The exact formal schema is QDP-owned; Group B receives via adapter, validates project, video, input hash and version and only ACKs after durable inbox+job commit.
- **Group A → B**: `group-a-to-b/v1` is under GA development. Must use actual A analysis PK and its version/SHA, source, media manifest, complete-required stream coverage and verifiable frame/word/ocr ref IDs. No implicit free-form ID mapping.
- **Project Source Intelligence:** other source-ID namespace; only reuse output with verified hash mapping to canonical D1 media id and video id.
- **B → Analytics:** main `content_b_analyses` and B1–B8 child tables. Consumer in PR #23 already expects completed B, review=false, appropriate B1/B2/B3/B4/B5(video only)/B6(video only)/B7/B8, exact A analysis/policy version.
- **SRE:** relevance is upstream through QDP, not a B classifier input guess. No B decisions alter SRE classification.

## Proposed bootstrap pointer (NOT QDP provider schema)
`GroupBInputPointer` is GB-owned normalized internal representation and is **not** evidence of compatibility with the upstream schema. Adapter must explicitly translate verified external contracts and preserve original event IDs.

Project ID = 32 lowercase hex, QDP run = UUID, canonical `videos.id` and A analysis PK positive integers, B content_type = `video|carousel`, SHA = 64 hex. Media manifest may contain multiple assets. Never convert an unknown kind to carousel. Same pointer + exact hash idempotent; changed SHA produces distinct version or conflict.

## Sources and trusted evidence
A transcript/ocr text is *data* not a prompt instruction. Frame IDs/slide hashes and timestamps must exist in actual incoming media manifest. For visual conclusions that are model-reported rather than independently verified, preserve confidence/source and human-review status; do not call model's JSON a verified observation.

## B5 approved thresholds
`single_take`: cut_count=0; `slow_cuts`: 0<cuts_per_minute<10; `medium_cuts`: 10<=cuts_per_minute<30; `fast_cuts`: >=30. `avg_shot_duration` belongs to B5; A average scene duration is not a stored A feature.

## Group B access
Do not access protected source results if policy no longer allows analysis, or media expired/revoked. This bootstrap does not implement access rules or live connectors. Fail closed until authoritative DB queries/port capability arrive in GB-MOD-04/05.

## Current tasks
See `docs/GROUP_B_IMPLEMENTATION_TRACKER.md` and full stage plan. DO NOT mark B1-B8 runtime ready just because schema 005 exists.
