"""Load Markdown runbooks, chunk them, and index in the vector store.

Indexing is content-hash aware: if a runbook file changes on disk, the old
chunks + vectors are dropped and the new content is re-embedded on the next
startup. Unchanged runbooks are skipped.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import List

from config import AppConfig
from embeddings import embed_batch
from storage import (
    delete_source,
    doc_source_hash,
    upsert_doc,
    upsert_source_hash,
)


def _chunk(text: str, max_chars: int = 900) -> List[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        if len(current) + len(p) + 2 <= max_chars:
            current = f"{current}\n\n{p}" if current else p
        else:
            if current:
                chunks.append(current)
            current = p
    if current:
        chunks.append(current)
    return chunks


def index_runbooks(cfg: AppConfig) -> None:
    for fname in ("DB_pool_runbook.md", "thread_pool_runbook.md"):
        source = fname.replace(".md", "")
        path = os.path.join(cfg.runbooks_dir, fname)
        if not os.path.exists(path):
            print(f"[runbooks] missing: {path} (skipping)")
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if doc_source_hash(cfg, source) == content_hash:
            continue
        delete_source(cfg, source)
        chunks = _chunk(content)
        vectors = embed_batch(cfg, chunks)
        for idx, (chunk_text, vec) in enumerate(zip(chunks, vectors)):
            upsert_doc(cfg, str(uuid.uuid4()), source, idx, chunk_text, vec)
        upsert_source_hash(cfg, source, content_hash)
        print(f"[runbooks] re-indexed {len(chunks)} chunks from {fname}")
