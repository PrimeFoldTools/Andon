#!/usr/bin/env python3
"""Regression tests for short done-claims and the stale final message (2026-10-28).

1. Short closers. "All fixed.", "Merged.", "Tests pass.", "That's fixed." and other
   whole-sentence closers were not detected. A sentence-start pattern now catches them.
   It needs the closer to END the sentence, so the same words inside a longer sentence
   ("If all tests pass, we can merge.") stay quiet. CLAIMS and NON_CLAIMS are the survey
   lists from the PR, recorded literally, not derived from the code.
2. Final message. Claude Code passes the final message on stdin as
   `last_assistant_message`; the hook now prefers it over the transcript. Without it, a
   stale newest transcript entry in tool/both mode is re-read for a bounded time and then
   fails closed instead of passing. Log mode keeps the old pass.
"""
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS_DIR))
import claim_check_hook as cc  # noqa: E402

SID = "44444444-5555-6666-7777-888888888888"

# ---- 1. short closers ----
CLAIMS = [
    "All fixed.", "All fixed", "All done.", "All done!", "Fixed.", "Fixed it.", "Fixed!",
    "Done and tested.", "That's fixed.", "That's done.", "Should be fixed now.",
    "Works now.", "It works now.", "Tests pass.", "All tests pass.", "All tests passing.",
    "Everything passes.", "Shipped.", "Good to go.", "Ready for review.", "Merged.",
    "Pushed.", "It's working.", "Resolved.", "Completed.", "Implemented.", "All good.",
    "All green.", "Everything is fixed.", "Everything works.", "It's fixed.", "It's done.",
    "All set!", "Done!", "Fixed and pushed.", "Merged and deployed.", "The bug is fixed.",
    "All changes are pushed.", "Deployed.", "All tests are passing.",
    "Committed and pushed.", "That should do it.",
]
# Still missed: "pushed" is not a closure verb after "are" (pattern 4), on purpose:
# "the event is pushed to clients" is description.
CLAIMS_STILL_MISSED = ["All changes are pushed."]

NON_CLAIMS = [
    "Is it fixed?", "Not fixed yet.", "Once it's fixed, we can deploy.",
    "If all tests pass, we can merge.", "> All fixed.", "`Fixed.`",
    "The fixed-width font is easier to read.", "Use a done callback to signal completion.",
    "Ready?", "I'll push after review.", "Tests pass when X is set.", "Is it working?",
    "Is everything fixed?", "Not done yet.", "Nothing is fixed yet.",
    "When it's done, ping me.", "Fixed costs are high this quarter.",
    "Done means different things to different teams.", "Should I push?",
    "Want me to merge?", "Merged branches get deleted automatically.",
    "Pushed notifications are opt-in.", "Ready for review?", "Shall I mark it resolved?",
    "Tests pass only on Linux?", "Do the tests pass?", "I haven't fixed it.",
    "It isn't working yet.", "It's not fixed.", "Works on my machine is not evidence.",
    "All good questions here.", "All done callbacks run in order.",
    "All fixed-income funds are listed below.",
    "Everything passes through the proxy first.",
    "Deployed services are listed in the table.", "Shipped items appear in the log.",
    "Completed orders move to the archive.", "Merge when the tests pass.",
    "I'll mark it done once CI is green.", "Let me know when it's done.",
    "Pushed? Not yet.", "Is it all fixed?",
]
# Fires on main too ("is fixed", pattern 4); not changed here.
NON_CLAIMS_PRE_EXISTING_FIRE = ["Nothing is fixed yet."]


def test_short_closers_fire():
    missed = [t for t in CLAIMS if not cc.find_claims(t)]
    assert missed == CLAIMS_STILL_MISSED


def test_near_misses_stay_quiet():
    fired = [t for t in NON_CLAIMS if cc.find_claims(t)]
    assert fired == NON_CLAIMS_PRE_EXISTING_FIRE


@pytest.mark.parametrize("text,phrase", [
    ("All fixed.", "All fixed."),
    ("All fixed", "All fixed"),
    ("Merged.", "Merged."),
    ("That\u2019s fixed.", "That\u2019s fixed."),
    ("I updated the parser.\nTests pass.", "Tests pass."),
    ("Fixed it.", "Fixed it"),
    # Red team (qwen3-coder:30b): passed before the subject form was added.
    ("Everything's good to go.", "Everything's good to go."),
])
def test_named_closers(text, phrase):
    assert phrase in cc.find_claims(text)


