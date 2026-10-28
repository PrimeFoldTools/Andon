#!/usr/bin/env python3
"""Regression tests for invisible characters and the in-flight final record (2026-10-28).

1. Invisible characters. A format character (Unicode category Cf: zero-width space,
   zero-width joiner, BOM, word joiner, soft hyphen) or a control character inside a
   claim word ("Do<ZWSP>ne.") made the claim invisible to the matcher. The hook now
   checks the message as a reader sees it: those characters are removed first, and every
   later step (code spans, quotes, questions, token binding, the echoed phrases) runs on
   that same text. Text without such characters is unchanged.
2. In-flight final record. In tool/both mode an unparseable newest record fails closed.
   A last line with no terminating newline is a record still being written, not a
   broken one, so it is skipped in every mode. A newline-terminated record that does not
   parse still fails closed.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent
HOOK = str(HOOKS_DIR / "claim_check_hook.py")
sys.path.insert(0, str(HOOKS_DIR))
import claim_check_hook as cc  # noqa: E402

SID = "33333333-4444-5555-6666-777777777777"
INVISIBLE = {
    "ZWSP": "​", "ZWJ": "‍", "BOM": "﻿", "WJ": "⁠", "SHY": "­",
    "NUL": "\x00", "VT": "\x0b", "FF": "\x0c", "ESC": "\x1b", "DEL": "\x7f", "NEL": "\x85",
}
PARTIAL = '{"type":"user","sessionId":"s1","mess'


def _now(delta_min=0.0):
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_min)).isoformat()


def _assistant(text, delta_min=0.0):
    return {"type": "assistant", "sessionId": SID, "timestamp": _now(delta_min),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _write(tmp_path, entries, tail=""):
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries) + tail,
                 encoding="utf-8")
    return p


def _run(tmp_path, transcript, evidence=None, mode="block"):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAIM_CHECK") and k != "CLAUDE_CODE_SESSION_ID"}
    env["CLAIM_CHECKS_LOG_PATH"] = str(tmp_path / "log.jsonl")
    env["CLAIM_CHECK_ENFORCE_MODE"] = mode
    if evidence is not None:
        env["CLAIM_CHECK_EVIDENCE"] = evidence
    stdin = {"transcript_path": str(transcript), "hook_event_name": "Stop", "session_id": SID}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(stdin), capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().split("\n")[-1])


def _blocked(out):
    return out.get("decision") == "block"


def _passed(out):
    return out.get("continue") is True and "decision" not in out and "systemMessage" not in out


# ---- 1. invisible characters ----
def test_baseline_claims_found():
    assert cc.find_claims("Done.") == ["Done."]
    assert cc.find_claims("The fix is done.") == ["is done"]


@pytest.mark.parametrize("name", sorted(INVISIBLE))
@pytest.mark.parametrize("template,want", [
    ("{c}Done.", ["Done."]),
    ("Do{c}ne.", ["Done."]),
    ("The fix is d{c}one.", ["is done"]),
])
def test_invisible_character_does_not_hide_claim(name, template, want):
    assert cc.find_claims(template.format(c=INVISIBLE[name])) == want


def test_carriage_return_inside_word_does_not_hide_claim():
    assert cc.find_claims("Do\rne.") == ["Done."]
    assert cc.find_claims("Done.\r\nNext I read the file.") == ["Done."]


@pytest.mark.parametrize("text", [
    "`Do​ne.`",                    # still inside a code span
    "> Do​ne.",                     # still in a blockquote
    "Is the fix d​one?",            # still a question
])
def test_exemptions_still_apply_after_removal(text):
    assert cc.find_claims(text) == []


def test_token_binding_uses_the_visible_text():
    sets = cc._claim_token_sets("The migra​tion is d​one.")
    assert [set(s) for s in sets] == [{"migration"}]


@pytest.mark.parametrize("text", [
    "", "Done.", "The fix is done.\n\nNext\tstep: read `x`.", "日本語 é — “done”",
    "Line one line two", "> quoted\n```\ncode\n```",
])
def test_text_without_hidden_characters_is_unchanged(text):
    assert cc._visible_text(text) == text


def test_removal_is_idempotent():
    s = "Do​ne.\x0b\r\nx"
    assert cc._visible_text(cc._visible_text(s)) == cc._visible_text(s) == "Done.\nx"


@pytest.mark.parametrize("evidence", [None, "log", "tool", "both"])
def test_hook_blocks_claim_with_zero_width_space(tmp_path, evidence):
    t = _write(tmp_path, [_assistant("The fix is d​one.")])
    out = _run(tmp_path, t, evidence=evidence)
    assert _blocked(out), out
    assert '"is done"' in out["reason"]        # the echoed phrase is the visible one
    assert "​" not in out["reason"]


# ---- 2. in-flight final record ----
def test_tool_mode_in_flight_final_record_is_skipped(tmp_path):
    t = _write(tmp_path, [_assistant("Reading the file next.")], tail=PARTIAL)
    assert not t.read_text().endswith("\n")
    assert _passed(_run(tmp_path, t, evidence="tool"))


@pytest.mark.parametrize("evidence", [None, "both"])
def test_other_modes_in_flight_final_record_is_skipped(tmp_path, evidence):
    t = _write(tmp_path, [_assistant("Reading the file next.")], tail=PARTIAL)
    assert _passed(_run(tmp_path, t, evidence=evidence))


def test_tool_mode_terminated_unparseable_record_still_fails_closed(tmp_path):
    t = _write(tmp_path, [_assistant("Reading the file next.")], tail=PARTIAL + "\n")
    out = _run(tmp_path, t, evidence="tool")
    assert _blocked(out) and "could not examine" in out["reason"], out


def test_tool_mode_claim_before_in_flight_record_still_gated(tmp_path):
    # Skipping the in-flight line must not skip the claim before it.
    t = _write(tmp_path, [_assistant("The migration is complete.")], tail=PARTIAL)
    out = _run(tmp_path, t, evidence="tool")
    assert _blocked(out) and "no passing verification run" in out["reason"], out
