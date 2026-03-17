"""
pipeline.py — Re-export from shared_infra.pipeline

Canonical implementation lives in shared_infra.pipeline.
This file exists so all existing trader_cycle imports continue to work.
"""

from shared_infra.pipeline import (  # noqa: F401
    PipelineContext,
    CriticalError,
    RecoverableError,
    Step,
    Pipeline,
)

__all__ = [
    "PipelineContext",
    "CriticalError",
    "RecoverableError",
    "Step",
    "Pipeline",
]