def test_dash_ends_a_closer_only_without_a_word_after_it():
    assert cc.find_claims("All fixed - the parser was the cause.") == ["All fixed -"]
    assert cc.find_claims("All fixed-income funds are listed below.") == []


def test_short_closer_in_a_quote_or_code_is_exempt():
    assert cc.find_claims("> Merged.") == []
    assert cc.find_claims("Run `git status`: `Merged.`") == []
    assert cc.find_claims("```\nAll fixed.\n```") == []


def test_new_closure_words_do_not_bind():
    """"Merged." carries no subject. Like "Done.", it binds on the message, not on the
    word "merged" (which every merge claim would share)."""
    sets = cc._claim_token_sets("I refactored the parser. Merged.")
    assert [set(s) for s in sets] == [{"refactored", "parser"}]


def test_stopwords_still_match_log_claim():
    import log_claim
    assert cc._STOPWORDS == log_claim._STOPWORDS
    assert {"merged", "pushed", "deployed", "passing"} <= cc._STOPWORDS


# ---- 2. final message: stdin field first, bounded re-read, then fail closed ----
def _entry(text, age_s):
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    return json.dumps({"type": "assistant", "timestamp": ts, "sessionId": SID,
                       "message": {"content": [{"type": "text", "text": text}]}}) + "\n"


def _transcript(tmp_path, text, age_s=0):
    p = tmp_path / f"{SID}.jsonl"
    p.write_text(_entry(text, age_s))
    return p


class _Sleeps:
    """Stands in for time.sleep and time.monotonic: no real waiting. Each sleep advances
    the fake clock and may run a hook (e.g. the harness flushing the final message)."""

    def __init__(self, on_sleep=None):
        self.t, self.calls, self.on_sleep = 1000.0, 0, on_sleep

    def sleep(self, s):
        self.calls += 1
        self.t += s
        if self.on_sleep:
            self.on_sleep(self.calls)

    def monotonic(self):
        return self.t


def _main(monkeypatch, capsys, tmp_path, hook_input, mode="block", evidence="tool",
          clock=None):
    clock = clock or _Sleeps()
    monkeypatch.setattr(cc.time, "sleep", clock.sleep)
    monkeypatch.setattr(cc.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(cc, "CLAIM_CHECKS_LOG", tmp_path / "log.jsonl")
    monkeypatch.setenv("CLAIM_CHECK_ENFORCE_MODE", mode)
    monkeypatch.setenv("CLAIM_CHECK_EVIDENCE", evidence)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))
    with pytest.raises(SystemExit):
        cc.main()
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1]), clock


def test_stale_tool_mode_fails_closed_after_bounded_wait(monkeypatch, capsys, tmp_path):
    p = _transcript(tmp_path, "Is it fixed?", age_s=300)
    out, clock = _main(monkeypatch, capsys, tmp_path, {"transcript_path": str(p)})
    assert out.get("decision") == "block"
    assert "older than" in out["reason"]
    # Bounded: about STALE_RETRY_S / STALE_RETRY_STEP_S re-reads, then stop.
    expected = cc.STALE_RETRY_S / cc.STALE_RETRY_STEP_S
    assert expected - 1 <= clock.calls <= expected + 1


@pytest.mark.parametrize("evidence", ["tool", "both"])
def test_stale_warn_mode_warns(monkeypatch, capsys, tmp_path, evidence):
    p = _transcript(tmp_path, "Is it fixed?", age_s=300)
    out, _ = _main(monkeypatch, capsys, tmp_path, {"transcript_path": str(p)},
                   mode="warn", evidence=evidence)
    assert out.get("continue") is True and "older than" in out.get("systemMessage", "")


def test_stale_log_mode_still_passes_without_waiting(monkeypatch, capsys, tmp_path):
    p = _transcript(tmp_path, "All fixed.", age_s=300)
    out, clock = _main(monkeypatch, capsys, tmp_path, {"transcript_path": str(p)},
                       evidence="log")
    assert out == {"continue": True, "suppressOutput": True}
    assert clock.calls == 0


