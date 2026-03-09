#!/usr/bin/env python3
"""Background worker: extract facts from Claude Code transcript → abhimem DB."""

import json
import math
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DB_PATH = Path.home() / ".abhimem" / "memory.db"
EMBED_MODEL = "nomic-embed-text"
EXTRACT_MODEL = "llama3.2:3b"
LOG = Path.home() / ".claude" / "hooks" / "extract-memories.log"


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {msg}\n")


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            created_at INTEGER NOT NULL,
            source TEXT DEFAULT 'manual',
            embedding TEXT
        )
    """)
    db.commit()
    return db


def embed(text: str) -> list:
    payload = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["embeddings"][0]


def cosine_sim(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def is_duplicate(db: sqlite3.Connection, text: str, vec: list, threshold: float = 0.92) -> bool:
    rows = db.execute(
        "SELECT embedding FROM memories WHERE embedding IS NOT NULL"
    ).fetchall()
    for (emb_json,) in rows:
        emb = json.loads(emb_json)
        if cosine_sim(vec, emb) >= threshold:
            return True
    return False


def extract_facts(conversation: str) -> list:
    prompt = (
        "Extract key facts, preferences, or important context from this text. "
        "Return one fact per line, plain text, no bullets or numbering. "
        "Only include things worth remembering long-term about the user, their projects, or their preferences. "
        "Skip transient details like 'user asked about X today'. "
        "If nothing is worth remembering, return exactly: NOTHING\n\n"
        f"{conversation}"
    )
    result = subprocess.run(
        ["ollama", "run", EXTRACT_MODEL, prompt],
        capture_output=True, text=True, timeout=90,
    )
    output = result.stdout.strip()
    if not output or output.upper() == "NOTHING":
        return []
    return [line.strip() for line in output.splitlines() if line.strip() and len(line.strip()) > 10]


def build_conversation(transcript_path: str) -> str:
    lines = Path(transcript_path).read_text().strip().splitlines()
    turns = []
    for line in lines:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        role = entry.get("role", "")
        content = entry.get("content", "")
        # content can be a list of blocks
        if isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            content = " ".join(parts)
        if role in ("user", "assistant") and content:
            turns.append(f"{role}: {content[:600]}")
    return "\n".join(turns)


def main():
    if len(sys.argv) < 3:
        sys.exit(0)

    transcript_path, session_id = sys.argv[1], sys.argv[2]
    log(f"Starting extraction for session {session_id[:8]}")

    try:
        conversation = build_conversation(transcript_path)
    except Exception as e:
        log(f"Failed to read transcript: {e}")
        sys.exit(0)

    if len(conversation) < 300:
        log("Transcript too short, skipping.")
        sys.exit(0)

    # Only pass the last ~4000 chars to keep prompt manageable
    conversation_snippet = conversation[-4000:]

    facts = extract_facts(conversation_snippet)
    if not facts:
        log("No memorable facts found.")
        sys.exit(0)

    log(f"Extracted {len(facts)} candidate facts.")

    db = get_db()
    stored = []
    try:
        for fact in facts:
            try:
                vec = embed(fact)
                if is_duplicate(db, fact, vec):
                    log(f"Skipped duplicate: {fact[:60]}")
                    continue
                db.execute(
                    "INSERT INTO memories (text, category, created_at, source, embedding) VALUES (?, ?, ?, ?, ?)",
                    (fact, "extracted", int(time.time()), f"session:{session_id[:8]}", json.dumps(vec)),
                )
                stored.append(fact)
            except Exception as e:
                log(f"Error storing fact '{fact[:40]}': {e}")
        db.commit()
    finally:
        db.close()

    log(f"Stored {len(stored)} new memories from session {session_id[:8]}.")
    for f in stored:
        log(f"  → {f[:80]}")


if __name__ == "__main__":
    main()
