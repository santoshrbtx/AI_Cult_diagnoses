"""Pydantic data contracts shared across modules."""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


Outcome = Literal["success", "db_timeout", "thread_timeout", "error"]


class LogEntry(BaseModel):
    """One virtual-user request; the atom of Phase 1 output."""

    run_id: str
    user_id: int
    scenario: str
    outcome: Outcome
    started_at: float
    ended_at: float
    latency_ms: float

    db_wait_ms: Optional[float] = None
    db_call_ms: Optional[float] = None
    db_pool_in_use: int = 0
    db_pool_size: int = 0

    thread_wait_ms: Optional[float] = None
    thread_run_ms: Optional[float] = None
    thread_pool_in_use: int = 0
    thread_pool_size: int = 0

    error: Optional[str] = None

    def summary(self) -> str:
        return (
            f"user={self.user_id} scenario={self.scenario} outcome={self.outcome} "
            f"latency={self.latency_ms:.0f}ms db_wait={self.db_wait_ms} "
            f"thread_wait={self.thread_wait_ms} db_pool={self.db_pool_in_use}/{self.db_pool_size} "
            f"thread_pool={self.thread_pool_in_use}/{self.thread_pool_size} "
            f"err={self.error or '-'}"
        )


class RunSummary(BaseModel):
    run_id: str
    scenario: str
    total: int
    success: int
    db_timeout: int
    thread_timeout: int
    error: int
    p50_latency_ms: float
    p95_latency_ms: float
    db_pool_size: int
    thread_pool_size: int

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 0.0


class AgentReport(BaseModel):
    """Output shape for ThreadPoolAgent and DatabasePoolAgent."""

    agent: Literal["thread_pool", "db_pool"]
    bottleneckDetected: bool
    severity: Literal["none", "low", "medium", "high", "critical"]
    findings: List[str] = Field(default_factory=list)
    evidenceLogIds: List[str] = Field(default_factory=list)
    metricsSummary: str = ""
    recommendation: str = ""
    suggestedPoolSize: Optional[int] = None


class Decision(BaseModel):
    """Output shape for DecisionAgent."""

    decision: Literal["HYBRID", "APPLICATION", "DATABASE_POOL", "NO_ACTION"]
    primaryCause: str
    secondaryEffect: str = ""
    supportingEvidence: List[str] = Field(default_factory=list)
    recommendedActions: List[str] = Field(default_factory=list)
    executionOrder: List[str] = Field(default_factory=list)
    requiresApproval: bool = True
    recommendedDbPoolSize: Optional[int] = None
    recommendedThreadPoolSize: Optional[int] = None
