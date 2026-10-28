#!/usr/bin/env python3
"""check_ledger.py — check the lifecycle lines in DEFECT_LEDGER.md.

Every `###` entry in the ledger carries two extra lines after its four-line story:

  **Verdict:** confirmed — <where the evidence is>
  **Control:** id: D-001 · status: test · review_by: 2027-04-27 · modified: 2026-10-27

This script fails (exit 1) when:
  - an entry is missing either line, or has more than one of either;
  - the verdict is not confirmed / needs_validation / rejected, or carries no reason;
  - the status is not experimental / test / stable / deprecated / withdrawn;
  - an active control (experimental / test / stable) has no review_by, or its
    review_by date has passed (the todo_or_die pattern: the date is a promise to
    re-check, and the build breaks when the promise lapses);
  - an active control's review_by is before its modified date, or more than
    MAX_REVIEW_DAYS (365) after it (a far-off date would defeat the promise);
  - a code fence (``` or ~~~) is never closed. Lines inside a fence are an
    example, not the entry's own lines: they never count as a heading, a
    **Verdict:** line or a **Control:** line;
  - a rejected defect still has an active control;
  - an id is malformed or duplicated, a date is not YYYY-MM-DD, a key is unknown,
    or a `related` link has an unknown type or points at an id that does not exist.

  python3 scripts/check_ledger.py [path/to/DEFECT_LEDGER.md] [--today YYYY-MM-DD]

Exit 0 = clean · 1 = problems found (one per line on stdout) · 2 = usage or read error.
Stdlib only. Never edits the ledger.
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

VERDICTS = ("confirmed", "needs_validation", "rejected")
STATUSES = ("experimental", "test", "stable", "deprecated", "withdrawn")
ACTIVE = ("experimental", "test", "stable")
RELATION_TYPES = ("derived", "obsolete", "merged", "renamed")
KEYS = ("id", "status", "review_by", "modified", "related")
REQUIRED = ("id", "status", "modified")
MAX_REVIEW_DAYS = 365

ID_RE = re.compile(r"^D-\d{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEADING_RE = re.compile(r"^### (.+)$")
VERDICT_RE = re.compile(r"^\*\*Verdict:\*\*\s*(.*)$")
CONTROL_RE = re.compile(r"^\*\*Control:\*\*\s*(.*)$")
# A fence opens with 3+ backticks or tildes (up to 3 spaces of indent; a backtick
# fence's info string has no backtick) and closes with a run of the same character
# at least as long, with nothing after it but spaces (CommonMark).
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}(?!.*`)|~{3,})")
FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
DASH = " — "


def _date(value):
    if not DATE_RE.match(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def unfenced_lines(text):
    """Return ([(line_number, line)] outside code fences, line number of a fence that
    is never closed or None). Fence marker lines are dropped too."""
    out, fence, fence_at = [], None, None
    for n, line in enumerate(text.splitlines(), 1):
        if fence is None:
            m = FENCE_OPEN_RE.match(line)
            if m:
                fence, fence_at = m.group(1), n
            else:
                out.append((n, line))
        else:
            m = FENCE_CLOSE_RE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
    return out, (fence_at if fence is not None else None)


def split_entries(text):
    """Return [(title, line_number, [lines])] for every `###` entry. Lines inside a
    code fence are left out: a fenced example is not the entry's own line."""
    entries = []
    for n, line in unfenced_lines(text)[0]:
        m = HEADING_RE.match(line)
        if m:
            entries.append((m.group(1).strip(), n, []))
        elif line.startswith("## "):
            entries.append((None, n, []))       # a level-2 section ends the entry
        elif entries:
            entries[-1][2].append(line)
    return [e for e in entries if e[0] is not None]


def parse_control(body):
    """Parse `key: value · key: value`. Return (fields, problems)."""
    fields, problems = {}, []
    for part in body.split(" · "):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not value:
            problems.append(f"control field '{part}' is not 'key: value'")
        elif key not in KEYS:
            problems.append(f"unknown control key '{key}' (allowed: {', '.join(KEYS)})")
        elif key in fields:
            problems.append(f"control key '{key}' appears twice")
        else:
            fields[key] = value
    return fields, problems


