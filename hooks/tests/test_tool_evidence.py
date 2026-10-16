#!/usr/bin/env python3
"""
Tests for CLAIM_CHECK_EVIDENCE=tool|both — the recorded-run evidence mode.

The fixtures mirror the record shape read off real Claude Code transcripts: a Bash call
is an assistant entry with a "tool_use" block (id, name, input.command); its outcome is
a later user entry with a "tool_result" block (tool_use_id, is_error) plus a
"toolUseResult" dict (stdout/stderr/interrupted/...) — or a plain string on error.
Every entry carries sessionId + timestamp.

Most tests drive the hook the way the harness does: stdin JSON -> subprocess.
Run:  python3 -m pytest hooks/tests/ -q
"""
import json
import os
import subprocess
import sys

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
HOOK = str(HOOKS_DIR / "claim_check_hook.py")
WRITER = str(HOOKS_DIR / "log_claim.py")

SID = "11111111-2222-3333-4444-555555555555"
OTHER_SID = "99999999-8888-7777-6666-555555555555"
CLAIM = "I ran the suite. The migration is complete."


def _now(delta_min=0.0):
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_min)).isoformat()


def _prompt(sid=SID):
    return {"type": "user", "sessionId": sid, "timestamp": _now(-1), "isSidechain": False,
            "message": {"role": "user", "content": "please run the migration"}}


def _tool_use(cmd, tid="toolu_01", sid=SID, background=False, sidechain=False):
    inp = {"command": cmd, "description": "run tests"}
    if background:
        inp["run_in_background"] = True
    return {"type": "assistant", "sessionId": sid, "timestamp": _now(-0.5),
            "isSidechain": sidechain,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Bash", "input": inp}]}}


def _tool_result(tid="toolu_01", sid=SID, ok=True, ts=None, extra=None, sidechain=False):
    if ok:
        tur = {"stdout": "12 passed in 0.31s", "stderr": "", "interrupted": False,
               "isImage": False, "noOutputExpected": False}
        tur.update(extra or {})
        content = "12 passed in 0.31s"
    else:
        tur = "Error: Exit code 1\n1 failed, 11 passed"
        content = "Exit code 1\n1 failed, 11 passed"
    return {"type": "user", "sessionId": sid, "timestamp": ts or _now(-0.4),
            "isSidechain": sidechain, "toolUseResult": tur,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": content,
                 "is_error": not ok}]}}


def _final(text=CLAIM, sid=SID):
    return {"type": "assistant", "sessionId": sid, "timestamp": _now(),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _write(tmp_path, entries, sid=SID):
    p = tmp_path / f"{sid}.jsonl"   # filename stem == session id, as in real transcripts
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return str(p)


def _passing_run(cmd="python3 -m pytest hooks/tests/ -q", **kw):
    return [_tool_use(cmd, **{k: v for k, v in kw.items() if k in ("sid", "background")}),
            _tool_result(**{k: v for k, v in kw.items() if k in ("sid", "ts", "extra")})]


def _env(tmp_path, evidence="tool", mode="block", extra=None):
    # Hermetic: never inherit the runner's CLAIM_CHECK_* knobs or session id.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAIM_CHECK") and k != "CLAUDE_CODE_SESSION_ID"}
    env["CLAIM_CHECKS_LOG_PATH"] = str(tmp_path / "log.jsonl")
    env["CLAIM_CHECK_ENFORCE_MODE"] = mode
    if evidence is not None:
        env["CLAIM_CHECK_EVIDENCE"] = evidence
    env.update(extra or {})
    return env


def _run(tmp_path, transcript, evidence="tool", mode="block", extra=None, session_id=None):
    stdin = {"transcript_path": transcript, "hook_event_name": "Stop"}
    if session_id is not None:
        stdin["session_id"] = session_id
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(stdin), capture_output=True,
                       text=True, env=_env(tmp_path, evidence, mode, extra))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _log(tmp_path, claim="migration complete", verification="ran the migration tests"):
    env = _env(tmp_path, None)
    env["CLAUDE_CODE_SESSION_ID"] = SID
    subprocess.run([sys.executable, WRITER, claim, verification], capture_output=True,
                   text=True, env=env, check=True)


