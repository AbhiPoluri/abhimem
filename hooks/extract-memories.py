#!/usr/bin/env python3
"""Claude Code Stop hook — auto-extract memories from conversation transcript.

Reads the transcript JSONL, runs llama3.2:3b to extract facts,
stores them to abhimem SQLite DB (~/.abhimem/memory.db).

Runs extraction in a detached subprocess so the hook returns immediately.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

WORKER = Path(__file__).parent / "extract-memories-worker.py"
MIN_TRANSCRIPT_CHARS = 300


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    transcript_path = data.get("transcript_path")
    if not transcript_path or not Path(transcript_path).exists():
        sys.exit(0)

    # Quick check — skip trivially short sessions
    try:
        size = Path(transcript_path).stat().st_size
        if size < MIN_TRANSCRIPT_CHARS:
            sys.exit(0)
    except Exception:
        sys.exit(0)

    session_id = data.get("session_id", "unknown")

    # Fire and forget — don't block the hook
    subprocess.Popen(
        [sys.executable, str(WORKER), transcript_path, session_id],
        start_new_session=True,
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=open(Path.home() / ".claude" / "hooks" / "extract-memories.log", "a"),
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
