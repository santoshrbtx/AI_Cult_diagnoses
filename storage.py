"""SQLite + sqlite-vec persistence and retrieval.

One writer serializes all inserts (Phase 1 uses an asyncio.Queue that funnels
into this module). Vectors live in `vec_logs` / `vec_docs` virtual tables
created by sqlite-vec.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import uuid
from contextlib import contextmanager
from typing import Iterable, List, Sequence, Tuple

import sqlite_vec

from config import AppConfig
from models import LogEntry


def _pack(vec: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


@contextmanager
def _connect(cfg: AppConfig):
    conn = sqlite3.connect(cfg.db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(cfg: AppConfig) -> None:
    with _connect(cfg) as conn:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS logs (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                scenario TEXT NOT NULL,
                outcome TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_logs_run ON logs(run_id);
            CREATE INDEX IF NOT EXISTS idx_logs_outcome ON logs(outcome);

            CREATE TABLE IF NOT EXISTS docs (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS vec_logs USING vec0(
                embedding float[{cfg.embed_dim}]
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_docs USING vec0(
                embedding float[{cfg.embed_dim}]
            );

            CREATE TABLE IF NOT EXISTS vec_logs_map (rowid INTEGER PRIMARY KEY, log_id TEXT);
            CREATE TABLE IF NOT EXISTS vec_docs_map (rowid INTEGER PRIMARY KEY, doc_id TEXT);

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                scenario TEXT NOT NULL,
                db_pool_size INTEGER,
                thread_pool_size INTEGER,
                summary_json TEXT
            );

            CREATE TABLE IF NOT EXISTS doc_hashes (
                source TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL
            );
            """
        )


def insert_log(cfg: AppConfig, entry: LogEntry) -> str:
    log_id = str(uuid.uuid4())
    with _connect(cfg) as conn:
        conn.execute(
            "INSERT INTO logs(id, run_id, scenario, outcome, payload) VALUES (?, ?, ?, ?, ?)",
            (log_id, entry.run_id, entry.scenario, entry.outcome, entry.model_dump_json()),
        )
    return log_id


def save_run_summary(cfg: AppConfig, run_id: str, scenario: str, pools, summary_json: str) -> None:
    with _connect(cfg) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs(run_id, scenario, db_pool_size, thread_pool_size, summary_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, scenario, pools.db_pool_size, pools.thread_pool_size, summary_json),
        )


def fetch_logs_for_run(cfg: AppConfig, run_id: str) -> List[Tuple[str, LogEntry]]:
    with _connect(cfg) as conn:
        rows = conn.execute(
            "SELECT id, payload FROM logs WHERE run_id = ?", (run_id,)
        ).fetchall()
    return [(rid, LogEntry.model_validate_json(payload)) for rid, payload in rows]


def upsert_log_embeddings(cfg: AppConfig, items: Iterable[Tuple[str, Sequence[float]]]) -> None:
    with _connect(cfg) as conn:
        for log_id, vec in items:
            cur = conn.execute("INSERT INTO vec_logs(embedding) VALUES (?)", (_pack(vec),))
            conn.execute("INSERT INTO vec_logs_map(rowid, log_id) VALUES (?, ?)", (cur.lastrowid, log_id))


def upsert_doc(cfg: AppConfig, doc_id: str, source: str, chunk_index: int, text: str, vec: Sequence[float]) -> None:
    with _connect(cfg) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO docs(id, source, chunk_index, text) VALUES (?, ?, ?, ?)",
            (doc_id, source, chunk_index, text),
        )
        cur = conn.execute("INSERT INTO vec_docs(embedding) VALUES (?)", (_pack(vec),))
        conn.execute("INSERT INTO vec_docs_map(rowid, doc_id) VALUES (?, ?)", (cur.lastrowid, doc_id))


def doc_source_hash(cfg: AppConfig, source: str) -> str | None:
    with _connect(cfg) as conn:
        row = conn.execute("SELECT content_hash FROM doc_hashes WHERE source = ?", (source,)).fetchone()
    return row[0] if row else None


def upsert_source_hash(cfg: AppConfig, source: str, content_hash: str) -> None:
    with _connect(cfg) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO doc_hashes(source, content_hash) VALUES (?, ?)",
            (source, content_hash),
        )


def delete_source(cfg: AppConfig, source: str) -> None:
    """Drop all docs + vectors for a source so it can be re-indexed cleanly."""
    with _connect(cfg) as conn:
        rows = conn.execute("SELECT id FROM docs WHERE source = ?", (source,)).fetchall()
        for (doc_id,) in rows:
            map_rows = conn.execute("SELECT rowid FROM vec_docs_map WHERE doc_id = ?", (doc_id,)).fetchall()
            for (rid,) in map_rows:
                conn.execute("DELETE FROM vec_docs WHERE rowid = ?", (rid,))
                conn.execute("DELETE FROM vec_docs_map WHERE rowid = ?", (rid,))
        conn.execute("DELETE FROM docs WHERE source = ?", (source,))
        conn.execute("DELETE FROM doc_hashes WHERE source = ?", (source,))


def search_logs(cfg: AppConfig, query_vec: Sequence[float], run_id: str, scenarios: Sequence[str], k: int) -> List[Tuple[str, LogEntry, float]]:
    with _connect(cfg) as conn:
        rows = conn.execute(
            f"""
            SELECT m.log_id, l.payload, v.distance
            FROM vec_logs v
            JOIN vec_logs_map m ON m.rowid = v.rowid
            JOIN logs l ON l.id = m.log_id
            WHERE v.embedding MATCH ? AND k = ?
              AND l.run_id = ?
              AND l.scenario IN ({",".join("?" * len(scenarios))})
            ORDER BY v.distance
            """,
            (_pack(query_vec), k, run_id, *scenarios),
        ).fetchall()
    return [(rid, LogEntry.model_validate_json(payload), dist) for rid, payload, dist in rows]


def search_docs(cfg: AppConfig, query_vec: Sequence[float], source_prefix: str, k: int) -> List[Tuple[str, str, str, float]]:
    with _connect(cfg) as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.source, d.text, v.distance
            FROM vec_docs v
            JOIN vec_docs_map m ON m.rowid = v.rowid
            JOIN docs d ON d.id = m.doc_id
            WHERE v.embedding MATCH ? AND k = ?
              AND d.source LIKE ?
            ORDER BY v.distance
            """,
            (_pack(query_vec), k, f"{source_prefix}%"),
        ).fetchall()
    return rows
