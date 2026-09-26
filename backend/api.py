"""FastAPI HTTP + SSE facade for the AICult workflow.

Endpoints:
  POST /run/start                 -> {"run_id": "..."}
  GET  /run/{run_id}/events       -> text/event-stream of phase events
  POST /run/{run_id}/approve      -> resolve the current HITL pause (approve)
  POST /run/{run_id}/reject       -> resolve the current HITL pause (reject)

Run with:
  uvicorn backend.api:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import queue as _queue
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .workflow import RUNS, resolve_approval, start_run


app = FastAPI(title="AICult API")

# Angular dev server (ng serve) runs on 4200 by default.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartRequest(BaseModel):
    scenario: str = "combined"
    users: int = 100
    db_pool: int = 5
    thread_pool: int = 5
    seed: int = 42
    target_success_rate: float = 1.0
    max_iterations: int = 5


class StartResponse(BaseModel):
    run_id: str


@app.post("/run/start", response_model=StartResponse)
def start(req: StartRequest) -> StartResponse:
    run_id = start_run(req.model_dump())
    return StartResponse(run_id=run_id)


@app.post("/run/{run_id}/approve")
def approve(run_id: str) -> Dict[str, Any]:
    ok = resolve_approval(run_id, approved=True)
    if not ok:
        raise HTTPException(404, "No pending approval for that run")
    return {"ok": True}


@app.post("/run/{run_id}/reject")
def reject(run_id: str) -> Dict[str, Any]:
    ok = resolve_approval(run_id, approved=False)
    if not ok:
        raise HTTPException(404, "No pending approval for that run")
    return {"ok": True}


@app.get("/run/{run_id}/events")
async def events(run_id: str):
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "Unknown run_id")

    async def gen():
        while True:
            try:
                event = await asyncio.to_thread(state.events.get, True, 1.0)
            except _queue.Empty:
                # SSE keepalive so proxies don't close the connection.
                yield ": keepalive\n\n"
                continue
            if event.get("phase") == "stream_end":
                yield "event: end\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/")
def root() -> Dict[str, str]:
    return {"service": "AICult API", "docs": "/docs"}
