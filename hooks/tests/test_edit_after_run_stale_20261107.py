#!/usr/bin/env python3
"""
Tool/both evidence mode: a passing run that happened BEFORE the last file edit is not
credited. The case: tests pass, the agent edits one more file, then says "all tests pass"
without running them again (anthropics/claude-code#104504, the stale-pass finding).

Order is transcript order (the harness appends entries as they happen), not timestamps.
Run:  python3 -m pytest hooks/tests/ -q
"""
import pytest
from pathlib import Path

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
    "git checkout main",
    # Codex review of PR #28, verbatim
    'printf x > /tmp/".."/repo/app.py',
    'echo "$(printf x > app.py)"',
    "bash -c 'printf x > app.py'",
    "sed '-i' '' 's/a/b/' app.py",
    "sed -e 's/a/b/;s/c/d/' -i '' app.py",
    "perl '-pi' -e 's/a/b/' app.py",
    "git -C . restore app.py",
    "git reset HEAD --hard",
    "git checkout '--' app.py",
    "/usr/bin/patch -p1 < fix.patch",
    "printf x | t'e'e app.py",
    "sed \\\n  -i '' 's/a/b/' app.py",
    "python3 -c \"open ('app.py', 'w').write('x')\"",
    "python3 -c \"from pathlib import Path; open(Path('app.py'), 'w').write('x')\"",
    "python3 -c \"open('app.py', 'rb+').write(b'x')\"",
    "TMPDIR_CODE=$PWD; printf x > $TMPDIR_CODE/app.py",
    "TMPDIR=$PWD; printf x > $TMPDIR/app.py",
    "printf x >=app.py",
    "printf x >&app.py",
    "ls\nrm app.py",
    "env rm app.py",
    # Codex review round 2, verbatim
    "echo x#tag > app.py",
    "echo ok # comment\nrm app.py",
    "echo ok;>/dev/null rm app.py",
    'find . -ex\\\nec sh -c "printf x > app.py" {} +',
    "sort -roapp.py data.txt",
    "sort --out=app.py data.txt",
    "tree -o app.py .",
    "git diff --output=app.py",
    "git config --file config.ini x.y changed",
    "ruff check --fix app.py",
    "eslint --fix app.js",
    "tsc --outFile app.js",
    "rg --pre ./mutator needle app.py",
    "git -c core.fsmonitor=./mutator status",
    "git -c core.hooksPath=./hooks commit -m wip",
    "./bin/cat app.py",
    "PATH=./bin cat app.py",
    "printf x > 1",
    "printf x > -",
    "echo hi\r\nrm app.py",
])
def test_bash_edit_after_run_stales_it(tmp_path, cmd):
    t = _write(tmp_path, [_prompt(), *_passing_run(), _bash(cmd), _final()])
    assert _blocked(_run(tmp_path, t))


@pytest.mark.parametrize("cmd", [
    "sed -n 1,20p app.py",
    "grep -n \"a > b\" app.py",
    "git status && git diff --stat",
    "ls > /dev/null 2>&1",
    "env > /tmp/env.txt",
    "cat app.py",
    "grep -n tee app.py",
    'ls > "/dev/null"',
    'env > "/tmp/env.txt"',
    "git add -A && git commit -m wip && git push",
    "git --no-pager log --oneline -3",
    "find . -name '*.py'",
    "git ls-tree HEAD",
    "git -C. status",
    "sed -n '1,20p' -- app.py",
    "grep -F '`' README.md",
    "ls # just a note",
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


def test_test_line_that_also_writes_earns_no_credit(tmp_path):
    # Codex finding 1: the write happens after the tests finish, in the same command
    t = _write(tmp_path, [_prompt(),
                          _tool_use("pytest && sed -i '' 's/a/b/' app.py"),
                          _tool_result(), _final()])
    assert _blocked(_run(tmp_path, t))


def test_scratch_symlink_into_project_counts_as_a_write():
    # pytest's own tmp_path is under /var/folders (scratch), so link to the real repo
    import sys
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import claim_check_hook as h
    repo = Path(__file__).resolve().parent
    link = Path(tempfile.mkdtemp()) / "src"
    link.symlink_to(repo)
    assert h._scratch_target(str(Path(tempfile.mkdtemp()) / "out.txt"))  # control
    assert not h._scratch_target(f"{link}/app.py")
    assert h._bash_edits(f"printf x > {link}/app.py")


def test_doc_name_symlinked_to_code_stales_the_run(tmp_path):
    code = tmp_path / "app.py"
    code.write_text("x = 1\n")
    readme = tmp_path / "README.md"
    readme.symlink_to(code)
    t = _write(tmp_path, [_prompt(), *_passing_run(), _edit_path(str(readme)), _final()])
    assert _blocked(_run(tmp_path, t))


@pytest.mark.parametrize("cmd", ["pytest && printf x > app.py",
                                 "pytest && git diff --output=app.py"])
def test_test_line_with_writing_tail_earns_no_credit(tmp_path, cmd):
    t = _write(tmp_path, [_prompt(), _tool_use(cmd), _tool_result(), _final()])
    assert _blocked(_run(tmp_path, t))


def test_run_and_edit_in_one_message_earn_no_credit(tmp_path):
    # Codex round 2: parallel tool calls in one assistant entry, the run's result after
    both = _tool_use("python3 -m pytest hooks/tests/ -q", tid="run")
    both["message"]["content"].append(_edit()["message"]["content"][0])
    t = _write(tmp_path, [_prompt(), both, _tool_result(tid="run"), _final()])
    assert _blocked(_run(tmp_path, t))


def test_self_fixing_verifier_keeps_its_own_credit(tmp_path):
    # ruff check --fix changes code (so it stales the earlier pytest) but its own result
    # describes the fixed code, so it may stand as the evidence
    t = _write(tmp_path, [_prompt(), *_passing_run(),
                          _tool_use("ruff check --fix .", tid="toolu_fix"),
                          _tool_result(tid="toolu_fix"), _final()])
    assert _passed(_run(tmp_path, t))


def test_redirect_into_project_in_a_temp_folder_counts(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import claim_check_hook as h
    assert h._bash_edits("printf x > /tmp/proj/app.py", cwd="/tmp/proj")
    assert not h._bash_edits("printf x > /tmp/other/out.txt", cwd="/tmp/proj")  # control