def _blocked(out):
    return out.get("decision") == "block"


def _passed(out):
    return out.get("continue") is True and "decision" not in out and "systemMessage" not in out


# ---- the self-alibi: the case this mode exists for ----
def test_self_alibi_log_line_without_run_blocks(tmp_path):
    _log(tmp_path)
    t = _write(tmp_path, [_prompt(), _final()])
    assert _passed(_run(tmp_path, t, evidence="log"))     # default channel: the line clears it
    out = _run(tmp_path, t, evidence="tool")
    assert _blocked(out)                                  # tool channel: the line does not
    assert "does not satisfy" in out["reason"]


def test_honest_recorded_passing_run_passes(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _final()])
    assert _passed(_run(tmp_path, t))


def test_honest_pass_with_stdin_session_id(tmp_path):
    # The harness passes session_id on stdin; the transcript filename is not consulted then.
    p = tmp_path / "renamed.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in [_prompt(), *_passing_run(), _final()]))
    assert _passed(_run(tmp_path, str(p), session_id=SID))
    assert _blocked(_run(tmp_path, str(p), session_id=OTHER_SID))


def test_cross_session_run_blocks(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(sid=OTHER_SID), _final()])
    assert _blocked(_run(tmp_path, t))


def test_failed_run_blocks(tmp_path):
    t = _write(tmp_path, [_prompt(), _tool_use("pytest -q"), _tool_result(ok=False), _final()])
    assert _blocked(_run(tmp_path, t))
    # is_error alone must decide: an errored result with a dict-shaped toolUseResult
    # (not the string real failures carry today) is still a failure.
    res = _tool_result()
    res["message"]["content"][0]["is_error"] = True
    t = _write(tmp_path, [_prompt(), _tool_use("pytest -q"), res, _final()])
    assert _blocked(_run(tmp_path, t))


def test_missing_is_error_flag_is_not_success(tmp_path):
    res = _tool_result()
    del res["message"]["content"][0]["is_error"]
    t = _write(tmp_path, [_prompt(), _tool_use("pytest -q"), res, _final()])
    assert _blocked(_run(tmp_path, t))


def test_stale_run_outside_window_blocks(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(ts=_now(-30)), _final()])
    assert _blocked(_run(tmp_path, t))


def test_window_boundary(tmp_path):
    # Window is CLAIM_CHECK_FRESH_MIN (15 min): 14 min ago counts, 16 min ago does not.
    t = _write(tmp_path, [_prompt(), *_passing_run(ts=_now(-14)), _final()])
    assert _passed(_run(tmp_path, t))
    t = _write(tmp_path, [_prompt(), *_passing_run(ts=_now(-16)), _final()])
    assert _blocked(_run(tmp_path, t))


def test_future_dated_run_blocks(tmp_path):
    # A future timestamp would otherwise keep a (forged) record "fresh" indefinitely.
    t = _write(tmp_path, [_prompt(), *_passing_run(ts=_now(+30)), _final()])
    assert _blocked(_run(tmp_path, t))


def test_allowlist_non_match_blocks(tmp_path):
    for cmd in ("echo pytest", "npm install", 'echo ";" pytest', "cat pytest.ini"):
        t = _write(tmp_path, [_prompt(), *_passing_run(cmd=cmd), _final()])
        assert _blocked(_run(tmp_path, t)), cmd


def test_exit_status_not_the_verifications_blocks(tmp_path):
    # The recorded success is the WHOLE call's status — only credit it when that status
    # is the verification's own.
    for cmd in ("pytest -q | tail -3", "pytest -q || true", "pytest -q; echo $?",
                "pytest -q &", "echo $(pytest -q)", "cd x && pytest -q || true"):
        t = _write(tmp_path, [_prompt(), *_passing_run(cmd=cmd), _final()])
        assert _blocked(_run(tmp_path, t)), cmd


