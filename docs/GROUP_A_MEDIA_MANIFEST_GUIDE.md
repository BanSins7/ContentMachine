# GA-MOD-05 — Verified Media Manifest and Input Resolver

**Implementation:** standalone module `feature/group-a-full-module`.
**Runtime:** explicit opt-in only. QDP dispatcher, FFmpeg extractor pipeline,
ASR/OCR and host activation remain disabled.
**QA:** static source/branch audit **PASS**; committed unit + disposable
PostgreSQL17 tests **QUEUED/NOT EXECUTED**.

## 1. Ownership and explicit inputs

D1 (main's `001_foundation.sql`) is the only owner of `media_assets`.
A media row contains `id, video_id, source_id, media_kind, local_path,
sha256, permission, expires_at`. It does **not** include a byte count,
validated codec/duration, carousel slide index or secure local-path token.
GA never fabricates those DB columns or imports QDP/PSI private Python.

`intelligence/group_a/adapters/media_assets.py::VerifiedMediaCatalog`
requires a same-transaction PostgreSQL connection, explicit authorized
local roots and exact version `ga-media-manifest/v1`. The adapter reads:
- QDP prepared Group A snapshot `qdp_preparations`, selected
  `qdp_enrichments.media_asset_id` and valid `qdp_items`;
- canonical D1 `videos.content_kind`, source and active matching
  `source_policy_versions` with true `can_analyze_media`;
- every D1 asset's exact `video_id`, `source_id`, `media_kind`,
  recorded SHA, rights `permission='analysis_allowed'` and expiry.

Records are locked `FOR SHARE` for the caller's QDP inbox transaction.
An unnegotiated/missing/incompatible peer is unavailable; no HTTP/media
download or schema creation is triggered. `GroupAInboxReceiver` validates
the source/policy/QDP selected asset again before accepting the manifest.

## 2. Media kind and ordering matrix

| Canonical content_kind | Media manifest | Position/time rules |
|---|---|---|
| `video` | exactly 1 video | Real video timebase later by FFprobe |
| `slideshow_video` | exactly 1 encoded video | Slides exist within video frames, not individual invented swipe durations |
| `image_post` | exactly 1 image | Never assign `duration_sec` |
| `photo_carousel` | 2..100 ordered image assets | Explicit trusted ordered asset list required |
| `mixed_carousel` | 2..100 ordered image/video assets, at least one of each | Explicit order; video time is local to each video slide |

For carousel types, `ordered_selector(conn, pointer, selected_id,
content_kind)` MUST return an ordered `list[int]` based on a trusted
upstream carousel item manifest; QDP's single selected asset must be in
that list. Absence of that authoritative order fails with
`carousel_order_unavailable`. Sorting D1 asset IDs, parsing a source
URL, using UI order without provenance, or creating a single synthesized
video from multiple images is **forbidden**. Byte hashes of every slide
are independently checked and the original positions are retained.

D1 does not supply measured duration/timebase. GA-MOD-05 deliberately
does not guess one. Optional exact `duration_sec` strings are supported
by the pure verifier only when supplied from actual upstream measurement.
GA-MOD-06 handles FFprobe timestamps; A4 item video times remain local
to that slide. Image items never receive duration.

## 3. Byte provenance, file safety, and revalidation

`intelligence/group_a/media_manifest.py` implements:

- Explicit configured **absolute local root allowlist**. Remote URI,
  relative path, traversal `..`, symlink file and symlink directory
  components are rejected. Unapproved outside-root files are rejected.
- Linux/macOS directory-relative file descriptors opened using
  `O_NOFOLLOW` per path component; nonblocking reads avoid FIFO hangs.
  On Windows, the more conservative fallback rejects symlinks before/after
  opening and compares opened inode/file metadata; Windows race behaviour
  requires its own CI verification during GA-MOD-23.
- `fstat` regular-file check, exact bytes, maximum 2 GiB and a bounded
  1 MiB streaming SHA-256 loop. Hash must match the existing D1 SHA.
  Optional exact expected byte count must match. Read-before/read-after
  inode/device, size, ctime/mtime identity detects mutation during digest.
- Signature/magic vs extension **screening** for MP4/MOV, WebM/MKV,
  PNG, JPEG, GIF, WebP, HEIF/HEIC/AVIF; filenames are never treated as
  proof of actual media format. Magic is NOT a complete codec or malware
  validation; actual video decode will be checked in GA-MOD-06.
- Existing A1 `extractor.authorized_file` is reused as an **additional**
  video authorization gate without allowing wider video extensions.
- A full SHA-bound, opaque `verified_local_path_ref` (e.g.,
  `ga-local:a101-<64hex>`) appears in the wire DTO. The absolute path
  and filesystem device/inode never leave the internal verifier.
- `open_verified_ref(ref)` returns an **already opened file descriptor**,
  after rechecking SHA/size/inode/time. A verifier cannot guarantee
  a separate consumer's later pathname open is race-free: future workers
  must use the verified descriptor or revalidate immediately before/after
  processing, and must recheck QDP rights independently.

Every `read_verified_manifest` invalidates previous in-memory refs
before trying new authorization. If the file disappears, bytes change or
rights expire, no stale handle survives a failed read.

A manifest uses the already frozen `ga-media-manifest/v1` contract:
project, QDP run, D1 video, prep SHA, content kind, source/policy, all
ordered real items, observed SHA and size, a UTC `verified_at`, then the
strict canonical JSON `manifest_sha256`. For a fixed verification time
and identical contents, the hash is deterministic. New independent
verification times produce new immutable observations, which should not
be confused with a changed media SHA. Event-level retries reuse the
persisted GA inbox/run for exact same event ID.

## 4. Explicit host injection

```python
from intelligence.group_a import GroupAService, GASettings
from intelligence.group_a.adapters.qdp import PostgresQDPReader

service = GroupAService(settings=GASettings(
    enabled=True, qdp_handoff_enabled=True
))
authorized_roots = ["/var/lib/contentmachine/media"]

receiver = service.make_qdp_receiver(
    connect=connect,  # injected host PostgreSQL connection factory
    qdp_factory=lambda conn: PostgresQDPReader(
        conn, observed_version="qdp-handoff/v1"
    ),
    media_factory=lambda conn: service.make_verified_media_catalog(
        conn, roots=authorized_roots,
        ordered_selector=trusted_carousel_item_reader,
    ),
)
# Only after authentic QDP outbox event + rights:
ack = receiver.receive(qdp_envelope, qdp_payload_sha256)
```

Names `connect`, `trusted_carousel_item_reader`, `qdp_envelope`
and `qdp_payload_sha256` are host-supplied; Group A does not invent
their implementations. In standalone mode, none of them are auto-wired.
For video-only installations a carousel selector may be omitted (which
makes carousel intake explicitly unavailable).

The ACK is still only a *durable intake receipt*, not a completed analysis.
External models, media acquisition, workers and API routes remain off.

## 5. Adversarial test inventory and outstanding QA

`tests/test_group_a_media_manifest.py` covers one video/image, photo and
mixed carousel order, full bytes/SHA, strict byte size, saved descriptor
reopen, replacement with identical bytes but a different inode, changed
bytes, symlink and traversal, out-of-root file, invalid extension/magic,
missing file, empty file and FIFO nonblocking rejection.

`tests/test_group_a_media_catalog.py` checks public-D1-style SQL queries
with a fake SQL connection: no fake carousel order, source/policy revocation,
expiration, foreign video, incorrect D1 hash, stale ref invalidation and
missing capability negotiation.

`tests/test_group_a_handoff.py` now includes a disposable PostgreSQL17
fixture that modifies an actual D1 `media_assets` record to a locally
written file, routes it through the D1 catalog and QDP receiver, and
asserts byte mutation and source revocation cannot create another receipt.

GitHub connector static audit confirmed GA-only diff, file descriptor
path hardening, SHA streaming, A1 compatibility, authoritatively selected
carousel order, D1 rights and presence of all new tests: **STATIC PASS**.
The Python unit/PG17 checks themselves have **not executed** because
GitHub Actions has remained queued. Do not declare test PASS or media
release readiness until executable CI reports a result.
