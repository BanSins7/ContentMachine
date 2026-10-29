"""M42 read-only migration-062 provenance audit and deployment safety gate.

This is NOT a migration runner, SQL fixer or checksum reconciliation. The
current SRE branch contains malformed historical 062 SQL; an operator must
inspect the actual deployed schema_migrations ledger and plan a compatible
reviewed fix before any clean/upgrade PostgreSQL17 migration acceptance.
No DDL/DML, network, paid provider or automatic ledger edits occur here.
"""
from __future__ import annotations

from hashlib import sha1, sha256
from pathlib import Path

from intelligence.migrate import MIGRATIONS

VERSION = "sre.m42.migration_062_audit/v1"
FILENAME = "062_sre_live_opus_receipts.sql"
ORIGINAL_VALID_GIT_BLOB = "9338026761de4b3aaf68a9c0c171b2d460bca51c"
KNOWN_BROKEN_GIT_BLOB = "64d029f9772021c0b85c44c62f8e653d457dd819"
BROKEN_FRAGMENT = "CHECK(evidence_sha256 ~ '^[0-9a-f]{64} BIGINT NOT NULL"
_DUPLICATES = (
    "CREATE INDEX sre_m28_receipts_project_recorded",
    "CREATE FUNCTION sre_m28_receipt_guard()",
    "CREATE TRIGGER sre_m28_receipt_insert_guard",
    "CREATE TRIGGER sre_m28_receipt_after_insert",
)
_HASH = __import__("re").compile(r"[0-9a-f]{64}\Z")


class Migration062Blocked(RuntimeError):
    """Fail-closed, privacy-safe error. No credentials or DSNs in messages."""


def _git_blob(text: str) -> str:
    raw = text.encode("utf-8")
    return sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def inspect_062_source(*, root: Path | None = None):
    """Read an actual checkout, classify known Git blobs; never claim PASS."""
    directory = Path(root) if root is not None else MIGRATIONS
    path = directory / FILENAME
    if path.is_symlink() or not path.is_file():
        return {"schema_version": VERSION, "status": "blocked",
                "reason": "062_missing_or_symlink", "safe_to_apply": False}
    try:
        # Matching intelligence.migrate's universal newline text encoding.
        data = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {"schema_version": VERSION, "status": "blocked",
                "reason": "062_unreadable", "safe_to_apply": False}
    git_sha = _git_blob(data)
    sql_sha = sha256(data.encode("utf-8")).hexdigest()
    if git_sha == KNOWN_BROKEN_GIT_BLOB or BROKEN_FRAGMENT in data:
        reason = "062_known_malformed_sql"
    elif git_sha == ORIGINAL_VALID_GIT_BLOB:
        # Original source is syntactically plausible but predates newer
        # evidence_status/zero-charge accounting. No whole-chain PASS.
        reason = "062_historical_schema_requires_review"
    else:
        reason = "062_unreviewed_sql_blob"
    duplicates = [name for name in _DUPLICATES if data.count(name) > 1]
    return {
        "schema_version": VERSION, "status": "blocked", "reason": reason,
        "safe_to_apply": False, "git_blob_sha1": git_sha, "sql_sha256": sql_sha,
        "duplicate_ddl_names": duplicates,
        # The malformed initial table contains the token name only inside
        # an unclosed regex literal. A later duplicated token definition
        # cannot rescue the original CREATE TABLE parser error.
        "missing_input_tokens_column": (
            BROKEN_FRAGMENT in data or "input_tokens BIGINT NOT NULL" not in data
        ),
        "corrupted_evidence_constraint": BROKEN_FRAGMENT in data,
    }


def inspect_062_installed(conn, *, root: Path | None = None):
    """Read installed ledger IN A REPEATABLE READ READ ONLY transaction.

    Uses the current public schema only (same standard migrator table);
    callers must select an already trusted database. Missing ledger and
    checksum divergence both remain BLOCKED, even on a test machine.
    """
    source = inspect_062_source(root=root)
    if getattr(conn, "autocommit", None) is not True:
        raise Migration062Blocked("dedicated idle autocommit read-only session required")
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            exists = conn.execute(
                "SELECT to_regclass('public.schema_migrations')"
            ).fetchone()
            if exists is None or len(exists) != 1:
                raise Migration062Blocked("migration ledger lookup failed")
            if exists[0] is None:
                ledger_state = "ledger_absent"
                digest = None
            else:
                records = conn.execute(
                    "SELECT sha256 FROM public.schema_migrations WHERE version=%s",
                    (FILENAME,),
                ).fetchall()
                if not isinstance(records, list) or len(records) > 1:
                    raise Migration062Blocked("unexpected migration ledger rows")
                if not records:
                    ledger_state = "062_not_installed"
                    digest = None
                else:
                    digest = records[0][0]
                    if type(digest) is not str or _HASH.fullmatch(digest) is None:
                        raise Migration062Blocked("invalid applied migration checksum")
                    ledger_state = (
                        "installed_exact_checkout"
                        if digest == source.get("sql_sha256")
                        else "installed_checksum_differs"
                    )
    except Migration062Blocked:
        raise
    except Exception:
        raise Migration062Blocked("read-only migration ledger inspection unavailable") from None
    return {
        **source, "status": "blocked", "safe_to_apply": False,
        "ledger_state": ledger_state, "installed_sql_sha256": digest,
        "operator_action": (
            "review_exact_deployed_schema_and_checksum_before_repair"
            if digest is not None else "review_clean_install_062_fix_before_migrate"
        ), "schema_changed": False, "paid_calls": 0,
    }


__all__ = (
    "VERSION", "FILENAME", "Migration062Blocked",
    "inspect_062_source", "inspect_062_installed",
)
