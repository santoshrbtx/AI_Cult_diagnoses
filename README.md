# AICult — Local Incident Diagnosis & Remediation

A local Python console app that simulates a resource-pool incident, uses three
LLM agents (via the TrueFoundry / TrueForge SDK) to diagnose it, asks the
operator to approve a configuration change, and reruns the same workload to
verify whether the change helped.

## Mental model

```
                Phase 1                    Phase 2                    Phase 3                       Phase 4
+----------+   +----------+   logs    +----------+   embed   +---------------+    JSON     +---------------+
| Operator |-->| Workload |---------->|  SQLite  |---------->| Diagnostic    |------------>|   Decision    |
|          |   | (100 VUs)|           | + vec0   |           | agents x2     |    reports  |    agent      |
|          |   +----------+           +----------+           +---------------+             +-------+-------+
|          |         ^                     ^                        |                              |
|          |         | rerun               | retrieval              |                              v
|          |         |  (same seed)        |  (top-k logs +         |                       proposes change
|          |         |                     |   runbook chunks)      |                              |
|          |<--------+---------------------+------------------------+------------------------------+
|          |                approval prompt (validated + clamped by Python)                        |
|          |                                                                                       |
|          |----------->  applies pool changes ->  reruns workload  ->  before/after report        |
+----------+                                                                                       |
                                                                                                   v
                                                                                          verdict: improved /
                                                                                          unresolved / shifted
```

**Flow, step by step**

1. **Phase 1 — Workload.** `thread_pool_workload.py` spawns 100 virtual users
   against a bounded async DB pool (`asyncio.Semaphore`) and a bounded
   `ThreadPoolExecutor(max_workers=N)`. Scenario is `db_only`, `thread_only`,
   or `combined`. One `asyncio.Queue` consumer writes every `LogEntry` to
   SQLite so there is only ever one SQLite writer.
2. **Phase 2 — Retrieval prep.** `embeddings.py` batch-embeds every log
   entry via Titan-on-Bedrock (`amazon.titan-embed-text-v2:0`). The two
   Markdown runbooks under `Data/` are embedded once at startup. Everything
   lives in SQLite virtual tables backed by `sqlite-vec`.
3. **Phase 3 — Agents.** Three agent LLM calls (all via TrueForge SDK):
   - `ThreadPoolAgent` — retrieves `thread_only` + `combined` logs and the
     thread pool runbook chunks. Emits an `AgentReport` (JSON, schema-enforced).
   - `DatabasePoolAgent` — same shape, for the DB side.
   - `DecisionAgent` — takes both peer reports + runbook context and emits a
     final `Decision` (HYBRID | APPLICATION | DATABASE_POOL | NO_ACTION).
   All untrusted content (logs, runbooks, peer reports) is XML-escaped and
   wrapped in `<logs>`, `<runbook>`, `<peer_report>` tags. System
   instructions tell the agent to treat those blocks as evidence only.
4. **Phase 4 — Approval, remediation, verification.** `remediation.py`
   validates the decision: it filters `recommendedDbPoolSize` /
   `recommendedThreadPoolSize` to just the knobs Python knows how to apply,
   clamps them to `[min_pool_size, max_pool_size]`, and shows the plan to
   the operator. On rejection: stop. On approval: apply the change, rerun
   the same scenario/users/seed, then print a before-vs-after report with
   a verdict — improved, unresolved, or bottleneck shifted.

## Module map

| File | Responsibility |
| --- | --- |
| `main.py` | Orchestrates all four phases. |
| `config.py` | `AppConfig`/`WorkloadConfig`/`Pools`; `with_pools()` for retest. |
| `models.py` | Pydantic contracts: `LogEntry`, `RunSummary`, `AgentReport`, `Decision`. |
| `thread_pool_workload.py` | Phase 1 simulator; single SQLite-writer queue. |
| `storage.py` | SQLite schema + `sqlite-vec` virtual tables + retrieval helpers. |
| `embeddings.py` | Titan-on-Bedrock embed calls (isolated so we can swap providers). |
| `runbooks.py` | Loads, chunks, and indexes the Markdown runbooks. |
| `agents/prompts.py` | XML-escape helpers + safety preamble. |
| `agents/llm.py` | Wraps TrueForge SDK; asks for JSON via `response_format`. |
| `agents/thread_pool_agent.py` | ThreadPoolAgent diagnosis. |
| `agents/db_pool_agent.py` | DatabasePoolAgent diagnosis. |
| `agents/decision_agent.py` | DecisionAgent — final call. |
| `remediation.py` | Approval, validation/clamp, apply, before/after compare. |
| `Data/DB_pool_runbook.md` | Runbook — indexed with source `DB_pool_runbook`. |
| `Data/thread_pool_runbook.md` | Runbook — indexed with source `thread_pool_runbook`. |

## Data contracts (see `models.py` for the canonical schema)

