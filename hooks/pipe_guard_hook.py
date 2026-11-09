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
  - followed by `||`, directly or later in the same `&&`/`||` chain (`pytest || true`,
    `pytest && echo ok || true`). The line's status is then the handler's, not the test's.

Everything else is allowed, including `set -o pipefail; pytest | tail -3`, a bare run,
`pytest > out.txt`, `pytest; echo $?` and `pytest --help | head`. Commands it cannot
tokenize are allowed: the Stop hook still withholds credit from them.

What it does NOT see (it allows these): a verification inside `bash -c "..."`, `$(...)`,
a script, a Makefile target or an alias; a group piped as a whole (`{ pytest; } | tail`,
`(pytest) | tail`); a `set -o pipefail` that runs in a subshell or may be skipped
(`(set -o pipefail); ...`, `false && set -o pipefail; ...`); a wrapper option it does not
know that takes a value (`uv run --some-new-flag X pytest`). Heredoc bodies are skipped as
data, including an unquoted one whose `$(...)` the shell would run. A deny costs one rerun.

Config:
  ANDON_PIPE_GUARD=on       enable (default: off). `1`, `true` and `yes` also enable it,
                            in any case; any other value, or none, leaves it off.
  CLAIM_CHECK_VERIFY_COMMANDS="pytest,make test"   overrides the command list

Input: the PreToolUse JSON on stdin. Output: a deny decision on stdout, or nothing.
Never fails closed: any error allows the call.
"""
from __future__ import annotations

import json
import os
import re
import shlex
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
# Wrappers that run a later word as the command, with the options that take a separate
# value (`timeout -s TERM 300 pytest`, `env -u PYTHONPATH pytest`). The command is the
# first word after the wrapper's options (and env's VAR=value, timeout's duration).
_WRAP_VALUE_OPTS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "time": set(), "nohup": set(), "command": set(),
    "nice": {"-n", "--adjustment"},
    "exec": {"-a"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "gtimeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "caffeinate": {"-t", "-w"},
}
_WRAP_RUN = frozenset(("uv", "poetry", "pipenv", "hatch", "pdm", "rye"))  # `X run ...`
_RUN_VALUE_OPTS = frozenset((
    "--with", "--with-editable", "--with-requirements", "--python", "-p", "--project",
    "--directory", "--extra", "--group", "--only-group", "--package", "--env-file",
    "--index", "--default-index", "--index-url", "-i", "--extra-index-url", "-C", "-P",
    "-w", "-e", "--env"))
_INFO_ARGS = frozenset(("--help", "-h", "--version", "-V"))  # prints, runs no tests
_PLAIN_WORD = re.compile(r"[A-Za-z0-9_@%+=:,./~-]+")


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
            if not (op == "\n" and out and out[-1][0] == "op"
                    and out[-1][1] in ("|", "|&", "&&", "||")):
                out.append(("op", op))  # a newline right after | && || continues the line
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


def _skip_opts(words, value_opts):
    """Drop leading options (and the value of each option in value_opts) up to `--`."""
    while words and words[0].startswith("-") and words[0] != "-":
        opt, words = words[0], words[1:]
        if opt == "--":
            break
        if opt in value_opts:
            words = words[1:]
    return words


def _unwrap(words, norm):
    """The words of the command a wrapper chain runs, or None for `command -v X`."""
    while words:
        head = norm(words[0])
        if head == "command" and any(w in ("-v", "-V") for w in words[1:3]):
            return None  # a lookup, not a run
        if head in _WRAP_VALUE_OPTS:
            words = _skip_opts(words[1:], _WRAP_VALUE_OPTS[head])
            if head == "env":
                while words and _ASSIGNMENT.match(words[0]):
                    words = words[1:]
            elif head in ("timeout", "gtimeout"):
                words = words[1:]  # the duration
        elif head in _WRAP_RUN and words[1:2] == ["run"]:
            words = _skip_opts(words[2:], _RUN_VALUE_OPTS)
        else:
            return words
    return words


def _quote(word):
    return word if _PLAIN_WORD.fullmatch(word) else shlex.quote(word)


def _find_verify(words, prefixes, norm):
    """The verification command these words run, as a shell-quoted string, or None."""
    words = _unwrap(words, norm) if words else None
    if not words:
        return None
    w = [norm(words[0])] + words[1:]
    p = next((p for p in prefixes if w[:len(p)] == p), None)
    if p is None or any(a in _INFO_ARGS for a in w[len(p):]):
        return None
    return " ".join(_quote(x) for x in words)


def _pipefail_setting(words):
    """True for `set -o pipefail` (or -euo), False for `set +o pipefail`, else None.
    Stops at `--`, `-` or the first positional word: `set -- -o pipefail` sets $1..$3."""
    if not words or words[0] != "set":
        return None
    pending = None  # the sign of a just-seen -o / +o, waiting for its option name
    for w in words[1:]:
        if pending is not None:
            if w == "pipefail":
                return pending
            pending = None
            continue
        if w in ("--", "-") or w[:1] not in "-+":
            return None
        if len(w) > 1 and w.endswith("o"):
            pending = w[0] == "-"
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
    pipefail, before, chain = False, None, None  # chain: a test run earlier in this && || list
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
            if after not in ("&&", "||"):
                chain = None
            continue
        before = after
        found = [(k, _find_verify(w, prefixes, _norm_cmd_token)) for k, w in enumerate(words)]
        chain = next((c for _, c in found if c), None) or chain
        if after == "||" and chain:
            return (f"Andon pipe guard: `{chain}` is followed by `||` in the same command "
                    f"list, so the line's exit status is the handler's, not the test "
                    f"run's (`|| true` turns a failure into a pass). Run `{chain}` on its "
                    f"own and read its exit status.")
        if after not in ("&&", "||"):
            chain = None
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
    return None


def main():
    if os.environ.get("ANDON_PIPE_GUARD", "").strip().lower() not in ("on", "1", "true", "yes"):
        return 0  # off by default: it denies often (1 in 24 Bash calls in the author's history)
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