def test_pipefail_makes_piped_verification_count(tmp_path):
    for cmd, credited in (("set -o pipefail; pytest -q 2>&1 | tail -3", True),
                          ("set -euo pipefail && cd x && pytest -q | tail -3", True),
                          ("pytest -q | tail -3; set -o pipefail", False),   # set too late
                          ("set +o pipefail; pytest -q | tail -3", False),   # turned off
                          ("set -o pipefail; pytest -q | tail -3 || true", False)):
        t = _write(tmp_path, [_prompt(), *_passing_run(cmd=cmd), _final()])
        out = _run(tmp_path, t)
        assert (_passed(out) if credited else _blocked(out)), cmd


def test_and_chains_around_verification_pass(tmp_path):
    for cmd in ("cd hooks && python3 -m pytest tests -q", "CI=1 pytest -q 2>&1",
                "pytest -q && echo ok", ".venv/bin/pytest -x"):
        t = _write(tmp_path, [_prompt(), *_passing_run(cmd=cmd), _final()])
        assert _passed(_run(tmp_path, t)), cmd


def test_background_and_timeout_markers_are_not_success(tmp_path):
    # All three were observed with is_error == false in real transcripts.
    variants = [
        {"background": True, "extra": {"backgroundTaskId": "b1"}},
        {"background": True},                                   # input flag alone
        {"extra": {"backgroundTaskId": "b2", "timedOutAfterMs": 60000}},
        {"extra": {"timedOutAfterMs": 60000}},                  # marker alone
        {"extra": {"backgroundTaskId": "b3"}},                  # alone: auto-backgrounded
        {"extra": {"returnCodeInterpretation": "No matches found"}},
        {"extra": {"interrupted": True}},
    ]
    for v in variants:
        t = _write(tmp_path, [_prompt(), *_passing_run(**v), _final()])
        assert _blocked(_run(tmp_path, t)), v


def test_sidechain_run_not_credited(tmp_path):
    t = _write(tmp_path, [_prompt(), _tool_use("pytest -q", sidechain=True),
                          _tool_result(sidechain=True), _final()])
    assert _blocked(_run(tmp_path, t))


def test_result_without_matching_tool_use_not_credited(tmp_path):
    t = _write(tmp_path, [_prompt(), _tool_use("pytest -q", tid="toolu_A"),
                          _tool_result(tid="toolu_B"), _final()])
    assert _blocked(_run(tmp_path, t))


def test_tool_mode_warn_surfaces_message(tmp_path):
    t = _write(tmp_path, [_prompt(), _final()])
    out = _run(tmp_path, t, mode="warn")
    assert out["continue"] is True and "no passing verification run" in out["systemMessage"]


def test_tool_mode_off_is_silent(tmp_path):
    t = _write(tmp_path, [_prompt(), _final()])
    assert _passed(_run(tmp_path, t, mode="off"))


def test_tool_mode_no_claim_passes(tmp_path):
    t = _write(tmp_path, [_prompt(), _final("Here are the options I found.")])
    assert _passed(_run(tmp_path, t))


def test_default_is_unchanged_log_mode(tmp_path):
    # Unset CLAIM_CHECK_EVIDENCE: a recorded run is ignored, the log line still decides.
    t = _write(tmp_path, [_prompt(), *_passing_run(), _final()])
    assert _blocked(_run(tmp_path, t, evidence=None))
    _log(tmp_path)
    assert _passed(_run(tmp_path, t, evidence=None))


def test_both_mode_requires_run_and_log(tmp_path):
    t_run = _write(tmp_path, [_prompt(), *_passing_run(), _final()])
    assert _blocked(_run(tmp_path, t_run, evidence="both"))   # run, no log
    _log(tmp_path)
    assert _passed(_run(tmp_path, t_run, evidence="both"))    # run + log
    t_norun = _write(tmp_path, [_prompt(), _final()])
    assert _blocked(_run(tmp_path, t_norun, evidence="both"))  # log, no run


def test_unrecognized_evidence_value_is_strictest(tmp_path):
    # A typo must not silently fall back to the weakest channel. With a log line but
    # no recorded run, "log" would pass; the typo'd value must still block.
    _log(tmp_path)
    t_norun = _write(tmp_path, [_prompt(), _final()])
    assert _passed(_run(tmp_path, t_norun, evidence="log"))
    assert _blocked(_run(tmp_path, t_norun, evidence="tools"))
    t_run = _write(tmp_path, [_prompt(), *_passing_run(), _final()])
    assert _passed(_run(tmp_path, t_run, evidence="tools"))     # both requirements met


