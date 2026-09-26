# Thread Pool Exhaustion Runbook

## Symptoms

- Requests fail with `thread_timeout` outcome while waiting to be scheduled
  on the executor.
- `thread_wait_ms` climbs into the submit-timeout range.
- `thread_pool_in_use` sits at or very close to `thread_pool_size` for a
  sustained period.
- Latency degrades sharply once concurrent users exceed the worker count.

## Escalation ladder (drive success_rate to 100%)

The `<prior_runs>` block lists every run in this diagnosis session with the
`thread_pool_size` used and the resulting `thread_timeout` count.

**Choose the tier for `recommendedThreadPoolSize` based on how many prior
runs still had `thread_timeout > 0` AFTER a resize:**

| Tier | Trigger | Formula | Example (N=100) |
| ---- | ------- | ------- | --------------- |
| 1 | First iteration in which `thread_timeout > 0` appears. | `ceil(0.30 * N)` | 30 |
| 2 | Tier 1 already applied, but `success_rate < 100%` and `thread_timeout > 0` still remains. | `ceil(0.60 * N)` | 60 |
| 3 | Tier 2 already applied and `success_rate < 100%` and `thread_timeout > 0` still remains. | `ceil(0.90 * N)` | 90 |
| ceiling | Tier 3 already applied and `thread_timeout > 0` still remains. | `N` | 100 |

**Never propose a value lower than the largest `thread_pool_size` seen in
`<prior_runs>`.** Escalation is monotonic upward until `thread_timeout == 0`.

### Required output when thread_timeout > 0

- `decision`: `APPLICATION` if only the thread pool needs to change on this
  iteration; `HYBRID` if the DB pool ALSO needs a bump this iteration.
- `recommendedThreadPoolSize`: the tier value.
- `recommendedActions`: exactly one bullet — the thread pool resize.
- `executionOrder`:
  1. Resize the thread pool to the tier value.
  2. Rerun the same scenario with identical `users` and `seed`.
  3. Compare `success_rate`, `thread_timeout`, and `p95_latency_ms`.

### Bottleneck-shift note

If the previous run resized only the DB pool and this run introduces new
`thread_timeout` events (`thread_timeout > 0` when it was 0 before), that is
a bottleneck shift and the thread pool must be bumped on this iteration.
Emit `decision = "HYBRID"` if the DB pool ALSO still needs escalation.

## Do NOT put these in `recommendedActions`

- "Monitor thread pool usage" / "add alerting".
- "Investigate blocking work" — only relevant when `thread_run_ms` is
  elevated while the pool has spare capacity.
- "Compare wait vs run times" — this is how the agent decides, not an action.

## When NOT to touch the thread pool

- Failure mode is `db_timeout`, not `thread_timeout`.
- `thread_pool_in_use` never approaches `thread_pool_size`.
- Elevated `thread_wait_ms` is entirely explained by upstream `db_wait_ms`.
