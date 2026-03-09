#!/usr/bin/env python3
"""
abhimem — lightweight personal memory MCP server for Claude Code.
SQLite (facts + vectors as JSON) + Ollama for embeddings + llama3.2:3b for extraction.
Pure Python cosine similarity — no extensions needed.

Storage: ~/.abhimem/memory.db
MCP: http://127.0.0.1:8765/mcp
Tools: remember, recall, forget, list_memories, extract_and_remember
"""

import json
import math
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

from fastmcp import FastMCP

# ── Config ─────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".abhimem" / "memory.db"
EMBED_MODEL = "nomic-embed-text"
EXTRACT_MODEL = "llama3.2:3b"
OBSIDIAN_VAULT = Path.home() / "Documents" / "Obsidian Vault"
OBSIDIAN_MEMORY_NOTE = OBSIDIAN_VAULT / "Claude Memory.md"
OBSIDIAN_INBOX = OBSIDIAN_VAULT / "Memory Inbox"

# ── DB setup ────────────────────────────────────────────────────────────────

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


# ── Ollama helpers ───────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    payload = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["embeddings"][0]


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def extract_facts(conversation: str) -> list[str]:
    prompt = (
        "Extract key facts, preferences, or important context from this text. "
        "Return one fact per line, plain text, no bullets or numbering. "
        "Only include things worth remembering long-term. "
        "If nothing is worth remembering, return exactly: NOTHING\n\n"
        f"{conversation}"
    )
    result = subprocess.run(
        ["ollama", "run", EXTRACT_MODEL, prompt],
        capture_output=True, text=True, timeout=60
    )
    output = result.stdout.strip()
    if output == "NOTHING" or not output:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


# ── MCP server ───────────────────────────────────────────────────────────────

mcp = FastMCP("abhimem")


@mcp.tool()
def remember(text: str, category: str = "general", source: str = "manual") -> str:
    """Store a memory. category: preference | task | project | person | general"""
    db = get_db()
    try:
        vec = embed(text)
        cur = db.execute(
            "INSERT INTO memories (text, category, created_at, source, embedding) VALUES (?, ?, ?, ?, ?)",
            (text, category, int(time.time()), source, json.dumps(vec))
        )
        db.commit()
        return f"Stored memory #{cur.lastrowid}: {text[:80]}"
    finally:
        db.close()


@mcp.tool()
def recall(query: str, limit: int = 5) -> str:
    """Search memories by semantic similarity."""
    db = get_db()
    try:
        query_vec = embed(query)
        rows = db.execute(
            "SELECT id, text, category, created_at, embedding FROM memories WHERE embedding IS NOT NULL"
        ).fetchall()
        if not rows:
            return "No memories stored yet."
        scored = []
        for row in rows:
            mem_id, text, cat, ts, emb_json = row
            emb = json.loads(emb_json)
            sim = cosine_sim(query_vec, emb)
            scored.append((sim, mem_id, text, cat, ts))
        scored.sort(reverse=True)
        top = scored[:limit]
        lines = []
        for sim, mem_id, text, cat, ts in top:
            age = _age_str(ts)
            lines.append(f"[#{mem_id} | {cat} | {age} | {sim:.2f}] {text}")
        return "\n".join(lines)
    finally:
        db.close()


@mcp.tool()
def forget(memory_id: int) -> str:
    """Delete a memory by ID."""
    db = get_db()
    try:
        db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        db.commit()
        return f"Deleted memory #{memory_id}"
    finally:
        db.close()


