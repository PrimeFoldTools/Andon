#!/usr/bin/env python3
"""Security regression tests for auto_orient.py (2026-11-05 review, A1 + A2).

A1: a cloned repo controls everything under the project folder. Its MEMORY.md must not
be able to pull a file from elsewhere on disk into the agent's context (a symlink at
the file, or a symlinked parent folder), must not hang the hook (a FIFO), and the hook
must read only the byte cap, not the whole file.

A2: once-per-session markers go in a private folder, are created 0600, and old ones
are pruned instead of piling up.

Most tests run the hook as a subprocess (the real entry point). The read-cap test runs
it in-process so tracemalloc can see how much it actually read.
"""
import io
import json
import os
import stat
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
HOOK = str(HOOKS / "auto_orient.py")
SECRET = "TOP-SECRET-OUTSIDE-FILE-CONTENT"


def _env(tmp_path, extra=None):
    env = dict(os.environ)
    for k in ("AUTO_ORIENT", "AUTO_ORIENT_MEMORY_PATH", "AUTO_ORIENT_MAX_BYTES"):
        env.pop(k, None)
    env["TMPDIR"] = str(tmp_path / "tmpdir")  # isolate markers per test
    (tmp_path / "tmpdir").mkdir(exist_ok=True)
    if extra:
        env.update(extra)
    return env


def _run(tmp_path, cwd, sid="s1", extra=None):
    r = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"cwd": str(cwd), "session_id": f"{sid}-{tmp_path.name}"}),
        capture_output=True, text=True, env=_env(tmp_path, extra), timeout=10,
    )
    assert r.returncode == 0, r.stderr
    assert r.stderr == "", r.stderr  # fail quietly: no traceback
    return r.stdout.strip()


def _outside(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (out / "creds").write_text(f"# MEMORY\n{SECRET}\n")
    return out


def _proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


# --- A1: refuse symlinks / out-of-project paths -------------------------------------

def test_symlinked_memory_file_to_outside_is_not_injected(tmp_path):
    out = _outside(tmp_path)
    proj = _proj(tmp_path)
    (proj / "memory").mkdir()
    (proj / "memory" / "MEMORY.md").symlink_to(out / "creds")
    stdout = _run(tmp_path, proj)
    assert stdout == ""
    assert SECRET not in stdout


def test_symlinked_root_memory_file_to_outside_is_not_injected(tmp_path):
    out = _outside(tmp_path)
    proj = _proj(tmp_path)
    (proj / "MEMORY.md").symlink_to(out / "creds")
    assert _run(tmp_path, proj) == ""


def test_relative_symlink_escape_is_not_injected(tmp_path):
    # The shape a cloned repo would carry: a relative link climbing out of the project.
    out = _outside(tmp_path)
    proj = _proj(tmp_path)
    (proj / "memory").mkdir()
    os.symlink("../../outside/creds", proj / "memory" / "MEMORY.md")
    assert (proj / "memory" / "MEMORY.md").read_text().find(SECRET) >= 0  # link is live
    assert _run(tmp_path, proj) == ""


def test_symlinked_parent_dir_escape_is_refused(tmp_path):
    out = _outside(tmp_path)
    (out / "MEMORY.md").write_text(f"# MEMORY\n{SECRET}\n")
    proj = _proj(tmp_path)
    (proj / "memory").symlink_to(out, target_is_directory=True)
    assert (proj / "memory" / "MEMORY.md").is_file()  # positive control: path resolves
    assert _run(tmp_path, proj) == ""


def test_symlink_inside_project_is_also_refused(tmp_path):
    proj = _proj(tmp_path)
    (proj / "notes.md").write_text("# MEMORY\n- inside but linked\n")
    (proj / "MEMORY.md").symlink_to(proj / "notes.md")
    assert _run(tmp_path, proj) == ""


def test_fifo_memory_file_does_not_hang_or_inject(tmp_path):
    proj = _proj(tmp_path)
    os.mkfifo(proj / "MEMORY.md")
    assert _run(tmp_path, proj) == ""  # _run has a 10 s timeout


def test_bad_link_falls_through_to_safe_root_file(tmp_path):
    out = _outside(tmp_path)
    proj = _proj(tmp_path)
    (proj / "memory").mkdir()
    (proj / "memory" / "MEMORY.md").symlink_to(out / "creds")
    (proj / "MEMORY.md").write_text("# MEMORY\n- real root fact\n")
    ctx = json.loads(_run(tmp_path, proj))["hookSpecificOutput"]["additionalContext"]
    assert "real root fact" in ctx and SECRET not in ctx


# --- A1: normal files still work -----------------------------------------------------

def test_normal_memory_file_still_injected(tmp_path):
    proj = _proj(tmp_path)
    (proj / "memory").mkdir()
    (proj / "memory" / "MEMORY.md").write_text("# MEMORY\n- a durable fact\n")
    out = json.loads(_run(tmp_path, proj))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "a durable fact" in ctx
    assert "memory/MEMORY.md" in ctx  # display path stays project-relative


def test_normal_file_in_symlinked_project_root_still_injected(tmp_path):
    # The project folder itself being reached through a link (macOS /tmp -> /private/tmp)
    # is the user's choice and must keep working.
    real = _proj(tmp_path)
    (real / "MEMORY.md").write_text("# MEMORY\n- via linked root\n")
    link = tmp_path / "proj-link"
    link.symlink_to(real, target_is_directory=True)
    assert "via linked root" in _run(tmp_path, link)


def test_user_override_path_may_live_outside_project(tmp_path):
    out = _outside(tmp_path)
    proj = _proj(tmp_path)
    stdout = _run(tmp_path, proj, extra={"AUTO_ORIENT_MEMORY_PATH": str(out / "creds")})
    assert SECRET in stdout  # the user's own env var is trusted


# --- A1: read only the cap -----------------------------------------------------------

def test_huge_file_read_is_capped(tmp_path, monkeypatch):
    proj = _proj(tmp_path)
    big = proj / "MEMORY.md"
    with open(big, "wb") as f:
        f.write(b"# MEMORY\n" + b"line of memory text\n" * 1000)
        f.truncate(64 * 1024 * 1024)  # 64 MB (sparse tail)
    monkeypatch.delenv("AUTO_ORIENT_MEMORY_PATH", raising=False)
    monkeypatch.delenv("AUTO_ORIENT", raising=False)
    monkeypatch.setenv("AUTO_ORIENT_MAX_BYTES", "4000")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.syspath_prepend(str(HOOKS))
    import importlib
    import tempfile
    tempfile.tempdir = None
    mod = importlib.import_module("auto_orient")
    importlib.reload(mod)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(proj), "session_id": ""})))
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    tracemalloc.start()
    try:
        with pytest.raises(SystemExit) as e:
            mod.main()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        tempfile.tempdir = None
    assert e.value.code == 0
    ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "truncated" in ctx and "line of memory text" in ctx
    assert peak < 4 * 1024 * 1024, f"peak {peak} bytes: whole file was read"


