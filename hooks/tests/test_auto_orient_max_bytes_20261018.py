#!/usr/bin/env python3
"""Regression tests for auto_orient's AUTO_ORIENT_MAX_BYTES handling (audit 2026-10-18).

Before the fix the cap was parsed with int() at import time, outside the hook's
fail-safe try, so AUTO_ORIENT_MAX_BYTES=abc crashed the hook with a traceback and
exit 1. A value that is not a positive whole number now falls back to the 8000-byte
default. Separately, a first line longer than the cap used to leave nothing but the
truncation notice; the first line is now hard-cut at the cap instead.

Run as a subprocess, the real production entry point.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "auto_orient.py")


def _env(extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUTO_ORIENT")}
    env.update(extra)
    return env


def _run(proj, sid, extra):
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"cwd": str(proj), "session_id": sid}),
                       capture_output=True, text=True, env=_env(extra))
    return r


def _mk_memory(tmp_path, text):
    d = tmp_path / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / "MEMORY.md").write_text(text)
    return tmp_path


def _ctx(r):
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    return json.loads(r.stdout.strip())["hookSpecificOutput"]["additionalContext"]


def test_non_integer_max_bytes_falls_back_to_default(tmp_path):
    proj = _mk_memory(tmp_path, "# MEMORY\n- a durable fact\n")
    for i, bad in enumerate(("abc", "", "  ", "12.5", "8k", "-100", "0")):
        r = _run(proj, f"bad-{i}-{tmp_path.name}", {"AUTO_ORIENT_MAX_BYTES": bad})
        ctx = _ctx(r)
        assert "a durable fact" in ctx, bad
        assert "truncated" not in ctx, bad


def test_bad_max_bytes_still_applies_the_default_cap(tmp_path):
    # 5000 bytes per line x 3 lines = 15000 > 8000: the default cap must still apply.
    proj = _mk_memory(tmp_path, "# MEMORY\n" + ("a" * 5000 + "\n") * 3)
    ctx = _ctx(_run(proj, f"cap-{tmp_path.name}", {"AUTO_ORIENT_MAX_BYTES": "abc"}))
    assert "truncated" in ctx
    assert ctx.count("a" * 5000) == 1


def test_valid_max_bytes_with_whitespace_is_honoured(tmp_path):
    # A control, not a red test: int(" 500 ") already worked before the fix. It pins that
    # the padded value is read as exactly 500, not dropped to the 8000 default.
    # 9 header bytes + 4 lines x 100 bytes = 409 fit; a 5th line would make 509.
    proj = _mk_memory(tmp_path, "# MEMORY\n" + ("x" * 99 + "\n") * 20)
    ctx = _ctx(_run(proj, f"ws-{tmp_path.name}", {"AUTO_ORIENT_MAX_BYTES": " 500 "}))
    body = ctx.split("\n\n", 1)[1]
    kept = body.split("\n\n…[truncated", 1)[0]
    assert "truncated" in body
    assert kept.count("x" * 99) == 4
    assert len(kept.encode()) <= 500


def test_first_line_longer_than_cap_is_hard_cut_not_dropped(tmp_path):
    proj = _mk_memory(tmp_path, "# " + "M" * 198 + "\n- second line\n")
    ctx = _ctx(_run(proj, f"long-{tmp_path.name}", {"AUTO_ORIENT_MAX_BYTES": "50"}))
    body = ctx.split("\n\n", 1)[1]
    assert body.startswith("# " + "M" * 48)
    assert "truncated" in body


def test_hard_cut_never_splits_a_multibyte_character(tmp_path):
    # Each "é" is 2 bytes in UTF-8; an odd cap lands mid-character and must back off.
    proj = _mk_memory(tmp_path, "é" * 100 + "\n")
    ctx = _ctx(_run(proj, f"mb-{tmp_path.name}", {"AUTO_ORIENT_MAX_BYTES": "51"}))
    body = ctx.split("\n\n", 1)[1]
    assert body.startswith("é" * 25)
    assert "�" not in body
