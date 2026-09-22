"""Running one slow thing in the background, and reporting on it.

An adaptation takes twenty to sixty seconds. Holding an HTTP request open for
that long works on localhost right up until it does not: the browser shows a
blank tab, a reload fires a second model run, and closing the tab abandons work
that is already being paid for in plan usage.

So the request starts a job and returns immediately, and the page polls. This
is a single-slot runner on purpose -- one adaptation at a time, for one reader.
Anything more (a real queue, persistence across restarts, cancellation) would be
building a job system for a problem that does not have one yet. Jobs live in
memory and are forgotten on restart, which is the right trade while the work
they guard is one minute long.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class JobState(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobBusy(Exception):
    """Something is already running, and only one thing may run at a time."""


@dataclass
class Job(Generic[T]):
    """One background task and whatever it produced."""

    id: str
    label: str
    state: JobState = JobState.RUNNING
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    result: T | None = None
    error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.state is JobState.RUNNING

    @property
    def seconds(self) -> float:
        end = self.finished_at or datetime.now()
        return (end - self.started_at).total_seconds()


class Runner(Generic[T]):
    """Runs one job at a time and remembers the last one.

    The lock covers the decision to start as well as the bookkeeping, so two
    requests arriving together cannot both conclude that nothing is running.
    Without it the reader double-clicks and pays for two adaptations.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job[T]] = {}
        self._current_id: str | None = None

    def submit(self, label: str, work: Callable[[], T]) -> Job[T]:
        with self._lock:
            if self._current_id is not None:
                current = self._jobs[self._current_id]
                raise JobBusy(f"{current.label} is still running")
            job: Job[T] = Job(id=uuid.uuid4().hex[:12], label=label)
            self._jobs[job.id] = job
            self._current_id = job.id

        thread = threading.Thread(target=self._run, args=(job, work), daemon=True)
        thread.start()
        return job

    def _run(self, job: Job[T], work: Callable[[], T]) -> None:
        try:
            outcome = work()
        except Exception as error:  # noqa: BLE001 -- the reason is shown to the reader
            with self._lock:
                job.state = JobState.FAILED
                job.error = str(error) or error.__class__.__name__
                job.finished_at = datetime.now()
                self._current_id = None
            return

        with self._lock:
            job.result = outcome
            job.state = JobState.DONE
            job.finished_at = datetime.now()
            self._current_id = None

    def get(self, job_id: str) -> Job[T] | None:
        with self._lock:
            return self._jobs.get(job_id)

    def current(self) -> Job[T] | None:
        with self._lock:
            return self._jobs[self._current_id] if self._current_id else None

    def busy(self) -> bool:
        with self._lock:
            return self._current_id is not None

    def forget_finished(self, keep: int = 20) -> None:
        """Drop the oldest finished jobs so the dict cannot grow forever."""
        with self._lock:
            finished = sorted(
                (job for job in self._jobs.values() if not job.running),
                key=lambda job: job.finished_at or job.started_at,
            )
            for job in finished[:-keep] if len(finished) > keep else []:
                self._jobs.pop(job.id, None)
