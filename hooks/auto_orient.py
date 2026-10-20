#!/usr/bin/env python3
"""
auto_orient.py — UserPromptSubmit hook that loads your MEMORY.md index into context
at the start of a session, so the agent starts oriented instead of amnesiac.

This is the other half of the anti-amnesia loop: the wrap protocol SAVES state at
session end; orient LOADS it at the start. Two hooks, one loop.

USAGE
  1. Copy to your agent config dir (e.g. ~/.claude/hooks/).
  2. Register as a UserPromptSubmit hook in settings.json — MERGE into any existing array:
       { "hooks": { "UserPromptSubmit": [{ "matcher": "*",
           "hooks": [{ "type": "command",
             "command": "python3 /ABSOLUTE/PATH/TO/.claude/hooks/auto_orient.py" }] }] } }
  3. It looks for `memory/MEMORY.md` (then `MEMORY.md`) in the project; override with
     AUTO_ORIENT_MEMORY_PATH=/abs/path/to/MEMORY.md
  4. It injects the index ONCE per session (not every turn — that would waste context).
     Turn it off with AUTO_ORIENT=off.
  5. It caps the index text at 8000 bytes (a one-line header and a truncation note are
     added on top); override with AUTO_ORIENT_MAX_BYTES=N. A value that is not a
     positive whole number falls back to 8000.

Fail-safe by design: any error, a missing file, or an already-oriented session →
injects nothing and never blocks the prompt. (Contract: UserPromptSubmit context
injection = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
"additionalContext": "..."}}, exit 0.)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_MAX_BYTES = 8000


def _max_bytes():
    # Parsed at call time, not import time: an int() at module level ran outside the
    # fail-safe try below, so AUTO_ORIENT_MAX_BYTES=abc crashed the hook (exit 1).
    try:
        n = int(os.environ.get("AUTO_ORIENT_MAX_BYTES", ""))
    except ValueError:
        return DEFAULT_MAX_BYTES
    return n if n > 0 else DEFAULT_MAX_BYTES  # 0 / negative would keep nothing


def _emit_context(text):
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": text,
                }
            }
        )
        + "\n"
    )
    sys.exit(0)


def _noop():
    sys.exit(0)


def _find_memory(cwd):
    override = os.environ.get("AUTO_ORIENT_MEMORY_PATH")
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    if cwd:
        candidates += [Path(cwd) / "memory" / "MEMORY.md", Path(cwd) / "MEMORY.md"]
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def main():
    if os.environ.get("AUTO_ORIENT", "on").lower() == "off":
        _noop()

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        _noop()

    cwd = data.get("cwd") or os.getcwd()
    session_id = data.get("session_id") or ""

    mem = _find_memory(cwd)
    if not mem:
        _noop()

    # Read FIRST — a failed read must NOT mark the session oriented (else it never retries).
    try:
        text = mem.read_text(errors="ignore")
    except OSError:
        _noop()
    if not text.strip():
        _noop()

    # Once-per-session guard, only after a successful read. Don't re-inject every prompt.
    if session_id:
        key = hashlib.sha1(f"{session_id}:{mem}".encode()).hexdigest()[:16]
        marker = Path(tempfile.gettempdir()) / f"auto_orient_{key}"
        try:
            if marker.exists():
                _noop()
            marker.write_text("1")
        except OSError:
            pass  # can't mark → still inject once now (safe, just not deduped)

    # Over the byte cap: keep whole lines (never cut mid-character). Only a first line
    # that is over the cap by itself gets cut mid-line, on a character boundary.
    max_bytes = _max_bytes()
    if len(text.encode()) > max_bytes:
        kept, total = [], 0
        for ln in text.splitlines(keepends=True):
            b = len(ln.encode())
            if total + b > max_bytes:
                break
            kept.append(ln)
            total += b
        if not kept:
            # The first line alone is over the cap: cut it at the cap on a character
            # boundary (drop a split multi-byte tail) rather than inject only the notice.
            kept = [text.encode()[:max_bytes].decode("utf-8", errors="ignore")]
        text = "".join(kept).rstrip() + "\n\n…[truncated — open the file for the rest]"

    # Show a path relative to the project when possible (disambiguates root vs memory/).
    try:
        display_path = mem.relative_to(Path(cwd))
    except ValueError:
        display_path = mem.name
    _emit_context(f"[Project memory — loaded from {display_path} at session start]\n\n{text}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail-safe: never block a prompt because the hook errored.
        _noop()
