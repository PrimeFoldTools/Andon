#!/usr/bin/env python3
"""
log_claim.py — write the verification entry that claim_check_hook.py looks for.

Run this right AFTER you actually verify a done-claim:

    python3 log_claim.py "what you claimed" "how you verified it"

It creates ~/.claude/state/claim_checks/log.jsonl on first run, so the very first
claim doesn't get blocked by a missing file/dir. Override the location with the
CLAIM_CHECKS_LOG_PATH env var (must match the hook's).

Each entry also stamps:
  - session_id — from CLAUDE_CODE_SESSION_ID (the current agent session), so one
    session's log cannot alibi another session's claim on a shared $HOME.
  - claim_fingerprint — content tokens of the claim (subject nouns, closure verbs
    stopworded), so the hook can require a fresh entry that plausibly refers to the
    specific claim being made rather than accepting any recent entry. See issue #2.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAIM_CHECKS_LOG = Path(
    os.environ.get(
        "CLAIM_CHECKS_LOG_PATH",
        str(Path.home() / ".claude" / "state" / "claim_checks" / "log.jsonl"),
    )
).expanduser()

# ---- claim fingerprinting (MUST stay byte-identical to claim_check_hook.py) ----
# Closure verbs are stopworded: they appear in every done-claim, so binding on them
# would be vacuous ("done" matches "done"). Only SUBJECT tokens (migration, endpoint,
# paginator) carry the fingerprint. Cross-file consistency is pinned by a test.
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "this", "that", "these", "those", "and", "or", "but",
    "to", "of", "in", "on", "at", "it", "its", "for", "with", "now", "all",
    "not", "from", "into", "out", "up", "down", "so", "as", "by", "just", "then",
    "also", "ive", "weve", "our", "your", "my", "me", "we", "i",
    # closure verbs — must NOT bind
    "done", "complete", "completed", "completing", "ready", "verified", "verify",
    "fixed", "fix", "resolved", "resolve", "shipped", "ship", "live", "set",
    "wired", "implemented", "implement", "applied", "apply", "patched", "synced",
    "finished", "finish", "production", "working",
    # generic dev nouns — too common to bind a specific claim on their own
    "test", "tests", "code", "file", "files", "thing", "things", "stuff",
    "work", "item", "items", "run", "ran",
})


def _content_tokens(text):
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {t for t in toks if len(t) >= 2 and t not in _STOPWORDS}


def _fingerprint(*texts):
    # Fingerprint the claim AND the verification: an honest verification usually
    # names the artifact ("route", "alembic", "stripe"), so a claim worded
    # differently from the verification still binds. See issue #2 discussion.
    return " ".join(sorted(_content_tokens(" ".join(t for t in texts if t))))


def main():
    claim = sys.argv[1] if len(sys.argv) > 1 else ""
    verification = sys.argv[2] if len(sys.argv) > 2 else ""
    if not claim or not verification:
        print(
            'usage: python3 log_claim.py "<what you claim>" "<how you verified it>"',
            file=sys.stderr,
        )
        sys.exit(2)

    CLAIM_CHECKS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "claim": claim,
        "verification": verification,
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
        "claim_fingerprint": _fingerprint(claim, verification),
    }
    with CLAIM_CHECKS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"logged → {CLAIM_CHECKS_LOG}")


if __name__ == "__main__":
    main()
