"""Thin wrapper around the TrueForge SDK for one-shot structured LLM calls.

Every agent creates its own throwaway session with a system prompt (agent
instructions) and asks for JSON output via response_format. Some Bedrock
open-weights models don't strictly honor response_format, so we defensively
extract the first balanced JSON object from the response.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

from trueforge_sdk import (
    AgentSpec,
    Model,
    SessionAgentSpecBody,
    TrueForge,
    UserMessage,
)
from trueforge_sdk.types.model_message_delta_event import ModelMessageDeltaEvent
from trueforge_sdk.types.model_message_event import ModelMessageEvent
from trueforge_sdk.types.response_format_json_schema import ResponseFormatJsonSchema
from trueforge_sdk.types.response_format_json_schema_json_schema import (
    ResponseFormatJsonSchemaJsonSchema,
)
from trueforge_sdk.types.turn_done_event import TurnDoneEvent

from config import AppConfig


class LLMResponseError(RuntimeError):
    def __init__(self, message: str, raw: str):
        super().__init__(f"{message}\n--- raw response (first 800 chars) ---\n{raw[:800]}")
        self.raw = raw


def _flatten(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(getattr(part, "text", "") or "" for part in content)


def _strip_fences(text: str) -> str:
    text = text.strip()
    # ```json ... ``` or ``` ... ```
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def _iter_json_candidates(text: str):
    """Yield substrings starting at each `{` and ending at each `}` in `text`.
    We do it this way (rather than tracking balanced braces) so that if the
    model wraps a valid JSON body inside a broken outer object, we still
    find the inner good one."""
    opens = [i for i, ch in enumerate(text) if ch == "{"]
    closes = [i for i, ch in enumerate(text) if ch == "}"]
    for start in opens:
        for end in reversed(closes):
            if end > start:
                yield text[start : end + 1]


def call_json(
    cfg: AppConfig,
    *,
    instructions: str,
    prompt: str,
    schema: Dict[str, Any],
    schema_name: str,
    max_attempts: int = 3,
) -> Dict[str, Any]:
    """Runs one turn on a fresh session; returns parsed JSON matching `schema`.
    Retries transient turn-errors (throttling etc.) with linear backoff."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _call_json_once(cfg, instructions=instructions, prompt=prompt, schema=schema, schema_name=schema_name)
        except LLMResponseError as exc:
            last_exc = exc
            # Only retry when we got no response at all (turn error / empty).
            if exc.raw.strip():
                raise
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    assert last_exc is not None
    raise last_exc


def _call_json_once(cfg: AppConfig, *, instructions: str, prompt: str, schema: Dict[str, Any], schema_name: str) -> Dict[str, Any]:
    client = TrueForge(base_url=cfg.trueforge_base_url)
    response_format = ResponseFormatJsonSchema(
        json_schema=ResponseFormatJsonSchemaJsonSchema.model_validate(
            {"name": schema_name, "schema": schema}
        )
    )
    session = client.sessions.create(
        agent=SessionAgentSpecBody(
            spec=AgentSpec(
                model=Model(name=cfg.llm_model),
                instructions=instructions,
                response_format=response_format,
            )
        )
    )
    session_id = session.data.id

    stream = client.sessions.create_turn_stream(
        session_id=session_id, input=[UserMessage(content=prompt)]
    )
    parts: list[str] = []
    final_text = ""
    turn_error: Optional[str] = None
    for event in stream:
        if isinstance(event, ModelMessageDeltaEvent) and event.content is not None:
            parts.append(_flatten(event.content))
        elif isinstance(event, ModelMessageEvent) and event.content is not None:
            final_text = _flatten(event.content)
        elif isinstance(event, TurnDoneEvent):
            state = event.state
            status = getattr(state, "status", None)
            if status == "error":
                turn_error = getattr(state, "message", None) or "unknown turn error"
            else:
                output = getattr(state, "output", None)
                if output is not None and output.content is not None:
                    final_text = _flatten(output.content)
            break
    raw = final_text or "".join(parts)
    if turn_error and not raw:
        raise LLMResponseError(f"turn ended in error: {turn_error}", raw)

    stripped = _strip_fences(raw)
    last_error: Optional[Exception] = None
    for candidate in [raw.strip(), stripped, *_iter_json_candidates(stripped)]:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    raise LLMResponseError(
        f"model did not return valid JSON ({last_error})", raw
    )
