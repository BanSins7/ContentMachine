"""M42: real M27 fake transport -> M21 technical evidence -> shadow-only PG journal.

This runs the existing M20/M27/M21 validators, not a second model protocol.
It never authorizes spending, writes M28 receipts, creates a genuine M30
result, or publishes M36 events. A valid fake quote is not a billing record.
The caller must produce the sealed envelopes using the existing read-only
M27 builders. No local or internet model is initialized.
"""
from __future__ import annotations

import hashlib
from .m42_fixture_isolation import (
    PROVENANCE, _blocked, _canonical_uuid,
    record_simulated_step, require_disposable_m42,
)

VERSION = "sre.m42.fake_opus_verified_shadow/v1"


def verify_and_journal_fake_opus(conn, run_id, manifest, envelopes, scripted):
    """Verify every synthetic reply before any SQL mutation; then atomically
    journal SHA-only test provenance (never provider receipts).

    The synthetic request/fixture payloads are untrusted and therefore checked
    by the existing production M27 and M21 *pure* validators. All semantics
    remain unverified and no M28 authenticated generation may be fabricated.
    """
    require_disposable_m42(conn)
    run = _canonical_uuid(run_id)

    # Import only after an explicit disposable DB gate. Do not invoke any
    # live transport method; M27's public paid send remains disabled.
    from .opus_evidence_verifier import _manifest, verify_opus_responses
    from .opus_transport import FakeOpusTransport, MAX_RESPONSE_BYTES
    from .v1_contracts import fingerprint, project_id

    if type(manifest) is not dict:
        _blocked("M20 manifest required")
    _manifest(manifest)
    pid = project_id(manifest["project_id"])
    requests = manifest["requests"]
    if (type(envelopes) is not list
            or len(envelopes) != len(requests)
            or not 1 <= len(requests) <= 2048):
        _blocked("every M20 request requires one sealed fake envelope")
    if type(scripted) is not dict or set(scripted) != {
        request["request_sha256"] for request in requests
    }:
        _blocked("scripts must cover exactly all M20 request hashes")
    if "run_id" in manifest and manifest["run_id"] != run:
        _blocked("M20 run identity mismatch")

    # Pre-bound every response by *character* length before UTF-8 hashing;
    # the existing FakeOpusTransport applies its own exact byte size limit.
    if any(type(raw) is not str or not raw or len(raw) > MAX_RESPONSE_BYTES
           for raw in scripted.values()):
        _blocked("unbounded or invalid scripted response")
    fake = FakeOpusTransport(scripted)

    # Source witness binds the *whole script set* without persisting text.
    # UTF-8 raw fixture material may be inspected inside the isolated process
    # but is never sent to SQL, returned to API callers or written to logs.
    bound = {
        "run_id": run,
        "project_id": pid,
        "batch_manifest_sha256": manifest["batch_manifest_sha256"],
        "script_sha256s": [
            [request["request_sha256"],
             hashlib.sha256(scripted[request["request_sha256"]].encode("utf-8")).hexdigest()]
            for request in requests
            if type(scripted[request["request_sha256"]]) is str
        ],
    }
    if len(bound["script_sha256s"]) != len(requests):
        _blocked("non-UTF8 or missing scripted response")
    source_sha = fingerprint(bound)

    received = []
    for request, envelope in zip(requests, envelopes):
        if (type(envelope) is not dict
                or envelope.get("project_id") != pid
                or envelope.get("request_sha256") != request["request_sha256"]
                or envelope.get("batch_manifest_sha256") != manifest["batch_manifest_sha256"]):
            _blocked("sealed M27 envelope does not match M20")
        answer = fake.execute(envelope)
        if (answer.get("source") != "synthetic_fixture_not_provider"
                or answer.get("receipt_verified") is not False
                or answer.get("actual_charge_usd") is not None
                or answer.get("authenticated_provider_usage") is not None
                or answer.get("dispatch_allowed") is not False
                or answer.get("paid_calls") != 0):
            _blocked("fake transport attempted provider/finance authority")
        received.append({
            "request_sha256": request["request_sha256"],
            "raw_json": answer["raw_answer"],
        })

    verified = verify_opus_responses(manifest, received)
    expected = manifest["expected_hypothesis_keys"]
    if (verified.get("expected_hypothesis_keys") != expected
            or verified.get("verified_category_count") != len(expected)
            or verified.get("verified_request_count") != len(requests)
            or verified.get("provider_receipts_authenticated") is not False
            or verified.get("semantics_independently_confirmed") is not False
            or verified.get("dispatch_allowed") is not False
            or verified.get("paid_calls") != 0):
        _blocked("technical verifier cannot authorize LIVE semantics")
    verification_sha = verified["verification_sha256"]

    # Nested .transaction() in record_simulated_step becomes PG savepoints.
    # On a later conflict the whole two-stage operation rolls back atomically.
    with conn.transaction():
        a = record_simulated_step(conn, pid, run, "fake_opus_response", source_sha)
        b = record_simulated_step(
            conn, pid, run, "simulated_verified_pairs", verification_sha
        )
    return {
        "schema_version": VERSION,
        "provenance": PROVENANCE,
        "project_id": pid,
        "run_id": run,
        "source_sha256": source_sha,
        "verification_sha256": verification_sha,
        "validated_requests": verified["verified_request_count"],
        "validated_categories": verified["verified_category_count"],
        "decisions_are_untrusted": True,
        "provider_receipts_authenticated": False,
        "financial_settlement_authorized": False,
        "exportable_to_live_consumers": False,
        "paid_calls": 0,
        "replayed": a["replayed"] and b["replayed"],
    }


__all__ = ("VERSION", "verify_and_journal_fake_opus")
