#!/usr/bin/env python3
"""
claim_check_hook.py — Stop-hook enforcer skeleton.

Blocks turn-end if the assistant claims "done / shipped / verified / complete"
without a recent verification log entry that refers to that specific claim and
was written in the same session. Make the agent's done-claim mean something.

USAGE
  1. Copy hooks/claim_check_hook.py + hooks/log_claim.py into your agent config dir
     (e.g. ~/.claude/hooks/).
  2. Register as a Stop hook in settings.json — MERGE into any existing "Stop" array,
     don't overwrite it:
       { "hooks": { "Stop": [{ "matcher": "*",
           "hooks": [{ "type": "command",
             "command": "CLAIM_CHECK_ENFORCE_MODE=warn python3 /abs/path/to/claim_check_hook.py" }] }] } }
  3. Tell your agent (in CLAUDE.md) that after it verifies a done-claim it must log it:
       python3 /abs/path/to/log_claim.py "what I claimed" "how I verified it"
     log_claim.py creates ~/.claude/state/claim_checks/log.jsonl on first run.
  4. Test it fired:
       echo '{"transcript_path":"/dev/null"}' | python3 claim_check_hook.py
       → should print {"continue": true, ...}
  5. Modes (default = warn; promote to block once you trust it):
       CLAIM_CHECK_ENFORCE_MODE=warn    # surface a warning, don't block  (DEFAULT)
       CLAIM_CHECK_ENFORCE_MODE=block   # block turn-end until a fresh log entry exists
       CLAIM_CHECK_ENFORCE_MODE=off     # silent pass-through

NOTE — this is a STOP hook; Stop hooks block with {"decision": "block"}.
A PreToolUse hook uses a DIFFERENT schema ({"hookSpecificOutput": {"permissionDecision": "deny"}}).
The two are NOT interchangeable — using the wrong one is a silent no-op.

KNOWN LIMITS (binding is a forcing function, not a cryptographic guarantee):
  - Token-overlap binding raises the bar from "type any two words" to "reference this
    claim's subject in the same session," but an agent that names the subject in its
    verification without doing the work can still pass — that act is legible on the record.
  - A truly subjectless closure ("Done." with no other content in the turn) is unbindable
    and degrades to a same-session freshness floor (still stronger than global freshness).
  - Two claims in the SAME sentence share that sentence's subject tokens (per-claim binding
    separates claims across sentences, not within one).

CUSTOMIZE
  - COMPLETION_VERBS — closure verbs that trigger the check (kept tight to avoid false-fires).
  - OPT_IN_VERBS — looser verbs; enable only if your domain needs them.
  - CLAIM_CHECK_FRESH_MIN — "fresh" window (15 is forgiving; don't go below ~5).
  - OVERLAP_MIN_TOKENS — content tokens a log entry must share with the claim (default 1).
  - CLAIM_CHECKS_LOG_PATH env var — override the log location.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------- CONFIG (edit these) ----------
CLAIM_CHECKS_LOG = Path(
    os.environ.get(
        "CLAIM_CHECKS_LOG_PATH",
        str(Path.home() / ".claude" / "state" / "claim_checks" / "log.jsonl"),
    )
).expanduser()
CLAIM_CHECK_FRESH_MIN = 15      # minutes — log entries newer than this count as fresh
STALE_THRESHOLD_S = 30          # transcript-flush race guard
DEFAULT_MODE = "warn"           # warn | block | off  (start warn; promote to block once tuned)
OVERLAP_MIN_TOKENS = 1          # a fresh log entry must share >=N content tokens with the claim

# Unambiguous closure verbs — kept tight so everyday prose ("the data is current",
# "are we done here?") does NOT false-fire.
COMPLETION_VERBS = (
    "ready", "shipped", "complete", "completed", "done",
    "verified", "fixed", "resolved", "production[\\s-]ready",
)
# Looser verbs — higher false-positive rate in normal English. Add deliberately if your
# domain needs them: "live", "functional", "applied", "patched", "synced",
# "current", "in[\\s-]sync", "wired".
OPT_IN_VERBS = ()
COMPLETION_VERBS = COMPLETION_VERBS + OPT_IN_VERBS


# ---------- PATTERNS ----------
_VERBS = "|".join(COMPLETION_VERBS)

CLAIM_PATTERNS = (
    # Standalone / sentence-start closure markers:
    # "Done.", "All set.", "Shipped:", "Ready — ..."
    re.compile(
        rf"(?im)(?:^|(?<=[.!?])\s+|\n)\s*"
        rf"(?:done|complete|completed|ready|verified|fixed|resolved|shipped|"
        rf"production[\s-]ready|all\s+set)\b"
        rf"(?:\s*(?:[.!:;]|—|-)|\s*$)"
    ),
    # Leading action-claim forms — REQUIRE a determiner after the verb so that
    # ordinary participle-adjective prose ("Fixed income securities…",
    # "Shipped goods arrived…", "Verified users get a badge…") does NOT
    # false-fire. Only "Shipped the fix.", "Fixed the tests.",
    # "Implemented the migration." (verb + determiner + object) trigger.
    re.compile(
        r"(?im)(?:^|(?<=[.!?])\s+|\n)\s*"
        r"(?:shipped|fixed|verified|resolved|completed|implemented)\s+"
        r"(?:the|this|that|these|those|all|our|your|its|my)\b[^\n.!?]{0,80}"
    ),
    # "X is/are [already/now/fully] $VERB"
    re.compile(
        rf"\b(?:is|are|has\s+been|have\s+been|now|finally|fully|officially|already)"
        rf"(?:\s+(?:already|just|now|fully))?"
        rf"\s+(?:{_VERBS})\b",
        re.IGNORECASE,
    ),
    # Bolded markers
    re.compile(r"\*\*(?:SHIPPED|DONE|COMPLETE|READY|VERIFIED|LIVE|FIXED|RESOLVED|CLOSED)\*\*"),
    # "verified end-to-end"
    re.compile(r"\bverified\s+end[\s-]to[\s-]end\b", re.IGNORECASE),
)

EXEMPT_CONTEXT = (
    re.compile(r'["“]\s*[^"”]{0,80}\bis\s+(?:done|ready|complete)\b[^"”]{0,80}\s*["”]'),
    re.compile(r"\bwould\s+(?:be|claim|say)\s+(?:is|are)\s+", re.IGNORECASE),
)


# ---------- EMIT HELPERS ----------
def emit_ok():
    sys.stdout.write('{"continue": true, "suppressOutput": true}\n')
    sys.exit(0)


def emit_warn(msg):
    sys.stdout.write(json.dumps({"continue": True, "systemMessage": msg}) + "\n")
    sys.exit(0)


def emit_block(reason):
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
    sys.exit(0)


# ---------- TRANSCRIPT READING (with recency guard) ----------
def _parse_ts_as_utc(ts_str):
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def read_last_assistant_message(transcript_path):
    """Return the most recent assistant message text. Empty string if the
    most recent entry is older than STALE_THRESHOLD_S (transcript-flush race)."""
    p = Path(transcript_path).expanduser()
    if not p.exists():
        return ""
    try:
        lines = p.read_text(errors="ignore").splitlines()
    except OSError:
        return ""
    now_ts = datetime.now(timezone.utc).timestamp()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        ts_str = entry.get("timestamp")
        if ts_str:
            try:
                if (now_ts - _parse_ts_as_utc(ts_str)) > STALE_THRESHOLD_S:
                    return ""  # stale prior turn; do not block on it
            except (ValueError, AttributeError):
                pass
        msg = entry.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return ""
    return ""


# ---------- CLAIM DETECTION + EXEMPTION ----------
def _is_backtick_wrapped(text, m_start, m_end):
    bt_open = text.rfind("`", max(0, m_start - 200), m_start)
    if bt_open == -1:
        return False
    bt_close = text.find("`", m_end, min(len(text), m_end + 200))
    if bt_close == -1:
        return False
    return "`" not in text[bt_open + 1:m_start]


def _is_blockquote_line(text, m_start):
    line_start = text.rfind("\n", 0, m_start) + 1
    i = line_start
    saw_gt = False
    while i < m_start:
        c = text[i]
        if c == ">":
            saw_gt = True
            i += 1
        elif c in (" ", "\t"):
            i += 1
        else:
            break
    return saw_gt


def _ends_in_question(text, m_end):
    # Scan from the match end to the next sentence terminator. If that
    # terminator is "?", the claim sits inside a QUESTION ("Shipped the fix?",
    # "The tests are fixed now?") — not an assertion — so it must not fire.
    for i in range(m_end, min(len(text), m_end + 160)):
        c = text[i]
        if c in ".!?\n":
            return c == "?"
    return False


def find_claims(text):
    matches = []
    seen = set()
    for pat in CLAIM_PATTERNS:
        for m in pat.finditer(text):
            phrase = m.group(0).strip()
            key = phrase.lower()
            if key in seen:
                continue
            if _ends_in_question(text, m.end()):
                continue
            if _is_backtick_wrapped(text, m.start(), m.end()):
                continue
            if _is_blockquote_line(text, m.start()):
                continue
            ctx = text[max(0, m.start() - 80):min(len(text), m.end() + 80)]
            if any(ex.search(ctx) for ex in EXEMPT_CONTEXT):
                continue
            seen.add(key)
            matches.append(phrase)
    return matches[:5]


# ---------- CLAIM FINGERPRINTING (MUST stay byte-identical to log_claim.py) ----------
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


def _sentence_around(text, idx):
    """The sentence enclosing position `idx`, bounded by .!?\\n or string ends."""
    start = 0
    for i in range(idx - 1, -1, -1):
        if text[i] in ".!?\n":
            start = i + 1
            break
    end = len(text)
    for i in range(idx, len(text)):
        if text[i] in ".!?\n":
            end = i
            break
    return text[start:end]


def _claim_token_sets(message, claim_phrases):
    """One token set PER claim — the content tokens of the SENTENCE each claim sits
    in (the matched phrase itself is usually just the closure verb; the subject —
    "migration" — lives in the sentence). Per-claim (not a pooled union) so that
    verifying one claim cannot launder the other claims in the same turn.

    Fallback: if a claim's sentence has no content tokens (a bare "Done." with no
    subject), fall back to the whole message's tokens, so an adjacent "I refactored
    the parser. Done." still binds on "parser". A truly subjectless message yields an
    empty set — see has_fresh_log for how that (unbindable) case is handled."""
    msg_tokens = _content_tokens(message)
    low = message.lower()
    sets = []
    for phrase in claim_phrases:
        j = low.find(phrase.lower())
        toks = _content_tokens(_sentence_around(message, j)) if j != -1 else _content_tokens(phrase)
        if not toks:
            toks = msg_tokens
        sets.append(toks)
    return sets


# ---------- FRESH LOG CHECK ----------
def has_fresh_log(claim_token_sets, session_id, window_min=CLAIM_CHECK_FRESH_MIN):
    """A turn is cleared only if EVERY claim is covered by a fresh log entry that
    (a) is within the window, (b) was written in THIS session (kills the cross-session
    alibi), and (c) whose fingerprint shares >= OVERLAP_MIN_TOKENS content tokens with
    that claim (kills the self- and unrelated-work alibis). Per-claim, not pooled —
    verifying one claim cannot launder the others in the same turn.

    Legacy entries (pre-binding schema) are honored ONLY when the log has no new-schema
    entries at all (a genuinely un-migrated log), so one hand-written legacy line can't
    downgrade an upgraded log. Legacy fail-open self-expires within one window.

    A contentless claim (empty token set — a bare subjectless "Done.") is unbindable and
    is covered by the same-session freshness floor only. Documented residual limit; still
    strictly stronger than the global-freshness behavior it replaces."""
    if not CLAIM_CHECKS_LOG.exists():
        return False
    try:
        lines = CLAIM_CHECKS_LOG.read_text().splitlines()
    except OSError:
        return False
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = now_ts - (window_min * 60)
    fresh_entry_tokens = []   # same-session, new-schema, fresh entries
    legacy_fresh = False
    have_new_schema = False
    for line in reversed(lines[-200:]):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts_str = entry.get("timestamp") or entry.get("ts")
            if not ts_str:
                continue
            if _parse_ts_as_utc(ts_str) < cutoff:
                continue  # stale
            if "session_id" not in entry or "claim_fingerprint" not in entry:
                legacy_fresh = True
                continue
            have_new_schema = True
            esid = entry.get("session_id") or ""
            if session_id and esid != session_id:
                continue  # another session's (or a session-less) entry cannot alibi this claim
            fresh_entry_tokens.append(set(str(entry.get("claim_fingerprint") or "").split()))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            continue
    # Genuinely un-migrated log (only legacy entries present) → preserve prior fail-open.
    if legacy_fresh and not have_new_schema:
        return True
    # Every claim must be individually covered by a fresh, same-session entry.
    for cset in claim_token_sets:
        if not cset:
            if not fresh_entry_tokens:
                return False  # subjectless claim, no same-session evidence at all
            continue          # subjectless claim → same-session freshness floor
        if not any(len(cset & ets) >= OVERLAP_MIN_TOKENS for ets in fresh_entry_tokens):
            return False
    return True


# ---------- MAIN ----------
def main():
    mode = os.environ.get("CLAIM_CHECK_ENFORCE_MODE", DEFAULT_MODE).lower()
    if mode == "off":
        emit_ok()

    try:
        hook_data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        emit_ok()

    if hook_data.get("stop_hook_active") is True:
        emit_ok()  # prevent re-block loop

    transcript_path = hook_data.get("transcript_path")
    if not transcript_path:
        emit_ok()

    message = read_last_assistant_message(transcript_path)
    if not message:
        emit_ok()

    claims = find_claims(message)
    if not claims:
        emit_ok()

    # The transcript filename stem IS the session UUID (== CLAUDE_CODE_SESSION_ID that
    # log_claim.py stamps). This is how the entry is bound to THIS session.
    session_id = Path(transcript_path).stem
    claim_token_sets = _claim_token_sets(message, claims)
    if has_fresh_log(claim_token_sets, session_id):
        emit_ok()

    bullets = "\n".join(f'  - "{p}"' for p in claims)
    reason = (
        f"⚠️  Claim-check enforcer — done-claim detected without a fresh verification "
        f"log entry that refers to THIS claim, from THIS session.\n"
        f"Matched phrases:\n{bullets}\n\n"
        f"An entry clears this gate only if it is < {CLAIM_CHECK_FRESH_MIN}min old, was written "
        f"in this session, and its claim references what you're claiming here.\n"
        f"Before stopping this turn:\n"
        f"  1. Actually run the verification for THIS claim — the test, the end-to-end\n"
        f"     check, or a first-hand read of the artifact you're claiming about.\n"
        f"  2. Only if it genuinely passed, record the evidence for this specific claim.\n"
        f"A fresh entry about unrelated work, or from another session, will NOT clear this gate.\n\n"
        f"Override: CLAIM_CHECK_ENFORCE_MODE=warn or =off"
    )

    if mode == "warn":
        emit_warn(reason)
    emit_block(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # True fail-safe: an unexpected error must NEVER break the operator's turn.
        emit_ok()
