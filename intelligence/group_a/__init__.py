"""Local Group A stage A1 (FFprobe/FFmpeg geometry, duration and scene timeline)."""
from .contracts import GroupAError, TechnicalMeasurement, Scene, parse_config
from .repository import create_task, task_details, list_tasks, work_once
from .contracts_v1 import (ContractViolation, qdp_handoff_v1, media_manifest_v1,
                           stream_truth_v1, group_a_to_b_v1, ensure_same_lineage)
__all__=["GroupAError","TechnicalMeasurement","Scene","parse_config",
         "create_task","task_details","list_tasks","work_once",
         "ContractViolation","qdp_handoff_v1","media_manifest_v1",
         "stream_truth_v1","group_a_to_b_v1","ensure_same_lineage"]
