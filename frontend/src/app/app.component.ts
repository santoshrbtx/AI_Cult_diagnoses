import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { AicultService, StartConfig } from './aicult.service';

type PhaseKey = 'init' | 'workload' | 'embedding' | 'agents' | 'hitl_approval' | 'retest' | 'done' | 'error';
type PhaseStatus = 'pending' | 'running' | 'done' | 'error';

interface PhaseStep {
  key: PhaseKey;
  label: string;
  status: PhaseStatus;
}

interface IterationRow {
  step: string;
  db_pool: number;
  thread_pool: number;
  success_rate: string;
  db_timeout: number;
  thread_timeout: number;
  p50_ms: number;
  p95_ms: number;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent {
  cfg: StartConfig = {
    scenario: 'combined',
    users: 100,
    db_pool: 5,
    thread_pool: 5,
    seed: 42,
    target_success_rate: 1.0,
    max_iterations: 5,
  };

  scenarios = ['combined', 'db_only', 'thread_only'];

  running = false;
  runId: string | null = null;
  doneReason: string | null = null;
  errorMessage: string | null = null;

  steps: PhaseStep[] = [
    { key: 'workload', label: 'Phase 1 — Workload', status: 'pending' },
    { key: 'embedding', label: 'Phase 2 — Embedding', status: 'pending' },
    { key: 'agents', label: 'Phase 3 — Agents', status: 'pending' },
    { key: 'hitl_approval', label: 'Phase 4 — HITL Approval', status: 'pending' },
    { key: 'retest', label: 'Phase 4 — Retest', status: 'pending' },
  ];

  currentIteration = 0;
  pendingDecision: any = null;
  pendingChange: { db_pool_size: number | null; thread_pool_size: number | null } | null = null;
  iterations: IterationRow[] = [];

  constructor(private api: AicultService) {}

  start(): void {
    this.reset();
    this.running = true;
    this.api.start(this.cfg).subscribe({
      next: (res) => {
        this.runId = res.run_id;
        this.subscribe();
      },
      error: (err) => {
        this.errorMessage = `Failed to start: ${err.message ?? err}`;
        this.running = false;
      },
    });
  }

  reset(): void {
    this.running = false;
    this.runId = null;
    this.doneReason = null;
    this.errorMessage = null;
    this.currentIteration = 0;
    this.pendingDecision = null;
    this.pendingChange = null;
    this.iterations = [];
    this.steps.forEach((s) => (s.status = 'pending'));
  }

  approve(): void {
    if (!this.runId) return;
    this.api.approve(this.runId).subscribe();
    this.pendingDecision = null;
    this.pendingChange = null;
  }

  reject(): void {
    if (!this.runId) return;
    this.api.reject(this.runId).subscribe();
    this.pendingDecision = null;
    this.pendingChange = null;
  }

  private subscribe(): void {
    if (!this.runId) return;
    this.api.events(this.runId).subscribe({
      next: (evt) => this.handleEvent(evt),
      complete: () => {
        this.running = false;
      },
    });
  }

  private handleEvent(evt: any): void {
    const phase: PhaseKey = evt.phase;
    if (phase === 'done') {
      this.doneReason = evt.reason;
      this.running = false;
      if (evt.history) this.iterations = this.rowsFromHistory(evt.history);
      return;
    }
    if (phase === 'error') {
      this.errorMessage = evt.message ?? 'Unknown error';
      this.steps.forEach((s) => (s.status = s.status === 'running' ? 'error' : s.status));
      this.running = false;
      return;
    }

    if (evt.iteration) this.currentIteration = evt.iteration;

    const step = this.steps.find((s) => s.key === phase);
    if (step) step.status = evt.status;

    if (phase === 'agents' && evt.status === 'done' && evt.decision) {
      // decision is emitted; HITL is where the pause happens
    }
    if (phase === 'hitl_approval' && evt.status === 'running') {
      this.pendingDecision = evt.decision;
      this.pendingChange = evt.change;
    }
    if (phase === 'retest' && evt.status === 'done' && evt.history) {
      this.iterations = this.rowsFromHistory(evt.history);
      // On a new iteration, reset the later steps to pending for the stepper.
      this.steps.forEach((s) => {
        if (['agents', 'hitl_approval', 'retest'].includes(s.key)) s.status = 'pending';
      });
    }
  }

  private rowsFromHistory(history: any[]): IterationRow[] {
    return history.map((h, i) => ({
      step: i === 0 ? 'baseline' : `iter ${i}`,
      db_pool: h.db_pool_size,
      thread_pool: h.thread_pool_size,
      success_rate: `${Math.round(h.success_rate * 1000) / 10}%`,
      db_timeout: h.db_timeout,
      thread_timeout: h.thread_timeout,
      p50_ms: Math.round(h.p50_latency_ms),
      p95_ms: Math.round(h.p95_latency_ms),
    }));
  }

  iconFor(status: PhaseStatus): string {
    return { pending: '○', running: '●', done: '✓', error: '✗' }[status];
  }

  activePhaseLabel(): string {
    const active = this.steps.find((s) => s.status === 'running');
    return active ? active.label : 'starting';
  }

  poolDelta(pool: 'db' | 'thread'): number | null {
    if (!this.pendingChange) return null;
    const proposed = pool === 'db' ? this.pendingChange.db_pool_size : this.pendingChange.thread_pool_size;
    if (proposed === null || proposed === undefined) return null;
    const current = this.iterations.length
      ? pool === 'db'
        ? this.iterations[this.iterations.length - 1].db_pool
        : this.iterations[this.iterations.length - 1].thread_pool
      : pool === 'db'
      ? this.cfg.db_pool
      : this.cfg.thread_pool;
    return proposed - current;
  }
}
