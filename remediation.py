"""Approval + remediation guardrails.

The LLM proposes numbers; this module is the ONLY thing that decides which
configuration knobs are actually mutable and clamps their values. The LLM
never touches the config directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from config import AppConfig, with_pools
from models import Decision, RunSummary


@dataclass
class ApprovedChange:
    db_pool_size: Optional[int]
    thread_pool_size: Optional[int]


def _clamp(cfg: AppConfig, value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return max(cfg.min_pool_size, min(cfg.max_pool_size, int(value)))


_DB_HINT = re.compile(r"\b(?:db|database)[^\n]*?\b(?:pool|connection[s]?)[^\n]*?\b(\d{1,4})\b", re.IGNORECASE)
_THREAD_HINT = re.compile(r"\bthread[^\n]*?\bpool[^\n]*?\b(\d{1,4})\b", re.IGNORECASE)


def _first_int(pattern: re.Pattern, strings: Iterable[str]) -> Optional[int]:
    for s in strings:
        m = pattern.search(s)
        if m:
            return int(m.group(1))
    return None


def _mine_pool_sizes(decision: Decision) -> tuple[Optional[int], Optional[int]]:
    """Fallback: LLMs sometimes put the numeric target in `recommendedActions`
    text (e.g. "Resize DB connection pool to 60") but leave the JSON fields
    null. Extract the numbers so validate() still returns a real change."""
    corpus = [*decision.recommendedActions, *decision.executionOrder]
    return _first_int(_DB_HINT, corpus), _first_int(_THREAD_HINT, corpus)


def validate(cfg: AppConfig, decision: Decision) -> ApprovedChange:
    """Filter the decision down to changes we actually support + clamp them.

    Prefers the numeric fields the LLM populated. If those are null but the
    action text names a pool size, mine the number out of the text — the
    LLM is inconsistent about which slot it uses, and we don't want a valid
    remediation to silently disappear because of that.
    """
    if decision.decision == "NO_ACTION":
        return ApprovedChange(db_pool_size=None, thread_pool_size=None)
    db = decision.recommendedDbPoolSize
    thread = decision.recommendedThreadPoolSize
    if db is None or thread is None:
        mined_db, mined_thread = _mine_pool_sizes(decision)
        db = db if db is not None else mined_db
        thread = thread if thread is not None else mined_thread
    return ApprovedChange(db_pool_size=_clamp(cfg, db), thread_pool_size=_clamp(cfg, thread))


def render_for_operator(decision: Decision, change: ApprovedChange) -> str:
    lines = [
        "",
        "==================== DECISION ====================",
        f"decision: {decision.decision}",
        f"primary cause: {decision.primaryCause}",
    ]
    if decision.secondaryEffect:
        lines.append(f"secondary effect: {decision.secondaryEffect}")
    if decision.supportingEvidence:
        lines.append("supporting evidence:")
        lines.extend(f"  - {ev}" for ev in decision.supportingEvidence)
    if decision.recommendedActions:
        lines.append("recommended actions:")
        lines.extend(f"  - {a}" for a in decision.recommendedActions)
    if decision.executionOrder:
        lines.append("execution order:")
        lines.extend(f"  {i+1}. {step}" for i, step in enumerate(decision.executionOrder))
    lines.append("proposed applicable change:")
    lines.append(f"  db_pool_size    -> {change.db_pool_size}")
    lines.append(f"  thread_pool_size -> {change.thread_pool_size}")
    lines.append("==================================================")
    return "\n".join(lines)


def prompt_operator(decision: Decision, change: ApprovedChange) -> bool:
    print(render_for_operator(decision, change))
    if change.db_pool_size is None and change.thread_pool_size is None:
        print("No supported configuration change to apply.")
        return False
    reply = input("Apply this change and rerun? [y/N]: ").strip().lower()
    return reply in {"y", "yes"}


def apply(cfg: AppConfig, change: ApprovedChange) -> AppConfig:
    return with_pools(
        cfg,
        db_pool_size=change.db_pool_size,
        thread_pool_size=change.thread_pool_size,
    )


def compare(before: RunSummary, after: RunSummary) -> str:
    def delta(a: float, b: float) -> str:
        d = b - a
        sign = "+" if d >= 0 else ""
        return f"{sign}{d:.1f}"

    verdict = "unresolved"
    if after.success_rate > before.success_rate + 0.10:
        verdict = "improved"
    if after.db_timeout == 0 and after.thread_timeout > before.thread_timeout:
        verdict = "shifted (thread pool now bottleneck)"
    elif after.thread_timeout == 0 and after.db_timeout > before.db_timeout:
        verdict = "shifted (db pool now bottleneck)"

    return "\n".join(
        [
            "",
            "==================== BEFORE vs AFTER ====================",
            f"success_rate:   {before.success_rate:.2%} -> {after.success_rate:.2%}",
            f"db_timeouts:    {before.db_timeout} -> {after.db_timeout} ({delta(before.db_timeout, after.db_timeout)})",
            f"thread_timeouts:{before.thread_timeout} -> {after.thread_timeout} ({delta(before.thread_timeout, after.thread_timeout)})",
            f"p50 latency ms: {before.p50_latency_ms:.0f} -> {after.p50_latency_ms:.0f}",
            f"p95 latency ms: {before.p95_latency_ms:.0f} -> {after.p95_latency_ms:.0f}",
            f"pools:          db={before.db_pool_size}->{after.db_pool_size} thread={before.thread_pool_size}->{after.thread_pool_size}",
            f"verdict:        {verdict}",
            "=========================================================",
        ]
    )
