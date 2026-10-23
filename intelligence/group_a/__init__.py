"""Local Group A stage A1 (FFprobe/FFmpeg geometry, duration and scene timeline)."""
from .contracts import GroupAError, TechnicalMeasurement, Scene, parse_config
from .repository import create_task, task_details, list_tasks, work_once
__all__=["GroupAError","TechnicalMeasurement","Scene","parse_config",
         "create_task","task_details","list_tasks","work_once"]
