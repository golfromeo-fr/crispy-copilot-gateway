"""Best-effort Copilot usage metering in a local SQLite store (stdlib sqlite3)."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from . import config

_SCHEMA = """CREATE TABLE IF NOT EXISTS usage(
    id INTEGER PRIMARY KEY, ts TEXT, key_name TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, nano_aiu INTEGER, cost_usd REAL)"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.data_dir() / "usage.db")
    conn.execute(_SCHEMA)
    return conn


def record(key_name, model, input_tokens, output_tokens, nano_aiu) -> None:
    nano_aiu = nano_aiu or 0
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO usage(ts,key_name,model,input_tokens,output_tokens,nano_aiu,cost_usd)"
            " VALUES(?,?,?,?,?,?,?)",
            (datetime.now(UTC).isoformat(timespec="seconds"), key_name, model,
             input_tokens or 0, output_tokens or 0, nano_aiu, nano_aiu / 1e11))
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    conn = _connect()
    try:
        conn.commit()
    finally:
        conn.close()


def totals() -> dict:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
            " COALESCE(SUM(nano_aiu),0), COALESCE(SUM(cost_usd),0) FROM usage").fetchone()
    finally:
        conn.close()
    requests, input_tokens, output_tokens, nano_aiu, cost_usd = row
    return {"requests": requests, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "credits": nano_aiu / 1e9, "cost_usd": cost_usd}


def recent(limit: int = 20) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT ts,key_name,model,input_tokens,output_tokens,nano_aiu,cost_usd"
            " FROM usage ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    cols = ("ts", "key_name", "model", "input_tokens", "output_tokens", "nano_aiu", "cost_usd")
    return [dict(zip(cols, r)) for r in rows]


def parse_usage(obj: dict) -> dict:
    """Token counts (OpenAI or Anthropic naming) plus Copilot nano-AIU billing."""
    if not isinstance(obj, dict):
        return {}
    # Responses SSE frames nest usage under "response", but copilot_usage sits on the
    # event envelope (sibling of response); non-stream payloads carry both at top level.
    inner = obj.get("response")
    src = inner if isinstance(inner, dict) else obj
    usage = src.get("usage") or {}
    out = {}
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    if input_tokens is not None:
        out["input_tokens"] = input_tokens
    if output_tokens is not None:
        out["output_tokens"] = output_tokens
    copilot = obj.get("copilot_usage") or src.get("copilot_usage") or {}
    nano = copilot.get("total_nano_aiu")
    if nano is not None:
        out["nano_aiu"] = nano
    return out


def parse_sse(buf: bytes) -> dict:
    """Merge usage across SSE data frames (later frames win); best-effort, never raises."""
    merged: dict = {}
    for line in buf.splitlines():
        if not line.startswith(b"data: "):
            continue
        payload = line[6:].strip()
        if payload == b"[DONE]":
            continue
        try:
            merged.update(parse_usage(json.loads(payload)))
        except Exception:
            continue
    return merged
