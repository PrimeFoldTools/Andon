#!/usr/bin/env python3
"""Regression tests for transcript framing and matcher cost in claim_check_hook (2026-10-28).

1. Framing. JSONL records are split on "\\n" only. str.splitlines() also splits on
   U+2028, U+2029 and U+0085, which JSON allows raw inside a string (Claude Code writes
   U+2028 unescaped). A final message holding one of them was cut into pieces that did
   not parse, and the hook read an older message instead.
2. Sentence-start regex cost. The first three claim patterns used to rescan a whole run
   of blank lines from every line start in it (quadratic). The new anchor is linear and
   gives the same match spans; a seeded differential test pins that.
3. Token-set cost. Each sentence fragment is tokenised once, not once per claim in it.

The framing tests run the hook as a subprocess (the production entry point). The cost
tests run the work in a child process with a hard timeout, so a regression fails in
seconds instead of hanging the suite. The time limits are generous; the old code took
minutes on the same inputs.
"""
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent
HOOK = str(HOOKS_DIR / "claim_check_hook.py")
WRITER = str(HOOKS_DIR / "log_claim.py")
sys.path.insert(0, str(HOOKS_DIR))
import claim_check_hook as cc  # noqa: E402

SID = "22222222-3333-4444-5555-666666666666"
CLAIM = "The migration is complete."
SEPARATORS = [" ", " ", "\x85"]


def _now(delta_min=0.0):
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_min)).isoformat()


def _assistant(text, delta_min=0.0):
    return {"type": "assistant", "sessionId": SID, "timestamp": _now(delta_min),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _write_raw(tmp_path, entries, tail=""):
    """Write JSONL the way Claude Code does: non-ASCII characters stay raw."""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries) + tail,
                 encoding="utf-8")
    return p


def _env(tmp_path, evidence=None, mode="block"):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAIM_CHECK") and k != "CLAUDE_CODE_SESSION_ID"}
    env["CLAIM_CHECKS_LOG_PATH"] = str(tmp_path / "log.jsonl")
    env["CLAIM_CHECK_ENFORCE_MODE"] = mode
    if evidence is not None:
        env["CLAIM_CHECK_EVIDENCE"] = evidence
    return env


def _run(tmp_path, transcript, evidence=None, mode="block"):
    stdin = {"transcript_path": str(transcript), "hook_event_name": "Stop", "session_id": SID}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(stdin), capture_output=True,
                       text=True, env=_env(tmp_path, evidence, mode))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().split("\n")[-1])


def _log(tmp_path, claim="migration complete", verification="ran the migration tests"):
    env = _env(tmp_path)
    env["CLAUDE_CODE_SESSION_ID"] = SID
    subprocess.run([sys.executable, WRITER, claim, verification], capture_output=True,
                   text=True, env=env, check=True)


def _blocked(out):
    return out.get("decision") == "block"


def _passed(out):
    return out.get("continue") is True and "decision" not in out and "systemMessage" not in out


def _passing_run(output="12 passed in 0.31s"):
    use = {"type": "assistant", "sessionId": SID, "timestamp": _now(-0.5), "isSidechain": False,
           "message": {"role": "assistant", "content": [
               {"type": "tool_use", "id": "toolu_01", "name": "Bash",
                "input": {"command": "python3 -m pytest hooks/tests/ -q",
                          "description": "run tests"}}]}}
    res = {"type": "user", "sessionId": SID, "timestamp": _now(-0.4), "isSidechain": False,
           "toolUseResult": {"stdout": output, "stderr": "", "interrupted": False,
                             "isImage": False, "noOutputExpected": False},
           "message": {"role": "user", "content": [
               {"type": "tool_result", "tool_use_id": "toolu_01", "content": output,
                "is_error": False}]}}
    return [use, res]


# ---- 1. framing: a raw line separator inside the final message ----
@pytest.mark.parametrize("sep", SEPARATORS, ids=["U+2028", "U+2029", "U+0085"])
def test_separator_in_final_message_still_blocks(tmp_path, sep):
    t = _write_raw(tmp_path, [_assistant("Looking at the schema now.", -0.2),
                              _assistant(CLAIM + sep)])
    raw = t.read_text(encoding="utf-8")
    assert sep in raw                                    # written raw, not escaped
    assert len(raw.splitlines()) > raw.count("\n")      # the old framing would split it
    out = _run(tmp_path, t)
    assert _blocked(out), out


@pytest.mark.parametrize("sep", SEPARATORS, ids=["U+2028", "U+2029", "U+0085"])
def test_separator_mid_message_still_blocks(tmp_path, sep):
    t = _write_raw(tmp_path, [_assistant("Looking at the schema now.", -0.2),
                              _assistant("I updated the schema." + sep + CLAIM)])
    assert _blocked(_run(tmp_path, t))


def test_separator_with_matching_log_passes(tmp_path):
    _log(tmp_path)
    t = _write_raw(tmp_path, [_assistant(CLAIM + " ")])
    assert _passed(_run(tmp_path, t))


def test_separator_without_claim_passes(tmp_path):
    t = _write_raw(tmp_path, [_assistant("Here is the plan. Step one: read the schema.")])
    assert _passed(_run(tmp_path, t))


