#!/usr/bin/env python3
"""
pipe_guard_hook: deny a test run whose recorded exit status would not be the run's own
(piped without pipefail, or followed by ||), before it runs. Allow everything else.

Run:  python3 -m pytest hooks/tests/ -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "pipe_guard_hook.py"
sys.path.insert(0, str(HOOK.parent))
from pipe_guard_hook import check  # noqa: E402

DENY = [
    "pytest -q | tail -3",
    "pytest -q | head -20",
    "python3 -m pytest hooks/tests/ -q 2>&1 | tail -5",
    "npm test 2>&1 | grep -c passing",
    "cargo test |& tee log.txt",
    "cd app && npm test | tail",
    "uv run pytest -x | tail -3",
    "timeout 300 pytest | tail -3",
    "CI=1 pytest | wc -l",
    ".venv/bin/pytest -q | tail -1",
    "python3.12 -m pytest | tail -1",
    "pytest || true",
    "npm test || echo failed",
    "make test || exit 0",
    "ls\npytest -q | tail -3",                      # multi-line command
    "set -o pipefail; echo hi\nset +o pipefail; pytest | tail",  # turned back off
    # Codex review, 2026-11-09
    "cd app;\npytest -q | tail -20",                # ; then newline
    "python3 -m pytest \\\n hooks/tests -q | tail -20",  # backslash-newline continuation
    "uv run --no-sync pytest -q | tail -20",
    "set -o pipefail; pytest -q | tail -20 || true",  # || after the whole pipeline
    "echo ready\n\npytest -q | tail -20",          # blank line
    "echo ready # setup complete\npytest -q | tail -20",  # comment keeps its newline
    "if pytest -q | tail -20; then echo passed; fi",
    "(pytest -q) | tail -20",
    "timeout -s TERM 300 pytest -q | tail -20",
    "env -u PYTHONPATH pytest -q | tail -20",
    "true || set -o pipefail; pytest -q | tail -20",  # that set may never run
    "cat > a.md <<'EOF'\npytest | tail\nEOF\npytest -q | tail -3",  # run after the body
]

ALLOW = [
    "pytest -q",
    "set -o pipefail; pytest -q | tail -3",
    "set -euo pipefail; npm test 2>&1 | tail -30",
    "set -o pipefail\npytest -q | tail -3",
    "set -o pipefail && cargo test | grep -v warning",
    "pytest -q > out.txt 2>&1",
    "pytest -q; echo $?",
    "pytest -q && git commit -m x",
    "ls | head",
    "grep -rn pytest . | head",                  # mentions, does not run
    "echo 'pytest | tail'",                      # quoted, one word
    "cat log | pytest -q",                       # pytest is the last stage
    "git log --oneline | head -5",
    "pytest -q 'unbalanced",                     # untokenizable: allowed
    "",
    # Codex review, 2026-11-09
    'command -v pytest || echo "pytest is not installed"',  # a lookup, not a run
    "pytest -q # run tests\nls | head",             # the comment ends at the newline
    'pytest --junit-prefix "|"',                   # a quoted | is not a pipe
    "pytest -q 2>&1 >out.txt",
    # heredoc bodies are data (PR bodies, commit messages, docs written by the agent)
    "gh pr create --title x --body-file - <<'EOF'\nRepro:\npytest -q | tail -3\nEOF",
    "cat > README.md <<'EOF'\nRun the tests:\n    pytest -q | tail\nEOF",
    "cat > a.md <<-EOF\n\tnpm test || true\n\tEOF",
    "cat <<A >x; cat <<B >y\npytest | tail\nA\nnpm test || true\nB",
    "cat <<< 'pytest | tail'",
]


@pytest.mark.parametrize("cmd", DENY)
def test_denies_status_masking_runs(cmd):
    assert check(cmd) is not None, cmd


@pytest.mark.parametrize("cmd", ALLOW)
def test_allows_honest_runs_and_non_tests(cmd):
    assert check(cmd) is None, cmd


def test_reason_names_the_fix():
    r = check("pytest -q | tail -3")
    assert "tail" in r and "set -o pipefail;" in r
    assert "exit status" in check("pytest || true")


def test_custom_verify_list_is_used(monkeypatch):
    monkeypatch.setenv("CLAIM_CHECK_VERIFY_COMMANDS", "bun test")
    assert check("bun test | tail") is not None
    assert check("pytest | tail") is None


def _run(payload, **env):
    e = {**os.environ, **env}
    e.pop("CLAIM_CHECK_VERIFY_COMMANDS", None)
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=30)
    return p.returncode, p.stdout


def test_hook_emits_pretooluse_deny_schema():
    rc, out = _run({"tool_name": "Bash", "tool_input": {"command": "pytest | tail -3"}})
    assert rc == 0
    hso = json.loads(out)["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"]


def test_hook_silent_on_allow_other_tools_off_and_bad_input():
    assert _run({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}) == (0, "")
    assert _run({"tool_name": "Read", "tool_input": {"command": "pytest | tail"}}) == (0, "")
    assert _run({"tool_name": "Bash", "tool_input": {"command": "pytest | tail"}},
                ANDON_PIPE_GUARD="off") == (0, "")
    p = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True, timeout=30)
    assert (p.returncode, p.stdout) == (0, "")


def test_import_failure_in_claim_check_fails_open():
    # Codex review: a bad CLAIM_CHECKS_LOG_PATH made the claim_check_hook import raise at
    # module load, before the off switch or the try. The import is now inside check().
    for extra in ({}, {"ANDON_PIPE_GUARD": "off"}):
        rc, out = _run({"tool_name": "Bash", "tool_input": {"command": "pytest | tail"}},
                       CLAIM_CHECKS_LOG_PATH="~andon_nonexistent_user_20261109/log", **extra)
        assert (rc, out) == (0, ""), extra
