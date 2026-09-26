"""Background workflow orchestrator for the web UI.

Runs the same four-phase pipeline as `main.py`, but in a background thread so
the FastAPI event loop stays responsive. Phase events are dropped into a
thread-safe queue that the SSE endpoint drains. When a decision is ready,
the workflow blocks on a `threading.Event` until the operator clicks
Approve or Reject in the UI.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Make the repo root importable when this module is loaded by uvicorn.
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents import db_pool_agent, decision_agent, thread_pool_agent
from config import AppConfig, Pools, WorkloadConfig
from embeddings import embed_batch
from models import RunSummary
from remediation import apply, validate
from runbooks import index_runbooks
from storage import fetch_logs_for_run, init_schema, upsert_log_embeddings
from thread_pool_workload import run as run_workload


@dataclass
class ApprovalRequest:
    decision: Dict[str, Any]
    change: Dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


@dataclass
class RunState:
    run_id: str
    events: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    pending_approval: Optional[ApprovalRequest] = None
    thread: Optional[threading.Thread] = None
    finished: bool = False


RUNS: Dict[str, RunState] = {}


def _emit(state: RunState, **payload: Any) -> None:
    state.events.put({"ts": time.time(), **payload})


def _summary_to_dict(s: RunSummary) -> Dict[str, Any]:
    d = s.model_dump()
    d["success_rate"] = s.success_rate
    return d


def _embed_run_logs(cfg: AppConfig, run_id: str) -> None:
    pairs = fetch_logs_for_run(cfg, run_id)
    if not pairs:
        return
    texts = [entry.summary() for _, entry in pairs]
    vectors = embed_batch(cfg, texts)
    upsert_log_embeddings(
        cfg,
        ((log_id, vec) for (log_id, _), vec in zip(pairs, vectors)),
    )


def _stop_reason(cfg: AppConfig, current_cfg: AppConfig, summary: RunSummary, iteration: int) -> Optional[str]:
    ceiling = min(cfg.workload.num_users, cfg.max_pool_size)
    if summary.success_rate >= cfg.target_success_rate:
        return f"Target success_rate {cfg.target_success_rate:.0%} reached."
    if current_cfg.pools.db_pool_size >= ceiling and current_cfg.pools.thread_pool_size >= ceiling:
        return f"Both pools at ceiling ({ceiling}); no further escalation possible."
    if iteration >= cfg.max_remediation_iterations:
        return f"Hit max iterations ({cfg.max_remediation_iterations})."
    return None


def _wait_for_approval(state: RunState, decision, change) -> bool:
    req = ApprovalRequest(
        decision=decision.model_dump(),
        change={"db_pool_size": change.db_pool_size, "thread_pool_size": change.thread_pool_size},
    )
    state.pending_approval = req
    _emit(state, phase="hitl_approval", status="running",
          decision=req.decision, change=req.change)
    req.event.wait()
    state.pending_approval = None
    return req.approved


def _workflow(state: RunState, cfg: AppConfig) -> None:
    try:
        _emit(state, phase="init", status="running", message="initializing schema and runbooks")
        init_schema(cfg)
        index_runbooks(cfg)
        _emit(state, phase="init", status="done")

        _emit(state, phase="workload", status="running", label="baseline")
        baseline = run_workload(cfg)
        _emit(state, phase="workload", status="done", label="baseline",
              summary=_summary_to_dict(baseline))

        _emit(state, phase="embedding", status="running")
        _embed_run_logs(cfg, baseline.run_id)
        _emit(state, phase="embedding", status="done")

        history: List[RunSummary] = [baseline]
        current_cfg = cfg
        current_summary = baseline

        for iteration in range(1, cfg.max_remediation_iterations + 1):
            reason = _stop_reason(cfg, current_cfg, current_summary, iteration - 1)
            if reason:
                _emit(state, phase="done", status="done", reason=reason,
                      history=[_summary_to_dict(h) for h in history])
                return

            _emit(state, phase="agents", status="running", iteration=iteration)
            thread_report = thread_pool_agent.diagnose(current_cfg, current_summary)
            db_report = db_pool_agent.diagnose(current_cfg, current_summary)
            decision = decision_agent.decide(
                current_cfg, current_summary, thread_report, db_report, prior_runs=history
            )
            _emit(state, phase="agents", status="done", iteration=iteration,
                  thread_report=thread_report.model_dump(),
                  db_report=db_report.model_dump(),
                  decision=decision.model_dump())

            change = validate(current_cfg, decision)

            db_new = change.db_pool_size if change.db_pool_size is not None else current_cfg.pools.db_pool_size
            thread_new = change.thread_pool_size if change.thread_pool_size is not None else current_cfg.pools.thread_pool_size
            if db_new <= current_cfg.pools.db_pool_size and thread_new <= current_cfg.pools.thread_pool_size:
                _emit(state, phase="done", status="done",
                      reason="Decision proposes no upward change from current pools.",
                      history=[_summary_to_dict(h) for h in history])
                return

            approved = _wait_for_approval(state, decision, change)
            if not approved:
                _emit(state, phase="done", status="done",
                      reason="Operator rejected the proposed change.",
                      history=[_summary_to_dict(h) for h in history])
                return
            _emit(state, phase="hitl_approval", status="done", iteration=iteration)

            current_cfg = apply(current_cfg, change)
            _emit(state, phase="retest", status="running", iteration=iteration,
                  applied={"db_pool_size": current_cfg.pools.db_pool_size,
                           "thread_pool_size": current_cfg.pools.thread_pool_size})
            current_summary = run_workload(current_cfg)
            _embed_run_logs(current_cfg, current_summary.run_id)
            history.append(current_summary)
            _emit(state, phase="retest", status="done", iteration=iteration,
                  summary=_summary_to_dict(current_summary),
                  history=[_summary_to_dict(h) for h in history])

        _emit(state, phase="done", status="done",
              reason=f"Hit max iterations ({cfg.max_remediation_iterations}).",
              history=[_summary_to_dict(h) for h in history])
    except Exception as exc:  # pragma: no cover - surface errors to UI
        _emit(state, phase="error", status="error",
              message=str(exc), traceback=traceback.format_exc())
    finally:
        state.finished = True
        state.events.put({"ts": time.time(), "phase": "stream_end"})


def start_run(cfg_kwargs: Dict[str, Any]) -> str:
    cfg = AppConfig(
        workload=WorkloadConfig(
            scenario=cfg_kwargs.get("scenario", "combined"),
            num_users=int(cfg_kwargs.get("users", 100)),
            rng_seed=int(cfg_kwargs.get("seed", 42)),
        ),
        pools=Pools(
            db_pool_size=int(cfg_kwargs.get("db_pool", 5)),
            thread_pool_size=int(cfg_kwargs.get("thread_pool", 5)),
        ),
        target_success_rate=float(cfg_kwargs.get("target_success_rate", 1.0)),
        max_remediation_iterations=int(cfg_kwargs.get("max_iterations", 5)),
    )
    run_id = str(uuid.uuid4())
    state = RunState(run_id=run_id)
    RUNS[run_id] = state
    thread = threading.Thread(target=_workflow, args=(state, cfg), daemon=True)
    state.thread = thread
    thread.start()
    return run_id


def resolve_approval(run_id: str, approved: bool) -> bool:
    state = RUNS.get(run_id)
    if state is None or state.pending_approval is None:
        return False
    state.pending_approval.approved = approved
    state.pending_approval.event.set()
    return True
