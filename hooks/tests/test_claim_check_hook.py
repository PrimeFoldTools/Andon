#!/usr/bin/env python3
"""
Regression tests for claim_check_hook.py + log_claim.py.

The hook reads stdin JSON + env vars + a transcript file, so we exercise it as a
subprocess (the real production entry point) rather than importing it — that's the
only way the test reflects how the agent harness actually invokes it.

Run:  python3 -m pytest hooks/tests/ -q
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "claim_check_hook.py")
WRITER = str(Path(__file__).resolve().parent.parent / "log_claim.py")


def _transcript(tmp_path, text):
    """A transcript with one CURRENT assistant entry (so the recency guard passes)."""
    p = tmp_path / "transcript.jsonl"
    entry = {
        "type": "assistant",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": {"content": [{"type": "text", "text": text}]},
    }
    p.write_text(json.dumps(entry) + "\n")
    return str(p)


def _clean_env(extra=None):
    env = dict(os.environ)
    env.pop("CLAIM_CHECK_ENFORCE_MODE", None)
    env.pop("CLAIM_CHECKS_LOG_PATH", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)  # hermetic: never inherit the runner's session
    if extra:
        env.update(extra)
    return env


def _run(stdin_obj, env_extra=None):
    r = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(stdin_obj),
        capture_output=True, text=True, env=_clean_env(env_extra),
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _log_path(tmp_path):
    return str(tmp_path / "log.jsonl")


def _session_transcript(tmp_path, text, session_id):
    """A CURRENT assistant transcript whose FILENAME stem is the session id —
    that stem is exactly how the hook derives the session (== CLAUDE_CODE_SESSION_ID)."""
    p = tmp_path / f"{session_id}.jsonl"
    entry = {
        "type": "assistant",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": {"content": [{"type": "text", "text": text}]},
    }
    p.write_text(json.dumps(entry) + "\n")
    return str(p)


def _log_claim(logp, session_id, claim, verification):
    """Run log_claim.py the way an agent session would — with CLAUDE_CODE_SESSION_ID set."""
    env = _clean_env({"CLAIM_CHECKS_LOG_PATH": logp, "CLAUDE_CODE_SESSION_ID": session_id})
    subprocess.run([sys.executable, WRITER, claim, verification],
                   capture_output=True, text=True, env=env, check=True)


# ---- safe pass-through (must never crash / never spuriously block) ----
def test_off_mode_passes(tmp_path):
    out = _run({"transcript_path": _transcript(tmp_path, "all done and shipped")},
               {"CLAIM_CHECK_ENFORCE_MODE": "off"})
    assert out["continue"] is True and "decision" not in out


def test_empty_stdin_passes():
    r = subprocess.run([sys.executable, HOOK], input="", capture_output=True, text=True, env=_clean_env())
    assert r.returncode == 0
    assert json.loads(r.stdout.strip().splitlines()[-1])["continue"] is True


def test_malformed_stdin_passes():
    r = subprocess.run([sys.executable, HOOK], input="not json{", capture_output=True, text=True, env=_clean_env())
    assert r.returncode == 0
    assert json.loads(r.stdout.strip().splitlines()[-1])["continue"] is True


def test_stop_hook_active_passes(tmp_path):
    out = _run({"stop_hook_active": True, "transcript_path": _transcript(tmp_path, "this is done")})
    assert out["continue"] is True


def test_no_transcript_passes():
    assert _run({})["continue"] is True


def test_missing_transcript_file_passes(tmp_path):
    assert _run({"transcript_path": str(tmp_path / "nope.jsonl")})["continue"] is True


# ---- claim detection ----
def test_claim_without_log_warns_by_default(tmp_path):
    out = _run({"transcript_path": _transcript(tmp_path, "The migration is complete.")},
               {"CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    assert out["continue"] is True and "systemMessage" in out  # warn, not block


def test_claim_without_log_blocks_in_block_mode(tmp_path):
    out = _run({"transcript_path": _transcript(tmp_path, "The migration is complete.")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    assert out.get("decision") == "block" and "reason" in out


def test_common_done_claim_forms_block(tmp_path):
    # These are common agent closures; the original v1 detector only caught
    # auxiliary forms like "is complete" / "are fixed".
    for text in (
        "Done.",
        "Done — fixed.",
        "Shipped the fix.",
        "Fixed the tests.",
        "Implemented the migration.",
        "All set.",
    ):
        out = _run({"transcript_path": _transcript(tmp_path, text)},
                   {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
        assert out.get("decision") == "block", text


def test_benign_participle_openers_do_not_fire(tmp_path):
    # Regression: the leading-action pattern must NOT fire on ordinary prose
    # that merely OPENS with a past-participle adjective ("Fixed income",
    # "Shipped goods"). It fires only on verb + determiner + object
    # ("Fixed the tests."). Without the determiner requirement these all
    # false-block — the exact thing a skeptic weaponizes against a hook repo.
    for text in (
        "Fixed income securities are stable this quarter.",
        "Shipped goods arrived at the dock this morning.",
        "Resolved disputes are common in arbitration.",
        "Completed applications go in the left tray.",
        "Verified users get a badge on their profile.",
        "Implemented designs need review before launch.",
    ):
        out = _run({"transcript_path": _transcript(tmp_path, text)},
                   {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
        assert out["continue"] is True, text


def test_no_claim_passes(tmp_path):
    out = _run({"transcript_path": _transcript(tmp_path, "Here is a summary of the options.")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    assert out["continue"] is True


# ---- trimmed verbs: everyday English must NOT false-fire ----
def test_current_does_not_false_fire(tmp_path):
    out = _run({"transcript_path": _transcript(tmp_path, "The data is current as of yesterday.")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    assert out["continue"] is True


def test_functional_does_not_false_fire(tmp_path):
    out = _run({"transcript_path": _transcript(tmp_path, "Is the new endpoint functional now?")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    assert out["continue"] is True


def test_questions_do_not_false_fire(tmp_path):
    # The first three are question word-order that matches no pattern anyway.
    # The last three DO match a claim pattern and are exempted ONLY by the
    # question guard (verb+determiner / "are fixed" + a trailing "?") — deleting
    # the guard makes them block, so they actually exercise it (not pass-by-luck).
    for text in (
        "Are the tests fixed?",
        "Did we ship the fix?",
        "Is the migration complete?",
        "Shipped the fix?",
        "Fixed the tests already?",
        "The tests are fixed now?",
    ):
        out = _run({"transcript_path": _transcript(tmp_path, text)},
                   {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
        assert out["continue"] is True, text


# ---- exemptions ----
def test_backtick_exempt(tmp_path):
    out = _run({"transcript_path": _transcript(tmp_path, "Set the flag `is done` in config.")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    assert out["continue"] is True


def test_blockquote_exempt(tmp_path):
    out = _run({"transcript_path": _transcript(tmp_path, "> the feature is complete")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    assert out["continue"] is True


# ---- the chicken-and-egg fix: log_claim.py creates the dir + satisfies the gate ----
def test_log_claim_creates_dir_and_appends(tmp_path):
    logp = tmp_path / "nested" / "deeper" / "log.jsonl"  # parent does NOT exist
    env = _clean_env({"CLAIM_CHECKS_LOG_PATH": str(logp)})
    r = subprocess.run([sys.executable, WRITER, "did X", "ran test Y"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert logp.exists()
    entry = json.loads(logp.read_text().strip().splitlines()[-1])
    assert entry["claim"] == "did X" and entry["verification"] == "ran test Y"


def test_fresh_log_lets_claim_pass(tmp_path):
    # A fresh, same-session entry whose fingerprint shares the claim's subject clears it.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "shipped the feature", "ran pytest")
    out = _run({"transcript_path": _session_transcript(tmp_path, "The feature is shipped.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out["continue"] is True and "decision" not in out


def test_stale_log_does_not_satisfy_claim(tmp_path):
    # A verification entry OLDER than CLAIM_CHECK_FRESH_MIN (default 15min) must NOT
    # satisfy a fresh done-claim — otherwise "I verified something an hour ago" would
    # license any "done" now. Complements test_fresh_log_lets_claim_pass: together
    # they pin the freshness window from both sides.
    logp = tmp_path / "log.jsonl"
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    logp.write_text(json.dumps({"timestamp": stale_ts, "claim": "x", "verification": "y"}) + "\n")
    out = _run({"transcript_path": _transcript(tmp_path, "The migration is complete.")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": str(logp)})
    assert out.get("decision") == "block"


def test_ignores_stale_prior_assistant_message(tmp_path):
    # Transcript-flush race guard: if the most-recent assistant entry is older than
    # STALE_THRESHOLD_S (30s) it belongs to a PRIOR turn — the hook must not block on
    # it even though it carries a done-claim and no fresh log exists.
    p = tmp_path / "transcript.jsonl"
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    entry = {"type": "assistant", "timestamp": old_ts,
             "message": {"content": [{"type": "text", "text": "The migration is complete."}]}}
    p.write_text(json.dumps(entry) + "\n")
    out = _run({"transcript_path": str(p)},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    assert out["continue"] is True and "decision" not in out


def test_log_claim_requires_two_args(tmp_path):
    env = _clean_env({"CLAIM_CHECKS_LOG_PATH": _log_path(tmp_path)})
    r = subprocess.run([sys.executable, WRITER, "only one"], capture_output=True, text=True, env=env)
    assert r.returncode == 2


def test_malformed_log_line_does_not_crash(tmp_path):
    # P0 regression: a non-string timestamp in the log must NOT crash the Stop hook —
    # the whole pitch is "fail-safe: any error passes the turn through." Pre-fix this
    # raised AttributeError and exited 1; the bad line must be skipped instead.
    logp = tmp_path / "log.jsonl"
    logp.write_text('{"ts": 12345}\n')  # int timestamp = malformed
    out = _run({"transcript_path": _transcript(tmp_path, "The migration is complete.")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": str(logp)})
    # Valid output (exit 0 is asserted in _run); bad line skipped → no fresh log → blocks.
    assert out.get("decision") == "block"


# ---- issue #2: bind the fresh log entry to (claim ∧ session) ----
# Reporter's three alibi repros must BLOCK; each has a paired legitimate-work test
# that must PASS, so nothing passes by "always block."

def test_self_alibi_blocked(tmp_path):
    # repro #1: a fresh entry whose content is unrelated to the claim must NOT clear it.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "done", "checked")  # fingerprint has no content tokens
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out.get("decision") == "block"


def test_matching_claim_passes(tmp_path):
    # false-block guard for test_self_alibi_blocked: a same-session entry that shares a
    # content token with the claim ("migration") DOES clear it.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "migration applied cleanly", "ran alembic upgrade head, 0 errors")
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out["continue"] is True and "decision" not in out


def test_unrelated_work_alibi_blocked(tmp_path):
    # repro #2: verifying task A must not license an unverified claim about task B.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "database migration verified", "ran migration test")  # task A
    out = _run({"transcript_path": _session_transcript(tmp_path, "The API endpoint is shipped.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})  # task B claim
    assert out.get("decision") == "block"


def test_cross_session_alibi_blocked(tmp_path):
    # repro #3 (the most dangerous): a fresh, token-MATCHING entry from another session
    # must NOT clear this session's claim — even though tokens match and it's fresh.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "migration applied", "ran test")
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-B")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out.get("decision") == "block"


def test_legacy_entry_still_passes(tmp_path):
    # backward-compat: a pre-binding entry (no session_id / claim_fingerprint) that is
    # fresh keeps the old freshness-only behavior — fail-open, so the upgrade never
    # traps in-flight legitimate work. (These entries self-expire within one window.)
    logp = tmp_path / "log.jsonl"
    fresh_ts = datetime.now(timezone.utc).isoformat()
    logp.write_text(json.dumps({"timestamp": fresh_ts, "claim": "x", "verification": "y"}) + "\n")
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": str(logp)})
    assert out["continue"] is True and "decision" not in out


def test_new_schema_stale_still_blocks(tmp_path):
    # freshness still dominates: a session+token MATCHING new-schema entry that is stale
    # must not clear the claim.
    logp = tmp_path / "log.jsonl"
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    logp.write_text(json.dumps({
        "timestamp": stale_ts, "claim": "migration applied", "verification": "ran test",
        "session_id": "sess-A", "claim_fingerprint": "migration applied",
    }) + "\n")
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": str(logp)})
    assert out.get("decision") == "block"


def test_closure_verbs_do_not_bind(tmp_path):
    # Mutation guard (behavioral): closure verbs are stopworded, so a shared "complete"
    # between claim and log must NOT create a spurious bind. If someone un-stopwords the
    # closure verbs, claim {migration, complete} and log {task, complete} would overlap on
    # "complete" and this would PASS — flipping this assertion RED.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "task complete", "did the task")  # shares only "complete" (stopworded)
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out.get("decision") == "block"


def _import_hook_modules():
    import importlib
    sys.path.insert(0, str(Path(HOOK).parent))
    return importlib.import_module("claim_check_hook"), importlib.import_module("log_claim")


def test_content_tokens_stopwords_closure_verbs():
    hook, _ = _import_hook_modules()
    toks = hook._content_tokens("The migration is complete.")
    assert "migration" in toks
    assert "complete" not in toks and "is" not in toks and "the" not in toks


def test_fingerprint_consistency_across_files():
    # Guard the intentional duplication: assert the stopword set AND the tokenizer
    # SOURCE are identical, not just that they agree on a few sample strings (a shallow
    # sample would miss drift on any word not in the sample).
    import inspect
    hook, writer = _import_hook_modules()
    assert hook._STOPWORDS == writer._STOPWORDS
    assert inspect.getsource(hook._content_tokens) == inspect.getsource(writer._content_tokens)


def test_multi_claim_partial_verification_blocks(tmp_path):
    # Per-claim binding: two claims in two sentences; verifying only the first must NOT
    # clear the second (no union laundering across claims).
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "migration applied", "ran alembic upgrade")  # covers claim 1 only
    out = _run({"transcript_path": _session_transcript(
                    tmp_path, "The migration is complete. The API endpoint is shipped.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out.get("decision") == "block"


def test_multi_claim_full_verification_passes(tmp_path):
    # Paired pass for the above: covering BOTH claims (two entries) clears the turn.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "migration applied", "ran alembic upgrade")
    _log_claim(logp, "sess-A", "endpoint deployed", "curl /health returns 200")
    out = _run({"transcript_path": _session_transcript(
                    tmp_path, "The migration is complete. The API endpoint is shipped.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out["continue"] is True and "decision" not in out


def test_empty_session_entry_cannot_alibi(tmp_path):
    # An entry written without CLAUDE_CODE_SESSION_ID (session_id == "") must NOT satisfy
    # a session-bound claim — otherwise the cross-session hole reopens via a blank session.
    logp = tmp_path / "log.jsonl"
    fresh_ts = datetime.now(timezone.utc).isoformat()
    logp.write_text(json.dumps({
        "timestamp": fresh_ts, "claim": "migration applied", "verification": "ran test",
        "session_id": "", "claim_fingerprint": "migration",
    }) + "\n")
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-B")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": str(logp)})
    assert out.get("decision") == "block"


def test_paraphrase_verification_passes(tmp_path):
    # False-block guard: the claim's subject appears in the VERIFICATION text, not the
    # claim arg. Because the fingerprint covers claim + verification, it still binds.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "shipped it", "the endpoint returns 200 on /health")
    out = _run({"transcript_path": _session_transcript(tmp_path, "The endpoint is shipped.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out["continue"] is True and "decision" not in out


def test_handwritten_legacy_line_cannot_downgrade_migrated_log(tmp_path):
    # A hand-appended legacy-shaped line must NOT revert an already-migrated log to
    # fail-open: because a new-schema entry exists, legacy entries are ignored.
    logp = tmp_path / "log.jsonl"
    now = datetime.now(timezone.utc).isoformat()
    logp.write_text(
        json.dumps({"timestamp": now, "claim": "x", "verification": "y"}) + "\n" +        # legacy
        json.dumps({"timestamp": now, "claim": "unrelated", "verification": "z",
                    "session_id": "sess-A", "claim_fingerprint": "unrelated"}) + "\n"      # new-schema
    )
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": str(logp)})
    assert out.get("decision") == "block"


def test_repeated_phrase_across_sentences_binds_separately(tmp_path):
    # Audit finding: the SAME closure phrase in two sentences with different subjects
    # must be bound per-sentence — verifying one must not launder the other.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "migration applied", "ran alembic upgrade")  # only the migration
    out = _run({"transcript_path": _session_transcript(
                    tmp_path, "The migration is complete. The endpoint is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out.get("decision") == "block"


def test_repeated_phrase_across_sentences_both_verified_passes(tmp_path):
    # Paired pass: covering BOTH same-phrased claims clears the turn.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "migration applied", "ran alembic upgrade")
    _log_claim(logp, "sess-A", "endpoint deployed", "curl /health 200")
    out = _run({"transcript_path": _session_transcript(
                    tmp_path, "The migration is complete. The endpoint is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out["continue"] is True and "decision" not in out


def test_dotted_identifier_not_split_into_extension(tmp_path):
    # Audit finding: "migration.py" must not segment on the dot (which would truncate the
    # subject to "py"). Honest evidence naming the module clears it; it isn't false-blocked.
    logp = _log_path(tmp_path)
    _log_claim(logp, "sess-A", "updated the migration module", "ran the migration")
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration.py is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": logp})
    assert out["continue"] is True and "decision" not in out


def test_stale_new_schema_plus_fresh_legacy_does_not_failopen(tmp_path):
    # Audit finding (migration determination): a STALE new-schema entry + a FRESH legacy
    # line must NOT revert the log to fail-open — the log is migrated, so legacy is off.
    logp = tmp_path / "log.jsonl"
    stale = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    logp.write_text(
        json.dumps({"timestamp": stale, "claim": "old", "verification": "z",
                    "session_id": "sess-A", "claim_fingerprint": "old"}) + "\n" +   # stale new-schema
        json.dumps({"timestamp": fresh, "claim": "x", "verification": "y"}) + "\n"   # fresh legacy
    )
    out = _run({"transcript_path": _session_transcript(tmp_path, "The migration is complete.", "sess-A")},
               {"CLAIM_CHECK_ENFORCE_MODE": "block", "CLAIM_CHECKS_LOG_PATH": str(logp)})
    assert out.get("decision") == "block"