def check_entry(title, lines, today):
    """Return (fields, problems) for one entry."""
    problems = []
    verdicts = [m.group(1).strip() for m in map(VERDICT_RE.match, lines) if m]
    controls = [m.group(1).strip() for m in map(CONTROL_RE.match, lines) if m]

    if len(verdicts) != 1:
        problems.append(f"needs exactly one **Verdict:** line, found {len(verdicts)}")
    if len(controls) != 1:
        problems.append(f"needs exactly one **Control:** line, found {len(controls)}")

    verdict = None
    if len(verdicts) == 1:
        verdict, _, reason = verdicts[0].partition(DASH)
        verdict = verdict.strip()
        if verdict not in VERDICTS:
            problems.append(f"verdict '{verdict}' is not one of {', '.join(VERDICTS)}")
            verdict = None
        elif not reason.strip():
            problems.append(f"verdict '{verdict}' needs a reason after ' — ' "
                            "(confirmed: where the evidence is; needs_validation: "
                            "the exact unresolved fact; rejected: what disproved it)")

    if len(controls) != 1:
        return {}, problems
    fields, parse_problems = parse_control(controls[0])
    problems += parse_problems
    for key in REQUIRED:
        if key not in fields:
            problems.append(f"control is missing '{key}'")

    if "id" in fields and not ID_RE.match(fields["id"]):
        problems.append(f"id '{fields['id']}' is not D-NNN")

    status = fields.get("status")
    if status is not None and status not in STATUSES:
        problems.append(f"status '{status}' is not one of {', '.join(STATUSES)}")
        status = None

    if "modified" in fields and _date(fields["modified"]) is None:
        problems.append(f"modified '{fields['modified']}' is not a YYYY-MM-DD date")

    if status in ACTIVE:
        if "review_by" not in fields:
            problems.append(f"status '{status}' is active, so it needs a review_by date")
        else:
            due = _date(fields["review_by"])
            if due is None:
                problems.append(f"review_by '{fields['review_by']}' is not a YYYY-MM-DD date")
            elif due < today:
                problems.append(f"review_by {fields['review_by']} has passed (today {today}): "
                                "re-check the control, then set a new date, promote it, "
                                "or retire it (deprecated / withdrawn)")
            modified = _date(fields.get("modified", ""))
            if due is not None and modified is not None:
                if due < modified:
                    problems.append(f"review_by {fields['review_by']} is before modified "
                                    f"{fields['modified']}")
                elif (due - modified).days > MAX_REVIEW_DAYS:
                    problems.append(f"review_by {fields['review_by']} is more than "
                                    f"{MAX_REVIEW_DAYS} days after modified "
                                    f"{fields['modified']}: pick a nearer review date")
    elif "review_by" in fields and _date(fields["review_by"]) is None:
        problems.append(f"review_by '{fields['review_by']}' is not a YYYY-MM-DD date")

    if verdict == "rejected" and status in ACTIVE:
        problems.append(f"verdict is rejected but status '{status}' is still active: "
                        "set status to deprecated or withdrawn (keep the entry)")
    return fields, problems


def check_text(text, today):
    """Return a list of problem strings for the whole ledger."""
    problems = []
    unclosed = unfenced_lines(text)[1]
    if unclosed is not None:
        problems.append(f"line {unclosed}: code fence is never closed")
    entries = split_entries(text)
    if not entries:
        return problems + ["no ### entries found"]
    seen, parsed = {}, []
    for title, line_no, lines in entries:
        fields, entry_problems = check_entry(title, lines, today)
        where = f"line {line_no} '{title}'"
        problems += [f"{where}: {p}" for p in entry_problems]
        eid = fields.get("id")
        if eid:
            if eid in seen:
                problems.append(f"{where}: id {eid} is already used at {seen[eid]}")
            else:
                seen[eid] = where
        parsed.append((where, eid, fields))

    for where, eid, fields in parsed:
        for link in filter(None, (s.strip() for s in fields.get("related", "").split(","))):
            rtype, sep, target = link.partition(":")
            rtype, target = rtype.strip(), target.strip()
            if not sep or rtype not in RELATION_TYPES:
                problems.append(f"{where}: related '{link}' must be <type>:<id>, "
                                f"type one of {', '.join(RELATION_TYPES)}")
            elif target == eid:
                problems.append(f"{where}: related '{link}' points at itself")
            elif target not in seen:
                problems.append(f"{where}: related '{link}' points at an id that does not exist")
    return problems


def main(argv):
    args = list(argv)
    today = datetime.date.today()
    if "--today" in args:
        i = args.index("--today")
        if i + 1 >= len(args) or _date(args[i + 1]) is None:
            print("check_ledger: --today needs a YYYY-MM-DD date")
            return 2
        today = _date(args[i + 1])
        del args[i:i + 2]
    path = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "DEFECT_LEDGER.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"check_ledger: cannot read {path}: {exc}")
        return 2
    problems = check_text(text, today)
    for p in problems:
        print(f"check_ledger: {p}")
    if problems:
        print(f"check_ledger: {len(problems)} problem(s) in {path}")
        return 1
    print(f"check_ledger: {len(split_entries(text))} entries ok in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
