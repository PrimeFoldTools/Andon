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
     A project file is skipped if it is a symlink, if its real path is outside the
     project folder (for example a symlinked memory/ folder), or if it is not a regular
     file. A cloned repo cannot point the hook at a file elsewhere on your disk. The
     override path is yours, so it may live anywhere.
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
import stat
import sys
import tempfile
import time
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


def _project_root(cwd):
    try:
        return Path(cwd).resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _safe_project_file(candidate, root):
    """Return the resolved path of a project MEMORY.md only if it is safe to read.

    A cloned repo controls everything under the project folder, so refuse: a symlink at
    the file itself, any path whose real location is outside the project root (this also
    catches a symlinked parent folder such as memory/ -> /elsewhere), and anything that is
    not a regular file (FIFO, device, directory).
    """
    try:
        if candidate.is_symlink():
            return None
        real = candidate.resolve(strict=True)
        real.relative_to(root)  # ValueError when the real path escapes the project
        if not stat.S_ISREG(os.lstat(real).st_mode):
            return None
        return real
    except (OSError, RuntimeError, ValueError):
        return None


def _find_memory(cwd):
    """Return (path, root). root is None for the user's own override path."""
    override = os.environ.get("AUTO_ORIENT_MEMORY_PATH")
    if override:
        # Set by the user in their own environment, not by the repo: it may live
        # anywhere and may be a symlink, but it must still be a regular file.
        try:
            p = Path(override).expanduser().resolve(strict=True)
            if stat.S_ISREG(os.stat(p).st_mode):
                return p, None
        except (OSError, RuntimeError):
            pass
    if cwd:
        root = _project_root(cwd)
        if root is not None:
            for c in (Path(cwd) / "memory" / "MEMORY.md", Path(cwd) / "MEMORY.md"):
                real = _safe_project_file(c, root)
                if real is not None:
                    return real, root
    return None, None


def _read_capped(path, limit, no_follow):
    """Read at most `limit` bytes. Never follows a final symlink for project files and
    never blocks on a FIFO swapped in after the checks above."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if no_follow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, left = [], limit
        while left > 0:
            b = os.read(fd, min(left, 65536))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


MARKER_MAX_AGE_S = 7 * 24 * 3600


def _marker_dir():
    """Private per-user folder for the once-per-session markers (0700, owned by us)."""
    d = Path(tempfile.gettempdir()) / f"andon-auto-orient-{os.getuid()}"
    try:
        d.mkdir(mode=0o700)
    except FileExistsError:
        pass
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise OSError("marker dir is not a private directory")
    if st.st_mode & 0o077:
        os.chmod(d, 0o700)
    return d


def _prune_markers(d):
    """Delete markers older than MARKER_MAX_AGE_S, so they no longer pile up."""
    cutoff = time.time() - MARKER_MAX_AGE_S
    try:
        entries = list(os.scandir(d))
    except OSError:
        return
    for e in entries:
        try:
            if (e.name.startswith("auto_orient_") and e.is_file(follow_symlinks=False)
                    and e.stat(follow_symlinks=False).st_mtime < cutoff):
                os.unlink(e.path)
        except OSError:
            continue


def main():
    if os.environ.get("AUTO_ORIENT", "on").lower() == "off":
        _noop()

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        _noop()

    cwd = data.get("cwd") or os.getcwd()
    session_id = data.get("session_id") or ""

    mem, root = _find_memory(cwd)
    if not mem:
        _noop()

    # Read FIRST — a failed read must NOT mark the session oriented (else it never retries).
    # Read only cap + 1 bytes: enough to know the file is over the cap, never the whole file.
    max_bytes = _max_bytes()
    try:
        raw = _read_capped(mem, max_bytes + 1, no_follow=root is not None)
    except OSError:
        _noop()
    if raw is None:
        _noop()
    text = raw.decode("utf-8", errors="ignore")
    if not text.strip():
        _noop()

    # Once-per-session guard, only after a successful read. Don't re-inject every prompt.
    # Markers live in a private 0700 folder, are created exclusively (never through a
    # symlink), and markers older than a week are pruned.
    if session_id:
        key = hashlib.sha1(f"{session_id}:{mem}".encode()).hexdigest()[:16]
        try:
            d = _marker_dir()
            _prune_markers(d)
            fd = os.open(str(d / f"auto_orient_{key}"),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                         0o600)
            os.close(fd)
        except FileExistsError:
            _noop()
        except OSError:
            pass  # can't mark → still inject once now (safe, just not deduped)

    # Over the byte cap: keep whole lines (never cut mid-character). Only a first line
    # that is over the cap by itself gets cut mid-line, on a character boundary.
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
        display_path = mem.relative_to(root) if root is not None else mem.name
    except ValueError:
        display_path = mem.name
    _emit_context(f"[Project memory — loaded from {display_path} at session start]\n\n{text}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail-safe: never block a prompt because the hook errored.
        _noop()