def test_allowlist_override(tmp_path):
    extra = {"CLAIM_CHECK_VERIFY_COMMANDS": "make test, npm test"}
    t_py = _write(tmp_path, [_prompt(), *_passing_run(cmd="pytest -q"), _final()])
    assert _blocked(_run(tmp_path, t_py, extra=extra))
    t_mk = _write(tmp_path, [_prompt(), *_passing_run(cmd="make test"), _final()])
    assert _passed(_run(tmp_path, t_mk, extra=extra))


def test_malformed_transcript_lines_do_not_crash(tmp_path):
    t = tmp_path / f"{SID}.jsonl"
    body = [json.dumps(e) for e in (_prompt(), *_passing_run())]
    body += ["not json", "[1, 2]", '"a string"', json.dumps(_final())]  # final stays last
    t.write_text("\n".join(body) + "\n")
    assert _passed(_run(tmp_path, str(t)))




# ---- the creditable grammar, end to end ----
# Every shape below was shown (by a critic, against an earlier parser) to be credited
# while the recorded success was NOT the verification's own. Each must block through the
# real subprocess entry point.
MUST_BLOCK = (
    # comments / mid-word '#'
    "pytest tests/  # run the suite\necho finished", "pytest -k a#b || true",
    "pytest tests/test_x.py#z\ntrue", "echo hi #; pytest",
    # separators before the verification
    "true || pytest -q", "cd x || pytest -q", "exit 0; pytest -q", "exit 0 && pytest -q",
    "if true; then exit 0; fi; pytest -q", "cd x || exit 0 && pytest -q",
    "true ||\npytest -q", "true ||\rpytest -q", "false; pytest",
    # separators after it / status masking
    "pytest -q | tail -3", "pytest -q || true", "pytest -q; echo $?", "pytest -q &",
    "echo $(pytest -q)", "cd x && pytest -q || true", "pytest -q;", "pytest &&\n echo done",
    "pytest -q 2>&1 >/dev/null; pytest --version", "pytest |& tee log", "pytest -q && true || true",
    "pytest &> out.txt", "pytest -q >&2",
    # pipefail latched on syntax, not semantics
    "set -o pipefail; set +o pipefail; pytest | tail -3", "true || set -o pipefail; pytest | tail -3",
    "set -o pipefail | true; pytest | tail -3", "set -- -o pipefail; pytest | tail -3",
    "set +o pipefail; pytest -q | tail -3", "echo -o pipefail; pytest | tail -3",
    "pytest -q | tail -3; set -o pipefail", "set -o pipefail; pytest -q | sh",
    "cd x && set -o pipefail; pytest | tail -3",
    # shell machinery
    "trap 'exit 0' EXIT; pytest", "trap 'exit 0' EXIT && pytest -q", "eval pytest", "exec pytest",
    "source x; pytest", ". x && pytest", "alias pytest=true; pytest", "(pytest)", "{ pytest; }",
    "! pytest", "echo `pytest`", "echo \\; pytest", "echo a\\\npytest", "cat <<EOF\npytest\n",
    'pytest "unterminated', "pytest -k 'a\nb'", "pytest -q {--co,-x}", "pytest $PYTEST_FLAGS",
    "pytest -q <(true)",
    # quoting / redirection misparse
    'echo a"; pytest -q b"', "echo a'; pytest -q b'", 'echo a"|| pytest -q b"', "echo ok >| pytest",
    "true >| pytest -q", 'echo ";" pytest',
    # environment that changes the verifier
    "PYTEST_ADDOPTS=--co pytest -q", "PATH=/tmp pytest", "MAKEFLAGS=-i make test",
    "make test MAKEFLAGS=-i", "GOFLAGS=-run=X go test ./...", "npm_config_if_present=true npm test",
    # flags that mask the status or skip the run
    "ruff check --exit-zero .", "ruff check -e .", "flake8 --exit-zero .", "make test -i",
    "make test --ignore-errors", "make -n test", "make test -n", "npm test --if-present",
    "pytest --suppress-tests-failed-exit-code", "pytest --version", "pytest -V", "pytest --help",
    "pytest --collect-only -q", "pytest --co", "pytest --coll", "pytest -o addopts=--co",
    "mypy --version", "tsc --version", "tsc -v", "cargo test --no-run", "cargo test -- --list",
    "go test -c ./...", "go test -list . ./...", "ctest -N", "tox -e py -- --co", "tox list",
    "npm test -- --passWithNoTests",
    # fresh-critic round (alias spellings, attached short values, make/npm overrides)
    "pytest --collectonly", "python3 -m pytest --collectonly", "pytest --setuponly",
    "pytest --setupplan", "pytest --funcargs", "pytest --cache-show", "pytest -oaddopts=--co",
    "pytest -cfake.ini", "make test --dry", "make test --just", "make test --rec",
    "make test --ignore-err", "make test --tou", "make test SHELL=true", "make check SHELL=true",
    "npm test --script-shell=true", "npm test --script-shell true", "npm test --usage",
    "SHELL=/usr/bin/true make test", "RUSTC_WRAPPER=true cargo test",
    "CARGO_TARGET_X_RUNNER=true cargo test", "tox -n", "tox --skip-env x", "tsc --noCheck",
    "tsc -b --clean", "ctest --print-labels", "eslint --stdin", "eslint --no-config-lookup .",
)

