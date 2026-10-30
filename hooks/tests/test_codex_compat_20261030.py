#!/usr/bin/env python3
"""Codex CLI compatibility for the two plugin hooks (2026-10-30).

Codex runs the same hooks/hooks.json. Its hook input was captured from a real
`codex exec` run (codex-cli 0.153.4) and matches Claude Code's field names:
session_id, transcript_path, cwd, hook_event_name, plus last_assistant_message and
stop_hook_active on Stop. Two differences matter:

1. log_claim.py read the session only from CLAUDE_CODE_SESSION_ID. Codex sets
   CODEX_SESSION_ID / CODEX_THREAD_ID in the agent's shell instead, so under Codex every
   log entry was session-less and could never clear a claim.
2. Codex documents transcript_path as "string or null". With a null path the hook used
   to skip the check even though last_assistant_message held the final message.

Each test feeds both a Codex-shaped and a Claude-shaped payload where the case applies,
so Claude Code behaviour is pinned alongside the new Codex behaviour.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
HOOK = str(HERE / "claim_check_hook.py")
WRITER = str(HERE / "log_claim.py")
ORIENT = str(HERE / "auto_orient.py")

SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID")
CLAIM = "The parser fix is done and all tests pass."


def _env(extra=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAIM_CHECK") and not k.startswith("AUTO_ORIENT")
           and k not in SESSION_VARS}
    env["CLAIM_CHECK_ENFORCE_MODE"] = "warn"  # what hooks/hooks.json sets
    if extra:
        env.update(extra)
    return env


def _run(script, payload, env_extra=None):
    r = subprocess.run([sys.executable, script], input=json.dumps(payload),
                       capture_output=True, text=True, env=_env(env_extra))
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip()
    return json.loads(out.splitlines()[-1]) if out else None


def _codex_transcript(tmp_path, sid):
    """A Codex rollout file: its records are session_meta / response_item / event_msg,
    not Claude Code's type=assistant records."""
    p = tmp_path / f"rollout-2026-10-30T16-29-20-{sid}.jsonl"
    ts = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps({"timestamp": ts, "type": "session_meta",
                             "payload": {"session_id": sid}}) + "\n")
    return str(p)


