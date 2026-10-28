#!/usr/bin/env python3
"""Tests for scripts/check_ledger.py — the Defect Ledger lifecycle checker.

Each rule has a failing case and a legitimate passing case. The last test runs the
checker on the real DEFECT_LEDGER.md, so CI goes red when a control's review_by lapses.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = str(ROOT / "scripts" / "check_ledger.py")
TODAY = "2026-10-27"

VALID_VERDICT = "**Verdict:** confirmed — repro in issue #9"
VALID_CONTROL = "**Control:** id: D-001 · status: test · review_by: 2027-04-27 · modified: 2026-10-27"


def _entry(title="2026-10 — A defect", verdict=VALID_VERDICT, control=VALID_CONTROL):
    lines = [f"### {title}", "", "**Defect:** d.", "**Root cause:** r.",
             "**Countermeasure:** c.", "**Result:** r.", ""]
    lines += [x for x in (verdict, control) if x is not None]
    return "\n".join(lines) + "\n"


def _ledger(*entries):
    return "# Defect Ledger\n\n## The patterns underneath\n\n---\n\n" + "\n".join(entries)


def _run(tmp_path, text, today=TODAY):
    f = tmp_path / "DEFECT_LEDGER.md"
    f.write_text(text, encoding="utf-8")
    return subprocess.run([sys.executable, SCRIPT, str(f), "--today", today],
                          capture_output=True, text=True)


def _ok(r):
    return r.returncode == 0 and "entries ok" in r.stdout


def _fails(r, fragment):
    return r.returncode == 1 and fragment in r.stdout


# ---- valid entry (the positive control for every rule below) ----
def test_valid_entry_passes(tmp_path):
    assert _ok(_run(tmp_path, _ledger(_entry())))


# ---- review_by: the todo_or_die rule ----
def test_expired_review_by_fails(tmp_path):
    r = _run(tmp_path, _ledger(_entry()), today="2027-04-28")
    assert _fails(r, "review_by 2027-04-27 has passed")


def test_review_by_due_today_still_passes(tmp_path):
    assert _ok(_run(tmp_path, _ledger(_entry()), today="2027-04-27"))


def test_active_control_without_review_by_fails(tmp_path):
    c = "**Control:** id: D-001 · status: experimental · modified: 2026-10-27"
    assert _fails(_run(tmp_path, _ledger(_entry(control=c))), "needs a review_by date")


def test_retired_control_needs_no_review_by(tmp_path):
    for status in ("deprecated", "withdrawn"):
        c = f"**Control:** id: D-001 · status: {status} · modified: 2026-10-27"
        assert _ok(_run(tmp_path, _ledger(_entry(control=c))))


def test_retired_control_with_old_review_by_passes(tmp_path):
    c = "**Control:** id: D-001 · status: deprecated · review_by: 2020-01-01 · modified: 2026-10-27"
    assert _ok(_run(tmp_path, _ledger(_entry(control=c))))


def test_malformed_review_by_fails(tmp_path):
    c = "**Control:** id: D-001 · status: test · review_by: next spring · modified: 2026-10-27"
    assert _fails(_run(tmp_path, _ledger(_entry(control=c))), "is not a YYYY-MM-DD date")


# ---- status ----
def test_bad_status_fails(tmp_path):
    c = VALID_CONTROL.replace("status: test", "status: production")
    assert _fails(_run(tmp_path, _ledger(_entry(control=c))), "status 'production' is not one of")


def test_every_valid_status_passes(tmp_path):
    for status in ("experimental", "test", "stable", "deprecated", "withdrawn"):
        c = VALID_CONTROL.replace("status: test", f"status: {status}")
        assert _ok(_run(tmp_path, _ledger(_entry(control=c)))), status


# ---- verdict ----
def test_bad_verdict_fails(tmp_path):
    v = "**Verdict:** probably — looked fine"
    assert _fails(_run(tmp_path, _ledger(_entry(verdict=v))), "verdict 'probably' is not one of")


def test_every_valid_verdict_passes(tmp_path):
    for v in ("confirmed — test X", "needs_validation — whether X reproduces on Y"):
        assert _ok(_run(tmp_path, _ledger(_entry(verdict=f"**Verdict:** {v}"))))
    c = VALID_CONTROL.replace("status: test", "status: withdrawn")
    assert _ok(_run(tmp_path, _ledger(_entry(verdict="**Verdict:** rejected — rerun showed no bug",
                                             control=c))))


def test_verdict_without_reason_fails(tmp_path):
    r = _run(tmp_path, _ledger(_entry(verdict="**Verdict:** needs_validation")))
    assert _fails(r, "needs a reason")


def test_rejected_with_active_control_fails(tmp_path):
    v = "**Verdict:** rejected — rerun showed no bug"
    assert _fails(_run(tmp_path, _ledger(_entry(verdict=v))), "is still active")


# ---- structure ----
def test_missing_lines_fail(tmp_path):
    assert _fails(_run(tmp_path, _ledger(_entry(verdict=None))), "exactly one **Verdict:** line")
    assert _fails(_run(tmp_path, _ledger(_entry(control=None))), "exactly one **Control:** line")


def test_unknown_key_fails(tmp_path):
    c = VALID_CONTROL + " · owner: someone"
    assert _fails(_run(tmp_path, _ledger(_entry(control=c))), "unknown control key 'owner'")


def test_duplicate_id_fails(tmp_path):
    r = _run(tmp_path, _ledger(_entry("2026-10 — One"), _entry("2026-10 — Two")))
    assert _fails(r, "id D-001 is already used")


def test_related_links(tmp_path):
    second = VALID_CONTROL.replace("D-001", "D-002")
    good = _ledger(_entry("2026-10 — One"),
                   _entry("2026-10 — Two", control=second + " · related: derived:D-001"))
    assert _ok(_run(tmp_path, good))
    dangling = _ledger(_entry("2026-10 — One"),
                       _entry("2026-10 — Two", control=second + " · related: derived:D-099"))
    assert _fails(_run(tmp_path, dangling), "does not exist")
    badtype = _ledger(_entry("2026-10 — One"),
                      _entry("2026-10 — Two", control=second + " · related: similar:D-001"))
    assert _fails(_run(tmp_path, badtype), "must be <type>:<id>")


# ---- review_by window: no later than 365 days after modified, never before it ----
def _control(review_by, modified="2026-10-27", status="test"):
    return (f"**Control:** id: D-001 · status: {status} · review_by: {review_by} · "
            f"modified: {modified}")


def test_far_future_review_by_fails(tmp_path):
    r = _run(tmp_path, _ledger(_entry(control=_control("9999-12-31"))))
    assert _fails(r, "more than 365 days after modified")


def test_review_by_one_day_past_the_window_fails(tmp_path):
    r = _run(tmp_path, _ledger(_entry(control=_control("2027-10-28"))))
    assert _fails(r, "more than 365 days after modified")


def test_review_by_at_the_window_edge_passes(tmp_path):
    assert _ok(_run(tmp_path, _ledger(_entry(control=_control("2027-10-27")))))


def test_review_by_before_modified_fails(tmp_path):
    r = _run(tmp_path, _ledger(_entry(control=_control("2026-10-26"))), today="2026-10-20")
    assert _fails(r, "is before modified")


def test_review_by_equal_to_modified_passes(tmp_path):
    assert _ok(_run(tmp_path, _ledger(_entry(control=_control("2026-10-27")))))


def test_window_rules_skip_retired_controls(tmp_path):
    # A retired control keeps its old review_by; its modified date moves on retirement.
    for rb in ("2020-01-01", "9999-12-31"):
        c = _control(rb, status="deprecated")
        assert _ok(_run(tmp_path, _ledger(_entry(control=c)))), rb


# ---- code fences: a fenced example is not the entry's own line ----
@pytest.mark.parametrize("fence", ["```", "~~~", "```text", "~~~~"])
def test_fenced_control_is_not_counted(tmp_path, fence):
    # The entry's only Control line sits in a fence, so it has none of its own.
    example = f"{fence}\n{VALID_CONTROL}\n{fence[0] * len(fence.rstrip('text'))}"
    r = _run(tmp_path, _ledger(_entry(control=example)))
    assert _fails(r, "exactly one **Control:** line, found 0")


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_fenced_example_beside_real_lines_passes(tmp_path, fence):
    example = (f"{fence}\n**Verdict:** rejected — example\n"
               f"**Control:** id: D-900 · status: bogus\n### Not a heading\n{fence}")
    assert _ok(_run(tmp_path, _ledger(_entry() + "\n" + example + "\n")))


def test_backtick_fence_is_not_closed_by_tildes(tmp_path):
    body = _entry(control="```\n~~~\n" + VALID_CONTROL + "\n```")
    assert _fails(_run(tmp_path, _ledger(body)), "found 0")


def test_unclosed_fence_fails(tmp_path):
    r = _run(tmp_path, _ledger(_entry(), "```\n" + _entry("2026-10 — Hidden")))
    assert _fails(r, "code fence is never closed")


# ---- the real ledger ----
def test_real_ledger_passes_today():
    r = subprocess.run([sys.executable, SCRIPT, str(ROOT / "DEFECT_LEDGER.md")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_real_ledger_entry_count_matches_headings():
    # Count from the first entry on: the explainer above it shows an example line.
    lines = (ROOT / "DEFECT_LEDGER.md").read_text(encoding="utf-8").splitlines()
    lines = lines[next(i for i, line in enumerate(lines) if line.startswith("### ")):]
    headings = sum(1 for line in lines if line.startswith("### "))
    controls = sum(1 for line in lines if line.startswith("**Control:** id: D-"))
    assert headings == controls > 0
