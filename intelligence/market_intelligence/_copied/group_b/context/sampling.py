"""GB-MOD-08: deterministic stratified selection of *verified* media references.

Selections are pointers only, never generated frames/slides. Farthest-first
temporal coverage begins at the start/end, then fills maximal gaps; an early
hook-window candidate is reserved when capacity permits. Carousels preserve
canonical slide ordinals and record every omitted reference.
"""
from __future__ import annotations

from collections.abc import Sequence

from .evidence import VerifiedObservation


def _key(item: VerifiedObservation):
    return (item.start_ms if item.start_ms is not None else -1,
            item.slide_index if item.slide_index is not None else -1,
            item.ref_sha256)


def select_diverse(items: Sequence[VerifiedObservation], cap: int, *,
                   early_window_ms: int | None = None) -> tuple[VerifiedObservation, ...]:
    """Stable max-min cover of supplied, nonfabricated observations.

    For slides use slide_index; for frames use start_ms. A fixed cap may
    undersample the content; coverage is reported by the context builder.
    """
    if type(cap) is not int or cap < 0:
        raise ValueError("cap must be a nonnegative integer")
    if not items or cap == 0:
        return ()
    if any(not isinstance(x, VerifiedObservation) for x in items):
        raise TypeError("verified observations required")
    ordered = sorted(items, key=_key)
    if len(ordered) <= cap:
        return tuple(ordered)
    def position(item):
        value = item.slide_index if item.kind == "slide_ref" else item.start_ms
        if type(value) is not int or value < 0:
            raise ValueError("Measured media position required")
        return value
    # Input is ordered by canonical temporal/slide position. Duplicate refs
    # remain detectable in the upstream manifest, never silently merged.
    chosen = [ordered[0]]
    if cap > 1 and ordered[-1].ref_sha256 != ordered[0].ref_sha256:
        chosen.append(ordered[-1])
    if early_window_ms is not None and len(chosen) < cap:
        early = [x for x in ordered[1:-1]
                 if x.kind != "slide_ref" and position(x) <= early_window_ms]
        if early:
            chosen.append(early[0])
    selected = {x.ref_sha256 for x in chosen}
    while len(chosen) < cap:
        candidates = [x for x in ordered if x.ref_sha256 not in selected]
        if not candidates:
            break
        # Prefer largest uncovered distance, then earliest position and hash.
        winner = min(candidates, key=lambda x: (
            -min(abs(position(x)-position(c)) for c in chosen),
            position(x), x.ref_sha256,
        ))
        chosen.append(winner)
        selected.add(winner.ref_sha256)
    return tuple(sorted(chosen, key=_key))


def uncovered_time_windows(items: Sequence[VerifiedObservation], *,
                           duration_ms: int,
                           bucket_ms: int = 10_000) -> tuple[tuple[int, int], ...]:
    """Exact time buckets with no sampled-frame point inside, not invented gaps."""
    if (type(duration_ms) is not int or duration_ms < 0 or
        type(bucket_ms) is not int or bucket_ms < 1):
        raise ValueError("Valid measured duration and bucket required")
    if duration_ms == 0:
        return ()
    positions = {x.start_ms for x in items
                 if x.kind == "sampled_frame" and x.start_ms is not None}
    windows = []
    for start in range(0, duration_ms, bucket_ms):
        stop = min(duration_ms, start + bucket_ms)
        if not any(start <= p < stop or (p == duration_ms and stop == duration_ms)
                   for p in positions):
            windows.append((start, stop))
    return tuple(windows)
