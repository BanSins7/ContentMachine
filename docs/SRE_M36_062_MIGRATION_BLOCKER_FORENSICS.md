# SRE M28/M36 blocking migration-062 forensics (2026-10-28)

**Scope:** evidence only, NO historical migration repair. Repository `BanSins7/ContentMachine`, SRE branch `feature/sre-production-v1-20261023`. No modifications to main, QDP, RTB or external migrations. No PostgreSQL17 successful execution claimed.

## Reproduced exact SQL defect

Current `intelligence/migrations/062_sre_live_opus_receipts.sql` has a broken declaration:

```sql
evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64} BIGINT NOT NULL CHECK(input_tokens>=0),
```

The string does not close and `input_tokens` loses its column declaration; in addition the Oct 27 commit diff shows a large block of duplicate M28 DDL appended inside the original table definition. **This is a parser blocker**, not a runtime policy denial. A fresh PostgreSQL migration chain cannot reach 070 until reconciled.

GitHub history inspection via the connected repository shows **only two path commits**:

1. `9671d48350a8f7c2256b7648fd487f2b5b99c2fa` (2026-10-27 19:55 UTC), original M28 schema with syntactically normal `evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$')` and separate `input_tokens BIGINT NOT NULL CHECK(input_tokens>=0)`; Git blob SHA `9338026761de4b3aaf68a9c0c171b2d460bca51c`.
2. `09f2372726e19aec3047f0b6313da213aee9f5a2` (2026-10-27 19:57 UTC), ostensibly the M28 bad-output/zero-cost settlement fix; its diff replaces that one well-formed line with hundreds of lines of partial/duplicated SQL, and its file blob is `64d029f9772021c0b85c44c62f8e653d457dd819`. The corrupted file still exists at M36 HEAD.

**Do not revert 09f blindly.** M28/M29/M30 current Python/SQL modules depend on later `evidence_status` and zero-cost settlement semantics. Restoring the earlier 062 in isolation could remove these required columns or triggers and break valid downstream behavior. Conversely overwriting 062 with a new checksum without inspecting `schema_migrations` will block existing databases that applied the earlier valid 062 blob.

## Safe next repair plan (NOT executed)

1. Read the actual `schema_migrations` table and applied SHA256 of 062 on each existing SRE development DB, including operator-managed installations; do not use a live database for destructive trial and never lie about unknown deployment state.
2. Obtain original valid 062 file byte-for-byte from git commit `9671d48...` and review its full final schema against actual receipt, budget and journal code; derive M28 09f intended *incremental* changes, separating honest bad-output receipt status from original version.
3. Prefer preserving any already-applied 062 checksum. A separately named, lexicographically ordered, additive SRE-only corrective migration **before dependent 063** can be evaluated if existing ledgers admit it; otherwise a documented operator-approved reconciliation of installed SHA is required. Never auto-edit schema migration ledger or force a checksum mismatch through.
4. Run a *disposable* PostgreSQL17 fresh install and simulated upgrade using `intelligence.migrate`, then M28 receipt + M29–M36 schema/unit/PG fixture suites at exactly the same Git SHA; track real PASS/FAIL/SKIP. In particular verify only one M28 receipt table, exactly one M25 settlement trigger path, no duplicated SQL function/triggers and no financial double-credit.
5. Only then remove the `062` blocker from M36 QA, with explicit tested database fingerprints. Later M37/QDP external integration is separately blocked until a real LIVE positive path and downstream authenticated consumer are proven.

**Current status:** M36 code committed but actual PostgreSQL17 migration/E2E DoD BLOCKED by a pre-existing malformed 062. This forensic report does not fix or claim to fix the migration and does not make a fake synthetic/provider result publishable.