def _claude_transcript(tmp_path, sid, text):
    p = tmp_path / f"{sid}.jsonl"
    p.write_text(json.dumps({"type": "assistant",
                             "timestamp": datetime.now(timezone.utc).isoformat(),
                             "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
    return str(p)


def _codex_stop(tmp_path, sid, text, transcript=True):
    return {"session_id": sid, "turn_id": "t-1",
            "transcript_path": _codex_transcript(tmp_path, sid) if transcript else None,
            "cwd": str(tmp_path), "hook_event_name": "Stop", "model": "gpt-6-astra",
            "permission_mode": "bypassPermissions", "stop_hook_active": False,
            "last_assistant_message": text}


def _claude_stop(tmp_path, sid, text):
    return {"session_id": sid, "transcript_path": _claude_transcript(tmp_path, sid, text),
            "cwd": str(tmp_path), "hook_event_name": "Stop", "stop_hook_active": False,
            "last_assistant_message": text}


def _log(logp, env_session, claim=CLAIM, verification="ran pytest on the parser"):
    env = _env({"CLAIM_CHECKS_LOG_PATH": logp, **env_session})
    subprocess.run([sys.executable, WRITER, claim, verification],
                   capture_output=True, text=True, env=env, check=True)
    return json.loads(Path(logp).read_text().strip().splitlines()[-1])


def _warned(out):
    return out["continue"] is True and "Claim-check enforcer" in out.get("systemMessage", "")


def _passed(out):
    return out == {"continue": True, "suppressOutput": True}


# ---- log_claim.py: which session id is stamped ----
@pytest.mark.parametrize("env_session, expected", [
    ({"CLAUDE_CODE_SESSION_ID": "cc-1"}, "cc-1"),
    ({"CODEX_SESSION_ID": "cx-1", "CODEX_THREAD_ID": "cx-1"}, "cx-1"),
    ({"CODEX_THREAD_ID": "cx-2"}, "cx-2"),
    ({"CLAUDE_CODE_SESSION_ID": "cc-1", "CODEX_SESSION_ID": "cx-1"}, "cc-1"),  # Claude wins
    ({}, ""),
])
def test_log_claim_session_id_source(tmp_path, env_session, expected):
    entry = _log(str(tmp_path / "log.jsonl"), env_session)
    assert entry["session_id"] == expected


# ---- Stop hook: same verdicts for both payload shapes ----
@pytest.mark.parametrize("shape", ["codex", "claude"])
def test_unlogged_claim_warns(tmp_path, shape):
    mk = _codex_stop if shape == "codex" else _claude_stop
    out = _run(HOOK, mk(tmp_path, "s-1", CLAIM),
               {"CLAIM_CHECKS_LOG_PATH": str(tmp_path / "log.jsonl")})
    assert _warned(out)


@pytest.mark.parametrize("shape, env_session", [
    ("codex", {"CODEX_SESSION_ID": "s-1", "CODEX_THREAD_ID": "s-1"}),
    ("claude", {"CLAUDE_CODE_SESSION_ID": "s-1"}),
])
def test_same_session_log_clears_claim(tmp_path, shape, env_session):
    logp = str(tmp_path / "log.jsonl")
    _log(logp, env_session)
    mk = _codex_stop if shape == "codex" else _claude_stop
    out = _run(HOOK, mk(tmp_path, "s-1", CLAIM), {"CLAIM_CHECKS_LOG_PATH": logp})
    assert _passed(out)


def test_codex_other_session_log_does_not_clear(tmp_path):
    logp = str(tmp_path / "log.jsonl")
    _log(logp, {"CODEX_SESSION_ID": "other"})
    out = _run(HOOK, _codex_stop(tmp_path, "s-1", CLAIM), {"CLAIM_CHECKS_LOG_PATH": logp})
    assert _warned(out)


def test_codex_sessionless_log_does_not_clear(tmp_path):
    """The pre-fix Codex behaviour: an entry with no session id stays rejected."""
    logp = str(tmp_path / "log.jsonl")
    _log(logp, {})
    out = _run(HOOK, _codex_stop(tmp_path, "s-1", CLAIM), {"CLAIM_CHECKS_LOG_PATH": logp})
    assert _warned(out)


@pytest.mark.parametrize("shape", ["codex", "claude"])
def test_no_claim_passes(tmp_path, shape):
    mk = _codex_stop if shape == "codex" else _claude_stop
    out = _run(HOOK, mk(tmp_path, "s-1", "Here is the plan. I have not started yet."),
               {"CLAIM_CHECKS_LOG_PATH": str(tmp_path / "log.jsonl")})
    assert _passed(out)


def test_codex_stop_hook_active_passes(tmp_path):
    payload = _codex_stop(tmp_path, "s-1", CLAIM)
    payload["stop_hook_active"] = True
    assert _passed(_run(HOOK, payload))


# ---- null transcript_path (Codex allows it) ----
def test_codex_null_transcript_still_checks_message(tmp_path):
    out = _run(HOOK, _codex_stop(tmp_path, "s-1", CLAIM, transcript=False),
               {"CLAIM_CHECKS_LOG_PATH": str(tmp_path / "log.jsonl")})
    assert _warned(out)


def test_codex_null_transcript_logged_claim_passes(tmp_path):
    logp = str(tmp_path / "log.jsonl")
    _log(logp, {"CODEX_SESSION_ID": "s-1"})
    out = _run(HOOK, _codex_stop(tmp_path, "s-1", CLAIM, transcript=False),
               {"CLAIM_CHECKS_LOG_PATH": logp})
    assert _passed(out)


def test_null_transcript_tool_mode_still_fails_closed(tmp_path):
    """Tool mode needs the transcript; a null path stays an 'unavailable' warning."""
    out = _run(HOOK, _codex_stop(tmp_path, "s-1", CLAIM, transcript=False),
               {"CLAIM_CHECK_EVIDENCE": "tool"})
    assert out["continue"] is True and "could not examine" in out["systemMessage"]


def test_no_transcript_and_no_message_still_passes():
    """Unchanged: with neither a transcript nor a final message there is nothing to check."""
    assert _passed(_run(HOOK, {"hook_event_name": "Stop", "transcript_path": None}))


# ---- auto_orient.py: Codex UserPromptSubmit payload ----
@pytest.mark.parametrize("shape", ["codex", "claude"])
def test_auto_orient_injects_for_both_shapes(tmp_path, shape):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text("# MEMORY\n- codeword HERON-42\n")
    payload = {"session_id": f"{shape}-{tmp_path.name}", "cwd": str(tmp_path),
               "hook_event_name": "UserPromptSubmit", "prompt": "hi"}
    if shape == "codex":
        payload.update({"turn_id": "t-1", "model": "gpt-6-astra", "transcript_path": None,
                        "permission_mode": "bypassPermissions"})
    else:
        payload["transcript_path"] = str(tmp_path / "t.jsonl")
    out = _run(ORIENT, payload)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert "HERON-42" in hso["additionalContext"]
