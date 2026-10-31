"""Offline smoke for the three *copied* feature-branch codebases.

No payments, providers, DB, or fake model observations. Use this to verify
import integrity, independent import paths and representative math/decoding.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
import json

PREFIX = "intelligence.market_intelligence._copied."
MODULES = {
    "a": "group_a.extractors.technical",
    "b1": "group_b.blocks.b1_hook",
    "b2": "group_b.blocks.b2_format",
    "b3": "group_b.blocks.b3_story",
    "b4": "group_b.blocks.b4_visual",
    "b5": "group_b.blocks.b5_editing",
    "b7": "group_b.blocks.b7_cta",
    "c_ratio": "group_c.creator_ratio",
    "c_growth": "group_c.views_growth",
}
CAPABILITIES = {
    "group_a": ("technical", "frames", "ocr", "asr", "audio", "subtitles"),
    "group_b": ("hook", "format", "story", "visual", "editing", "subtitles", "cta"),
    "group_c": ("creator_ratio", "views_growth", "cohort_comparison", "engagement"),
}


def run_smoke() -> dict:
    imported = {}
    for label, subpath in MODULES.items():
        module = importlib.import_module(PREFIX + subpath)
        if not module.__name__.startswith(PREFIX):
            raise RuntimeError("copied_module_path_not_isolated: " + label)
        imported[label] = module
    # Actual source algorithms, no mocked functions or simulated network.
    a = imported["a"]
    cuts = a.parse_cuts(
        "[Parsed_showinfo_0 @ 0x0] n: 75 pts_time: 3.000 showinfo\n",
        Decimal("10.000"), Decimal("0.200"),
    )
    if len(cuts) != 2:
        raise RuntimeError("group_a_scene_math_failed")
    ratio = imported["c_ratio"].compute_creator_ratio(100, Decimal("50"), Decimal("1"))
    if ratio != Decimal("1.980392156863"):
        raise RuntimeError("group_c_creator_ratio_failed")
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    growth = imported["c_growth"].compute_views_growth_per_hour(
        100, 150, now-timedelta(hours=2), now
    )
    if growth != Decimal("25.000000000000"):
        raise RuntimeError("group_c_growth_failed")
    # Group B represents B1/B3/B4/B7 as verified inference ONLY; no media or
    # authenticated model response is provided by this probe.
    b = {name: getattr(imported[name], attribute).__name__
         for name, attribute in (
            ("b1", "validate_b1_response"),
            ("b2", "validate_b2_response"),
            ("b3", "validate_b3_story"),
            ("b4", "validate_b4_visual"),
            ("b5", "analyze_b5"),
            ("b7", "validate_b7_cta"),
         )}
    return {
        "status": "offline_copy_smoke_passed",
        "namespace": PREFIX.rstrip("."),
        "source_algorithms_executed": ["A:scene_timeline", "C:creator_ratio", "C:views_growth"],
        "group_b_validators_imported": b,
        "group_b_semantic_inference": "not_executed_no_authenticated_media_or_model",
        "video_provider_calls": 0,
        "llm_provider_calls": 0,
        "project_database_writes": 0,
    }


if __name__ == "__main__":
    print(json.dumps(run_smoke(), ensure_ascii=False, indent=2))