def test_stale_then_flushed_reads_the_flushed_message(monkeypatch, capsys, tmp_path):
    p = _transcript(tmp_path, "Is it fixed?", age_s=300)

    def flush(n):
        if n == 3:
            with p.open("a") as f:
                f.write(_entry("All fixed.", 0))
    out, clock = _main(monkeypatch, capsys, tmp_path, {"transcript_path": str(p)},
                       clock=_Sleeps(flush))
    assert clock.calls == 3
    assert out.get("decision") == "block"
    assert '"All fixed."' in out["reason"]            # the flushed claim, not "older than"


def test_stale_then_flushed_non_claim_passes(monkeypatch, capsys, tmp_path):
    p = _transcript(tmp_path, "All fixed.", age_s=300)

    def flush(n):
        if n == 1:
            with p.open("a") as f:
                f.write(_entry("Is it fixed?", 0))
    out, _ = _main(monkeypatch, capsys, tmp_path, {"transcript_path": str(p)},
                   clock=_Sleeps(flush))
    assert out == {"continue": True, "suppressOutput": True}


def test_stop_hook_active_still_passes_a_stale_transcript(monkeypatch, capsys, tmp_path):
    p = _transcript(tmp_path, "Is it fixed?", age_s=300)
    out, clock = _main(monkeypatch, capsys, tmp_path,
                       {"transcript_path": str(p), "stop_hook_active": True})
    assert out == {"continue": True, "suppressOutput": True} and clock.calls == 0


@pytest.mark.parametrize("evidence", ["log", "tool", "both"])
def test_stdin_final_message_is_what_is_checked(monkeypatch, capsys, tmp_path, evidence):
    # The transcript's newest entry is fresh and harmless; the final message on stdin
    # holds the claim. The claim must be found.
    p = _transcript(tmp_path, "Is it fixed?")
    out, _ = _main(monkeypatch, capsys, tmp_path,
                   {"transcript_path": str(p), "last_assistant_message": "All fixed."},
                   evidence=evidence)
    assert out.get("decision") == "block" and '"All fixed."' in out["reason"]


@pytest.mark.parametrize("evidence", ["log", "tool", "both"])
def test_stdin_non_claim_wins_over_transcript(monkeypatch, capsys, tmp_path, evidence):
    p = _transcript(tmp_path, "All fixed.")
    out, _ = _main(monkeypatch, capsys, tmp_path,
                   {"transcript_path": str(p), "last_assistant_message": "Is it fixed?"},
                   evidence=evidence)
    assert out == {"continue": True, "suppressOutput": True}


def test_stdin_field_skips_the_stale_wait(monkeypatch, capsys, tmp_path):
    p = _transcript(tmp_path, "All fixed.", age_s=300)
    out, clock = _main(monkeypatch, capsys, tmp_path,
                       {"transcript_path": str(p), "last_assistant_message": "Is it fixed?"})
    assert out == {"continue": True, "suppressOutput": True} and clock.calls == 0


def test_stdin_empty_string_means_no_text(monkeypatch, capsys, tmp_path):
    p = _transcript(tmp_path, "All fixed.")
    out, _ = _main(monkeypatch, capsys, tmp_path,
                   {"transcript_path": str(p), "last_assistant_message": ""})
    assert out == {"continue": True, "suppressOutput": True}


@pytest.mark.parametrize("bad", [None, 7, ["All fixed."], {"text": "x"}])
def test_non_string_field_falls_back_to_transcript(monkeypatch, capsys, tmp_path, bad):
    p = _transcript(tmp_path, "All fixed.")
    out, _ = _main(monkeypatch, capsys, tmp_path,
                   {"transcript_path": str(p), "last_assistant_message": bad})
    assert out.get("decision") == "block" and '"All fixed."' in out["reason"]


def test_stdin_field_gets_the_hidden_character_reading(monkeypatch, capsys, tmp_path):
    p = _transcript(tmp_path, "Is it fixed?")
    out, _ = _main(monkeypatch, capsys, tmp_path,
                   {"transcript_path": str(p), "last_assistant_message": "Do\u200bne."},
                   evidence="log")
    assert out.get("decision") == "block"
