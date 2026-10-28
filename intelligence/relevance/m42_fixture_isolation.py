"""M42 test-only PostgreSQL fixture journal, never a LIVE receipt/outbox.

The marker is created *outside* this module by an operator only in a disposable
PostgreSQL 17 database. The module will never establish a connection, create a
marker, call a provider, or alter a production SRE table. Its journal cannot be
queried by M28/M30/M36 authority paths. This is an M42 safety foundation, NOT
an implementation of a full paid/verified E2E runtime.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
import re
from uuid import UUID

VERSION = "sre.m42.synthetic_fixture/v1"
PROVENANCE = "synthetic_test_only_not_authenticated_provider"
_APPLICATION = "contentmachine-sre-m42-isolated"
_DATABASE = re.compile(r"sre_m42_[a-z0-9_]{1,40}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_STAGES = frozenset((
    "approved_c9_read", "authorized_d1_read", "local_rank_all",
    "fake_quote", "fake_consent", "fake_reserve", "fake_dispatch_intent",
    "fake_opus_response", "fake_receipt", "simulated_verified_pairs",
    "simulated_human_review", "synthetic_outbox", "rights_revoked",
))
_ENV = ("CM_SRE_M42_TEST_ONLY", "CM_SRE_M42_DISPOSABLE")
_UNSAFE_FLAGS = (
    "CM_SRE_LIVE_CLASSIFICATION", "CM_SRE_QDP_EVENT_DELIVERY",
    "CM_SRE_COLLECTOR_EVENT_INGEST", "CM_SRE_OPENROUTER_API_KEY",
    "OPENROUTER_API_KEY", "SRE_ENABLE_LIVE_PAID",
)


class M42FixtureBlocked(RuntimeError):
    """Fixed non-sensitive errors; never echo environment, DSN or credentials."""


def _blocked(message: str):
    raise M42FixtureBlocked("M42 fixture blocked: " + message)


def _canonical_uuid(value: str) -> str:
    if type(value) is not str:
        _blocked("invalid run UUID")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        _blocked("invalid run UUID")
    if str(parsed) != value:
        _blocked("noncanonical run UUID")
    return value


def require_disposable_m42(conn):
    """Guard *every* write; never authorize simulation from an env flag alone.

    Needs two explicit flags, exact application name, a matching disposable DB
    name, a nonprivileged role and an operator-created marker. Test-only data
    must remain outside every production LIVE/analytics table and view.
    """
    # No injectable environment: tests patch the process environment instead.
    # Caller-controlled mappings must never bypass actual production flags.
    env = os.environ
    if any(env.get(key) != "1" for key in _ENV):
        _blocked("explicit two-key disposable test approval missing")
    if any(env.get(key) not in (None, "", "0", "false") for key in _UNSAFE_FLAGS):
        _blocked("LIVE, exporter or provider settings present")
    if getattr(conn, "autocommit", None) is not True:
        _blocked("dedicated autocommit test connection required")
    try:
        row = conn.execute("""SELECT current_database(), current_setting('application_name'),
          current_setting('server_version_num')::integer, pg_is_in_recovery(),
          r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication
          FROM pg_roles r WHERE r.rolname=current_user""").fetchone()
    except Exception:
        _blocked("database identity query failed")
    if (not isinstance(row, (tuple, list)) or len(row) != 8
            or type(row[0]) is not str or not _DATABASE.fullmatch(row[0])
            or row[1] != _APPLICATION or type(row[2]) is not int
            or row[2] < 170000 or row[3] is not False
            or any(v is not False for v in row[4:])):
        _blocked("database/version/session/role is not isolated")
    try:
        marker = conn.execute("""SELECT to_regclass('public.sre_m42_disposable_marker')::text""").fetchone()
        if marker != ("sre_m42_disposable_marker",):
            _blocked("operator disposable marker missing")
        proof = conn.execute("""SELECT marker FROM public.sre_m42_disposable_marker
                              WHERE marker='fixture_database_only'""").fetchall()
    except M42FixtureBlocked:
        raise
    except Exception:
        _blocked("operator disposable marker invalid")
    if proof != [("fixture_database_only",)]:
        _blocked("operator disposable marker invalid")
    return {"schema_version": VERSION, "database": row[0], "mode": "test_only"}


# Only shadow records. No foreign keys to actual sre_live_*, no column that
# impersonates a provider generation ID or an M28 authenticated receipt.
_JOURNAL_DDL = """CREATE TABLE IF NOT EXISTS public.sre_m42_fixture_journal (
    project_id BIGINT NOT NULL CHECK(project_id>0),
    run_id UUID NOT NULL,
    step TEXT NOT NULL CHECK(step IN (
      'approved_c9_read','authorized_d1_read','local_rank_all',
      'fake_quote','fake_consent','fake_reserve','fake_dispatch_intent',
      'fake_opus_response','fake_receipt','simulated_verified_pairs',
      'simulated_human_review','synthetic_outbox','rights_revoked')),
    source_sha256 TEXT NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
    payload JSONB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
    provenance TEXT NOT NULL DEFAULT 'synthetic_test_only_not_authenticated_provider'
      CHECK(provenance='synthetic_test_only_not_authenticated_provider'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(project_id,run_id,step,source_sha256)
)"""


def _safe_payload(project: int, run: str, step: str, source_sha: str):
    # Fingerprints, never texts, user IDs, secrets or fictional receipts.
    return {"schema_version": VERSION, "provenance": PROVENANCE,
            "project_id": project, "run_id": run, "step": step,
            "source_sha256": source_sha, "provider_authenticated": False,
            "financial_settlement_authorized": False,
            "exportable_to_live_consumers": False, "paid_calls": 0}


def record_simulated_step(conn, project_id: int, run_id: str, step: str,
                          source_sha256: str):
    """Durably journal an isolated TEST step with SHA-bound replay validation.

    Passing a real M28 receipt or a paid charge is intentionally impossible:
    only hashes and constant no-authority provenance enter this shadow table.
    """
    require_disposable_m42(conn)
    if type(project_id) is not int or not 1 <= project_id <= 9223372036854775807:
        _blocked("invalid project")
    run_id = _canonical_uuid(run_id)
    if type(step) is not str or step not in _STAGES:
        _blocked("unknown simulation step")
    if type(source_sha256) is not str or _SHA.fullmatch(source_sha256) is None:
        _blocked("invalid source digest")
    payload = _safe_payload(project_id, run_id, step, source_sha256)
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    digest = sha256(blob.encode("utf-8")).hexdigest()
    with conn.transaction():
        conn.execute(_JOURNAL_DDL)
        created = conn.execute("""INSERT INTO public.sre_m42_fixture_journal
          (project_id,run_id,step,source_sha256,payload,payload_sha256)
          VALUES (%s,%s::uuid,%s,%s,%s::jsonb,%s)
          ON CONFLICT DO NOTHING RETURNING payload_sha256""",
          (project_id,run_id,step,source_sha256,blob,digest)).fetchone()
        if created is None:
            stored = conn.execute("""SELECT payload,payload_sha256,provenance
              FROM public.sre_m42_fixture_journal
              WHERE project_id=%s AND run_id=%s::uuid AND step=%s
                AND source_sha256=%s FOR UPDATE""",
              (project_id,run_id,step,source_sha256)).fetchone()
            if stored != (payload, digest, PROVENANCE):
                _blocked("fixture replay mismatch")
        elif created != (digest,):
            _blocked("unexpected journal insert")
    return {"schema_version": VERSION, "provenance": PROVENANCE,
            "payload_sha256": digest, "replayed": created is None,
            "paid_calls": 0, "exportable_to_live_consumers": False}


__all__ = ("M42FixtureBlocked", "PROVENANCE", "VERSION",
           "require_disposable_m42", "record_simulated_step")
