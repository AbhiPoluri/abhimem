#!/bin/bash
# abhimem installer — sets up the MCP memory server and Claude Code Stop hook

set -e

ABHIMEM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_HOME="$HOME"
LAUNCHAGENT_DIR="$HOME/Library/LaunchAgents"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
HOOKS_DIR="$HOME/.claude/hooks"
MCP_CONFIG="$HOME/.claude/mcp.json"

echo "abhimem installer"
echo "================="
echo ""

# 1. Python deps
echo "[1/6] Installing Python dependencies..."
pip3 install fastmcp --quiet
echo "  done"

# 2. Ollama check
echo "[2/6] Checking Ollama..."
if ! command -v ollama &>/dev/null; then
    echo "  WARNING: ollama not found. Install from https://ollama.ai"
    echo "  Then run: ollama pull nomic-embed-text && ollama pull llama3.2:3b"
else
    echo "  Pulling models (this may take a while on first run)..."
    ollama pull nomic-embed-text --quiet 2>/dev/null || echo "  (nomic-embed-text already pulled)"
    ollama pull llama3.2:3b --quiet 2>/dev/null || echo "  (llama3.2:3b already pulled)"
    echo "  done"
fi

# 3. LaunchAgent (auto-start on login)
echo "[3/6] Installing LaunchAgent..."
mkdir -p "$LAUNCHAGENT_DIR"
sed "s|ABHIMEM_DIR|$ABHIMEM_DIR|g; s|USER_HOME|$USER_HOME|g" \
    "$ABHIMEM_DIR/com.abhiram.abhimem.plist.template" \
    > "$LAUNCHAGENT_DIR/com.abhiram.abhimem.plist"
launchctl unload "$LAUNCHAGENT_DIR/com.abhiram.abhimem.plist" 2>/dev/null || true
launchctl load "$LAUNCHAGENT_DIR/com.abhiram.abhimem.plist"
echo "  done — server will auto-start on login at http://127.0.0.1:8765"

# 4. Stop hook
echo "[4/6] Installing Claude Code Stop hook..."
mkdir -p "$HOOKS_DIR"
cp "$ABHIMEM_DIR/hooks/extract-memories.py" "$HOOKS_DIR/"
cp "$ABHIMEM_DIR/hooks/extract-memories-worker.py" "$HOOKS_DIR/"
chmod +x "$HOOKS_DIR/extract-memories.py" "$HOOKS_DIR/extract-memories-worker.py"

# Patch settings.json
if [ ! -f "$CLAUDE_SETTINGS" ]; then
    mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
    echo '{}' > "$CLAUDE_SETTINGS"
fi

# Add hook to settings if not already present
if ! grep -q "extract-memories" "$CLAUDE_SETTINGS" 2>/dev/null; then
    python3 - "$CLAUDE_SETTINGS" << 'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
cfg.setdefault("hooks", {}).setdefault("Stop", [])
hook_cmd = f"/usr/bin/python3 {__import__('os').path.expanduser('~')}/.claude/hooks/extract-memories.py"
entry = {"hooks": [{"type": "command", "command": hook_cmd}]}
if not any(
    any(h.get("command","").endswith("extract-memories.py") for h in e.get("hooks",[]))
    for e in cfg["hooks"]["Stop"]
):
    cfg["hooks"]["Stop"].append(entry)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("  settings.json updated")
PYEOF
else
    echo "  hook already registered in settings.json"
fi

# 5. MCP registration
echo "[5/6] Registering MCP server in Claude Code..."
if [ ! -f "$MCP_CONFIG" ]; then
    mkdir -p "$(dirname "$MCP_CONFIG")"
    echo '{"mcpServers":{}}' > "$MCP_CONFIG"
fi

python3 - "$MCP_CONFIG" << 'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
cfg.setdefault("mcpServers", {})["abhimem"] = {
    "type": "http",
    "url": "http://127.0.0.1:8765/mcp"
}
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("  MCP server registered")
PYEOF

# 6. Start server now
echo "[6/6] Starting server..."
sleep 1
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/mcp 2>/dev/null | grep -q "4"; then
    echo "  server already running at http://127.0.0.1:8765"
else
    launchctl start com.abhiram.abhimem 2>/dev/null || true
    sleep 2
    echo "  server started"
fi

echo ""
echo "Done. abhimem is running."
echo ""
echo "Restart Claude Code to activate the MCP tools and Stop hook."
echo ""
echo "Tools available in Claude Code:"
echo "  mcp__abhimem__remember        — store a memory"
echo "  mcp__abhimem__recall          — semantic search"
echo "  mcp__abhimem__list_memories   — browse all memories"
echo "  mcp__abhimem__forget          — delete by ID"
echo "  mcp__abhimem__extract_and_remember — extract facts from text"
