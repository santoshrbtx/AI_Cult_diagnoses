"""Embedding provider abstraction.

Default: Amazon Titan (v2) via Bedrock. Isolated behind `embed_batch()` so we
can later evaluate a TrueFoundry-hosted embedding model without touching the
callers.
"""

from __future__ import annotations

import json
from typing import List, Sequence

import boto3

from config import AppConfig


_bedrock = None


def _client(cfg: AppConfig):
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=cfg.embed_region)
    return _bedrock


def embed_one(cfg: AppConfig, text: str) -> List[float]:
    body = json.dumps({"inputText": text, "dimensions": cfg.embed_dim, "normalize": True})
    resp = _client(cfg).invoke_model(modelId=cfg.embed_model_id, body=body)
    payload = json.loads(resp["body"].read())
    return payload["embedding"]


def embed_batch(cfg: AppConfig, texts: Sequence[str]) -> List[List[float]]:
    # Titan Bedrock API is one-text-per-call; loop until we swap providers.
    return [embed_one(cfg, t) for t in texts]
