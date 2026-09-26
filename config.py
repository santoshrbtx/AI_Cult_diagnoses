"""Central configuration for AICult.

Only DB_POOL_SIZE and THREAD_POOL_SIZE are considered *remediable* — the
DecisionAgent can propose new values for these, and the Python application
validates + applies them before the retest. Everything else stays fixed so
that the retest is directly comparable with the initial run.
"""

from dataclasses import dataclass, field, replace
from typing import Literal


Scenario = Literal["db_only", "thread_only", "combined"]


@dataclass(frozen=True)
class WorkloadConfig:
    scenario: Scenario = "combined"
    num_users: int = 100
    request_work_seconds: float = 0.2
    db_call_seconds: float = 0.3
    db_acquire_timeout_seconds: float = 1.0
    thread_submit_timeout_seconds: float = 1.0
    rng_seed: int = 42


@dataclass(frozen=True)
class Pools:
    db_pool_size: int = 5
    thread_pool_size: int = 5


@dataclass(frozen=True)
class AppConfig:
    workload: WorkloadConfig = field(default_factory=WorkloadConfig)
    pools: Pools = field(default_factory=Pools)

    trueforge_base_url: str = "http://localhost:8790"
    llm_model: str = "bedrock/openai-gpt-oss-120b"

    embed_model_id: str = "amazon.titan-embed-text-v2:0"
    embed_region: str = "us-east-1"
    embed_dim: int = 1024

    db_path: str = "aicult.db"
    runbooks_dir: str = "Data"

    top_k_logs: int = 8
    top_k_runbook_chunks: int = 4

    # Remediation guardrails: hard bounds the DecisionAgent's numbers get
    # clamped to before anything is applied.
    min_pool_size: int = 1
    max_pool_size: int = 200

    # Safety cap on the remediation loop — the loop normally stops when
    # success_rate hits target_success_rate OR both pools reach the ceiling.
    # This cap is a belt-and-suspenders bound to avoid a runaway if the LLM
    # keeps proposing tiny nudges.
    target_success_rate: float = 1.0
    max_remediation_iterations: int = 5


def with_pools(cfg: AppConfig, *, db_pool_size: int = None, thread_pool_size: int = None) -> AppConfig:
    pools = replace(
        cfg.pools,
        db_pool_size=db_pool_size if db_pool_size is not None else cfg.pools.db_pool_size,
        thread_pool_size=thread_pool_size if thread_pool_size is not None else cfg.pools.thread_pool_size,
    )
    return replace(cfg, pools=pools)