# Honest forms the grammar accepts (documented in HOOK_INSTALL §6).
MUST_PASS = (
    "pytest", "pytest -q", "python3 -m pytest hooks/tests -q", "/usr/bin/python3.11 -m pytest",
    ".venv/bin/pytest -x", "cd hooks && python3 -m pytest tests -q", "cd ~/x && npm test",
    "CI=1 pytest -q 2>&1", "pytest -q && echo ok", "pytest -q > out.txt", "pytest -q >| out.txt",
    "pytest -k 'a or b' -q", 'pytest "tests/test_x.py::test_a[1]"', "pytest -n auto --cov=pkg",
    "set -o pipefail; pytest -q 2>&1 | tail -3", "set -euo pipefail && cd x && pytest -q | tail -3",
    "set -o pipefail; pytest -q 2>&1 | grep -E 'passed|failed' | tail -1",
    "go test ./...", "make test", "cargo test", "ruff check --fix .", "ctest -V", "pytest -q\n",
)


@pytest.mark.parametrize("cmd", MUST_BLOCK)
def test_grammar_must_block_e2e(tmp_path, cmd):
    t = _write(tmp_path, [_prompt(), *_passing_run(cmd=cmd), _final()])
    assert _blocked(_run(tmp_path, t)), cmd


@pytest.mark.parametrize("cmd", MUST_PASS)
def test_grammar_honest_forms_pass_e2e(tmp_path, cmd):
    t = _write(tmp_path, [_prompt(), *_passing_run(cmd=cmd), _final()])
    assert _passed(_run(tmp_path, t)), cmd


# ---- record-shape guards ----
def test_non_bash_tool_not_credited(tmp_path):
    # Real transcripts carry input.command on Monitor (a background watcher) and on
    # MCP tools; only the Bash tool's record shape is trusted.
    for name in ("Monitor", "bash", "mcp__x__run"):
        use = _tool_use("pytest -q")
        use["message"]["content"][0]["name"] = name
        t = _write(tmp_path, [_prompt(), use, _tool_result(), _final()])
        assert _blocked(_run(tmp_path, t)), name


def test_tool_use_on_user_entry_not_credited(tmp_path):
    use = _tool_use("pytest -q")
    use["type"] = "user"
    t = _write(tmp_path, [_prompt(), use, _tool_result(), _final()])
    assert _blocked(_run(tmp_path, t))


def test_tool_result_on_assistant_entry_not_credited(tmp_path):
    res = _tool_result()
    res["type"] = "assistant"
    t = _write(tmp_path, [_prompt(), _tool_use("pytest -q"), res, _final()])
    assert _blocked(_run(tmp_path, t))


