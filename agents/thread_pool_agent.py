"""ThreadPoolAgent: diagnoses thread-pool-related issues from thread_only +
combined logs and the thread_pool_runbook.
"""

from __future__ import annotations

from config import AppConfig
from embeddings import embed_one
from models import AgentReport, RunSummary
from storage import search_docs, search_logs

from .llm import call_json
from .prompts import SAFETY_PREAMBLE, logs_block, runbook_block, summary_block


AGENT_INSTRUCTIONS = (
    SAFETY_PREAMBLE + "\n"
    "You are the ThreadPoolAgent. Focus on ThreadPoolExecutor exhaustion, "
    "thread queue timeouts, and blocking work occupying workers. Ignore "
    "database concerns unless they clearly cascade into thread saturation."
)


AGENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "agent",
        "bottleneckDetected",
        "severity",
        "findings",
        "evidenceLogIds",
        "metricsSummary",
        "recommendation",
    ],
    "properties": {
        "agent": {"const": "thread_pool"},
        "bottleneckDetected": {"type": "boolean"},
        "severity": {"enum": ["none", "low", "medium", "high", "critical"]},
        "findings": {"type": "array", "items": {"type": "string"}},
        "evidenceLogIds": {"type": "array", "items": {"type": "string"}},
        "metricsSummary": {"type": "string"},
        "recommendation": {"type": "string"},
        "suggestedPoolSize": {"type": ["integer", "null"]},
    },
}


def diagnose(cfg: AppConfig, summary: RunSummary) -> AgentReport:
    query = "thread pool exhaustion, ThreadPoolExecutor timeouts, blocking work"
    q_vec = embed_one(cfg, query)

    log_hits = search_logs(
        cfg, q_vec, summary.run_id, scenarios=["thread_only", "combined"], k=cfg.top_k_logs
    )
    doc_hits = search_docs(cfg, q_vec, source_prefix="thread_pool_runbook", k=cfg.top_k_runbook_chunks)

    prompt = "\n".join(
        [
            summary_block(summary.model_dump_json()),
            logs_block(log_hits),
            runbook_block(doc_hits),
            "Return your diagnosis as JSON matching the schema.",
        ]
    )
    data = call_json(
        cfg,
        instructions=AGENT_INSTRUCTIONS,
        prompt=prompt,
        schema=AGENT_SCHEMA,
        schema_name="ThreadPoolReport",
    )
    return AgentReport.model_validate(data)
