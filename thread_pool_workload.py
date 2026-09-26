"""Phase 1: simulate 100 concurrent virtual users against a bounded DB
connection pool (asyncio.Semaphore) and a bounded ThreadPoolExecutor.

Three scenarios expose different bottlenecks:
- db_only: pure DB pool exhaustion
- thread_only: pure thread pool exhaustion (blocking work on the executor)
- combined: both pools are stressed

Each virtual user produces exactly one LogEntry. A single asyncio.Queue
consumer writes them to SQLite so we never contend for the SQLite writer.
"""

from __future__ import annotations

import asyncio
import random
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List

from config import AppConfig
from models import LogEntry, RunSummary
from storage import fetch_logs_for_run, insert_log, save_run_summary


class _DbPool:
    """Bounded async DB pool. `acquire()` waits (with timeout) for a slot."""

    def __init__(self, size: int):
        self.size = size
        self._sem = asyncio.Semaphore(size)
        self.in_use = 0

    async def acquire(self, timeout: float):
        await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
        self.in_use += 1

    def release(self):
        self.in_use -= 1
        self._sem.release()


def _blocking_thread_work(seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass  # busy-wait so the worker is genuinely occupied


async def _virtual_user(
    cfg: AppConfig,
    run_id: str,
    user_id: int,
    db_pool: _DbPool,
    thread_pool: ThreadPoolExecutor,
    thread_in_use: List[int],  # boxed counter
    queue: asyncio.Queue,
) -> None:
    scenario = cfg.workload.scenario
    started = time.perf_counter()
    entry = LogEntry(
        run_id=run_id,
        user_id=user_id,
        scenario=scenario,
        outcome="success",
        started_at=started,
        ended_at=started,
        latency_ms=0.0,
        db_pool_size=db_pool.size,
        thread_pool_size=thread_pool._max_workers,
    )

    try:
        # DB step
        if scenario in ("db_only", "combined"):
            wait_start = time.perf_counter()
            try:
                await db_pool.acquire(cfg.workload.db_acquire_timeout_seconds)
            except asyncio.TimeoutError:
                entry.outcome = "db_timeout"
                entry.db_wait_ms = (time.perf_counter() - wait_start) * 1000
                entry.db_pool_in_use = db_pool.in_use
                raise
            entry.db_wait_ms = (time.perf_counter() - wait_start) * 1000
            entry.db_pool_in_use = db_pool.in_use
            try:
                call_start = time.perf_counter()
                await asyncio.sleep(cfg.workload.db_call_seconds)
                entry.db_call_ms = (time.perf_counter() - call_start) * 1000
            finally:
                db_pool.release()

        # Thread pool step
        if scenario in ("thread_only", "combined"):
            wait_start = time.perf_counter()
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(
                thread_pool, _blocking_thread_work, cfg.workload.request_work_seconds
            )
            thread_in_use[0] += 1
            entry.thread_pool_in_use = thread_in_use[0]
            try:
                await asyncio.wait_for(
                    fut, timeout=cfg.workload.thread_submit_timeout_seconds
                )
            except asyncio.TimeoutError:
                entry.outcome = "thread_timeout"
                entry.thread_wait_ms = (time.perf_counter() - wait_start) * 1000
                raise
            finally:
                thread_in_use[0] -= 1
            entry.thread_wait_ms = (time.perf_counter() - wait_start) * 1000
            entry.thread_run_ms = cfg.workload.request_work_seconds * 1000
    except asyncio.TimeoutError:
        pass
    except Exception as exc:  # pragma: no cover - defensive
        entry.outcome = "error"
        entry.error = f"{type(exc).__name__}: {exc}"
    finally:
        entry.ended_at = time.perf_counter()
        entry.latency_ms = (entry.ended_at - started) * 1000
        await queue.put(entry)


async def _writer(cfg: AppConfig, queue: asyncio.Queue, stop_after: int) -> None:
    written = 0
    while written < stop_after:
        entry: LogEntry = await queue.get()
        insert_log(cfg, entry)
        written += 1


def _summarize(run_id: str, scenario: str, entries: List[LogEntry], db_pool_size: int, thread_pool_size: int) -> RunSummary:
    latencies = sorted(e.latency_ms for e in entries)
    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(round(p * (len(latencies) - 1))))
        return latencies[idx]
    counts = {"success": 0, "db_timeout": 0, "thread_timeout": 0, "error": 0}
    for e in entries:
        counts[e.outcome] = counts.get(e.outcome, 0) + 1
    return RunSummary(
        run_id=run_id,
        scenario=scenario,
        total=len(entries),
        success=counts["success"],
        db_timeout=counts["db_timeout"],
        thread_timeout=counts["thread_timeout"],
        error=counts["error"],
        p50_latency_ms=pct(0.50),
        p95_latency_ms=pct(0.95),
        db_pool_size=db_pool_size,
        thread_pool_size=thread_pool_size,
    )


async def _run_async(cfg: AppConfig) -> RunSummary:
    random.seed(cfg.workload.rng_seed)
    run_id = str(uuid.uuid4())

    db_pool = _DbPool(cfg.pools.db_pool_size)
    thread_pool = ThreadPoolExecutor(max_workers=cfg.pools.thread_pool_size)
    thread_in_use = [0]

    queue: asyncio.Queue = asyncio.Queue()
    writer_task = asyncio.create_task(_writer(cfg, queue, cfg.workload.num_users))

    users = [
        _virtual_user(cfg, run_id, i, db_pool, thread_pool, thread_in_use, queue)
        for i in range(cfg.workload.num_users)
    ]
    await asyncio.gather(*users)
    await writer_task
    thread_pool.shutdown(wait=False)

    entries = [entry for _, entry in fetch_logs_for_run(cfg, run_id)]
    summary = _summarize(
        run_id, cfg.workload.scenario, entries, cfg.pools.db_pool_size, cfg.pools.thread_pool_size
    )
    save_run_summary(cfg, run_id, cfg.workload.scenario, cfg.pools, summary.model_dump_json())
    return summary


def run(cfg: AppConfig) -> RunSummary:
    """Blocking entrypoint that returns the run summary."""
    return asyncio.run(_run_async(cfg))