def test_missing_ids_do_not_pair(tmp_path):
    # A tool_use with no id must not pair with a result that has no tool_use_id.
    use = _tool_use("pytest -q")
    del use["message"]["content"][0]["id"]
    res = _tool_result()
    del res["message"]["content"][0]["tool_use_id"]
    t = _write(tmp_path, [_prompt(), use, res, _final()])
    assert _blocked(_run(tmp_path, t))


MALFORMED = (
    {"type": "user", "sessionId": SID, "message": "x"},
    {"type": "user", "sessionId": SID, "message": {"content": [
        {"type": "tool_result", "tool_use_id": ["x"], "is_error": False}]}},
    {"type": "assistant", "sessionId": SID, "message": {"content": [
        {"type": "tool_use", "id": ["x"], "name": "Bash", "input": {"command": "pytest"}}]}},
    {"type": "assistant", "sessionId": SID, "message": {"content": [
        {"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": 7}}]}},
    {"type": "user", "sessionId": SID, "timestamp": 12345, "toolUseResult": {"interrupted": False},
     "message": {"content": [{"type": "tool_result", "tool_use_id": "t9", "is_error": False}]}},
)


@pytest.mark.parametrize("bad", MALFORMED)
def test_malformed_entries_fail_closed_and_do_not_mask(tmp_path, bad):
    # Alone: no credit (and no crash-to-pass). Before a real run: the real run still counts.
    t = _write(tmp_path, [_prompt(), bad, _final()])
    assert _blocked(_run(tmp_path, t))
    t = _write(tmp_path, [_prompt(), bad, *_passing_run(), _final()])
    assert _passed(_run(tmp_path, t))


@pytest.mark.parametrize("stdin", ({"transcript_path": 123}, [1, 2]))
def test_internal_error_fails_closed_in_tool_mode(tmp_path, stdin):
    def run(evidence, mode):
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(stdin), capture_output=True,
                           text=True, env=_env(tmp_path, evidence, mode))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout.strip().splitlines()[-1])
    assert _passed(run(None, "block"))            # default: fail-safe, unchanged
    assert _blocked(run("tool", "block"))         # opted in: fail closed
    assert _blocked(run("both", "block"))
    warn = run("tool", "warn")
    assert warn["continue"] is True and "internal error" in warn["systemMessage"]
    assert _passed(run("tool", "off"))


# ---- the grammar, in-process (hermetic: allowlist from the code, not the env) ----
@pytest.fixture
def h(monkeypatch):
    for k in list(os.environ):
        if k.startswith("CLAIM_CHECK"):
            monkeypatch.delenv(k)
    sys.path.insert(0, str(HOOKS_DIR))
    import claim_check_hook as mod
    return mod


def test_parser_quote_resolution(h):
    assert h._lex('echo a"; pytest -q b"') == [("w", "echo"), ("w", "a; pytest -q b")]
    assert h._lex("pytest -k 'x || y'") == [("w", "pytest"), ("w", "-k"), ("w", "x || y")]
    assert h._lex('echo "$(x)"') is None


def test_lexer_refuses_outside_the_grammar(h):
    # Each of these is ALSO refused later by the grammar; the lexer refusal is pinned on
    # its own so it cannot silently become the only thing standing.
    for cmd in ("pytest || true", "pytest |& tee x", "pytest # x", "pytest $X", "pytest {a,b}",
                "pytest -k 'a\nb'", "pytest -k 'a\rb'", "pytest \\x", "pytest -k ok\u00e9"):
        assert h._lex(cmd) is None, cmd


def test_parser_block_and_pass_lists_in_process(h):
    P = h._verify_prefixes()
    assert not [c for c in MUST_BLOCK if h._is_verification_command(c, P)]
    assert not [c for c in MUST_PASS if not h._is_verification_command(c, P)]


def test_parser_rejects_non_string(h):
    P = h._verify_prefixes()
    for cmd in (None, 7, ["pytest"], ""):
        assert h._is_verification_command(cmd, P) is False
