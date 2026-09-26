# Database Connection Pool Exhaustion Runbook

## Symptoms

- Requests fail with `db_timeout` outcome while waiting for a connection.
- `db_wait_ms` climbs into the acquire-timeout range.
- `db_pool_in_use` sits at or very close to `db_pool_size` for a sustained period.
- Success rate drops sharply once concurrent users exceed pool size.

## Escalation ladder (drive success_rate to 100%)

The `<prior_runs>` block in the prompt lists every run that has already been
executed in this diagnosis session, oldest first, with pool sizes and
resulting `success_rate` / `db_timeout` counts.

**Choose the tier for `recommendedDbPoolSize` based on how many prior runs
still had `db_timeout > 0` on the DB pool AFTER a resize:**

| Tier | Trigger | Formula | Example (N=100) |
| ---- | ------- | ------- | --------------- |
| 1 | First remediation attempt (only the baseline run exists). | `ceil(0.30 * N)` | 30 |
| 2 | Tier 1 already applied, but `success_rate < 100%` and `db_timeout > 0` still remains. | `ceil(0.60 * N)` | 60 |
| 3 | Tier 2 already applied and `success_rate < 100%` and `db_timeout > 0` still remains. | `ceil(0.90 * N)` | 90 |
| ceiling | Tier 3 already applied and `db_timeout > 0` still remains. | `N` (one connection per user) | 100 |

**Never propose a pool size lower than the largest `db_pool_size` seen in
`<prior_runs>`.** Escalation is monotonic upward until `db_timeout == 0`.

`N` is the peak concurrent user count of the current run (available as
`total` in the run summary for a single-batch workload).

### Required output when db_timeout > 0

- `decision`: `DATABASE_POOL` if only the DB pool needs to change on this
  iteration; `HYBRID` if the thread pool ALSO needs a bump this iteration
  (per the thread runbook).
- `recommendedDbPoolSize`: the tier value from the table above.
- `recommendedActions`: exactly one bullet — the DB pool resize.
- `executionOrder`:
  1. Resize the DB connection pool to the tier value.
  2. Rerun the same scenario with identical `users` and `seed`.
  3. Compare `success_rate`, `db_timeout`, and `p95_latency_ms`.

### Worked examples

- Baseline: `db_pool_size = 5`, N=100, `success_rate = 15%`. Tier 1 applies:
  `recommendedDbPoolSize = 30`.
- After Tier 1: `db_pool_size = 30`, N=100, `success_rate = 52%`,
  `db_timeout = 40`. Tier 2 applies: `recommendedDbPoolSize = 60`.
- After Tier 2: `db_pool_size = 60`, N=100, `success_rate = 88%`,
  `db_timeout = 12`. Tier 3 applies: `recommendedDbPoolSize = 90`.
- After Tier 3: `db_pool_size = 90`, N=100, `db_timeout = 4`. Ceiling
  applies: `recommendedDbPoolSize = 100`.

## Do NOT put these in `recommendedActions`

These are pre-conditions the agent checks internally. They are NOT
operator-executable steps and MUST NOT appear as bullets:

- "Monitor `db_pool_in_use`" / "add alerting" / "watch the dashboard".
- "Investigate query performance" — only relevant when `db_call_ms` is
  elevated *while the pool has spare capacity*, which is not the case when
  `db_timeout > 0`.
- "Compare pool usage" / "inspect wait distribution" — these are how you
  decide, not what the operator does.

## When NOT to touch the DB pool

- Failure mode is `thread_timeout`, not `db_timeout`.
- `db_pool_in_use` never approaches `db_pool_size` even in the current run.
- `db_call_ms` (per-call latency) is elevated while the pool is NOT
  saturated — the query itself is slow. In that case emit
  `decision = "NO_ACTION"` and note the cause in `secondaryEffect`.
