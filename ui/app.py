"""Streamlit UI for AICult — workflow visualization + HITL approval.

Run from the repo root:

    streamlit run ui/app.py

The UI reuses every backend module directly (no separate API layer). It
drives the same four phases as `main.py`, but replaces the CLI approval
prompt with an in-page Approve / Reject button gate.
"""

from __future__ import annotations

import os
import sys

# Make the project root importable when Streamlit runs this file directly.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st

from agents import db_pool_agent, decision_agent, thread_pool_agent
from config import AppConfig, Pools, WorkloadConfig
from embeddings import embed_batch
from remediation import apply, validate
from runbooks import index_runbooks
from storage import fetch_logs_for_run, init_schema, upsert_log_embeddings
from thread_pool_workload import run as run_workload


PHASES = [
    "Phase 1: Workload",
    "Phase 2: Embedding",
    "Phase 3: Agents",
    "Phase 4: HITL Approval",
    "Phase 4: Retest",
]

STATUS_ICON = {"pending": "⚪", "running": "⏳", "done": "✅", "error": "❌"}


# ---------- session state ----------

def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("phase", "config")  # config -> baseline -> iterating -> awaiting_approval -> applying -> done
    ss.setdefault("cfg", None)
    ss.setdefault("current_cfg", None)
    ss.setdefault("current_summary", None)
    ss.setdefault("history", [])
    ss.setdefault("iteration", 0)
    ss.setdefault("decision", None)
    ss.setdefault("change", None)
    ss.setdefault("thread_report", None)
    ss.setdefault("db_report", None)
    ss.setdefault("phase_status", {name: "pending" for name in PHASES})
    ss.setdefault("done_reason", None)


def _set_status(name: str, status: str) -> None:
    st.session_state.phase_status[name] = status


def _reset_state() -> None:
    for k in list(st.session_state.keys()):
        del st.session_state[k]


# ---------- helpers ----------

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


def _stop_reason() -> str | None:
    ss = st.session_state
    cfg: AppConfig = ss.cfg
    ceiling = min(cfg.workload.num_users, cfg.max_pool_size)
    s = ss.current_summary
    if s is None:
        return None
    if s.success_rate >= cfg.target_success_rate:
        return f"Target success_rate {cfg.target_success_rate:.0%} reached."
    pools = ss.current_cfg.pools
    if pools.db_pool_size >= ceiling and pools.thread_pool_size >= ceiling:
        return f"Both pools at ceiling ({ceiling}); no further escalation possible."
    if ss.iteration >= cfg.max_remediation_iterations:
        return f"Hit max iterations ({cfg.max_remediation_iterations})."
    return None


# ---------- rendering ----------

def _render_phase_cards() -> None:
    st.subheader("Workflow")
    cols = st.columns(len(PHASES))
    for col, name in zip(cols, PHASES):
        status = st.session_state.phase_status.get(name, "pending")
        col.markdown(
            f"**{name}**\n\n{STATUS_ICON.get(status, '⚪')} {status.title()}"
        )


