#!/bin/bash
# abhimem uninstaller — removes all hooks, LaunchAgent, and MCP registration

set -e

LAUNCHAGENT_DIR="$HOME/Library/LaunchAgents"
LAUNCHAGENT_PLIST="$LAUNCHAGENT_DIR/com.abhiram.abhimem.plist"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
HOOKS_DIR="$HOME/.claude/hooks"
MCP_CONFIG="$HOME/.claude/mcp.json"

echo "abhimem uninstaller"
echo "==================="
echo ""

# 1. Stop and remove LaunchAgent
echo "[1/4] Removing LaunchAgent..."
if [ -f "$LAUNCHAGENT_PLIST" ]; then
    launchctl unload "$LAUNCHAGENT_PLIST" 2>/dev/null || true
    launchctl stop com.abhiram.abhimem 2>/dev/null || true
    rm -f "$LAUNCHAGENT_PLIST"
    echo "  removed"
else
    echo "  not found (skipping)"
fi

# 2. Remove Stop hook from settings.json
echo "[2/4] Removing Stop hook from Claude Code settings..."
if [ -f "$CLAUDE_SETTINGS" ] && grep -q "extract-memories" "$CLAUDE_SETTINGS" 2>/dev/null; then
    python3 - "$CLAUDE_SETTINGS" << 'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
stops = cfg.get("hooks", {}).get("Stop", [])
cfg["hooks"]["Stop"] = [
    e for e in stops
    if not any(
        h.get("command", "").endswith("extract-memories.py")
        for h in e.get("hooks", [])
    )
]
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("  hook removed from settings.json")
PYEOF
else
    echo "  hook not found (skipping)"
fi

# 3. Remove MCP server from mcp.json
echo "[3/4] Removing MCP server registration..."
if [ -f "$MCP_CONFIG" ] && grep -q "abhimem" "$MCP_CONFIG" 2>/dev/null; then
    python3 - "$MCP_CONFIG" << 'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
cfg.get("mcpServers", {}).pop("abhimem", None)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("  MCP server removed from mcp.json")
PYEOF
else
    echo "  not found (skipping)"
fi

# 4. Remove hook scripts
echo "[4/4] Removing hook scripts..."
removed=0
for f in extract-memories.py extract-memories-worker.py; do
    if [ -f "$HOOKS_DIR/$f" ]; then
        rm -f "$HOOKS_DIR/$f"
        removed=$((removed + 1))
    fi
done
if [ $removed -gt 0 ]; then
    echo "  removed $removed file(s) from $HOOKS_DIR"
else
    echo "  not found (skipping)"
fi

echo ""
echo "Done. abhimem has been uninstalled."
echo ""
echo "Your memory database (~/.abhimem/ or local memories.db) was NOT deleted."
echo "To remove it: rm -rf ~/.abhimem"
echo ""
echo "Restart Claude Code to apply changes."
