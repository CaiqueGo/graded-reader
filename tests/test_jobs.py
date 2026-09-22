"""Tests for the background runner.

An adaptation costs a minute of waiting and a slice of the reader's plan, so
the thing that must not happen is two of them starting because a button was
clicked twice. That is the whole reason this runner holds a lock.
"""

from __future__ import annotations

import threading
import time

import pytest

from graded_reader.jobs import JobBusy, JobState, Runner


def test_a_finished_job_carries_its_result() -> None:
    runner: Runner[str] = Runner()
    job = runner.submit("work", lambda: "done")

    for _ in range(200):
        if not job.running:
            break
        time.sleep(0.01)

    assert job.state is JobState.DONE
    assert job.result == "done"
    assert job.finished_at is not None
    assert not runner.busy()


def test_a_failing_job_keeps_the_reason_and_frees_the_slot() -> None:
    runner: Runner[str] = Runner()

    def explode() -> str:
        raise ValueError("the model said no")

    job = runner.submit("work", explode)
    for _ in range(200):
        if not job.running:
            break
        time.sleep(0.01)

    assert job.state is JobState.FAILED
    assert job.error == "the model said no"
    assert not runner.busy(), "a failure must not wedge the runner shut"


def test_a_second_job_is_refused_while_one_is_running() -> None:
    runner: Runner[str] = Runner()
    release = threading.Event()

    def slow() -> str:
        release.wait(timeout=5)
        return "done"

    first = runner.submit("first", slow)
    try:
        with pytest.raises(JobBusy, match="first"):
            runner.submit("second", lambda: "nope")
    finally:
        release.set()

    for _ in range(500):
        if not first.running:
            break
        time.sleep(0.01)
    assert runner.submit("now free", lambda: "ok") is not None


def test_a_job_can_be_found_again_by_its_id() -> None:
    """The page polls by id after the response that started it has gone."""
    runner: Runner[str] = Runner()
    job = runner.submit("work", lambda: "done")
    assert runner.get(job.id) is job
    assert runner.get("nonexistent") is None


def test_finished_jobs_are_eventually_forgotten() -> None:
    """In-memory bookkeeping must not grow for the life of the process."""
    runner: Runner[str] = Runner()
    for n in range(8):
        job = runner.submit(f"job {n}", lambda: "done")
        for _ in range(200):
            if not job.running:
                break
            time.sleep(0.005)

    runner.forget_finished(keep=3)
    assert len(runner._jobs) == 3
