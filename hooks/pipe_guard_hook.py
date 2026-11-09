#!/usr/bin/env python3
"""
pipe_guard_hook.py — PreToolUse hook: stop a test run whose exit status would lie.

The claim-check Stop hook (tool/both mode) already refuses to CREDIT a run like
`pytest -q | tail -3`: without `pipefail`, the shell reports tail's exit status, so a
failing suite looks green. But that refusal arrives at turn end, after the agent has read
the wrong status and moved on. This hook catches the same line BEFORE it runs, says why,
and gives the fix, so the agent reruns it in a form the harness records honestly.

It denies a Bash command when an allowlisted verification command (the same list as
claim-check: pytest, npm test, cargo test, ... or CLAIM_CHECK_VERIFY_COMMANDS) is
  - piped into anything (`|` or `|&`) with no `set -o pipefail` earlier in the command, or
  - followed by `||` (`pytest || true`, `npm test || echo failed`).

Everything else is allowed, including `set -o pipefail; pytest | tail -3`, a bare run,
`pytest > out.txt`, and `pytest; echo $?`. Commands it cannot tokenize are allowed: the
Stop hook still withholds credit from them.

What it does NOT see: a verification inside `bash -c "..."`, a script, a Makefile target
or an alias, or a test run inside a heredoc fed to a shell (`bash <<EOF`): heredoc bodies
are skipped as data. A deny costs one rerun.

Config:
  ANDON_PIPE_GUARD=off      disable (default: on)
  CLAIM_CHECK_VERIFY_COMMANDS="pytest,make test"   overrides the command list

Input: the PreToolUse JSON on stdin. Output: a deny decision on stdout, or nothing.
Never fails closed: any error allows the call.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_OPS = ("<<<", "<<-", "||", "|&", "&&", ";;", ">>", ">&", "&>", "<<", ">|", "<&",
        "|", "&", ";", "\n", "(", ")", "<", ">")
_LIST_OPS = ("||", "&&", ";;", "&", ";", "\n")
_PIPE_OPS = ("|", "|&")
_GROUPING = ("(", ")")
_KEYWORDS = frozenset(("if", "then", "elif", "else", "while", "until", "do", "!", "{", "}",
                       "fi", "done"))
# Wrappers that run a later word as the command (`timeout -s TERM 300 pytest`,
# `uv run --no-sync pytest`). After one, the verify list is matched at each of the next
# _WRAP_SCAN words rather than parsing every wrapper's own flags.
_WRAP_1 = frozenset(("env", "time", "command", "nice", "nohup", "exec", "timeout",
                     "gtimeout", "stdbuf", "caffeinate"))
_WRAP_RUN = frozenset(("uv", "poetry", "pipenv", "hatch", "pdm", "rye"))  # `X run ...`
_WRAP_SCAN = 8


def _tokens(s):
    """Split a command line into [("w", word) | ("op", op)], the way bash reads it for
    this purpose: quotes make one word (so a quoted `|` is not a pipe), a `#` at the start
    of a word comments to end of line (the newline stays), backslash-newline joins lines,
    and a heredoc body (`<<EOF` ... `EOF`) is skipped: it is data, not commands.
    Raises ValueError on an unclosed quote."""
    out, cur, have, i, n = [], [], False, 0, len(s)
    want_delim, heredocs = None, []  # (delimiter, strip leading tabs) for each pending body

    def flush():
        nonlocal cur, have, want_delim
        if have:
            out.append(("w", "".join(cur)))
            if want_delim is not None:
                heredocs.append(("".join(cur), want_delim))
                want_delim = None
        cur, have = [], False

    while i < n:
        c = s[i]
        if c == "\\":
            if s[i + 1:i + 2] == "\n":
                i += 2  # line continuation
                continue
            cur.append(s[i + 1:i + 2])
            have, i = True, i + 2
        elif c in " \t\r":
            flush()
            i += 1
        elif c == "#" and not have:
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise ValueError("unclosed '")
            cur.append(s[i + 1:j])
            have, i = True, j + 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(s[j])
                j += 1
            if j >= n:
                raise ValueError('unclosed "')
            cur.append("".join(buf))
            have, i = True, j + 1
        elif c in "|&;<>()\n":
            flush()
            op = next(o for o in _OPS if s.startswith(o, i))
            out.append(("op", op))
            i += len(op)
            if op in ("<<", "<<-"):
                want_delim = op == "<<-"
            elif op == "\n" and heredocs:
                for delim, strip in heredocs:  # skip each body up to its delimiter line
                    while i < n:
                        j = s.find("\n", i)
                        line = s[i:] if j < 0 else s[i:j]
                        i = n if j < 0 else j + 1
                        if (line.lstrip("\t") if strip else line) == delim:
                            break
                heredocs.clear()
        else:
            cur.append(c)
            have, i = True, i + 1
    flush()
    return out


def _words(stage):
    """The command words of one pipeline stage: drop redirections (and their target),
    the fd number of `2>`, grouping, leading shell keywords and VAR=value."""
    words, k = [], 0
    while k < len(stage):
        kind, val = stage[k]
        if kind == "op":
            k += 1 if val in _GROUPING else 2  # a redirection and its target
            continue
        nxt = stage[k + 1] if k + 1 < len(stage) else None
        if val.isdigit() and nxt and nxt[0] == "op" and nxt[1][0] in "<>":
            k += 1
            continue
        words.append(val)
        k += 1
    while words and (words[0] in _KEYWORDS or _ASSIGNMENT.match(words[0])):
        words = words[1:]
    return words


def _find_verify(words, prefixes, norm):
    """The verification command these words run, as a string, or None."""
    if not words:
        return None
    head = norm(words[0])
    if head == "command" and any(w in ("-v", "-V") for w in words[1:3]):
        return None  # a lookup, not a run
    if head in _WRAP_1 or (head in _WRAP_RUN and words[1:2] == ["run"]):
        starts = range(1, min(len(words), 1 + _WRAP_SCAN))
    else:
        starts = (0,)
    for s0 in starts:
        w = [norm(words[s0])] + words[s0 + 1:]
        if any(w[:len(p)] == p for p in prefixes):
            return " ".join(words[s0:])
    return None


def _pipefail_setting(words):
    """True for `set -o pipefail` (or -euo), False for `set +o pipefail`, else None."""
    if not words or words[0] != "set":
        return None
    for i, w in enumerate(words[2:], 2):
        flag = words[i - 1]
        if w == "pipefail" and len(flag) > 1 and flag[0] in "-+" and flag.endswith("o"):
            return flag[0] == "-"
    return None


def check(command, prefixes=None):
    """Return a deny reason (str) for `command`, or None to allow."""
    if not isinstance(command, str) or not command.strip():
        return None
    sys.path.insert(0, str(_HERE))
    from claim_check_hook import _norm_cmd_token, _verify_prefixes  # lazy: see main()
    prefixes = _verify_prefixes() if prefixes is None else prefixes
    try:
        toks = _tokens(command)
    except ValueError:
        return None  # unclosed quote: let the shell report it
    pipefail, before = False, None
    # Walk lists (split on ; && || & newline), each a pipeline of stages split on |.
    i, n = 0, len(toks)
    while i < n:
        stages, cur = [], []
        while i < n and toks[i] not in (("op", o) for o in _LIST_OPS):
            if toks[i][0] == "op" and toks[i][1] in _PIPE_OPS:
                stages.append(cur)
                cur = []
            else:
                cur.append(toks[i])
            i += 1
        stages.append(cur)
        after = toks[i][1] if i < n else None
        i += 1
        words = [_words(st) for st in stages]
        if not any(words):
            continue  # blank line or bare grouping: keep `before` as it was
        setting = _pipefail_setting(words[0]) if len(words) == 1 else None
        if setting is not None:
            if before in (None, ";", "\n", "&&", ";;"):
                pipefail = setting  # a `set` after || may never run
            before = after
            continue
        before = after
        found = [(k, _find_verify(w, prefixes, _norm_cmd_token)) for k, w in enumerate(words)]
        for k, cmd in found:
            if cmd is None:
                continue
            if k < len(words) - 1 and not pipefail:
                into = " ".join(words[k + 1][:1]) or "another command"
                return (f"Andon pipe guard: `{cmd}` is piped into `{into}` without "
                        f"pipefail, so the shell reports `{into}`'s exit status, not the "
                        f"test run's. A failing run would look like a pass. Rerun it "
                        f"unpiped, or put `set -o pipefail;` at the start of the command "
                        f"(e.g. `set -o pipefail; {cmd} 2>&1 | tail -30`).")
            if after == "||":
                return (f"Andon pipe guard: `{cmd} ... || ...` turns a failing run into "
                        f"exit 0, so a failure would look like a pass. Run `{cmd}` on "
                        f"its own and read its exit status.")
    return None


def main():
    if os.environ.get("ANDON_PIPE_GUARD", "").strip().lower() in ("off", "0", "false", "no"):
        return 0
    try:  # claim_check_hook is imported inside check(), so its import errors land here
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Bash":
            return 0
        reason = check((data.get("tool_input") or {}).get("command"))
    except Exception:
        return 0  # never fail closed
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
