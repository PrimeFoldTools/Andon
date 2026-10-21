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
  6. Evidence source (opt-in, v0.2; independent of the mode above):
       CLAIM_CHECK_EVIDENCE=log     # the agent's own log_claim.py line           (DEFAULT)
       CLAIM_CHECK_EVIDENCE=tool    # a harness-recorded PASSING verification run in the
                                    # transcript, this session, inside the window
       CLAIM_CHECK_EVIDENCE=both    # both of the above
     A non-blank unrecognized value is treated as "both" (strictest), not as "log"; a
     blank value means "log". In tool/both mode an internal error fails CLOSED.
     CLAIM_CHECK_VERIFY_COMMANDS="pytest,make test" overrides the allowlist.

NOTE — this is a STOP hook; Stop hooks block with {"decision": "block"}.
A PreToolUse hook uses a DIFFERENT schema ({"hookSpecificOutput": {"permissionDecision": "deny"}}).
The two are NOT interchangeable — using the wrong one is a silent no-op.

KNOWN LIMITS (binding is a forcing function, not a cryptographic guarantee):
  - Binding is by content-token overlap: it establishes TOPICAL correspondence, not proof.
    An agent that names the subject in its verification without doing the work can pass
    (legible on the record), and an incidental shared word — a file extension ("py"), an
    adverb ("successfully"), a number ("200") — can bind unrelated claim/evidence.
  - A truly subjectless closure ("Done." with no other content in the turn) is unbindable
    and degrades to a same-session freshness floor (still stronger than global freshness).
  - Two claims in the SAME sentence share that sentence's subject tokens (claims in
    DIFFERENT sentences are bound separately).
  - log_claim.py records the session from CLAUDE_CODE_SESSION_ID (set by Claude Code). Under
    a harness that does not set it, entries are session-less and cannot clear a session-bound
    claim — the verification must run in the same session that makes the claim.
  - CLAIM_CHECK_EVIDENCE=tool reads the transcript, which the agent's own Bash can still
    append to: forging a passing run is possible — deliberate, and the call that writes it
    is recorded, though the forging code can hide inside a script that call runs. A
    recorded run is not bound to the claim's subject (any passing pytest covers "the
    migration is complete"), it says nothing about whether the tests are meaningful or
    selected (-k, -run NOMATCH), and it can predate later edits. The allowlist credits a
    command LINE (the harness-recorded input.command), not the program that ran: a fake
    `pytest` on PATH, ./pytest, a shell function or (global) alias named pytest passes.
    Only the Bash tool's record shape is verified; Monitor, MCP tools, PowerShell and
    subagent (sidechain) runs are not credited. A block holds one turn (stop_hook_active).
  - "**DONE** Want me to deploy?" (a bold marker followed by a question on the same line) is
    exempted as a question — genuinely ambiguous; terminator-ended closures ("Done. Anything
    else?") are treated as completed assertions and DO gate. Plain bullet closers
    ("- Fixed the auth bug.") are a known detector-recall gap, tracked with issue #3.

CUSTOMIZE
  - COMPLETION_VERBS — closure verbs that trigger the check (kept tight to avoid false-fires).
  - OPT_IN_VERBS — looser verbs; enable only if your domain needs them.
  - CLOSURE_NOUNS — subjects for the bare "Task complete!" / "Migration done." form.
  - CLAIM_CHECK_FRESH_MIN — "fresh" window (15 is forgiving; don't go below ~5).
  - OVERLAP_MIN_TOKENS — content tokens a log entry must share with the claim (default 1).
  - CLAIM_CHECKS_LOG_PATH env var — override the log location.
"""
from __future__ import annotations

import json
import os
import re
import sys
from bisect import bisect_left, bisect_right
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
    "ready", "shipped", "complete", "completed", "done", "finished",
    "verified", "fixed", "resolved", "production[\\s-]ready",
    # State-of-the-thing closures: "the fix is in place", "everything is now
    # wired up". Bare "wired" is NOT included — "the button is wired to the
    # handler" is description, not closure. "live" is below, it needs a guard.
    "in[\\s-]place", "wired[\\s-]up",
)
# "live" is a closure only when it ENDS the clause: "the change is live",
# "the change is live now". Attributive and compound uses are ordinary English
# — "this is live data", "is live-streaming to viewers" — so it carries its own
# terminal guard instead of sitting in the list above.
LIVE_VERB = (
    r"live(?![-\u2010-\u2015\w])"
    r"(?=\s*(?:[.,;:!?)\]]|$|\b(?:now|again|already|and|on|in|for)\b))"
)
# Looser verbs — higher false-positive rate in normal English. Add deliberately if your
# domain needs them: "functional", "applied", "patched", "synced", "current",
# "in[\\s-]sync". NOT "working" — "I'm now working on the tests" would fire.
OPT_IN_VERBS = ()
COMPLETION_VERBS = COMPLETION_VERBS + OPT_IN_VERBS

# Subject-noun closures: "Task complete!", "Migration done." — a small noun list
# plus a closure verb, anchored to sentence start, so it stays narrow.
CLOSURE_NOUNS = (
    "task", "work", "job", "migration", "change", "fix", "deploy", "deployment",
    "refactor", "build", "patch", "update", "release", "rollout", "feature",
    "implementation", "cleanup", "ticket", "pr", "merge",
)


# ---------- PATTERNS ----------
_VERBS = "|".join(COMPLETION_VERBS + (LIVE_VERB,))
_CLOSURE_NOUNS = "|".join(CLOSURE_NOUNS)
# Optional first-person lead-in for the sentence-start forms. Agents write
# "I've implemented the migration." far more often than "Implemented the
# migration." — without this the leading subject defeated both anchors.
_SUBJECT = r"(?:(?:i|we)(?:'ve|\s+have|\s+just|\s+have\s+just)?\s+)?"

CLAIM_PATTERNS = (
    # Standalone / sentence-start closure markers:
    # "Done.", "All set.", "Shipped:", "Ready — ..."
    re.compile(
        rf"(?im)(?:^|(?<=[.!?])\s+|\n)\s*"
        rf"(?:done|complete|completed|ready|verified|fixed|resolved|shipped|"
        rf"implemented|finished|production[\s-]ready|all\s+set)\b"
        rf"(?:\s*(?:[.!:;]|—|-)|\s*$)"
    ),
    # Subject-noun closure: "Task complete!", "Migration done." — bare noun +
    # closure verb with no copula. ("The fix is done." is the auxiliary
    # pattern below, not this one.)
    re.compile(
        rf"(?im)(?:^|(?<=[.!?])\s+|\n)\s*"
        rf"(?:(?:the|this|that)\s+)?(?:{_CLOSURE_NOUNS})\s+"
        rf"(?:done|complete|completed|finished|shipped|deployed|merged|applied|verified)\b"
        rf"(?:\s*(?:[.!:;]|—|-)|\s*$)"
    ),
    # Leading action-claim forms — REQUIRE a determiner after the verb so that
    # ordinary participle-adjective prose ("Fixed income securities…",
    # "Shipped goods arrived…", "Verified users get a badge…") does NOT
    # false-fire. Only "Shipped the fix.", "Fixed the tests.",
    # "Implemented the migration." (verb + determiner + object) trigger — with or
    # without a first-person lead-in ("I've implemented the migration.",
    # "We have fixed the tests.", "I finished the migration."). The determiner
    # requirement is what keeps "We have verified users in the table" exempt.
    re.compile(
        rf"(?im)(?:^|(?<=[.!?])\s+|\n)\s*{_SUBJECT}"
        rf"(?:shipped|fixed|verified|resolved|completed|implemented|finished)\s+"
        rf"(?:the|this|that|these|those|all|our|your|its|my)\b[^\n.!?]{{0,80}}"
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
class TranscriptUnavailable(Exception):
    """The transcript exists but could not be read. In tool/both mode this is not
    "no claim": main() turns it into a fail-closed verdict."""


def _unreadable(exc):
    if _evidence_mode() != "log":
        raise TranscriptUnavailable(exc.__class__.__name__)


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
    except OSError as e:
        _unreadable(e)
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
# Code is found the way Markdown pairs backticks, not by "nearest tick before, nearest
# tick after" — that heuristic read the CLOSING tick of one span and the OPENING tick of
# the next as one span around prose, and hid the done-claim between them.
_FENCE_OPEN = re.compile(r"(`{3,})[^`]*")      # ```lang (no backtick in the info string)
_FENCE_CLOSE = re.compile(r"(`{3,})[ \t]*")
_LIST_ITEM = re.compile(r"(?:[-+*]|(\d{1,9})[.)])(?=[ \t]|$)[ \t]*")
_QUOTE = re.compile(r" {0,3}> ?")
# Text that is not paragraph text: an ATX heading, a table row, a fence marker line (a
# ~~~ fence is only a boundary here: its lines are read as prose, as before).
_NOT_PARA = re.compile(r"#{1,6}(?:[ \t]|$)|\||`{3,}[^`]*$|~{3,}")
_TICK_RUN = re.compile(r"`+")


def _width(s):
    """Columns of leading whitespace (a tab runs to the next multiple of 4)."""
    return len(s[:len(s) - len(s.lstrip(" \t"))].expandtabs(4))


def _is_para(rest):
    return bool(rest) and not _NOT_PARA.match(rest)


def _interrupts(rest, lazy):
    """Does this line (its text after indentation) start a block that ends the paragraph
    above it? A list item does only when it has text and, if numbered, starts at 1 —
    unless the line is lazy (outside the paragraph's list item or quote): then any does."""
    if _NOT_PARA.match(rest):
        return True
    m = _LIST_ITEM.match(rest)
    return bool(m and (lazy or (rest[m.end():].strip()
                                and (m.group(1) is None or int(m.group(1)) == 1))))


def _list_markers(rest, at, items):
    """Strip the list markers that start `rest` (at column `at`), pushing each new item's
    content column onto `items`. Returns (content, its column, is it indented code) —
    5+ columns after a marker start indented code inside the item."""
    i, m = 0, _LIST_ITEM.match(rest)
    while m:
        mark = m.group(0).rstrip(" \t")
        c = at + len(mark)
        for ch in m.group(0)[len(mark):]:       # the gap, in columns (tab: next stop)
            c = c + 1 if ch == " " else (c // 4 + 1) * 4
        gap, i = c - at - len(mark), m.end()
        at += len(mark) + (gap if 1 <= gap <= 4 and i < len(rest) else 1)
        items.append(at)
        if gap > 4 and i < len(rest):
            return rest[i:], at, True
        m = _LIST_ITEM.match(rest, i)
    return rest[i:], at, False


def _escaped(text, i):
    """1 when text[i] follows an odd number of backslashes (it is escaped), else 0."""
    j = i
    while j and text[j - 1] == "\\":
        j -= 1
    return (i - j) % 2


def _intersect(xs, ys):
    """The overlaps of two sorted lists of disjoint (start, end) spans."""
    out, i, j = [], 0, 0
    while i < len(xs) and j < len(ys):
        a, b = max(xs[i][0], ys[j][0]), min(xs[i][1], ys[j][1])
        if a < b:
            out.append((a, b))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def _code_spans(text):
    """Sorted, non-overlapping (start, end) spans of Markdown code: fences, then inline.

    Blocks are read the way Markdown reads them, closely enough for this exemption: a
    fence opens on a line of 3+ backticks and an info string with no backtick, indented
    0-3 columns from its container (the page, or the list item it opens or sits in), and
    closes on a line of at least as many backticks and nothing else, indented the same
    way. A line 4+ columns in, outside a paragraph, is indented code, not a fence.
    Inline spans pair left to right: a run of N backticks closes at the next run of
    exactly N in its paragraph (so ``a `b` c`` is one span), and only text that also
    closes on the same line or the next one counts. Outside code, a backslash before a run
    makes its first tick literal, so the run opens one tick short (one escaped tick cannot
    open at all); inside a span a backslash is literal, so a tick after one still closes.
    Deliberately strict, on the side of firing: a run with no partner that near is a
    literal backtick, and a fence that never closes, or whose list item ends first, is
    plain text — an unpaired backtick never turns the rest of the message into code.
    (Indented code reads as prose: claims fire.)"""
    spans, prose = [], []     # prose = (start, end, line no) of lines outside any fence
    joins = set()             # lines that continue the paragraph of the line above
    items = []                # content columns of the open list items (ascending)
    fence, held = None, []    # open fence (start, run length, column) + the lines it holds
    para = None               # quote depth of the open paragraph (None: no paragraph)
    lines = text.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    k = 0
    while k < len(lines):
        body = lines[k][:-1] if lines[k].endswith("\r") else lines[k]
        start, ind = starts[k], _width(body)
        if fence is not None:
            rest = body.lstrip(" \t")
            if rest and ind < fence[2]:
                prose += held           # its list item ended first: plain text, and
                fence, held = None, []  # this line is read again as an ordinary line
                continue
            m = _FENCE_CLOSE.fullmatch(rest)
            if m and len(m.group(1)) >= fence[1] and ind - fence[2] <= 3:
                spans.append((fence[0], min(start + len(lines[k]) + 1, len(text))))
                fence, held = None, []
            else:
                held.append((start, start + len(body), k))
            k += 1
            continue
        depth, qend, m = 0, 0, _QUOTE.match(body)
        while m:
            depth, qend, m = depth + 1, m.end(), _QUOTE.match(body, m.end())
        rest = body[qend:].lstrip(" \t")
        if not rest:                                # a blank line ends the paragraph
            para = None
        elif depth:                                 # quoted (claims here never fire):
            if para is not None and depth <= para and not _interrupts(rest, depth < para):
                joins.add(k)                        #   track its paragraph, nothing more
            else:
                para = depth if _is_para(rest) else None
        else:
            inner = bisect_right(items, ind)        # items[:inner] hold this line
            col = items[inner - 1] if inner else 0
            lazy = bool(para) or inner < len(items)
            if para is not None and (ind - col >= 4 or not _interrupts(rest, lazy)):
                joins.add(k)                        # continues the paragraph (or lazily)
            elif ind - col >= 4:                    # indented code, not paragraph text
                del items[inner:]
                para = None
            else:                                   # a new block
                del items[inner:]
                rest, at, code = _list_markers(rest, ind, items)
                if _QUOTE.match(rest):          # a quote opens inside the item: keep
                    para = 1 if _is_para(rest.lstrip(" >\t")) else None  # only this
                    prose.append((start, start + len(body), k))
                    k += 1
                    continue
                m = None if code else _FENCE_OPEN.fullmatch(rest)
                if m:
                    fc = at if at != ind else col
                    fence, held = (start, len(m.group(1)), fc), [(start, start + len(body), k)]
                    para = None
                    k += 1
                    continue
                para = 0 if _is_para(rest) and not code else None
        prose.append((start, start + len(body), k))
        k += 1
    prose += held               # an unclosed fence is plain text
    runs, pid = [], {}          # runs (start, end, line no, escaped); line no -> paragraph
    for a, b, n in prose:
        pid[n] = pid.get(n - 1, n) if n in joins else n
        runs += [(m.start(), m.end(), n, _escaped(text, m.start()))
                 for m in _TICK_RUN.finditer(text, a, b)]
    nxt, last = [None] * len(runs), {}
    for i in range(len(runs) - 1, -1, -1):   # the next run that closes it (linear): an
        s, e, _n, esc = runs[i]              # escaped run opens one tick short, but
        nxt[i], last[e - s] = last.get(e - s - esc), i   # closes with all its ticks

    def pair(near):
        out, i = [], 0
        while i < len(runs):
            j = nxt[i]
            if j is not None and near(runs[i][2], runs[j][2]):
                out.append((runs[i][0] + runs[i][3], runs[j][1]))
                i = j + 1
            else:
                i += 1          # no partner close enough: a literal backtick
        return out
    # Inline code is text that BOTH readings call code: Markdown's (a tick pairs anywhere
    # in its paragraph) and the strict one (its partner is on the same line or the next).
    # Either alone can shift later pairs and turn prose into code; together they cannot.
    spans += _intersect(pair(lambda a, b: pid[a] == pid[b]), pair(lambda a, b: b - a <= 1))
    return sorted(spans)


def _in_code(code, text, m_start, m_end):
    """True when the match, minus its surrounding whitespace, lies inside ONE code span.
    A match that only partly overlaps code is still (at least partly) prose, so it fires.
    `code` = (starts, ends) of the sorted, disjoint spans: a binary search finds the one
    span that could hold the match, so thousands of spans and claims stay fast."""
    starts, ends = code
    seg = text[m_start:m_end]
    s = m_start + (len(seg) - len(seg.lstrip()))
    e = m_end - (len(seg) - len(seg.rstrip()))
    i = bisect_right(starts, s) - 1
    return i >= 0 and e <= ends[i]


def _blockquote_checker(text):
    """is_quoted(m_start): does a ">" appear in the run of ">" / blanks that opens the
    match's line, before the match? Each line's run is scanned once and the line start
    is found by binary search, so many claims on one long line stay linear."""
    newlines = [m.start() for m in re.finditer("\n", text)]
    first_gt = {}             # line start -> index of the first ">" in its run, or -1

    def is_quoted(m_start):
        n = bisect_left(newlines, m_start)
        line_start = newlines[n - 1] + 1 if n else 0
        if line_start not in first_gt:
            i, gt = line_start, -1
            while i < len(text) and text[i] in "> \t":
                if gt < 0 and text[i] == ">":
                    gt = i
                i += 1
            first_gt[line_start] = gt
        return 0 <= first_gt[line_start] < m_start
    return is_quoted


def _ends_in_question(text, m_end):
    # Scan from the match end to the next sentence terminator. If that
    # terminator is "?", the claim sits inside a QUESTION ("Shipped the fix?",
    # "The tests are fixed now?") — not an assertion — so it must not fire.
    for i in range(m_end, min(len(text), m_end + 160)):
        c = text[i]
        if c in ".!?\n":
            return c == "?"
    return False


# A phrase that already ends with an assertion terminator ("Done.", "All set:") is a
# COMPLETED assertion — a question in the NEXT sentence ("Done. Anything else?") must
# not exempt it. The question guard is for the claim's own sentence only.
_ASSERTED_END = tuple(".!:;—-")


def _iter_claim_matches(text):
    """Yield (phrase, start, end) for every NON-exempt claim match, with all exemptions
    evaluated ONCE, at message level (the quoted-context exemption sees a ±80-char
    window around each match, so an exemption crossing that window edge can be missed —
    false-block side, narrow). Binding must reuse these spans rather than re-running the
    matcher on isolated fragments — a fence or quotation that spans a fragment boundary
    loses its markers in isolation, which re-litigates the exemption with less context
    and mints phantom claims out of pasted evidence."""
    spans = _code_spans(text)
    code = ([a for a, _b in spans], [b for _a, b in spans])
    is_quoted = _blockquote_checker(text)
    for pat in CLAIM_PATTERNS:
        for m in pat.finditer(text):
            phrase = m.group(0).strip()
            if not phrase.endswith(_ASSERTED_END) and _ends_in_question(text, m.end()):
                continue
            if _in_code(code, text, m.start(), m.end()):
                continue
            if is_quoted(m.start()):
                continue
            ctx_start = max(0, m.start() - 80)
            ctx = text[ctx_start:min(len(text), m.end() + 80)]
            c0, c1 = m.start() - ctx_start, m.end() - ctx_start
            # An exemption counts only if its match OVERLAPS the claim — a quotation
            # merely NEAR the claim ("As they say, \"done is done\".") must not silence it.
            if any(exm.start() < c1 and exm.end() > c0
                   for ex in EXEMPT_CONTEXT for exm in ex.finditer(ctx)):
                continue
            yield phrase, m.start(), m.end()


def find_claims(text):
    matches = []
    seen = set()
    for phrase, _s, _e in _iter_claim_matches(text):
        key = phrase.lower()
        if key in seen:
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


# Split on sentence punctuation FOLLOWED BY whitespace (or a newline). Requiring the
# whitespace keeps dotted identifiers intact: "migration.py" is NOT a sentence boundary.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n")


def _fragment_spans(text):
    """(start, end) spans of the sentence fragments _SENTENCE_SPLIT produces."""
    spans, start = [], 0
    for m in _SENTENCE_SPLIT.finditer(text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _claim_token_sets(message):
    """One token set PER claim OCCURRENCE, attributed to the sentence fragment(s) its
    match span touches (the matched phrase is usually just the closure verb; the
    subject — "migration" — lives in the sentence). Per-occurrence attribution means
    two same-phrased claims in DIFFERENT sentences ("The docs are complete. The
    migration is complete.") are bound separately — verifying one cannot launder the
    other. Splitting on punctuation + whitespace keeps dotted identifiers
    ("migration.py") whole.

    Exemptions were already evaluated ONCE on the full message in _iter_claim_matches —
    the spans arriving here are real claims. (Re-running the matcher per fragment would
    re-litigate exemptions with less context: a fence or quotation spanning a boundary
    loses its markers and pasted evidence becomes a phantom claim.) A match that spans a
    fragment boundary (a hard-wrapped "is\\ncomplete") binds the union of the fragments
    it touches, so each hard-wrapped claim keeps its own subject.

    A claim whose fragment has no content tokens (a bare "Done.") falls back to the
    whole message, so "I refactored the parser. Done." still binds on "parser". A truly
    subjectless message yields an empty set — see has_fresh_log for that (unbindable) case."""
    msg_tokens = _content_tokens(message)
    frags = _fragment_spans(message)
    frag_ends = [fe for _fs, fe in frags]
    sets = []
    for _phrase, s, e in _iter_claim_matches(message):
        # Fragments are sorted and disjoint: the ones the match touches run from the
        # first that ends after it starts (binary search, not a scan per claim).
        k, parts = bisect_right(frag_ends, s), []
        while k < len(frags) and frags[k][0] < e:
            parts.append(message[frags[k][0]:frags[k][1]])
            k += 1
        toks = _content_tokens(" ".join(parts))
        sets.append(toks or msg_tokens)
    if not sets:  # gate saw a claim but spans vanished (shouldn't happen) — fail closed-ish
        sets.append(msg_tokens)
    return sets


# ---------- FRESH LOG CHECK ----------
def has_fresh_log(claim_token_sets, session_id, window_min=CLAIM_CHECK_FRESH_MIN):
    """Match each claim separately against fresh, same-session new-format entries.

    Claims with content tokens require >= OVERLAP_MIN_TOKENS shared tokens.
    This blocks the recorded unrelated-work and cross-session reproductions;
    shared vocabulary and fabricated entries can still satisfy correspondence.

    Legacy entries are honored only when the log has no binding-schema entry.
    Each legacy entry expires with the freshness window; new legacy entries can
    continue to qualify until the log contains a binding-schema entry.

    Contentless claims (such as "Done.") require only a fresh same-session entry.
    Neither fallback establishes that verification occurred.
    """
    if not CLAIM_CHECKS_LOG.exists():
        return False
    try:
        lines = CLAIM_CHECKS_LOG.read_text().splitlines()
    except OSError:
        return False
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = now_ts - (window_min * 60)
    # Migration status is determined over the WHOLE file, independent of freshness or the
    # tail window: once the log has ANY binding-schema entry, legacy fail-open is off for
    # good (a stale new-schema entry + a fresh hand-written legacy line must NOT revert it).
    # Parse each line and test the actual KEY — a raw substring check would count the
    # literal text "claim_fingerprint" inside a claim/verification VALUE as migration,
    # wrongly denying a genuinely un-migrated log its fail-open.
    def _has_binding_key(ln):
        try:
            d = json.loads(ln)
            return isinstance(d, dict) and "claim_fingerprint" in d
        except (json.JSONDecodeError, ValueError):
            return False
    have_new_schema = any(_has_binding_key(ln) for ln in lines)
    fresh_entry_tokens = []   # same-session, new-schema, fresh entries
    legacy_fresh = False
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


# ---------- RECORDED-RUN CHECK (opt-in: CLAIM_CHECK_EVIDENCE=tool|both) ----------
# The log above is written by the agent whose claim is being gated. The transcript is
# written by the harness: every Bash call is an assistant "tool_use" block (id, name,
# input.command) and its outcome is a later user "tool_result" block (tool_use_id,
# is_error) on an entry stamped with sessionId + timestamp. Field shapes were read off
# real Claude Code transcripts, not assumed. In tool mode a done-claim needs a recorded,
# PASSING, allowlisted verification run from THIS session inside the freshness window.
#
# The transcript is still a file the agent's own Bash could append to. In @w-30x's words
# (issue #2) this raises the cost of a fake from "one log line" to "forging harness
# records": deliberate, done through a tool call the transcript records — though the
# forging code can hide inside a script that recorded call runs. It is not impossible.
EVIDENCE_MODES = ("log", "tool", "both")
SHELL_TOOLS = ("Bash",)  # only the Bash tool's record shape has been verified

# Command-line prefixes that count as verification, compared word-by-word. The first
# word is compared by basename (".venv/bin/pytest" == "pytest") and every "pythonX.Y"
# is folded to "python".
# Override with CLAIM_CHECK_VERIFY_COMMANDS="pytest,make test,npm test" (comma-separated).
VERIFY_COMMANDS = (
    "pytest", "python -m pytest", "python -m unittest", "tox", "nox",
    "npm test", "npm run test", "pnpm test", "yarn test",
    "cargo test", "go test", "make test", "make check", "ctest",
    "ruff check", "mypy", "flake8", "tsc", "eslint",
)

# A tool_result with is_error == False is NOT always a finished, passing run. Real
# transcripts show three ways a run reports success without having succeeded:
#   - backgroundTaskId (with or without run_in_background on the input): the call
#     returned at once ("Command running in background"), incl. auto-backgrounding
#   - timedOutAfterMs: a run that hit the tool timeout is "moved to the background"
#     with is_error false — a hung test suite looks green
#   - returnCodeInterpretation: a nonzero exit reinterpreted as non-error ("No matches found")
# Any of these markers means the exit status is not the command's own -> not credited.
_UNTRUSTED_RESULT_KEYS = ("backgroundTaskId", "timedOutAfterMs", "returnCodeInterpretation")

# ---- the creditable command-line grammar ----
# Instead of parsing arbitrary shell and hunting for dangerous separators (shlex and the
# shell disagree in too many places for that to converge), a command line is credited
# ONLY if it matches this small grammar exactly:
#
#   [set -o pipefail ; | &&]  [cd WORD &&]  [NAME=WORD ...]  VERIFY [ARG ...] [REDIR ...]
#       [| FILTER [ARG ...] ...]          # pipes only when the line starts with set -o pipefail
#       [&& WORD [WORD ...] [REDIR ...] ...]
#
#   WORD   = unquoted [A-Za-z0-9_-./:=,@%+~] and/or '...' (anything but ' and newlines)
#            (glob characters * ? [ count only inside quotes: unquoted, the shell may
#            expand them into a file named like a banned flag)
#            and/or "..." (no $ ` \ ! inside)
#   REDIR  = 2>&1 | > WORD | >> WORD | >| WORD | 2> WORD | 2>> WORD
#   FILTER = tail | head | grep | cat
#
# Every other character or operator — '#', '$', '(', ')', '{', '}', '<', '\', backtick,
# '!', '||', a lone '&', '|&', ';' anywhere but right after the leading set, a newline
# or carriage return, any non-ASCII byte outside quotes — makes the line uncreditable.
# Refusal only withholds credit from an honest run; it never grants credit.
_UNQUOTED_OK = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-./:=,@%+~*?[]")
_DQ_FORBIDDEN = frozenset('$`\\!')
_FILTERS = ("tail", "head", "grep", "cat")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")
_SET_OPT = re.compile(r"^-[eux]*o$")
# Leading VAR=value assignments that change the shell, the loader, or the verifier's own
# options (PYTEST_ADDOPTS=--co, MAKEFLAGS=-i, GOFLAGS=-run=X, npm_config_if_present=true).
_DENY_ASSIGN = frozenset((
    "PATH", "BASH_ENV", "ENV", "IFS", "ZDOTDIR", "SHELLOPTS", "BASHOPTS", "PS4",
    "PROMPT_COMMAND", "CDPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONSTARTUP",
    "NODE_OPTIONS", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES", "GOFLAGS", "SHELL",
))
_DENY_ASSIGN_PREFIX = ("LD_", "DYLD_", "NPM_CONFIG_", "TOX_", "NOX_", "RUFF_", "CARGO_",
                       "RUSTC_")

# Flags that make a verifier exit 0 without running, or mask its status. Checked on
# every word after the verify command (incl. words forwarded after "--").
_GENERIC_LONG = frozenset((
    "--version", "--help", "--dry-run", "--exit-zero", "--if-present", "--ignore-scripts",
    "--ignore-errors", "--no-run", "--list", "--collect-only", "--co",
    "--suppress-tests-failed-exit-code", "--suppress-no-test-exit-code",
    "--passWithNoTests", "--listTests", "--showConfig", "--watch",
))
_PYTEST_LONG = frozenset((
    "--version", "--help", "--collect-only", "--co", "--fixtures", "--fixtures-per-test",
    "--markers", "--setup-only", "--setup-plan", "--suppress-tests-failed-exit-code",
    "--suppress-no-test-exit-code", "--last-failed-no-failures", "--lfnf",
    "--override-ini", "--config-file",
    # pytest's own alias spellings of the above
    "--collectonly", "--setuponly", "--setupplan", "--funcargs", "--cache-show",
))
# tool -> (banned long flags, banned single-dash letters, argparse abbreviations?)
_TOOL_RULES = {
    "pytest": (_PYTEST_LONG, "hVoc", True),
    "unittest": (frozenset(("--help",)), "h", True),
    "tox": (_PYTEST_LONG | {"--notest", "--showconfig", "--listenvs", "--listenvs-all",
                            "--skip-missing-interpreters", "--help-ini", "--devenv",
                            "--skip-env"},
            "hlacn", True),
    "nox": (_PYTEST_LONG | {"--list", "--list-sessions", "--json"}, "hl", True),
    "npm": (_GENERIC_LONG | _PYTEST_LONG | {"--script-shell", "--usage"}, "h", False),
    "cargo": (frozenset(("--no-run", "--help", "--version", "--list")), "hV", False),
    "make": (frozenset(("--ignore-errors", "--dry-run", "--just-print", "--recon",
                        "--question", "--touch", "--print-data-base", "--help",
                        "--version", "--file", "--makefile",
                        # treat the target as up to date (recipe never runs, exit 0),
                        # or inject a recipe from the command line
                        "--old-file", "--assume-old", "--new-file", "--assume-new",
                        "--what-if", "--eval",
                        # let an allowed VAR=value in the environment replace a recipe variable
                        "--environment-overrides")), "inqtpvhfoWEe", True),
    "ctest": (frozenset(("--show-only", "--version", "--help", "--no-tests", "--print-labels",
                         "--help-full")), "Nh", False),
    "ruff": (frozenset(("--exit-zero", "--help", "--version", "--show-files",
                        "--show-settings", "--watch")), "ehV", False),
    "mypy": (frozenset(("--version", "--help")), "Vh", True),
    "flake8": (frozenset(("--exit-zero", "--version", "--help", "--bug-report")), "h", True),
    "tsc": (frozenset(("--version", "--help", "--init", "--showconfig", "--listfilesonly",
                       "--watch", "--all", "--nocheck", "--clean", "--dry")), "vhw", False),
    "eslint": (frozenset(("--version", "--help", "--print-config", "--init", "--env-info",
                          "--no-error-on-unmatched-pattern", "--stdin",
                          "--no-config-lookup")), "vh", False),
}
# go's flag package spells long flags with one dash; these compile, list or wrap
# without running the tests.
_GO_BANNED = frozenset(("-c", "-n", "-list", "-exec", "-toolexec", "-help", "-h"))
_TOX_SUBCOMMANDS = frozenset(("list", "l", "config", "c", "depends", "de", "quickstart", "q",
                              "devenv", "d", "exec", "e", "schema", "legacy", "le"))


def _evidence_mode():
    raw = os.environ.get("CLAIM_CHECK_EVIDENCE", "").strip().lower()
    if not raw:
        return "log"  # unset or blank: unchanged pre-v0.2 behavior
    # A set, non-blank but unrecognized value ("tools", "on") falls to the STRICTEST
    # mode rather than silently to the weakest — the resulting warnings surface the typo.
    return raw if raw in EVIDENCE_MODES else "both"


def _norm_cmd_token(tok):
    base = tok.rsplit("/", 1)[-1]
    return "python" if _PYTHON.match(base) else base


def _verify_prefixes():
    raw = os.environ.get("CLAIM_CHECK_VERIFY_COMMANDS")
    entries = raw.split(",") if raw is not None else VERIFY_COMMANDS
    out = []
    for e in entries:
        toks = e.split()
        if toks:
            out.append([_norm_cmd_token(toks[0])] + toks[1:])
    return out


def _lex(command):
    """Tokenize a command line into [("w", word) | ("op", op)], or None if it uses
    anything outside the grammar. Quotes are resolved the way the shell resolves them
    (adjacent quoted and unquoted parts form ONE word), so `echo a"; pytest -q b"` is
    the two words `echo` and `a; pytest -q b`."""
    if not isinstance(command, str):
        return None
    s = command.rstrip("\n").strip(" \t")
    if not s or "\n" in s or "\r" in s:
        return None
    out, cur, have, i, n = [], [], False, 0, len(s)

    def flush():
        nonlocal cur, have
        if have:
            out.append(("w", "".join(cur)))
        cur, have = [], False

    while i < n:
        c = s[i]
        if c in " \t":
            flush()
            i += 1
        elif c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                return None
            cur.append(s[i + 1:j])
            have, i = True, j + 1
        elif c == '"':
            j = s.find('"', i + 1)
            if j < 0 or any(ch in _DQ_FORBIDDEN for ch in s[i + 1:j]):
                return None
            cur.append(s[i + 1:j])
            have, i = True, j + 1
        elif c == "&":
            if s[i:i + 2] != "&&":
                return None  # lone & backgrounds; &> / &| / &! are not in the grammar
            flush()
            out.append(("op", "&&"))
            i += 2
        elif c == "|":
            if s[i + 1:i + 2] in ("|", "&"):
                return None  # || masks failure; |& is not in the grammar
            flush()
            out.append(("op", "|"))
            i += 1
        elif c == ";":
            if s[i + 1:i + 2] in (";", "&", "|"):
                return None
            flush()
            out.append(("op", ";"))
            i += 1
        elif c == ">":
            fd = ""
            if have:
                if "".join(cur) != "2" or s[i - 1] != "2":
                    return None  # only a bare unquoted "2" may prefix a redirect
                fd, cur, have = "2", [], False
            if s.startswith(">&1", i) and (i + 3 == n or s[i + 3] in " \t|"):
                if fd != "2":
                    return None
                out.append(("op", "2>&1"))
                i += 3
            elif s[i:i + 2] == ">>":
                out.append(("op", fd + ">>"))
                i += 2
            elif s[i:i + 2] == ">|":
                if fd:
                    return None
                out.append(("op", ">|"))
                i += 2
            elif s[i:i + 2] == ">&":
                return None
            else:
                out.append(("op", fd + ">"))
                i += 1
        elif c in _UNQUOTED_OK:
            if c in "*?[":
                # an unquoted glob is expanded by the shell before the verifier sees it;
                # a file named --collect-only turns `pytest --co*` or `pytest *` into a
                # no-run flag. Quote globs ('tests/test_*.py') to keep them creditable.
                return None
            cur.append(c)
            have = True
            i += 1
        else:
            return None  # # $ ( ) { } < \ ` ! non-ASCII control ...
    flush()
    return out


def _tool_key(prefix):
    if prefix[0] == "python" and len(prefix) >= 3:
        return prefix[2]
    if prefix[0] in ("npm", "pnpm", "yarn"):
        return "npm"
    return prefix[0]


def _flag_banned(word, long_banned, letters, abbrev, fold=False):
    if word.startswith("--") and len(word) > 2:
        name = word.split("=", 1)[0]
        if (name.lower() if fold else name) in long_banned:
            return True
        # argparse tools accept unambiguous prefixes: --coll == --collect-only
        return abbrev and len(name) >= 3 and any(b.startswith(name) for b in long_banned)
    m = re.match(r"-([A-Za-z]+)", word)
    if m and not word.startswith("--"):
        # the whole leading letter run, so an attached value (-oaddopts=--co) is checked
        return any(ch in letters for ch in m.group(1))
    return False


def _args_ok(prefix, args):
    key = _tool_key(prefix)
    if key == "go":
        return not any(a.split("=", 1)[0] in _GO_BANNED
                       or a.split("=", 1)[0][1:] in _GO_BANNED for a in args)
    long_banned, letters, abbrev = _TOOL_RULES.get(key, (_GENERIC_LONG, "h", False))
    for a in args:
        if _flag_banned(a, long_banned, letters, abbrev, fold=(key == "tsc")):  # tsc: case-insensitive
            return False
        if key == "tox" and a in _TOX_SUBCOMMANDS:
            return False
        if key == "make" and not a.startswith("-") and "=" in a:
            # make reads EVERY non-flag word with "=" as a variable assignment, in any
            # spelling (=, :=, ::=, +=, ?=, !=, .SHELLFLAGS=-nc): SHELL:=true skips recipes
            return False
    return True


def _simple(toks, k):
    """Consume words and redirections from toks[k:] up to the next | ; or &&.
    Returns (words, next_index) or None on a malformed redirection."""
    words = []
    while k < len(toks):
        kind, val = toks[k]
        if kind == "w":
            words.append(val)
            k += 1
        elif val == "2>&1":
            k += 1
        elif val in (">", ">>", ">|", "2>", "2>>"):
            if k + 1 >= len(toks) or toks[k + 1][0] != "w" or not words:
                return None
            k += 2
        else:
            break
    return words, k


def _is_verification_command(command, prefixes):
    """True only when `command` matches the creditable grammar (see above) — i.e. the
    exit status the harness records for the whole call IS the verification's status."""
    toks = _lex(command)
    if not toks:
        return False
    k, pipefail = 0, False
    w = [v for kind, v in toks[:3] if kind == "w"]
    if (len(toks) > 3 and len(w) == 3 and toks[0][0] == toks[1][0] == toks[2][0] == "w"
            and w[0] == "set" and _SET_OPT.match(w[1]) and w[2] == "pipefail"
            and toks[3] in (("op", ";"), ("op", "&&"))):
        pipefail, k = True, 4
    if (len(toks) > k + 2 and toks[k] == ("w", "cd") and toks[k + 1][0] == "w"
            and not toks[k + 1][1].startswith("-") and toks[k + 2] == ("op", "&&")):
        k += 3
    seg = _simple(toks, k)
    if seg is None:
        return False
    words, k = seg
    while words and _ASSIGNMENT.match(words[0]):
        name = words[0].split("=", 1)[0].upper()
        if name in _DENY_ASSIGN or name.startswith(_DENY_ASSIGN_PREFIX):
            return False
        words = words[1:]
    if not words:
        return False
    words = [_norm_cmd_token(words[0])] + words[1:]
    prefix = next((p for p in prefixes if words[:len(p)] == p), None)
    if prefix is None or not _args_ok(prefix, words[len(prefix):]):
        return False
    # Optional filters, only under pipefail (a pipe otherwise reports the LAST stage).
    while k < len(toks) and toks[k] == ("op", "|"):
        if not pipefail:
            return False
        seg = _simple(toks, k + 1)
        if seg is None or not seg[0] or seg[0][0] not in _FILTERS:
            return False
        words, k = seg
    # Optional && tails: they run only after everything before them succeeded, so a
    # zero status for the whole line still implies the verification passed.
    while k < len(toks) and toks[k] == ("op", "&&"):
        seg = _simple(toks, k + 1)
        if seg is None or not seg[0]:
            return False
        k = seg[1]
    return k == len(toks)


def _is_id(x):
    return isinstance(x, str) and bool(x)  # a missing/None/list id must never pair


def _scan_entry(entry, session_id, prefixes, uses, cutoff, now_ts):
    """One transcript entry. Records creditable tool_use ids into `uses`; returns True
    when a tool_result completes one of them as a fresh, finished success."""
    if not isinstance(entry, dict) or entry.get("isSidechain"):
        return False
    if entry.get("sessionId") != session_id:
        return False  # another session's run (e.g. resumed history) cannot alibi this one
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return False
    etype = entry.get("type")
    for b in content:
        if not isinstance(b, dict):
            continue
        if etype == "assistant" and b.get("type") == "tool_use":
            if b.get("name") not in SHELL_TOOLS:
                continue
            bid, inp = b.get("id"), b.get("input")
            if not _is_id(bid) or not isinstance(inp, dict):
                continue
            if inp.get("run_in_background"):
                continue
            if _is_verification_command(inp.get("command"), prefixes):
                uses[bid] = inp["command"]
        elif etype == "user" and b.get("type") == "tool_result":
            tid = b.get("tool_use_id")
            if not _is_id(tid) or tid not in uses:
                continue
            if b.get("is_error") is not False:
                continue  # nonzero exit, or no explicit success flag
            tur = entry.get("toolUseResult")
            if not isinstance(tur, dict) or tur.get("interrupted") is not False:
                continue
            if any(k in tur for k in _UNTRUSTED_RESULT_KEYS):
                continue
            ts_raw = entry.get("timestamp")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = _parse_ts_as_utc(ts_raw)
            except (ValueError, TypeError, AttributeError, OverflowError):
                continue
            if cutoff <= ts <= now_ts + 60:  # +60s clock-skew slack, no further
                return True
    return False


def has_recorded_run(transcript_path, session_id, window_min=CLAIM_CHECK_FRESH_MIN):
    """True if the harness-written transcript holds a PASSING allowlisted verification
    run from this session, finished inside the freshness window. Fails closed: anything
    missing, malformed, cross-session, sidechain, stale, future-dated, errored,
    interrupted, backgrounded, timed-out or reinterpreted is not credited."""
    if not session_id or not isinstance(session_id, str):
        return False
    p = Path(transcript_path).expanduser()
    try:
        lines = p.read_text(errors="ignore").splitlines()
    except OSError as e:
        _unreadable(e)
        return False
    prefixes = _verify_prefixes()
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = now_ts - (window_min * 60)
    uses = {}  # tool_use id -> command, for main-thread verification calls in this session
    for line in lines:
        try:
            if _scan_entry(json.loads(line), session_id, prefixes, uses, cutoff, now_ts):
                return True
        except Exception:  # one malformed line must not decide the gate either way
            continue
    return False


# ---------- MAIN ----------
def _unavailable(mode, what, soft=False):
    """Evidence cannot be examined (bad stdin, no transcript, unreadable transcript).
    Default (log) evidence keeps the old behavior and lets the turn through. In tool/both
    mode the operator asked for a stricter gate: missing evidence is not passing evidence,
    so this fails closed (warn in warn mode, block otherwise)."""
    evidence = _evidence_mode()
    if evidence == "log" or mode == "off":
        emit_ok()
    reason = (f"⚠️  Claim-check enforcer (CLAIM_CHECK_EVIDENCE={evidence}) could not examine "
              f"the session transcript: {what}. A recorded verification run cannot be "
              f"confirmed, so this fails closed.\nOverride: CLAIM_CHECK_ENFORCE_MODE=warn or =off")
    if mode == "warn" or soft:
        emit_warn(reason)  # soft: stop_hook_active is unreadable, so a block could loop
    emit_block(reason)


def main():
    mode = os.environ.get("CLAIM_CHECK_ENFORCE_MODE", DEFAULT_MODE).lower()
    if mode == "off":
        emit_ok()

    try:
        hook_data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        _unavailable(mode, "the hook input was not valid JSON", soft=True)

    if hook_data.get("stop_hook_active") is True:
        emit_ok()  # prevent re-block loop

    transcript_path = hook_data.get("transcript_path")
    if not transcript_path:
        _unavailable(mode, "the hook input named no transcript")

    if not Path(transcript_path).expanduser().exists():
        _unavailable(mode, f"the transcript does not exist: {transcript_path}")

    try:
        message = read_last_assistant_message(transcript_path)
    except TranscriptUnavailable as e:
        _unavailable(mode, f"the transcript could not be read: {e}")
    if not message:
        emit_ok()

    claims = find_claims(message)
    if not claims:
        emit_ok()

    # Prefer the session id the harness passes on stdin; fall back to the transcript
    # filename stem (verified to equal CLAUDE_CODE_SESSION_ID, which log_claim.py stamps).
    session_id = hook_data.get("session_id") or Path(transcript_path).stem
    evidence = _evidence_mode()
    log_ok = run_ok = True
    if evidence in ("log", "both"):
        log_ok = has_fresh_log(_claim_token_sets(message), session_id)
    if evidence in ("tool", "both"):
        try:
            run_ok = has_recorded_run(transcript_path, session_id)
        except TranscriptUnavailable as e:
            _unavailable(mode, f"the transcript could not be read: {e}")
    if log_ok and run_ok:
        emit_ok()

    bullets = "\n".join(f'  - "{p}"' for p in claims)
    if evidence != "log" and not run_ok:
        allow = ", ".join(" ".join(p) for p in _verify_prefixes()) or "(empty allowlist)"
        reason = (
            f"⚠️  Claim-check enforcer (CLAIM_CHECK_EVIDENCE={evidence}) — done-claim "
            f"detected, but the transcript holds no passing verification run from this "
            f"session in the last {CLAIM_CHECK_FRESH_MIN}min.\n"
            f"Matched phrases:\n{bullets}\n\n"
            f"A run counts only if the harness recorded it: a foreground Bash call that "
            f"finished with a non-error result, whose command line is an allowlisted "
            f"verification ({allow}) in the accepted shape: optionally "
            f"`set -o pipefail;` first, `cd DIR &&`, VAR=value; then the command and its "
            f"arguments; `| tail/head/grep/cat` only after pipefail; `&& more` after. "
            f"No `;`, `||`, `&`, `$`, `#`, newlines, or flags like --version/--co/--exit-zero.\n"
            f"A log_claim.py entry alone does not satisfy this mode.\n"
            f"Before stopping this turn, run the verification THIS claim rests on and "
            f"report its actual result, or narrow the claim to what you verified.\n"
            + ("A matching log_claim.py entry is also required (both mode).\n"
               if evidence == "both" and not log_ok else "")
            + f"\nOverride: CLAIM_CHECK_ENFORCE_MODE=warn or =off"
        )
        if mode == "warn":
            emit_warn(reason)
        emit_block(reason)

    reason = (
        f"⚠️  Claim-check enforcer — done-claim detected without a fresh verification "
        f"log entry satisfying the applicable correspondence or fallback rule.\n"
        f"Matched phrases:\n{bullets}\n\n"
        f"New-format entries must be within the {CLAIM_CHECK_FRESH_MIN}min freshness window "
        f"and match this session. Claims with subject tokens also require token overlap.\n"
        f"Subjectless claims require only same-session freshness. Legacy-only logs use "
        f"freshness alone until a binding-schema entry exists.\n"
        f"Before stopping this turn:\n"
        f"  1. Actually run the verification for THIS claim — the test, the end-to-end\n"
        f"     check, or a first-hand read of the artifact you're claiming about.\n"
        f"  2. Only if it genuinely passed, record the evidence for this specific claim.\n"
        f"These checks do not establish that verification occurred.\n\n"
        f"Override: CLAIM_CHECK_ENFORCE_MODE=warn or =off"
    )

    if mode == "warn":
        emit_warn(reason)
    emit_block(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Default (log) evidence: an unexpected error must NEVER break the operator's turn.
        # In tool/both mode the operator asked for a stricter gate, so an error FAILS
        # CLOSED instead (warn in warn mode, block in block mode) — a crash must not be a
        # way to clear a done-claim.
        _ev = _evidence_mode()
        _md = os.environ.get("CLAIM_CHECK_ENFORCE_MODE", DEFAULT_MODE).lower()
        if _ev == "log" or _md == "off":
            emit_ok()
        _reason = (f"⚠️  Claim-check enforcer (CLAIM_CHECK_EVIDENCE={_ev}) hit an internal "
                   f"error and could not confirm a recorded verification run; failing "
                   f"closed.\nOverride: CLAIM_CHECK_ENFORCE_MODE=warn or =off")
        if _md == "warn":
            emit_warn(_reason)
        emit_block(_reason)