# --- A2: private, pruned markers -----------------------------------------------------

def _marker_dir(tmp_path):
    return tmp_path / "tmpdir" / f"andon-auto-orient-{os.getuid()}"


def test_markers_are_private_and_once_per_session_still_holds(tmp_path):
    proj = _proj(tmp_path)
    (proj / "MEMORY.md").write_text("# MEMORY\n- fact\n")
    assert _run(tmp_path, proj, sid="m") != ""
    assert _run(tmp_path, proj, sid="m") == ""
    d = _marker_dir(tmp_path)
    assert stat.S_IMODE(os.lstat(d).st_mode) == 0o700
    markers = list(d.glob("auto_orient_*"))
    assert len(markers) == 1
    assert stat.S_IMODE(os.lstat(markers[0]).st_mode) == 0o600
    assert not list((tmp_path / "tmpdir").glob("auto_orient_*"))  # none in shared tmp


def test_old_markers_are_pruned_fresh_ones_kept(tmp_path):
    proj = _proj(tmp_path)
    (proj / "MEMORY.md").write_text("# MEMORY\n- fact\n")
    d = _marker_dir(tmp_path)
    d.mkdir(parents=True, mode=0o700)
    old, fresh = d / "auto_orient_old0000000000", d / "auto_orient_fresh00000000"
    old.write_text("1")
    fresh.write_text("1")
    week_ago = time.time() - 8 * 24 * 3600
    os.utime(old, (week_ago, week_ago))
    assert _run(tmp_path, proj, sid="p") != ""
    assert not old.exists()
    assert fresh.exists()


def test_marker_symlink_is_not_followed(tmp_path):
    proj = _proj(tmp_path)
    (proj / "MEMORY.md").write_text("# MEMORY\n- fact\n")
    # Compute the marker name the hook will use, and plant a symlink there.
    import hashlib
    sid = f"ln-{tmp_path.name}"
    mem = (proj / "MEMORY.md").resolve()
    key = hashlib.sha1(f"{sid}:{mem}".encode()).hexdigest()[:16]
    d = _marker_dir(tmp_path)
    d.mkdir(parents=True, mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    (d / f"auto_orient_{key}").symlink_to(victim)
    _run(tmp_path, proj, sid="ln")
    assert victim.read_text() == "original"
