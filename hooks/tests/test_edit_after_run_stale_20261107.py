#!/usr/bin/env python3
"""
Tool/both evidence mode: a passing run that happened BEFORE the last file edit is not
credited. The case: tests pass, the agent edits one more file, then says "all tests pass"
without running them again (anthropics/claude-code#104504, the stale-pass finding).

Order is transcript order (the harness appends entries as they happen), not timestamps.
Run:  python3 -m pytest hooks/tests/ -q
"""
import pytest

from test_tool_evidence import (SID, OTHER_SID, _blocked, _final, _now, _passed,
                                _passing_run, _prompt, _run, _tool_result, _tool_use,
                                _write)


def _edit(name="Edit", sid=SID, sidechain=False, tid="toolu_edit"):
    inp = {"file_path": "/repo/app.py", "old_string": "a", "new_string": "b"}
    return {"type": "assistant", "sessionId": sid, "timestamp": _now(-0.3),
            "isSidechain": sidechain,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _edit_result(ok=True, sid=SID, tid="toolu_edit"):
    return {"type": "user", "sessionId": sid, "timestamp": _now(-0.2), "isSidechain": False,
            "toolUseResult": {"filePath": "/repo/app.py"} if ok else "Error: no match",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "ok",
                 "is_error": not ok}]}}


def _rerun():
    return [_tool_use("python3 -m pytest hooks/tests/ -q", tid="toolu_02"),
            _tool_result(tid="toolu_02")]


@pytest.mark.parametrize("evidence", ["tool", "both"])
def test_edit_after_passing_run_blocks(tmp_path, evidence):
    from test_tool_evidence import _log
    _log(tmp_path)  # "both" also needs the log line; with it, only the stale run can block
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit(), _edit_result(), _final()])
    assert _blocked(_run(tmp_path, t, evidence=evidence))
    t2 = _write(tmp_path, [_prompt(), *_passing_run(), _final()])
    assert _passed(_run(tmp_path, t2, evidence=evidence))  # control: same, minus the edit


def test_rerun_after_edit_passes(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit(), _edit_result(),
                          *_rerun(), _final()])
    assert _passed(_run(tmp_path, t))


def test_edit_before_run_passes(tmp_path):
    t = _write(tmp_path, [_prompt(), _edit(), _edit_result(), *_passing_run(), _final()])
    assert _passed(_run(tmp_path, t))


@pytest.mark.parametrize("name", ["Edit", "MultiEdit", "Write", "NotebookEdit"])
def test_every_edit_tool_stales_the_run(tmp_path, name):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit(name=name), _final()])
    assert _blocked(_run(tmp_path, t))


def test_run_started_before_edit_finishing_after_it_blocks(tmp_path):
    # parallel tool calls: the run is issued, then the edit, then the run's result lands
    run_use, run_result = _passing_run()
    t = _write(tmp_path, [_prompt(), run_use, _edit(), run_result, _final()])
    assert _blocked(_run(tmp_path, t))


def test_failed_edit_still_stales_the_run(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit(), _edit_result(ok=False),
                          _final()])
    assert _blocked(_run(tmp_path, t))


def test_sidechain_edit_stales_the_run(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit(sidechain=True), _final()])
    assert _blocked(_run(tmp_path, t))


def test_other_session_edit_does_not_stale_the_run(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit(sid=OTHER_SID), _final()])
    assert _passed(_run(tmp_path, t))


def test_read_only_tool_after_run_does_not_stale_it(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit(name="Read"), _final()])
    assert _passed(_run(tmp_path, t))


def test_log_mode_unchanged_by_edit(tmp_path):
    # default evidence (the agent's own log line) does not read tool records at all
    from test_tool_evidence import _log
    _log(tmp_path)
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit(), _final()])
    assert _passed(_run(tmp_path, t, evidence="log"))


# ---- doc edits do not stale a run; Bash edits do ----
def _edit_path(path, name="Edit"):
    e = _edit(name=name)
    key = "notebook_path" if name == "NotebookEdit" else "file_path"
    e["message"]["content"][0]["input"] = {key: path, "old_string": "a", "new_string": "b"}
    return e


def _bash(cmd, tid="toolu_bash"):
    return _tool_use(cmd, tid=tid)


@pytest.mark.parametrize("path", ["/repo/README.md", "/repo/CHANGELOG.md", "/repo/docs/guide.rst",
                                  "/repo/LICENSE", "/repo/notes/plan.markdown"])
def test_doc_edit_after_run_does_not_stale_it(tmp_path, path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit_path(path), _final()])
    assert _passed(_run(tmp_path, t))


@pytest.mark.parametrize("path", ["/repo/app.py", "/repo/requirements.txt", "/repo/CMakeLists.txt",
                                  "/repo/config.json", "", None])
def test_code_or_unknown_path_edit_stales_the_run(tmp_path, path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit_path(path, name="Write"), _final()])
    assert _blocked(_run(tmp_path, t))


def test_notebook_edit_uses_notebook_path(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(),
                          _edit_path("/repo/analysis.ipynb", name="NotebookEdit"), _final()])
    assert _blocked(_run(tmp_path, t))


@pytest.mark.parametrize("cmd", [
    "sed -i '' 's/a/b/' app.py",
    "sed -E -i.bak 's/a/b/' app.py",
    "perl -pi -e 's/a/b/' app.py",
    "echo 'x = 1' > app.py",
    "printf 'x' >> app.py",
    "cat > app.py <<'EOF'\nprint(1)\nEOF",
    "python3 - <<'EOF'\nopen('app.py', 'w').write('x')\nEOF",
    "python3 -c \"import pathlib; pathlib.Path('a.py').write_text('x')\"",
    "echo x | tee app.py",
    "git checkout -- app.py",
    "git stash pop",
    "git pull --ff-only",
    "git apply fix.patch",
    "git reset --hard HEAD~1",
    "echo x > /tmp/../repo/app.py",
])
def test_bash_edit_after_run_stales_it(tmp_path, cmd):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _bash(cmd), _final()])
    assert _blocked(_run(tmp_path, t))


@pytest.mark.parametrize("cmd", [
    "sed -n 1,20p app.py",
    "grep -n \"a > b\" app.py",
    "git status && git diff --stat",
    "git checkout main",
    "ls > /dev/null 2>&1",
    "env > /tmp/env.txt",
    "cat app.py",
])
def test_read_only_bash_after_run_does_not_stale_it(tmp_path, cmd):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _bash(cmd), _final()])
    assert _passed(_run(tmp_path, t))


def test_bash_edit_then_rerun_passes(tmp_path):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _bash("sed -i '' 's/a/b/' app.py"),
                          *_rerun(), _final()])
    assert _passed(_run(tmp_path, t))


def test_other_session_bash_edit_does_not_stale_the_run(tmp_path):
    e = _bash("sed -i '' 's/a/b/' app.py")
    e["sessionId"] = OTHER_SID
    t = _write(tmp_path, [_prompt(), *_passing_run(), e, _final()])
    assert _passed(_run(tmp_path, t))
