"""DecisionAgent: takes both peer reports plus retrieved runbook context and
returns the final structured Decision.
"""

from __future__ import annotations

from typing import Sequence

from config import AppConfig
from embeddings import embed_one
from models import AgentReport, Decision, RunSummary
from storage import search_docs

from .llm import call_json
from .prompts import (
    SAFETY_PREAMBLE,
    peer_report_block,
    prior_runs_block,
    runbook_block,
    summary_block,
)


AGENT_INSTRUCTIONS = (
    SAFETY_PREAMBLE + "\n"
    "You are the DecisionAgent. Given peer reports from ThreadPoolAgent and "
    "DatabasePoolAgent plus the relevant runbook chunks, decide the primary "
    "cause and emit numeric recommendations.\n"
    "\n"
    "The `decision` field MUST be chosen using these EXACT rules:\n"
    "  - DATABASE_POOL: only the DB pool is being changed. `recommendedDbPoolSize` must be set; `recommendedThreadPoolSize` must be null.\n"
    "  - APPLICATION:   only the thread pool is being changed. `recommendedThreadPoolSize` must be set; `recommendedDbPoolSize` must be null.\n"
    "  - HYBRID:        BOTH pools are being changed. Both `recommendedDbPoolSize` AND `recommendedThreadPoolSize` must be set.\n"
    "  - NO_ACTION:     no change proposed. Both recommendations must be null.\n"
    "\n"
    "Never emit APPLICATION with a `recommendedDbPoolSize`. Never emit "
    "DATABASE_POOL with a `recommendedThreadPoolSize`. The label MUST match "
    "the numbers.\n"
    "\n"
    "CRITICAL: whenever you recommend a pool resize, the target number MUST "
    "appear in the corresponding numeric field (`recommendedDbPoolSize` or "
    "`recommendedThreadPoolSize`) as an integer. Putting the number ONLY in "
    "the `recommendedActions` text (e.g. \"Resize DB pool to 60\") is a bug "
    "— the numeric field must ALSO be set to 60. If both pools are being "
    "resized under HYBRID, BOTH numeric fields must be integers.\n"
    "\n"
    "Do not simply enlarge whichever pool a peer flagged; consider whether "
    "the bottleneck cascades. Only recommend HYBRID if BOTH pools show "
    "independent saturation. Prefer NO_ACTION when signals are weak."
)


AGENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision",
        "primaryCause",
        "secondaryEffect",
        "supportingEvidence",
        "recommendedActions",
        "executionOrder",
        "requiresApproval",
    ],
    "properties": {
        "decision": {"enum": ["HYBRID", "APPLICATION", "DATABASE_POOL", "NO_ACTION"]},
        "primaryCause": {"type": "string"},
        "secondaryEffect": {"type": "string"},
        "supportingEvidence": {"type": "array", "items": {"type": "string"}},
        "recommendedActions": {"type": "array", "items": {"type": "string"}},
        "executionOrder": {"type": "array", "items": {"type": "string"}},
        "requiresApproval": {"type": "boolean"},
        "recommendedDbPoolSize": {"type": ["integer", "null"]},
        "recommendedThreadPoolSize": {"type": ["integer", "null"]},
    },
}


def decide(
    cfg: AppConfig,
    summary: RunSummary,
    thread_report: AgentReport,
    db_report: AgentReport,
    prior_runs: Sequence[RunSummary] = (),
) -> Decision:
    query = "resolve resource pool exhaustion, choose remediation between thread pool and database pool, escalation ladder"
    q_vec = embed_one(cfg, query)

    doc_hits_db = search_docs(cfg, q_vec, source_prefix="DB_pool_runbook", k=cfg.top_k_runbook_chunks)
    doc_hits_thread = search_docs(cfg, q_vec, source_prefix="thread_pool_runbook", k=cfg.top_k_runbook_chunks)

    prompt = "\n".join(
        [
            summary_block(summary.model_dump_json()),
            prior_runs_block(prior_runs),
            peer_report_block("thread_pool", thread_report.model_dump_json()),
            peer_report_block("db_pool", db_report.model_dump_json()),
            runbook_block(list(doc_hits_db) + list(doc_hits_thread)),
            "Consult <prior_runs> to pick the correct escalation tier. "
            "Return the decision as JSON matching the schema.",
        ]
    )
    data = call_json(
        cfg,
        instructions=AGENT_INSTRUCTIONS,
        prompt=prompt,
        schema=AGENT_SCHEMA,
        schema_name="Decision",
    )
    return Decision.model_validate(data)