def test_tool_mode_credits_run_whose_output_has_separator(tmp_path):
    # has_recorded_run frames the same transcript: a passing run whose recorded output
    # holds U+2028 must still be credited (the old framing dropped it and blocked).
    t = _write_raw(tmp_path, [*_passing_run("12 passed in 0.31s"), _assistant(CLAIM)])
    assert _passed(_run(tmp_path, t, evidence="tool"))


def test_tool_mode_unparseable_newest_record_fails_closed(tmp_path):
    # The newest record does not parse, so the final message may be in it. Tool mode must
    # not fall back to an older message: it reports the transcript as unavailable.
    t = _write_raw(tmp_path, [*_passing_run(), _assistant(CLAIM)],
                   tail='{"type": "assistant", "message": {"content": [\n')
    out = _run(tmp_path, t, evidence="tool")
    assert _blocked(out) and "could not examine" in out["reason"], out


def test_log_mode_unparseable_newest_record_keeps_old_behaviour(tmp_path):
    # Log mode (the default) keeps skipping an unparseable record: the claim in the
    # previous record is still read and still gated.
    t = _write_raw(tmp_path, [_assistant(CLAIM)], tail='{"type": "assistant", "mess\n')
    assert _blocked(_run(tmp_path, t))


def test_log_entry_with_raw_separator_is_read(tmp_path):
    # The verification log is JSONL too. log_claim.py escapes U+2028, but a line that
    # holds it raw is still one valid record and must count.
    _log(tmp_path)
    logp = tmp_path / "log.jsonl"
    entry = json.loads(logp.read_text().strip())
    entry["verification"] += " (see output)"
    logp.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    t = _write_raw(tmp_path, [_assistant(CLAIM)])
    assert _passed(_run(tmp_path, t))


# ---- 2 + 3. cost: run in a child process with a hard timeout ----
def _timed(expr):
    """Seconds the hook module takes to evaluate `expr` (bound to `r`), plus repr(r)."""
    code = (
        "import sys, time; sys.path.insert(0, %r)\n"
        "import claim_check_hook as cc\n"
        "t = time.perf_counter(); r = %s; t = time.perf_counter() - t\n"
        "print(t); print(repr(r))\n" % (str(HOOKS_DIR), expr)
    )
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{expr} took over 30 s")
    assert p.returncode == 0, p.stderr
    secs, result = p.stdout.split("\n", 1)
    return float(secs), result.strip()


def test_long_blank_line_run_is_fast():
    secs, result = _timed("cc.find_claims('\\n' * 50000)")
    assert result == "[]"
    assert secs < 2.0, secs


def test_claim_after_long_blank_line_run_still_found_fast():
    secs, result = _timed("cc.find_claims('\\n' * 50000 + 'Done.')")
    assert result == "['Done.']"
    assert secs < 2.0, secs


def test_many_claims_in_one_fragment_tokenise_fast():
    secs, result = _timed("len(cc._claim_token_sets('x is done ' * 20000))")
    assert result == "20000"
    assert secs < 2.0, secs


def test_token_sets_match_joined_tokenisation():
    # Per-fragment sets, unioned, must equal tokenising the joined fragments (old code).
    msg = ("I migrated the users table. The migration is\ncomplete. The docs are complete. "
           "Shipped the parser.py fix!\n\nDone.")
    frags = cc._fragment_spans(msg)
    ends = [e for _s, e in frags]
    want = []
    for _p, s, e in cc._iter_claim_matches(msg):
        k, parts = cc.bisect_right(ends, s), []
        while k < len(frags) and frags[k][0] < e:
            parts.append(msg[frags[k][0]:frags[k][1]])
            k += 1
        want.append(cc._content_tokens(" ".join(parts)) or cc._content_tokens(msg))
    got = cc._claim_token_sets(msg)
    assert len(got) == len(want) >= 4
    assert [set(g) for g in got] == want


# ---- 2. the new sentence-start anchor gives the old spans ----
_OLD_PREFIX = r"(?:^|(?<=[.!?])\s+|\n)\s*"


def test_sentence_start_anchor_keeps_old_spans():
    """Seeded differential check: patterns 0-2 rebuilt with the old prefix vs the new
    anchor plus _sentence_start(). Inputs are short, so the old prefix stays fast."""
    old = [re.compile(p.pattern.replace(cc._LINE_START, _OLD_PREFIX), p.flags)
           for p in cc.CLAIM_PATTERNS[:cc._SENTENCE_START_PATTERNS]]
    assert all(o.pattern != n.pattern for o, n in zip(old, cc.CLAIM_PATTERNS))
    atoms = ["\n", "\n", " ", "\t", "\r\n", ".", "!", "?", " ", "\x0b", "Done",
             "done.", "Task done", "the fix complete", "Fixed the parser", "I've fixed the",
             "we just implemented this", "all set", "x", "> ", ":", "-"]
    rng = random.Random(20261028)
    checked = hits = 0
    for _ in range(4000):
        t = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 20)))
        for o, n in zip(old, cc.CLAIM_PATTERNS):
            want = [(m.start(), m.end()) for m in o.finditer(t)]
            got, floor = [], 0
            for m in n.finditer(t):
                got.append((cc._sentence_start(t, m, floor), m.end()))
                floor = m.end()
            assert got == want, (repr(t), want, got)
            checked += 1
            hits += bool(want)
    assert hits > 1000    # non-vacuous: many inputs do match
