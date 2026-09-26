import { Injectable, NgZone } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, Subject } from 'rxjs';

export interface StartConfig {
  scenario: string;
  users: number;
  db_pool: number;
  thread_pool: number;
  seed: number;
  target_success_rate: number;
  max_iterations: number;
}

@Injectable({ providedIn: 'root' })
export class AicultService {
  private base = 'http://localhost:8000';

  constructor(private http: HttpClient, private zone: NgZone) {}

  start(cfg: StartConfig): Observable<{ run_id: string }> {
    return this.http.post<{ run_id: string }>(`${this.base}/run/start`, cfg);
  }

  approve(runId: string): Observable<unknown> {
    return this.http.post(`${this.base}/run/${runId}/approve`, {});
  }

  reject(runId: string): Observable<unknown> {
    return this.http.post(`${this.base}/run/${runId}/reject`, {});
  }

  /** SSE stream of workflow events. Emits parsed JSON events and completes
   *  on the server's `event: end` message or on error. */
  events(runId: string): Observable<any> {
    const subject = new Subject<any>();
    const source = new EventSource(`${this.base}/run/${runId}/events`);

    source.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        this.zone.run(() => subject.next(data));
      } catch {
        /* ignore malformed */
      }
    };
    source.addEventListener('end', () => {
      this.zone.run(() => subject.complete());
      source.close();
    });
    source.onerror = () => {
      this.zone.run(() => subject.complete());
      source.close();
    };

    return subject.asObservable();
  }
}
