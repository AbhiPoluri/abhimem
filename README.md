# abhimem

A lightweight, local-first persistent memory system for Claude Code. Uses SQLite for storage, Ollama for semantic embeddings and fact extraction, and FastMCP to expose everything as tools Claude can call directly.

Every session, a Stop hook automatically reads the conversation transcript, runs a local LLM to extract memorable facts, deduplicates against existing memories, and stores them — so Claude remembers things across sessions without manual effort.

---

## How it works

```
Claude Code session
       │
       ▼
  Stop hook fires (after every response)
       │
       ▼
  extract-memories.py  ←── reads transcript JSONL
       │
       ▼
  extract-memories-worker.py (background)
       │
       ├── sends last 4000 chars to llama3.2:3b via Ollama
       │   "Extract facts worth remembering long-term..."
       │
       ├── embeds each fact with nomic-embed-text
       │
       ├── checks cosine similarity against existing memories
       │   (threshold: 0.92 — skips near-duplicates)
       │
       └── stores new facts to ~/.abhimem/memory.db (SQLite)

                    ┌─────────────────┐
                    │   abhimem MCP   │  ← http://127.0.0.1:8765/mcp
                    │    server       │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
          remember        recall         forget
          (store)    (semantic search)  (delete)
```

### Components

| Component | What it does |
|-----------|-------------|
| `server.py` | FastMCP server over streamable-HTTP. Exposes memory tools to Claude Code. Runs on `http://127.0.0.1:8765/mcp`. |
| `hooks/extract-memories.py` | Claude Code Stop hook entry point. Fires after every response, immediately exits to avoid blocking. Spawns the worker in the background. |
| `hooks/extract-memories-worker.py` | Background extraction worker. Reads transcript → extracts facts via LLM → embeds → deduplicates → stores. Logs to `~/.claude/hooks/extract-memories.log`. |
| `~/.abhimem/memory.db` | SQLite database. Each memory has: `text`, `category`, `created_at`, `source`, `embedding` (JSON array). |

### Memory flow in detail

1. **Extraction** — The worker sends the last 4,000 characters of the session transcript to `llama3.2:3b` with a prompt instructing it to extract long-term-worthy facts (one per line). Transient details are explicitly excluded.

2. **Embedding** — Each extracted fact is embedded using `nomic-embed-text` (768-dimensional vectors) via the local Ollama API.

3. **Deduplication** — Cosine similarity is computed against every existing memory. If any similarity score exceeds 0.92, the fact is skipped as a near-duplicate.

4. **Storage** — New facts are inserted into SQLite with `source = "session:<id>"` and `category = "extracted"`.

5. **Retrieval** — At session start, `MEMORY.md` is loaded into context automatically. Mid-session, Claude calls `mcp__abhimem__recall` with a query string to do semantic search across all stored memories.

---

## Requirements

- macOS (tested on macOS 13+)
- Python 3.9+
- [Ollama](https://ollama.ai) with two models:
  - `nomic-embed-text` — for semantic embeddings
  - `llama3.2:3b` — for fact extraction
- [Claude Code](https://claude.ai/code) CLI

---

## Installation

```bash
git clone https://github.com/AbhiPoluri/abhimem
cd abhimem
bash install.sh
```

The installer:
1. Installs the `fastmcp` Python package
2. Pulls required Ollama models
3. Installs a macOS LaunchAgent so the server auto-starts on login
4. Copies the Stop hook to `~/.claude/hooks/` and registers it in `~/.claude/settings.json`
5. Registers the MCP server in `~/.claude/mcp.json`
6. Starts the server immediately

**Restart Claude Code** after installation to activate the MCP tools and hook.

---

## MCP tools

Once installed, these tools are available in every Claude Code session:

| Tool | Description |
|------|-------------|
| `mcp__abhimem__remember` | Store a memory manually. Params: `text`, `category` (preference/task/project/person/general), `source`. |
| `mcp__abhimem__recall` | Semantic search. Params: `query`, `limit` (default 5). Returns top matches with similarity scores. |
| `mcp__abhimem__list_memories` | List recent memories, optionally filtered by category. |
| `mcp__abhimem__forget` | Delete a memory by ID. |
| `mcp__abhimem__extract_and_remember` | Run extraction manually on any text string. |
| `mcp__abhimem__sync_to_obsidian` | Export all memories to `Claude Memory.md` in your Obsidian vault. |
| `mcp__abhimem__import_from_obsidian` | Import `.md` files from `Memory Inbox/` folder in Obsidian. |

---

## Storage

All data lives locally in `~/.abhimem/`:

```
~/.abhimem/
├── memory.db      # SQLite — all memories + embeddings
└── server.log     # LaunchAgent stdout/stderr
```

Database schema:

```sql
CREATE TABLE memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    created_at  INTEGER NOT NULL,
    source      TEXT DEFAULT 'manual',
    embedding   TEXT   -- JSON array of floats (768-dim)
);
```

---

## Logs

- **Server log:** `~/.abhimem/server.log`
- **Hook log:** `~/.claude/hooks/extract-memories.log`

The hook log shows every extraction run: which facts were found, which were skipped as duplicates, and how many were stored.

---

## Obsidian integration

If you use Obsidian, abhimem can sync both ways:

- **Export:** Call `mcp__abhimem__sync_to_obsidian` — writes all memories to `Claude Memory.md` in your vault, grouped by category.
- **Import:** Drop `.md` files into `Memory Inbox/` in your vault, then call `mcp__abhimem__import_from_obsidian` — files are parsed, embedded, and stored. Processed files are moved to `Memory Inbox/processed/`.

---

## notesgraph integration

[notesgraph](https://github.com/AbhiPoluri/notesgraph-dad) reads from `~/.abhimem/memory.db` and renders memories as orange nodes in the graph view. The `/api/memory-edges` endpoint computes pairwise cosine similarity and draws edges between semantically related memories, giving you a visual map of how your knowledge is connected.

---

## Architecture notes

- **No cloud.** Everything runs locally. Ollama, SQLite, the MCP server — all on-device.
- **Non-blocking.** The Stop hook exits in milliseconds. Extraction runs in a detached subprocess, so it never slows down Claude Code responses.
- **Deduplication.** Cosine similarity at 0.92 threshold prevents near-duplicate facts from piling up over time.
- **Pure Python.** No native extensions — cosine similarity is computed in Python, no pgvector or similar required.