@mcp.tool()
def list_memories(category: str = "", limit: int = 20) -> str:
    """List recent memories, optionally filtered by category."""
    db = get_db()
    try:
        if category:
            rows = db.execute(
                "SELECT id, text, category, created_at FROM memories WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                (category, limit)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, text, category, created_at FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        if not rows:
            return "No memories stored yet."
        lines = [f"[#{r[0]} | {r[2]} | {_age_str(r[3])}] {r[1]}" for r in rows]
        return "\n".join(lines)
    finally:
        db.close()


@mcp.tool()
def extract_and_remember(conversation: str, source: str = "conversation") -> str:
    """Run llama3.2:3b to extract facts from a conversation and store them."""
    facts = extract_facts(conversation)
    if not facts:
        return "No memorable facts found in the conversation."
    db = get_db()
    stored = []
    try:
        for fact in facts:
            vec = embed(fact)
            cur = db.execute(
                "INSERT INTO memories (text, category, created_at, source, embedding) VALUES (?, ?, ?, ?, ?)",
                (fact, "extracted", int(time.time()), source, json.dumps(vec))
            )
            stored.append(f"#{cur.lastrowid}: {fact}")
        db.commit()
    finally:
        db.close()
    return f"Stored {len(stored)} facts:\n" + "\n".join(stored)


@mcp.tool()
def sync_to_obsidian() -> str:
    """Export all memories to Claude Memory.md in the Obsidian vault, grouped by category."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, text, category, created_at, source FROM memories ORDER BY category, created_at DESC"
        ).fetchall()
    finally:
        db.close()

    if not rows:
        return "No memories to export."

    # Group by category
    by_cat: dict[str, list] = {}
    for row in rows:
        cat = row[2]
        by_cat.setdefault(cat, []).append(row)

    now = time.strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Claude Memory",
        f"*Last synced: {now}*",
        "",
        "> Auto-generated by abhimem. Edit memories with `forget(id)` + `remember(text)` or add notes to Memory Inbox/.",
        "",
    ]
    for cat, cat_rows in sorted(by_cat.items()):
        lines.append(f"## {cat.title()}")
        for row in cat_rows:
            mem_id, text, _, ts, source = row
            age = _age_str(ts)
            lines.append(f"- `#{mem_id}` {text}  *(stored {age}, source: {source})*")
        lines.append("")

    OBSIDIAN_VAULT.mkdir(parents=True, exist_ok=True)
    OBSIDIAN_MEMORY_NOTE.write_text("\n".join(lines))
    return f"Exported {len(rows)} memories to {OBSIDIAN_MEMORY_NOTE}"


@mcp.tool()
def import_from_obsidian() -> str:
    """Import notes from Memory Inbox/ folder in Obsidian vault into abhimem."""
    if not OBSIDIAN_INBOX.exists():
        OBSIDIAN_INBOX.mkdir(parents=True)
        return f"Created Memory Inbox at {OBSIDIAN_INBOX}. Drop .md files there and call this again."

    notes = list(OBSIDIAN_INBOX.glob("*.md"))
    if not notes:
        return f"No .md files in {OBSIDIAN_INBOX}"

    stored = []
    for note_path in notes:
        content = note_path.read_text().strip()
        if not content:
            continue
        # Extract tags from frontmatter or inline
        category = "general"
        if "category:" in content.lower():
            for line in content.splitlines():
                if line.lower().startswith("category:"):
                    category = line.split(":", 1)[1].strip()
                    break
        # Strip frontmatter if present
        text = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                text = parts[2].strip()

        vec = embed(text[:1000])
        db = get_db()
        try:
            cur = db.execute(
                "INSERT INTO memories (text, category, created_at, source, embedding) VALUES (?, ?, ?, ?, ?)",
                (text[:2000], category, int(time.time()), f"obsidian:{note_path.name}", json.dumps(vec))
            )
            db.commit()
            stored.append(f"#{cur.lastrowid}: {note_path.name}")
        finally:
            db.close()
        # Move processed note to archive
        archive = OBSIDIAN_INBOX / "processed"
        archive.mkdir(exist_ok=True)
        note_path.rename(archive / note_path.name)

    return f"Imported {len(stored)} notes:\n" + "\n".join(stored)


def _age_str(ts: int) -> str:
    delta = int(time.time()) - ts
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8765)
