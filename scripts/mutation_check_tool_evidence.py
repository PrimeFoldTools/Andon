#!/usr/bin/env python3
"""
mutation_check_tool_evidence.py — break each guard of the recorded-run evidence mode
and confirm the test suite notices.

A "blocks" test passes trivially against code that blocks everything, so a green suite
alone does not show that each guard is load-bearing. This script copies hooks/ to a temp
dir, applies ONE textual mutation at a time to claim_check_hook.py, runs
hooks/tests/test_tool_evidence.py against the copy, and expects it to FAIL (be killed).
A mutation the suite survives is a guard no test pins.

Run from the repo root:  python3 scripts/mutation_check_tool_evidence.py
Exit 0 only if the unmutated copy passes AND every mutation is killed.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Hermetic: an exported CLAIM_CHECK_* (e.g. CLAIM_CHECK_EVIDENCE=tool) must not decide
# whether a mutation is killed.
_ENV = {k: v for k, v in os.environ.items()
        if not k.startswith("CLAIM_CHECK") and k != "CLAUDE_CODE_SESSION_ID"}

# (name, exact source text, replacement) — each source text must occur exactly once.
MUTATIONS = (
    # --- result guards ---
    ("is_error guard off", 'if b.get("is_error") is not False:', "if False:"),
    ("missing is_error counts as success", 'if b.get("is_error") is not False:',
     'if b.get("is_error") is True:'),
    ("session check off", 'if entry.get("sessionId") != session_id:', "if False:"),
    ("window lower bound off", "if cutoff <= ts <= now_ts + 60:", "if ts <= now_ts + 60:"),
    ("future-date bound off", "if cutoff <= ts <= now_ts + 60:", "if cutoff <= ts:"),
    ("untrusted-result markers ignored", "if any(k in tur for k in _UNTRUSTED_RESULT_KEYS):",
     "if False:"),
    ("backgroundTaskId marker dropped",
     '_UNTRUSTED_RESULT_KEYS = ("backgroundTaskId", "timedOutAfterMs"',
     '_UNTRUSTED_RESULT_KEYS = ("timedOutAfterMs"'),
    ("run_in_background input ignored", "            if inp.get(\"run_in_background\"):\n",
     "            if False:\n"),
    ("interrupted ignored", 'tur.get("interrupted") is not False', "False"),
    ("sidechain credited", 'if not isinstance(entry, dict) or entry.get("isSidechain"):',
     "if not isinstance(entry, dict):"),
    ("tool_use/result id pairing off", 'if not _is_id(tid) or tid not in uses:', "if False:"),
    ("id type checks off", "return isinstance(x, str) and bool(x)", "return True"),
    ("SHELL_TOOLS name check off", 'if b.get("name") not in SHELL_TOOLS:', "if False:"),
    ("tool_use accepted on any entry type",
     'if etype == "assistant" and b.get("type") == "tool_use":', 'if b.get("type") == "tool_use":'),
    ("tool_result accepted on any entry type",
     'elif etype == "user" and b.get("type") == "tool_result":', 'elif b.get("type") == "tool_result":'),
    # --- the grammar ---
    ("allowlist match off", "prefix = next((p for p in prefixes if words[:len(p)] == p), None)",
     "prefix = prefixes[0]"),
    ("flag checks off", "if prefix is None or not _args_ok(prefix, words[len(prefix):]):",
     "if prefix is None:"),
    ("argparse abbreviations ignored",
     "return abbrev and len(name) >= 3 and any(b.startswith(name) for b in long_banned)",
     "return False"),
    ("go flags unchecked", 'if key == "go":', 'if key == "go" and False:'),
    ("tox subcommands unchecked", 'if key == "tox" and a in _TOX_SUBCOMMANDS:', "if False:"),
    ("make variable overrides unchecked", 'if key == "make" and _ASSIGNMENT.match(a):',
     "if False:"),
    ("attached short values unchecked", 'm = re.match(r"-([A-Za-z]+)", word)',
     'm = re.fullmatch(r"-([A-Za-z]+)", word)'),
    ("assignment denylist off",
     "if name in _DENY_ASSIGN or name.startswith(_DENY_ASSIGN_PREFIX):", "if False:"),
    ("pipe allowed without pipefail", "        if not pipefail:\n            return False\n",
     "        if False:\n            return False\n"),
    ("any program allowed after a pipe", "if seg is None or not seg[0] or seg[0][0] not in _FILTERS:",
     "if seg is None or not seg[0]:"),
    ("trailing tokens ignored", "    return k == len(toks)\n", "    return True\n"),
    ("set word not checked", 'and w[0] == "set" and _SET_OPT.match(w[1])', "and _SET_OPT.match(w[1])"),
    ("set option not checked", 'and w[0] == "set" and _SET_OPT.match(w[1])', 'and w[0] == "set"'),
    ("newline/CR accepted", 'if not s or "\\n" in s or "\\r" in s:', "if not s:"),
    ("|| / |& accepted", 'if s[i + 1:i + 2] in ("|", "&"):', "if False:"),
    ("lone & accepted", 'if s[i:i + 2] != "&&":\n                return None',
     'if False:\n                return None'),
    ("double-quote $ ` \\ ! accepted",
     "if j < 0 or any(ch in _DQ_FORBIDDEN for ch in s[i + 1:j]):", "if j < 0:"),
    ("unknown characters accepted", "            return None  # # $ ( ) { } < \\ ` ! non-ASCII control ...",
     "            cur.append(c); have = True; i += 1"),
    # --- wiring ---
    ("tool mode skips run check", 'if evidence in ("tool", "both"):', 'if evidence in ("both",):'),
    ("evidence requirements OR-ed", "if log_ok and run_ok:", "if log_ok or run_ok:"),
    ("default flips to tool", 'return "log"  # unset', 'return "tool"  # unset'),
    ("unknown value falls to weakest", 'else "both"', 'else "log"'),
    ("allowlist override ignored", 'raw = os.environ.get("CLAIM_CHECK_VERIFY_COMMANDS")',
     "raw = None"),
    ("catch-all fails open in tool mode", 'if _ev == "log" or _md == "off":', "if True:"),
)


def _run_suite(hooks_copy):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(hooks_copy / "tests" / "test_tool_evidence.py")],
        capture_output=True, text=True, env=_ENV,
    )
    killers = [ln.split("::")[-1].split(" ")[0] for ln in r.stdout.splitlines()
               if ln.startswith("FAILED")]
    return r.returncode, ", ".join(k.replace("test_", "", 1) for k in killers)


def main():
    src = (ROOT / "hooks" / "claim_check_hook.py").read_text()
    survivors, rows = [], []
    with tempfile.TemporaryDirectory() as td:
        hooks_copy = Path(td) / "hooks"
        shutil.copytree(ROOT / "hooks", hooks_copy,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        rc, _ = _run_suite(hooks_copy)
        if rc != 0:
            print("BASELINE FAILED — unmutated copy does not pass; fix that first.")
            return 1
        for name, old, new in MUTATIONS:
            n = src.count(old)
            if n != 1:
                print(f"SKIPPED (source text found {n}x, need 1): {name}")
                survivors.append(name)
                continue
            (hooks_copy / "claim_check_hook.py").write_text(src.replace(old, new))
            rc, killer = _run_suite(hooks_copy)
            killed = rc != 0
            rows.append((name, "KILLED" if killed else "SURVIVED", killer))
            if not killed:
                survivors.append(name)
        (hooks_copy / "claim_check_hook.py").write_text(src)
    for name, status, killer in rows:
        print(f"{status:9} {name:40} {killer}")
    print(f"\n{len(rows) - len([r for r in rows if r[1] == 'SURVIVED'])}/{len(MUTATIONS)} killed")
    return 0 if not survivors else 1


if __name__ == "__main__":
    sys.exit(main())
