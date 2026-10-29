# PIE-MOD-06 — Provenance & Current Source-Eligibility Gate

**Branch:** `feature/patterns-insights-full-module`. **Scope:** PIE-only standalone stage. **Implemented:** an independently injected, *per-read* fail-closed gate with source/project and row-level evidence checks. **Not implemented:** actual authenticated UAE exporter, real current D1/SRE/QDP/A/B/C adapters, or authorization of live statistical computations.

## Trust contract

- Input MUST be a `UAEReadResult` from the prior PIE receiver; raw `PIEDatasetPointer`, a JSON object, or a historical summary is refused. However, Python typing is **not** proof that this result was genuinely produced by UAE. Signed receipt verification belongs to PIE-MOD-05.
- `SourceEligibilityGate` requires an explicit external `ProvenanceAuthority` implementing `check_scope(pointer)` and `check_row(subject)`. Default constructor without one refuses with `capability_unavailable`; does not probe local/remote DB automatically. **No test fixture is a real authorization provider.**
- `ScopeEligibility` binds the current project, source, source-policy PK, exact dataset+manifest SHA, project membership, active/current source policy, enabled source, all four D1 permissions and effective retention expiry. Its check time must follow the stage-05 rights decision and not be in the future.
- `RowProvenanceSubject` exposes only exact identity/reference keys: canonical `video_id`, immutable UAE snapshot ID/SHA, original row SHA, QDP run/preparation hash, SRE decision digest, pinned Group A/B and persisted C metric/snapshot IDs+digests. It excludes raw scores, features, transcripts and creator PII. Concrete authority MUST query actual currently readable source records; it must not return booleans copied from the manifest.
- `RowEligibility` must independently confirm current project-video membership, qualified UAE read gate, current QDP qualified membership, LIVE/approved SRE relevance, exact completed A/B versions, persisted complete Group C calculation, metric quality, immutable linked lineage and derived expiry. Source + project + policy + dataset/row/snapshot SHA must match exact request; null/unavailable/mismatched responses are denied. No failed `qualified` flag can be overridden by a user JSON claim.
- Every evaluation rechecks source, each row, and the source again after the row scan, to catch mid-scan revocation. Every row's expiry is compared again at completion to prevent a long read from returning stale results. No cross-request permission caching or `last known good` fallback.
- A single failing row blocks **the whole frozen input sample**. The result exposes only count and safe reason codes, not row IDs or raw values; silent reduction of N would invalidate statistical inference. Errors from external checks are wrapped in typed, sanitized `PIEBoundaryError` without credentials.
- `EligibilityReport.candidate_eligible` is a mockable structural criterion; **`verified_live=False` and `ready_for_analysis=False` unconditionally**, including the positive synthetic test. This layer alone cannot attest adapter authenticity or active permissions. No generated insights or downstream exports are authorized by it.

## Why real upstream remains blocked

UAE branch last verified at UA-MOD-05 (`3c616f9ae1b461bb7ab439f89b3ef36a082bc419`): it has the canonical lineage reader, but not the production `uae-to-pie/v1` research exporter planned around UA-MOD-25, and not all LIVE SRE/QDP/A/B/C final handshakes. PIE uses no import or SQL shortcut to pretend UAE is ready. Next stages must negotiate and verify actual producer receipt and current source rights with trusted implementations. Independently check fresh authorization at every read and export, not just initial analysis.

## Tests and QA truth

Run from repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -v
python -m compileall -q intelligence/patterns_insights tests/test_pie_provenance.py
```

In local isolated `/mnt/data/pie_stage06` with Python 3.13: **93 tests, 85 passed, 8 PostgreSQL tests skipped** because `CM_TEST_PG_DSN` / PG17 unavailable. Includes 22 additional tests for happy-path synthetic candidate (still non-live), missing verifier, spoofed cross-project scope and snapshots, unknown/deleted/expired evidence, QDP/SRE/A/B/C conditions, policy expiry, mid-scan revocation, staleness, replay/recheck, generic upstream exceptions, no raw-data leakage and reproducible pinned lineage. Python compileall PASS. These do not establish real UAE handoff or PostgreSQL migration PASS. GitHub CI must be checked separately after push; queued status is not success.

**Next:** PIE-MOD-07 frozen reproducible dataset builder, remaining fail-closed pending trusted provider implementation.