def _render_iteration_table() -> None:
    history = st.session_state.history
    if not history:
        return
    st.subheader("Per-iteration results")
    rows = []
    for i, s in enumerate(history):
        rows.append(
            {
                "step": "baseline" if i == 0 else f"iter {i}",
                "db_pool": s.db_pool_size,
                "thread_pool": s.thread_pool_size,
                "success_rate": f"{s.success_rate:.1%}",
                "db_timeout": s.db_timeout,
                "thread_timeout": s.thread_timeout,
                "p50 ms": int(s.p50_latency_ms),
                "p95 ms": int(s.p95_latency_ms),
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)


def _render_decision_panel() -> None:
    ss = st.session_state
    d = ss.decision
    c = ss.change
    st.subheader(f"Iteration {ss.iteration}: proposed change")
    m1, m2 = st.columns(2)
    m1.metric(
        "db_pool_size",
        f"{c.db_pool_size if c.db_pool_size is not None else '—'}",
        delta=(c.db_pool_size - ss.current_cfg.pools.db_pool_size)
        if c.db_pool_size is not None
        else None,
    )
    m2.metric(
        "thread_pool_size",
        f"{c.thread_pool_size if c.thread_pool_size is not None else '—'}",
        delta=(c.thread_pool_size - ss.current_cfg.pools.thread_pool_size)
        if c.thread_pool_size is not None
        else None,
    )
    st.info(f"**Decision:** {d.decision}\n\n**Primary cause:** {d.primaryCause}")
    if d.secondaryEffect:
        st.caption(f"Secondary effect: {d.secondaryEffect}")
    if d.supportingEvidence:
        with st.expander("Supporting evidence"):
            for ev in d.supportingEvidence:
                st.write(f"- {ev}")
    if d.executionOrder:
        with st.expander("Execution order"):
            for i, step in enumerate(d.executionOrder, 1):
                st.write(f"{i}. {step}")


# ---------- step actions ----------

def _step_baseline() -> None:
    cfg = st.session_state.cfg
    _set_status("Phase 1: Workload", "running")
    with st.spinner("Phase 1 — running workload…"):
        summary = run_workload(cfg)
    _set_status("Phase 1: Workload", "done")
    _set_status("Phase 2: Embedding", "running")
    with st.spinner("Phase 2 — embedding logs…"):
        _embed_run_logs(cfg, summary.run_id)
    _set_status("Phase 2: Embedding", "done")
    st.session_state.current_cfg = cfg
    st.session_state.current_summary = summary
    st.session_state.history.append(summary)


def _step_diagnose() -> None:
    ss = st.session_state
    ss.iteration += 1
    _set_status("Phase 3: Agents", "running")
    _set_status("Phase 4: HITL Approval", "pending")
    _set_status("Phase 4: Retest", "pending")
    with st.spinner(f"Phase 3 — iteration {ss.iteration}: diagnostic agents…"):
        ss.thread_report = thread_pool_agent.diagnose(ss.current_cfg, ss.current_summary)
        ss.db_report = db_pool_agent.diagnose(ss.current_cfg, ss.current_summary)
        ss.decision = decision_agent.decide(
            ss.current_cfg,
            ss.current_summary,
            ss.thread_report,
            ss.db_report,
            prior_runs=ss.history,
        )
    ss.change = validate(ss.current_cfg, ss.decision)
    _set_status("Phase 3: Agents", "done")
    _set_status("Phase 4: HITL Approval", "running")


def _step_apply_and_rerun() -> None:
    ss = st.session_state
    _set_status("Phase 4: Retest", "running")
    new_cfg = apply(ss.current_cfg, ss.change)
    with st.spinner("Phase 4 — rerunning workload with new pools…"):
        summary = run_workload(new_cfg)
    _embed_run_logs(new_cfg, summary.run_id)
    _set_status("Phase 4: Retest", "done")
    ss.current_cfg = new_cfg
    ss.current_summary = summary
    ss.history.append(summary)


# ---------- main ----------

def main() -> None:
    st.set_page_config(page_title="AICult", layout="wide")
    st.title("AICult — Incident Diagnosis & Remediation")
    _init_state()

    with st.sidebar:
        st.header("Configuration")
        disabled = st.session_state.phase != "config"
        scenario = st.selectbox(
            "Scenario", ["combined", "db_only", "thread_only"], index=0, disabled=disabled
        )
        users = st.number_input("Users", 10, 500, 100, disabled=disabled)
        db_pool = st.number_input("Initial DB pool", 1, 200, 5, disabled=disabled)
        thread_pool = st.number_input(
            "Initial thread pool", 1, 200, 5, disabled=disabled
        )
        seed = st.number_input("Seed", 0, 999999, 42, disabled=disabled)
        target = st.slider(
            "Target success rate", 0.5, 1.0, 1.0, 0.05, disabled=disabled
        )
        max_iter = st.number_input(
            "Max iterations", 1, 20, 5, disabled=disabled
        )
        start = st.button("Start baseline run", type="primary", disabled=disabled)
        st.divider()
        if st.button("Reset workflow"):
            _reset_state()
            st.rerun()

    if start:
        cfg = AppConfig(
            workload=WorkloadConfig(
                scenario=scenario, num_users=int(users), rng_seed=int(seed)
            ),
            pools=Pools(db_pool_size=int(db_pool), thread_pool_size=int(thread_pool)),
            target_success_rate=float(target),
            max_remediation_iterations=int(max_iter),
        )
        init_schema(cfg)
        with st.spinner("Indexing runbooks…"):
            index_runbooks(cfg)
        st.session_state.cfg = cfg
        st.session_state.phase = "baseline"
        st.rerun()

    _render_phase_cards()

    phase = st.session_state.phase

    if phase == "baseline":
        _step_baseline()
        st.session_state.phase = "iterating"
        st.rerun()

    if phase == "iterating":
        reason = _stop_reason()
        if reason:
            st.session_state.phase = "done"
            st.session_state.done_reason = reason
            st.rerun()
        else:
            _step_diagnose()
            st.session_state.phase = "awaiting_approval"
            st.rerun()

    if phase == "awaiting_approval":
        _render_decision_panel()
        c1, c2 = st.columns(2)
        approve = c1.button("✅ Approve & retest", type="primary", key="approve_btn")
        reject = c2.button("❌ Reject & stop", key="reject_btn")
        if approve:
            _set_status("Phase 4: HITL Approval", "done")
            st.session_state.phase = "applying"
            st.rerun()
        if reject:
            _set_status("Phase 4: HITL Approval", "done")
            st.session_state.phase = "done"
            st.session_state.done_reason = "Operator rejected the proposed change."
            st.rerun()

    if phase == "applying":
        _step_apply_and_rerun()
        st.session_state.phase = "iterating"
        st.rerun()

    if phase == "done":
        st.success(f"Workflow complete — {st.session_state.done_reason}")

    _render_iteration_table()


if __name__ == "__main__":
    main()
