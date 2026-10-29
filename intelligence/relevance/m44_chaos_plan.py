"""SRE M44 bounded chaos/load case planner (NO database, network, or model).

Provides reproducible 100/1000/10000-video *plans* for external disposable
PostgreSQL test runners. It does not create SRE LIVE runs, certify capacity,
measure PostgreSQL throughput or resolve the historical migration 062 blocker.

A caller consumes one chunk at a time. No list of all (video x category)
combinations is ever allocated. No synthetic status/label is promoted to LIVE.
"""
from __future__ import annotations

from hashlib import sha256
import json
from uuid import UUID

VERSION = "sre-m44-disposable-chaos-plan/v1"
SCALES = (100, 1000, 10000)
MAX_LEAVES = 48
MAX_CHUNK = 1000
FAULTS = (
    ("dual_claim", "only_one_worker_token_wins_current_lease_generation"),
    ("lease_death", "dead_worker_token_cannot_commit_after_reclaim"),
    ("ambiguous_paid_intent", "possible_paid_charge_stays_unknown_no_repost"),
    ("revoked_source_race", "revoked_rights_blocks_result_and_export"),
    ("outbox_ack_retry", "one_delivered_event_same_idempotency_witness"),
    ("restart_after_commit", "repeat_idempotent_no_duplicate_finance_or_pairs"),
    ("backup_restore", "restored_generation_fences_and_rights_still_enforced"),
)
_SHA = __import__("re").compile(r"[0-9a-f]{64}\Z")


class M44PlanBlocked(ValueError):
    pass


def _uuid(value):
    if type(value) is not str:
        raise M44PlanBlocked("category key must be canonical UUID")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise M44PlanBlocked("invalid category UUID") from exc
    if str(parsed) != value:
        raise M44PlanBlocked("noncanonical category UUID")
    return value


def _manifest_sha(categories):
    payload = json.dumps(categories, ensure_ascii=False, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _validate(video_count, keys, chunk_size):
    if type(video_count) is not int or video_count not in SCALES:
        raise M44PlanBlocked("only 100/1000/10000 video scales permitted")
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK:
        raise M44PlanBlocked("invalid bounded chunk size")
    if type(keys) not in (list, tuple) or not 1 <= len(keys) <= MAX_LEAVES:
        raise M44PlanBlocked("invalid approved category count")
    categories = tuple(_uuid(k) for k in keys)
    if categories != tuple(sorted(set(categories))):
        raise M44PlanBlocked("approved category keys must be sorted unique")
    return categories


def iter_bounded_pairs(video_count, category_keys, *, chunk_size=100):
    """Yield at most chunk_size*len(category_keys) pairs per chunk.

    The output is a deterministic *test instruction*, not a source-authorized
    C9 or D1 identity. No large cross-product is cached across yields.
    """
    categories = _validate(video_count, category_keys, chunk_size)
    for start in range(0, video_count, chunk_size):
        stop = min(start + chunk_size, video_count)
        # The list is intentionally bounded to <=1000*48 test pairs.
        pairs = tuple((video_id, key)
                      for video_id in range(start + 1, stop + 1)
                      for key in categories)
        yield {
            "schema_version": VERSION,
            "test_only": True,
            "batch_index": start // chunk_size,
            "first_synthetic_video_index": start + 1,
            "last_synthetic_video_index": stop,
            "category_count": len(categories),
            "pair_count": len(pairs),
            "pairs": pairs,
        }


def plan_chaos(video_count, category_keys, *, chunk_size=100, seed_sha256=None):
    """Return small bounded scenario METADATA, not 480k generated pairs."""
    categories = _validate(video_count, category_keys, chunk_size)
    if seed_sha256 is not None and (
        type(seed_sha256) is not str or not _SHA.fullmatch(seed_sha256)
    ):
        raise M44PlanBlocked("invalid deterministic scenario seed")
    count = (video_count + chunk_size - 1) // chunk_size
    witness = {
        "schema_version": VERSION,
        "video_count": video_count, "category_count": len(categories),
        "chunk_size": chunk_size, "chunk_count": count,
        "total_expected_pairs": video_count * len(categories),
        "category_sha256": _manifest_sha(categories),
        "seed_sha256": seed_sha256 or ("0" * 64),
        "scenarios": [
            {"name": name, "expected_invariant": invariant,
             "result": "not_executed"}
            for name, invariant in FAULTS
        ],
    }
    witness["plan_sha256"] = sha256(json.dumps(
        witness, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return {**witness, "provider_calls": 0, "database_calls": 0,
            "performance_measured": False, "chaos_pass": False,
            "release_gate": "not_executed"}


__all__ = (
    "VERSION", "SCALES", "FAULTS", "M44PlanBlocked",
    "iter_bounded_pairs", "plan_chaos",
)