- **`LogEntry`** — one virtual-user request. Includes outcome
  (`success` / `db_timeout` / `thread_timeout` / `error`), latency, and per-pool
  wait/run times and in-use/size gauges.
- **`AgentReport`** — `bottleneckDetected`, `severity`, `findings`,
  `evidenceLogIds`, `metricsSummary`, `recommendation`, optional
  `suggestedPoolSize`.
- **`Decision`** — `decision`, `primaryCause`, `secondaryEffect`,
  `supportingEvidence`, `recommendedActions`, `executionOrder`,
  `requiresApproval`, optional `recommendedDbPoolSize` /
  `recommendedThreadPoolSize`.

The LLM cannot execute anything. It proposes numbers; `remediation.validate()`
is the only place that maps a `Decision` onto real config knobs.

## Prerequisites

- TrueForge running locally at `http://localhost:8790` with a chat model
  configured (default: `bedrock/openai-gpt-oss-120b`).
- AWS credentials in the environment with access to Bedrock Titan
  embeddings in `us-east-1` (override in `config.py` if needed).
- Python 3.11+.

Confirm the TrueForge model is present:
```
curl http://localhost:8790/api/v1/models
```

## Install

```
pip install -r requirements.txt
```

## Run — CLI

```
python main.py --scenario combined --users 100 --db-pool 5 --thread-pool 5 --seed 42
```

The operator prompt at the end of Phase 3 asks whether to apply the proposed
pool change. On `y`, the same workload runs again with the new pools and a
before/after comparison is printed. The loop repeats until `success_rate`
hits the target, both pools reach the ceiling, the operator rejects, or the
iteration cap is hit.

## Run — Angular web UI (FastAPI + SSE + Angular 17)

Two processes: a Python API and an Angular dev server.

**Terminal 1 — start the API:**

```
uvicorn backend.api:app --reload --port 8000
```

Endpoints:
- `POST /run/start` — kick off a workflow (same knobs as the CLI + `target_success_rate`, `max_iterations`).
- `GET  /run/{run_id}/events` — Server-Sent Events stream of phase events.
- `POST /run/{run_id}/approve` / `.../reject` — resolves the HITL pause.
- Interactive docs at `http://localhost:8000/docs`.

**Terminal 2 — start Angular:**

```
cd frontend
npm install    # first time only
npm start      # or: ng serve
```

Opens `http://localhost:4200`. The UI has:
- A **configuration form** (sidebar) with a `Start workflow` button — clicking it POSTs `/run/start` and subscribes to the SSE event stream. This is the "click a button to run `python main.py`" flow.
- A **workflow stepper** — five steps (Phase 1 Workload / Phase 2 Embedding / Phase 3 Agents / Phase 4 HITL Approval / Phase 4 Retest) that flip pending → running → done as the SSE events arrive.
- A **decision panel** that appears when the DecisionAgent has a proposal. Shows proposed pool sizes (with delta tiles), decision label, primary/secondary cause, evidence, execution order, and **Approve & retest / Reject & stop** buttons — the HITL gate.
- A **per-iteration table** that grows with each retest.

## Run — Streamlit UI

```
streamlit run ui/app.py
```

The UI mirrors the same workflow as the CLI:

- **Sidebar** — configuration (scenario, users, initial pool sizes, seed,
  target success rate, max iterations). Locked once the run starts; use
  "Reset workflow" to unlock.
- **Workflow row** — five status cards (Phase 1 Workload / Phase 2
  Embedding / Phase 3 Agents / Phase 4 HITL Approval / Phase 4 Retest) that
  flip pending → running → done as each phase executes.
- **Per-iteration decision panel** — appears when the DecisionAgent has a
  proposal. Shows the proposed pool sizes with delta vs. current, the
  decision label, primary/secondary cause, supporting evidence, and
  execution order. Two buttons: **Approve & retest** or **Reject & stop**.
- **Per-iteration results table** — grows with each retest: pool sizes,
  success rate, timeout counts, p50 / p95 latency. Same numbers as the CLI
  `compare()` output, one row per iteration.

The UI reuses `config`, `agents`, `thread_pool_workload`, `remediation`,
`runbooks`, `storage`, and `embeddings` directly — no separate API layer.

## Hackathon deck

A print-ready presentation PDF lives at `docs/AICult_Presentation.pdf`
(regenerate with `python docs/generate_pdf.py`). It covers the problem, what
the project does (DB pool + thread pool management, extensible pattern), the
RAG-driven decision-tree workflow, a real end-to-end result table
(15% → 60% → 83% → 100% success across three approved retests), the
architecture, and future extensions.

## Retest comparability

- Same `--seed`, `--scenario`, and `--users` are reused for the rerun.
- Only pool sizes change between the two runs.
- Both runs are persisted (`runs` table + full log rows) so the comparison
  can be reproduced later.
