"""AICult orchestrator.

Phase 1 -> Phase 2 -> Phase 3 -> Phase 4:
  1. run workload
  2. embed + index new logs (runbooks are indexed once at startup)
  3. ThreadPoolAgent + DatabasePoolAgent + DecisionAgent
  4. operator approves or rejects; if approved, apply + rerun + compare.
     LOOP: keep re-diagnosing / re-approving / rerunning until success_rate
     hits the target (default 100%), the operator declines, or the safety
     cap on iterations is reached.
"""

from __future__ import annotations

import argparse

from agents import db_pool_agent, decision_agent, thread_pool_agent
from config import AppConfig, WorkloadConfig, Pools
from embeddings import embed_batch
from models import RunSummary
from remediation import apply, compare, prompt_operator, validate
from runbooks import index_runbooks
from storage import fetch_logs_for_run, init_schema, upsert_log_embeddings
from thread_pool_workload import run as run_workload


def _embed_run_logs(cfg: AppConfig, run_id: str) -> None:
    pairs = fetch_logs_for_run(cfg, run_id)
    if not pairs:
        return
    texts = [entry.summary() for _, entry in pairs]
    vectors = embed_batch(cfg, texts)
    upsert_log_embeddings(cfg, ((log_id, vec) for (log_id, _), vec in zip(pairs, vectors)))


def _print_summary(label: str, s: RunSummary) -> None:
    print(
        f"[{label}] scenario={s.scenario} total={s.total} success={s.success} "
        f"db_timeout={s.db_timeout} thread_timeout={s.thread_timeout} "
        f"p50={s.p50_latency_ms:.0f}ms p95={s.p95_latency_ms:.0f}ms "
        f"pools(db={s.db_pool_size},thread={s.thread_pool_size})"
    )


def parse_args() -> AppConfig:
    ap = argparse.ArgumentParser(description="AICult incident diagnosis")
    ap.add_argument("--scenario", choices=["db_only", "thread_only", "combined"], default="combined")
    ap.add_argument("--users", type=int, default=100)
    ap.add_argument("--db-pool", type=int, default=5)
    ap.add_argument("--thread-pool", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-iterations", type=int, default=5,
                    help="Safety cap on the DecisionAgent remediation loop.")
    ap.add_argument("--target-success", type=float, default=1.0,
                    help="Stop looping once success_rate reaches this value.")
    args = ap.parse_args()
    return AppConfig(
        workload=WorkloadConfig(scenario=args.scenario, num_users=args.users, rng_seed=args.seed),
        pools=Pools(db_pool_size=args.db_pool, thread_pool_size=args.thread_pool),
        max_remediation_iterations=args.max_iterations,
        target_success_rate=args.target_success,
    )


def main() -> None:
    cfg = parse_args()
    init_schema(cfg)
    index_runbooks(cfg)

    print(f"[phase 1] running workload scenario={cfg.workload.scenario} ...")
    baseline = run_workload(cfg)
    _print_summary("baseline", baseline)

    print("[phase 2] embedding + indexing logs ...")
    _embed_run_logs(cfg, baseline.run_id)

    history: list[RunSummary] = [baseline]
    current_cfg = cfg
    current_summary = baseline
    # Ceiling for any pool = one worker per concurrent user, capped at the
    # deterministic max_pool_size guardrail.
    ceiling = min(cfg.workload.num_users, cfg.max_pool_size)

    for iteration in range(1, cfg.max_remediation_iterations + 1):
        if current_summary.success_rate >= cfg.target_success_rate:
            print(f"[phase 4] target success_rate {cfg.target_success_rate:.0%} reached; stopping.")
            break
        if (
            current_cfg.pools.db_pool_size >= ceiling
            and current_cfg.pools.thread_pool_size >= ceiling
        ):
            print(
                f"[phase 4] both pools already at ceiling ({ceiling}); no further "
                f"escalation possible. Final success_rate: {current_summary.success_rate:.2%}."
            )
            break

        print(f"[phase 3] iteration {iteration}: running diagnostic agents ...")
        thread_report = thread_pool_agent.diagnose(current_cfg, current_summary)
        print(f"  thread_pool: bottleneck={thread_report.bottleneckDetected} severity={thread_report.severity}")
        db_report = db_pool_agent.diagnose(current_cfg, current_summary)
        print(f"  db_pool:     bottleneck={db_report.bottleneckDetected} severity={db_report.severity}")
        decision = decision_agent.decide(
            current_cfg, current_summary, thread_report, db_report, prior_runs=history
        )
        print(f"  decision:    {decision.decision}")

        change = validate(current_cfg, decision)

        # If the LLM proposed no-ops (values equal to or lower than current
        # pool sizes), treat as "nothing more to try" and stop instead of
        # asking the operator to rubber-stamp an identity change.
        db_new = change.db_pool_size if change.db_pool_size is not None else current_cfg.pools.db_pool_size
        thread_new = change.thread_pool_size if change.thread_pool_size is not None else current_cfg.pools.thread_pool_size
        if db_new <= current_cfg.pools.db_pool_size and thread_new <= current_cfg.pools.thread_pool_size:
            print(
                f"[phase 4] decision proposes no upward change from current pools "
                f"(db={current_cfg.pools.db_pool_size}, thread={current_cfg.pools.thread_pool_size}); stopping."
            )
            break

        if not prompt_operator(decision, change):
            print("[phase 4] operator rejected or no supported change; stopping.")
            return

        current_cfg = apply(current_cfg, change)
        print(
            f"[phase 4] iteration {iteration}: applied pools db={current_cfg.pools.db_pool_size} "
            f"thread={current_cfg.pools.thread_pool_size}. Rerunning ..."
        )
        prev = current_summary
        current_summary = run_workload(current_cfg)
        _print_summary(f"after#{iteration}", current_summary)
        _embed_run_logs(current_cfg, current_summary.run_id)
        history.append(current_summary)
        print(compare(prev, current_summary))
    else:
        print(
            f"[phase 4] hit safety cap of {cfg.max_remediation_iterations} iterations "
            f"without reaching target {cfg.target_success_rate:.0%}. Final success_rate: "
            f"{current_summary.success_rate:.2%}."
        )


if __name__ == "__main__":
    main()
