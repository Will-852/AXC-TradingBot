"""
pipeline.py — Step-based pipeline runner
每個 step 係獨立 callable，可隨時 insert/remove/replace
"""

from __future__ import annotations
import traceback
from typing import Protocol, runtime_checkable

from ..core.context import CycleContext


class CriticalError(Exception):
    """Unrecoverable error — abort pipeline, send URGENT alert."""
    pass


class RecoverableError(Exception):
    """Recoverable error — skip step/pair, continue pipeline."""
    pass


@runtime_checkable
class Step(Protocol):
    """Protocol for pipeline steps."""
    name: str

    def run(self, ctx: CycleContext) -> CycleContext:
        ...


class Pipeline:
    """
    Ordered sequence of steps. Each step takes CycleContext and returns it.
    Extensible: insert_before/after, remove, replace steps at runtime.
    """

    def __init__(self):
        self._steps: list[Step] = []

    def add_step(self, step: Step) -> "Pipeline":
        """Append a step to the end."""
        self._steps.append(step)
        return self

    def insert_before(self, target_name: str, step: Step) -> "Pipeline":
        """Insert a step before the named target."""
        for i, s in enumerate(self._steps):
            if s.name == target_name:
                self._steps.insert(i, step)
                return self
        raise ValueError(f"Step '{target_name}' not found")

    def insert_after(self, target_name: str, step: Step) -> "Pipeline":
        """Insert a step after the named target."""
        for i, s in enumerate(self._steps):
            if s.name == target_name:
                self._steps.insert(i + 1, step)
                return self
        raise ValueError(f"Step '{target_name}' not found")

    def remove_step(self, name: str) -> "Pipeline":
        """Remove a step by name."""
        self._steps = [s for s in self._steps if s.name != name]
        return self

    def replace_step(self, name: str, new_step: Step) -> "Pipeline":
        """Replace a step by name."""
        for i, s in enumerate(self._steps):
            if s.name == name:
                self._steps[i] = new_step
                return self
        raise ValueError(f"Step '{name}' not found")

    def get_step_names(self) -> list[str]:
        """Return ordered list of step names."""
        return [s.name for s in self._steps]

    def run(self, ctx: CycleContext) -> CycleContext:
        """
        Execute all steps in order.
        - CriticalError → abort, always finalize
        - RecoverableError → log warning, continue
        - Unknown Exception → treat as critical
        """
        for step in self._steps:
            try:
                if ctx.verbose:
                    print(f"  [STEP] {step.name}...")
                ctx = step.run(ctx)
            except CriticalError as e:
                ctx.errors.append(f"CRITICAL@{step.name}: {e}")
                if ctx.verbose:
                    print(f"  [CRITICAL] {step.name}: {e}")
                break
            except RecoverableError as e:
                ctx.warnings.append(f"WARN@{step.name}: {e}")
                if ctx.verbose:
                    print(f"  [WARN] {step.name}: {e}")
                continue
            except Exception as e:
                ctx.errors.append(f"UNKNOWN@{step.name}: {e}\n{traceback.format_exc()}")
                if ctx.verbose:
                    print(f"  [ERROR] {step.name}: {e}")
                break

        return ctx
